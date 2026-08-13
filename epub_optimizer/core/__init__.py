"""Core, GUI-independent EPUB optimization pipeline.

Everything under :mod:`epub_optimizer.core` deliberately avoids importing
Qt so the pipeline can be exercised from unit tests, scripts, or a future
CLI without needing a display server.
"""

from epub_optimizer.core.devices import (
    COVER_ASPECT,
    DEFAULT_DEVICE_KEY,
    DEVICE_PRESETS,
    DevicePreset,
    get_device_preset,
)
from epub_optimizer.core.exceptions import (
    EpubOptimizerError,
    InvalidEpubError,
    OptimizerCancelled,
)
from epub_optimizer.core.optimizer import EpubOptimizer, OptimizationStats

__all__ = [
    "COVER_ASPECT",
    "DEFAULT_DEVICE_KEY",
    "DEVICE_PRESETS",
    "DevicePreset",
    "EpubOptimizer",
    "EpubOptimizerError",
    "InvalidEpubError",
    "OptimizationStats",
    "OptimizerCancelled",
    "get_device_preset",
]
