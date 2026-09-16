"""Private checkpoint files and atomic filesystem helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_state_dir() -> Path:
    """Return a per-user state directory without creating it eagerly."""

    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "Wenveil"
    root = os.environ.get("XDG_STATE_HOME")
    if root:
        return Path(root) / "wenveil"
    return Path.home() / ".local" / "state" / "wenveil"


class CheckpointStore:
    """Store intermediate text outside SQLite under a private job directory."""

    def __init__(self, root: str | Path, job_id: str):
        self.root = Path(root).expanduser().resolve()
        self.job_id = job_id
        self.job_root = self.root / "jobs" / job_id
        self.job_root.mkdir(parents=True, exist_ok=True)

    def path_for(self, item_id: str | None, kind: str) -> Path:
        scope = item_id or "job"
        safe_kind = "".join(char for char in kind if char.isalnum() or char in {"-", "_"})
        safe_scope = "".join(char for char in scope if char.isalnum() or char in {"-", "_"})
        return self.job_root / f"{safe_scope}.{safe_kind}.checkpoint"

    def write_text(self, item_id: str | None, kind: str, text: str) -> tuple[Path, str, int]:
        path = self.path_for(item_id, kind)
        self._atomic_write(path, text.encode("utf-8"))
        return path, sha256_file(path), path.stat().st_size

    def read_text(self, item_id: str | None, kind: str) -> str:
        return self.path_for(item_id, kind).read_text(encoding="utf-8")

    def valid(self, path: str | Path, sha256: str, size_bytes: int) -> bool:
        candidate = Path(path)
        try:
            return candidate.is_file() and candidate.stat().st_size == size_bytes and sha256_file(candidate) == sha256
        except OSError:
            return False

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Remove only this job's private checkpoint directory."""

        if self.job_root.exists():
            shutil.rmtree(self.job_root)


__all__ = ["CheckpointStore", "default_state_dir", "sha256_file"]
