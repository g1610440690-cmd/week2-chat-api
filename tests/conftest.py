"""pytest 全局配置。

关键点：必须在导入 app 之前设置环境变量！
- 数据库换成 SQLite：测试不需要真的装 PostgreSQL
- REDIS_URL 置空：禁用 Redis（限流放行），测试不需要装 Redis
这样任何机器上都能直接跑 pytest。
"""
import os
import shutil

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_chat.db"
os.environ["REDIS_URL"] = ""  # 禁用 Redis
os.environ["JWT_SECRET"] = "test-secret-0123456789abcdef0123456789abcdef"
os.environ["UPLOAD_DIR"] = "./test_uploads"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"  # 测试里不触发限流

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# 环境变量设置完之后再导入 app（此时才会读取配置）
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """一个共享的 TestClient。

    with 语句会触发 lifespan（启动时自动建表），
    所以这里不需要手动建表；会话结束自动关连接。
    """
    # 清理上次运行残留的测试数据库/上传文件
    if os.path.exists("./test_chat.db"):
        os.remove("./test_chat.db")
    if os.path.exists("./test_uploads"):
        shutil.rmtree("./test_uploads")

    with TestClient(app) as c:
        yield c
