"""Stable public API for OCR, organization, merge, masking and restore."""

from __future__ import annotations

from pathlib import Path

from .checkpoints import default_state_dir
from .models import (
    JobSnapshot,
    ProcessRequest,
    ProgressCallback,
    RestoreRequest,
)
from .runner import WorkflowRunner
from .store import WorkflowStore


class WorkflowService:
    """The single application-facing entry point for document workflows."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        state_dir: str | Path | None = None,
        checkpoint_root: str | Path | None = None,
        lease_seconds: int = 300,
    ):
        resolved_state = Path(state_dir or default_state_dir()).expanduser().resolve()
        resolved_db = Path(db_path or resolved_state / "workflow.sqlite3").expanduser().resolve()
        self.store = WorkflowStore(resolved_db)
        self.runner = WorkflowRunner(
            self.store,
            state_dir=resolved_state,
            checkpoint_root=checkpoint_root,
            lease_seconds=lease_seconds,
        )

    @property
    def db_path(self) -> Path:
        return self.store.path

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "WorkflowService":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def create_process_job(self, request: ProcessRequest) -> JobSnapshot:
        return self.runner.create_process_job(request)

    def process(
        self,
        request: ProcessRequest,
        *,
        password: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> JobSnapshot:
        created = self.create_process_job(request)
        return self.runner.run(created.job_id, password=password, progress=progress)

    def restore(
        self,
        request: RestoreRequest,
        *,
        progress: ProgressCallback | None = None,
    ) -> JobSnapshot:
        created = self.runner.create_restore_job(request)
        return self.runner.run(created.job_id, password=request.password, progress=progress)

    def resume(
        self,
        job_id: str,
        *,
        password: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> JobSnapshot:
        return self.runner.run(job_id, password=password, progress=progress, resuming=True)

    def cancel(self, job_id: str) -> JobSnapshot:
        return self.runner.cancel(job_id)

    def get_status(self, job_id: str) -> JobSnapshot:
        return self.store.snapshot(job_id)

    def list_jobs(self, *, limit: int = 50, status: str | None = None) -> list[JobSnapshot]:
        return self.store.list_jobs(limit=limit, status=status)


__all__ = ["WorkflowService"]
