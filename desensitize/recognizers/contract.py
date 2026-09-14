from __future__ import annotations

import re

from ..models import Span


class ContractIdRecognizer:
    _pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:HT|HT号|合同号|协议号)[ \t]*[-－—:/]?[ \t]*"
        r"(?:19|20)?\d{2,4}[ \t]*[-－—/]\s*\d+(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    _label_pattern = re.compile(
        r"(?:合同编号|合同号|协议编号|协议号)\s*[:：#]?\s*"
        r"(?P<value>(?!⟦)[A-Za-z0-9][A-Za-z0-9._/－—\-]{2,})"
    )

    def __init__(self, *, priority: int = 95, anonymize: bool = True, protect: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        matches: dict[tuple[int, int], str] = {
            match.span(0): "contract_id"
            for match in self._pattern.finditer(text)
        }
        for match in self._label_pattern.finditer(text):
            matches.setdefault(match.span("value"), "contract_id_label")
        return [
            Span(
                start,
                end,
                "CONTRACT_ID",
                text[start:end],
                score=1.0,
                priority=self.priority,
                source="contract_id_rule",
                rule_id=rule_id,
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for (start, end), rule_id in sorted(matches.items())
        ]
