"""High-level EPUB optimization pipeline (GUI-independent).

The :class:`EpubOptimizer` orchestrates the whole flow for a single file:

1. open the EPUB (a ZIP archive) and read every entry into memory,
2. locate the OPF package document via ``META-INF/container.xml``,
3. find the cover, build the 3:4 blurred composite (when enabled) and
   rewrite the OPF + all textual references,
4. downscale / re-encode every inline content image for the target device,
5. re-pack the archive with maximum ZIP compression (level 9) and write
   it atomically (temp file + rename) so a crash can never corrupt an
   existing output file.

Progress and log messages are delivered through plain callbacks so the
same class works headlessly (tests, future CLI) and from a QThread worker
that forwards them as Qt signals.
"""

from __future__ import annotations

import os
import posixpath
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from epub_optimizer.core.cover_processor import (
    _decode,
    apply_cover,
    build_blurred_cover,
    find_cover,
    update_manifest_media_type,
)
from epub_optimizer.core.devices import DevicePreset
from epub_optimizer.core.exceptions import (
    EpubOptimizerError,
    InvalidEpubError,
    OptimizerCancelled,
)
from epub_optimizer.core.image_processor import (
    image_info,
    is_raster_image,
    optimize_image,
)
from epub_optimizer.core.utils import fmt_bytes

#: Level -> short label used in log messages.
_LOG_LABELS = {"info": "INFO", "success": " OK ", "warning": "WARN", "error": "ERROR"}

#: Container XML namespace (OCF spec).
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

#: Progress milestones (percent) for each pipeline stage.
_PROGRESS = {
    "read": 6,      # archive unpacked
    "cover": 12,    # cover compositing finished
    "images": (18, 92),  # image loop spans this range
    "repack": 95,   # repacking started
}

#: Callbacks used to report progress / log lines / cancellation.
LogCallback = Callable[[str, str], None]        # (level, message)
ProgressCallback = Callable[[int, str], None]   # (percent, message)
CancelCheck = Callable[[], bool]


@dataclass
class OptimizationStats:
    """Summary of a completed (or cancelled) optimization run."""

    source_path: str
    output_path: str
    input_size: int
    output_size: int = 0
    images_total: int = 0
    images_changed: int = 0
    cover_processed: bool = False
    cover_old: str | None = None
    cover_new: str | None = None
    elapsed: float = 0.0

    @property
    def savings_percent(self) -> float:
        """Percentage of the original size removed (0.0 if input is empty)."""
        if self.input_size <= 0:
            return 0.0
        return max(0.0, (1.0 - self.output_size / self.input_size) * 100.0)


