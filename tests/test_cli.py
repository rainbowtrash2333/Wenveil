import json
from pathlib import Path

from desensitize.cli import _build_parser, main


def test_mask_defaults_to_test_artifact_output_directory() -> None:
    args = _build_parser().parse_args(["mask", "sample.md"])

    assert args.output == Path("test-artifacts/desensitization-outputs")


def test_mask_without_password_writes_irreversible_mask_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "synthetic-note.md"
    source.write_text("联系人：张三，电话：13800138000\n", encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.delenv("DESENSE_PASSWORD", raising=False)

    main(["mask", str(source), "-o", str(output)])

    response = json.loads(capsys.readouterr().out)
    assert response["reversible"] is False
    assert response["normalized"] is None
    assert response["mapping"] is None
    masked_path = Path(response["masked"])
    assert masked_path.is_file()
    assert "张三" not in masked_path.read_text(encoding="utf-8")
    assert "13800138000" not in masked_path.read_text(encoding="utf-8")
    assert not list(output.glob("*.mapping.enc"))
    assert not list(output.glob("*.normalized.md"))

    report = json.loads(Path(response["report"]).read_text(encoding="utf-8"))
    assert report["reversible"] is False
    assert report["mapping_encrypted"] is False


def test_mask_without_password_removes_stale_reversible_artifacts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "synthetic-note.md"
    source.write_text("电话：13800138000\n", encoding="utf-8")
    output = tmp_path / "output"

    main(["mask", str(source), "-o", str(output), "--password", "fixture-password"])
    capsys.readouterr()
    assert list(output.glob("*.mapping.enc"))
    assert list(output.glob("*.normalized.md"))

    monkeypatch.delenv("DESENSE_PASSWORD", raising=False)
    main(["mask", str(source), "-o", str(output)])

    response = json.loads(capsys.readouterr().out)
    assert response["reversible"] is False
    assert not list(output.glob("*.mapping.enc"))
    assert not list(output.glob("*.normalized.md"))
