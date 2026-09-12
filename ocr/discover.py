"""
文件发现模块。

给定根目录，将其下一级子目录识别为"项目目录"，并在每个项目目录中递归
收集所有受支持的文件，按项目组织返回。
"""

import logging
from pathlib import Path
from typing import Dict, List, Set

from ocr.config import AppConfig
from ocr.safety import safe_id

logger = logging.getLogger("wenveil.ocr")


def discover_projects(config: AppConfig) -> Dict[str, List[Path]]:
    """发现根目录下的所有项目及其包含的支持文件。

    遍历根目录的**一级子目录**，将每个子目录视为一个独立的"项目"。
    对每个项目递归搜索所有文件，筛选出扩展名在配置白名单中的文件。
    隐藏目录（以 . 开头）将被跳过。

    Args:
        config: 应用配置，包含根目录路径和支持的扩展名列表。

    Returns:
        一个字典，键为项目名称（目录名），值为该项目中所有支持文件的 Path 列表。
        每个项目的文件列表按路径排序，确保处理顺序确定。

    Raises:
        FileNotFoundError: 根目录不存在时抛出。
        NotADirectoryError: 根路径不是一个目录时抛出。
    """
    root = Path(config.root_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"根目录未找到: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"根路径不是目录: {root}")

    # 规范化扩展名：统一转为小写并确保有点号前缀
    extensions_lower: Set[str] = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in config.supported_extensions
    }

    projects: Dict[str, List[Path]] = {}

    for entry in sorted(root.iterdir()):
        # 跳过非目录、隐藏目录、输出目录
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name == "merged":
            continue

        # 递归搜索该项目目录下的所有支持文件
        project_files: List[Path] = []
        for file_path in sorted(entry.rglob("*")):
            if not file_path.is_file() or file_path.suffix.lower() not in extensions_lower:
                continue
            # 跳过 Office 临时/锁文件（~$xxx.docx、.~xxx.xlsx 等），无内容且无法解析
            fname = file_path.name
            if fname.startswith("~$") or fname.startswith(".~"):
                logger.debug("跳过临时文件 file_id=%s", safe_id(fname))
                continue
            project_files.append(file_path)

        if project_files:
            projects[entry.name] = project_files
            logger.info(
                "发现项目 id=%s: %d 个文件", safe_id(entry.name), len(project_files)
            )
        else:
            logger.warning("项目 id=%s 没有支持的文件，已跳过", safe_id(entry.name))

    if not projects:
        logger.warning("输入目录中未发现任何包含支持文件的项目")

    return projects
