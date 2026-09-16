"""JSON Lines adapter for :mod:`workflow`.

The desktop bridge intentionally contains no OCR, organization or masking
algorithm. It translates the existing desktop payload to the unified service
and keeps the established progress/result shape for the current UI.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from workflow import (  # noqa: E402
    InputRef,
    ProcessRequest,
    RestoreRequest,
    WorkflowError,
    WorkflowService,
    WorkflowSteps,
)


ProgressWriter = Callable[[dict[str, Any]], None]


def safe_error(error: Exception) -> str:
    """Convert implementation failures into non-sensitive user messages."""

    if isinstance(error, FileNotFoundError):
        return "输入文件、映射文件或配置不存在"
    if isinstance(error, PermissionError):
        return "文件或输出位置没有访问权限"
    if isinstance(error, WorkflowError):
        return error.summary
    if isinstance(error, (ValueError, TypeError)):
        safe_messages = {
            "请至少开启一个处理步骤",
            "脱敏检查需要同时开启文档脱敏",
            "输出位置不能为空",
            "桌面端未提供有效的本地文件路径",
            "输入文件不存在或不可读取",
            "未开启文字识别时，只能处理 Markdown、TXT 或 RTF 文件",
            "密码格式无效",
            "输入文件列表格式无效",
            "输入文件内容无效",
            "脱敏文件、映射文件和恢复密码均不能为空",
            "脱敏文件或映射文件不存在",
            "unsupported mapping format",
            "masked text hash does not match the mapping",
            "restored text hash does not match the mapping",
        }
        if str(error) == "unable to decrypt mapping; password or file may be invalid":
            return "密码错误或文件完整性验证未通过"
        if str(error).startswith("mapping entry is missing for token "):
            return "映射文件缺少恢复项"
        if str(error) in safe_messages:
            return str(error)
        return "输入参数或文件内容无效，请检查后重试"
    if isinstance(error, ImportError):
        return "OCR 依赖未安装，请安装 Wenveil 的 OCR 可选依赖"
    return "本地处理失败，请检查输入文件、密码和输出位置"


def _bool_steps(payload: dict[str, Any]) -> WorkflowSteps:
    raw = payload.get("steps") or {}
    steps = WorkflowSteps(
        ocr=bool(raw.get("ocr", False)),
        organize=bool(raw.get("organize", False)),
        # The current UI has no merge toggle. Preserve its per-file behavior
        # until the UI is rebuilt around the persisted job API.
        merge=bool(raw.get("merge", False)),
        mask=bool(raw.get("mask", False)),
        audit=bool(raw.get("audit", False)),
    )
    if not any(steps.as_dict().values()):
        raise WorkflowError("invalid_steps", "请至少开启一个处理步骤")
    if steps.audit and not steps.mask:
        raise WorkflowError("invalid_steps", "脱敏检查需要同时开启文档脱敏")
    return steps


def _output_dir(payload: dict[str, Any]) -> Path:
    value = payload.get("outputDir") or payload.get("output_dir")
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError("invalid_output", "输出位置不能为空")
    target = Path(value).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _password(payload: dict[str, Any]) -> str | None:
    value = payload.get("password")
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise WorkflowError("invalid_password", "密码格式无效")
    return value


def _progress(emit: ProgressWriter, file_id: str, status: str, progress: float, **extra: Any) -> None:
    event: dict[str, Any] = {
        "type": "progress",
        "event": {"fileId": file_id, "status": status, "progress": progress},
    }
    event["event"].update(extra)
    emit(event)


def _service(payload: dict[str, Any]) -> WorkflowService:
    state_dir = payload.get("stateDir")
    db_path = payload.get("dbPath")
    return WorkflowService(
        db_path=Path(db_path).expanduser() if isinstance(db_path, str) and db_path.strip() else None,
        state_dir=Path(state_dir).expanduser() if isinstance(state_dir, str) and state_dir.strip() else None,
    )


def _safe_materialized_suffix(value: object) -> str:
    """Keep an inline file suffix from escaping the bridge temp directory."""

    suffix = str(value or ".txt").strip().lower()
    if (
        not suffix.startswith(".")
        or suffix in {".", ".."}
        or len(suffix) > 20
        or any(character in suffix for character in ("/", "\\", ":", "\x00"))
    ):
        raise ValueError("输入文件类型无效")
    return suffix


def _materialize(
    items: list[Any],
    temp_dir: Path,
) -> tuple[list[tuple[dict[str, Any], Path]], list[dict[str, Any]]]:
    valid: list[tuple[dict[str, Any], Path]] = []
    invalid: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise WorkflowError("invalid_input", "输入文件列表格式无效")
        encoded = raw.get("contentBase64")
        if encoded:
            try:
                suffix = _safe_materialized_suffix(raw.get("extension"))
            except ValueError:
                invalid.append({
                    "fileId": str(raw.get("id") or f"input-{index}"),
                    "status": "error",
                    "outputNames": [],
                    "auditIssues": [],
                    "reversible": False,
                    "message": "输入文件类型无效",
                })
                continue
            path = temp_dir / f"input-{index}{suffix}"
            try:
                path.write_bytes(base64.b64decode(str(encoded), validate=True))
            except (ValueError, binascii.Error):
                invalid.append({
                    "fileId": str(raw.get("id") or f"input-{index}"),
                    "status": "error",
                    "outputNames": [],
                    "auditIssues": [],
                    "reversible": False,
                    "message": "输入文件内容无效",
                })
                continue
            valid.append(({**raw, "path": str(path)}, path))
            continue
        value = raw.get("path")
        if not isinstance(value, str) or not value.strip():
            invalid.append({
                "fileId": str(raw.get("id") or f"input-{index}"),
                "status": "error",
                "outputNames": [],
                "auditIssues": [],
                "reversible": False,
                "message": "桌面端未提供有效的本地文件路径",
            })
            continue
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            invalid.append({
                "fileId": str(raw.get("id") or f"input-{index}"),
                "status": "error",
                "outputNames": [],
                "auditIssues": [],
                "reversible": False,
                "message": "输入文件不存在或不可读取",
            })
            continue
        valid.append((raw, path))
    return valid, invalid


def _audit_findings(snapshot: Any, item_id: str) -> list[dict[str, Any]]:
    for artifact in snapshot.artifacts:
        if artifact.item_id == item_id and artifact.kind == "audit" and Path(artifact.path).is_file():
            try:
                data = json.loads(Path(artifact.path).read_text(encoding="utf-8"))
                return list(data.get("findings", []))
            except (OSError, ValueError, TypeError):
                return []
    return []


def process_request(payload: dict[str, Any], emit: ProgressWriter) -> dict[str, Any]:
    steps = _bool_steps(payload)
    raw_items = payload.get("files")
    if not isinstance(raw_items, list) or not raw_items:
        raise WorkflowError("input_empty", "请至少选择一个输入文件")
    target = _output_dir(payload)
    password = _password(payload)
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="wenveil-input-") as temp_name:
        valid, invalid = _materialize(raw_items, Path(temp_name))
        results.extend(invalid)
        for item in invalid:
            _progress(emit, item["fileId"], "error", 1.0, message=item["message"])
        if not valid:
            return {"files": results}

        refs = tuple(
            InputRef(
                path,
                item_id=str(item.get("id") or f"input-{index}"),
                display_name=str(item.get("name") or path.name),
            )
            for index, (item, path) in enumerate(valid)
        )
        names = {ref.item_id: ref.display_name for ref in refs}

        def on_progress(event: dict[str, Any]) -> None:
            item_id = event.get("itemId")
            if not item_id:
                return
            status = str(event.get("status") or "running")
            ui_status = "attention" if status == "attention" else "error" if status == "error" else "done" if status == "done" else "running"
            extra: dict[str, Any] = {
                "fileName": names.get(str(item_id), ""),
                "step": event.get("stage"),
            }
            if "issue_count" in event:
                extra["issueCount"] = event["issue_count"]
            _progress(emit, str(item_id), ui_status, float(event.get("progress", 0.0)), **extra)

        request = ProcessRequest(
            inputs=refs,
            output_dir=target,
            steps=steps,
            config_path=Path(payload["configPath"]).expanduser() if isinstance(payload.get("configPath"), str) else None,
            entities=tuple(str(value) for value in payload.get("entities", [])),
            ai_enhanced=bool(payload.get("aiEnhanced", True)),
            ocr_mode=str(payload.get("ocrMode", "auto")).lower(),
            device=str(payload.get("device", "auto")).lower(),
            retain_intermediate=bool(payload.get("keepIntermediate", False)),
            allow_partial=bool(payload.get("allowPartial", False)),
        )
        with _service(payload) as service:
            snapshot = service.process(request, password=password, progress=on_progress)

        by_id = {item.item_id: item for item in snapshot.items}
        for item, _path in valid:
            file_id = str(item.get("id") or "unknown")
            item_snapshot = by_id.get(file_id)
            if item_snapshot is None:
                continue
            status = "attention" if item_snapshot.audit_issue_count else "done" if item_snapshot.status == "succeeded" else "error"
            stage_error = next(
                (stage.error_summary for stage in snapshot.stages if stage.item_id == file_id and stage.error_summary),
                None,
            )
            reversible = any(artifact.item_id == file_id and artifact.kind == "mapping" for artifact in snapshot.artifacts)
            results.append({
                "fileId": file_id,
                "status": status,
                "outputNames": list(item_snapshot.output_names),
                "auditIssues": _audit_findings(snapshot, file_id),
                "reversible": reversible,
                **({"message": stage_error} if stage_error else {}),
            })
    return {"jobId": snapshot.job_id if valid else None, "status": snapshot.status.value if valid else "failed", "files": results}


def restore_request(payload: dict[str, Any]) -> dict[str, Any]:
    masked_value = payload.get("maskedPath") or payload.get("masked_path")
    mapping_value = payload.get("mappingPath") or payload.get("mapping_path")
    if not isinstance(masked_value, str) or not isinstance(mapping_value, str):
        raise WorkflowError("invalid_input", "脱敏文件、映射文件和恢复密码均不能为空")
    target = _output_dir(payload)
    password = _password(payload)
    with tempfile.TemporaryDirectory(prefix="wenveil-restore-") as temp_name:
        temp_dir = Path(temp_name)
        masked_path = Path(masked_value).expanduser().resolve()
        mapping_path = Path(mapping_value).expanduser().resolve()
        try:
            if payload.get("maskedContentBase64"):
                masked_path = temp_dir / "input.masked.md"
                masked_path.write_bytes(base64.b64decode(str(payload["maskedContentBase64"]), validate=True))
            if payload.get("mappingContentBase64"):
                mapping_path = temp_dir / "input.mapping.enc"
                mapping_path.write_bytes(base64.b64decode(str(payload["mappingContentBase64"]), validate=True))
        except (ValueError, binascii.Error):
            raise WorkflowError("invalid_input", "输入文件内容无效") from None
        with _service(payload) as service:
            snapshot = service.restore(
                RestoreRequest(
                    masked_path=masked_path,
                    mapping_path=mapping_path,
                    output_dir=target,
                    password=password,
                    restore_filename=bool(payload.get("restoreFilename", False)),
                )
            )
    restored = next((artifact for artifact in snapshot.artifacts if artifact.kind == "restored"), None)
    return {
        "jobId": snapshot.job_id,
        "status": snapshot.status.value,
        **({"outputName": Path(restored.path).name} if restored else {}),
    }


def handle_request(payload: dict[str, Any], emit: ProgressWriter | None = None) -> dict[str, Any]:
    emit = emit or (lambda _event: None)
    operation = payload.get("op")
    if operation == "health":
        return {"status": "ready", "capabilities": ["process", "restore"]}
    if operation == "process":
        return process_request(payload, emit)
    if operation == "restore":
        return restore_request(payload)
    if operation == "status":
        with _service(payload) as service:
            return service.get_status(str(payload.get("jobId"))).as_dict()
    if operation == "resume":
        with _service(payload) as service:
            return service.resume(str(payload.get("jobId")), password=_password(payload)).as_dict()
    if operation == "cancel":
        with _service(payload) as service:
            return service.cancel(str(payload.get("jobId"))).as_dict()
    raise WorkflowError("unsupported_operation", "不支持的桥接操作")


def _serve() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        payload: Any = None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise WorkflowError("invalid_input", "请求必须是 JSON 对象")
            request_id = payload.get("id")

            def emit(event: dict[str, Any]) -> None:
                event["id"] = request_id
                sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
                sys.stdout.flush()

            result = handle_request(payload, emit)
            emit({"type": "result", "result": result})
        except Exception as error:
            sys.stdout.write(
                json.dumps(
                    {
                        "type": "error",
                        "id": payload.get("id") if isinstance(payload, dict) else None,
                        "message": safe_error(error),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    _serve()
