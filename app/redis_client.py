"""[MOD-注释增强-20260901]
Redis 客户端封装（限流 + 缓存 + 自动降级）。

【Redis 在本项目里的定位】—— 不是核心依赖，是【可选加速器】。
它只管"临时状态"（存了不心疼，丢了不影响主业务）：
1. 【限流计数】：key 格式 rl:chat:{user_id}，一分钟内自增，超阈值就拒绝请求；
2. 【热点缓存】：cache_set / cache_get，存一些短时效数据（演示用，暂时没业务用到，留作扩展）。

【最关键的设计：自动降级】
    Redis 挂了 / 没装 / 连不上时，整个应用绝不崩！
    具体策略：
    - 限流：查 Redis 失败就【放行请求】（宁愿让用户刷爆也不能不让用）；
    - 缓存：查不到直接返回 None（等价于缓存 miss，去走数据库）。
    这样本地开发时可以完全不装 Redis，整个应用照样跑。
    （测试环境 conftest.py 里就是把 REDIS_URL 设成 "" 来禁用 Redis 的。）
"""

import logging

# [MOD-注释增强-20260901] redis.asyncio：Redis 官方的异步客户端
#                            （同步的是 redis.Redis，异步的用 redis.asyncio 子包）
import redis.asyncio as aioredis

from .config import get_settings

# [MOD-注释增强-20260901] 给这个模块的日志起个名字叫 "app.redis"，
#                            这样日志里能一眼区分"是 Redis 模块打的日志"。
logger = logging.getLogger("app.redis")


# ========================================================================
# [MOD-注释增强-20260901] RedisManager 封装类
#   把"连接状态"和"限流/缓存方法"都包在一个类里，
#   外面用全局单例 redis_manager，调用起来就是 redis_manager.rate_limit(...)
# ========================================================================

