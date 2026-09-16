"""OCR 流水线的可选分阶段计时与安全统计。

profiling 默认关闭。启用后只记录安全 ID、计数、耗时、配置摘要和哈希，
不记录输入文档原文、文件名或 OCR 文本，便于定位完整处理生命周期中的瓶颈。
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, MutableMapping, Optional


Number = int | float


def _round_ms(value: float) -> float:
    """将秒转换为稳定的毫秒表示。"""

    return round(value * 1000, 3)


def add_elapsed(target: MutableMapping[str, float], name: str, elapsed_ms: float) -> None:
    """向阶段累计耗时，允许多个页面或 worker 汇总到同一阶段。"""

    target[name] = round(target.get(name, 0.0) + elapsed_ms, 3)


@contextmanager
def timed(target: MutableMapping[str, float], name: str) -> Iterator[None]:
    """测量一个代码块并将耗时写入指定字典。"""

    started = time.perf_counter()
    try:
        yield
    finally:
        add_elapsed(target, name, _round_ms(time.perf_counter() - started))


def fingerprint_file(path: Path, *, include_hash: bool = True) -> tuple[int, Optional[str]]:
    """返回文件大小和可选 SHA-256，不返回路径或文件内容。"""

    size_bytes = path.stat().st_size
    if not include_hash:
        return size_bytes, None

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return size_bytes, digest.hexdigest()


def _runtime_snapshot() -> dict[str, Any]:
    """收集不含路径和用户数据的运行时摘要。"""

    providers: list[str] = []
    try:
        import onnxruntime as ort

        providers = sorted(str(item) for item in ort.get_available_providers())
    except Exception:
        pass

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "onnxruntime_providers": providers,
    }


def _config_snapshot(config: Any) -> dict[str, Any]:
    """只保留影响性能的非路径配置，避免 profile 泄露本地目录。"""

    ocr = config.ocr
    docling = config.docling
    concurrency = config.concurrency
    return {
        "ocr": {
            "enabled": bool(ocr.enabled),
            "lang": list(ocr.lang),
            "use_angle_cls": bool(ocr.use_angle_cls),
            "use_gpu": bool(ocr.use_gpu),
            "use_dml": bool(ocr.use_dml),
            "text_score": float(ocr.text_score),
            "box_score": float(ocr.box_score),
            "max_image_size": int(ocr.max_image_size),
            "image_scale": float(ocr.image_scale),
            "intra_op_num_threads": int(ocr.intra_op_num_threads),
            "inter_op_num_threads": int(ocr.inter_op_num_threads),
        },
        "docling": {
            "images_scale": float(docling.images_scale),
            "num_threads": int(docling.num_threads),
            "queue_max_size": int(docling.queue_max_size),
            "pdf_backend": str(docling.pdf_backend),
            "table_mode": str(docling.table_mode),
            "scan_fast_path": bool(docling.scan_fast_path),
            "min_valid_text_chars": int(docling.min_valid_text_chars),
            "scan_bitmap_threshold": float(docling.scan_bitmap_threshold),
            "fast_scan_workers": int(docling.fast_scan_workers),
        },
        "concurrency": {
            "max_workers": int(concurrency.max_workers),
        },
    }


@dataclass
class DocumentProfile:
    """单个文档的生命周期统计。"""

    document_id: str
    extension: str
    started: float = field(default_factory=time.perf_counter, repr=False)
    stages_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, Any] = field(default_factory=dict)
    page_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    size_bytes: Optional[int] = None
    source_sha256: Optional[str] = None
    status: str = "running"
    error_type: Optional[str] = None
    elapsed_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def stage(self, name: str):
        """返回一个用于测量阶段的上下文管理器。"""

        return timed(self.stages_ms, name)

    def add_stage(self, name: str, elapsed_ms: float) -> None:
        """线程安全地累计阶段耗时。"""

        with self._lock:
            add_elapsed(self.stages_ms, name, elapsed_ms)

    def set_counter(self, name: str, value: Any) -> None:
        """设置安全计数或固定枚举值。"""

        with self._lock:
            self.counters[name] = value

    def add_counter(self, name: str, value: Number) -> None:
        """线程安全地累计数值计数。"""

        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + value

    def set_page_profile(self, page_no: int, values: dict[str, Any]) -> None:
        """保存单页统计，不保存单页原文。"""

        with self._lock:
            self.page_profiles[str(page_no)] = dict(values)

    def finish(self, *, status: str, error_type: Optional[str] = None) -> None:
        """结束文档计时。"""

        self.status = status
        self.error_type = error_type
        self.elapsed_ms = _round_ms(time.perf_counter() - self.started)

    def to_dict(self, *, include_page_profiles: bool = True) -> dict[str, Any]:
        """序列化为不含原文的 JSON 对象。"""

        with self._lock:
            result: dict[str, Any] = {
                "document_id": self.document_id,
                "extension": self.extension,
                "size_bytes": self.size_bytes,
                "source_sha256": self.source_sha256,
                "status": self.status,
                "error_type": self.error_type,
                "elapsed_ms": self.elapsed_ms,
                "stages_ms": dict(self.stages_ms),
                "counters": dict(self.counters),
            }
            if include_page_profiles:
                result["page_profiles"] = {
                    page_no: dict(values)
                    for page_no, values in self.page_profiles.items()
                }
            return result


@dataclass
class ProfileSession:
    """一次 OCR 流水线运行的统计收集器。"""

    config: Any
    include_page_profiles: bool = True
    started: float = field(default_factory=time.perf_counter, repr=False)
    stages_ms: dict[str, float] = field(default_factory=dict)
    documents: list[DocumentProfile] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    _event_sequence: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def stage(self, name: str):
        """返回一个用于测量流水线阶段的上下文管理器。"""

        return timed(self.stages_ms, name)

    def add_stage(self, name: str, elapsed_ms: float) -> None:
        """线程安全地累计流水线阶段耗时。"""

        with self._lock:
            add_elapsed(self.stages_ms, name, elapsed_ms)

    def record_document(self, profile: DocumentProfile) -> None:
        """收集一个已结束的文档 profile。"""

        with self._lock:
            self.documents.append(profile)

    def record_event(
        self,
        name: str,
        *,
        document_id: Optional[str] = None,
    ) -> None:
        """记录安全的生命周期事件，不保存路径、原文或异常上下文。"""

        event = {
            "name": name,
            "at_ms": _round_ms(time.perf_counter() - self.started),
            "thread": threading.current_thread().name,
        }
        if document_id is not None:
            event["document_id"] = document_id
        with self._lock:
            event["sequence"] = self._event_sequence
            self._event_sequence += 1
            self.events.append(event)

    def record_project(
        self,
        *,
        project_id: str,
        file_count: int,
        elapsed_ms: float,
        stages_ms: dict[str, float],
        status: str,
        output_size_bytes: Optional[int] = None,
    ) -> None:
        """记录项目级转换、合并和写盘统计。"""

        with self._lock:
            self.projects.append({
                "project_id": project_id,
                "file_count": file_count,
                "elapsed_ms": round(elapsed_ms, 3),
                "stages_ms": dict(stages_ms),
                "status": status,
                "output_size_bytes": output_size_bytes,
            })

    def _document_stage_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for document in self.documents:
            for name, elapsed_ms in document.stages_ms.items():
                add_elapsed(totals, name, elapsed_ms)
        return totals

    def _document_counter_totals(self) -> dict[str, Number]:
        totals: dict[str, Number] = {}
        for document in self.documents:
            for name, value in document.counters.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                totals[name] = totals.get(name, 0) + value
        return totals

    def snapshot(self) -> dict[str, Any]:
        """生成完整运行报告。"""

        documents = [
            profile.to_dict(include_page_profiles=self.include_page_profiles)
            for profile in sorted(self.documents, key=lambda item: item.document_id)
        ]
        document_elapsed_ms = round(
            sum(float(item["elapsed_ms"]) for item in documents),
            3,
        )
        return {
            "schema_version": 1,
            "report_type": "ocr_profile",
            "measurement_mode": "wall_clock_with_parallel_stage_sums",
            "runtime": _runtime_snapshot(),
            "config": _config_snapshot(self.config),
            "totals": {
                "elapsed_ms": _round_ms(time.perf_counter() - self.started),
                "document_elapsed_ms_sum": document_elapsed_ms,
                "documents": len(documents),
                "projects": len(self.projects),
                "pipeline_stages_ms": dict(self.stages_ms),
                "document_stages_ms_sum": self._document_stage_totals(),
                "document_numeric_counters": self._document_counter_totals(),
            },
            "projects": list(self.projects),
            "documents": documents,
            "events": list(self.events),
        }

    def write(self, path: str | Path) -> Path:
        """写入 profiling JSON，父目录不存在时创建。"""

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path
