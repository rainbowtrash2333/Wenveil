"""安全标识工具，避免日志和输出文件名回显原始名称。"""

from __future__ import annotations

import hashlib
from pathlib import Path


def safe_id(value: str | Path, length: int = 12) -> str:
    """为路径或名称生成稳定、不可逆的短标识。"""

    if length < 8:
        raise ValueError("safe id length must be at least 8")
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest[:length]
