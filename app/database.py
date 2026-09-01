"""[MOD-注释增强-20260901]
数据库引擎与会话管理（核心重点：连接池 + 异步）。

【为什么需要连接池？】
    新建一条数据库连接的成本非常高：
    → TCP 三次握手 → PostgreSQL 身份认证 → 服务端分配内存与进程 → ...
    如果每个 HTTP 请求都"新建连接 → 用完就关"，高并发时：
    ① 性能极差（每个请求浪费几十毫秒在建连接上）；
    ② 数据库会被打满（PostgreSQL 默认最大连接数只有 100 左右）。

    【连接池 = "连接仓库"】：
    应用启动时先建好 N 条连接放在"池子"里，
    一个请求来了 → 从池子里"借"一条；
    请求处理完 → "还"回池子，不会真的关闭。
    借还都是内存操作，微秒级，巨快。

【本项目连接池关键参数（可以背下来，面试高频题）】
    pool_size=5        → 池子里【常驻】最多 5 条连接（平时没人用的时候也保持 5 条待命）
    max_overflow=10    → 池子被借满时，最多再【临时开】10 条救急（高峰期用完就关，不常驻）
    pool_timeout=30    → 池子被借空时，请求最多【等 30 秒】等别人还，等不到就抛异常
    pool_recycle=1800  → 一条连接存活【30 分钟】强制回收（防止数据库/防火墙静默断开导致"死连接"）
    pool_pre_ping=True → 每次借出连接前先【ping 一下】（SELECT 1），如果连接死了就自动剔除并换新的，
                         这样"数据库重启后"应用能自愈，不会一直拿失效连接报错。

【SQLite 的特殊处理】
    SQLite 是【文件型数据库】，整个数据库就是一个 .db 文件，
    不支持多连接并发写（会报"database is locked"）。
    所以测试环境用 SQLite 时，特意用 NullPool：用完就关，不做池化。
"""

from collections.abc import AsyncIterator

# [MOD-注释增强-20260901] SQLAlchemy 异步三件套：
#   - create_async_engine：创建数据库引擎（里面带着连接池）
#   - async_sessionmaker：会话工厂——不是会话本身，是"造会话的机器"
#   - AsyncSession：数据库会话——一次"数据库交互对话"（commit/rollback 的基本单位）
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import get_settings

# [MOD-注释增强-20260901] 注意：settings 必须在【模块级别】取一次，
#                            不要在函数里反复 get_settings()（虽然有缓存，但写起来冗余）。
settings = get_settings()

# ========================================================================
# [MOD-注释增强-20260901] 【引擎创建】—— 根据数据库类型走不同分支
# ========================================================================
if settings.DATABASE_URL.startswith("sqlite"):
    # [MOD-注释增强-20260901]
    # 分支 A：SQLite（本地学习 / pytest 测试）
    # - poolclass=NullPool：不做连接池，每次用完直接关；
    # - echo=False：不打印 SQL 语句（要调试 SQL 时改成 True，所有 SQL 都会打印到控制台）。
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool, echo=False)
else:
    # [MOD-注释增强-20260901]
    # 分支 B：PostgreSQL（生产环境 / Docker 启动）
    # 显式把连接池的每个参数都写出来，避免用默认值踩坑。
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=False,  # 生产必须关！否则 SQL 全打到日志里，性能差 + 可能泄露敏感数据
    )

# ========================================================================
# [MOD-注释增强-20260901] 【会话工厂】SessionLocal
#
# 注意：SessionLocal 不是一个会话对象，而是一个【工厂函数】。
#       每次调用 SessionLocal() 会造一个新的 AsyncSession（用完记得关）。
#       正常情况下我们不会手动 SessionLocal()，而是通过下面的 get_db() 依赖来拿。
#
# expire_on_commit=False：
#   【默认 True】的坑：commit 之后，SQLAlchemy 会把所有 ORM 对象"过期"，
#                     下次再访问 user.username 时会自动去数据库重新查一遍 SELECT，
#                     如果会话关了就直接报错。
#   【设置 False】：commit 之后对象还能用，不用再查一遍数据库，性能更好，
#                    也更符合 FastAPI 场景（commit 后马上返回给前端）。
# ========================================================================
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """[MOD-注释增强-20260901]
    所有 ORM 数据模型的【公共基类】。

    为什么要自己定义一个空的 Base？
    - 所有表模型（User、ChatSession、Message）都继承它；
    - SQLAlchemy 会自动把"哪些类是表模型"注册到 Base.metadata 里；
    - 建表时只要 Base.metadata.create_all()，就能一口气把所有表建好。
    """


async def get_db() -> AsyncIterator[AsyncSession]:
    """[MOD-注释增强-20260901]
    FastAPI 依赖注入——给每个请求提供一个数据库会话。

    【用法】（在路由函数的参数里这样写）：
        @router.get("")
        async def my_route(db: AsyncSession = Depends(get_db)):
            ... 这里就能用 db 查数据库了 ...

    【工作原理】：
    - 请求进来时，FastAPI 看到参数上写了 Depends(get_db)，就会执行这个函数；
    - async with SessionLocal() as session: —— 创建一个全新的会话；
    - yield session —— 把会话"吐给"路由函数当参数，路由开始执行；
    - 路由函数执行完（不管正常返回还是抛异常），代码回到 yield 之后，
      async with 自动关闭会话，连接还给连接池。

    【好处】：
    ① 每个请求一个独立会话，保证事务隔离；
    ② 不用手动 open/close，不会忘关导致连接泄漏；
    ③ 代码干净，不用每个路由都写一遍创建关闭。
    """
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """[MOD-注释增强-20260901]
    【建表函数】—— 在应用启动 lifespan 里被调用一次。

    工作流程：
    ① import .models ： 把 models.py 里的 User / ChatSession / Message 三个类加载进来，
                        它们会自动注册到 Base.metadata；
                        （# noqa: F401 是告诉 flake8 不要骂我"import 了没用"，
                         这个 import 是【有副作用】的，必须要。）
    ② async with engine.begin() as conn ： 拿一个连接，并【自动开启事务】；
    ③ conn.run_sync(Base.metadata.create_all) ：
        在这个连接上执行"CREATE TABLE IF NOT EXISTS ..."把所有不存在的表建出来。

    ⚠️ 注意：这是【学习期简化方案】！
    生产项目绝对不能这样做——要改用 Alembic（SQLAlchemy 官方的数据库迁移工具），
    每次改表结构生成一个迁移脚本，能版本化、能回滚、能在多台机器上同步执行。
    """
    from . import models  # noqa: F401  必须先导入，模型类才会注册到 Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
