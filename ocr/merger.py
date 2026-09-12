"""
Markdown 合并模块。

将同一项目下所有文件的转换结果合并为一个完整的 Markdown 文档，
各文件内容以一级标题分隔。
"""

import logging
import re
from pathlib import Path
from typing import List, Tuple

from ocr.config import OutputConfig
from ocr.safety import safe_id

logger = logging.getLogger("wenveil.ocr")


def merge_results(
    project_name: str,
    project_root: Path,
    converted_files: List[Tuple[Path, str]],
) -> str:
    """将转换后的文件内容合并为单个 Markdown 文档。

    生成结构：
     1. 标题行（项目名称作为一级标题）
     2. 各文件内容 —— 以文件名作为一级标题分隔

    Args:
        project_name: 项目名称，用作一级标题。
        project_root: 项目根目录路径，用于计算相对路径。
        converted_files: (文件路径, Markdown内容) 的列表，按路径排序。
        output_config: 输出配置，包含文件名模板和分隔符模板。

    Returns:
        合并后的完整 Markdown 字符串。
    """
    # 按文件路径排序确保输出顺序确定
    converted_files.sort(key=lambda x: str(x[0]))

    parts: List[str] = [f"# document-project-{safe_id(project_name)}", ""]

    for filepath, content in converted_files:
        rel_path = filepath.relative_to(project_root)
        parts.append(f"# ---- document-{safe_id(rel_path)} ----")
        parts.append("")
        cleaned = re.sub(r"<!--\s*image\s*-->", "", content).strip()
        parts.append(cleaned)
        parts.append("")

    return "\n".join(parts)


def write_merged_output(
    project_name: str,
    output_dir: Path,
    merged_content: str,
    output_config: OutputConfig,
) -> Path:
    """将合并后的 Markdown 内容写入独立的 OCR 输出目录。

    Args:
        project_name: 项目名称，用于生成文件名。
        output_dir: 输出目录路径，与原始输入目录分离。
        merged_content: 合并后的 Markdown 文本。
        output_config: 输出配置，包含文件名模板和编码设置。

    Returns:
        输出文件的 Path 对象。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    project_id = safe_id(project_name)
    filename = output_config.merged_filename_template.format(
        project_name=project_id,
        safe_id=project_id,
    )
    output_path = output_dir / filename
    output_path.write_text(merged_content, encoding=output_config.encoding)
    logger.info("合并输出已写入: %s", output_path)
    return output_path
