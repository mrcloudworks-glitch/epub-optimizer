"""Tests for the core (GUI-independent) EPUB optimization pipeline."""

from __future__ import annotations

import io
import os
import zipfile

import pytest
from PIL import Image

from epub_optimizer.core.devices import get_device_preset
from epub_optimizer.core.exceptions import OptimizerCancelled
from epub_optimizer.core.optimizer import EpubOptimizer
from tests.fixtures import build_epub

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_optimizer(
    source: str,
    tmp_path,
    device_key: str = "kindle_10th_basic",
    quality: int = 80,
    blur_cover: bool = True,
    cancel_check=None,
):
    """Run the pipeline against ``source`` and return the stats object."""
    output = str(tmp_path / "out.epub")
    optimizer = EpubOptimizer(
        source_path=source,
        output_path=output,
        preset=get_device_preset(device_key),
        quality=quality,
        blur_cover=blur_cover,
        progress_cb=lambda _p, _m: None,
        log_cb=lambda _l, _m: None,
        cancel_check=cancel_check or (lambda: False),
    )
    return optimizer.run()


def read_output(tmp_path) -> dict[str, bytes]:
    with zipfile.ZipFile(str(tmp_path / "out.epub")) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------


def test_full_pipeline_basic_device(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source)
    run_optimizer(source, tmp_path)

    entries = read_output(tmp_path)
    preset = get_device_preset("kindle_10th_basic")

    # Cover: replaced by a strict-3:4 JPEG within the device resolution.
    cover_names = [n for n in entries if "cover" in n.lower()]
    assert cover_names, "cover entry missing from output"
    cover_name = cover_names[0]
    assert cover_name.endswith(".jpg"), f"cover should be JPEG, got {cover_name}"
    width, height = image_size(entries[cover_name])
    assert width <= preset.max_width and height <= preset.max_height
    assert abs(width * 4 - height * 3) <= 2, "cover is not strict 3:4"

    # Inline images: downscaled to the device resolution.
    photo_w, photo_h = image_size(entries["OEBPS/images/photo1.png"])
    assert photo_w <= preset.max_width and photo_h <= preset.max_height

    # The small icon stays small (never upscaled) and remains a PNG.
    icon_w, icon_h = image_size(entries["OEBPS/images/icon.png"])
    assert icon_w == 48 and icon_h == 48

    # References to the old cover were rewritten everywhere.
    for name, data in entries.items():
        if name.endswith((".xhtml", ".opf", ".css", ".ncx")):
            assert b"cover.png" not in data, f"stale cover reference in {name}"

    # OPF points at the new cover with a JPEG media type.
    opf = entries["OEBPS/content.opf"].decode("utf-8")
    assert 'href="images/cover_optimized.jpg"' in opf
    assert '<meta name="cover"' in opf

    # The photo was converted to JPEG: manifest media-type must match the
    # *actual* content even though the entry keeps its .png filename.
    assert 'id="photo1" href="images/photo1.png" media-type="image/jpeg"' in opf

    # The tiny icon stayed PNG; its media-type is untouched.
    assert 'id="icon" href="images/icon.png" media-type="image/png"' in opf

    # Output is meaningfully smaller.
    assert os.path.getsize(str(tmp_path / "out.epub")) < os.path.getsize(source)


def test_mimetype_first_and_stored(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source)
    run_optimizer(source, tmp_path)

    with zipfile.ZipFile(str(tmp_path / "out.epub")) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        # Everything else should be deflated at max compression.
        for name in names[1:]:
            if name.endswith("/"):
                continue
            assert archive.getinfo(name).compress_type == zipfile.ZIP_DEFLATED


def test_paperwhite_device_limits(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source)
    run_optimizer(source, tmp_path, device_key="kindle_10th_paperwhite")

    entries = read_output(tmp_path)
    preset = get_device_preset("kindle_10th_paperwhite")
    for name, data in entries.items():
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
            width, height = image_size(data)
            assert width <= preset.max_width, f"{name} too wide: {width}"
            assert height <= preset.max_height, f"{name} too tall: {height}"

    # The Paperwhite cover canvas is the biggest *exact* 3:4 that fits
    # inside 1072×1448, i.e. 1072×1429.
    cover = [n for n in entries if n.endswith("cover_optimized.jpg")]
    assert cover, "cover missing on paperwhite run"
    width, height = image_size(entries[cover[0]])
    assert (width, height) == (1072, 1429)
    assert abs(width * 4 - height * 3) <= 2


