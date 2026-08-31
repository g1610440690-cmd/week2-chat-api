"""数据库引擎与会话管理（重点：连接池）。

连接池是什么？
    数据库连接的建立很昂贵（TCP + 认证 + 内存分配），
    如果每个请求都新建连接、用完就关，高并发下会非常慢甚至打爆数据库。
    连接池 = 提前建好一批连接放在池里，请求来了"借"一条，用完"还"回去。

关键参数（本项目）：
    pool_size=5       池中常驻最多 5 条连接
    max_overflow=10   池满后最多再临时多开 10 条（峰值时用）
    pool_timeout=30   池子被借空时，最多等待 30 秒拿连接，超时抛异常
    pool_recycle=1800 连接存活 30 分钟强制回收（防止被数据库/防火墙静默断开）
    pool_pre_ping=True 每次借出前先 ping 一下，自动剔除失效连接（数据库重启后能自愈）

SQLite 是文件型数据库，不适合多连接，测试时用 NullPool（用完即弃）。
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import get_settings

settings = get_settings()

if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite（本地学习 / 测试）：每次连接用完即弃，避免文件锁问题
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool, echo=False)
else:
    # PostgreSQL（生产 / Docker）：显式配置连接池
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=False,  # 改成 True 可打印所有 SQL，学习时很有用
    )

# 会话工厂：每个请求通过 get_db() 拿一个 AsyncSession
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：为每个请求提供一个数据库会话，请求结束后自动关闭。

    这就是"依赖注入"：路由函数声明 db: AsyncSession = Depends(get_db)，
    FastAPI 会在调用前帮我们创建会话，结束后帮我们关闭。
    """
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """建表（学习期简化方案；生产项目一般用 Alembic 做数据库迁移）。"""
    from . import models  # noqa: F401  确保模型类已注册到 Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
