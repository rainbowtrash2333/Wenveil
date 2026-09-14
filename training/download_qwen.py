"""Download and verify a Qwen3.5 base checkpoint into the local model tree."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_REPOSITORY = "Qwen/Qwen3.5-0.8B"
DEFAULT_OUTPUT = Path("models/qwen3.5-0.8b-base")
REQUIRED_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_local_model(path: str | Path) -> dict[str, Any]:
    """Verify required local artifacts without loading model weights."""

    root = Path(path)
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    weights = sorted(root.glob("*.safetensors"))
    if not weights:
        missing.append("*.safetensors")
    if missing:
        raise FileNotFoundError(
            "local Qwen checkpoint is incomplete: " + ", ".join(missing)
        )
    return {
        "model_family": "Qwen3.5",
        "files": len([item for item in root.iterdir() if item.is_file()]),
        "weight_files": len(weights),
        "weight_bytes": sum(item.stat().st_size for item in weights),
        "config_sha256": _sha256(root / "config.json"),
    }


def _write_manifest(destination: Path, summary: dict[str, Any]) -> None:
    (destination / "download_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def download_model(
    *,
    repository: str = DEFAULT_REPOSITORY,
    output_dir: str | Path = DEFAULT_OUTPUT,
    revision: str | None = None,
) -> dict[str, Any]:
    """Download one exact Hub revision and return a safe manifest."""

    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as exc:  # pragma: no cover - optional command dependency
        raise RuntimeError("model download requires huggingface_hub") from exc

    info = HfApi().model_info(repository, revision=revision, files_metadata=True)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repository,
        revision=revision,
        local_dir=destination,
        local_dir_use_symlinks=False,
    )
    summary = verify_local_model(destination)
    summary.update(
        {
            "repository": repository,
            "revision": revision or info.sha,
            "hub_commit": info.sha,
        }
    )
    _write_manifest(destination, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="download a local Qwen3.5 base model")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--revision")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify an already downloaded local model without network access",
    )
    args = parser.parse_args(argv)
    if args.verify_only:
        summary = verify_local_model(args.output_dir)
        summary.update({"repository": "local", "revision": None, "hub_commit": None})
        _write_manifest(args.output_dir, summary)
    else:
        summary = download_model(
            repository=args.repository,
            output_dir=args.output_dir,
            revision=args.revision,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
