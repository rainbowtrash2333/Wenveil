from __future__ import annotations

import re

from ..models import Span


class PhoneRecognizer:
    """Recognize mobile and landline numbers without touching source text."""

    _mobile = re.compile(
        r"(?<![A-Za-z0-9])1[3-9](?:[ \t\-－—]?\d){9}(?![ \t\-－—]?\d)"
    )
    _landline = re.compile(
        r"(?<![A-Za-z0-9])0\d{2,3}(?:[ \t\-－—]?\d){7,8}(?![ \t\-－—]?\d)"
    )
    _context = re.compile(
        r"(?:电话|联系电话|传真|手机|移动电话|联系电话号码)\s*[:：]?\s*[【\[]?"
        r"(?P<value>(?:1[3-9](?:[ \t\-－—]?\d){9}|"
        r"0\d{2,3}(?:[ \t\-－—]?\d){7,8}|"
        r"0(?:[ \t\-－—]?\d){9,10}))"
        r"[】\]]?"
    )

    def __init__(self, *, priority: int = 97, anonymize: bool = True, protect: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        matches: dict[tuple[int, int], str] = {}
        for pattern, rule_id in ((self._mobile, "mobile"), (self._landline, "landline"), (self._context, "phone_context")):
            for match in pattern.finditer(text):
                if pattern is self._context:
                    start, end = match.span("value")
                else:
                    start, end = match.span(0)
                matches[(start, end)] = rule_id
        return [
            Span(
                start,
                end,
                "PHONE",
                text[start:end],
                score=1.0 if rule_id != "phone_context" else 1.02,
                priority=self.priority,
                source="phone_rule",
                rule_id=rule_id,
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for (start, end), rule_id in sorted(matches.items())
        ]
