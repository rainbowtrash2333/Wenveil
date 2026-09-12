from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - installation metadata includes PyYAML
    yaml = None


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class EntityConfig:
    detect: bool = True
    anonymize: bool = True
    protect_when_disabled: bool = False
    priority: int = 50


@dataclass(frozen=True, slots=True)
class CustomRule:
    name: str
    entity_type: str
    matcher: str
    values: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    file: str | None = None
    detect: bool = True
    anonymize: bool = True
    protect_when_disabled: bool = False
    priority: int = 95


@dataclass(frozen=True, slots=True)
class EngineConfig:
    version: int = 1
    normalization: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, EntityConfig] = field(default_factory=dict)
    dictionaries: dict[str, tuple[str, ...]] = field(default_factory=dict)
    whitelist: dict[str, tuple[str, ...]] = field(default_factory=dict)
    organization_registry: dict[str, Any] = field(default_factory=dict)
    custom: tuple[CustomRule, ...] = ()
    ner: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None
    config_hash: str = ""

    def entity(self, entity_type: str) -> EntityConfig:
        return self.entities.get(
            entity_type.upper(),
            EntityConfig(detect=False, anonymize=False, protect_when_disabled=False, priority=50),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "normalization": self.normalization,
            "entities": {
                key: {
                    "detect": value.detect,
                    "anonymize": value.anonymize,
                    "protect_when_disabled": value.protect_when_disabled,
                    "priority": value.priority,
                }
                for key, value in sorted(self.entities.items())
            },
            "dictionaries": {
                key: list(value) for key, value in sorted(self.dictionaries.items())
            },
            "whitelist": {
                key: list(value) for key, value in sorted(self.whitelist.items())
            },
            "organization_registry": self.organization_registry,
            "custom": [
                {
                    "name": rule.name,
                    "type": rule.entity_type,
                    "matcher": rule.matcher,
                    "values": list(rule.values),
                    "patterns": list(rule.patterns),
                    "labels": list(rule.labels),
                    "file": rule.file,
                    "detect": rule.detect,
                    "anonymize": rule.anonymize,
                    "protect_when_disabled": rule.protect_when_disabled,
                    "priority": rule.priority,
                }
                for rule in self.custom
            ],
            "ner": self.ner,
            "model": self.model,
        }


def _as_bool(value: Any, default: bool) -> bool:
    return default if value is None else bool(value)


def _load_list(value: Any, base_dir: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, Path)):
        path = Path(value)
        if not path.is_absolute():
            path = base_dir / path
        if path.exists():
            return tuple(
                line.strip()
                for line in path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
        return (str(value),)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if str(item))
    raise TypeError(f"expected a list or file path, got {type(value).__name__}")


def _raw_config(path: Path | None) -> tuple[dict[str, Any], Path, Path | None]:
    chosen = path
    if chosen is None:
        cwd_default = Path.cwd() / "config" / "default.yaml"
        package_default = PACKAGE_ROOT / "config" / "default.yaml"
        bundled_default = PACKAGE_DIR / "config" / "default.yaml"
        chosen = next((candidate for candidate in (cwd_default, package_default, bundled_default) if candidate.exists()), bundled_default)
    chosen = Path(chosen).resolve()
    if not chosen.exists():
        raise FileNotFoundError(f"configuration file not found: {chosen}")
    if yaml is None:
        raise RuntimeError("PyYAML is required to load YAML configuration files")
    data = yaml.safe_load(chosen.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(data, dict):
        raise ValueError("configuration root must be a mapping")
    return data, chosen.parent, chosen


def load_config(path: str | Path | None = None) -> EngineConfig:
    raw, base_dir, source_path = _raw_config(Path(path) if path else None)

    raw_entities = raw.get("entities") or {}
    entities: dict[str, EntityConfig] = {}
    for name, values in raw_entities.items():
        values = values or {}
        if isinstance(values, bool):
            values = {"detect": values, "anonymize": values}
        key = str(name).upper()
        entities[key] = EntityConfig(
            detect=_as_bool(values.get("detect"), True),
            anonymize=_as_bool(values.get("anonymize"), True),
            protect_when_disabled=_as_bool(values.get("protect_when_disabled"), False),
            priority=int(values.get("priority", 50)),
        )

    dictionaries: dict[str, tuple[str, ...]] = {}
    for name, value in (raw.get("dictionaries") or {}).items():
        dictionaries[str(name)] = _load_list(value, base_dir)

    raw_whitelist = raw.get("whitelist") or {}
    if isinstance(raw_whitelist, (str, Path, list, tuple)):
        raw_whitelist = {"organizations": raw_whitelist}
    if not isinstance(raw_whitelist, dict):
        raise TypeError("whitelist must be a mapping, list, or file path")
    whitelist: dict[str, tuple[str, ...]] = {}
    for name, value in raw_whitelist.items():
        whitelist[str(name)] = _load_list(value, base_dir)

    raw_registry = raw.get("organization_registry") or {}
    if isinstance(raw_registry, (str, Path)):
        registry_path = Path(raw_registry)
        if not registry_path.is_absolute():
            registry_path = base_dir / registry_path
        if registry_path.exists():
            loaded_registry = yaml.safe_load(registry_path.read_text(encoding="utf-8-sig")) or {}
            if not isinstance(loaded_registry, dict):
                raise ValueError("organization_registry file must contain a mapping")
            raw_registry = loaded_registry
        else:
            raw_registry = {}
    if not isinstance(raw_registry, dict):
        raise TypeError("organization_registry must be a mapping or YAML path")

    custom_rules: list[CustomRule] = []
    for index, values in enumerate(raw.get("custom") or []):
        if not isinstance(values, dict):
            raise ValueError(f"custom rule #{index + 1} must be a mapping")
        matcher = str(values.get("matcher", "literal")).lower()
        if matcher not in {"literal", "dictionary", "regex", "field"}:
            raise ValueError(f"unsupported custom matcher: {matcher}")
        inline_values = _load_list(values.get("values"), base_dir)
        file_value = str(values["file"]) if values.get("file") is not None else None
        if file_value:
            file_values = _load_list(file_value, base_dir)
            inline_values = tuple(dict.fromkeys(inline_values + file_values))
        custom_rules.append(
            CustomRule(
                name=str(values.get("name", f"custom_{index + 1}")),
                entity_type=str(values.get("type", "CUSTOM")).upper(),
                matcher=matcher,
                values=inline_values,
                patterns=tuple(str(item) for item in values.get("patterns", []) or []),
                labels=tuple(str(item) for item in values.get("labels", []) or []),
                file=file_value,
                detect=_as_bool(values.get("detect"), True),
                anonymize=_as_bool(values.get("anonymize"), True),
                protect_when_disabled=_as_bool(values.get("protect_when_disabled"), False),
                priority=int(values.get("priority", 95)),
            )
        )

    config = EngineConfig(
        version=int(raw.get("version", 1)),
        normalization=dict(raw.get("normalization") or {}),
        entities=entities,
        dictionaries=dictionaries,
        whitelist=whitelist,
        organization_registry=dict(raw_registry),
        custom=tuple(custom_rules),
        ner=dict(raw.get("ner") or {}),
        model=dict(raw.get("model") or raw.get("ner") or {}),
        source_path=source_path,
    )
    canonical = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return EngineConfig(
        version=config.version,
        normalization=config.normalization,
        entities=config.entities,
        dictionaries=config.dictionaries,
        whitelist=config.whitelist,
        organization_registry=config.organization_registry,
        custom=config.custom,
        ner=config.ner,
        model=config.model,
        source_path=config.source_path,
        config_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
