"""Read-only audits for residual PII and structural damage in masked Markdown.

The audit deliberately does not use the pipeline's recognizers.  It is a
post-condition check: it scans the text that would be handed to a downstream
consumer and reports suspicious *shapes* without copying the matched value
into an issue, log message, or exception.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# Both the legacy job-scoped token and the compact semantic token are valid.
TOKEN_PATTERN = re.compile(
    r"(?:⟦[A-Z][A-Z0-9_]*:\d{6}(?::[0-9a-f]{12})?⟧|"
    r"⟦(?:机构\d+(?:-(?:别名|子公司|分公司)\d+)?(?:-\d+)?|"
    r"人员\d+(?:-\d+)?|电话\d+(?:-\d+)?|邮箱\d+(?:-\d+)?|账号\d+(?:-\d+)?|"
    r"证件\d+(?:-\d+)?|地址\d+(?:-\d+)?|项目\d+(?:-\d+)?|部门\d+(?:-\d+)?|"
    r"合同\d+(?:-\d+)?|日期\d+(?:-\d+)?|金额\d+(?:-\d+)?|数字\d+(?:-\d+)?|"
    r"实体\d+(?:-\d+)?)⟧)"
)
_TOKEN_CANDIDATE_PATTERN = re.compile(r"⟦[^\n⟧]*⟧")

_MOBILE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])1[3-9](?:[ \t\-－—]?\d){9}(?![ \t\-－—]?\d)"
)
_LANDLINE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])0\d{2,3}(?:[ \t\-－—]?\d){7,8}(?![ \t\-－—]?\d)"
)
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"[A-Za-z0-9._%+\-]+\s*@\s*[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}"
    r"(?![A-Za-z0-9._%+\-])"
)

# The broad run is intentionally checked after compacting OCR separators.  A
# date and checksum are not required here: an invalid-but-ID-shaped value is
# still worth auditing, and false positives are safer than silently accepting
# a residual identity number.
_ID_RUN_PATTERN = re.compile(r"(?<![0-9Xx])[0-9Xx][0-9Xx \t\-－—]{13,24}[0-9Xx](?![0-9Xx])")

_ACCOUNT_LABEL_PATTERN = re.compile(
    r"(?:银行账号|银行账户|开户账号|开户账户|持有人账号|持有人账户|"
    r"账号|帐号|账户(?:号|号码)?|卡号)"
    r"\s*[:：]?\s*[【\[]?(?P<value>(?:\d[ \t\-－—]?){9,18}\d)[】\]]?"
)
_ACCOUNT_GENERIC_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d{16,19}(?![A-Za-z0-9])")
_ACCOUNT_GROUPED_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:\d[ \t\-－—]?){15,18}\d(?![ \t\-－—]?\d)"
)

_CONTRACT_PREFIX_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:HT|HT号)\s*[-－—:/]?\s*"
    r"(?:19|20)?\d{2,4}(?:\s*[-－—/]\s*\d+)+(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CONTRACT_LABEL_PATTERN = re.compile(
    r"(?:合同编号|合同号|协议编号|协议号)\s*[:：#]?\s*"
    r"(?!⟦)[A-Za-z0-9][A-Za-z0-9._/－—\-]{2,}"
)

_ADDRESS_LABELS = (
    "住所地/通信地址",
    "注册地址",
    "通讯地址",
    "联系地址",
    "办公地址",
    "通信地址",
    "送达地址",
    "收件地址",
    "住所地",
    "住址",
    "地址",
)
_ADDRESS_LABEL_RE = "|".join(re.escape(value) for value in sorted(_ADDRESS_LABELS, key=len, reverse=True))
_NEXT_FIELD_LABEL = (
    r"(?:电话|联系电话|座机|传真|手机|邮箱|电子邮箱|联系地址|通讯地址|"
    r"注册地址|办公地址|法定代表人|联系人|经办/代理人姓名|经办人姓名)"
)
_ADDRESS_FIELD_PATTERN = re.compile(
    rf"(?:{_ADDRESS_LABEL_RE})\s*(?:[:：]|为|是)\s*(?P<value>[^|\n；;，,。]+?)"
    rf"(?=\s*(?:已通过|{_NEXT_FIELD_LABEL}\s*[:：为是])|[；;，,。]|$)"
)
_EMPTY_ADDRESS_FIELD_PATTERN = re.compile(
    rf"(?:{_ADDRESS_LABEL_RE})\s*[:：]\s*(?:\|\s*)?$"
)

_AMOUNT_LIKE_PATTERN = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*"
    r"(?:万亿元|亿元|万元|人民币|美元|港币|元|万|亿)"
)

_SAFE_REDACTION_PATTERN = re.compile(
    r"^(?:[*#Xx_\-—–]+|保密|已脱敏|已隐藏|REDACTED|redacted|脱敏)$"
)


@dataclass(frozen=True, slots=True)
class AuditIssue:
    """One safe-to-display audit finding.

    ``line`` is one-based.  No matched text, offsets, or surrounding context
    are stored, so converting an issue to ``repr``/JSON cannot disclose the
    residual value that triggered it.
    """

    line: int
    category: str
    summary: str

    @property
    def line_number(self) -> int:
        """Compatibility alias for callers that prefer the explicit name."""

        return self.line

    @property
    def line_no(self) -> int:
        """Compatibility alias for callers that use ``line_no``."""

        return self.line

    def to_dict(self) -> dict[str, object]:
        """Return the complete, non-sensitive public representation."""

        return {"line": self.line, "category": self.category, "summary": self.summary}


@dataclass(frozen=True, slots=True)
class _Finding:
    start: int
    end: int
    category: str
    summary: str


def _normalized_view(line: str) -> str:
    """Normalize only the disposable scan view, never the user's text."""

    return unicodedata.normalize("NFKC", line)


