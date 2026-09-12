from __future__ import annotations

import re

from ..models import Span


class EmailRecognizer:
    _pattern = re.compile(
        r"(?<![A-Za-z0-9._%+\-])"
        r"[A-Za-z0-9._%+\-]+[ \t]*@[ \t]*[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}"
        r"(?![A-Za-z0-9._%+\-])"
    )

    def __init__(self, *, priority: int = 96, anonymize: bool = True, protect: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        return [
            Span(
                match.start(),
                match.end(),
                "EMAIL",
                match.group(0),
                score=1.0,
                priority=self.priority,
                source="email_rule",
                rule_id="email",
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for match in self._pattern.finditer(text)
        ]
