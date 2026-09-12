from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..models import Span


@dataclass(slots=True)
class _Node:
    children: dict[str, int] = field(default_factory=dict)
    fail: int = 0
    outputs: list[tuple[str, int]] = field(default_factory=list)


class KeywordAutomaton:
    """Small pure-Python Aho–Corasick implementation.

    It is used as a dependency-free fallback and keeps the same single-pass
    matching shape as the optional ``pyahocorasick`` extension.
    """

    def __init__(self, keywords: list[str] | tuple[str, ...]):
        self.nodes = [_Node()]
        for keyword in dict.fromkeys(keywords):
            if keyword:
                self._add(keyword)
        self._build_failures()

    def _add(self, keyword: str) -> None:
        state = 0
        for char in keyword:
            next_state = self.nodes[state].children.get(char)
            if next_state is None:
                next_state = len(self.nodes)
                self.nodes[state].children[char] = next_state
                self.nodes.append(_Node())
            state = next_state
        self.nodes[state].outputs.append((keyword, len(keyword)))

    def _build_failures(self) -> None:
        queue: deque[int] = deque()
        for child in self.nodes[0].children.values():
            queue.append(child)
        while queue:
            state = queue.popleft()
            for char, child in self.nodes[state].children.items():
                queue.append(child)
                fallback = self.nodes[state].fail
                while fallback and char not in self.nodes[fallback].children:
                    fallback = self.nodes[fallback].fail
                self.nodes[child].fail = self.nodes[fallback].children.get(char, 0)
                inherited = self.nodes[self.nodes[child].fail].outputs
                if inherited:
                    self.nodes[child].outputs.extend(inherited)

    def finditer(self, text: str):
        state = 0
        for index, char in enumerate(text):
            while state and char not in self.nodes[state].children:
                state = self.nodes[state].fail
            state = self.nodes[state].children.get(char, 0)
            for keyword, length in self.nodes[state].outputs:
                end = index + 1
                yield end - length, end, keyword


class DictionaryRecognizer:
    def __init__(
        self,
        entity_type: str,
        values: list[str] | tuple[str, ...],
        *,
        priority: int,
        source: str = "dictionary",
        rule_id: str = "dictionary",
        anonymize: bool = True,
        protect: bool = False,
    ):
        self.entity_type = entity_type.upper()
        self.values = tuple(dict.fromkeys(value for value in values if value))
        self.priority = priority
        self.source = source
        self.rule_id = rule_id
        self.anonymize = anonymize
        self.protect = protect
        self.automaton = KeywordAutomaton(self.values) if self.values else None

    def recognize(self, text: str) -> list[Span]:
        if self.automaton is None:
            return []
        return [
            Span(
                start,
                end,
                self.entity_type,
                text[start:end],
                score=1.0,
                priority=self.priority,
                source=self.source,
                rule_id=self.rule_id,
                anonymize=self.anonymize,
                protect=self.protect,
            )
            for start, end, _ in self.automaton.finditer(text)
        ]
