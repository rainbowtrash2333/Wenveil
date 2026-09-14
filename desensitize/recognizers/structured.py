from __future__ import annotations

import datetime as _datetime
import re

from ..models import Span


class IdCardRecognizer:
    _pattern = re.compile(
        r"(?<!\d)(?:[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?!\d)"
    )
    _context = re.compile(
        r"(?:身份证(?:号码|号)?|证件(?:号码|号)?)\s*[:：]?\s*[【\[]?"
        r"(?P<value>(?:[0-9Xx][ \t\-－—]?){14,17}[0-9Xx])"
        r"[】\]]?"
    )
    _weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    _checks = "10X98765432"

    def __init__(self, *, priority: int = 100, anonymize: bool = True, protect: bool = False, allow_15: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect
        self.allow_15 = allow_15

    def recognize(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for match in self._pattern.finditer(text):
            value = match.group(0)
            if self._valid_18(value):
                spans.append(
                    Span(
                        match.start(),
                        match.end(),
                        "ID_CARD",
                        value,
                        score=1.0,
                        priority=self.priority,
                        source="id_card_rule",
                        rule_id="cn_id_18",
                        anonymize=self.anonymize,
                        protect=self.protect,
                    )
                )
        existing = {(span.start, span.end) for span in spans}
        for match in self._context.finditer(text):
            start, end = match.span("value")
            if (start, end) in existing:
                continue
            spans.append(
                Span(
                    start,
                    end,
                    "ID_CARD",
                    text[start:end],
                    score=0.99,
                    priority=self.priority,
                    source="id_card_rule",
                    rule_id="id_card_context",
                    anonymize=self.anonymize,
                    protect=self.protect,
                )
            )
        if self.allow_15:
            pattern = re.compile(r"(?<!\d)[1-9]\d{7}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)")
            for match in pattern.finditer(text):
                if self._valid_15(match.group(0)):
                    spans.append(
                        Span(
                            match.start(),
                            match.end(),
                            "ID_CARD",
                            match.group(0),
                            score=0.98,
                            priority=self.priority,
                            source="id_card_rule",
                            rule_id="cn_id_15",
                            anonymize=self.anonymize,
                            protect=self.protect,
                        )
                    )
        return spans

    @classmethod
    def _valid_18(cls, value: str) -> bool:
        try:
            _datetime.date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
        except ValueError:
            return False
        expected = cls._checks[sum(int(digit) * weight for digit, weight in zip(value[:17], cls._weights)) % 11]
        return value[-1].upper() == expected

    @classmethod
    def _valid_15(cls, value: str) -> bool:
        try:
            _datetime.date(1900 + int(value[6:8]), int(value[8:10]), int(value[10:12]))
        except ValueError:
            return False
        expanded = value[:6] + "19" + value[6:]
        return cls._valid_18(expanded + cls._checks[sum(int(digit) * weight for digit, weight in zip(expanded, cls._weights)) % 11])


class AmountRecognizer:
    _pattern = re.compile(
        r"(?<!\d)(?:[¥￥$€]\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:万亿元|亿元|万元|人民币|美元|港币|元|万|亿)(?!\d)"
    )

    def __init__(self, *, priority: int = 80, anonymize: bool = False, protect: bool = True):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        return [
            Span(
                match.start(),
                match.end(),
                "AMOUNT",
                match.group(0),
                score=1.0,
                priority=self.priority,
                source="amount_rule",
                rule_id="amount_with_unit",
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for match in self._pattern.finditer(text)
        ]


class NumberRecognizer:
    _pattern = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?!\d)")

    def __init__(self, *, priority: int = 10, anonymize: bool = False, protect: bool = False):
        self.priority = priority
        self.anonymize = anonymize
        self.protect = protect

    def recognize(self, text: str) -> list[Span]:
        return [
            Span(
                match.start(),
                match.end(),
                "NUMBER",
                match.group(0),
                score=0.8,
                priority=self.priority,
                source="number_rule",
                rule_id="number",
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for match in self._pattern.finditer(text)
        ]
