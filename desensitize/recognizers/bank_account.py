from __future__ import annotations

import re

from ..models import Span


class BankAccountRecognizer:
    """Recognize labelled and long numeric account identifiers.

    The labelled rule handles OCR tables and bracketed values.  The bounded
    fallback catches unlabelled account numbers while deliberately excluding
    ordinary dates and 11-digit phone numbers (handled by PHONE with higher
    priority).
    """

    _context = re.compile(
        r"(?:银行账号|银行账户|账号|帐号|账户(?:号|号码)?|卡号|持有人账号|持有人账户(?:号|号码)?)"
        r"\s*[:：]?\s*[【\[]?(?P<value>(?:\d[ \t\-－—]?){9,18}\d)[】\]]?"
    )
    _generic = re.compile(r"(?<![A-Za-z0-9])\d{12,19}(?![A-Za-z0-9])")
    _grouped = re.compile(
        r"(?<![A-Za-z0-9])(?:\d[ \t\-－—]?){15,18}\d(?![ \t\-－—]?\d)"
    )
    _credit_code = re.compile(
        r"(?<![0-9A-Z])[0-9A-Z]{18}(?![0-9A-Z])",
        re.IGNORECASE,
    )

    def __init__(self, *, priority: int = 96, anonymize: bool = True, protect: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        matches: dict[tuple[int, int], str] = {}
        for match in self._credit_code.finditer(text):
            value = match.group(0)
            if any(char.isalpha() for char in value) and any(char.isdigit() for char in value):
                matches[match.span(0)] = "unified_social_credit_code"
        for match in self._context.finditer(text):
            matches.setdefault(match.span("value"), "account_context")
        for match in self._generic.finditer(text):
            matches.setdefault(match.span(0), "long_numeric_identifier")
        for match in self._grouped.finditer(text):
            matches.setdefault(match.span(0), "grouped_numeric_identifier")
        result: list[Span] = []
        for (start, end), rule_id in sorted(matches.items()):
            is_credit_code = rule_id == "unified_social_credit_code"
            result.append(
                Span(
                    start,
                    end,
                    "BANK_ACCOUNT",
                    text[start:end],
                    score=1.03 if is_credit_code else 1.02 if rule_id == "account_context" else 0.92,
                    priority=self.priority + 2 if is_credit_code else self.priority if rule_id == "account_context" else self.priority - 2,
                    source="bank_account_rule",
                    rule_id=rule_id,
                    anonymize=self.anonymize,
                    protect=self.protect,
                )
            )
        return result
