"""Execution engine for the unified OCR-to-desensitization workflow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from common.safety import safe_id
from desensitize.audit import audit_masked_text
from desensitize.config import CustomRule, EntityConfig, load_config as load_desensitize_config
from desensitize.mapping import MappingVault, restore_text
from desensitize.pipeline import Desensitizer
from organize.core import OrganizeOptions, TextOrganizer
from ocr.formats import SUPPORTED_FILE_EXTENSION_SET, TEXT_EXTENSIONS

from .checkpoints import CheckpointStore, default_state_dir, sha256_file
from .logging import JobLogger
from .models import (
    InputRef,
    JobSnapshot,
    JobStatus,
    ProcessRequest,
    ProgressCallback,
    RestoreRequest,
    StageStatus,
    WorkflowSteps,
)
from .store import JobBusyError, JobNotFoundError, WorkflowStore


_TEXT_EXTENSIONS = set(TEXT_EXTENSIONS)
_OCR_EXTENSIONS = set(SUPPORTED_FILE_EXTENSION_SET)
_KNOWN_ENTITY_TYPES = (
    "PERSON", "ORG", "ID_CARD", "PHONE", "BANK_ACCOUNT", "AMOUNT", "NUMBER",
    "ADDRESS", "CONTRACT_ID", "DATE", "PROJECT", "DEPARTMENT",
)


class WorkflowError(RuntimeError):
    """An expected workflow failure with a safe code and summary."""

    def __init__(self, code: str, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


class _Cancelled(Exception):
    pass


class WorkflowRunner:
    """Create, execute and resume jobs stored in a :class:`WorkflowStore`."""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        state_dir: str | Path | None = None,
        checkpoint_root: str | Path | None = None,
        lease_seconds: int = 300,
    ):
        self.store = store
        self.state_dir = Path(state_dir or default_state_dir()).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_root = Path(checkpoint_root or self.state_dir / "checkpoints").expanduser().resolve()
        self.lease_seconds = lease_seconds
        self.store.reconcile_stale_jobs(lease_seconds)

    def create_process_job(self, request: ProcessRequest) -> JobSnapshot:
        normalized = self._validate_process_request(request)
        inputs = self._expand_inputs(normalized)
        job_id = f"job-{uuid.uuid4().hex}"
        log_path = self.state_dir / "logs" / f"job-{job_id}.log"
        safe_request = self._request_json(normalized)
        stages = self._stage_definitions(normalized.steps, inputs)
        self.store.create_job(
            job_id=job_id,
            operation="process",
            config_hash=self._config_hash(normalized.config_path),
            steps_json=json.dumps(normalized.steps.as_dict(), sort_keys=True),
            request_json=json.dumps(safe_request, ensure_ascii=False, sort_keys=True),
            output_dir=str(normalized.output_dir),
            log_path=str(log_path),
            items=[self._item_record(item, index) for index, item in enumerate(inputs)],
            stages=[
                {"stage_id": f"stage-{uuid.uuid4().hex}", **stage}
                for stage in stages
            ],
        )
        JobLogger(log_path, job_id).write(event="job_created", status=JobStatus.QUEUED.value, count=len(inputs))
        return self.store.snapshot(job_id)

    def create_restore_job(self, request: RestoreRequest) -> JobSnapshot:
        masked = request.masked_path.expanduser().resolve()
        mapping = request.mapping_path.expanduser().resolve()
        if not masked.is_file() or not mapping.is_file():
            raise WorkflowError("input_missing", "脱敏文件或映射文件不存在")
        output_dir = request.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        job_id = f"job-{uuid.uuid4().hex}"
        log_path = self.state_dir / "logs" / f"job-{job_id}.log"
        safe_request = {
            "masked_path": str(masked),
            "mapping_path": str(mapping),
            "restore_filename": bool(request.restore_filename),
        }
        self.store.create_job(
            job_id=job_id,
            operation="restore",
            config_hash=None,
            steps_json=json.dumps({"restore": True}),
            request_json=json.dumps(safe_request, ensure_ascii=False, sort_keys=True),
            output_dir=str(output_dir),
            log_path=str(log_path),
            items=[],
            stages=[{"stage_id": f"stage-{uuid.uuid4().hex}", "stage_name": "restore"}],
        )
        JobLogger(log_path, job_id).write(event="job_created", status=JobStatus.QUEUED.value)
        return self.store.snapshot(job_id)

    def run(
        self,
        job_id: str,
        *,
        password: str | None = None,
        progress: ProgressCallback | None = None,
        resuming: bool = False,
    ) -> JobSnapshot:
        job = self.store.snapshot(job_id)
        owner = f"runner-{uuid.uuid4().hex}"
        logger = JobLogger(job.log_path, job_id)
        self.store.claim_job(job_id, owner, resuming=resuming)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.ATTENTION, JobStatus.CANCELLED}:
            return self.store.snapshot(job_id)
        self._emit(progress, job_id, None, "job", "running", job.progress)
        checkpoint = CheckpointStore(self.checkpoint_root, job_id)
        try:
            if job.operation == "restore":
                self._run_restore(job_id, password=password, progress=progress, logger=logger, checkpoint=checkpoint)
            else:
                self._run_process(job_id, password=password, progress=progress, logger=logger, checkpoint=checkpoint)
        except _Cancelled:
            self.store.set_job_status(job_id, JobStatus.CANCELLED, clear_lease=True)
            logger.write(event="job_cancelled", status=JobStatus.CANCELLED.value)
            checkpoint.cleanup()
        except WorkflowError as error:
            self.store.set_job_status(
                job_id,
                JobStatus.FAILED,
                error_code=error.code,
                error_summary=error.summary,
                clear_lease=True,
            )
            logger.write(event="job_failed", status=JobStatus.FAILED.value, error_code=error.code, summary=error.summary)
        except Exception as error:  # safe boundary: never persist exception text
            code, summary = self._safe_error(error)
            self.store.set_job_status(
                job_id,
                JobStatus.FAILED,
                error_code=code,
                error_summary=summary,
                clear_lease=True,
            )
            logger.write(event="job_failed", status=JobStatus.FAILED.value, error_code=code, summary=summary)
        finally:
            self.store.heartbeat(job_id, owner)
        return self.store.snapshot(job_id)

    def cancel(self, job_id: str) -> JobSnapshot:
        self.store.request_cancel(job_id)
        return self.store.snapshot(job_id)

    def _run_process(
        self,
        job_id: str,
        *,
        password: str | None,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
    ) -> None:
        request = self._request_from_job(job_id)
        item_rows = self.store.get_items(job_id)
        failed_items = 0
        for item_row in item_rows:
            if self.store.is_cancel_requested(job_id):
                raise _Cancelled()
            try:
                self._run_item(
                    job_id,
                    item_row,
                    request,
                    password=password,
                    progress=progress,
                    logger=logger,
                    checkpoint=checkpoint,
                )
            except _Cancelled:
                raise
            except WorkflowError as error:
                failed_items += 1
                self._fail_item(job_id, item_row["item_id"], error, logger)
            except Exception as error:
                failed_items += 1
                code, summary = self._safe_error(error)
                self._fail_item(job_id, item_row["item_id"], WorkflowError(code, summary), logger)

        if failed_items and not request.allow_partial:
            self._skip_job_stages(job_id, "存在失败文件，未发布作业级结果")
            self.store.set_job_status(
                job_id,
                JobStatus.PARTIAL_FAILED,
                error_code="item_failed",
                error_summary="部分输入文件处理失败，未发布完整结果",
                clear_lease=True,
            )
            return

        if request.steps.merge:
            try:
                for stage_name in ("merge", "mask", "audit"):
                    if not getattr(request.steps, stage_name):
                        continue
                    if self.store.is_cancel_requested(job_id):
                        raise _Cancelled()
                    self._run_job_stage(
                        job_id,
                        stage_name,
                        request,
                        password=password,
                        progress=progress,
                        logger=logger,
                        checkpoint=checkpoint,
                    )
            except _Cancelled:
                raise
            except WorkflowError as error:
                stage_name = self._current_running_stage(job_id)
                if stage_name:
                    self.store.fail_stage(stage_name, error.code, error.summary)
                self.store.set_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error_code=error.code,
                    error_summary=error.summary,
                    clear_lease=True,
                )
                return
            except Exception as error:
                code, summary = self._safe_error(error)
                stage_name = self._current_running_stage(job_id)
                if stage_name:
                    self.store.fail_stage(stage_name, code, summary)
                self.store.set_job_status(
                    job_id,
                    JobStatus.FAILED,
                    error_code=code,
                    error_summary=summary,
                    clear_lease=True,
                )
                return

        attention_count = self._attention_count(job_id)
        final_status = JobStatus.PARTIAL_FAILED if failed_items else JobStatus.ATTENTION if attention_count else JobStatus.SUCCEEDED
        self.store.set_job_status(
            job_id,
            final_status,
            error_code="item_failed" if failed_items else None,
            error_summary="部分输入文件处理失败" if failed_items else None,
            attention_count=attention_count,
            clear_lease=True,
        )
        logger.write(
            event="job_finished",
            status=final_status.value,
            count=len(item_rows),
        )
        if final_status in {JobStatus.SUCCEEDED, JobStatus.ATTENTION}:
            checkpoint.cleanup()

    def _run_item(
        self,
        job_id: str,
        item_row: Any,
        request: ProcessRequest,
        *,
        password: str | None,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
    ) -> None:
        item_id = item_row["item_id"]
        item_request = InputRef(Path(item_row["source_path"]), item_id=item_id)
        current_text: str | None = None
        source_stage = "ocr" if request.steps.ocr else "input"
        current_text = self._run_text_stage(
            job_id,
            item_id,
            source_stage,
            request,
            item_request,
            password=password,
            progress=progress,
            logger=logger,
            checkpoint=checkpoint,
        )
        if self.store.is_cancel_requested(job_id):
            raise _Cancelled()
        if request.steps.organize:
            current_text = self._run_organize_stage(
                job_id,
                item_id,
                current_text,
                progress=progress,
                logger=logger,
                checkpoint=checkpoint,
            )
            if self.store.is_cancel_requested(job_id):
                raise _Cancelled()

        if request.steps.merge:
            self.store.update_item(job_id, item_id, status="succeeded")
            return

        if request.steps.mask:
            masked_path, output_names, issue_count = self._run_mask(
                job_id,
                item_id,
                current_text,
                request,
                password=password,
                progress=progress,
                logger=logger,
                checkpoint=checkpoint,
            )
            if self.store.is_cancel_requested(job_id):
                raise _Cancelled()
            if request.steps.audit:
                issue_count = self._run_audit(
                    job_id,
                    item_id,
                    masked_path,
                    progress=progress,
                    logger=logger,
                )
                output_names = self._output_names_for_scope(job_id, item_id)
            self.store.update_item(
                job_id,
                item_id,
                status="succeeded",
                output_names=output_names,
                audit_issue_count=issue_count,
            )
        else:
            output_name = f"document-{safe_id(item_id)}.{'organized' if request.steps.organize else 'ocr' if request.steps.ocr else 'processed'}.md"
            output_path = request.output_dir / output_name
            self._atomic_write_text(output_path, current_text)
            self._register_output(job_id, item_id, "processed", output_path, visible=True, retained=True)
            self.store.update_item(job_id, item_id, status="succeeded", output_names=[output_name])
            self._emit(progress, job_id, item_id, "organize" if request.steps.organize else source_stage, "done", 1.0)

    def _run_text_stage(
        self,
        job_id: str,
        item_id: str,
        stage_name: str,
        request: ProcessRequest,
        item: InputRef,
        *,
        password: str | None,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
    ) -> str:
        stage = self.store.get_stage(job_id, item_id, stage_name)
        if self._stage_valid(stage, password=password):
            source = item.resolved()
            expected_sha256 = next(
                (
                    row["source_sha256"]
                    for row in self.store.get_items(job_id)
                    if row["item_id"] == item_id
                ),
                None,
            )
            if source.is_file() and expected_sha256 and sha256_file(source) != expected_sha256:
                raise WorkflowError("input_changed", "输入文件在作业期间发生变化，无法安全恢复")
            self._emit(progress, job_id, item_id, stage_name, "done", 1.0)
            return self._artifact_text(stage["artifact_id"])
        self.store.start_stage(stage["stage_id"])
        started = time.perf_counter()
        self._emit(progress, job_id, item_id, stage_name, "running", 0.02)
        source = item.resolved()
        if not source.is_file():
            raise WorkflowError("input_missing", "输入文件不存在或不可读取")
        expected_sha256 = next(
            (
                row["source_sha256"]
                for row in self.store.get_items(job_id)
                if row["item_id"] == item_id
            ),
            None,
        )
        if expected_sha256 and sha256_file(source) != expected_sha256:
            raise WorkflowError("input_changed", "输入文件在作业期间发生变化，无法安全恢复")
        if stage_name == "ocr":
            text = self._ocr_text(source, request)
        else:
            if source.suffix.lower() not in _TEXT_EXTENSIONS:
                raise WorkflowError("unsupported_input", "未开启文字识别时，只能处理 Markdown、TXT 或 RTF 文件")
            text = self._read_text(source)
        path, digest, size = checkpoint.write_text(item_id, stage_name, text)
        artifact_id = self.store.add_artifact(
            job_id=job_id,
            item_id=item_id,
            kind=f"{stage_name}_checkpoint",
            path=str(path),
            sha256=digest,
            size_bytes=size,
            visible=False,
            retained=False,
        )
        self.store.complete_stage(stage["stage_id"], artifact_id=artifact_id, safe_summary="文本阶段完成")
        self.store.heartbeat(job_id)
        logger.write(
            event="stage_finished",
            item_id=item_id,
            stage=stage_name,
            status=StageStatus.SUCCEEDED.value,
            size_bytes=len(text.encode("utf-8")),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        self._emit(progress, job_id, item_id, stage_name, "done", 1.0)
        return text

    def _run_organize_stage(
        self,
        job_id: str,
        item_id: str,
        text: str,
        *,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
    ) -> str:
        stage = self.store.get_stage(job_id, item_id, "organize")
        if self._stage_valid(stage):
            self._emit(progress, job_id, item_id, "organize", "done", 1.0)
            return self._artifact_text(stage["artifact_id"])
        self.store.start_stage(stage["stage_id"])
        started = time.perf_counter()
        self._emit(progress, job_id, item_id, "organize", "running", 0.35)
        organized = TextOrganizer(OrganizeOptions()).organize_text(text)
        path, digest, size = checkpoint.write_text(item_id, "organized", organized)
        artifact_id = self.store.add_artifact(
            job_id=job_id,
            item_id=item_id,
            kind="organized_checkpoint",
            path=str(path),
            sha256=digest,
            size_bytes=size,
            visible=False,
            retained=False,
        )
        self.store.complete_stage(stage["stage_id"], artifact_id=artifact_id, safe_summary="文本整理完成")
        logger.write(
            event="stage_finished",
            item_id=item_id,
            stage="organize",
            status=StageStatus.SUCCEEDED.value,
            size_bytes=len(organized.encode("utf-8")),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        self._emit(progress, job_id, item_id, "organize", "done", 1.0)
        return organized

    def _run_job_stage(
        self,
        job_id: str,
        stage_name: str,
        request: ProcessRequest,
        *,
        password: str | None,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
    ) -> None:
        stage = self.store.get_stage(job_id, None, stage_name)
        if self._stage_valid(stage, password=password):
            self._emit(progress, job_id, None, stage_name, "done", 1.0)
            return
        self.store.start_stage(stage["stage_id"])
        started = time.perf_counter()
        self._emit(progress, job_id, None, stage_name, "running", 0.02)
        if stage_name == "merge":
            text = self._merged_text(job_id, checkpoint)
            if request.steps.mask:
                path, digest, size = checkpoint.write_text(None, "merged", text)
                artifact_id = self.store.add_artifact(
                    job_id=job_id, item_id=None, kind="merged_checkpoint", path=str(path),
                    sha256=digest, size_bytes=size, visible=False, retained=False,
                )
            else:
                output_name = request.output_name or f"document-{safe_id(job_id)}.merged.md"
                output_path = request.output_dir / output_name
                self._atomic_write_text(output_path, text)
                artifact_id = self._register_output(job_id, None, "merged", output_path, visible=True, retained=True)
                self._update_job_output_names(job_id, [output_name])
            self.store.complete_stage(stage["stage_id"], artifact_id=artifact_id, safe_summary="文件合并完成")
            logger.write(event="stage_finished", stage="merge", status=StageStatus.SUCCEEDED.value, duration_ms=round((time.perf_counter() - started) * 1000))
            self._emit(progress, job_id, None, "merge", "done", 1.0)
            return
        if stage_name == "mask":
            source_text = self._merged_text_from_stage(job_id, checkpoint)
            masked_path, output_names, _ = self._run_mask(
                job_id,
                None,
                source_text,
                request,
                password=password,
                progress=progress,
                logger=logger,
                checkpoint=checkpoint,
                stage_started=True,
            )
            self._update_job_output_names(job_id, output_names)
            logger.write(event="stage_finished", stage="mask", status=StageStatus.SUCCEEDED.value, duration_ms=round((time.perf_counter() - started) * 1000))
            self._emit(progress, job_id, None, "mask", "done", 1.0)
            return
        if stage_name == "audit":
            masked = self._masked_path(job_id)
            issue_count = self._run_audit(job_id, None, masked, progress=progress, logger=logger, stage_started=True)
            self.store.set_job_status(job_id, JobStatus.RUNNING, attention_count=issue_count)
            logger.write(event="stage_finished", stage="audit", status=StageStatus.SUCCEEDED.value, count=issue_count, duration_ms=round((time.perf_counter() - started) * 1000))
            self._emit(progress, job_id, None, "audit", "attention" if issue_count else "done", 1.0, issue_count=issue_count)
            return
        raise WorkflowError("unsupported_stage", "不支持的工作流阶段")

    def _run_mask(
        self,
        job_id: str,
        item_id: str | None,
        text: str,
        request: ProcessRequest,
        *,
        password: str | None,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
        stage_started: bool = False,
    ) -> tuple[Path, list[str], int]:
        stage = self.store.get_stage(job_id, item_id, "mask")
        if self._stage_valid(stage, password=password):
            masked = self._artifact_path_for_kind(job_id, item_id, "masked")
            names = self._output_names_for_scope(job_id, item_id)
            return masked, names, self._audit_count_for_scope(job_id, item_id)
        if not stage_started:
            self.store.start_stage(stage["stage_id"])
        scope = item_id or job_id
        started = time.perf_counter()
        self._emit(progress, job_id, item_id, "mask", "running", 0.55)
        engine = self._configured_engine(request)
        result = engine.anonymize(text, source_name=f"document-{safe_id(scope)}.md")
        stem = f"document-{safe_id(scope)}"
        request.output_dir.mkdir(parents=True, exist_ok=True)
        masked_path = request.output_dir / f"{stem}.masked.md"
        report_path = request.output_dir / f"{stem}.report.json"
        mapping_path = request.output_dir / f"{stem}.mapping.enc"
        normalized_path = request.output_dir / f"{stem}.normalized.md"
        self._atomic_write_text(masked_path, result.masked_text)
        reversible = bool(password)
        if reversible:
            if not isinstance(password, str):
                raise WorkflowError("invalid_password", "密码格式无效")
            self._atomic_write_bytes(mapping_path, result.vault.dumps(password))
        else:
            mapping_path.unlink(missing_ok=True)
            normalized_path.unlink(missing_ok=True)
        if request.retain_intermediate and reversible:
            self._atomic_write_text(normalized_path, result.normalized_text)
        report = {
            **result.report,
            "reversible": reversible,
            "mapping_encrypted": reversible,
        }
        self._atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        output_names = [masked_path.name, report_path.name]
        masked_artifact = self._register_output(job_id, item_id, "masked", masked_path, visible=True, retained=True)
        if reversible:
            self._register_output(job_id, item_id, "mapping", mapping_path, visible=True, retained=True)
        if request.retain_intermediate and reversible:
            self._register_output(job_id, item_id, "normalized", normalized_path, visible=True, retained=True)
            output_names.insert(0, normalized_path.name)
        self._register_output(job_id, item_id, "report", report_path, visible=True, retained=True)
        issue_count = 0
        self.store.complete_stage(stage["stage_id"], artifact_id=masked_artifact, safe_summary="文档脱敏完成")
        logger.write(event="stage_finished", item_id=item_id, stage="mask", status=StageStatus.SUCCEEDED.value, count=len(result.accepted_spans), duration_ms=round((time.perf_counter() - started) * 1000))
        self._emit(progress, job_id, item_id, "mask", "done", 0.85)
        if item_id:
            self.store.update_item(job_id, item_id, output_names=output_names)
        return masked_path, output_names, issue_count

    def _run_audit(
        self,
        job_id: str,
        item_id: str | None,
        masked_path: Path,
        *,
        progress: ProgressCallback | None,
        logger: JobLogger,
        stage_started: bool = False,
    ) -> int:
        stage = self.store.get_stage(job_id, item_id, "audit")
        if self._stage_valid(stage):
            return self._audit_count_for_scope(job_id, item_id)
        if not stage_started:
            self.store.start_stage(stage["stage_id"])
        text = masked_path.read_text(encoding="utf-8")
        issues = audit_masked_text(text)
        payload = {
            "issues": len(issues),
            "by_category": {
                category: sum(1 for issue in issues if issue.category == category)
                for category in sorted({issue.category for issue in issues})
            },
            "findings": [issue.to_dict() for issue in issues],
        }
        scope = item_id or job_id
        audit_path = Path(self.store.snapshot(job_id).output_dir) / f"document-{safe_id(scope)}.audit.json"
        self._atomic_write_text(audit_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._register_output(job_id, item_id, "audit", audit_path, visible=True, retained=True)
        if item_id:
            self.store.update_item(job_id, item_id, audit_issue_count=len(issues))
        self.store.complete_stage(stage["stage_id"], artifact_id=self._artifact_for_path(job_id, str(audit_path)), safe_summary=f"审计完成，发现 {len(issues)} 项需关注")
        logger.write(event="audit_finished", item_id=item_id, stage="audit", count=len(issues))
        return len(issues)

    def _run_restore(
        self,
        job_id: str,
        *,
        password: str | None,
        progress: ProgressCallback | None,
        logger: JobLogger,
        checkpoint: CheckpointStore,
    ) -> None:
        stage = self.store.get_stage(job_id, None, "restore")
        if not password:
            self.store.wait_stage_for_secret(stage["stage_id"])
            self.store.set_job_status(job_id, JobStatus.WAITING_SECRET, error_code="secret_required", error_summary="恢复需要重新提供密码", clear_lease=True)
            logger.write(event="waiting_secret", status=JobStatus.WAITING_SECRET.value, error_code="secret_required")
            return
        if self._stage_valid(stage):
            self.store.set_job_status(job_id, JobStatus.SUCCEEDED, clear_lease=True)
            return
        self.store.start_stage(stage["stage_id"])
        request = self._request_from_job(job_id)
        try:
            mapping = Path(request["mapping_path"])
            masked = Path(request["masked_path"])
            vault = MappingVault.load(mapping, password)
            restored = restore_text(masked.read_text(encoding="utf-8-sig"), vault)
        except Exception as error:
            code, summary = self._safe_error(error)
            self.store.fail_stage(stage["stage_id"], code, summary)
            raise WorkflowError(code, summary) from None
        output_dir = Path(self.store.snapshot(job_id).output_dir)
        output_name = f"document-{safe_id(job_id)}.restored.md"
        if request.get("restore_filename") and vault.source_name:
            output_name = Path(vault.source_name).name
        output_path = output_dir / output_name
        self._atomic_write_text(output_path, restored)
        artifact_id = self._register_output(job_id, None, "restored", output_path, visible=True, retained=True)
        self.store.complete_stage(stage["stage_id"], artifact_id=artifact_id, safe_summary="脱敏恢复完成")
        self.store.set_job_status(job_id, JobStatus.SUCCEEDED, clear_lease=True)
        logger.write(event="job_finished", status=JobStatus.SUCCEEDED.value, size_bytes=output_path.stat().st_size)
        self._emit(progress, job_id, None, "restore", "done", 1.0)

    def _merged_text(self, job_id: str, checkpoint: CheckpointStore) -> str:
        chunks: list[str] = [f"# document-project-{safe_id(job_id)}", ""]
        for row in self.store.get_items(job_id):
            if row["status"] != "succeeded":
                continue
            text = self._item_text(job_id, row["item_id"])
            chunks.extend([f"# ---- document-{safe_id(row['item_id'])} ----", "", text.strip(), ""])
        if len(chunks) <= 2:
            raise WorkflowError("no_successful_items", "没有可合并的成功文件")
        return "\n".join(chunks)

    def _merged_text_from_stage(self, job_id: str, checkpoint: CheckpointStore) -> str:
        stage = self.store.get_stage(job_id, None, "merge")
        if not self._stage_valid(stage):
            raise WorkflowError("missing_checkpoint", "合并阶段产物缺失，无法继续脱敏")
        return self._artifact_text(stage["artifact_id"])

    def _item_text(self, job_id: str, item_id: str) -> str:
        stage_name = "organize" if self._has_stage(job_id, item_id, "organize") else "ocr" if self._has_stage(job_id, item_id, "ocr") else "input"
        stage = self.store.get_stage(job_id, item_id, stage_name)
        if not self._stage_valid(stage):
            raise WorkflowError("missing_checkpoint", "文件阶段产物缺失，无法继续")
        return self._artifact_text(stage["artifact_id"])

    def _artifact_text(self, artifact_id: str | None) -> str:
        if not artifact_id:
            raise WorkflowError("missing_artifact", "阶段产物登记缺失")
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None or not Path(artifact["path"]).is_file():
            raise WorkflowError("missing_artifact", "阶段产物不存在")
        return Path(artifact["path"]).read_text(encoding="utf-8")

    def _stage_valid(self, stage: Any, *, password: str | None = None) -> bool:
        if stage["status"] != StageStatus.SUCCEEDED.value:
            return False
        artifact = self.store.get_artifact(stage["artifact_id"]) if stage["artifact_id"] else None
        if artifact is None or not CheckpointStore(self.checkpoint_root, stage["job_id"]).valid(artifact["path"], artifact["sha256"], artifact["size_bytes"]):
            return False
        if stage["stage_name"] == "mask" and password:
            rows = [row for row in self.store.get_artifacts(stage["job_id"]) if row["item_id"] == (stage["item_id"] or None) and row["kind"] == "mapping"]
            if not rows or not any(Path(row["path"]).is_file() for row in rows):
                return False
        return True

    def _has_stage(self, job_id: str, item_id: str, stage_name: str) -> bool:
        try:
            self.store.get_stage(job_id, item_id, stage_name)
            return True
        except JobNotFoundError:
            return False

    def _current_running_stage(self, job_id: str) -> str | None:
        rows = self.store.get_stages(job_id)
        for row in rows:
            if row["status"] == StageStatus.RUNNING.value:
                return row["stage_id"]
        return None

    def _fail_item(self, job_id: str, item_id: str, error: WorkflowError, logger: JobLogger) -> None:
        error_recorded = False
        for stage in self.store.get_stages(job_id):
            if stage["item_id"] == item_id and stage["status"] in {
                StageStatus.PENDING.value,
                StageStatus.RUNNING.value,
                StageStatus.INTERRUPTED.value,
            }:
                if stage["status"] in {StageStatus.RUNNING.value, StageStatus.INTERRUPTED.value} and not error_recorded:
                    self.store.fail_stage(stage["stage_id"], error.code, error.summary)
                    error_recorded = True
                else:
                    self.store.skip_stage(stage["stage_id"], error.summary)
        self.store.update_item(job_id, item_id, status="failed")
        logger.write(event="item_failed", item_id=item_id, status="failed", error_code=error.code, summary=error.summary)
        self._emit(None, job_id, item_id, "item", "error", 1.0, message=error.summary)

    def _skip_job_stages(self, job_id: str, summary: str) -> None:
        for stage in self.store.get_stages(job_id):
            if not stage["item_id"] and stage["status"] == StageStatus.PENDING.value:
                self.store.skip_stage(stage["stage_id"], summary)

    def _attention_count(self, job_id: str) -> int:
        rows = self.store.get_items(job_id)
        count = sum(int(row["audit_issue_count"] or 0) for row in rows)
        for artifact in self.store.get_artifacts(job_id):
            if artifact["item_id"] is None and artifact["kind"] == "audit" and Path(artifact["path"]).is_file():
                try:
                    count += int(json.loads(Path(artifact["path"]).read_text(encoding="utf-8")).get("issues", 0))
                except (OSError, ValueError, TypeError):
                    continue
        return count

    def _audit_count_for_scope(self, job_id: str, item_id: str | None) -> int:
        if item_id:
            rows = self.store.get_items(job_id)
            return next((int(row["audit_issue_count"] or 0) for row in rows if row["item_id"] == item_id), 0)
        for row in self.store.get_artifacts(job_id):
            if row["item_id"] is None and row["kind"] == "audit" and Path(row["path"]).is_file():
                try:
                    return int(json.loads(Path(row["path"]).read_text(encoding="utf-8")).get("issues", 0))
                except (OSError, ValueError, TypeError):
                    return 0
        return 0

    def _masked_path(self, job_id: str) -> Path:
        return self._artifact_path_for_kind(job_id, None, "masked")

    def _artifact_path_for_kind(self, job_id: str, item_id: str | None, kind: str) -> Path:
        rows = self.store.get_artifacts(job_id)
        matches = [row for row in rows if row["item_id"] == item_id and row["kind"] == kind]
        if not matches:
            raise WorkflowError("missing_artifact", "输出产物登记缺失")
        path = Path(matches[-1]["path"])
        if not path.is_file():
            raise WorkflowError("missing_artifact", "输出产物不存在")
        return path

    def _artifact_for_path(self, job_id: str, path: str) -> str:
        for row in self.store.get_artifacts(job_id):
            if row["path"] == path:
                return row["artifact_id"]
        raise WorkflowError("missing_artifact", "输出产物登记缺失")

    def _output_names_for_scope(self, job_id: str, item_id: str | None) -> list[str]:
        return [Path(row["path"]).name for row in self.store.get_artifacts(job_id) if row["item_id"] == item_id and row["visible"]]

    def _update_job_output_names(self, job_id: str, _names: list[str]) -> None:
        # Job-level names are represented by visible artifact references in the
        # snapshot; no duplicate JSON field is required in the schema.
        return

    def _register_output(self, job_id: str, item_id: str | None, kind: str, path: Path, *, visible: bool, retained: bool) -> str:
        return self.store.add_artifact(
            job_id=job_id,
            item_id=item_id,
            kind=kind,
            path=str(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            visible=visible,
            retained=retained,
        )

    def _configured_engine(self, request: ProcessRequest) -> Desensitizer:
        config = load_desensitize_config(request.config_path)
        if request.entities:
            selected = {str(value).upper() for value in request.entities}
            entities = dict(config.entities)
            for entity_type in _KNOWN_ENTITY_TYPES:
                current = entities.get(entity_type) or EntityConfig()
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
            config = replace(config, entities=entities, custom=custom_rules)
        if not request.ai_enhanced:
            config = replace(config, model={**config.model, "enabled": False})
        return Desensitizer(config)

    def _ocr_text(self, source: Path, request: ProcessRequest) -> str:
        try:
            from ocr.config import load_config as load_ocr_config
            from ocr.converter import DocumentConverter
        except ImportError as error:
            raise WorkflowError("dependency_missing", "OCR 依赖未安装，请安装 Wenveil 的 OCR 可选依赖") from error
        config_path = Path(__file__).resolve().parents[1] / "config" / "ocr.yaml"
        config = load_ocr_config(str(config_path))
        if request.ocr_mode == "fast":
            config = replace(config, docling=replace(config.docling, table_mode="fast"))
        if request.device == "cpu":
            config = replace(config, ocr=replace(config.ocr, use_gpu=False, use_dml=False))
        elif request.device == "gpu":
            config = replace(config, ocr=replace(config.ocr, use_gpu=True, use_dml=False))
        return DocumentConverter(config).convert(source) or ""

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return path.read_text(encoding="latin-1")

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        WorkflowRunner._atomic_write_bytes(path, text.encode("utf-8"))

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _item_record(item: InputRef, ordinal: int) -> dict[str, Any]:
        path = item.resolved()
        if not path.is_file():
            raise WorkflowError("input_missing", "输入文件不存在或不可读取")
        return {
            "item_id": item.item_id or f"item-{uuid.uuid4().hex}",
            "ordinal": ordinal,
            "source_path": str(path),
            "source_path_hash": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "source_sha256": sha256_file(path),
        }

    @staticmethod
    def _stage_definitions(steps: WorkflowSteps, inputs: list[InputRef]) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        source_name = "ocr" if steps.ocr else "input"
        for item in inputs:
            item_id = item.item_id
            definitions.append({"item_id": item_id, "stage_name": source_name})
            if steps.organize:
                definitions.append({"item_id": item_id, "stage_name": "organize"})
            if not steps.merge and steps.mask:
                definitions.append({"item_id": item_id, "stage_name": "mask"})
            if not steps.merge and steps.audit:
                definitions.append({"item_id": item_id, "stage_name": "audit"})
        if steps.merge:
            definitions.append({"item_id": None, "stage_name": "merge"})
            if steps.mask:
                definitions.append({"item_id": None, "stage_name": "mask"})
            if steps.audit:
                definitions.append({"item_id": None, "stage_name": "audit"})
        return definitions

    def _expand_inputs(self, request: ProcessRequest) -> list[InputRef]:
        expanded: list[InputRef] = []
        for raw in request.inputs:
            item = raw if isinstance(raw, InputRef) else InputRef(Path(raw))
            path = item.resolved()
            if path.is_dir():
                for child in sorted(path.rglob("*"), key=lambda candidate: str(candidate).lower()):
                    if child.is_file() and (request.steps.ocr and child.suffix.lower() in _OCR_EXTENSIONS or not request.steps.ocr and child.suffix.lower() in _TEXT_EXTENSIONS):
                        expanded.append(InputRef(child))
            elif path.is_file():
                if not request.steps.ocr and path.suffix.lower() not in _TEXT_EXTENSIONS:
                    raise WorkflowError("unsupported_input", "未开启文字识别时，只能处理 Markdown、TXT 或 RTF 文件")
                if request.steps.ocr and path.suffix.lower() not in _OCR_EXTENSIONS:
                    raise WorkflowError("unsupported_input", "输入文件类型不受支持")
                expanded.append(InputRef(path, item_id=item.item_id, display_name=item.display_name))
            else:
                raise WorkflowError("input_missing", "输入文件不存在或不可读取")
        if not expanded:
            raise WorkflowError("input_empty", "请至少选择一个输入文件")
        seen: set[str] = set()
        normalized: list[InputRef] = []
        for item in expanded:
            path = str(item.resolved())
            item_id = item.item_id or f"item-{uuid.uuid4().hex}"
            if item_id in seen:
                raise WorkflowError("duplicate_item", "输入文件标识重复")
            seen.add(item_id)
            normalized.append(InputRef(Path(path), item_id=item_id, display_name=item.display_name))
        return normalized

    def _validate_process_request(self, request: ProcessRequest) -> ProcessRequest:
        if not isinstance(request, ProcessRequest):
            raise TypeError("request must be ProcessRequest")
        if not any(request.steps.as_dict().values()):
            raise WorkflowError("invalid_steps", "请至少开启一个处理步骤")
        if request.steps.audit and not request.steps.mask:
            raise WorkflowError("invalid_steps", "脱敏检查需要同时开启文档脱敏")
        if request.ocr_mode not in {"auto", "fast", "enhanced"}:
            raise WorkflowError("invalid_ocr_mode", "OCR 模式无效")
        if request.device not in {"auto", "cpu", "gpu"}:
            raise WorkflowError("invalid_device", "设备模式无效")
        if request.output_name is not None:
            output_name = request.output_name
            output_path = Path(output_name)
            if (
                not output_name
                or output_path.is_absolute()
                or output_path.name != output_name
                or output_name in {".", ".."}
            ):
                raise WorkflowError("invalid_output_name", "合并输出文件名必须是安全的单层文件名")
        output_dir = request.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return replace(request, output_dir=output_dir)

    @staticmethod
    def _request_json(request: ProcessRequest) -> dict[str, Any]:
        return {
            "steps": request.steps.as_dict(),
            "output_name": request.output_name,
            "config_path": str(request.config_path.expanduser().resolve()) if request.config_path else None,
            "entities": list(request.entities),
            "ai_enhanced": request.ai_enhanced,
            "ocr_mode": request.ocr_mode,
            "device": request.device,
            "retain_intermediate": request.retain_intermediate,
            "allow_partial": request.allow_partial,
        }

    @staticmethod
    def _config_hash(config_path: Path | None) -> str | None:
        if not config_path:
            return None
        path = config_path.expanduser().resolve()
        if not path.is_file():
            raise WorkflowError("config_missing", "配置文件不存在")
        return sha256_file(path)

    def _request_from_job(self, job_id: str) -> Any:
        row = self.store.get_job_row(job_id)
        request = json.loads(row["request_json"])
        if row["operation"] == "restore":
            return request
        steps = WorkflowSteps(**json.loads(row["steps_json"]))
        inputs = tuple(
            InputRef(Path(item["source_path"]), item_id=item["item_id"])
            for item in self.store.get_items(job_id)
        )
        return ProcessRequest(
            inputs=inputs,
            output_dir=Path(row["output_dir"]),
            steps=steps,
            output_name=request.get("output_name"),
            config_path=Path(request["config_path"]) if request.get("config_path") else None,
            entities=tuple(request.get("entities", [])),
            ai_enhanced=bool(request.get("ai_enhanced", True)),
            ocr_mode=request.get("ocr_mode", "auto"),
            device=request.get("device", "auto"),
            retain_intermediate=bool(request.get("retain_intermediate", False)),
            allow_partial=bool(request.get("allow_partial", False)),
        )

    @staticmethod
    def _safe_error(error: Exception) -> tuple[str, str]:
        if isinstance(error, FileNotFoundError):
            return "input_missing", "输入文件、映射文件或配置不存在"
        if isinstance(error, PermissionError):
            return "permission_denied", "文件或输出位置没有访问权限"
        if isinstance(error, ImportError):
            return "dependency_missing", "OCR 依赖未安装，请安装 Wenveil 的 OCR 可选依赖"
        if isinstance(error, (ValueError, TypeError)):
            return "invalid_input", "输入参数或文件内容无效，请检查后重试"
        return "processing_failed", "本地处理失败，请检查输入文件、密码和输出位置"

    @staticmethod
    def _emit(
        progress: ProgressCallback | None,
        job_id: str,
        item_id: str | None,
        stage: str,
        status: str,
        value: float,
        **extra: Any,
    ) -> None:
        if progress is None:
            return
        event: dict[str, Any] = {
            "jobId": job_id,
            "itemId": item_id,
            "stage": stage,
            "status": status,
            "progress": max(0.0, min(1.0, value)),
        }
        event.update(extra)
        try:
            progress(event)
        except Exception:
            # A UI/transport callback must not corrupt the local job.
            return


__all__ = ["WorkflowError", "WorkflowRunner"]
