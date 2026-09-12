"""OCR 文本的确定性整理，不执行实体识别或脱敏。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from common.text_normalizer import TextNormalizer
from common.safety import safe_id


_IMAGE_COMMENT = re.compile(r"^\s*<!--\s*image\s*-->\s*$", re.IGNORECASE)
_PAGE_MARKER = re.compile(r"^\s*(?:\f|第\s*\d+\s*页)\s*$")
_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".rtf"}


@dataclass(frozen=True, slots=True)
class OrganizeOptions:
    """文本整理选项。"""

    remove_image_comments: bool = True
    convert_page_markers: bool = True
    max_blank_lines: int = 2


class TextOrganizer:
    """将 OCR 文本整理为稳定、可读的 Markdown/纯文本。"""

    def __init__(self, options: OrganizeOptions | None = None):
        self.options = options or OrganizeOptions()
        if self.options.max_blank_lines < 1:
            raise ValueError("max_blank_lines must be at least 1")
        self.normalizer = TextNormalizer()

    def organize_text(self, text: str) -> str:
        """整理一段文本，保留 Markdown 结构，不识别或替换实体。"""

        normalized = self.normalizer.normalize(text)
        lines: list[str] = []
        blank_lines = 0
        in_code = False

        for raw_line in normalized.split("\n"):
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code = not in_code

            line = raw_line
            if not in_code:
                if self.options.remove_image_comments and _IMAGE_COMMENT.match(line):
                    continue
                if self.options.convert_page_markers and _PAGE_MARKER.match(line):
                    line = "---"
                line = line.rstrip()

            if not line.strip():
                blank_lines += 1
                if blank_lines > self.options.max_blank_lines:
                    continue
            else:
                blank_lines = 0
            lines.append(line)

        result = "\n".join(lines).strip()
        return f"{result}\n" if result else ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _safe_output_name(path: Path) -> str:
    return f"document-{safe_id(path.name)}.organized.md"


def organize_path(
    input_path: str | Path,
    output_path: str | Path = "test-artifacts/organized-outputs",
    options: OrganizeOptions | None = None,
) -> list[Path]:
    """整理一个文件或目录，返回生成文件列表。"""

    source = Path(input_path)
    target = Path(output_path)
    organizer = TextOrganizer(options)

    if source.is_file():
        destination = target if target.suffix.lower() in {".md", ".txt"} else target / _safe_output_name(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(organizer.organize_text(_read_text(source)), encoding="utf-8")
        return [destination]

    if not source.is_dir():
        raise FileNotFoundError(f"input path not found: {source}")

    target.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in _TEXT_EXTENSIONS
    )
    outputs: list[Path] = []
    for path in files:
        destination = target / _safe_output_name(path)
        destination.write_text(organizer.organize_text(_read_text(path)), encoding="utf-8")
        outputs.append(destination)
    return outputs
