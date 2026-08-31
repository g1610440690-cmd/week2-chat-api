"""ORM 数据模型：用户 / 会话 / 消息（第 2 周实践流程第 5 步）。

主键用 32 位十六进制字符串（uuid4().hex）而非数据库自增 id：
- 客户端可以预生成 id，方便断线重试等场景
- PostgreSQL 与 SQLite 都能用，跨库兼容
- 对外不暴露"注册人数"这类信息
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def gen_uuid() -> str:
    """生成 32 位十六进制主键，例如 '9f2c...ab'。"""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """用户表。密码只存哈希（PBKDF2），绝不存明文。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # 用 Python 侧微秒级时间戳而不是数据库 CURRENT_TIMESTAMP（后者只有秒级精度，
    # 同一秒内的多条记录排序不确定；微秒级保证消息历史顺序稳定，SQLite/PostgreSQL 通用）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ChatSession(Base):
    """会话表：一个用户可以有多个会话，每个会话是一段独立对话。

    ondelete="CASCADE"：删用户时级联删除其所有会话（由数据库保证一致性）。
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """消息表：role 为 user（用户）或 assistant（AI 回复）。"""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(10))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    # 联合索引：按 (会话, 时间) 查历史消息
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)
