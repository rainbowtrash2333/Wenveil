"""Public request and status models for the unified workflow service.

The models in this module deliberately contain metadata only.  Text, mapping
contents and passwords are runtime values and must never be serialized into a
workflow snapshot or persisted by the state store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ATTENTION = "attention"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    WAITING_SECRET = "waiting_secret"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"
    WAITING_SECRET = "waiting_secret"


OcrMode = Literal["auto", "fast", "enhanced"]
DeviceMode = Literal["auto", "cpu", "gpu"]
ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class InputRef:
    """An input path with an optional caller-owned stable ID.

    ``display_name`` is kept in memory for adapters that already know the
    user's selected filename.  It is not written to the database or logs.
    """

    path: Path
    item_id: str | None = None
    display_name: str = ""

    def resolved(self) -> Path:
        return self.path.expanduser().resolve()


@dataclass(frozen=True, slots=True)
class WorkflowSteps:
    ocr: bool = True
    organize: bool = True
    merge: bool = True
    mask: bool = True
    audit: bool = True

    def as_dict(self) -> dict[str, bool]:
        return {
            "ocr": self.ocr,
            "organize": self.organize,
            "merge": self.merge,
            "mask": self.mask,
            "audit": self.audit,
        }


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    inputs: tuple[Path | InputRef, ...]
    output_dir: Path
    steps: WorkflowSteps = field(default_factory=WorkflowSteps)
    config_path: Path | None = None
    entities: tuple[str, ...] = ()
    ai_enhanced: bool = True
    ocr_mode: OcrMode = "auto"
    device: DeviceMode = "auto"
    retain_intermediate: bool = False
    allow_partial: bool = False
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    masked_path: Path
    mapping_path: Path
    output_dir: Path
    password: str | None = None
    restore_filename: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    job_id: str
    item_id: str | None
    kind: str
    path: str
    sha256: str
    size_bytes: int
    visible: bool
    retained: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifactId": self.artifact_id,
            "jobId": self.job_id,
            "itemId": self.item_id,
            "kind": self.kind,
            "path": self.path,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "visible": self.visible,
            "retained": self.retained,
        }


@dataclass(frozen=True, slots=True)
class StageSnapshot:
    stage_id: str
    job_id: str
    item_id: str | None
    name: str
    status: StageStatus
    progress: float
    attempt: int
    artifact_id: str | None = None
    error_code: str | None = None
    error_summary: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stageId": self.stage_id,
            "jobId": self.job_id,
            "itemId": self.item_id,
            "name": self.name,
            "status": self.status.value,
            "progress": self.progress,
            "attempt": self.attempt,
            "artifactId": self.artifact_id,
            "errorCode": self.error_code,
            "errorSummary": self.error_summary,
        }


@dataclass(frozen=True, slots=True)
class ItemSnapshot:
    item_id: str
    job_id: str
    ordinal: int
    source_path: str
    extension: str
    size_bytes: int
    source_path_hash: str
    source_sha256: str | None
    status: str
    output_names: tuple[str, ...] = ()
    audit_issue_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "jobId": self.job_id,
            "ordinal": self.ordinal,
            "sourcePath": self.source_path,
            "extension": self.extension,
            "sizeBytes": self.size_bytes,
            "sourcePathHash": self.source_path_hash,
            "sourceSha256": self.source_sha256,
            "status": self.status,
            "outputNames": list(self.output_names),
            "auditIssueCount": self.audit_issue_count,
        }


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    operation: str
    status: JobStatus
    workflow_version: str
    config_hash: str | None
    output_dir: str
    log_path: str
    progress: float
    resume_count: int
    last_error_code: str | None
    last_error_summary: str | None
    attention_count: int
    items: tuple[ItemSnapshot, ...] = ()
    stages: tuple[StageSnapshot, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    created_at: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "operation": self.operation,
            "status": self.status.value,
            "workflowVersion": self.workflow_version,
            "configHash": self.config_hash,
            "outputDir": self.output_dir,
            "logPath": self.log_path,
            "progress": self.progress,
            "resumeCount": self.resume_count,
            "lastErrorCode": self.last_error_code,
            "lastErrorSummary": self.last_error_summary,
            "attentionCount": self.attention_count,
            "items": [item.as_dict() for item in self.items],
            "stages": [stage.as_dict() for stage in self.stages],
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "finishedAt": self.finished_at,
        }


__all__ = [
    "ArtifactRef",
    "DeviceMode",
    "InputRef",
    "ItemSnapshot",
    "JobSnapshot",
    "JobStatus",
    "OcrMode",
    "ProgressCallback",
    "ProcessRequest",
    "RestoreRequest",
    "StageSnapshot",
    "StageStatus",
    "WorkflowSteps",
]
