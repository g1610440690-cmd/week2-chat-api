"""[MOD-注释增强-20260901]
ORM 数据模型（数据库表结构）—— 三张表：用户 / 会话 / 消息。

【主键设计：为什么用 32 位十六进制字符串，而不用数据库自增 id？】
    传统自增 id（1, 2, 3...）的问题：
    ① 暴露业务规模：别人看你 id=10000，就知道你有一万个用户（信息泄露）；
    ② 分布式难用：分库分表 / 微服务场景下，多个数据库各产各自增 id 会撞车；
    ③ PostgreSQL / SQLite / MySQL 语法不同：迁移麻烦。

    本项目方案：uuid.uuid4().hex
    - uuid4 是随机生成的 128 位整数，全球唯一；
    - .hex 转成 32 个十六进制字符（去掉了中间的短横线），方便用 VARCHAR(32) 存；
    - 客户端甚至可以"预生成" id 再发给后端（断线重发时可以直接用同一个 id 防重复）。

【时间戳设计：为什么用 Python 端的 datetime.now(timezone.utc)，而不是数据库的 CURRENT_TIMESTAMP？】
    1) 精度问题：PostgreSQL 的 CURRENT_TIMESTAMP 是【微秒级】，但 SQLite 的只有【秒级】；
       同一个用户一秒钟发两条消息，created_at 可能完全相同 → 排序顺序不确定，
       导致消息历史顺序乱掉。
    2) Python 端统一生成更可控：无论底层换哪种数据库，精度和时区都一致。
    3) 时区统一用 UTC：存储用 UTC，展示时再转用户本地时区（国际惯例，避免夏令时坑）。

【ORM 级联关系（relationship / cascade）】
    cascade="all, delete-orphan" + ForeignKey ondelete="CASCADE" 双保险：
    意思是：删除一个用户 → 自动删除他所有的会话 → 自动删除每个会话下的所有消息。
    数据库层面保证数据一致性，不会出现"孤立的消息/会话"。
"""

import uuid
from datetime import datetime, timezone

# [MOD-注释增强-20260901] SQLAlchemy 2.0 现代写法（类型安全的 Mapped / mapped_column）
from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def gen_uuid() -> str:
    """[MOD-注释增强-20260901]
    主键生成函数：返回 32 位十六进制随机字符串，例如 '9f2c7fd25e1b43af7e9157a3b9c3a1bc'。
    作为 primary_key 的 default= 回调被调用（每次插入新记录时自动生成一个）。
    """
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """[MOD-注释增强-20260901]
    生成【当前 UTC 时间】的 datetime 对象（带时区信息 timezone.utc）。
    抽成一个函数是因为很多字段的 default / onupdate 都要用它，避免写重复代码。
    """
    return datetime.now(timezone.utc)


# ========================================================================
# [MOD-注释增强-20260901] 表 1：用户表 users
# ========================================================================
class User(Base):
    """[MOD-注释增强-20260901]
    用户表：每一行代表一个注册用户。

    ⚠️ 安全红线：password_hash 字段存的是【PBKDF2 加盐哈希】，
                 【绝对不能存明文密码】，哪怕是临时写 demo 也不行！
    """

    __tablename__ = "users"

    # [MOD-注释增强-20260901] 主键：32 位 UUID 十六进制
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    # [MOD-注释增强-20260901] 用户名：唯一（数据库级 UNIQUE 约束 + 索引，注册查重超快）
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    # [MOD-注释增强-20260901] 邮箱：同理唯一 + 索引（支持用邮箱登录）
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    # [MOD-注释增强-20260901] 密码哈希：PBKDF2 格式字符串，长度 255 足够
    password_hash: Mapped[str] = mapped_column(String(255))
    # [MOD-注释增强-20260901] 创建时间：UTC，带时区，由 Python 端生成（微秒级精度）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # [MOD-注释增强-20260901] ORM 关系（反向关系）：
    #   user.sessions 可以直接拿到这个用户的所有会话列表（类型提示是 list[ChatSession]）
    #   back_populates="user"：和 ChatSession 里的 user 字段双向关联，保持一致
    #   cascade="all, delete-orphan"：删用户自动删所有会话
    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# ========================================================================
# [MOD-注释增强-20260901] 表 2：会话表 chat_sessions
#   一个用户可以有 N 个会话，每个会话是一段【独立的对话】。
#   就像你在微信里和不同人的聊天窗口一样：会话 = 聊天窗口。
# ========================================================================
class ChatSession(Base):
    """[MOD-注释增强-20260901]
    聊天会话表。

    外键约束 ondelete="CASCADE"：
        如果 users 表里一行被删了，chat_sessions 表所有 user_id 指向那一行的会话，
        数据库会【自动删掉】，不会出现"孤儿会话"。
        （ORM 的 cascade 是 Python 层做的，这里又加了数据库层面的双保险。）
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    # [MOD-注释增强-20260901] 外键：所属用户的 id。加了索引，查"某个用户的所有会话"超快
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # [MOD-注释增强-20260901] 会话标题：前端侧边栏展示用。默认"新会话"，
    #   用户发第一句话时后端会自动把"第一句话前 20 字"作为标题（更直观）。
    title: Mapped[str] = mapped_column(String(100), default="新会话")
    # [MOD-注释增强-20260901] 创建时间
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # [MOD-注释增强-20260901] 最近更新时间：
    #   default=utcnow       → 新建时 = 创建时间
    #   onupdate=utcnow      → 每次 UPDATE 这个字段时自动更新为当前时间
    #                           （SQLAlchemy ORM 会自动帮你维护）
    #   用途：会话列表按"最近聊过的排前面"——用 updated_at.desc() 排序
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # [MOD-注释增强-20260901] ORM 关系：
    #   session.user → 拿到所属的 User 对象；
    #   session.messages → 拿到这个会话下所有消息（下面 Message 里的 back_populates 对应）
    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",  # 删会话自动删所有消息
        order_by="Message.created_at",  # 默认按发送时间正序排列（早的在前）
    )


# ========================================================================
# [MOD-注释增强-20260901] 表 3：消息表 messages
#   一行 = 一条消息。每条消息一定属于某一个会话。
#   role 只有两种取值：
#     - "user"       → 用户发的
#     - "assistant"  → AI 回复的
# ========================================================================
class Message(Base):
    """[MOD-注释增强-20260901] 聊天消息表。"""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    # [MOD-注释增强-20260901] 所属会话 id，加索引 + 外键级联删除
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    # [MOD-注释增强-20260901] 角色："user"（用户）/ "assistant"（AI）
    role: Mapped[str] = mapped_column(String(10))
    # [MOD-注释增强-20260901] 消息内容：用 TEXT 类型，长度无上限（VARCHAR(8000) 那种容易超长报错）
    content: Mapped[str] = mapped_column(Text)
    # [MOD-注释增强-20260901] 发送时间（UTC，微秒级精度保证排序稳定）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # [MOD-注释增强-20260901] ORM 反向关系：message.session → 拿所属的会话对象
    session: Mapped[ChatSession] = relationship(back_populates="messages")

    # [MOD-注释增强-20260901]
    # 【联合索引】：在 (session_id, created_at) 两列上建一个组合索引。
    # 为什么要建？因为查消息历史的 SQL 一定是：
    #     SELECT * FROM messages WHERE session_id = 'xxx' ORDER BY created_at
    # 没有索引的话，会做"全表扫描"，消息多了巨慢。
    # 有了这个联合索引，数据库直接按索引就能定位 + 按顺序返回，毫秒级。
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)
