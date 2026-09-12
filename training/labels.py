"""BIO/BILOU labels, span conversion, and lossless teacher-output checks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import re
from typing import Literal

from .types import EntityAnnotation

LabelScheme = Literal["BIO", "BILOU"]


class LabelValidationError(ValueError):
    """Raised when labels do not describe a legal or lossless annotation."""


@dataclass(frozen=True, slots=True)
class LabelValidationResult:
    scheme: str
    num_units: int
    text: str
    spans: tuple[EntityAnnotation, ...]

    @property
    def valid(self) -> bool:
        return True

    def __bool__(self) -> bool:
        return self.valid


@dataclass(frozen=True, slots=True)
class TaggedTextResult:
    text: str
    spans: tuple[EntityAnnotation, ...]


_LABEL_RE = re.compile(r"^(?P<prefix>O|B|I|L|U)(?:-(?P<entity>[A-Za-z][A-Za-z0-9_.:-]*))?$")
_TAG_RE = re.compile(r"</?(?P<entity>[A-Za-z][A-Za-z0-9_.:-]*)\s*>")


def normalize_scheme(scheme: str) -> str:
    normalized = str(scheme).upper().replace("-", "")
    if normalized in {"BIO", "BILOU", "BIOUL"}:
        return "BILOU" if normalized in {"BILOU", "BIOUL"} else "BIO"
    raise ValueError("scheme must be BIO or BILOU")


def parse_label(label: str) -> tuple[str, str | None]:
    if not isinstance(label, str):
        raise LabelValidationError(f"label must be a string, got {type(label).__name__}")
    match = _LABEL_RE.fullmatch(label)
    if not match:
        raise LabelValidationError(f"invalid label: {label!r}")
    prefix = match.group("prefix")
    entity = match.group("entity")
    if prefix == "O":
        if entity is not None:
            raise LabelValidationError("O cannot carry an entity type")
        return prefix, None
    if entity is None:
        raise LabelValidationError(f"{prefix} must carry an entity type")
    return prefix, entity


def _unit_text_and_offsets(
    units: Sequence[str] | None,
    text: str | None,
    offsets: Sequence[tuple[int, int]] | None,
    label_count: int,
) -> tuple[list[str], str, list[tuple[int, int]]]:
    if offsets is not None:
        offsets_list = [(int(start), int(end)) for start, end in offsets]
        if len(offsets_list) != label_count:
            raise LabelValidationError("offset count must match label count")
        previous_end = 0
        max_end = 0
        for start, end in offsets_list:
            if start < 0 or end < start:
                raise LabelValidationError("offsets must be ordered and non-overlapping")
            if end == start:
                continue
            if start < previous_end:
                raise LabelValidationError("offsets must be ordered and non-overlapping")
            previous_end = end
            max_end = max(max_end, end)
        if text is not None and max_end > len(text):
            raise LabelValidationError("offsets exceed text length")
        if units is None:
            units_list = [
                text[start:end] if text is not None else ""
                for start, end in offsets_list
            ]
        else:
            units_list = [str(unit) for unit in units]
            if len(units_list) != label_count:
                raise LabelValidationError("unit count must match label count")
        # ``text`` is authoritative when tokenizer offsets are supplied.  A
        # fast tokenizer can omit special-token text and may represent a
        # subword with a surface different from its raw token string.
        text_value = text if text is not None else "".join(units_list)
        return units_list, text_value, offsets_list

    if units is None:
        units_list = list(text) if text is not None else [""] * label_count
    else:
        units_list = [str(unit) for unit in units]
    if len(units_list) != label_count:
        raise LabelValidationError(
            f"label count {label_count} does not match unit count {len(units_list)}"
        )
    text_value = "".join(units_list)
    if text is not None and text_value != text:
        raise LabelValidationError("joining units after removing labels does not equal text")
    cursor = 0
    offsets_list = []
    for unit in units_list:
        end = cursor + len(unit)
        offsets_list.append((cursor, end))
        cursor = end
    return units_list, text_value, offsets_list


def _spans_from_parsed_labels(
    parsed: Sequence[tuple[str, str | None]],
    offsets: Sequence[tuple[int, int]],
    text: str,
    scheme: str,
) -> tuple[EntityAnnotation, ...]:
    spans: list[EntityAnnotation] = []
    open_start: int | None = None
    open_type: str | None = None

    def close(end_index: int) -> None:
        nonlocal open_start, open_type
        if open_start is None or open_type is None:
            raise LabelValidationError("internal label state has no open entity")
        end = offsets[end_index - 1][1]
        start = offsets[open_start][0]
        spans.append(EntityAnnotation(start, end, open_type, text[start:end]))
        open_start = None
        open_type = None

    for index, (prefix, entity_type) in enumerate(parsed):
        if prefix == "O":
            if open_start is not None and scheme == "BIO":
                close(index)
            elif scheme == "BILOU" and open_start is not None:
                raise LabelValidationError(f"open entity is not closed before unit {index}")
            continue

        if scheme == "BIO":
            if prefix == "B":
                if open_start is not None:
                    close(index)
                open_start, open_type = index, entity_type
            elif prefix == "I":
                if open_start is None or entity_type != open_type:
                    raise LabelValidationError(f"I label at unit {index} has no matching B label")
            else:
                raise LabelValidationError(f"{prefix} is not valid in BIO")
            if index == len(parsed) - 1 and open_start is not None:
                close(len(parsed))
            continue

        # BILOU is strict: every B/I run must terminate with L; U is one unit.
        if prefix == "U":
            if open_start is not None:
                raise LabelValidationError(f"U label at unit {index} interrupts an entity")
            start, end = offsets[index]
            spans.append(EntityAnnotation(start, end, entity_type or "", text[start:end]))
        elif prefix == "B":
            if open_start is not None:
                raise LabelValidationError(f"B label at unit {index} interrupts an entity")
            open_start, open_type = index, entity_type
        elif prefix == "I":
            if open_start is None or entity_type != open_type:
                raise LabelValidationError(f"I label at unit {index} has no matching B label")
        elif prefix == "L":
            if open_start is None or entity_type != open_type:
                raise LabelValidationError(f"L label at unit {index} has no matching B label")
            close(index + 1)
        else:
            raise LabelValidationError(f"unsupported label prefix {prefix!r}")

    if open_start is not None:
        raise LabelValidationError("entity started with B but has no terminating L")
    return tuple(sorted(spans, key=lambda span: (span.start, span.end)))


def validate_label_sequence(
    labels: Sequence[str],
    *,
    scheme: str = "BIO",
    units: Sequence[str] | None = None,
    text: str | None = None,
    offsets: Sequence[tuple[int, int]] | None = None,
) -> LabelValidationResult:
    """Validate transitions and lossless reconstruction of a label sequence.

    With no explicit ``offsets``, labels are aligned to characters (or the
    supplied ``units``).  Tokenizers can pass their offset mapping so the
    exact original text remains independently verifiable.
    """

    normalized_scheme = normalize_scheme(scheme)
    units_list, text_value, offsets_list = _unit_text_and_offsets(
        units, text, offsets, len(labels)
    )
    parsed = [parse_label(label) for label in labels]
    for index, ((start, end), (prefix, _)) in enumerate(zip(offsets_list, parsed)):
        if start == end and prefix != "O":
            raise LabelValidationError(f"entity label at zero-length unit {index}")
    spans = _spans_from_parsed_labels(parsed, offsets_list, text_value, normalized_scheme)
    # This is deliberately explicit: labels may never be used to reconstruct
    # a different string than the source units.
    if "".join(units_list) != text_value:
        raise LabelValidationError("removing labels does not reproduce the original text")
    return LabelValidationResult(normalized_scheme, len(labels), text_value, spans)


def validate_labels(
    text: str,
    labels: Sequence[str],
    scheme: str = "BIO",
    *,
    offsets: Sequence[tuple[int, int]] | None = None,
) -> LabelValidationResult:
    """Convenience validator for raw text plus character/token labels."""

    return validate_label_sequence(labels, scheme=scheme, text=text, offsets=offsets)


def validate_bio_labels(
    labels: Sequence[str],
    *,
    text: str | None = None,
    units: Sequence[str] | None = None,
    offsets: Sequence[tuple[int, int]] | None = None,
) -> LabelValidationResult:
    return validate_label_sequence(labels, scheme="BIO", text=text, units=units, offsets=offsets)


def validate_bilou_labels(
    labels: Sequence[str],
    *,
    text: str | None = None,
    units: Sequence[str] | None = None,
    offsets: Sequence[tuple[int, int]] | None = None,
) -> LabelValidationResult:
    return validate_label_sequence(labels, scheme="BILOU", text=text, units=units, offsets=offsets)


def spans_to_labels(
    text: str,
    spans: Iterable[EntityAnnotation | object],
    *,
    scheme: str = "BIO",
) -> list[str]:
    """Create character-aligned BIO/BILOU labels from non-overlapping spans."""

    normalized_scheme = normalize_scheme(scheme)
    annotations = [EntityAnnotation.from_value(span, text) for span in spans]
    annotations.sort(key=lambda span: (span.start, span.end, -span.priority, -span.score))
    labels = ["O"] * len(text)
    previous_end = -1
    for annotation in annotations:
        annotation.validate_against(text)
        if annotation.start < previous_end:
            raise LabelValidationError(
                f"overlapping spans cannot be represented in flat labels: "
                f"{annotation.start}:{annotation.end}"
            )
        previous_end = annotation.end
        length = annotation.length
        if normalized_scheme == "BIO":
            labels[annotation.start] = f"B-{annotation.entity_type}"
            for index in range(annotation.start + 1, annotation.end):
                labels[index] = f"I-{annotation.entity_type}"
        elif length == 1:
            labels[annotation.start] = f"U-{annotation.entity_type}"
        else:
            labels[annotation.start] = f"B-{annotation.entity_type}"
            for index in range(annotation.start + 1, annotation.end - 1):
                labels[index] = f"I-{annotation.entity_type}"
            labels[annotation.end - 1] = f"L-{annotation.entity_type}"

    result = validate_label_sequence(labels, scheme=normalized_scheme, text=text)
    expected_keys = tuple(
        (span.start, span.end, span.entity_type, text[span.start : span.end])
        for span in sorted(annotations, key=lambda item: (item.start, item.end))
    )
    actual_keys = tuple(
        (span.start, span.end, span.entity_type, span.surface) for span in result.spans
    )
    if actual_keys != expected_keys:
        raise LabelValidationError("generated labels do not round-trip to the source spans")
    return labels


def labels_to_spans(
    text: str,
    labels: Sequence[str],
    *,
    scheme: str = "BIO",
) -> tuple[EntityAnnotation, ...]:
    return validate_label_sequence(labels, scheme=scheme, text=text).spans


def reconstruct_text(units: Sequence[str], labels: Sequence[str], *, scheme: str = "BIO") -> str:
    """Validate labels and return the text left after removing labels."""

    result = validate_label_sequence(labels, scheme=scheme, units=units)
    return result.text


def parse_tagged_text(tagged_text: str, *, original_text: str | None = None) -> TaggedTextResult:
    """Parse ``<PERSON>张三</PERSON>`` teacher output without changing text.

    Tags must be properly nested.  Nested entity spans are rejected later
    because the token-classification target is a flat span sequence.
    """

    plain_parts: list[str] = []
    stack: list[tuple[str, int]] = []
    spans: list[EntityAnnotation] = []
    plain_length = 0
    cursor = 0
    for match in _TAG_RE.finditer(tagged_text):
        literal = tagged_text[cursor : match.start()]
        plain_parts.append(literal)
        plain_length += len(literal)
        raw_tag = match.group(0)
        entity_type = match.group("entity")
        if raw_tag.startswith("</"):
            if not stack:
                raise LabelValidationError(f"closing tag without opener: {raw_tag}")
            open_type, start = stack.pop()
            if open_type != entity_type:
                raise LabelValidationError(
                    f"mismatched closing tag {entity_type!r}; expected {open_type!r}"
                )
            spans.append(
                EntityAnnotation(start, plain_length, entity_type, "".join(plain_parts)[start:plain_length])
            )
        else:
            stack.append((entity_type, plain_length))
        cursor = match.end()
    tail = tagged_text[cursor:]
    plain_parts.append(tail)
    plain_text = "".join(plain_parts)
    if stack:
        raise LabelValidationError(f"unclosed tag: {stack[-1][0]}")
    if original_text is not None and plain_text != original_text:
        raise LabelValidationError("removing teacher tags does not reproduce the original text")
    ordered = tuple(sorted(spans, key=lambda span: (span.start, span.end)))
    previous_end = -1
    for span in ordered:
        if span.start < previous_end:
            raise LabelValidationError("nested entity tags are not representable as flat spans")
        previous_end = span.end
    return TaggedTextResult(plain_text, ordered)


def validate_tagged_text(tagged_text: str, original_text: str | None = None) -> TaggedTextResult:
    return parse_tagged_text(tagged_text, original_text=original_text)


def strip_tags(tagged_text: str, original_text: str | None = None) -> str:
    return parse_tagged_text(tagged_text, original_text=original_text).text
