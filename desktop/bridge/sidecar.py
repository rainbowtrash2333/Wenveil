"""JSON Lines adapter for the existing Wenveil Python processing modules.

The desktop shell owns process lifetime and file pickers. This adapter owns
only request validation, step orchestration, and safe result serialization.
It never writes source values to stdout, logs, or reports.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

if getattr(sys, "frozen", False):
    # PyInstaller onedir keeps Python modules and bundled data under the
    # internal runtime directory.  Resolve config/rules from that directory
    # instead of the build machine's source checkout.
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.safety import safe_id
from desensitize.audit import audit_masked_text
from desensitize.config import CustomRule, EntityConfig, load_config
from desensitize.mapping import MappingVault, restore_text, sha256_text
from desensitize.pipeline import Desensitizer
from organize.core import OrganizeOptions, TextOrganizer


ProgressWriter = Callable[[dict[str, Any]], None]


def safe_error(error: Exception) -> str:
    """Convert implementation failures into non-sensitive user messages."""

    if isinstance(error, FileNotFoundError):
        return "输入文件、映射文件或配置不存在"
    if isinstance(error, PermissionError):
        return "文件或输出位置没有访问权限"
    if isinstance(error, (ValueError, TypeError)):
        message = str(error)
        safe_messages = {
            "请至少开启一个处理步骤",
            "脱敏检查需要同时开启文档脱敏",
            "输出位置不能为空",
            "桌面端未提供有效的本地文件路径",
            "输入文件不存在或不可读取",
            "未开启文字识别时，只能处理 Markdown、TXT 或 RTF 文件",
            "开启文档脱敏时必须提供密码",
            "输入文件列表格式无效",
            "输入文件内容无效",
            "脱敏文件、映射文件和恢复密码均不能为空",
            "脱敏文件或映射文件不存在",
            "unsupported mapping format",
            "masked text hash does not match the mapping",
            "restored text hash does not match the mapping",
        }
        if message == "unable to decrypt mapping; password or file may be invalid":
            return "密码错误或文件完整性验证未通过"
        if message.startswith("mapping entry is missing for token "):
            return "映射文件缺少恢复项"
        if message in safe_messages:
            return message
        return "输入参数或文件内容无效，请检查后重试"
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "OCR 依赖未安装，请安装 Wenveil 的 OCR 可选依赖"
    return "本地处理失败，请检查输入文件、密码和输出位置"


def _bool_steps(payload: dict[str, Any]) -> dict[str, bool]:
    raw = payload.get("steps") or {}
    steps = {key: bool(raw.get(key, False)) for key in ("ocr", "organize", "mask", "audit")}
    if not any(steps.values()):
        raise ValueError("请至少开启一个处理步骤")
    if steps["audit"] and not steps["mask"]:
        raise ValueError("脱敏检查需要同时开启文档脱敏")
    return steps


def _output_dir(payload: dict[str, Any]) -> Path:
    value = payload.get("outputDir") or payload.get("output_dir")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("输出位置不能为空")
    target = Path(value).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _contains_model_file(path: Path, patterns: tuple[str, ...]) -> bool:
    """Return whether a local model directory contains a supported weight file."""

    return path.is_dir() and any(
        file.is_file() for pattern in patterns for file in path.rglob(pattern)
    )


def _runtime_root() -> Path:
    """Resolve the directory containing the directory-style release."""

    if getattr(sys, "frozen", False):
        # The sidecar is stored in <release>/wenveil-sidecar/.
        return Path(sys.executable).resolve().parent.parent
    return PROJECT_ROOT


def _find_local_model(
    env_name: str,
    relative_parts: tuple[str, ...],
    patterns: tuple[str, ...],
) -> Path | None:
    """Find a complete local model without contacting a model registry."""

    candidates: list[Path] = []
    explicit = os.environ.get(env_name)
    if explicit:
        candidates.append(Path(explicit).expanduser())

    roots = [_runtime_root(), Path.cwd()]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        candidates.append(root.joinpath(*relative_parts))

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved = candidate.resolve()
        if _contains_model_file(resolved, patterns):
            return resolved
    return None


def _path_from_file(item: dict[str, Any]) -> Path:
    value = item.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("桌面端未提供有效的本地文件路径")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("输入文件不存在或不可读取")
    return path


def _progress(emit: ProgressWriter, file_id: str, status: str, progress: float, **extra: Any) -> None:
    event: dict[str, Any] = {
        "type": "progress",
        "event": {"fileId": file_id, "status": status, "progress": progress},
    }
    event["event"].update(extra)
    emit(event)


def _configured_engine(payload: dict[str, Any]) -> Desensitizer:
    config = load_config()
    selected = {str(value).upper() for value in payload.get("entities", [])}
    entity_types = ("PERSON", "ORG", "ID_CARD", "PHONE", "BANK_ACCOUNT", "AMOUNT", "NUMBER")
    entities = dict(config.entities)
    for entity_type in entity_types:
        current = entities.get(entity_type)
        if current is None:
            current = EntityConfig()
        entities[entity_type] = replace(
            current,
            anonymize=entity_type in selected,
            protect_when_disabled=True,
        )
    custom_rules: tuple[CustomRule, ...] = tuple(
        replace(
            rule,
            anonymize=("CUSTOM" in selected or rule.entity_type in selected),
            protect_when_disabled=True,
        )
        for rule in config.custom
    )
    model = dict(config.model)
    # A packaged model lives next to the app, not inside PyInstaller's
    # read-only _internal directory.  Enable it only when a real local
    # checkpoint is present; never turn on a missing or incomplete model.
    qwen_patterns = ("*.safetensors", "pytorch_model*.bin", "*.pt", "*.pth")
    configured_path = model.get("path")
    configured_candidate = Path(str(configured_path)).expanduser() if configured_path else None
    if configured_candidate and not configured_candidate.is_absolute():
        configured_candidate = _runtime_root() / configured_candidate
    if configured_candidate and _contains_model_file(configured_candidate, qwen_patterns):
        model["path"] = str(configured_candidate.resolve())
        model["enabled"] = True
    else:
        qwen = _find_local_model(
            "WENVEIL_QWEN_MODEL_PATH",
            ("models", "qwen3-1.7b-pii"),
            qwen_patterns,
        )
        if qwen:
            model["path"] = str(qwen)
            model["enabled"] = True
        else:
            model["enabled"] = False
    if not bool(payload.get("aiEnhanced", True)):
        model["enabled"] = False
    return Desensitizer(replace(config, entities=entities, custom=custom_rules, model=model))


def _read_for_processing(path: Path, steps: dict[str, bool], payload: dict[str, Any]) -> str:
    if steps["ocr"]:
        from ocr.config import load_config as load_ocr_config
        from ocr.converter import DocumentConverter

        config = load_ocr_config(str(PROJECT_ROOT / "config" / "ocr.yaml"))
        ocr_model_dir = _find_local_model(
            "WENVEIL_OCR_MODEL_PATH",
            ("models", "ocr"),
            ("*.onnx", "*.pdmodel", "*.pdiparams"),
        )
        if ocr_model_dir:
            config = replace(
                config,
                ocr=replace(config.ocr, model_dir=str(ocr_model_dir)),
            )
        mode = str(payload.get("ocrMode", "auto")).lower()
        device = str(payload.get("device", "auto")).lower()
        if mode == "fast":
            config.docling = replace(config.docling, table_mode="fast")
        if device == "cpu":
            config.ocr = replace(config.ocr, use_gpu=False, use_dml=False)
        elif device == "gpu":
            config.ocr = replace(config.ocr, use_gpu=True, use_dml=False)
        return DocumentConverter(config).convert(path) or ""
    if path.suffix.lower() not in {".md", ".markdown", ".txt", ".rtf"}:
        raise ValueError("未开启文字识别时，只能处理 Markdown、TXT 或 RTF 文件")
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _safe_stem(path: Path, text: str) -> str:
    identity = path.name + chr(0) + sha256_text(text)
    return f"document-{safe_id(identity)}"


def _process_one(item: dict[str, Any], steps: dict[str, bool], payload: dict[str, Any], target: Path, emit: ProgressWriter) -> dict[str, Any]:
    file_id = str(item.get("id") or safe_id(str(item.get("path", "unknown"))))
    source_path = _path_from_file(item)
    name = str(item.get("name") or source_path.name)
    _progress(emit, file_id, "running", 0.02, fileName=name, step="ocr" if steps["ocr"] else "organize")
    text = _read_for_processing(source_path, steps, payload)
    if steps["ocr"]:
        _progress(emit, file_id, "done", 0.25, fileName=name, step="ocr")
    if steps["organize"]:
        text = TextOrganizer(OrganizeOptions()).organize_text(text)
        _progress(emit, file_id, "done", 0.5 if steps["mask"] else 0.9, fileName=name, step="organize")

    output_names: list[str] = []
    audit_issues: list[dict[str, Any]] = []
    if steps["mask"]:
        password = payload.get("password")
        if not isinstance(password, str) or not password:
            raise ValueError("开启文档脱敏时必须提供密码")
        result = _configured_engine(payload).anonymize(text, source_name=source_path.name)
        stem = _safe_stem(source_path, text)
        normalized_name = f"{stem}.normalized.md"
        masked_name = f"{stem}.masked.md"
        mapping_name = f"{stem}.mapping.enc"
        report_name = f"{stem}.report.json"
        (target / masked_name).write_text(result.masked_text, encoding="utf-8")
        result.vault.save(target / mapping_name, password)
        (target / report_name).write_text(json.dumps(result.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if bool(payload.get("keepIntermediate", False)):
            (target / normalized_name).write_text(result.normalized_text, encoding="utf-8")
            output_names.append(normalized_name)
        output_names.extend([masked_name, mapping_name, report_name])
        _progress(emit, file_id, "done", 0.75, fileName=name, step="mask")
        if steps["audit"]:
            audit_issues = [issue.to_dict() for issue in audit_masked_text(result.masked_text)]
            _progress(emit, file_id, "done" if not audit_issues else "attention", 1.0, fileName=name, step="audit")
    else:
        suffix = "organized" if steps["organize"] else "ocr" if steps["ocr"] else "processed"
        output_name = f"{_safe_stem(source_path, text)}.{suffix}.md"
        (target / output_name).write_text(text, encoding="utf-8")
        output_names.append(output_name)
        _progress(emit, file_id, "done", 1.0, fileName=name, step="organize" if steps["organize"] else "ocr")
    status = "attention" if audit_issues else "done"
    return {"fileId": file_id, "status": status, "outputNames": output_names, "auditIssues": audit_issues}


def process_request(payload: dict[str, Any], emit: ProgressWriter) -> dict[str, Any]:
    steps = _bool_steps(payload)
    items = payload.get("files")
    if not isinstance(items, list) or not items:
        raise ValueError("请至少选择一个输入文件")
    target = _output_dir(payload)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="wenveil-input-") as temp_dir:
        materialized: list[tuple[dict[str, Any], str | None]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError("输入文件列表格式无效")
            encoded = item.get("contentBase64")
            if encoded:
                suffix = str(item.get("extension") or ".txt")
                path = Path(temp_dir) / f"input-{index}{suffix}"
                try:
                    path.write_bytes(base64.b64decode(str(encoded), validate=True))
                except (ValueError, base64.binascii.Error):
                    materialized.append((item, "输入文件内容无效"))
                    continue
                materialized.append(({**item, "path": str(path)}, None))
            else:
                materialized.append((item, None))
        for item, materialize_error in materialized:
            file_id = str(item.get("id") or "unknown")
            if materialize_error:
                _progress(emit, file_id, "error", 1.0, message=materialize_error)
                results.append({"fileId": file_id, "status": "error", "outputNames": [], "auditIssues": [], "message": materialize_error})
                continue
            try:
                result = _process_one(item, steps, payload, target, emit)
                results.append(result)
            except Exception as error:
                message = safe_error(error)
                _progress(emit, file_id, "error", 1.0, message=message)
                results.append({"fileId": file_id, "status": "error", "outputNames": [], "auditIssues": [], "message": message})
    return {"files": results}


def restore_request(payload: dict[str, Any]) -> dict[str, Any]:
    masked_value = payload.get("maskedPath") or payload.get("masked_path")
    mapping_value = payload.get("mappingPath") or payload.get("mapping_path")
    password = payload.get("password")
    if not all(isinstance(value, str) and value.strip() for value in (masked_value, mapping_value, password)):
        raise ValueError("脱敏文件、映射文件和恢复密码均不能为空")
    with tempfile.TemporaryDirectory(prefix="wenveil-restore-") as temp_dir:
        masked_path = Path(masked_value).expanduser().resolve()
        mapping_path = Path(mapping_value).expanduser().resolve()
        if payload.get("maskedContentBase64"):
            masked_path = Path(temp_dir) / "input.masked.md"
            masked_path.write_bytes(base64.b64decode(str(payload["maskedContentBase64"]), validate=True))
        if payload.get("mappingContentBase64"):
            mapping_path = Path(temp_dir) / "input.mapping.enc"
            mapping_path.write_bytes(base64.b64decode(str(payload["mappingContentBase64"]), validate=True))
        if not masked_path.is_file() or not mapping_path.is_file():
            raise FileNotFoundError("脱敏文件或映射文件不存在")
        vault = MappingVault.load(mapping_path, password)
        restored = restore_text(masked_path.read_text(encoding="utf-8-sig"), vault)
        target = _output_dir(payload)
        output_name = f"document-{safe_id(str(masked_path))}.restored.md"
        (target / output_name).write_text(restored, encoding="utf-8")
    return {"outputName": output_name}


def handle_request(payload: dict[str, Any], emit: ProgressWriter | None = None) -> dict[str, Any]:
    emit = emit or (lambda _event: None)
    operation = payload.get("op")
    if operation == "health":
        return {"status": "ready", "capabilities": ["process", "restore"]}
    if operation == "process":
        return process_request(payload, emit)
    if operation == "restore":
        return restore_request(payload)
    raise ValueError("不支持的桥接操作")


def _serve() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        payload: Any = None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("请求必须是 JSON 对象")
            request_id = payload.get("id")

            def emit(event: dict[str, Any]) -> None:
                event["id"] = request_id
                sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
                sys.stdout.flush()

            result = handle_request(payload, emit)
            emit({"type": "result", "result": result})
        except Exception as error:
            sys.stdout.write(json.dumps({"type": "error", "id": payload.get("id") if isinstance(payload, dict) else None, "message": safe_error(error)}, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    _serve()
