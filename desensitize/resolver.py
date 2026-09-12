from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable

from .models import Span


def resolve_spans(spans: Iterable[Span], text: str) -> list[Span]:
    """Choose a non-overlapping set of spans deterministically.

    Priority wins first, then confidence, then the longer interval.  A
    protected span remains in the accepted set and therefore blocks a lower
    priority recognizer such as NUMBER from masking its interior.
    """

    candidates = list(spans)
    for span in candidates:
        span.validate_against(text)
    if not candidates:
        return []

    # Disjoint connected components cannot conflict.  Partitioning first
    # avoids comparing every NUMBER candidate with every accepted span in a
    # long document.
    by_start = sorted(candidates, key=lambda span: (span.start, span.end))
    components: list[list[Span]] = []
    component: list[Span] = [by_start[0]]
    component_end = by_start[0].end
    for candidate in by_start[1:]:
        if candidate.start >= component_end:
            components.append(component)
            component = [candidate]
            component_end = candidate.end
        else:
            component.append(candidate)
            component_end = max(component_end, candidate.end)
    components.append(component)

    accepted_all: list[Span] = []
    ranking = lambda span: (-span.priority, -span.score, -span.length, span.start, span.end)
    for component in components:
        ordered = sorted(component, key=ranking)
        accepted: list[Span] = []
        starts: list[int] = []
        for candidate in ordered:
            position = bisect_left(starts, candidate.start)
            left_overlaps = position > 0 and accepted[position - 1].end > candidate.start
            right_overlaps = position < len(accepted) and candidate.end > accepted[position].start
            if left_overlaps or right_overlaps:
                continue
            starts.insert(position, candidate.start)
            accepted.insert(position, candidate)
        accepted_all.extend(accepted)
    accepted_all.sort(key=lambda span: (span.start, span.end))
    return accepted_all
