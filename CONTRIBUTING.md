# Contributing to Wenveil（文隐）

Thank you for contributing to the offline OCR, text-organization, and reversible-desensitization toolkit.

## Data boundary

This repository accepts code, tests built from synthetic data, public documentation, and reviewed generic
rules. Do not submit source documents, OCR text copied from a client file, client/deal/project names,
personal data, masked or restored outputs, mapping files, passwords, model checkpoints, screenshots, or logs.
Use temporary authorized inputs under `test-artifacts/`, which is ignored by Git.

Project-specific rules belong in a local ignored configuration. The tracked `rules/projects.txt` file is an
empty public template by design.

## Development checks

```powershell
python -m pip install -e .
python -m compileall -q common desensitize ocr organize training
pytest -q
python -m ocr --help
python -m organize --help
python -m desensitize --help
```

Before opening a pull request, run the public-release checks and confirm that no sensitive path or value is
present in the working tree or reachable history. Keep commits small and describe behavior changes clearly.

## License

Contributions to Wenveil are accepted under the Apache License 2.0. See [LICENSE](LICENSE) for the complete
terms. Third-party dependencies and model weights retain their own licensing terms.
