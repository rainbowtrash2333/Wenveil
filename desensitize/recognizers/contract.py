from __future__ import annotations

import re

from ..models import Span


class ContractIdRecognizer:
    _pattern = re.compile(r"(?<![A-Za-z0-9])(?:HT|HT号|合同号|协议号)[-－]?(?:19|20)?\d{2,4}[-－]\d+(?![A-Za-z0-9])", re.IGNORECASE)

    def __init__(self, *, priority: int = 95, anonymize: bool = True, protect: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        return [
            Span(
                match.start(),
                match.end(),
                "CONTRACT_ID",
                match.group(0),
                score=1.0,
                priority=self.priority,
                source="contract_id_rule",
                rule_id="contract_id",
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for match in self._pattern.finditer(text)
        ]
