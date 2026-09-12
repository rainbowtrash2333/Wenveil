"""Reversible local document desensitization for OCR-derived Markdown."""

from .models import Span
from .config import EngineConfig, load_config
from .pipeline import AnonymizationResult, Desensitizer
from .mapping import MappingVault, restore_text

__all__ = [
    "AnonymizationResult",
    "Desensitizer",
    "EngineConfig",
    "MappingVault",
    "Span",
    "load_config",
    "restore_text",
]
