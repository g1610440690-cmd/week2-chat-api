"""全局配置。

用 pydantic-settings 读取 .env 文件 + 环境变量，
所有配置集中在这里，其他模块通过 get_settings() 获取（带缓存，只解析一次）。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 从项目根目录的 .env 读取；extra="ignore" 表示 .env 里多出的键不报错
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 应用 ----
    APP_NAME: str = "week2-chat-api"
    DEBUG: bool = False

    # ---- 数据库（连接池参数见 database.py）----
    # 本地无 Docker 时可改为 sqlite+aiosqlite:///./dev.db
    DATABASE_URL: str = "postgresql+asyncpg://chat:chatpass@localhost:5432/chatdb"

    # ---- Redis：置空字符串表示禁用（限流/缓存自动降级）----
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---- JWT ----
    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 天

    # ---- 限流：每个用户每分钟最多 N 次聊天请求 ----
    RATE_LIMIT_PER_MINUTE: int = 30

    # ---- 文件上传 ----
    MAX_UPLOAD_SIZE_MB: int = 5
    UPLOAD_DIR: str = "./uploads"
    # 注意：集合类型不建议通过环境变量覆盖（需要 JSON 格式），默认值即可
    ALLOWED_UPLOAD_EXTENSIONS: set[str] = {
        ".txt", ".md", ".csv", ".json", ".log", ".png", ".jpg", ".jpeg", ".pdf",
    }

    # ---- 可选：真实 LLM 接入（留空则使用内置模拟回复）----
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """全局唯一配置实例（懒加载 + 缓存）。"""
    return Settings()
