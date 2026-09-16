from __future__ import annotations

from pathlib import Path

import pytest

from workflow import ProcessRequest, WorkflowService, WorkflowSteps
from workflow.store import JobBusyError


def _service(tmp_path: Path, *, lease_seconds: int = 300) -> WorkflowService:
    return WorkflowService(
        db_path=tmp_path / "state" / "workflow.sqlite3",
        state_dir=tmp_path / "state",
        checkpoint_root=tmp_path / "state" / "checkpoints",
        lease_seconds=lease_seconds,
    )


def _crash(*_args, **_kwargs):
    raise KeyboardInterrupt("synthetic process crash")


@pytest.mark.parametrize("stage_name", ["ocr", "organize", "merge", "mask", "audit"])
def test_resume_after_crash_in_each_stage(tmp_path: Path, monkeypatch, stage_name: str) -> None:
    source = tmp_path / "source.txt"
    source.write_text("联系人：张三，电话：13800138000\n", encoding="utf-8")
    if stage_name == "ocr":
        steps = WorkflowSteps(ocr=True, organize=False, merge=False, mask=False, audit=False)
        password = None
    elif stage_name == "organize":
        steps = WorkflowSteps(ocr=False, organize=True, merge=False, mask=False, audit=False)
        password = None
    elif stage_name == "merge":
        steps = WorkflowSteps(ocr=False, organize=False, merge=True, mask=False, audit=False)
        password = None
    elif stage_name == "mask":
        steps = WorkflowSteps(ocr=False, organize=False, merge=True, mask=True, audit=False)
        password = "fixture-password"
    else:
        steps = WorkflowSteps(ocr=False, organize=False, merge=True, mask=True, audit=True)
        password = "fixture-password"

    request = ProcessRequest(
        inputs=(source,),
        output_dir=tmp_path / "output",
        steps=steps,
        entities=("PERSON", "PHONE"),
    )

    with _service(tmp_path) as first:
        created = first.create_process_job(request)
        if stage_name == "ocr":
            monkeypatch.setattr(first.runner, "_ocr_text", _crash)
        elif stage_name == "organize":
            stage = first.store.get_stage(created.job_id, created.items[0].item_id, "organize")

            def crash_organize(*_args, **_kwargs):
                first.store.start_stage(stage["stage_id"])
                raise KeyboardInterrupt("synthetic process crash")

            monkeypatch.setattr(first.runner, "_run_organize_stage", crash_organize)
        elif stage_name == "merge":
            monkeypatch.setattr(first.runner, "_merged_text", _crash)
        elif stage_name == "mask":
            monkeypatch.setattr(first.runner, "_run_mask", _crash)
        else:
            monkeypatch.setattr(first.runner, "_run_audit", _crash)

        with pytest.raises(KeyboardInterrupt):
            first.runner.run(created.job_id, password=password)

    with _service(tmp_path, lease_seconds=-1) as resumed_service:
        interrupted = resumed_service.get_status(created.job_id)
        assert interrupted.status.value == "interrupted"
        if stage_name == "ocr":
            monkeypatch.setattr(resumed_service.runner, "_ocr_text", lambda *_args, **_kwargs: "恢复后的 OCR 文本")
        resumed = resumed_service.resume(created.job_id, password=password)
        assert resumed.status.value in {"succeeded", "attention"}
        stage = next(item for item in resumed.stages if item.name == stage_name)
        assert stage.status.value == "succeeded"
        assert stage.attempt >= 1


def test_tampered_checkpoint_is_recomputed(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("原始文本\n", encoding="utf-8")
    request = ProcessRequest(
        inputs=(source,),
        output_dir=tmp_path / "output",
        steps=WorkflowSteps(ocr=False, organize=True, merge=True, mask=False, audit=False),
    )

    with _service(tmp_path) as first:
        created = first.create_process_job(request)
        original = first.runner._run_organize_stage

        def crash_organize(*args, **kwargs):
            stage = first.store.get_stage(created.job_id, created.items[0].item_id, "organize")
            first.store.start_stage(stage["stage_id"])
            raise KeyboardInterrupt("synthetic process crash")

        first.runner._run_organize_stage = crash_organize
        with pytest.raises(KeyboardInterrupt):
            first.runner.run(created.job_id)

        source_artifact = next(
            artifact for artifact in first.store.get_artifacts(created.job_id)
            if artifact["kind"] == "input_checkpoint"
        )
        Path(source_artifact["path"]).write_text("被篡改的 checkpoint", encoding="utf-8")
        first.runner._run_organize_stage = original

    with _service(tmp_path, lease_seconds=-1) as resumed_service:
        resumed = resumed_service.resume(created.job_id)
        assert resumed.status.value == "succeeded"
        input_stage = next(stage for stage in resumed.stages if stage.name == "input")
        organize_stage = next(stage for stage in resumed.stages if stage.name == "organize")
        assert input_stage.attempt == 2
        assert organize_stage.status.value == "succeeded"


def test_second_executor_cannot_claim_live_job(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("普通文本\n", encoding="utf-8")
    request = ProcessRequest(
        inputs=(source,),
        output_dir=tmp_path / "output",
        steps=WorkflowSteps(ocr=False, organize=False, merge=True, mask=False, audit=False),
    )
    first = _service(tmp_path)
    second = _service(tmp_path)
    try:
        created = first.create_process_job(request)
        first.store.claim_job(created.job_id, "fixture-owner")
        with pytest.raises(JobBusyError):
            second.resume(created.job_id)
    finally:
        first.close()
        second.close()


def test_resume_rejects_changed_source_file(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("原始文本\n", encoding="utf-8")
    request = ProcessRequest(
        inputs=(source,),
        output_dir=tmp_path / "output",
        steps=WorkflowSteps(ocr=False, organize=True, merge=True, mask=False, audit=False),
    )

    with _service(tmp_path) as first:
        created = first.create_process_job(request)
        stage = first.store.get_stage(created.job_id, created.items[0].item_id, "organize")

        def crash_organize(*_args, **_kwargs):
            first.store.start_stage(stage["stage_id"])
            raise KeyboardInterrupt("synthetic process crash")

        first.runner._run_organize_stage = crash_organize
        with pytest.raises(KeyboardInterrupt):
            first.runner.run(created.job_id)

    source.write_text("替换后的文本\n", encoding="utf-8")
    with _service(tmp_path, lease_seconds=-1) as resumed_service:
        resumed = resumed_service.resume(created.job_id)
        assert resumed.status.value == "partial_failed"
        assert resumed.last_error_code == "item_failed"
        assert any(stage.error_code == "input_changed" for stage in resumed.stages)
