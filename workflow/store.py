"""SQLite repository for safe workflow metadata.

The repository intentionally has no methods that accept document text.  It
stores paths and hashes for local recovery, while checkpoint contents remain
outside SQLite in the private workflow workspace.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ArtifactRef,
    ItemSnapshot,
    JobSnapshot,
    JobStatus,
    StageSnapshot,
    StageStatus,
)


WORKFLOW_VERSION = "1"
_MIGRATION_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class JobNotFoundError(KeyError):
    """Raised when a requested job does not exist."""


class JobBusyError(RuntimeError):
    """Raised when a live execution lease already belongs to another owner."""


class WorkflowStore:
    """Thread-safe SQLite repository with small transaction-oriented methods."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "WorkflowStore":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        migration_dir = Path(__file__).with_name("migrations")
        migrations = {
            1: migration_dir.joinpath("001_initial.sql"),
            2: migration_dir.joinpath("002_scope_items_to_job.sql"),
        }
        with self._connection:
            # The migration table itself is created by the first migration.
            applied = {
                row[0]
                for row in self._connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            } if self._table_exists("schema_migrations") else set()
            for version, migration_path in sorted(migrations.items()):
                if version in applied:
                    continue
                self._connection.executescript(migration_path.read_text(encoding="utf-8"))
                self._connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _now()),
                )

    def _table_exists(self, name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def create_job(
        self,
        *,
        job_id: str,
        operation: str,
        config_hash: str | None,
        steps_json: str,
        request_json: str,
        output_dir: str,
        log_path: str,
        items: Iterable[dict[str, Any]],
        stages: Iterable[dict[str, Any]],
    ) -> None:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO workflow_jobs(
                    job_id, operation, status, workflow_version, config_hash,
                    steps_json, request_json, output_dir, log_path, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    operation,
                    JobStatus.QUEUED.value,
                    WORKFLOW_VERSION,
                    config_hash,
                    steps_json,
                    request_json,
                    output_dir,
                    log_path,
                    now,
                    now,
                ),
            )
            for item in items:
                self._connection.execute(
                    """INSERT INTO workflow_items(
                        item_id, job_id, ordinal, source_path, source_path_hash,
                        extension, size_bytes, source_sha256, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["item_id"],
                        job_id,
                        item["ordinal"],
                        item["source_path"],
                        item["source_path_hash"],
                        item["extension"],
                        item["size_bytes"],
                        item.get("source_sha256"),
                        item.get("status", "queued"),
                    ),
                )
            for stage in stages:
                self._connection.execute(
                    """INSERT INTO workflow_stages(
                        stage_id, job_id, item_id, stage_name, status
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        stage["stage_id"],
                        job_id,
                        stage.get("item_id") or "",
                        stage["stage_name"],
                        stage.get("status", StageStatus.PENDING.value),
                    ),
                )
            self._append_event_locked(
                job_id,
                None,
                None,
                "job_created",
                JobStatus.QUEUED.value,
                0.0,
                "作业已创建",
            )

    def get_job_row(self, job_id: str) -> sqlite3.Row:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workflow_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return row

    def get_items(self, job_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    "SELECT * FROM workflow_items WHERE job_id = ? ORDER BY ordinal",
                    (job_id,),
                ).fetchall()
            )

    def get_stages(self, job_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """SELECT * FROM workflow_stages
                       WHERE job_id = ?
                       ORDER BY CASE WHEN item_id = '' THEN 1 ELSE 0 END, item_id, stage_name""",
                    (job_id,),
                ).fetchall()
            )

    def get_stage(self, job_id: str, item_id: str | None, stage_name: str) -> sqlite3.Row:
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM workflow_stages
                   WHERE job_id = ? AND item_id = ? AND stage_name = ?""",
                (job_id, item_id or "", stage_name),
            ).fetchone()
        if row is None:
            raise JobNotFoundError(f"stage:{job_id}:{item_id}:{stage_name}")
        return row

    def get_artifacts(self, job_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """SELECT * FROM workflow_artifacts
                       WHERE job_id = ? ORDER BY created_at, artifact_id""",
                    (job_id,),
                ).fetchall()
            )

    def get_artifact(self, artifact_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()

    def claim_job(self, job_id: str, owner: str, *, resuming: bool = False) -> None:
        now = _now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status, lease_owner FROM workflow_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            status = str(row["status"])
            if status == JobStatus.RUNNING.value and row["lease_owner"] not in {None, owner}:
                raise JobBusyError(job_id)
            if status in {
                JobStatus.SUCCEEDED.value,
                JobStatus.ATTENTION.value,
                JobStatus.CANCELLED.value,
            }:
                return
            if status not in {
                JobStatus.QUEUED.value,
                JobStatus.RUNNING.value,
                JobStatus.INTERRUPTED.value,
                JobStatus.WAITING_SECRET.value,
                JobStatus.PARTIAL_FAILED.value,
                JobStatus.FAILED.value,
            }:
                raise JobBusyError(job_id)
            self._connection.execute(
                """UPDATE workflow_jobs SET status = ?, started_at = COALESCE(started_at, ?),
                   updated_at = ?, heartbeat_at = ?, lease_owner = ?, cancel_requested = 0,
                   resume_count = resume_count + ? WHERE job_id = ?""",
                (
                    JobStatus.RUNNING.value,
                    now,
                    now,
                    now,
                    owner,
                    1 if resuming else 0,
                    job_id,
                ),
            )
            self._append_event_locked(
                job_id,
                None,
                None,
                "job_claimed",
                JobStatus.RUNNING.value,
                self._progress_locked(job_id),
                "作业开始执行",
            )

    def heartbeat(self, job_id: str, owner: str | None = None) -> None:
        with self._lock, self._connection:
            if owner is None:
                self._connection.execute(
                    "UPDATE workflow_jobs SET updated_at = ?, heartbeat_at = ? WHERE job_id = ?",
                    (_now(), _now(), job_id),
                )
            else:
                self._connection.execute(
                    """UPDATE workflow_jobs SET updated_at = ?, heartbeat_at = ?
                       WHERE job_id = ? AND (lease_owner = ? OR lease_owner IS NULL)""",
                    (_now(), _now(), job_id, owner),
                )

    def start_stage(self, stage_id: str) -> sqlite3.Row:
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE workflow_stages SET status = ?, progress = 0, attempt = attempt + 1,
                   started_at = ?, finished_at = NULL, heartbeat_at = ?, artifact_id = NULL,
                   error_code = NULL, error_summary = NULL WHERE stage_id = ?""",
                (StageStatus.RUNNING.value, now, now, stage_id),
            )
            row = self._connection.execute(
                "SELECT * FROM workflow_stages WHERE stage_id = ?",
                (stage_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(stage_id)
            self._append_event_locked(
                row["job_id"],
                row["item_id"] or None,
                stage_id,
                "stage_started",
                StageStatus.RUNNING.value,
                0.0,
                "阶段开始",
            )
            return row

    def update_stage_progress(self, stage_id: str, progress: float, safe_summary: str = "") -> None:
        progress = max(0.0, min(1.0, float(progress)))
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE workflow_stages SET progress = ?, heartbeat_at = ? WHERE stage_id = ?",
                (progress, now, stage_id),
            )
            row = self._connection.execute(
                "SELECT * FROM workflow_stages WHERE stage_id = ?",
                (stage_id,),
            ).fetchone()
            if row:
                self._connection.execute(
                    "UPDATE workflow_jobs SET updated_at = ?, heartbeat_at = ? WHERE job_id = ?",
                    (now, now, row["job_id"]),
                )
                self._append_event_locked(
                    row["job_id"],
                    row["item_id"] or None,
                    stage_id,
                    "stage_progress",
                    row["status"],
                    progress,
                    safe_summary or None,
                )

    def add_artifact(
        self,
        *,
        job_id: str,
        item_id: str | None,
        kind: str,
        path: str,
        sha256: str,
        size_bytes: int,
        visible: bool,
        retained: bool,
    ) -> str:
        artifact_id = _new_id("artifact")
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO workflow_artifacts(
                    artifact_id, job_id, item_id, kind, path, sha256, size_bytes, visible, retained, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id,
                    job_id,
                    item_id,
                    kind,
                    path,
                    sha256,
                    size_bytes,
                    int(visible),
                    int(retained),
                    _now(),
                ),
            )
        return artifact_id

    def complete_stage(
        self,
        stage_id: str,
        *,
        artifact_id: str | None = None,
        safe_summary: str = "阶段完成",
    ) -> None:
        now = _now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM workflow_stages WHERE stage_id = ?",
                (stage_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(stage_id)
            self._connection.execute(
                """UPDATE workflow_stages SET status = ?, progress = 1, finished_at = ?,
                   heartbeat_at = ?, artifact_id = ?, error_code = NULL, error_summary = NULL
                   WHERE stage_id = ?""",
                (StageStatus.SUCCEEDED.value, now, now, artifact_id, stage_id),
            )
            self._connection.execute(
                "UPDATE workflow_jobs SET updated_at = ?, heartbeat_at = ? WHERE job_id = ?",
                (now, now, row["job_id"]),
            )
            self._append_event_locked(
                row["job_id"],
                row["item_id"] or None,
                stage_id,
                "stage_succeeded",
                StageStatus.SUCCEEDED.value,
                1.0,
                safe_summary,
            )

    def fail_stage(self, stage_id: str, error_code: str, error_summary: str) -> None:
        self._finish_stage(stage_id, StageStatus.FAILED, error_code, error_summary)

    def wait_stage_for_secret(self, stage_id: str, safe_summary: str = "等待重新提供密码") -> None:
        self._finish_stage(stage_id, StageStatus.WAITING_SECRET, "secret_required", safe_summary)

    def skip_stage(self, stage_id: str, safe_summary: str = "因上游失败跳过") -> None:
        self._finish_stage(stage_id, StageStatus.SKIPPED, None, safe_summary)

    def _finish_stage(
        self,
        stage_id: str,
        status: StageStatus,
        error_code: str | None,
        error_summary: str,
    ) -> None:
        now = _now()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM workflow_stages WHERE stage_id = ?",
                (stage_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(stage_id)
            self._connection.execute(
                """UPDATE workflow_stages SET status = ?, progress = ?, finished_at = ?,
                   heartbeat_at = ?, error_code = ?, error_summary = ? WHERE stage_id = ?""",
                (
                    status.value,
                    1.0 if status == StageStatus.SKIPPED else row["progress"],
                    now,
                    now,
                    error_code,
                    error_summary,
                    stage_id,
                ),
            )
            self._append_event_locked(
                row["job_id"],
                row["item_id"] or None,
                stage_id,
                "stage_finished",
                status.value,
                1.0 if status == StageStatus.SKIPPED else row["progress"],
                error_summary,
            )

    def update_item(
        self,
        job_id: str,
        item_id: str,
        *,
        status: str | None = None,
        output_names: Iterable[str] | None = None,
        audit_issue_count: int | None = None,
        source_sha256: str | None = None,
    ) -> None:
        updates: list[str] = []
        values: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if output_names is not None:
            updates.append("output_names_json = ?")
            values.append(json.dumps(list(output_names), ensure_ascii=False))
        if audit_issue_count is not None:
            updates.append("audit_issue_count = ?")
            values.append(int(audit_issue_count))
        if source_sha256 is not None:
            updates.append("source_sha256 = ?")
            values.append(source_sha256)
        if not updates:
            return
        values.extend([job_id, item_id])
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE workflow_items SET {', '.join(updates)} WHERE job_id = ? AND item_id = ?",
                values,
            )

    def set_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
        attention_count: int | None = None,
        clear_lease: bool = False,
    ) -> None:
        now = _now()
        finished = now if status in {
            JobStatus.SUCCEEDED,
            JobStatus.ATTENTION,
            JobStatus.PARTIAL_FAILED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        } else None
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status.value, now]
        if finished is not None:
            assignments.append("finished_at = ?")
            values.append(finished)
        if error_code is not None or error_summary is not None:
            assignments.extend(["last_error_code = ?", "last_error_summary = ?"])
            values.extend([error_code, error_summary])
        if attention_count is not None:
            assignments.append("attention_count = ?")
            values.append(int(attention_count))
        if clear_lease:
            assignments.extend(["lease_owner = NULL", "heartbeat_at = NULL"])
        values.append(job_id)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE workflow_jobs SET {', '.join(assignments)} WHERE job_id = ?",
                values,
            )
            self._append_event_locked(
                job_id,
                None,
                None,
                "job_finished" if finished else "job_status",
                status.value,
                self._progress_locked(job_id),
                error_summary or status.value,
            )

    def request_cancel(self, job_id: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM workflow_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            status = str(row["status"])
            if status in {JobStatus.SUCCEEDED.value, JobStatus.ATTENTION.value, JobStatus.CANCELLED.value}:
                return
            self._connection.execute(
                "UPDATE workflow_jobs SET status = ?, cancel_requested = 1, updated_at = ? WHERE job_id = ?",
                (JobStatus.CANCEL_REQUESTED.value, _now(), job_id),
            )

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT cancel_requested FROM workflow_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def reconcile_stale_jobs(self, lease_seconds: int = 300) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - lease_seconds
        changed = 0
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT job_id, heartbeat_at FROM workflow_jobs WHERE status = ?",
                (JobStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                heartbeat = row["heartbeat_at"]
                try:
                    stale = datetime.fromisoformat(heartbeat).timestamp() < cutoff
                except (TypeError, ValueError):
                    stale = True
                if not stale:
                    continue
                self._connection.execute(
                    "UPDATE workflow_jobs SET status = ?, updated_at = ?, heartbeat_at = NULL, lease_owner = NULL WHERE job_id = ?",
                    (JobStatus.INTERRUPTED.value, _now(), row["job_id"]),
                )
                self._connection.execute(
                    """UPDATE workflow_stages SET status = ?, finished_at = ?, error_code = ?, error_summary = ?
                       WHERE job_id = ? AND status = ?""",
                    (
                        StageStatus.INTERRUPTED.value,
                        _now(),
                        "interrupted",
                        "执行进程中断，等待恢复",
                        row["job_id"],
                        StageStatus.RUNNING.value,
                    ),
                )
                self._append_event_locked(
                    row["job_id"],
                    None,
                    None,
                    "job_interrupted",
                    JobStatus.INTERRUPTED.value,
                    self._progress_locked(row["job_id"]),
                    "检测到过期执行租约",
                )
                changed += 1
        return changed

    def list_jobs(self, *, limit: int = 50, status: str | None = None) -> list[JobSnapshot]:
        limit = max(1, min(500, int(limit)))
        with self._lock:
            if status:
                rows = self._connection.execute(
                    "SELECT job_id FROM workflow_jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT job_id FROM workflow_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self.snapshot(row["job_id"]) for row in rows]

    def snapshot(self, job_id: str) -> JobSnapshot:
        with self._lock:
            job = self._connection.execute(
                "SELECT * FROM workflow_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise JobNotFoundError(job_id)
            items = self._connection.execute(
                "SELECT * FROM workflow_items WHERE job_id = ? ORDER BY ordinal",
                (job_id,),
            ).fetchall()
            stages = self._connection.execute(
                "SELECT * FROM workflow_stages WHERE job_id = ? ORDER BY stage_id",
                (job_id,),
            ).fetchall()
            artifacts = self._connection.execute(
                "SELECT * FROM workflow_artifacts WHERE job_id = ? ORDER BY created_at, artifact_id",
                (job_id,),
            ).fetchall()
            progress = self._progress_locked(job_id)

        item_models = tuple(
            ItemSnapshot(
                item_id=row["item_id"],
                job_id=row["job_id"],
                ordinal=row["ordinal"],
                source_path=row["source_path"],
                extension=row["extension"],
                size_bytes=row["size_bytes"],
                source_path_hash=row["source_path_hash"],
                source_sha256=row["source_sha256"],
                status=row["status"],
                output_names=tuple(json.loads(row["output_names_json"] or "[]")),
                audit_issue_count=row["audit_issue_count"],
            )
            for row in items
        )
        stage_models = tuple(
            StageSnapshot(
                stage_id=row["stage_id"],
                job_id=row["job_id"],
                item_id=row["item_id"] or None,
                name=row["stage_name"],
                status=StageStatus(row["status"]),
                progress=float(row["progress"]),
                attempt=row["attempt"],
                artifact_id=row["artifact_id"],
                error_code=row["error_code"],
                error_summary=row["error_summary"],
            )
            for row in stages
        )
        artifact_models = tuple(
            ArtifactRef(
                artifact_id=row["artifact_id"],
                job_id=row["job_id"],
                item_id=row["item_id"],
                kind=row["kind"],
                path=row["path"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                visible=bool(row["visible"]),
                retained=bool(row["retained"]),
            )
            for row in artifacts
        )
        return JobSnapshot(
            job_id=job["job_id"],
            operation=job["operation"],
            status=JobStatus(job["status"]),
            workflow_version=job["workflow_version"],
            config_hash=job["config_hash"],
            output_dir=job["output_dir"],
            log_path=job["log_path"],
            progress=progress,
            resume_count=job["resume_count"],
            last_error_code=job["last_error_code"],
            last_error_summary=job["last_error_summary"],
            attention_count=job["attention_count"],
            items=item_models,
            stages=stage_models,
            artifacts=artifact_models,
            created_at=job["created_at"],
            started_at=job["started_at"],
            updated_at=job["updated_at"],
            finished_at=job["finished_at"],
        )

    def _progress_locked(self, job_id: str) -> float:
        rows = self._connection.execute(
            "SELECT progress FROM workflow_stages WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        if not rows:
            return 0.0
        return round(sum(float(row["progress"]) for row in rows) / len(rows), 4)

    def _append_event_locked(
        self,
        job_id: str,
        item_id: str | None,
        stage_id: str | None,
        event_type: str,
        status: str | None,
        progress: float | None,
        safe_summary: str | None,
    ) -> None:
        sequence_row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM workflow_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        self._connection.execute(
            """INSERT INTO workflow_events(
                event_id, job_id, item_id, stage_id, sequence, event_type,
                status, progress, safe_summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _new_id("event"),
                job_id,
                item_id,
                stage_id,
                sequence_row["next_sequence"],
                event_type,
                status,
                progress,
                safe_summary,
                _now(),
            ),
        )


__all__ = ["JobBusyError", "JobNotFoundError", "WORKFLOW_VERSION", "WorkflowStore"]
