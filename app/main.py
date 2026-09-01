"""[MOD-注释增强-20260901]
FastAPI 应用入口文件（整个项目的"总指挥"）。

【启动方式】
- 本地开发：uvicorn app.main:app --reload
  （--reload 改代码自动重启，生产千万别用）
- Docker 部署：docker compose up -d --build
  （Dockerfile 里已经写好了启动命令）

【应用启动/关闭的生命周期管理 —— lifespan】
FastAPI 0.95+ 推荐用 @asynccontextmanager 形式的 lifespan（老版的 startup/shutdown 事件已弃用），
本项目的启动/关闭流程：

    启动时（yield 之前的代码）：
    ① setup_logging()            →  配置日志（控制台 + 滚动文件）
    ② makedirs UPLOAD_DIR        →  确保上传目录存在（不然第一次上传会报错）
    ③ redis_manager.connect()    →  连 Redis（连不上自动降级，不阻塞）
    ④ init_db()                  →  建表（学习版，生产改 Alembic）
    ⑤ logger.info(...)           →  打一条"启动完成"日志

    yield  →  【应用对外开始服务】，等着接收 HTTP 请求

    关闭时（yield 之后的代码）：
    ① redis_manager.close()      →  优雅关闭 Redis 连接
    ② engine.dispose()           →  释放数据库连接池（让 PostgreSQL 那边正常断开）
    ③ logger.info(...)           →  打一条"已关闭"日志
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import get_settings
from .database import engine, init_db
from .exceptions import register_exception_handlers
from .logging_conf import setup_logging
from .redis_client import redis_manager
from .routers import auth, chat, sessions, upload

# [MOD-注释增强-20260901] 全局 settings 实例 + app 级别的 logger
settings = get_settings()
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """[MOD-注释增强-20260901]
    应用生命周期管理（启动 + 关闭）。

    asynccontextmanager 用法：
    - 进入 with 块时，执行到 yield 之前（=启动）；
    - 退出 with 块时，执行 yield 之后（=关闭）。
    FastAPI 内部会把这个 lifespan 包在自己的 with 语句里。
    """

    # ========================== 启动阶段 ==========================
    # [MOD-注释增强-20260901] ① 最先配日志，保证后续步骤的报错都能被记录下来
    setup_logging()
    # [MOD-注释增强-20260901] ② 确保上传目录存在（os.makedirs + exist_ok=True 不会重复报错）
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    # [MOD-注释增强-20260901] ③ 连 Redis（会捕获所有异常，连不上也不会阻塞启动）
    await redis_manager.connect()
    # [MOD-注释增强-20260901] ④ 建表：CREATE TABLE IF NOT EXISTS ...
    await init_db()
    # [MOD-注释增强-20260901] ⑤ 记录应用名和连接的数据库地址（脱敏的，不含密码），
    #                       方便运维排查"连的是哪个 DB"
    logger.info("%s 启动完成 | 数据库: %s", settings.APP_NAME, settings.DATABASE_URL)

    # [MOD-注释增强-20260901] 【关键点】：yield 把控制权交还给 FastAPI，应用开始对外服务
    yield

    # ========================== 关闭阶段 ==========================
    # [MOD-注释增强-20260901] ① 优雅关闭 Redis 连接
    await redis_manager.close()
    # [MOD-注释增强-20260901] ② 释放 SQLAlchemy 连接池，
    #                       不要"数据库那边还以为连接活的，其实应用已经关了"
    await engine.dispose()
    logger.info("%s 已关闭", settings.APP_NAME)


# ========================================================================
# [MOD-注释增强-20260901] 【创建 FastAPI 应用实例】
#   - title/version/description：显示在 /docs 接口文档顶部，给前端/对接的人看
#   - lifespan=lifespan：把上面写的生命周期管理器绑到应用上
# ========================================================================
app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="第 2 周交付：带会话持久化的流式聊天 API（FastAPI + PostgreSQL + Redis + Docker）",
    lifespan=lifespan,
)

# [MOD-注释增强-20260901] 注册【统一异常体系】：
#   exceptions.py 里的 4 个全局异常处理器（AppError / HTTPException / ValidationError / Exception）
#   全部挂到 app 上，之后所有错误都以 {code, message, detail} 结构返回。
register_exception_handlers(app)


# ========================================================================
# [MOD-注释增强-20260901] 【HTTP 请求日志中间件】
#   给【每一个请求】打一条访问日志，格式：METHOD PATH -> STATUS 耗时 rid=xxx
#   同时给每个请求注入一个 X-Request-ID（响应头里），
#   方便出问题时"用 request_id 去日志里搜这次请求整条链路"。
# ========================================================================
@app.middleware("http")
async def request_logging(request: Request, call_next):
    """[MOD-注释增强-20260901]
    请求日志中间件：给所有请求打日志 + 注入 X-Request-ID。

    工作原理（所有中间件都是这个套路）：
    1) 请求进来 → 先执行我们的代码（生成 request_id、记开始时间）
    2) call_next(request) → 把请求交给真正的路由函数处理，拿到 response
    3) 路由处理完 → 再执行我们的代码（记耗时、打日志、把 request_id 塞到响应头）
    """

    # [MOD-注释增强-20260901] 从请求头取 X-Request-ID，没有就自己生成一个 12 位随机十六进制
    #                       （12 位足够区分日志里的请求，太长写起来丑）
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()  # [MOD-注释增强-20260901] 高精度计时器（比 time.time() 准很多，适合算耗时）
    try:
        response = await call_next(request)
    except Exception:
        # [MOD-注释增强-20260901] 路由里抛了未捕获异常：先记完整堆栈日志，
        #                       再原封不动 re-raise（给 Exception 兜底处理器再处理一次返回 JSON）
        logger.exception("请求异常 %s %s rid=%s", request.method, request.url.path, request_id)
        raise
    # [MOD-注释增强-20260901] 计算耗时（毫秒，保留 1 位小数）
    duration_ms = (time.perf_counter() - start) * 1000
    # [MOD-注释增强-20260901] 把 request_id 写回响应头，前端也能拿到（方便排查"刚才那个请求的 id 是啥"）
    response.headers["X-Request-ID"] = request_id
    # [MOD-注释增强-20260901] 打访问日志：METHOD 路径 -> 状态码 耗时 rid=id
    logger.info(
        "%s %s -> %s %.1fms rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


# ========================================================================
# [MOD-注释增强-20260901] 【注册路由】（把 4 个 router 挂到 app 上）
#   - auth.router     →  /auth/*        认证
#   - sessions.router →  /sessions/*    会话
#   - chat.router     →  /chat/*        聊天
#   - upload.router   →  /upload        文件上传
#   每个 router 的 prefix / tags 在各自文件里写好了，这里只管挂。
# ========================================================================
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(upload.router)

# ========================================================================
# [MOD-注释增强-20260901] 【挂载静态文件目录】
#   把磁盘上的 settings.UPLOAD_DIR（= ./uploads）映射为 HTTP URL 前缀 /static/。
#   举例：用户上传了"学习笔记.txt"，存到 uploads/abcd.txt，
#         访问 URL 就是 /static/abcd.txt，浏览器就能直接下载/渲染图片。
#   StaticFiles 是 FastAPI 自带的，不用装额外依赖。
# ========================================================================
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)  # 再保险一次，防止 lifespan 之后被人手动删了
app.mount("/static", StaticFiles(directory=settings.UPLOAD_DIR), name="static")


# ========================================================================
# [MOD-注释增强-20260901] 【根接口 / 】—— 返回服务基本信息（健康检查/探活的简易版）
# ========================================================================
@app.get("/", summary="服务信息")
async def root():
    return {"app": settings.APP_NAME, "docs": "/docs", "health": "/healthz"}


# ========================================================================
# [MOD-注释增强-20260901] 【健康检查 /healthz 】
#   Docker healthcheck / Kubernetes livenessProbe 都会调这个接口。
#   不要只返回 "ok" 字符串，最好分别探测一下 DB 和 Redis 的真实状态：
#   - DB 活 & Redis 活  →  status: "ok"
#   - DB 挂 / Redis 挂  →  status: "degraded"（降级运行，不直接判 500，因为 Redis 是可选依赖）
#   - 数据库挂了 = 真·不可用，前端看到 degraded 就告警。
# ========================================================================
@app.get("/healthz", summary="健康检查（Docker healthcheck 用）")
async def healthz():
    """[MOD-注释增强-20260901]
    分别探测数据库和 Redis 的健康状态，返回各自情况。

    返回示例：
        { "status": "ok", "db": true, "redis": true }
        { "status": "degraded", "db": true, "redis": "disabled" }  ← 本地开发常见
    """
    # [MOD-注释增强-20260901] 【探测数据库】：跑一条 SELECT 1（最快的一条 SQL，不查表）
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        logger.warning("健康检查：数据库不可用: %s", exc)

    # [MOD-注释增强-20260901] 【探测 Redis】：
    #   - 不可用（被禁用/连不上）→ 返回 "disabled" 字符串（而不是 false），
    #     这样健康检查前端能区分"Redis 本来就没开"和"Redis 挂了"；
    #   - 可用 → 真正发一次 PING。
    if redis_manager.available:
        redis_ok = await redis_manager.ping()
    else:
        redis_ok = "disabled"  # [MOD-注释增强-20260901] 本地无 Redis 时的正常状态，显示 disabled

    # [MOD-注释增强-20260901] 数据库真挂了才算 degraded（Redis 挂了不算严重故障，因为有自动降级）
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "redis": redis_ok}