class EpubOptimizer:
    """Optimize a single EPUB file for a target Kindle device."""

    def __init__(
        self,
        source_path: str,
        output_path: str,
        preset: DevicePreset,
        quality: int = 80,
        blur_cover: bool = True,
        progress_cb: ProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        self.source_path = source_path
        self.output_path = output_path
        self.preset = preset
        self.quality = int(quality)
        self.blur_cover = bool(blur_cover)
        self._progress_cb = progress_cb or (lambda _percent, _msg: None)
        self._log_cb = log_cb or (lambda _level, _msg: None)
        self._cancel_check = cancel_check or (lambda: False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> OptimizationStats:
        """Execute the full pipeline and return a stats summary."""
        started = time.monotonic()
        self._validate()

        entries, infos = self._read_archive()
        self._report_progress(_PROGRESS["read"], "Archive unpacked")
        self._maybe_cancel()

        opf_path = self._locate_opf(entries)
        self._log("info", f"Found package document: {opf_path}")

        stats = OptimizationStats(
            source_path=self.source_path,
            output_path=self.output_path,
            input_size=sum(len(data) for data in entries.values()),
        )

        # --- Cover: 3:4 blurred composite ---------------------------------
        new_cover_path: str | None = None
        if self.blur_cover:
            cover = find_cover(entries, opf_path)
            if cover is not None:
                stats.cover_old = cover.zip_path
                old_data = entries[cover.zip_path]
                old_w, old_h, _ = image_info(old_data)
                self._report_progress(
                    _PROGRESS["cover"],
                    f"Cover found ({cover.method}): {cover.zip_path} "
                    f"({old_w}×{old_h}) — building 3:4 blurred composite…",
                )
                self._maybe_cancel()
                new_data, canvas_w, canvas_h = build_blurred_cover(
                    old_data, self.preset, self.quality
                )
                new_cover_path = apply_cover(entries, opf_path, cover, new_data)
                stats.cover_processed = True
                stats.cover_new = new_cover_path
                self._log(
                    "success",
                    f"Cover optimized: {old_w}×{old_h} → {canvas_w}×{canvas_h} "
                    f"3:4 canvas, JPEG q{self.quality} ({fmt_bytes(len(new_data))})",
                )
            else:
                self._log(
                    "warning",
                    "No cover image found — 'blurred sidebars' skipped "
                    "(images are still optimized).",
                )
        self._maybe_cancel()

        # --- Inline images -------------------------------------------------
        image_names = sorted(
            name
            for name in entries
            if is_raster_image(name) and name != new_cover_path
        )
        stats.images_total = len(image_names)
        low, high = _PROGRESS["images"]
        opf_dir = posixpath.dirname(opf_path)
        for index, name in enumerate(image_names, start=1):
            self._maybe_cancel()
            old_data = entries[name]
            old_w, old_h, old_fmt = image_info(old_data)
            result = optimize_image(
                old_data,
                max_width=self.preset.max_width,
                max_height=self.preset.max_height,
                quality=self.quality,
            )
            if result.changed:
                entries[name] = result.data
                stats.images_changed += 1
                # Content format changed (e.g. PNG→JPEG): keep the entry
                # name (so every reference keeps working) but tell the OPF
                # manifest the truth about the media type.
                if (
                    result.format
                    and old_fmt
                    and result.format != old_fmt
                    and opf_path in entries
                ):
                    opf_text = _decode(entries[opf_path])
                    updated = update_manifest_media_type(
                        opf_text, opf_dir, name, f"image/{result.format.lower()}"
                    )
                    if updated != opf_text:
                        entries[opf_path] = updated.encode("utf-8")
            if result.width:
                detail = (
                    f"{name}: {old_w}×{old_h} ({old_fmt}, {fmt_bytes(len(old_data))}) → "
                    f"{result.width}×{result.height} — {result.note}"
                )
            else:
                detail = f"{name}: {result.note}"
            self._log("info" if result.changed else "detail", detail)
            percent = low + round((index / len(image_names)) * (high - low))
            self._report_progress(
                percent, f"Images {index}/{len(image_names)}: {posixpath.basename(name)}"
            )

        # --- Repack ---------------------------------------------------------
        self._maybe_cancel()
        self._report_progress(_PROGRESS["repack"], "Repacking EPUB (ZIP level 9)…")
        output_size = self._repack(entries, infos)
        self._report_progress(100, "Done")

        stats.output_size = output_size
        stats.elapsed = time.monotonic() - started
        self._log(
            "success",
            f"Finished: {fmt_bytes(stats.input_size)} → {fmt_bytes(output_size)} "
            f"({stats.savings_percent:.1f}% smaller) in {stats.elapsed:.1f} s → "
            f"{self.output_path}",
        )
        return stats

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        if not os.path.isfile(self.source_path):
            raise EpubOptimizerError(f"Source file not found: {self.source_path}")
        if os.path.abspath(self.source_path) == os.path.abspath(self.output_path):
            raise EpubOptimizerError(
                "Output path must differ from the source file (got the same path)."
            )
        self._log("info", f"Source: {self.source_path}")

    def _read_archive(self) -> tuple[dict[str, bytes], dict[str, zipfile.ZipInfo]]:
        """Read every entry of the EPUB into memory (name → bytes)."""
        entries: dict[str, bytes] = {}
        infos: dict[str, zipfile.ZipInfo] = {}
        try:
            with zipfile.ZipFile(self.source_path, "r") as archive:
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue  # directory entries are redundant in re-pack
                    if name in entries:
                        self._log("warning", f"Duplicate archive entry kept once: {name}")
                        continue
                    entries[name] = archive.read(name)
                    infos[name] = archive.getinfo(name)
        except zipfile.BadZipFile as exc:
            raise InvalidEpubError(
                f"'{os.path.basename(self.source_path)}' is not a valid EPUB/ZIP file."
            ) from exc
        except (OSError, RuntimeError) as exc:
            raise EpubOptimizerError(
                f"Failed to read '{os.path.basename(self.source_path)}': {exc}"
            ) from exc

        if not entries:
            raise InvalidEpubError("The EPUB archive is empty.")
        return entries, infos

    def _locate_opf(self, entries: dict[str, bytes]) -> str:
        """Find the OPF package document path (container.xml, then fallback)."""
        container = entries.get("META-INF/container.xml")
        if container:
            try:
                root = ET.fromstring(container)
                rootfile = root.find(f".//{{{_CONTAINER_NS}}}rootfile")
                if rootfile is not None and rootfile.get("full-path"):
                    path = posixpath.normpath(
                        urllib.parse.unquote(rootfile.get("full-path", ""))
                    )
                    if path in entries:
                        return path
            except ET.ParseError:
                pass

        # Fallback: first .opf entry (some producers omit container.xml).
        opfs = sorted(name for name in entries if name.lower().endswith(".opf"))
        if opfs:
            self._log("warning", "container.xml missing or broken — using first .opf")
            return opfs[0]
        raise InvalidEpubError("No OPF package document found inside the EPUB.")

    def _repack(
        self,
        entries: dict[str, bytes],
        infos: dict[str, zipfile.ZipInfo],
    ) -> int:
        """Write the optimized archive with maximum compression (level 9).

        The ``mimetype`` entry is forced to be the first file and stored
        *uncompressed* per the OCF specification — Kindle (and every other
        conformant reader) requires this for reliable detection. Writing
        happens to a temp file followed by an atomic rename.
        """
        out_dir = os.path.dirname(os.path.abspath(self.output_path)) or "."
        os.makedirs(out_dir, exist_ok=True)
        tmp_path = f"{self.output_path}.tmp-{os.getpid()}"

        def _sort_key(name: str) -> tuple[int, str]:
            return (0 if name == "mimetype" else 1, name)

        try:
            with zipfile.ZipFile(
                tmp_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
                allowZip64=True,
            ) as archive:
                for name in sorted(entries, key=_sort_key):
                    self._maybe_cancel()
                    zinfo = infos.get(name) or zipfile.ZipInfo(name)
                    # Per-entry compress_type; the level is set on the
                    # ZipFile constructor (ZIP_STORED ignores it entirely).
                    zinfo.compress_type = (
                        zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
                    )
                    archive.writestr(zinfo, entries[name])
            os.replace(tmp_path, self.output_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        return os.path.getsize(self.output_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _maybe_cancel(self) -> None:
        """Raise :class:`OptimizerCancelled` when the user hit Cancel."""
        if self._cancel_check():
            self._log("warning", "Optimization cancelled by user.")
            raise OptimizerCancelled("Cancelled by user")

    def _report_progress(self, percent: int, message: str) -> None:
        self._progress_cb(max(0, min(100, percent)), message)

    def _log(self, level: str, message: str) -> None:
        label = _LOG_LABELS.get(level, "INFO")
        self._log_cb(level, f"[{label}] {message}")
