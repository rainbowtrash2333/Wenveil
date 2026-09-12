from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LEGACY_TOKEN_PATTERN = r"⟦[A-Z][A-Z0-9_]*:\d{6}(?::[0-9a-f]{12})?⟧"
_COMPACT_TOKEN_PATTERN = (
    r"⟦(?:机构\d+(?:-(?:别名|子公司|分公司)\d+)?(?:-\d+)?|"
    r"人员\d+(?:-\d+)?|电话\d+(?:-\d+)?|邮箱\d+(?:-\d+)?|账号\d+(?:-\d+)?|"
    r"证件\d+(?:-\d+)?|地址\d+(?:-\d+)?|项目\d+(?:-\d+)?|部门\d+(?:-\d+)?|"
    r"合同\d+(?:-\d+)?|日期\d+(?:-\d+)?|金额\d+(?:-\d+)?|数字\d+(?:-\d+)?|"
    r"实体\d+(?:-\d+)?)⟧"
)
TOKEN_PATTERN = re.compile(rf"(?:{_LEGACY_TOKEN_PATTERN}|{_COMPACT_TOKEN_PATTERN})")
COMPACT_TOKEN_PATTERN = re.compile(_COMPACT_TOKEN_PATTERN)
_AAD = b"local-desensitize.mapping.v1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class MappingVault:
    schema_version: int
    job_id: str
    source_hash: str
    normalized_hash: str
    masked_hash: str
    config_hash: str
    token_format: str = "legacy-v1"
    source_name: str = ""
    token_to_surface: dict[str, str] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    alias_relations: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "source_hash": self.source_hash,
            "normalized_hash": self.normalized_hash,
            "masked_hash": self.masked_hash,
            "config_hash": self.config_hash,
            "token_format": self.token_format,
            "source_name": self.source_name,
            "entities": dict(sorted(self.token_to_surface.items())),
            "entity_counts": dict(sorted(self.entity_counts.items())),
            "alias_relations": {
                token: dict(sorted(value.items()))
                for token, value in sorted(self.alias_relations.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MappingVault":
        required = {"schema_version", "job_id", "normalized_hash", "masked_hash", "config_hash", "entities"}
        missing = required.difference(data)
        if missing:
            raise ValueError(f"mapping is missing fields: {', '.join(sorted(missing))}")
        entities = data["entities"]
        if not isinstance(entities, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in entities.items()):
            raise ValueError("mapping entities must be a string-to-string object")
        return cls(
            schema_version=int(data["schema_version"]),
            job_id=str(data["job_id"]),
            source_hash=str(data.get("source_hash", "")),
            normalized_hash=str(data["normalized_hash"]),
            masked_hash=str(data["masked_hash"]),
            config_hash=str(data["config_hash"]),
            token_format=str(
                data.get(
                    "token_format",
                    "compact-v1" if int(data["schema_version"]) >= 2 else "legacy-v1",
                )
            ),
            source_name=str(data.get("source_name", "")),
            token_to_surface=dict(entities),
            entity_counts={str(k): int(v) for k, v in (data.get("entity_counts") or {}).items()},
            alias_relations={
                str(token): {str(key): str(value) for key, value in relation.items()}
                for token, relation in (data.get("alias_relations") or {}).items()
                if isinstance(relation, dict)
            },
        )

    def dumps(self, password: str) -> bytes:
        if not password:
            raise ValueError("a non-empty password is required for an encrypted mapping")
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        except ImportError as exc:  # pragma: no cover - project dependency is declared
            raise RuntimeError("cryptography is required for encrypted mappings") from exc
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))
        ciphertext = AESGCM(key).encrypt(nonce, payload, _AAD)
        envelope = {
            "format": "local-desensitize.mapping.enc.v1",
            "kdf": "scrypt",
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def loads(cls, data: bytes, password: str) -> "MappingVault":
        if not password:
            raise ValueError("a non-empty password is required for an encrypted mapping")
        try:
            envelope = json.loads(data.decode("utf-8"))
            if envelope.get("format") != "local-desensitize.mapping.enc.v1":
                raise ValueError("unsupported mapping format")
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

            salt = base64.b64decode(envelope["salt"])
            nonce = base64.b64decode(envelope["nonce"])
            ciphertext = base64.b64decode(envelope["ciphertext"])
            key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))
            payload = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
            return cls.from_dict(json.loads(payload.decode("utf-8")))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("unable to decrypt mapping; password or file may be invalid") from exc

    def save(self, path: str | Path, password: str) -> None:
        Path(path).write_bytes(self.dumps(password))

    @classmethod
    def load(cls, path: str | Path, password: str) -> "MappingVault":
        return cls.loads(Path(path).read_bytes(), password)


