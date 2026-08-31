"""FastAPI 应用入口。

启动方式：
    uvicorn app.main:app --reload            # 本地开发
    docker compose up -d --build             # Docker

lifespan（启动/关闭钩子）：
- 启动：配置日志 -> 连 Redis -> 建表
- 关闭：关 Redis 连接 -> 释放数据库连接池
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

settings = get_settings()
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- 启动 ----------
    setup_logging()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    await redis_manager.connect()   # Redis 连不上会降级，不阻塞启动
    await init_db()                 # 建表（生产建议换 Alembic 迁移）
    logger.info("%s 启动完成 | 数据库: %s", settings.APP_NAME, settings.DATABASE_URL)
    yield
    # ---------- 关闭 ----------
    await redis_manager.close()
    await engine.dispose()          # 优雅释放连接池
    logger.info("%s 已关闭", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="第 2 周交付：带会话持久化的流式聊天 API（FastAPI + PostgreSQL + Redis + Docker）",
    lifespan=lifespan,
)

# 统一异常处理（所有错误都是 {code, message, detail} 结构）
register_exception_handlers(app)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    """请求日志中间件：记录方法、路径、状态码、耗时，并注入 X-Request-ID。"""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("请求异常 %s %s rid=%s", request.method, request.url.path, request_id)
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %s %.1fms rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


# 注册路由
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(upload.router)

# 静态文件：/static/xxx 可直接访问上传的文件
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=settings.UPLOAD_DIR), name="static")


@app.get("/", summary="服务信息")
async def root():
    return {"app": settings.APP_NAME, "docs": "/docs", "health": "/healthz"}


@app.get("/healthz", summary="健康检查（Docker healthcheck 用）")
async def healthz():
    """分别探测数据库和 Redis，返回各自状态。数据库挂了返回 degraded。"""
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        logger.warning("健康检查：数据库不可用: %s", exc)

    if redis_manager.available:
        redis_ok = await redis_manager.ping()
    else:
        redis_ok = "disabled"  # 本地无 Redis 时的正常状态

    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "redis": redis_ok}
