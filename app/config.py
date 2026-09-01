"""[MOD-注释增强-20260901]
全局配置模块。

设计思路（为什么这样做？）：
- 把所有"可配置项"集中放在一个文件里，运维改配置不用去翻代码；
- 用 pydantic-settings 自动读取 .env 文件 + 系统环境变量：
  环境变量优先级 > .env 文件 > 默认值，本地开发和 Docker 部署都能用；
- get_settings() 用 lru_cache 缓存，整个应用生命周期只解析一次配置，
  不会每次 import 都去读磁盘，性能更好也避免不一致。
"""

# [MOD-注释增强-20260901] functools.lru_cache：给函数加一个"内存缓存"，
#                            相同的参数调用第二次时直接返回上次的结果，不会再执行函数体。
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """[MOD-注释增强-20260901]
    全局配置类。每一个字段就是一个配置项：
    - 字段名 = 环境变量名（要大写）
    - 字段默认值 = 本地开发时的默认值
    - Pydantic 会自动做类型转换（比如字符串 "30" 会自动转成 int 30）
    """

    # [MOD-注释增强-20260901]
    # 告诉 pydantic-settings：从项目根目录的 .env 文件读配置；
    # extra="ignore" 表示 .env 里多写了不认识的键不会报错（方便加临时注释、备注）。
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ========================================================================
    # [MOD-注释增强-20260901] 【第一组：应用基础配置】
    # ========================================================================
    # 应用名称：会显示在 /docs 接口文档里、日志里
    APP_NAME: str = "week2-chat-api"
    # DEBUG 开关：True 时可以在 /docs 里看更详细的错误堆栈（生产必须关）
    DEBUG: bool = False

    # ========================================================================
    # [MOD-注释增强-20260901] 【第二组：数据库配置】
    #   为什么是 postgresql+asyncpg？
    #   - postgresql：数据库类型（PostgreSQL）
    #   - asyncpg：异步驱动（FastAPI 是异步的，数据库也要用异步才不阻塞）
    #   本地学习没装 PostgreSQL 时可以改成：sqlite+aiosqlite:///./dev.db
    # ========================================================================
    DATABASE_URL: str = "postgresql+asyncpg://chat:chatpass@localhost:5432/chatdb"

    # ========================================================================
    # [MOD-注释增强-20260901] 【第三组：Redis 配置】
    #   Redis 用于"临时状态"（限流计数、短缓存），不是核心依赖；
    #   置空字符串 "" 就是禁用（本地开发可以不装 Redis，程序会自动降级）。
    # ========================================================================
    REDIS_URL: str = "redis://localhost:6379/0"

    # ========================================================================
    # [MOD-注释增强-20260901] 【第四组：JWT 认证配置】
    #   JWT（JSON Web Token）= 三段字符串，服务端不用存 session，天然分布式友好。
    #   - JWT_SECRET：签名密钥（生产必须换成一个很长的随机串，绝不能用默认值！）
    #   - JWT_ALGORITHM：签名算法，HS256 最常用
    #   - JWT_EXPIRE_MINUTES：过期时间，默认 7 天 = 60分钟×24小时×7天
    # ========================================================================
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 天

    # ========================================================================
    # [MOD-注释增强-20260901] 【第五组：聊天限流】
    #   防止单个用户狂刷接口把服务打爆：每分钟最多 30 次聊天请求。
    #   （Redis 可用才生效，Redis 挂了就不限制）
    # ========================================================================
    RATE_LIMIT_PER_MINUTE: int = 30

    # ========================================================================
    # [MOD-注释增强-20260901] 【第六组：文件上传】
    #   - MAX_UPLOAD_SIZE_MB：单个文件最大 5MB
    #   - UPLOAD_DIR：文件落到磁盘的哪个目录（Docker 里要挂到 Volume，不然容器删了文件也没了）
    #   - ALLOWED_UPLOAD_EXTENSIONS：允许的文件扩展名白名单（安全第一，不允许 .exe .bat 等可执行文件）
    #     注意：集合类型（set）不建议通过环境变量覆盖，因为 pydantic 要解析 JSON 格式比较麻烦，
    #     直接在这里写默认值就好。
    # ========================================================================
    MAX_UPLOAD_SIZE_MB: int = 5
    UPLOAD_DIR: str = "./uploads"
    ALLOWED_UPLOAD_EXTENSIONS: set[str] = {
        ".txt", ".md", ".csv", ".json", ".log", ".png", ".jpg", ".jpeg", ".pdf",
    }

    # ========================================================================
    # [MOD-注释增强-20260901] 【第七组：可选——真实大模型 LLM 接入】
    #   现在项目用的是"内置模拟回复"（方便学习，不用申请 API Key）。
    #   想接真实 OpenAI / 通义千问 / DeepSeek 时，把这两个配置填好就行，
    #   然后去 chat_service.py 里替换 stream_reply 的实现即可。
    #   OPENAI_API_KEY 默认 None = 继续用模拟回复。
    # ========================================================================
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # ========================================================================
    # [MOD-注释增强-20260901] 【派生属性】
    #   max_upload_size_bytes = MB 数 × 1024 × 1024（转成字节）。
    #   用 @property 把它变成"只读属性"，调用 settings.max_upload_size_bytes 就能直接拿到字节数，
    #   不用每次自己在代码里算乘法，避免算错。
    # ========================================================================
    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """[MOD-注释增强-20260901]
    获取全局唯一的配置实例。

    为什么要用 @lru_cache 包一层？
    - 懒加载：第一次调用时才实例化 Settings（此时才会去读 .env 和环境变量）；
    - 缓存：后续所有调用直接返回同一个实例，不会重复解析 .env，
      性能更好，也保证了"整个应用用的都是同一份配置，不会各读各的"。

    使用方法（其他模块里都这样写）：
        from .config import get_settings
        settings = get_settings()
        print(settings.APP_NAME)
    """
    return Settings()