def _without_token_candidates(line: str) -> str:
    """Blank token candidates while preserving positions for regex boundaries."""

    return _TOKEN_CANDIDATE_PATTERN.sub(lambda match: " " * len(match.group(0)), line)


def _compact_ocr_digits(value: str) -> str:
    return re.sub(r"[ \t\-－—]", "", value)


def _overlaps(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in ranges)


def _looks_like_id(value: str) -> bool:
    compact = _compact_ocr_digits(value)
    if len(compact) == 18:
        if not compact[:17].isdigit() or compact[-1] not in "0123456789Xx":
            return False
        try:
            # Avoid treating arbitrary long numeric identifiers as IDs when
            # their date component cannot even be a calendar date.
            import datetime as _datetime

            _datetime.date(int(compact[6:10]), int(compact[10:12]), int(compact[12:14]))
        except (TypeError, ValueError):
            return False
        return True
    if len(compact) == 15 and compact.isdigit():
        try:
            import datetime as _datetime

            _datetime.date(1900 + int(compact[6:8]), int(compact[8:10]), int(compact[10:12]))
        except (TypeError, ValueError):
            return False
        return True
    return False


def _is_safe_masked_value(value: str) -> bool:
    stripped = value.strip().strip(";；,，。．:：|()（）[]【】")
    if not stripped:
        return False
    if _SAFE_REDACTION_PATTERN.fullmatch(stripped):
        return True
    # A field is considered covered when it consists only of valid tokens and
    # harmless punctuation.  Any remaining CJK/ASCII/digit character means
    # that some of the original field value is still visible.
    without_tokens = TOKEN_PATTERN.sub("", stripped)
    without_tokens = re.sub(r"[\s,，、;；。．:：|()（）[\]【】<>《》/\\'\"“”‘’\-—–_]+", "", without_tokens)
    return bool(TOKEN_PATTERN.search(stripped)) and not without_tokens


def _finding(start: int, end: int, category: str, summary: str) -> _Finding:
    return _Finding(start=start, end=end, category=category, summary=summary)


