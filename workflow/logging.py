"""Job-scoped JSONL logging with a deliberately small safe field surface."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobLogger:
    """Write safe operational records; callers cannot pass arbitrary context."""

    def __init__(self, path: str | Path, job_id: str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.job_id = job_id
        self._lock = threading.Lock()

    def write(
        self,
        *,
        event: str,
        status: str | None = None,
        item_id: str | None = None,
        stage: str | None = None,
        progress: float | None = None,
        count: int | None = None,
        size_bytes: int | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
        summary: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "time": _now(),
            "job_id": self.job_id,
            "event": event,
        }
        for key, value in {
            "status": status,
            "item_id": item_id,
            "stage": stage,
            "progress": progress,
            "count": count,
            "size_bytes": size_bytes,
            "duration_ms": duration_ms,
            "error_code": error_code,
            "summary": summary,
        }.items():
            if value is not None:
                record[key] = value
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()


__all__ = ["JobLogger"]
