from __future__ import annotations

import re

from ..config import CustomRule
from ..models import Span
from .dictionary import DictionaryRecognizer


class RegexRecognizer:
    def __init__(self, rule: CustomRule):
        self.rule = rule
        self.patterns = [re.compile(pattern) for pattern in rule.patterns]

    def recognize(self, text: str) -> list[Span]:
        result: list[Span] = []
        for pattern_index, pattern in enumerate(self.patterns):
            for match in pattern.finditer(text):
                result.append(
                    Span(
                        match.start(),
                        match.end(),
                        self.rule.entity_type,
                        match.group(0),
                        score=1.0,
                        priority=self.rule.priority,
                        source="custom_regex",
                        rule_id=f"{self.rule.name}:{pattern_index}",
                        anonymize=self.rule.anonymize,
                        protect=(not self.rule.anonymize and self.rule.protect_when_disabled),
                    )
                )
        return result


class FieldRecognizer:
    def __init__(self, rule: CustomRule):
        self.rule = rule
        labels = "|".join(re.escape(label) for label in rule.labels if label)
        self.pattern = re.compile(rf"(?:{labels})\s*[:：]\s*(?P<value>[^\n|；;]+)") if labels else None

    def recognize(self, text: str) -> list[Span]:
        if self.pattern is None:
            return []
        result: list[Span] = []
        for match in self.pattern.finditer(text):
            value = match.group("value").strip()
            if not value:
                continue
            raw_start, raw_end = match.span("value")
            leading = len(match.group("value")) - len(match.group("value").lstrip())
            trailing = len(match.group("value")) - len(match.group("value").rstrip())
            start = raw_start + leading
            end = raw_end - trailing
            result.append(
                Span(
                    start,
                    end,
                    self.rule.entity_type,
                    text[start:end],
                    score=1.0,
                    priority=self.rule.priority,
                    source="custom_field",
                    rule_id=self.rule.name,
                    anonymize=self.rule.anonymize,
                    protect=(not self.rule.anonymize and self.rule.protect_when_disabled),
                )
            )
        return result


def build_custom_recognizers(rules: tuple[CustomRule, ...]):
    recognizers = []
    for rule in rules:
        if not rule.detect:
            continue
        if rule.matcher in {"literal", "dictionary"}:
            values = rule.values
            recognizers.append(
                DictionaryRecognizer(
                    rule.entity_type,
                    values,
                    priority=rule.priority,
                    source=f"custom_{rule.matcher}",
                    rule_id=rule.name,
                    anonymize=rule.anonymize,
                    protect=(not rule.anonymize and rule.protect_when_disabled),
                )
            )
        elif rule.matcher == "regex":
            recognizers.append(RegexRecognizer(rule))
        elif rule.matcher == "field":
            recognizers.append(FieldRecognizer(rule))
    return recognizers
