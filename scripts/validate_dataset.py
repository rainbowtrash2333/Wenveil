"""Validate the fixed external dataset and print only a safe JSON summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from training.external_dataset import FIXED_SPLITS, validate_external_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate fixed external NER JSONL splits")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument(
        "--split",
        choices=(*FIXED_SPLITS, "all"),
        default="all",
    )
    args = parser.parse_args(argv)
    splits = FIXED_SPLITS if args.split == "all" else (args.split,)
    summary = validate_external_dataset(Path(args.data_dir), splits)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
