"""聊天业务逻辑（第 2 周实践流程第 6 步）。

把"业务逻辑"从"路由"里拆出来：
- 路由只负责 HTTP 层（参数、状态码、响应格式）
- 服务层负责真正的业务（保存消息、生成回复、拼上下文）

stream_reply 是一个异步生成器（async generator）：
调用方用 async for 逐块消费，这正是流式输出的核心机制。
"""
import asyncio
import logging
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..exceptions import NotFoundError
from ..models import ChatSession, Message, User

logger = logging.getLogger("app.chat")

# 拼进上下文（给"LLM"看）的最近消息条数
MAX_HISTORY = 6


def build_mock_reply(prompt: str, history_count: int) -> str:
    """内置模拟回复。

    学习提示：把这里换成真实的 LLM 调用（OpenAI / 通义 / DeepSeek 等），
    项目就变成了真正的 AI 聊天 API —— stream_reply 的调用方完全不用改。
    """
    return (
        f"（这是内置模拟回复，用于学习流式输出）\n"
        f"你刚才说：{prompt}\n"
        f"这是当前会话的第 {history_count // 2 + 1} 轮对话。\n"
        f"把 chat_service.stream_reply 换成真实的 LLM 调用，即可变成真正的 AI 聊天。"
    )


class ChatService:
    """聊天服务：负责消息落库与回复生成。"""

    async def get_owned_session(
        self, db: AsyncSession, session_id: str, user: User
    ) -> ChatSession:
        """取会话并校验归属。不属于当前用户的一律按 404 处理（不泄露存在性）。"""
        session = await db.get(ChatSession, session_id)
        if session is None or session.user_id != user.id:
            raise NotFoundError("会话不存在")
        return session

    async def save_user_message(
        self, db: AsyncSession, session_id: str, content: str
    ) -> Message:
        return await self._save_message(db, session_id, "user", content)

    async def save_reply(
        self, db: AsyncSession, session_id: str, content: str
    ) -> Message:
        return await self._save_message(db, session_id, "assistant", content)

    async def _save_message(
        self, db: AsyncSession, session_id: str, role: str, content: str
    ) -> Message:
        message = Message(session_id=session_id, role=role, content=content)
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    async def get_history(
        self, db: AsyncSession, session_id: str, limit: int = MAX_HISTORY
    ) -> list[str]:
        """取最近 limit 条消息内容，按时间正序返回（作为 LLM 上下文）。"""
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [m.content for m in reversed(rows)]  # 倒序取，再正序排回来

    async def stream_reply(
        self, prompt: str, history: list[str]
    ) -> AsyncIterator[str]:
        """模拟 LLM 逐块输出：每 4 个字符一块，间隔 0.02 秒。

        async for 逐块消费 => 客户端就能"打字机式"看到回复，
        这就是 SSE 流式响应的数据来源。
        """
        reply = build_mock_reply(prompt, len(history))
        chunk_size = 4
        for i in range(0, len(reply), chunk_size):
            await asyncio.sleep(0.02)  # 模拟推理/网络延迟（真实场景换成 LLM 调用）
            yield reply[i : i + chunk_size]


chat_service = ChatService()
