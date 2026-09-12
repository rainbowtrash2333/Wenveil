from __future__ import annotations

import re

from ..models import Span


class DateRecognizer:
    """Detect dates so NUMBER cannot consume their components."""

    _patterns = (
        (re.compile(r"(?<!\d)(?:19|20)\d{2}年(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])日?"), "date_cn"),
        (re.compile(r"(?<!\d)(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])(?!\d)"), "date_iso"),
        (re.compile(r"(?<!\d)(?:19|20)\d{6}(?!\d)"), "date_compact"),
    )

    def __init__(self, *, priority: int = 88, anonymize: bool = False, protect: bool = True):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        result: list[Span] = []
        for pattern, rule_id in self._patterns:
            for match in pattern.finditer(text):
                result.append(
                    Span(
                        match.start(),
                        match.end(),
                        "DATE",
                        match.group(0),
                        score=1.0,
                        priority=self.priority,
                        source="date_rule",
                        rule_id=rule_id,
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )
        return result