def test_no_cover_still_optimizes_images(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source, include_cover=False)
    run_optimizer(source, tmp_path, blur_cover=True)

    entries = read_output(tmp_path)
    assert "OEBPS/images/cover.png" not in entries
    width, _ = image_size(entries["OEBPS/images/photo1.png"])
    assert width <= 600


def test_cover_already_3x4_is_not_distorted(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source, cover_size=(600, 800))
    run_optimizer(source, tmp_path)

    entries = read_output(tmp_path)
    cover_name = next(n for n in entries if n.endswith(".jpg"))
    width, height = image_size(entries[cover_name])
    # 600x800 stays 600x800 (already exactly 3:4 at device resolution).
    assert (width, height) == (600, 800)


def test_cover_taller_than_3x4_keeps_height(tmp_path) -> None:
    """A 2:3 cover should keep its height; canvas is widened to 3:4."""
    source = str(tmp_path / "book.epub")
    build_epub(source, cover_size=(900, 1350))  # 2:3
    run_optimizer(source, tmp_path)

    entries = read_output(tmp_path)
    cover_name = next(n for n in entries if n.endswith(".jpg"))
    width, height = image_size(entries[cover_name])
    assert height == 800  # kept the 1350-height, downscaled to 800
    assert width == 600
    assert abs(width * 4 - height * 3) <= 2


def test_cancellation_removes_partial_output(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source)
    output = str(tmp_path / "out.epub")

    calls = {"n": 0}

    def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 3  # cancel almost immediately

    optimizer = EpubOptimizer(
        source_path=source,
        output_path=output,
        preset=get_device_preset("kindle_10th_basic"),
        quality=80,
        blur_cover=True,
        progress_cb=lambda _p, _m: None,
        log_cb=lambda _l, _m: None,
        cancel_check=cancel_check,
    )
    with pytest.raises(OptimizerCancelled):
        optimizer.run()
    assert not os.path.exists(output), "partial output must be cleaned up"


def test_epub3_cover_properties_detection(tmp_path) -> None:
    """EPUB 3 covers discovered via manifest properties=cover-image."""
    source = str(tmp_path / "book.epub")
    build_epub(source, epub3=True)
    run_optimizer(source, tmp_path)

    entries = read_output(tmp_path)
    opf = entries["OEBPS/content.opf"].decode("utf-8")
    assert 'href="images/cover_optimized.jpg"' in opf
    assert 'properties="cover-image"' in opf


def test_invalid_file_raises(tmp_path) -> None:
    bogus = tmp_path / "not_an_epub.epub"
    bogus.write_bytes(b"this is definitely not a zip file")

    from epub_optimizer.core.exceptions import InvalidEpubError

    with pytest.raises(InvalidEpubError):
        EpubOptimizer(
            source_path=str(bogus),
            output_path=str(tmp_path / "out.epub"),
            preset=get_device_preset("kindle_10th_basic"),
        ).run()


def test_quality_slider_range_affects_output_size(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source)

    sizes = {}
    for quality in (50, 80, 100):
        output = str(tmp_path / f"out_{quality}.epub")
        EpubOptimizer(
            source_path=source,
            output_path=output,
            preset=get_device_preset("kindle_10th_basic"),
            quality=quality,
            blur_cover=True,
        ).run()
        sizes[quality] = os.path.getsize(output)

    assert sizes[50] < sizes[80] < sizes[100], "higher quality must mean bigger files"


def test_stats_reporting(tmp_path) -> None:
    source = str(tmp_path / "book.epub")
    build_epub(source)
    output = str(tmp_path / "out.epub")
    optimizer = EpubOptimizer(
        source_path=source,
        output_path=output,
        preset=get_device_preset("kindle_10th_basic"),
        quality=80,
        blur_cover=True,
    )
    stats = optimizer.run()
    assert stats.cover_processed is True
    assert stats.images_total >= 2  # cover is handled separately
    assert stats.images_changed >= 1
    assert stats.cover_old == "OEBPS/images/cover.png"
    assert stats.cover_new == "OEBPS/images/cover_optimized.jpg"
    assert stats.output_size == os.path.getsize(output)
    assert stats.savings_percent > 0
    assert stats.elapsed > 0
