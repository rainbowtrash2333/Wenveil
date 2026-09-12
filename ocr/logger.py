"""
日志系统模块。

负责根据配置初始化 Python 标准 logging，同时输出到控制台和文件。
"""

import logging
import sys
from pathlib import Path

from ocr.config import LoggingConfig


def setup_logging(config: LoggingConfig) -> logging.Logger:
    """初始化日志系统。

    创建名为 "aicanread.ocr" 的 logger 实例，配置日志级别和格式化器，
    同时添加控制台处理器和可选的日志文件处理器。

    Args:
        config: 日志配置对象，包含级别、格式、输出文件等设置。

    Returns:
        配置完成的 Logger 实例。
    """
    logger = logging.getLogger("aicanread.ocr")
    logger.setLevel(getattr(logging, config.level.upper(), logging.INFO))

    formatter = logging.Formatter(config.format)

    # 清除已有的处理器，避免重复添加
    if logger.handlers:
        logger.handlers.clear()

    # 控制台输出处理器 —— 确保实时看到运行状态
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出处理器 —— 持久化日志便于离线排查
    if config.file:
        log_path = Path(config.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
