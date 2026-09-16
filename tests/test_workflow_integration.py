from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from desensitize.mapping import MappingVault, restore_text
from workflow import ProcessRequest, RestoreRequest, WorkflowService, WorkflowSteps


def _service(tmp_path: Path) -> WorkflowService:
    return WorkflowService(
        db_path=tmp_path / "state" / "workflow.sqlite3",
        state_dir=tmp_path / "state",
        checkpoint_root=tmp_path / "state" / "checkpoints",
    )


def test_workflow_persists_status_and_merges_without_raw_text(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.md"
    first.write_text("联系人：张三，电话：13800138000\n", encoding="utf-8")
    second.write_text("账号：6222020202020202\n", encoding="utf-8")
    output = tmp_path / "output"

    with _service(tmp_path) as service:
        snapshot = service.process(
            ProcessRequest(
                inputs=(first, second),
                output_dir=output,
                steps=WorkflowSteps(ocr=False, organize=True, merge=True, mask=True, audit=True),
                entities=("PERSON", "PHONE", "BANK_ACCOUNT"),
            ),
            password="fixture-password",
        )

        assert snapshot.status.value in {"succeeded", "attention"}
        assert snapshot.progress == 1.0
        assert any(artifact.kind == "masked" for artifact in snapshot.artifacts)
        assert any(artifact.kind == "mapping" for artifact in snapshot.artifacts)
        assert not list((tmp_path / "state" / "checkpoints").rglob("*.checkpoint"))
        db_text = "\n".join(
            "\t".join(str(value) for value in row)
            for row in sqlite3.connect(tmp_path / "state" / "workflow.sqlite3").execute(
                "SELECT job_id, request_json, last_error_summary FROM workflow_jobs"
            )
        )
        db_text += "\n" + "\n".join(
            "\t".join(str(value) for value in row)
            for row in sqlite3.connect(tmp_path / "state" / "workflow.sqlite3").execute(
                "SELECT item_id, source_path_hash, source_sha256, status FROM workflow_items"
            )
        )
        assert "13800138000" not in db_text
        assert "张三" not in db_text

    masked = next(artifact for artifact in snapshot.artifacts if artifact.kind == "masked")
    mapping = next(artifact for artifact in snapshot.artifacts if artifact.kind == "mapping")
    restored = restore_text(
        Path(masked.path).read_text(encoding="utf-8"),
        MappingVault.load(mapping.path, "fixture-password"),
    )
    assert "13800138000" in restored
    assert "6222020202020202" in restored


def test_workflow_mask_without_password_is_irreversible(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("电话：13800138000\n", encoding="utf-8")

    with _service(tmp_path) as service:
        snapshot = service.process(
            ProcessRequest(
                inputs=(source,),
                output_dir=tmp_path / "output",
                steps=WorkflowSteps(ocr=False, organize=True, merge=False, mask=True, audit=True),
                entities=("PHONE",),
            )
        )

    assert snapshot.status.value in {"succeeded", "attention"}
    assert not any(artifact.kind == "mapping" for artifact in snapshot.artifacts)
    masked = next(artifact for artifact in snapshot.artifacts if artifact.kind == "masked")
    assert "13800138000" not in Path(masked.path).read_text(encoding="utf-8")


def test_restore_waits_for_password_without_persisting_it(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("电话：13800138000\n", encoding="utf-8")
    output = tmp_path / "output"
    with _service(tmp_path) as service:
        processed = service.process(
            ProcessRequest(
                inputs=(source,),
                output_dir=output,
                steps=WorkflowSteps(ocr=False, organize=True, merge=False, mask=True, audit=False),
                entities=("PHONE",),
            ),
            password="fixture-password",
        )
        masked = next(artifact for artifact in processed.artifacts if artifact.kind == "masked")
        mapping = next(artifact for artifact in processed.artifacts if artifact.kind == "mapping")
        waiting = service.restore(
            RestoreRequest(Path(masked.path), Path(mapping.path), output, password=None)
        )
        assert waiting.status.value == "waiting_secret"
        resumed = service.resume(waiting.job_id, password="fixture-password")
        assert resumed.status.value == "succeeded"
        assert any(artifact.kind == "restored" for artifact in resumed.artifacts)
        assert "fixture-password" not in json.dumps(resumed.as_dict(), ensure_ascii=False)


def test_workflow_resume_reuses_successful_input_stage(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("普通文本\n", encoding="utf-8")
    with _service(tmp_path) as service:
        request = ProcessRequest(
            inputs=(source,),
            output_dir=tmp_path / "output",
            steps=WorkflowSteps(ocr=False, organize=True, merge=True, mask=False, audit=False),
        )
        created = service.create_process_job(request)
        original = service.runner._run_organize_stage
        calls = {"count": 0}

        def fail_once(*args, **kwargs):
            if calls["count"] == 0:
                calls["count"] += 1
                raise RuntimeError("synthetic failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(service.runner, "_run_organize_stage", fail_once)
        failed = service.runner.run(created.job_id)
        assert failed.status.value == "partial_failed"
        failed_stages = {stage.name: stage for stage in failed.stages if stage.item_id}
        assert failed_stages["input"].attempt == 1

        monkeypatch.setattr(service.runner, "_run_organize_stage", original)
        resumed = service.resume(created.job_id)
        assert resumed.status.value == "succeeded"
        resumed_stages = {stage.name: stage for stage in resumed.stages if stage.item_id}
        assert resumed_stages["input"].attempt == 1
        assert resumed_stages["organize"].attempt == 1


def test_workflow_cancel_is_persisted_at_stage_boundary(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("普通文本\n", encoding="utf-8")
    with _service(tmp_path) as service:
        created = service.create_process_job(
            ProcessRequest(
                inputs=(source,),
                output_dir=tmp_path / "output",
                steps=WorkflowSteps(ocr=False, organize=True, merge=False, mask=False, audit=False),
            )
        )

        def cancel_after_input(event: dict) -> None:
            if event.get("stage") == "input" and event.get("status") == "done":
                service.cancel(created.job_id)

        cancelled = service.runner.run(created.job_id, progress=cancel_after_input)
        assert cancelled.status.value == "cancelled"
        assert next(stage for stage in cancelled.stages if stage.name == "input").status.value == "succeeded"
        assert next(stage for stage in cancelled.stages if stage.name == "organize").status.value == "pending"
        assert not list((tmp_path / "state" / "checkpoints").rglob("*.checkpoint"))
