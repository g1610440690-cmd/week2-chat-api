"""日志配置（第 2 周实践流程第 7 步）。

- 控制台输出：本地开发直接看
- 滚动文件输出：logs/app.log，单文件最大 5MB，保留 3 个备份
- 日志级别：INFO（DEBUG 时可调低，能看到 SQL、更详细的调用链）

Python logging 三件套：Logger（记录者）-> Handler（输出到哪）-> Formatter（格式）。
logger = logging.getLogger("app.xxx") 按模块名分层，方便按名字过滤。
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
LOG_DIR = "./logs"
LOG_FILE = "app.log"


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根日志器：控制台 + 滚动文件。重复调用是幂等的（先清旧 handler）。"""
    root = logging.getLogger()
    root.setLevel(level)

    # 清掉已有 handler，避免重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(LOG_DIR, LOG_FILE),
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:  # 比如目录不可写：不阻塞启动
        logging.getLogger("app.logging").warning(
            "无法创建日志文件，仅使用控制台输出: %s", exc
        )
