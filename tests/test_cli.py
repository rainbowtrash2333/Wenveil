from pathlib import Path

from desensitize.cli import _build_parser


def test_mask_defaults_to_test_artifact_output_directory() -> None:
    args = _build_parser().parse_args(["mask", "sample.md"])

    assert args.output == Path("test-artifacts/desensitization-outputs")