def _scan_sensitive_line(line: str) -> list[_Finding]:
    """Return shape findings for one line; matched values never leave here."""

    scan = _without_token_candidates(line)
    findings: list[_Finding] = []

    for match in _MOBILE_PATTERN.finditer(scan):
        if len(_compact_ocr_digits(match.group(0))) == 11:
            findings.append(_finding(match.start(), match.end(), "PHONE", "发现疑似未脱敏手机号（11位数字）"))

    for match in _LANDLINE_PATTERN.finditer(scan):
        compact = _compact_ocr_digits(match.group(0))
        if 10 <= len(compact) <= 12:
            findings.append(_finding(match.start(), match.end(), "LANDLINE", "发现疑似未脱敏座机号"))

    for match in _EMAIL_PATTERN.finditer(scan):
        findings.append(_finding(match.start(), match.end(), "EMAIL", "发现疑似未脱敏邮箱地址"))

    id_ranges: list[tuple[int, int]] = []
    for match in _ID_RUN_PATTERN.finditer(scan):
        if _looks_like_id(match.group(0)):
            id_ranges.append((match.start(), match.end()))
            findings.append(_finding(match.start(), match.end(), "ID_CARD", "发现疑似未脱敏身份证号"))

    account_ranges: list[tuple[int, int]] = []
    for match in _ACCOUNT_LABEL_PATTERN.finditer(scan):
        start, end = match.span("value")
        value = match.group("value")
        if len(_compact_ocr_digits(value)) >= 10:
            account_ranges.append((start, end))
            findings.append(_finding(start, end, "BANK_ACCOUNT", "发现疑似未脱敏银行账号"))

    for match in _ACCOUNT_GENERIC_PATTERN.finditer(scan):
        start, end = match.span(0)
        if _overlaps(start, end, id_ranges):
            continue
        # Amounts and explicitly structured contract numbers are not bank
        # accounts merely because they contain many digits.
        if _overlaps(start, end, account_ranges):
            continue
        if any(match.start() >= amount.start() and match.end() <= amount.end() for amount in _AMOUNT_LIKE_PATTERN.finditer(scan)):
            continue
        account_ranges.append((start, end))
        findings.append(_finding(start, end, "BANK_ACCOUNT", "发现疑似未脱敏银行账号"))

    for match in _ACCOUNT_GROUPED_PATTERN.finditer(scan):
        start, end = match.span(0)
        compact = _compact_ocr_digits(match.group(0))
        if not 16 <= len(compact) <= 19:
            continue
        if _overlaps(start, end, id_ranges) or _overlaps(start, end, account_ranges):
            continue
        account_ranges.append((start, end))
        findings.append(_finding(start, end, "BANK_ACCOUNT", "发现疑似未脱敏银行账号"))

    for match in _CONTRACT_PREFIX_PATTERN.finditer(scan):
        findings.append(_finding(match.start(), match.end(), "CONTRACT_ID", "发现疑似未脱敏合同编号"))
    for match in _CONTRACT_LABEL_PATTERN.finditer(scan):
        findings.append(_finding(match.start(), match.end(), "CONTRACT_ID", "发现疑似未脱敏合同编号"))

    # A labelled address is auditable without attempting to infer arbitrary
    # unlabelled prose as an address.
    for match in _ADDRESS_FIELD_PATTERN.finditer(line):
        value = match.group("value")
        if value.strip() and not _is_safe_masked_value(value):
            findings.append(_finding(match.start("value"), match.end("value"), "ADDRESS", "地址字段的值未被脱敏 Token 覆盖"))

    return findings


def _pipe_positions(line: str) -> list[int]:
    positions: list[int] = []
    escaped = False
    in_code = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "`":
            in_code = not in_code
            continue
        if char == "|" and not in_code:
            positions.append(index)
    return positions


def _split_pipe_row(line: str) -> tuple[list[str], bool, bool] | None:
    positions = _pipe_positions(line)
    if not positions:
        return None
    leading = positions[0] == 0
    trailing = positions[-1] == len(line) - 1
    cells: list[str] = []
    cursor = 0
    for position in positions:
        cells.append(line[cursor:position].strip())
        cursor = position + 1
    cells.append(line[cursor:].strip())
    if leading:
        cells = cells[1:]
    if trailing:
        cells = cells[:-1]
    return cells, leading, trailing


def _is_separator_cell(cell: str) -> bool:
    return bool(re.fullmatch(r":?-{3,}:?", cell.strip()))


def _looks_like_separator(line: str) -> bool:
    parsed = _split_pipe_row(line)
    if parsed is None:
        return False
    cells, _, _ = parsed
    return len(cells) >= 2 and all(_is_separator_cell(cell) for cell in cells)


def _is_fence(line: str) -> bool:
    return bool(re.match(r"^\s{0,3}(?:`{3,}|~{3,})", line))


