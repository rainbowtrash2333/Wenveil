from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable


_GENERIC_ALIASES = frozenset(
    {
        "公司",
        "本公司",
        "我公司",
        "该公司",
        "银行",
        "基金",
        "协会",
        "计划",
        "本计划",
        "本股权计划",
        "受托人",
        "管理人",
        "委托人",
        "受益人",
    }
)


@dataclass(frozen=True, slots=True)
class OrganizationRecord:
    entity_id: str
    canonical: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AliasBinding:
    alias: str
    canonical_id: str
    canonical: str
    alias_index: int
    relation: str = "ALIAS_OF"
    source: str = "registry"
    confidence: float = 1.0

    @property
    def alias_label(self) -> str:
        return f"别名{self.alias_index}"

    @property
    def token_type(self) -> str:
        return f"ORG_ALIAS{self.alias_index}"


@dataclass(frozen=True, slots=True)
class SubsidiaryBinding:
    start: int
    end: int
    surface: str
    canonical_id: str
    canonical: str
    parent_alias: str
    location: str
    suffix: str
    alias_index: int
    source: str = "registry_pattern"
    confidence: float = 0.88

    @property
    def alias_label(self) -> str:
        return f"子公司别名{self.alias_index}"

    @property
    def token_type(self) -> str:
        return f"ORG_SUB_ALIAS{self.alias_index}"