class RedisManager:
    """[MOD-注释增强-20260901]
    Redis 客户端管理器（带自动降级）。

    内部状态：
    - _client：真正的 aioredis.Redis 连接对象（连接失败是 None）
    - _disabled：是否被禁用（REDIS_URL 为空串时直接为 True）
    """

    def __init__(self, url: str):
        self._url = url
        self._client: aioredis.Redis | None = None
        # [MOD-注释增强-20260901] 空字符串 => 禁用 Redis（跟"连不上"走同一条降级路径）
        self._disabled = not bool(url)

    # ====================================================================
    # [MOD-注释增强-20260901] 【生命周期方法】：connect / close / available / ping
    # ====================================================================

    async def connect(self) -> None:
        """[MOD-注释增强-20260901]
        连接 Redis（在应用启动 lifespan 里调用一次）。

        流程：
        ① 如果 _disabled=True（REDIS_URL 为空），打个警告日志直接 return，不阻塞启动；
        ② 尝试 from_url 创建连接 + ping 一下确认可用；
        ③ 任何异常（连接超时 / 认证失败 / 端口不通）→ 捕获异常、打警告、
           把 _client 置 None、_disabled 置 True —— 之后所有操作都自动降级。
        → 无论如何【connect 绝不会抛异常】，应用一定能启动起来。
        """
        if self._disabled:
            logger.warning("REDIS_URL 为空，Redis 功能已禁用（限流/缓存不生效）")
            return
        try:
            # [MOD-注释增强-20260901] decode_responses=True：Redis 返回的 bytes 自动转成 str，
            #                            不用每次调用方自己 .decode("utf-8")，省事。
            self._client = aioredis.from_url(
                self._url, encoding="utf-8", decode_responses=True
            )
            await self._client.ping()
            logger.info("Redis 连接成功: %s", self._url)
        except Exception as exc:
            # [MOD-注释增强-20260901] 连接失败 → 降级运行，不让应用崩
            logger.warning("Redis 连接失败，自动降级运行: %s", exc)
            self._client = None
            self._disabled = True

    async def close(self) -> None:
        """[MOD-注释增强-20260901] 关闭 Redis 连接（应用关闭 lifespan 里调用）。"""
        if self._client:
            await self._client.aclose()

    @property
    def available(self) -> bool:
        """[MOD-注释增强-20260901] 是否可用（_client 非 None 就认为可用）。"""
        return self._client is not None

    async def ping(self) -> bool:
        """[MOD-注释增强-20260901] 真正发一次 PING 给 Redis，看它活不活。
        （健康检查 /healthz 接口用）失败直接返回 False，不抛异常。
        """
        if not self.available:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    # ====================================================================
    # [MOD-注释增强-20260901] 【限流方法】：rate_limit —— 固定窗口限流算法
    # ====================================================================

    async def rate_limit(
        self, key: str, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        """[MOD-注释增强-20260901]
        固定窗口限流。

        【算法原理】（大白话版）：
        假设有"用户 A"在"14:30:00 ~ 14:31:00"这 60 秒窗口内聊天：
        - 第 1 次请求：key=rl:chat:A 不存在，INCR 变成 1 → ≤ 30，放行；EXPIRE 60 秒；
        - 第 30 次请求：INCR 变成 30 → 还放行；
        - 第 31 次请求：INCR 变成 31 → > 30，拒绝；
        - 14:31:00 整：key 自动过期，下一轮从 0 开始。

        【为什么用 pipeline（流水线）？】
        INCR 和 EXPIRE 是两条 Redis 命令，如果分两次发会有两次网络往返。
        pipeline 把两条命令打包一次性发给 Redis，Redis 一次性执行完再一次性回来，
        省一次 RTT（网络来回），而且两条命令是原子执行的（中间不会插入其他命令）。

        :param key:            Redis 键，例如 "rl:chat:9f2c7fd2..."（区分不同限流维度）
        :param limit:          窗口内最多允许多少次
        :param window_seconds: 窗口长度（秒），默认 60 秒 = 1 分钟
        :return: 元组 (是否放行, 剩余可用次数) —— 降级时永远 (True, limit)
        """
        if not self.available:
            # [MOD-注释增强-20260901] 降级：Redis 不可用 → 直接放行，返回"满额剩余次数"
            return True, limit
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)                  # [MOD-注释增强-20260901] 原子操作：计数 +1
            pipe.expire(key, window_seconds)  # [MOD-注释增强-20260901] 原子操作：刷新过期时间
            current, _ = await pipe.execute()  # [MOD-注释增强-20260901] 拿到两条命令的返回值
            # [MOD-注释增强-20260901] current 是当前已用次数，和 limit 比较；
            #                       剩余次数 = limit - current，最少 0（不能是负数）
            return int(current) <= limit, max(0, limit - int(current))
        except Exception as exc:
            # [MOD-注释增强-20260901] Redis 突然挂了（比如进程被杀），降级放行
            logger.warning("限流查询失败，放行请求: %s", exc)
            return True, limit

    # ====================================================================
    # [MOD-注释增强-20260901] 【缓存方法】：cache_get / cache_set
    #   目前业务里暂时没用到，留着做扩展（比如存"热门会话的消息数"之类）
    # ====================================================================

    async def cache_get(self, key: str) -> str | None:
        """[MOD-注释增强-20260901] 读缓存。失败/不可用 → 返回 None（等价于 cache miss）。"""
        if not self.available:
            return None
        try:
            return await self._client.get(key)
        except Exception as exc:
            logger.warning("缓存读取失败: %s", exc)
            return None

    async def cache_set(self, key: str, value: str, ttl_seconds: int) -> None:
        """[MOD-注释增强-20260901] 写缓存。ttl_seconds 是过期秒数（必须传，不允许永久缓存）。
        失败 → 打日志就完事，不阻塞业务。
        """
        if not self.available:
            return
        try:
            await self._client.set(key, value, ex=ttl_seconds)
        except Exception as exc:
            logger.warning("缓存写入失败: %s", exc)


# ========================================================================
# [MOD-注释增强-20260901] 全局唯一实例
#   模块被 import 时就会创建（此时 get_settings() 里 REDIS_URL 已经从 .env 读好了）。
#   其他模块直接 from .redis_client import redis_manager 用就行。
#   测试环境：conftest.py 先设置了环境变量 REDIS_URL=""，再 import 这里，
#             → _disabled=True，完全不影响测试。
# ========================================================================
redis_manager = RedisManager(get_settings().REDIS_URL)
