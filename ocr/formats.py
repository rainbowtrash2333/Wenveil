"""OCR 输入格式分类。

该模块只包含标准库数据结构，供 OCR、统一 Workflow 和文件前置转换层共享，
避免各入口维护互相漂移的扩展名白名单。
"""

from __future__ import annotations


IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif",
})

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rtf",
})

CODE_EXTENSIONS = frozenset({
    ".html", ".htm", ".xml", ".json",
})

DOCLING_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx",
})

LEGACY_OFFICE_EXTENSIONS = frozenset({
    ".doc", ".xls", ".ppt",
})

MESSAGE_EXTENSIONS = frozenset({".msg"})

ARCHIVE_EXTENSIONS = frozenset({
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz2",
    ".xz", ".txz", ".cab", ".iso",
})

SPECIAL_EXTENSIONS = (
    LEGACY_OFFICE_EXTENSIONS
    | MESSAGE_EXTENSIONS
    | ARCHIVE_EXTENSIONS
)

# The tuple is ordered for deterministic configuration defaults and help text.
SUPPORTED_FILE_EXTENSIONS = (
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".doc", ".xls", ".ppt", ".msg",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz2",
    ".xz", ".txz", ".cab", ".iso",
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif",
    ".txt", ".md", ".markdown", ".rtf",
    ".html", ".htm", ".xml", ".json", ".csv",
)

SUPPORTED_FILE_EXTENSION_SET = frozenset(SUPPORTED_FILE_EXTENSIONS)


__all__ = [
    "ARCHIVE_EXTENSIONS",
    "CODE_EXTENSIONS",
    "DOCLING_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "LEGACY_OFFICE_EXTENSIONS",
    "MESSAGE_EXTENSIONS",
    "SPECIAL_EXTENSIONS",
    "SUPPORTED_FILE_EXTENSIONS",
    "SUPPORTED_FILE_EXTENSION_SET",
    "TEXT_EXTENSIONS",
]
