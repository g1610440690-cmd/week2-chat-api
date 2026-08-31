"""聊天路由：非流式 JSON 回复 + SSE 流式回复（第 2 周实践流程第 6 步）。

流式原理（SSE，Server-Sent Events）：
- 响应头 Content-Type: text/event-stream
- 服务端持续往响应体里写 "data: {...}\\n\\n" 这样的行
- 客户端（浏览器 EventSource / httpx 流式读）每收到一行就渲染一次
- 连接保持打开，直到服务端发送 done 事件后关闭

对比 WebSocket：SSE 是"服务器单向推送给客户端"，实现简单、天然走 HTTP、
自动重连；WebSocket 是双向全双工，适合聊天室/游戏这类双向场景。
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


def sse(payload: dict) -> str:
    """把 JSON 包成 SSE 帧。ensure_ascii=False 保证中文原样输出。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _rate_limit_or_raise(user: User) -> None:
    """统一限流入口：超过阈值抛 429。"""
    settings = get_settings()
    allowed, remaining = await redis_manager.rate_limit(
        f"rl:chat:{user.id}", settings.RATE_LIMIT_PER_MINUTE, 60
    )
    if not allowed:
        raise RateLimitError(detail={"remaining": remaining})


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
    await _rate_limit_or_raise(user)
    session = await chat_service.get_owned_session(db, session_id, user)

    # 第一句话自动生成会话标题
    if session.title == "新会话":
        session.title = body.content[:20]

    await chat_service.save_user_message(db, session_id, body.content)
    history = await chat_service.get_history(db, session_id)

    # 非流式：把流式生成的块全部拼起来再返回
    reply_text = ""
    async for chunk in chat_service.stream_reply(body.content, history):
        reply_text += chunk

    reply = await chat_service.save_reply(db, session_id, reply_text)
    logger.info(
        "用户 %s 在会话 %s 发送消息，回复 %d 字符", user.id, session_id, len(reply_text)
    )
    return reply


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
    await _rate_limit_or_raise(user)
    session = await chat_service.get_owned_session(db, session_id, user)
    if session.title == "新会话":
        session.title = body.content[:20]

    user_msg = await chat_service.save_user_message(db, session_id, body.content)
    history = await chat_service.get_history(db, session_id)

    async def event_generator():
        """异步生成器：真正的流式输出逻辑。"""
        full = ""
        try:
            yield sse(
                {
                    "type": "start",
                    "session_id": session_id,
                    "user_message_id": user_msg.id,
                }
            )
            # 逐块消费模拟 LLM 的输出，边收边推
            async for chunk in chat_service.stream_reply(body.content, history):
                full += chunk
                yield sse({"type": "token", "content": chunk})
            # 完整回复落库（会话持久化的关键一步）
            reply = await chat_service.save_reply(db, session_id, full)
            yield sse(
                {"type": "done", "message_id": reply.id, "content": full}
            )
        except asyncio.CancelledError:
            # 客户端中途断开：把已生成的部分也保存下来，不丢数据
            if full:
                logger.info("客户端断开，保存部分回复（%d 字符）", len(full))
                await chat_service.save_reply(db, session_id, full)
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",  # 禁止中间层缓存流式响应
            "X-Accel-Buffering": "no",  # 让 nginx 不要缓冲，否则前端收不到"打字机"效果
        },
    )
