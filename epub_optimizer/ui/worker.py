"""Background :class:`QThread` worker that runs the optimization pipeline.

The worker never touches GUI widgets directly — everything flows back to
the main thread through Qt signals, which keeps the UI responsive while a
batch of EPUBs is being processed.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QThread, Signal

from epub_optimizer.core.devices import get_device_preset
from epub_optimizer.core.exceptions import OptimizerCancelled
from epub_optimizer.core.optimizer import EpubOptimizer


class OptimizeWorker(QThread):
    """Process a list of EPUB files sequentially in a background thread."""

    #: Emitted when a file starts. (path, index, total)
    file_started = Signal(str, int, int)
    #: Emitted when a file finishes. (path, success, summary message)
    file_finished = Signal(str, bool, str)
    #: Pipeline progress. (percent 0-100, detail message)
    progress = Signal(int, str)
    #: Log line. (level: info/detail/success/warning/error, message)
    log = Signal(str, str)
    #: Emitted after the whole batch. (succeeded, failed)
    batch_finished = Signal(int, int)
    #: Emitted when the user cancelled mid-batch.
    cancelled = Signal()

    def __init__(
        self,
        files: list[str],
        device_key: str,
        quality: int,
        blur_cover: bool,
        output_dir: str,
        replacement_cover_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._files = files
        self._device_key = device_key
        self._quality = quality
        self._blur_cover = blur_cover
        self._output_dir = output_dir
        self._replacement_cover_path = replacement_cover_path
        self._cancel_requested = False
        #: Outputs reserved during THIS batch (collision detection).
        self._used_outputs: set[str] = set()

    def cancel(self) -> None:
        """Ask the worker to stop as soon as the pipeline checks for it."""
        self._cancel_requested = True

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        succeeded = failed = 0
        preset = get_device_preset(self._device_key)

        for index, source in enumerate(self._files, start=1):
            if self._cancel_requested:
                break
            self.file_started.emit(source, index, len(self._files))
            self.progress.emit(0, f"Starting {os.path.basename(source)}…")

            try:
                output = self._output_path_for(source)
                stats = EpubOptimizer(
                    source_path=source,
                    output_path=output,
                    preset=preset,
                    quality=self._quality,
                    blur_cover=self._blur_cover,
                    replacement_cover_path=self._replacement_cover_path,
                    progress_cb=lambda percent, msg: self.progress.emit(percent, msg),
                    log_cb=lambda level, msg: self.log.emit(level, msg),
                    cancel_check=lambda: self._cancel_requested,
                ).run()
                summary = (
                    f"{stats.input_size / 1024:.0f} KB → "
                    f"{stats.output_size / 1024:.0f} KB "
                    f"({stats.savings_percent:.1f}% smaller)"
                )
                self.file_finished.emit(source, True, summary)
                succeeded += 1
            except OptimizerCancelled:
                self.cancelled.emit()
                return
            except Exception as exc:  # noqa: BLE001 — report & continue batch
                self.log.emit("error", f"{os.path.basename(source)}: {exc}")
                self.file_finished.emit(source, False, str(exc))
                failed += 1

        self.batch_finished.emit(succeeded, failed)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _output_path_for(self, source: str) -> str:
        """Compute the output path for a source file.

        * No output folder chosen → the optimized file is written next to
          the source as ``<stem>_optimized.epub`` (the original is never
          overwritten).
        * A previous run of the *same* source already produced the target
          → overwrite it (re-optimizing is an expected workflow).
        * Two different sources collide on the same target name within one
          batch (same stem, different folders) → numeric suffix keeps the
          outputs apart.
        """
        stem = os.path.splitext(os.path.basename(source))[0]
        folder = self._output_dir or (os.path.dirname(source) or ".")
        candidate = os.path.join(folder, f"{stem}_optimized.epub")
        if candidate in self._used_outputs:
            candidate = self._uniquify(folder, stem)
        self._used_outputs.add(candidate)
        return candidate

    def _uniquify(self, folder: str, stem: str) -> str:
        """Pick the next free ``<stem>_optimized_N.epub`` name."""
        for index in range(2, 1000):
            unique = os.path.join(folder, f"{stem}_optimized_{index}.epub")
            if unique not in self._used_outputs and not os.path.exists(unique):
                return unique
        return os.path.join(folder, f"{stem}_optimized_999.epub")
