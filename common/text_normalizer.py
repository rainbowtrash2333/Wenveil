from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


_UNICODE_SPACES = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_HAN_SPACE = re.compile(r"(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])")
_SPACE_BEFORE_PUNCT = re.compile(r" +([，。！？；：、,.!?;:）)】》」』])")
_SPACE_AFTER_OPEN = re.compile(r"([（(【《「『]) +")
_SPACED_ID = re.compile(
    r"(?<!\d)(\d{6})\s+(\d{4})\s+(\d{4})\s+(\d{3}[0-9Xx])(?!\d)"
)
_SPACED_ID_FLEX = re.compile(r"(?<!\d)((?:\d\s*){17}[0-9Xx])(?!\d)")
_AMOUNT_WITH_UNIT = re.compile(
    r"(?<!\d)([+-]?(?:\d[\d,\s]*\d|\d)(?:\s*\.\s*\d+)?)[ \t]*"
    r"(万亿元|亿元|万元|人民币|美元|港币|元|万|亿)(?!\d)"
)
_SPACED_PHONE = re.compile(r"(?<!\d)(1\d{2})\s+(\d{4})\s+(\d{4})(?!\d)")


@dataclass(frozen=True, slots=True)
class LineInfo:
    kind: str
    text: str


class TextNormalizer:
    """Conservative, idempotent OCR/Markdown normalizer.

    The normalizer changes only known formatting noise.  It keeps Markdown
    structure and never participates in entity replacement, so all later
    offsets refer to one stable normalized string.
    """

    def __init__(self, options: dict[str, Any] | None = None):
        self.options = {
            "unicode_nfkc": True,
            "remove_han_spaces": True,
            "merge_broken_lines": True,
            "repair_id_card": True,
            "repair_amount": True,
            "repair_phone": True,
            **(options or {}),
        }

    def normalize(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        value = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.options.get("unicode_nfkc", True):
            value = unicodedata.normalize("NFKC", value)
        value = _UNICODE_SPACES.sub(" ", value).replace("\t", " ")
        lines = value.split("\n")
        lines = self._normalize_lines(lines)
        if self.options.get("merge_broken_lines", True):
            lines = self._merge_broken_lines(lines)
        return "\n".join(lines)

    def _normalize_lines(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        in_code = False
        for raw in lines:
            if raw.lstrip().startswith("```"):
                in_code = not in_code
                result.append(raw.rstrip())
                continue
            if in_code:
                result.append(raw.rstrip(" \t"))
                continue
            kind = self._classify(raw)
            if kind == "EMPTY":
                result.append("")
            elif kind == "TABLE":
                result.append(self._normalize_table(raw))
            else:
                result.append(self._normalize_plain(raw, preserve_indent=kind in {"LIST", "HEADING"}))
        return result

    @staticmethod
    def _classify(line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return "EMPTY"
        if re.match(r"^#{1,6}\s", stripped):
            return "HEADING"
        if "|" in stripped and stripped.startswith("|"):
            return "TABLE"
        if re.match(r"^(?:[-*+]|\d+[.)]|[一二三四五六七八九十百千万]+[、.])\s*", stripped):
            return "LIST"
        if re.match(r"^(?:\f|[-=_]{3,}|第\s*\d+\s*页)$", stripped):
            return "PAGE_BREAK"
        if re.match(r"^[^:：|]{1,40}[:：]\s*\S", stripped):
            return "FIELD"
        return "PARAGRAPH"

    def _normalize_table(self, line: str) -> str:
        parts = line.split("|")
        normalized: list[str] = []
        for index, part in enumerate(parts):
            if index == 0 or index == len(parts) - 1:
                normalized.append(part.rstrip() if not part.strip() else part.strip())
            else:
                normalized.append(self._normalize_plain(part, preserve_indent=False).strip())
        return "|".join(normalized)

    def _normalize_plain(self, line: str, *, preserve_indent: bool) -> str:
        value = line.rstrip(" \t")
        leading = re.match(r"^\s*", value).group(0) if preserve_indent else ""
        body = value[len(leading) :] if preserve_indent else value.strip()
        body = _MULTI_SPACE.sub(" ", body)
        body = self._repair_structured(body)
        if self.options.get("remove_han_spaces", True):
            body = _HAN_SPACE.sub("", body)
        body = _SPACE_BEFORE_PUNCT.sub(r"\1", body)
        body = _SPACE_AFTER_OPEN.sub(r"\1", body)
        return leading + body.strip()

    def _repair_structured(self, value: str) -> str:
        if self.options.get("repair_id_card", True):
            value = _SPACED_ID.sub(lambda m: "".join(m.groups()), value)
            value = _SPACED_ID_FLEX.sub(lambda m: re.sub(r"\s+", "", m.group(1)), value)
        if self.options.get("repair_amount", True):
            def compact_amount(match: re.Match[str]) -> str:
                number = re.sub(r"\s+", "", match.group(1))
                return f"{number}{match.group(2)}"

            value = _AMOUNT_WITH_UNIT.sub(compact_amount, value)
        if self.options.get("repair_phone", True):
            value = _SPACED_PHONE.sub(r"\1\2\3", value)
        return value

    def _merge_broken_lines(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        for current in lines:
            if not result or not current.strip():
                result.append(current)
                continue
            previous = result[-1]
            if self._should_merge(previous, current):
                left = previous.rstrip()
                right = current.lstrip()
                separator = " " if left[-1:].isascii() and right[:1].isascii() and left[-1:].isalpha() and right[:1].isalpha() else ""
                result[-1] = self._normalize_plain(left + separator + right, preserve_indent=False)
            else:
                result.append(current)
        return result

    def _should_merge(self, previous: str, current: str) -> bool:
        if not previous.strip() or not current.strip():
            return False
        left = previous.rstrip()
        right = current.lstrip()
        joined = left + right
        boundary = len(left)
        structured_patterns = (_SPACED_ID_FLEX, _AMOUNT_WITH_UNIT, _SPACED_PHONE)
        if any(
            (match := pattern.search(joined)) is not None
            and match.start() < boundary < match.end()
            for pattern in structured_patterns
        ):
            return True
        if self._classify(previous) != "PARAGRAPH" or self._classify(current) != "PARAGRAPH":
            return False
        if re.search(r"[。！？；：.!?;:]$", previous.rstrip()):
            return False
        current_stripped = current.lstrip()
        if re.match(r"^(?:#{1,6}\s|[-*+>]\s|\d+[.)]\s|[一二三四五六七八九十百千万]+[、.])", current_stripped):
            return False
        if re.match(r"^(?:第\s*\d+|附件\s*\d+|日期\s*[:：])", current_stripped):
            return False
        return True