class MappingBuilder:
    _COMPACT_LABELS = {
        "ORG": "机构",
        "ORG_ALIAS": "机构",
        "ORG_SUBSIDIARY": "机构",
        "ORG_BRANCH": "机构",
        "PERSON": "人员",
        "PHONE": "电话",
        "EMAIL": "邮箱",
        "BANK_ACCOUNT": "账号",
        "ID_CARD": "证件",
        "ADDRESS": "地址",
        "PROJECT": "项目",
        "PROJECT_FIELD": "项目",
        "DEPARTMENT": "部门",
        "CONTRACT_ID": "合同",
        "DATE": "日期",
        "AMOUNT": "金额",
        "NUMBER": "数字",
    }

    def __init__(self, normalized_text: str, *, job_id: str):
        self.normalized_text = normalized_text
        self.job_id = job_id
        self._by_key: dict[tuple[str, str, str, str, int, str], str] = {}
        self._token_to_surface: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._public_entity_ids: dict[str, str] = {}
        self._alias_relations: dict[str, dict[str, str]] = {}

    def _public_entity_id(self, canonical_id: str) -> str:
        existing = self._public_entity_ids.get(canonical_id)
        if existing is not None:
            return existing
        public_id = f"O{len(self._public_entity_ids) + 1:03d}"
        self._public_entity_ids[canonical_id] = public_id
        return public_id

    def token_for(
        self,
        entity_type: str,
        surface: str,
        *,
        canonical_id: str = "",
        canonical_name: str = "",
        relation: str = "",
        alias_index: int = 0,
        location: str = "",
    ) -> str:
        key = (entity_type.upper(), surface, canonical_id, relation, alias_index, location)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        entity_key = entity_type.upper()
        public_entity_id = ""
        compact_base = ""
        if canonical_id:
            public_entity_id = self._public_entity_id(canonical_id)
            public_number = int(public_entity_id[1:])
            if relation == "ALIAS_OF":
                compact_base = f"机构{public_number}-别名{alias_index or 1}"
            elif relation in {"SUBSIDIARY_OF", "BRANCH_OF"}:
                relation_label = "子公司" if relation == "SUBSIDIARY_OF" else "分公司"
                compact_base = f"机构{public_number}-{relation_label}{alias_index or 1}"
            elif entity_key == "ORG":
                compact_base = f"机构{public_number}"
        if not compact_base:
            compact_label = self._COMPACT_LABELS.get(entity_key, "实体")
            counter = self._counters.get(entity_key, 0) + 1
            self._counters[entity_key] = counter
            compact_base = f"{compact_label}{counter}"
        else:
            self._counters[entity_key] = self._counters.get(entity_key, 0) + 1

        token = f"⟦{compact_base}⟧"
        suffix = 1
        while token in self.normalized_text or token in self._token_to_surface:
            suffix += 1
            token = f"⟦{compact_base}-{suffix}⟧"
        self._by_key[key] = token
        self._token_to_surface[token] = surface
        if canonical_id:
            self._alias_relations[token] = {
                "canonical_id": canonical_id,
                "canonical_name": canonical_name,
                "relation": relation or "ORG_OF",
                "public_entity_id": public_entity_id,
                "alias_index": str(alias_index) if alias_index else "",
                "location": location,
            }
        return token

    @property
    def token_to_surface(self) -> dict[str, str]:
        return dict(self._token_to_surface)

    @property
    def entity_counts(self) -> dict[str, int]:
        return dict(self._counters)

    @property
    def alias_relations(self) -> dict[str, dict[str, str]]:
        return {token: dict(value) for token, value in self._alias_relations.items()}


def restore_text(masked_text: str, vault: MappingVault) -> str:
    if vault.masked_hash and sha256_text(masked_text) != vault.masked_hash:
        raise ValueError("masked text hash does not match the mapping")

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in vault.token_to_surface:
            return vault.token_to_surface[token]
        if token.endswith(f":{vault.job_id}⟧"):
            raise ValueError(f"mapping entry is missing for token {token}")
        return token

    restored = TOKEN_PATTERN.sub(replace, masked_text)
    if vault.normalized_hash and sha256_text(restored) != vault.normalized_hash:
        raise ValueError("restored text hash does not match the mapping")
    return restored
