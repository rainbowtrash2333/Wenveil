from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .mapping import MappingBuilder, MappingVault, restore_text, sha256_text
from .models import Span
from .normalizer import TextNormalizer
from .recognizers import (
    AddressRecognizer,
    AmountRecognizer,
    BankAccountRecognizer,
    ContractIdRecognizer,
    DateRecognizer,
    DictionaryRecognizer,
    EmailRecognizer,
    IdCardRecognizer,
    ModelNERRecognizer,
    OnnxNERRecognizer,
    NumberRecognizer,
    OrganizationRecognizer,
    PersonRecognizer,
    PhoneRecognizer,
    build_custom_recognizers,
)
from .resolver import resolve_spans


@dataclass(slots=True)
class AnonymizationResult:
    source_text: str
    normalized_text: str
    masked_text: str
    vault: MappingVault
    report: dict[str, Any]
    candidates: list[Span]
    accepted_spans: list[Span]


class Desensitizer:
    def __init__(self, config: EngineConfig | None = None):
        self.config = config or load_config()
        self.normalizer = TextNormalizer(self.config.normalization)
        self.recognizers = self._build_recognizers()

    def _build_recognizers(self):
        recognizers = []
        organization_whitelist = self.config.whitelist.get("organizations", ())
        if organization_whitelist:
            recognizers.append(
                DictionaryRecognizer(
                    "ORG",
                    organization_whitelist,
                    priority=1000,
                    source="organization_whitelist",
                    rule_id="organization_whitelist",
                    anonymize=False,
                    protect=True,
                )
            )
        person_cfg = self.config.entity("PERSON")
        if person_cfg.detect:
            recognizers.append(
                PersonRecognizer(
                    self.config.dictionaries.get("persons", ()),
                    priority=person_cfg.priority,
                    anonymize=person_cfg.anonymize,
                    protect=(not person_cfg.anonymize and person_cfg.protect_when_disabled),
                )
            )
        id_cfg = self.config.entity("ID_CARD")
        if id_cfg.detect:
            recognizers.append(
                IdCardRecognizer(
                    priority=id_cfg.priority,
                    anonymize=id_cfg.anonymize,
                    protect=(not id_cfg.anonymize and id_cfg.protect_when_disabled),
                    allow_15=bool(self.config.normalization.get("allow_15_digit_id", False)),
                )
            )
        org_cfg = self.config.entity("ORG")
        if org_cfg.detect:
            recognizers.append(
                OrganizationRecognizer(
                    self.config.dictionaries.get("organizations", ()),
                    priority=org_cfg.priority,
                    anonymize=org_cfg.anonymize,
                    protect=(not org_cfg.anonymize and org_cfg.protect_when_disabled),
                    registry=self.config.organization_registry,
                )
            )
        for entity_type, dictionary_key in (("PROJECT", "projects"), ("DEPARTMENT", "departments")):
            entity_cfg = self.config.entity(entity_type)
            values = self.config.dictionaries.get(dictionary_key, ())
            if entity_cfg.detect and values:
                recognizers.append(
                    DictionaryRecognizer(
                        entity_type,
                        values,
                        priority=entity_cfg.priority,
                        source=f"{entity_type.lower()}_dictionary",
                        rule_id=f"{entity_type.lower()}_dictionary",
                        anonymize=entity_cfg.anonymize,
                        protect=(not entity_cfg.anonymize and entity_cfg.protect_when_disabled),
                    )
                )
        phone_cfg = self.config.entity("PHONE")
        if phone_cfg.detect:
            recognizers.append(
                PhoneRecognizer(
                    priority=phone_cfg.priority,
                    anonymize=phone_cfg.anonymize,
                    protect=(not phone_cfg.anonymize and phone_cfg.protect_when_disabled),
                )
            )
        email_cfg = self.config.entity("EMAIL")
        if email_cfg.detect:
            recognizers.append(
                EmailRecognizer(
                    priority=email_cfg.priority,
                    anonymize=email_cfg.anonymize,
                    protect=(not email_cfg.anonymize and email_cfg.protect_when_disabled),
                )
            )
        account_cfg = self.config.entity("BANK_ACCOUNT")
        if account_cfg.detect:
            recognizers.append(
                BankAccountRecognizer(
                    priority=account_cfg.priority,
                    anonymize=account_cfg.anonymize,
                    protect=(not account_cfg.anonymize and account_cfg.protect_when_disabled),
                )
            )
        address_cfg = self.config.entity("ADDRESS")
        if address_cfg.detect:
            recognizers.append(
                AddressRecognizer(
                    priority=address_cfg.priority,
                    anonymize=address_cfg.anonymize,
                    protect=(not address_cfg.anonymize and address_cfg.protect_when_disabled),
                )
            )
        contract_cfg = self.config.entity("CONTRACT_ID")
        if contract_cfg.detect:
            recognizers.append(
                ContractIdRecognizer(
                    priority=contract_cfg.priority,
                    anonymize=contract_cfg.anonymize,
                    protect=(not contract_cfg.anonymize and contract_cfg.protect_when_disabled),
                )
            )
        date_cfg = self.config.entity("DATE")
        if date_cfg.detect:
            recognizers.append(
                DateRecognizer(
                    priority=date_cfg.priority,
                    anonymize=date_cfg.anonymize,
                    protect=(not date_cfg.anonymize and date_cfg.protect_when_disabled),
                )
            )
        amount_cfg = self.config.entity("AMOUNT")
        if amount_cfg.detect:
            recognizers.append(
                AmountRecognizer(
                    priority=amount_cfg.priority,
                    anonymize=amount_cfg.anonymize,
                    protect=(not amount_cfg.anonymize and amount_cfg.protect_when_disabled),
                )
            )
        number_cfg = self.config.entity("NUMBER")
        if number_cfg.detect:
            recognizers.append(
                NumberRecognizer(
                    priority=number_cfg.priority,
                    anonymize=number_cfg.anonymize,
                    protect=(not number_cfg.anonymize and number_cfg.protect_when_disabled),
                )
            )
        recognizers.extend(build_custom_recognizers(self.config.custom))
        if self.config.model.get("enabled", False):
            base_dir = self.config.source_path.parent if self.config.source_path else Path.cwd()
            model_backend = str(self.config.model.get("backend", "transformers")).lower()
            recognizer_type = OnnxNERRecognizer if model_backend == "onnx" else ModelNERRecognizer
            recognizers.append(
                recognizer_type(
                    self.config.model,
                    base_dir=base_dir,
                    entity_options=self.config.entities,
                )
            )
        return recognizers

    def normalize(self, text: str) -> str:
        return self.normalizer.normalize(text)

    def recognize(self, normalized_text: str) -> list[Span]:
        candidates: list[Span] = []
        for recognizer in self.recognizers:
            candidates.extend(recognizer.recognize(normalized_text))
        return self._filter_nested_organization_whitelist(candidates)

    @staticmethod
    def _filter_nested_organization_whitelist(candidates: list[Span]) -> list[Span]:
        """Do not let a public-name substring exempt a larger private entity.

        Exact whitelist matches remain protected.  When the same text is only
        a proper substring of a larger anonymizing organization candidate, the
        larger entity keeps normal masking behavior.
        """

        anonymizing_organizations = [
            span
            for span in candidates
            if span.source != "organization_whitelist"
            and span.anonymize
            and span.entity_type.startswith("ORG")
        ]
        result: list[Span] = []
        for span in candidates:
            if span.source != "organization_whitelist":
                result.append(span)
                continue
            nested = any(
                other.start <= span.start
                and span.end <= other.end
                and (other.start < span.start or span.end < other.end)
                for other in anonymizing_organizations
            )
            if not nested:
                result.append(span)
        return result

    def anonymize(self, source_text: str, *, source_name: str = "") -> AnonymizationResult:
        started = time.perf_counter()
        normalized = self.normalize(source_text)
        candidates = self.recognize(normalized)
        accepted = resolve_spans(candidates, normalized)
        job_id = hashlib.sha256((sha256_text(normalized) + self.config.config_hash).encode("ascii")).hexdigest()[:12]
        builder = MappingBuilder(normalized, job_id=job_id)
        parts: list[str] = []
        cursor = 0
        masked_count: dict[str, int] = {}
        for span in accepted:
            parts.append(normalized[cursor : span.start])
            if span.anonymize:
                parts.append(
                    builder.token_for(
                        span.entity_type,
                        span.surface,
                        canonical_id=span.canonical_id,
                        canonical_name=span.canonical_name,
                        relation=span.relation,
                        alias_index=span.alias_index,
                        location=span.location,
                    )
                )
                masked_count[span.entity_type] = masked_count.get(span.entity_type, 0) + 1
            else:
                parts.append(span.surface)
            cursor = span.end
        parts.append(normalized[cursor:])
        masked = "".join(parts)

        detected_count: dict[str, int] = {}
        accepted_count: dict[str, int] = {}
        for span in candidates:
            detected_count[span.entity_type] = detected_count.get(span.entity_type, 0) + 1
        for span in accepted:
            accepted_count[span.entity_type] = accepted_count.get(span.entity_type, 0) + 1
        entity_types = sorted(set(detected_count) | set(accepted_count) | set(masked_count))
        entities = {
            entity_type: {
                "detected": detected_count.get(entity_type, 0),
                "accepted": accepted_count.get(entity_type, 0),
                "masked": masked_count.get(entity_type, 0),
            }
            for entity_type in entity_types
        }
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        builtin_types = {
            "PERSON",
            "ID_CARD",
            "ORG",
            "PROJECT",
            "DEPARTMENT",
            "ADDRESS",
            "PHONE",
            "EMAIL",
            "BANK_ACCOUNT",
            "AMOUNT",
            "DATE",
            "CONTRACT_ID",
            "NUMBER",
        }
        report: dict[str, Any] = {
            "schema_version": 1,
            "entities": entities,
            "custom": {
                "detected": sum(value["detected"] for key, value in entities.items() if key not in builtin_types),
                "masked": sum(value["masked"] for key, value in entities.items() if key not in builtin_types),
            },
            "processing_ms": elapsed_ms,
            "normalized_chars": len(normalized),
            "masked_chars": len(masked),
        }
        relation_counts: dict[str, int] = {}
        for span in accepted:
            if span.relation:
                relation_counts[span.relation] = relation_counts.get(span.relation, 0) + 1
        report["organization_relations"] = {
            "detected": sum(1 for span in candidates if span.relation),
            "accepted": sum(1 for span in accepted if span.relation),
            "masked": sum(1 for span in accepted if span.relation and span.anonymize),
            "by_relation": dict(sorted(relation_counts.items())),
        }
        whitelist_candidates = [span for span in candidates if span.source == "organization_whitelist"]
        whitelist_accepted = [span for span in accepted if span.source == "organization_whitelist"]
        report["whitelist"] = {
            "detected": len(whitelist_candidates),
            "accepted": len(whitelist_accepted),
            "protected": sum(1 for span in whitelist_accepted if span.protect and not span.anonymize),
        }
        for entity_type, counts in entities.items():
            report[entity_type] = counts
        vault = MappingVault(
            schema_version=2,
            job_id=job_id,
            source_hash=sha256_text(source_text),
            normalized_hash=sha256_text(normalized),
            masked_hash=sha256_text(masked),
            config_hash=self.config.config_hash,
            token_format="compact-v1",
            source_name=source_name,
            token_to_surface=builder.token_to_surface,
            entity_counts=builder.entity_counts,
            alias_relations=builder.alias_relations,
        )
        return AnonymizationResult(
            source_text=source_text,
            normalized_text=normalized,
            masked_text=masked,
            vault=vault,
            report=report,
            candidates=candidates,
            accepted_spans=accepted,
        )

    def anonymize_file(self, path: str | Path) -> AnonymizationResult:
        source_path = Path(path)
        source = source_path.read_text(encoding="utf-8-sig")
        return self.anonymize(source, source_name=source_path.name)

    @staticmethod
    def restore(masked_text: str, vault: MappingVault) -> str:
        return restore_text(masked_text, vault)
