import json
from pathlib import Path
from types import SimpleNamespace

from ocr.cli import _build_parser
from ocr.config import Config, ProfilingConfig
from ocr.profiling import DocumentProfile, ProfileSession, fingerprint_file
from ocr.pipeline import _convert_files


def test_profile_cli_options_are_opt_in() -> None:
    args = _build_parser().parse_args([
        "--profile",
        "--profile-output",
        "profile.json",
    ])

    assert args.profile is True
    assert args.profile_output == "profile.json"


def test_profile_report_contains_timings_but_not_input_text(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-input.pdf"
    source.write_bytes(b"synthetic private text")
    size_bytes, source_sha256 = fingerprint_file(source)

    document = DocumentProfile("document-safe-id", ".pdf")
    document.size_bytes = size_bytes
    document.source_sha256 = source_sha256
    with document.stage("synthetic.stage"):
        pass
    document.set_counter("total_pages", 1)
    document.set_page_profile(
        1,
        {
            "status": "success",
            "ocr_ms": 1.25,
            "ocr_result_count": 2,
        },
    )
    document.finish(status="success")

    session = ProfileSession(
        Config(profiling=ProfilingConfig(enabled=True)),
    )
    session.record_document(document)
    session.record_project(
        project_id="project-safe-id",
        file_count=1,
        elapsed_ms=2.0,
        stages_ms={"files.convert": 1.0, "project.write": 1.0},
        status="success",
        output_size_bytes=42,
    )

    output = session.write(tmp_path / "profile.json")
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["report_type"] == "ocr_profile"
    assert report["documents"][0]["document_id"] == "document-safe-id"
    assert report["documents"][0]["stages_ms"]["synthetic.stage"] >= 0
    assert report["documents"][0]["page_profiles"]["1"]["ocr_ms"] == 1.25
    assert "synthetic private text" not in output.read_text(encoding="utf-8")


def test_pipeline_profile_separates_worker_execution_and_future_wait(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"pdf")
    config = Config(
        profiling=ProfilingConfig(enabled=True),
    )
    session = ProfileSession(config)
    converter = SimpleNamespace(
        profile_session=session,
        convert=lambda _path: "markdown",
    )

    results = _convert_files(converter, [source], config, None)
    report = session.snapshot()

    assert results == [(source, "markdown")]
    assert report["totals"]["pipeline_stages_ms"]["pipeline.worker_execution"] >= 0
    assert report["totals"]["pipeline_stages_ms"]["pipeline.future_wait"] >= 0
    event_names = [event["name"] for event in report["events"]]
    assert "pipeline.worker_enter" in event_names
    assert "pipeline.worker_return" in event_names
    assert "pipeline.future_done" in event_names
    assert "pipeline.executor_shutdown_before" in event_names
    assert "pipeline.executor_shutdown_after" in event_names
    assert "synthetic.pdf" not in json.dumps(report, ensure_ascii=False)
