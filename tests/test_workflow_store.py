from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from workflow.checkpoints import CheckpointStore
from workflow.models import JobStatus
from workflow.store import WorkflowStore


def test_store_applies_migration_and_cancel_is_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "workflow.sqlite3"
    with WorkflowStore(db_path) as store:
        store.create_job(
            job_id="job-fixture",
            operation="process",
            config_hash=None,
            steps_json=json.dumps({"ocr": False}),
            request_json=json.dumps({"steps": {"ocr": False}}),
            output_dir=str(tmp_path / "output"),
            log_path=str(tmp_path / "job.log"),
            items=[],
            stages=[{"stage_id": "stage-fixture", "stage_name": "merge"}],
        )
        assert store.snapshot("job-fixture").status == JobStatus.QUEUED
        store.request_cancel("job-fixture")
        assert store.snapshot("job-fixture").status == JobStatus.CANCEL_REQUESTED

    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (2,)
    assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'workflow_artifacts'").fetchone() == (
        "workflow_artifacts",
    )


def test_checkpoint_hash_detects_tampering_and_cleanup_is_scoped(tmp_path: Path) -> None:
    checkpoints = CheckpointStore(tmp_path / "checkpoints", "job-fixture")
    path, digest, size = checkpoints.write_text("item-fixture", "organized", "safe test text")
    assert checkpoints.valid(path, digest, size)
    path.write_text("tampered", encoding="utf-8")
    assert not checkpoints.valid(path, digest, size)
    checkpoints.cleanup()
    assert not path.exists()