def _audit_tables(lines: list[str]) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    i = 0
    in_fence = False
    while i < len(lines):
        line = lines[i]
        if _is_fence(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue

        if i + 1 >= len(lines) or _split_pipe_row(line) is None or not _looks_like_separator(lines[i + 1]):
            i += 1
            continue

        header = _split_pipe_row(line)
        separator = _split_pipe_row(lines[i + 1])
        assert header is not None and separator is not None
        header_cells, _, _ = header
        separator_cells, _, _ = separator
        expected = len(separator_cells)
        if expected < 2 or not all(_is_separator_cell(cell) for cell in separator_cells):
            issues.append(AuditIssue(i + 2, "TABLE_PIPE", "Markdown 表格分隔行的管线/分隔符格式异常"))
        if len(header_cells) != expected:
            issues.append(AuditIssue(i + 1, "TABLE_PIPE", "Markdown 表格表头列数与分隔行不一致"))

        # Validate the data rows belonging to this table.  A row without a
        # pipe ends the table, which preserves normal Markdown paragraphs.
        row_index = i + 2
        while row_index < len(lines):
            if _is_fence(lines[row_index]) or not lines[row_index].strip():
                break
            parsed = _split_pipe_row(lines[row_index])
            if parsed is None:
                break
            cells, _, _ = parsed
            if len(cells) != expected:
                issues.append(
                    AuditIssue(
                        row_index + 1,
                        "TABLE_PIPE",
                        f"Markdown 表格列数异常（期望 {expected} 列，实际 {len(cells)} 列）",
                    )
                )
            row_index += 1
        i = max(i + 1, row_index)
    return issues


def _audit_table_address_fields(lines: list[str]) -> list[AuditIssue]:
    """Check address/value cell pairs only on plausible table rows."""

    issues: list[AuditIssue] = []
    address_labels = set(_ADDRESS_LABELS)
    in_fence = False
    for line_number, line in enumerate(lines, start=1):
        if _is_fence(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        parsed = _split_pipe_row(line)
        if parsed is None:
            continue
        cells, _, _ = parsed
        for index, cell in enumerate(cells[:-1]):
            label = cell.strip().rstrip(":：")
            if label in address_labels:
                value_index = index + 1
                while value_index < len(cells) and cells[value_index].strip() in {":", "："}:
                    value_index += 1
                if value_index >= len(cells):
                    continue
                value = cells[value_index].strip()
                if value and not _is_safe_masked_value(value):
                    issues.append(AuditIssue(line_number, "ADDRESS", "地址字段的值未被脱敏 Token 覆盖"))
    return issues


def _audit_empty_address_continuations(lines: list[str]) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    for index, line in enumerate(lines):
        if "|" in line or not _EMPTY_ADDRESS_FIELD_PATTERN.search(_normalized_view(line)):
            continue
        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index >= len(lines):
            continue
        candidate = lines[next_index].strip()
        if not candidate or candidate.startswith(("#", "-", "*", ">")) or _is_fence(candidate):
            continue
        if _is_safe_masked_value(candidate):
            continue
        if re.match(rf"^(?:{_NEXT_FIELD_LABEL})\s*[:：]", _normalized_view(candidate)):
            continue
        issues.append(AuditIssue(next_index + 1, "ADDRESS", "地址字段的续行值未被脱敏 Token 覆盖"))
    return issues


def _audit_tokens(lines: list[str]) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    for line_number, line in enumerate(lines, start=1):
        for candidate in _TOKEN_CANDIDATE_PATTERN.finditer(line):
            if TOKEN_PATTERN.fullmatch(candidate.group(0)) is None:
                issues.append(AuditIssue(line_number, "TOKEN", "脱敏 Token 格式无效"))
        if line.count("⟦") != line.count("⟧"):
            issues.append(AuditIssue(line_number, "TOKEN", "脱敏 Token 分隔符不成对"))
    return issues


def _deduplicate_and_sort(issues: Iterable[AuditIssue]) -> list[AuditIssue]:
    unique = {(issue.line, issue.category, issue.summary): issue for issue in issues}
    category_order = {
        "TOKEN": 0,
        "TABLE_PIPE": 1,
        "ID_CARD": 2,
        "PHONE": 3,
        "LANDLINE": 4,
        "EMAIL": 5,
        "BANK_ACCOUNT": 6,
        "CONTRACT_ID": 7,
        "ADDRESS": 8,
    }
    return sorted(unique.values(), key=lambda issue: (issue.line, category_order.get(issue.category, 99), issue.category))


def audit_masked_text(masked_text: str) -> list[AuditIssue]:
    """Audit masked Markdown and return only safe structured findings.

    The function is pure and does not mutate the input.  Line numbers are
    one-based; an empty result means no configured residual shape or Markdown
    structural defect was found.
    """

    if not isinstance(masked_text, str):
        raise TypeError("masked_text must be a string")
    lines = masked_text.splitlines()
    issues: list[AuditIssue] = []
    issues.extend(_audit_tokens(lines))
    # Sensitive scanners operate on an NFKC view so OCR full-width digits are
    # still caught, while address checks retain the original line for
    # token-only validation.
    issues.extend(
        AuditIssue(line_number, finding.category, finding.summary)
        for line_number, line in enumerate(lines, start=1)
        for finding in _scan_sensitive_line(_normalized_view(line))
    )
    issues.extend(_audit_tables(lines))
    issues.extend(_audit_table_address_fields(lines))
    issues.extend(_audit_empty_address_continuations(lines))
    return _deduplicate_and_sort(issues)


def audit_file(path: str | Path, *, encoding: str = "utf-8-sig") -> list[AuditIssue]:
    """Read a masked text file and run :func:`audit_masked_text`."""

    return audit_masked_text(Path(path).read_text(encoding=encoding))


# Short aliases keep the module convenient for scripts without changing the
# public pipeline or CLI surface.
audit = audit_masked_text
audit_masked = audit_masked_text


__all__ = [
    "AuditIssue",
    "TOKEN_PATTERN",
    "audit",
    "audit_file",
    "audit_masked",
    "audit_masked_text",
]