class OrganizationRegistry:
    """Document-aware canonical organization and alias registry.

    The registry is deliberately kept outside model weights.  Known entries
    are loaded from configuration, while explicit ``以下简称`` declarations
    are added only to the current document overlay.  This lets a model or a
    rule propose a mention without making the final replacement dependent on
    stale parameters.
    """

    _suffix = (
        r"(?:股份有限公司|有限责任公司|有限公司|集团公司|集团|保险公司|证券公司|银行|"
        r"基金管理公司|合伙企业(?:（有限合伙）|\(有限合伙\))?|研究院|医院|大学)"
    )
    _declaration = re.compile(
        rf"(?P<canonical>[\u3400-\u9fffA-Za-z0-9·（）()\-]{{3,80}}{_suffix})"
        r"\s*[（(]\s*(?:以下简称|简称|下称|以下称|简称为)\s*"
        r"[“\"「『]?(?P<alias>[\u3400-\u9fffA-Za-z0-9·]{2,24})"
    )
    _label_declaration = re.compile(
        rf"(?:全称|主体|公司名称)\s*[:：]\s*"
        rf"(?P<canonical>[\u3400-\u9fffA-Za-z0-9·（）()\-]{{3,80}}{_suffix})"
        r"[^\n；;。]{0,30}?(?:简称|别名)\s*[:：为是]\s*"
        r"[“\"「『]?(?P<alias>[\u3400-\u9fffA-Za-z0-9·]{2,24})"
    )
    _subsidiary_suffix = r"(?:子公司|分公司|公司)"

    def __init__(self, records: Iterable[OrganizationRecord] = ()):
        self._records: dict[str, OrganizationRecord] = {}
        self._by_canonical: dict[str, str] = {}
        self._bindings: dict[str, list[AliasBinding]] = {}
        self._document_bindings: dict[str, list[AliasBinding]] = {}
        self._alias_indices: dict[str, dict[str, int]] = {}
        for record in records:
            self._add_record(record)

    @classmethod
    def from_config(cls, data: Any = None, known_values: Iterable[str] = ()) -> "OrganizationRegistry":
        records: list[OrganizationRecord] = []
        if isinstance(data, dict):
            raw_records = data.get("organizations", data.get("entities", []))
            if isinstance(raw_records, dict):
                raw_records = [dict(value, entity_id=key) for key, value in raw_records.items()]
            if isinstance(raw_records, list):
                for index, raw in enumerate(raw_records, start=1):
                    if not isinstance(raw, dict):
                        continue
                    canonical = str(raw.get("canonical", raw.get("full_name", ""))).strip()
                    if not canonical:
                        continue
                    entity_id = str(raw.get("id", raw.get("entity_id", f"ORG_{index:04d}"))).strip()
                    aliases = tuple(str(item).strip() for item in raw.get("aliases", ()) if str(item).strip())
                    records.append(OrganizationRecord(entity_id, canonical, aliases))

        registry = cls(records)
        for value in known_values:
            canonical = str(value).strip()
            if canonical:
                registry.ensure_canonical(canonical)
        return registry

    @staticmethod
    def _generated_id(canonical: str) -> str:
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12].upper()
        return f"ORG_{digest}"

    @staticmethod
    def _clean_alias(value: str) -> str:
        return value.strip().strip("\"“”「」『』()（）[]【】,，;；:：")

    @staticmethod
    def _variants(alias: str) -> tuple[str, ...]:
        values = [alias]
        for suffix in ("本公司", "公司"):
            if alias.endswith(suffix) and len(alias) > len(suffix) + 1:
                values.append(alias[: -len(suffix)])
        return tuple(dict.fromkeys(values))

    def _add_record(self, record: OrganizationRecord) -> str:
        existing_id = self._by_canonical.get(record.canonical)
        if existing_id:
            entity_id = existing_id
        else:
            entity_id = record.entity_id or self._generated_id(record.canonical)
            if entity_id in self._records and self._records[entity_id].canonical != record.canonical:
                entity_id = self._generated_id(record.canonical)
            self._records[entity_id] = OrganizationRecord(entity_id, record.canonical, ())
            self._by_canonical[record.canonical] = entity_id
            self._alias_indices.setdefault(entity_id, {})
        for alias in record.aliases:
            self.add_alias(entity_id, alias, source="registry")
        return entity_id

    def ensure_canonical(self, canonical: str, *, entity_id: str | None = None) -> str:
        canonical = canonical.strip()
        existing = self._by_canonical.get(canonical)
        if existing:
            return existing
        return self._add_record(
            OrganizationRecord(entity_id or self._generated_id(canonical), canonical, ())
        )

    def add_alias(
        self,
        canonical_id: str,
        alias: str,
        *,
        source: str,
        confidence: float = 1.0,
        document_scope: bool = False,
    ) -> tuple[AliasBinding, ...]:
        alias = self._clean_alias(alias)
        if len(alias) < 2 or alias in _GENERIC_ALIASES or canonical_id not in self._records:
            return ()
        index_by_alias = self._alias_indices.setdefault(canonical_id, {})
        base_index = index_by_alias.get(alias)
        if base_index is None:
            base_index = len(index_by_alias) + 1
            index_by_alias[alias] = base_index
        record = self._records[canonical_id]
        target = self._document_bindings if document_scope else self._bindings
        result: list[AliasBinding] = []
        for variant in self._variants(alias):
            binding = AliasBinding(
                alias=variant,
                canonical_id=canonical_id,
                canonical=record.canonical,
                alias_index=base_index,
                source=source,
                confidence=confidence,
            )
            values = target.setdefault(variant, [])
            if not any(item.canonical_id == canonical_id for item in values):
                values.append(binding)
            result.append(binding)
        return tuple(result)

    def learn_from_text(self, text: str) -> None:
        for pattern, source, confidence in (
            (self._declaration, "document_declaration", 1.0),
            (self._label_declaration, "document_label", 0.98),
        ):
            for match in pattern.finditer(text):
                canonical = match.group("canonical").strip()
                alias = match.group("alias").strip()
                canonical_id = self.ensure_canonical(canonical)
                self.add_alias(
                    canonical_id,
                    alias,
                    source=source,
                    confidence=confidence,
                    document_scope=True,
                )

    def _resolved_bindings(self) -> list[AliasBinding]:
        values: list[AliasBinding] = []
        aliases = set(self._bindings) | set(self._document_bindings)
        for alias in aliases:
            scoped = self._document_bindings.get(alias)
            candidates = scoped if scoped else self._bindings.get(alias, [])
            unique = {item.canonical_id: item for item in candidates}
            if len(unique) == 1:
                values.append(next(iter(unique.values())))
        return sorted(values, key=lambda item: (-len(item.alias), item.alias, item.canonical_id))

    def resolve_alias(self, alias: str) -> AliasBinding | None:
        scoped = self._document_bindings.get(alias)
        candidates = scoped if scoped else self._bindings.get(alias, [])
        unique = {item.canonical_id: item for item in candidates}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def canonical_id_for(self, canonical: str) -> str | None:
        return self._by_canonical.get(canonical)

    def canonical_bindings(self, text: str) -> tuple[tuple[int, int, str, str], ...]:
        """Return document-local full-name spans confirmed by the registry."""

        result: list[tuple[int, int, str, str]] = []
        for record in self._records.values():
            for match in re.finditer(re.escape(record.canonical), text):
                result.append((match.start(), match.end(), record.entity_id, record.canonical))
        return tuple(
            sorted(
                result,
                key=lambda item: (item[0], -(item[1] - item[0]), item[2]),
            )
        )

    def alias_bindings(self) -> tuple[AliasBinding, ...]:
        return tuple(self._resolved_bindings())

    def ambiguous_alias_bindings(self) -> tuple[tuple[str, tuple[AliasBinding, ...]], ...]:
        """Return aliases that have multiple possible canonical subjects.

        Ambiguity must not become a privacy bypass.  The recognizer uses these
        bindings to mask the alias without attaching a potentially incorrect
        public subject ID.
        """

        values: list[tuple[str, tuple[AliasBinding, ...]]] = []
        aliases = set(self._bindings) | set(self._document_bindings)
        for alias in sorted(aliases, key=lambda item: (-len(item), item)):
            scoped = self._document_bindings.get(alias)
            candidates = scoped if scoped else self._bindings.get(alias, [])
            unique = {item.canonical_id: item for item in candidates}
            if len(unique) > 1:
                values.append((alias, tuple(sorted(unique.values(), key=lambda item: item.canonical_id))))
        return tuple(values)

    def subsidiary_bindings(self, text: str) -> tuple[SubsidiaryBinding, ...]:
        result: list[SubsidiaryBinding] = []
        canonical_ranges = [
            (match.start(), match.end())
            for record in self._records.values()
            for match in re.finditer(re.escape(record.canonical), text)
        ]
        for binding in self._resolved_bindings():
            pattern = re.compile(
                rf"(?<![A-Za-z0-9]){re.escape(binding.alias)}"
                rf"(?P<location>[\u3400-\u9fff]{{2,10}}?)(?P<suffix>{self._subsidiary_suffix})"
            )
            for match in pattern.finditer(text):
                surface = match.group(0)
                if surface in self._by_canonical or any(
                    start <= match.start() and match.end() <= end
                    for start, end in canonical_ranges
                ):
                    continue
                location = match.group("location")
                suffix = match.group("suffix")
                result.append(
                    SubsidiaryBinding(
                        start=match.start(),
                        end=match.end(),
                        surface=surface,
                        canonical_id=binding.canonical_id,
                        canonical=binding.canonical,
                        parent_alias=binding.alias,
                        location=location,
                        suffix=suffix,
                        alias_index=binding.alias_index,
                        source="document_subsidiary_pattern",
                        confidence=0.92 if suffix in {"子公司", "分公司"} else 0.86,
                    )
                )
        unique: dict[tuple[int, int, str], SubsidiaryBinding] = {}
        for binding in result:
            unique[(binding.start, binding.end, binding.canonical_id)] = binding
        return tuple(unique.values())


__all__ = [
    "AliasBinding",
    "OrganizationRecord",
    "OrganizationRegistry",
    "SubsidiaryBinding",
]
