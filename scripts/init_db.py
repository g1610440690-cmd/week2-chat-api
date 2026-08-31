"""手动初始化数据库表（学习/排查用）。

正常情况下应用启动时 lifespan 会自动建表，
这个脚本只是演示"如何手动执行建表"，以及列出当前有哪些表。

用法：python scripts/init_db.py
"""
import asyncio
import sys
from pathlib import Path

# 把项目根目录加入模块搜索路径，保证能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, engine  # noqa: E402


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("建表完成，当前表：")
    for name in sorted(Base.metadata.tables):
        print(" -", name)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
