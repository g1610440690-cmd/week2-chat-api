"""[MOD-注释增强-20260901]
聊天路由模块：两种聊天模式 —— 非流式 JSON 回复 + SSE 流式回复。

【SSE（Server-Sent Events）是什么？大白话解释】
    普通 HTTP 请求：客户端问一句 → 服务端想半天 → 一次性回一整句，结束。
    SSE：          客户端问一句 → 服务端"想一个字吐一个字"，
                    按固定格式一行一行往回推，连接一直开着，直到推完才关。

    帧格式（必须严格遵守这个格式浏览器才能识别）：
        data: {"type": "start"}\n\n
        data: {"type": "token", "content": "你"}\n\n
        data: {"type": "token", "content": "好"}\n\n
        data: {"type": "done", ...}\n\n

    每帧必须以 \n\n 结尾，前缀必须是 "data: "。
    和 WebSocket 的区别：
    - SSE：单向（只能服务器推给客户端），基于纯 HTTP，实现简单，自动断线重连；
    - WebSocket：双向全双工，适合聊天室/游戏这类客户端也要主动发消息的场景。
    聊天回复用 SSE 足够了，简单稳定。

【两个接口的职责分工】
    POST /chat/sessions/{id}/messages  →  简单场景：等 AI 全说完一次性返回 JSON（最容易对接）
    POST /chat/sessions/{id}/stream    →  高级场景：打字机效果，用户体验好
    两者底层复用同一个 chat_service，不会出现两份业务逻辑不一致。
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..core.deps import get_current_user
from ..database import get_db
from ..exceptions import RateLimitError
from ..models import User
from ..redis_client import redis_manager
from ..schemas import MessageCreate, MessageOut
from ..services.chat_service import chat_service

logger = logging.getLogger("app.chat")
router = APIRouter(prefix="/chat", tags=["聊天"])


# ========================================================================
# [MOD-注释增强-20260901] SSE 辅助函数 + 限流辅助函数
# ========================================================================

def sse(payload: dict) -> str:
    """[MOD-注释增强-20260901]
    把一个 Python dict 包装成【SSE 协议要求的一帧字符串】。

    作用示例：
        sse({"type": "token", "content": "你好"})
        → 'data: {"type": "token", "content": "你好"}\n\n'

    ensure_ascii=False 很关键：不加这个的话 json.dumps 会把中文变成 \u4f60\u597d，
    所有中文都成了 Unicode 转义，前端拿到再解码一次也能用，但日志里看的时候全是乱码，
    调试很难受。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _rate_limit_or_raise(user: User) -> None:
    """[MOD-注释增强-20260901]
    【聊天限流】统一入口。

    规则（可在配置里调）：
    - 每个用户每分钟最多 settings.RATE_LIMIT_PER_MINUTE（默认 30）次聊天请求；
    - 超过 → 抛 429 RateLimitError，detail 里还剩多少次；
    - Redis 不可用时 redis_manager 内部自动降级放行，绝不会崩。

    两个接口（非流式/流式）都会调这个函数，保证限流规则只有一份。
    """
    settings = get_settings()
    allowed, remaining = await redis_manager.rate_limit(
        f"rl:chat:{user.id}", settings.RATE_LIMIT_PER_MINUTE, 60
    )
    if not allowed:
        raise RateLimitError(detail={"remaining": remaining})


# ========================================================================
# [MOD-注释增强-20260901] 接口 1：非流式聊天（一次性返回 JSON）
#           POST /chat/sessions/{session_id}/messages
# ========================================================================

