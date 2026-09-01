"""[MOD-注释增强-20260901]
日志配置（控制台 + 滚动文件双路输出）。

【Python logging 三件套（必须理解，面试高频）】
    1) Logger（记录者）：真正"打日志"的入口，代码里调 logger.info(...) 就是用它；
                        可以按名字分层（"app"、"app.redis"、"app.chat"），
                        方便按名字过滤 / 调不同级别。
    2) Handler（处理器）：决定日志【输出到哪】
        - StreamHandler → 控制台（stdout）
        - RotatingFileHandler → 文件（按大小滚动，防止单个日志文件爆炸）
        - 一个 Logger 可以挂多个 Handler（双路输出就是这么来的）
    3) Formatter（格式化器）：决定日志【长啥样】
        我们的格式：时间 | 级别(7字符左对齐) | logger名 | 消息内容
        示例：2026-09-01 12:00:00,123 | INFO    | app | week2-chat-api 启动完成

【滚动文件策略】
    - 单个文件最大 5MB（超过就切下一份）；
    - 最多保留 3 份备份：app.log（最新）、app.log.1、app.log.2、app.log.3
      （超过 3 份就把最老的 app.log.3 删掉，保证日志总大小可控，不会把磁盘写爆）

【幂等性】
    setup_logging() 被多次调用不会导致"同一条日志打两遍"——
    因为我们开头先把 root.logger 上已有的 handler 全清掉再挂新的。
"""

import logging
import os
# [MOD-注释增强-20260901] RotatingFileHandler：Python 标准库自带的"按大小滚动的文件日志处理器"
#                            不用装任何第三方包，开箱即用。
from logging.handlers import RotatingFileHandler

# [MOD-注释增强-20260901] 日志格式：%(name)s 是 logger 名（可以看出是哪个模块打的）
#                            %(levelname)-7s 让日志级别（INFO/WARN/ERROR...）左对齐占 7 字符，整齐好看
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
LOG_DIR = "./logs"
LOG_FILE = "app.log"


def setup_logging(level: int = logging.INFO) -> None:
    """[MOD-注释增强-20260901]
    初始化根日志器 —— 控制台 + 滚动文件双路输出。
    在应用启动 lifespan 最开始调用一次即可。

    :param level: 全局最低日志级别，默认 INFO（比它低的 DEBUG 级别就不会打出来）。
                  调试时可以传 logging.DEBUG，能看到更详细的 SQL/调用链。
    """

    # [MOD-注释增强-20260901] 【步骤 0】拿 Python 根 logger + 设置全局最低级别
    root = logging.getLogger()
    root.setLevel(level)

    # [MOD-注释增强-20260901] 【步骤 1】清掉已有的 handlers
    #  防止 uvicorn / pytest 已经偷偷给 root 挂了 handler，
    #  我们再挂一遍 → 同一条日志打两遍（最常见的日志坑）。
    #  要先 list() 拷贝一份再删，不然一边遍历一边删会漏。
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # [MOD-注释增强-20260901] 【步骤 2】创建统一的 Formatter（两个 Handler 共用同一个格式）
    formatter = logging.Formatter(LOG_FORMAT)

    # [MOD-注释增强-20260901] 【步骤 3】挂控制台 Handler（永远成功，不用 try）
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # [MOD-注释增强-20260901] 【步骤 4】挂滚动文件 Handler（可能失败，比如 Docker 里目录只读）
    #  失败了只打警告，不阻塞启动——大不了只打控制台。
    try:
        os.makedirs(LOG_DIR, exist_ok=True)  # exist_ok=True：目录已存在也不报错
        file_handler = RotatingFileHandler(
            os.path.join(LOG_DIR, LOG_FILE),
            maxBytes=5 * 1024 * 1024,  # [MOD-注释增强-20260901] 单文件最大 5MB
            backupCount=3,             # [MOD-注释增强-20260901] 最多保留 3 份历史备份
            encoding="utf-8",          # [MOD-注释增强-20260901] 必须 utf-8，中文不乱码
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # [MOD-注释增强-20260901] 目录不可写 / 权限不足 / 磁盘满 → 只打个警告，不让应用崩
        logging.getLogger("app.logging").warning(
            "无法创建日志文件，仅使用控制台输出: %s", exc
        )
