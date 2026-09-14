"""Restore masked artifact filenames from encrypted mapping metadata.

This utility deliberately reads only encrypted mapping files. It never opens
the original documents or restores masked document content.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from desensitize.mapping import MappingVault


SAFE_MAPPING_RE = re.compile(r"^(document-[0-9a-f]{12})\.mapping\.enc$")
INVALID_WINDOWS_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class RenameItem:
    safe_stem: str
    original_name: str
    source_paths: tuple[Path, Path, Path]
    target_paths: tuple[Path, Path, Path]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore artifact filenames using encrypted mapping metadata."
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _validate_original_name(source_name: object) -> str:
    if not isinstance(source_name, str) or not source_name:
        raise ValueError("mapping has no usable source filename")

    name = Path(source_name).name
    if name in {"", ".", ".."}:
        raise ValueError("mapping has no usable source filename")
    if INVALID_WINDOWS_CHARS_RE.search(name) or name.endswith((".", " ")):
        raise ValueError("mapping contains an invalid Windows filename")
    return name


def _artifact_names(original_name: str, duplicate_index: int | None) -> tuple[str, str, str]:
    original_path = Path(original_name)
    extension = original_path.suffix or ".md"
    base = original_name[: -len(extension)] if original_path.suffix else original_name
    if duplicate_index is not None:
        base = f"{base}__duplicate-{duplicate_index:02d}"
    return (
        f"{base}.masked{extension}",
        f"{base}.mapping.enc",
        f"{base}.report.json",
    )


def _load_plan(output_dir: Path, password: str) -> tuple[list[RenameItem], int]:
    mapping_paths = sorted(output_dir.glob("*.mapping.enc"), key=lambda path: path.name.casefold())
    records: list[tuple[str, Path, MappingVault]] = []

    for mapping_path in mapping_paths:
        match = SAFE_MAPPING_RE.fullmatch(mapping_path.name)
        if match is None:
            raise ValueError("found a mapping file with an unexpected safe filename")
        vault = MappingVault.load(mapping_path, password)
        original_name = _validate_original_name(vault.source_name)
        records.append((original_name, mapping_path, vault))

    grouped: dict[str, list[tuple[str, Path, MappingVault]]] = {}
    for record in records:
        grouped.setdefault(record[0].casefold(), []).append(record)

    plan: list[RenameItem] = []
    duplicate_count = 0
    for group in grouped.values():
        duplicate_indices = range(1, len(group) + 1) if len(group) > 1 else [None]
        if len(group) > 1:
            duplicate_count += len(group)
        for (original_name, mapping_path, _vault), duplicate_index in zip(
            sorted(group, key=lambda item: item[1].name.casefold()), duplicate_indices
        ):
            safe_stem = mapping_path.name.removesuffix(".mapping.enc")
            masked_path = output_dir / f"{safe_stem}.masked.md"
            report_path = output_dir / f"{safe_stem}.report.json"
            target_names = _artifact_names(original_name, duplicate_index)
            plan.append(
                RenameItem(
                    safe_stem=safe_stem,
                    original_name=original_name,
                    source_paths=(masked_path, mapping_path, report_path),
                    target_paths=tuple(output_dir / name for name in target_names),
                )
            )

    source_paths = {path for item in plan for path in item.source_paths}
    target_paths = [path for item in plan for path in item.target_paths]
    if len(set(target_paths)) != len(target_paths):
        raise ValueError("target filename collision detected")
    for item in plan:
        for source_path in item.source_paths:
            if not source_path.is_file():
                raise FileNotFoundError(f"missing paired artifact for {item.safe_stem}")
    for target_path in target_paths:
        if target_path.exists() and target_path not in source_paths:
            raise FileExistsError("target filename already exists outside the rename set")

    return plan, duplicate_count


def _rename(plan: list[RenameItem]) -> None:
    token = uuid.uuid4().hex
    moves: list[tuple[Path, Path, Path]] = []
    for index, item in enumerate(plan):
        for kind, (source_path, target_path) in enumerate(
            zip(item.source_paths, item.target_paths)
        ):
            temporary_path = source_path.parent / (
                f".wenveil-filename-rename-{token}-{index:04d}-{kind:02d}.tmp"
            )
            moves.append((source_path, temporary_path, target_path))

    try:
        for source_path, temporary_path, _target_path in moves:
            source_path.rename(temporary_path)
        for _source_path, temporary_path, target_path in moves:
            temporary_path.rename(target_path)
    except Exception:
        # Best-effort rollback keeps a failed operation from leaving paired
        # artifacts split across safe and restored names.
        for source_path, temporary_path, target_path in reversed(moves):
            if target_path.exists() and not temporary_path.exists():
                target_path.rename(temporary_path)
            if temporary_path.exists() and not source_path.exists():
                temporary_path.rename(source_path)
        raise


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    if not output_dir.is_dir():
        raise SystemExit("output directory does not exist")

    password = os.environ.get("DESENSE_PASSWORD")
    if not password:
        raise SystemExit("set DESENSE_PASSWORD in the process environment")

    try:
        plan, duplicate_count = _load_plan(output_dir, password)
        if not args.dry_run:
            _rename(plan)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"MAPPINGS={len(plan)} ARTIFACTS={len(plan) * 3} "
        f"DUPLICATE_FILENAMES={duplicate_count} "
        f"DRY_RUN={str(args.dry_run).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