@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageOut,
    summary="发送消息（非流式，等完整回复后一次性返回）",
)
async def send_message(
    session_id: str,
    body: MessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[MOD-注释增强-20260901]
    发送消息（非流式）：最简单的对接方式。

    完整流程：
    ① 限流校验（超过每分钟 30 次 → 429）
    ② 归属校验（session 是不是你的）
    ③ 第一句话自动生成会话标题（如果标题还是默认的"新会话"，就用 content 前 20 字当标题）
    ④ 保存用户消息到数据库
    ⑤ 取最近 N 条历史（给"LLM"当上下文）
    ⑥ async for 把流式生成的块一块一块拼起来，拼成完整 reply_text
    ⑦ 把 AI 回复存到数据库（会话持久化）
    ⑧ 记录聊天日志 + 返回回复 Message 对象
    """
    await _rate_limit_or_raise(user)
    session = await chat_service.get_owned_session(db, session_id, user)

    # [MOD-注释增强-20260901] 自动标题：第一句话时，如果标题还是默认"新会话"，
    #                            就把 content 前 20 字截出来当标题（更直观）
    if session.title == "新会话":
        session.title = body.content[:20]

    await chat_service.save_user_message(db, session_id, body.content)
    history = await chat_service.get_history(db, session_id)

    # [MOD-注释增强-20260901] 非流式实现：把流式的块全部拼起来再返回
    #                            （复用同一个 stream_reply，保证回复内容一致）
    reply_text = ""
    async for chunk in chat_service.stream_reply(body.content, history):
        reply_text += chunk

    # [MOD-注释增强-20260901] 保存回复到数据库（这样 GET /sessions/{id}/messages 就能看到了）
    reply = await chat_service.save_reply(db, session_id, reply_text)
    logger.info(
        "用户 %s 在会话 %s 发送消息，回复 %d 字符", user.id, session_id, len(reply_text)
    )
    return reply


# ========================================================================
# [MOD-注释增强-20260901] 接口 2：SSE 流式聊天（边生成边推送）
#           POST /chat/sessions/{session_id}/stream
# ========================================================================

@router.post(
    "/sessions/{session_id}/stream",
    summary="发送消息（SSE 流式输出，边生成边推送）",
)
async def stream_message(
    session_id: str,
    body: MessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[MOD-注释增强-20260901]
    发送消息（SSE 流式）—— 打字机效果的核心接口。

    跟非流式的区别：
    - 不是返回一个 JSON 对象，而是返回一个 StreamingResponse，
      FastAPI 会持续往 socket 里写东西，不一次性关连接；
    - 回复的【最后一帧】发送完之后，才把完整回复落库（不然只能落一半）。

    SSE 事件序列（前端可以按 type 分别处理）：
    ① start：告诉前端"开始啦，session_id 是 xxx，用户那条消息的 id 是 xxx"
    ② token（N 帧）：每帧 4 个字符左右，前端 append 到页面上，就是打字机效果
    ③ done：告诉前端"结束啦，完整回复是 xxx，最终落库的 message_id 是 xxx"
    """
    await _rate_limit_or_raise(user)
    session = await chat_service.get_owned_session(db, session_id, user)
    # [MOD-注释增强-20260901] 同样的逻辑：第一句话自动生成标题
    if session.title == "新会话":
        session.title = body.content[:20]

    user_msg = await chat_service.save_user_message(db, session_id, body.content)
    history = await chat_service.get_history(db, session_id)

    async def event_generator():
        """[MOD-注释增强-20260901]
        【异步生成器】—— 真正的流式输出逻辑在这里。

        StreamingResponse 接收一个 async generator 作为 body，
        FastAPI 会不停地 async for 调这个生成器，
        每次 yield 出来的字符串都立刻写进响应 socket。

        ⚠️ 对 CancelledError 的处理（重要！）：
            客户端中途关浏览器 / 取消请求 → asyncio 会抛 CancelledError。
            我们捕获它，并把【已经生成的回复部分】也存进数据库，
            这样下次打开历史记录不会"用户说了一句话，AI 好像没回复"，
            至少能看到 AI 说到了哪。
        """
        full = ""
        try:
            # [MOD-注释增强-20260901] 第 1 帧：start 事件（给前端会话 id 和用户消息 id，方便定位）
            yield sse(
                {
                    "type": "start",
                    "session_id": session_id,
                    "user_message_id": user_msg.id,
                }
            )
            # [MOD-注释增强-20260901] 第 2 帧起：一块一块 token 往外吐
            async for chunk in chat_service.stream_reply(body.content, history):
                full += chunk
                yield sse({"type": "token", "content": chunk})
            # [MOD-注释增强-20260901] 最后一帧：done 事件（包含完整回复和落库后的 message_id）
            reply = await chat_service.save_reply(db, session_id, full)
            yield sse(
                {"type": "done", "message_id": reply.id, "content": full}
            )
        except asyncio.CancelledError:
            # [MOD-注释增强-20260901] 客户端中途取消 → 能存多少存多少，不丢数据
            if full:
                logger.info("客户端断开，保存部分回复（%d 字符）", len(full))
                await chat_service.save_reply(db, session_id, full)
            raise

    # [MOD-注释增强-20260901] 返回 StreamingResponse
    #   media_type="text/event-stream" 是 SSE 标准 MIME，浏览器的 EventSource 一看就懂
    #   两个响应头是给反向代理/CDN 的：
    #     - Cache-Control: no-cache：禁止任何中间缓存流式响应（不然用户看到的是旧缓存）
    #     - X-Accel-Buffering: no：专门对 Nginx 说"别缓冲我的响应"，
    #       不然 Nginx 默认会等响应收完了再一次性发给客户端，打字机效果就失效了。
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
