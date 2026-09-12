from __future__ import annotations

import re

from ..models import Span


class AddressRecognizer:
    """Extract address values from common audit-document field labels."""

    _labels = (
        "住所地/通信地址",
        "通讯地址",
        "联系地址",
        "注册地址",
        "办公地址",
        "通信地址",
        "住所地",
        "住所",
        "地址",
    )
    _next_label = r"(?:电话|联系电话|手机|邮箱|电子邮箱|传真|法定代表人|联系人|经办/代理人姓名|经办人姓名)"
    _protected_identifier = re.compile(
        r"(?<![0-9A-Z])(?:[0-9A-Z]{18}|\d{15,19})(?![0-9A-Z])",
        re.IGNORECASE,
    )

    def __init__(self, *, priority: int = 75, anonymize: bool = True, protect: bool = False):
        labels = "|".join(re.escape(label) for label in self._labels)
        self.pattern = re.compile(
            rf"(?:{labels})\s*[:：]\s*(?:【(?P<bracket>[^】\n|]+)】|\[(?P<square>[^\]\n|]+)\]|"
            rf"(?P<plain>[^\n|；;]+?)(?=\s*(?:{self._next_label})\s*[:：]|\s*[|\n]|$))"
        )
        # OCR text frequently turns prose fields into ``地址为...`` or
        # ``住所为...``.  A dedicated pattern keeps the field value bounded
        # by the next clause instead of swallowing the rest of the paragraph.
        prose_labels = "|".join(
            re.escape(label)
            for label in ("住所地/通信地址", "通讯地址", "联系地址", "注册地址", "办公地址", "通信地址", "住所地", "住所", "地址")
        )
        self.prose_pattern = re.compile(
            rf"(?:{prose_labels})\s*为\s*(?P<prose>[^\n|；;，,。]+?)"
            rf"(?=\s*(?:已通过|法定代表人|负责人|登记机关|经营范围|联系人|联系电话|电话|邮箱|电子邮箱)|[；;，,。]|$)"
        )
        self.reverse_table_pattern = re.compile(
            rf"(?:^|\|)\s*(?P<reverse>[^\n|]{{5,120}}?)\s*\|\s*(?:{labels})(?=\s*\||\s*$)",
            re.MULTILINE,
        )
        self.table_value_pattern = re.compile(
            r"\|\s*(?P<table>(?=[^\n|]{0,120}(?:省|市|区|县)[^\n|]{0,40}(?:路|街|道|镇|乡|村|号|院|楼|层|室))"
            r"[^\n|]{5,120}(?:号|院|楼|层|室|大厦|大楼|园区|基地|栋|座)[^\n|]{0,20})\s*(?=\||$)",
            re.MULTILINE,
        )
        self.city_shape_pattern = re.compile(
            r"(?P<city>(?:(?:[\u3400-\u9fff]{2,5}省)(?!级)(?=[\u3400-\u9fff]{1,8}(?:市|区|县|自治州))|"
            r"(?:[\u3400-\u9fff]{2,6}市)(?!公司)(?=[\u3400-\u9fff]{1,10}(?:区|县|市|镇|乡|路|街|道|村)))"
            r"[^\n|]{3,100}"
            r"(?:号|院|楼|层|室|大厦|大楼|园区|基地|栋|座|村))"
        )
        self.company_tail_pattern = re.compile(
            r"(?:公司名称|企业名称)\s*[:：]\s*[^\n|]*(?:股份有限公司|有限责任公司|有限公司|集团)"
            r"(?P<tail>[\u3400-\u9fffA-Za-z0-9·()（）\-]{2,40}(?:大厦|大楼|园区|基地|楼|层|室|栋|座)[^\n|]{0,20})(?=\s*\||\s*$)"
        )
        self.forward_table_pattern = re.compile(
            rf"(?m)^(?P<prefix>[^\n]*?\|\s*(?:{labels})\s*\|)(?P<rest>[^\n]*)$"
        )
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        result: list[Span] = []
        for match in self.pattern.finditer(text):
            group = "bracket" if match.group("bracket") is not None else "square" if match.group("square") is not None else "plain"
            start, end = match.span(group)
            value = text[start:end].strip()
            if not value:
                continue
            leading = len(text[start:end]) - len(text[start:end].lstrip())
            trailing = len(text[start:end]) - len(text[start:end].rstrip())
            start += leading
            end -= trailing
            result.append(
                Span(
                    start,
                    end,
                    "ADDRESS",
                    text[start:end],
                    score=1.0,
                    priority=self.priority,
                    source="address_field",
                    rule_id="address_label",
                    anonymize=self.anonymize,
                    protect=self.protect,
                )
            )
        existing = {(span.start, span.end) for span in result}
        for match in self.prose_pattern.finditer(text):
            start, end = match.span("prose")
            value = text[start:end].strip()
            if not value:
                continue
            leading = len(text[start:end]) - len(text[start:end].lstrip())
            trailing = len(text[start:end]) - len(text[start:end].rstrip())
            start += leading
            end -= trailing
            if (start, end) in existing:
                continue
            existing.add((start, end))
            result.append(
                Span(
                    start,
                    end,
                    "ADDRESS",
                    text[start:end],
                    score=0.96,
                    priority=self.priority,
                    source="address_prose",
                    rule_id="address_as_clause",
                    anonymize=self.anonymize,
                    protect=self.protect,
                )
            )
        # Some OCR tables put the address label in the first cell and then
        # place several party addresses in later cells.  Split each cell
        # around a high-confidence identifier so the identifier recognizer
        # can win its overlap while the remaining address text is covered.
        for match in self.forward_table_pattern.finditer(text):
            rest_start = match.start("rest")
            rest = match.group("rest")
            cursor = 0
            for cell in rest.split("|"):
                raw_start = rest_start + cursor
                raw_end = raw_start + len(cell)
                cursor += len(cell) + 1
                start, end = raw_start, raw_end
                protected = list(self._protected_identifier.finditer(cell))
                boundaries = [0, *(item.start() for item in protected), *(item.end() for item in protected), len(cell)]
                for left, right in zip(boundaries[::2], boundaries[1::2]):
                    value = cell[left:right].strip()
                    if len(value) < 2:
                        continue
                    segment_start = raw_start + left + (len(cell[left:right]) - len(cell[left:right].lstrip()))
                    segment_end = raw_start + right - (len(cell[left:right]) - len(cell[left:right].rstrip()))
                    if segment_end <= segment_start or (segment_start, segment_end) in existing:
                        continue
                    existing.add((segment_start, segment_end))
                    result.append(
                        Span(
                            segment_start,
                            segment_end,
                            "ADDRESS",
                            text[segment_start:segment_end],
                            score=0.91,
                            priority=self.priority,
                            source="address_forward_table",
                            rule_id="address_forward_table",
                            anonymize=self.anonymize,
                            protect=self.protect,
                        )
                    )
        for pattern, group, source, rule_id, score in (
            (self.reverse_table_pattern, "reverse", "address_reverse_table", "address_reverse_table", 0.94),
            (self.table_value_pattern, "table", "address_table_value", "address_table_value", 0.92),
            (self.city_shape_pattern, "city", "address_city_shape", "address_city_shape", 0.90),
            (self.company_tail_pattern, "tail", "address_company_tail", "address_company_tail", 0.93),
        ):
            for match in pattern.finditer(text):
                start, end = match.span(group)
                value = text[start:end].strip()
                if not value or (start, end) in existing:
                    continue
                # A table cell must look like a location, otherwise ordinary
                # narrative cells containing words such as "项目" could be
                # mistaken for an address.
                if pattern is self.table_value_pattern and not re.search(r"(?:市|区|县|路|街|道|号|院|楼|层|室)", value):
                    continue
                leading = len(text[start:end]) - len(text[start:end].lstrip())
                trailing = len(text[start:end]) - len(text[start:end].rstrip())
                start += leading
                end -= trailing
                if (start, end) in existing:
                    continue
                existing.add((start, end))
                result.append(
                    Span(
                        start,
                        end,
                        "ADDRESS",
                        text[start:end],
                        score=score,
                        priority=self.priority,
                        source=source,
                        rule_id=rule_id,
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )
        return result
