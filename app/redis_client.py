"""Redis 客户端封装（第 2 周实践流程：Redis 临时状态）。

Redis 在本项目里承担两类"临时状态"：
1. 限流计数：rl:chat:{user_id} 在 60 秒窗口内自增，超限拒绝请求
2. 热点缓存：cache_set / cache_get 存一些短时效数据（演示用）

关键设计：Redis 不可用时自动降级 —— 限流放行、缓存失效，
业务照常运行，只打日志提醒。这样本地没装 Redis 也能开发调试。
"""
import logging

import redis.asyncio as aioredis

from .config import get_settings

logger = logging.getLogger("app.redis")


class RedisManager:
    def __init__(self, url: str):
        self._url = url
        self._client: aioredis.Redis | None = None
        self._disabled = not bool(url)  # REDIS_URL 为空字符串 => 禁用

    # ---------- 生命周期 ----------
    async def connect(self) -> None:
        if self._disabled:
            logger.warning("REDIS_URL 为空，Redis 功能已禁用（限流/缓存不生效）")
            return
        try:
            self._client = aioredis.from_url(
                self._url, encoding="utf-8", decode_responses=True
            )
            await self._client.ping()
            logger.info("Redis 连接成功: %s", self._url)
        except Exception as exc:
            # 连接失败不阻塞启动，降级运行
            logger.warning("Redis 连接失败，自动降级运行: %s", exc)
            self._client = None
            self._disabled = True

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def available(self) -> bool:
        return self._client is not None

    async def ping(self) -> bool:
        if not self.available:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    # ---------- 限流（固定窗口） ----------
    async def rate_limit(
        self, key: str, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        """固定窗口限流。

        :return: (是否放行, 剩余次数)。Redis 不可用时放行（返回 True）。
        """
        if not self.available:
            return True, limit
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)          # 计数 +1（原子操作）
            pipe.expire(key, window_seconds)  # 刷新窗口过期时间
            current, _ = await pipe.execute()
            return int(current) <= limit, max(0, limit - int(current))
        except Exception as exc:
            logger.warning("限流查询失败，放行请求: %s", exc)
            return True, limit

    # ---------- 缓存 ----------
    async def cache_get(self, key: str) -> str | None:
        if not self.available:
            return None
        try:
            return await self._client.get(key)
        except Exception as exc:
            logger.warning("缓存读取失败: %s", exc)
            return None

    async def cache_set(self, key: str, value: str, ttl_seconds: int) -> None:
        if not self.available:
            return
        try:
            await self._client.set(key, value, ex=ttl_seconds)
        except Exception as exc:
            logger.warning("缓存写入失败: %s", exc)


# 全局唯一实例（模块导入时创建；测试环境 REDIS_URL 为空 => 自动禁用）
redis_manager = RedisManager(get_settings().REDIS_URL)
