from pathlib import Path

from desktop.bridge.restore_filenames import _load_plan, _rename
from desensitize.mapping import MappingVault


def _write_artifact_group(output_dir: Path, safe_stem: str, source_name: str, password: str) -> None:
    vault = MappingVault(
        schema_version=2,
        job_id=safe_stem,
        source_hash="source",
        normalized_hash="normalized",
        masked_hash="masked",
        config_hash="config",
        source_name=source_name,
    )
    vault.save(output_dir / f"{safe_stem}.mapping.enc", password)
    (output_dir / f"{safe_stem}.masked.md").write_text("masked", encoding="utf-8")
    (output_dir / f"{safe_stem}.report.json").write_text("{}", encoding="utf-8")


def test_restore_filenames_renames_paired_artifacts_and_disambiguates_duplicates(tmp_path: Path):
    password = "test-password"
    _write_artifact_group(tmp_path, "document-000000000001", "alpha.md", password)
    _write_artifact_group(tmp_path, "document-000000000002", "alpha.md", password)

    plan, duplicate_count = _load_plan(tmp_path, password)
    assert len(plan) == 2
    assert duplicate_count == 2

    _rename(plan)

    assert (tmp_path / "alpha__duplicate-01.masked.md").is_file()
    assert (tmp_path / "alpha__duplicate-01.mapping.enc").is_file()
    assert (tmp_path / "alpha__duplicate-01.report.json").is_file()
    assert (tmp_path / "alpha__duplicate-02.masked.md").is_file()
    assert not list(tmp_path.glob("document-*.mapping.enc"))
