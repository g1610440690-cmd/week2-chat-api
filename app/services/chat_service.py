"""[MOD-注释增强-20260901]
聊天业务逻辑层（Service Layer）。

【为什么要单独拆 service 层？不把代码都写在 router 里？】
这是经典的"三层架构"思想：

    ┌─────────────────────┐
    │ Router 层（路由）    │  只管 HTTP 相关的事：取参数、返回状态码、声明 response_model
    │  例：routers/chat.py │  调用 service 干活，把结果包成 JSON 返回
    ├─────────────────────┤
    │ Service 层（业务）   │ ←  【你在这里】—— 写真正的业务逻辑（消息落库、拼接上下文、生成回复）
    │  例：chat_service.py │  不关心 HTTP，也不关心数据库细节，只专注于"聊天这件事怎么做"
    ├─────────────────────┤
    │ ORM 层（数据模型）   │  只管"表怎么映射成 Python 类"
    │  例：models.py       │  不关心任何业务
    └─────────────────────┘

这样拆分的好处（面试高频题）：
① 【易测试】：业务逻辑在 ChatService 里，可以脱离 HTTP 直接写单测测业务；
② 【易复用】：路由 / 定时任务 / 脚本都可以直接调 chat_service.xxx，代码不重复；
③ 【易替换】：以后想把"模拟回复"换成"真实 LLM 调用"，只要改 stream_reply 一个方法，
              路由层一行代码都不用动。

【核心机制：stream_reply 是一个异步生成器（async generator）】
    调用方式：async for chunk in chat_service.stream_reply(...):
    意思是：stream_reply 不是一次性 return 一整段字符串，
            而是 yield 一小段、yield 一小段……（每 4 个字符 0.02 秒吐一小块）
    这正是 SSE 流式输出的核心数据来源——路由层收到一块就推一块给前端，
    形成"打字机效果"。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import NotFoundError
from ..models import ChatSession, Message, User

logger = logging.getLogger("app.chat")

# [MOD-注释增强-20260901] 最近多少条消息作为"上下文"拼给 LLM 看（= MAX_HISTORY / 2 轮对话）
#   取值 6：用户 3 问 + AI 3 答，既能保证上下文够用，又不会把 token 撑爆。
MAX_HISTORY = 6


def build_mock_reply(prompt: str, history_count: int) -> str:
    """[MOD-注释增强-20260901]
    【内置模拟回复生成器】（学习用，不接真实 LLM）。

    生成一段固定格式的"伪 AI 回复"，包含：
    - 告诉用户这是内置模拟回复
    - 复读用户刚才说的话（方便测试"回复里有没有包含用户问题"）
    - 告诉你当前是第几轮对话（history_count=总消息数=2n，所以 n=history_count//2+1 轮）

    学习提示：【接真实 LLM 就改这里】！
        把这里改成 openai / 通义 / DeepSeek 的 SDK 调用，
        然后在 stream_reply 里按 token 流式 yield 出来即可，
        【上层的 routers/chat.py 完全不用改】—— 这就是 Service 层解耦的威力。
    """
    return (
        f"（这是内置模拟回复，用于学习流式输出）\n"
        f"你刚才说：{prompt}\n"
        f"这是当前会话的第 {history_count // 2 + 1} 轮对话。\n"
        f"把 chat_service.stream_reply 换成真实的 LLM 调用，即可变成真正的 AI 聊天。"
    )


# ========================================================================
# [MOD-注释增强-20260901] ChatService 类：把聊天相关的业务方法集中封装
#   最后创建全局单例 chat_service，其他模块 from ... import chat_service 用。
# ========================================================================

class ChatService:
    """[MOD-注释增强-20260901]
    聊天服务——负责"会话归属校验 / 消息落库 / 历史查询 / 回复生成"四件事。

    设计成类（而不是一堆独立函数）的原因：
    - 方法都是围绕"聊天"这件事，放一个类里集中管理，好看；
    - 以后要扩展（如注入 LLM client、加缓存层）直接加私有字段即可；
    - 好做 mock（单元测试时可以把 chat_service 换成 Mock 对象）。
    """

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 方法 1：归属校验
    # ------------------------------------------------------------------
    async def get_owned_session(
        self, db: AsyncSession, session_id: str, user: User
    ) -> ChatSession:
        """[MOD-注释增强-20260901]
        按 session_id 取会话，并【校验归属】—— 只能拿自己的会话。

        安全细节：
        - 如果会话不存在 → 抛 404；
        - 如果会话存在，但 user_id 不是当前用户 → 【同样抛 404】，
          不抛 403，避免攻击者"靠返回 403/404 的不同"枚举哪些会话 id 真实存在。
        """
        session = await db.get(ChatSession, session_id)
        if session is None or session.user_id != user.id:
            raise NotFoundError("会话不存在")
        return session

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 方法 2+3：保存用户消息 / 保存 AI 回复
    #   两者逻辑完全相同，只差 role 字段，所以实际统一调私有方法 _save_message。
    #   写成两个公开方法是为了"调用方读代码时一眼就知道是在存用户的还是 AI 的"，
    #   比直接暴露 _save_message 可读性强得多。
    # ------------------------------------------------------------------
    async def save_user_message(
        self, db: AsyncSession, session_id: str, content: str
    ) -> Message:
        """[MOD-注释增强-20260901] 保存一条 role='user' 的消息到数据库。"""
        return await self._save_message(db, session_id, "user", content)

    async def save_reply(
        self, db: AsyncSession, session_id: str, content: str
    ) -> Message:
        """[MOD-注释增强-20260901] 保存一条 role='assistant' 的 AI 回复到数据库。"""
        return await self._save_message(db, session_id, "assistant", content)

    async def _save_message(
        self, db: AsyncSession, session_id: str, role: str, content: str
    ) -> Message:
        """[MOD-注释增强-20260901]
        【私有方法】真正写消息表的实现：
        ① new 一个 Message ORM 对象；
        ② db.add 放进当前会话的 Unit of Work；
        ③ commit 写盘（事务提交）；
        ④ refresh 把数据库自动生成的 id / created_at 等字段回写到 Python 对象上，
           这样调用方能拿到 message.id（流式回复里要把 message_id 带给前端）。
        """
        message = Message(session_id=session_id, role=role, content=content)
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 方法 4：查询历史消息（给 LLM 当上下文用）
    # ------------------------------------------------------------------
    async def get_history(
        self, db: AsyncSession, session_id: str, limit: int = MAX_HISTORY
    ) -> list[str]:
        """[MOD-注释增强-20260901]
        取最近 limit 条消息的【内容字符串】列表，按【时间正序】返回。

        用途：给 stream_reply 当"LLM 上下文"用（真实接入 LLM 时，
              把这些字符串拼成 prompt 里的历史对话部分）。

        实现小技巧：
        - SQL 层 ORDER BY created_at DESC（最新的在前）+ LIMIT 6，
          保证取到的是"最近 6 条"（不然消息多了，LIMIT 6 取到的是最早 6 条）；
        - 再用 reversed(rows) 在 Python 内存里倒回来，变成"从早到晚"的正序，
          保证上下文顺序是对的（先问的在前）。
        """
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())  # [MOD-注释增强-20260901] SQL 层倒序取"最新 N 条"
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        # [MOD-注释增强-20260901] Python 层再 reverse，恢复"由早到晚"的对话顺序
        return [m.content for m in reversed(rows)]

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 方法 5：流式生成回复（异步生成器）
    # ------------------------------------------------------------------
    async def stream_reply(
        self, prompt: str, history: list[str]
    ) -> AsyncIterator[str]:
        """[MOD-注释增强-20260901]
        模拟 LLM 流式输出——【每 4 个字符一块，间隔 0.02 秒】一块一块 yield 出去。

        为什么是"异步生成器"？
        - 普通函数 return 一次完事；生成器可以 yield 多次，调用方每次拿一块；
        - 加了 async 之后，每次 yield 之间用 await asyncio.sleep(0.02) 模拟网络/推理延迟，
          让其他请求也能抢到事件循环（不会阻塞整个服务）。

        调用方式：
            async for chunk in chat_service.stream_reply("你好", history):
                推 chunk 给 SSE 客户端 ...

        【接真实 LLM 的替换点】：
            把 build_mock_reply + for range 这一套，换成 OpenAI SDK 的 stream=True 调用，
            或者其他大模型 SDK 的流式接口，把收到的每个 delta 直接 yield 出去即可。
            routers/chat.py 那边完全不用改。
        """
        reply = build_mock_reply(prompt, len(history))
        chunk_size = 4
        # [MOD-注释增强-20260901] 步长 chunk_size，把整段回复切成 4 字符一块
        for i in range(0, len(reply), chunk_size):
            await asyncio.sleep(0.02)  # [MOD-注释增强-20260901] 模拟 LLM 推理/网络延迟（真实场景就是等大模型吐 token）
            yield reply[i : i + chunk_size]  # [MOD-注释增强-20260901] 切片 4 个字符 yield 出去


# ========================================================================
# [MOD-注释增强-20260901] 全局单例（模块 import 时创建一次，大家共用同一个实例）
# ========================================================================
chat_service = ChatService()
