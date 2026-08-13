"""Image decoding, downscaling and re-encoding helpers built on Pillow.

These functions operate on raw ``bytes`` in and raw ``bytes`` out so the
rest of the pipeline never has to touch the filesystem or keep track of
temporary files: the whole EPUB is processed in memory.

Optimization strategy for every inline content image:

1. Correct EXIF orientation (cameras often store rotated JPEGs).
2. Downscale with Lanczos so neither dimension exceeds the device screen
   (only ever *shrinks*, never upscales, so aspect ratio is preserved).
3. Flatten transparency onto white — Kindle e-ink renders JPEG covers
   and inline images without alpha support, and white is what EPUB
   readers assume anyway.
4. Re-encode as baseline (non-progressive) JPEG at the user-selected
   quality; baseline JPEGs have the broadest Kindle compatibility.
5. Never *grow* the archive: if re-encoding would produce a bigger file
   (typical for tiny PNG icons or sprites), keep the original format but
   try to losslessly re-optimize it (e.g. ``optimize=True`` PNG).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# ---------------------------------------------------------------------------
# Format tables
# ---------------------------------------------------------------------------

#: Raster formats treated as "content images" and therefore optimizable.
RASTER_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
)

#: File types that may reference other assets by filename (their text is
#: rewritten when the cover image is renamed, see ``cover_processor``).
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {".xhtml", ".html", ".htm", ".xml", ".opf", ".ncx", ".css", ".svg", ".txt"}
)

#: Guard against decompression-bomb attacks on hostile archives
#: (Pillow's default limit is ~178 Mpx; a 100 Mpx cap is still far beyond
#: anything a legit e-book contains).
Image.MAX_IMAGE_PIXELS = 100_000_000

#: Formats we know how to losslessly re-optimize when JPEG is not smaller.
_LOSSLESS_OPTIMIZABLE = frozenset({"PNG", "GIF"})


@dataclass
class OptimizedImage:
    """Result of optimizing one in-memory image."""

    data: bytes          #: Replacement bytes (identical to input if ``changed`` is False).
    width: int           #: Final pixel width (0 when the image was unreadable).
    height: int          #: Final pixel height (0 when the image was unreadable).
    changed: bool        #: Whether the entry should replace the original in the archive.
    note: str = ""       #: Short human-readable explanation for the log console.
    format: str = ""     #: Final encoded format ("JPEG", "PNG", …) — empty if unreadable.


def _extension(name: str) -> str:
    """Return ``'.png'``-style lowercase extension, or ``''`` if none."""
    dot = name.rfind(".")
    return f".{name[dot + 1 :].lower()}" if dot != -1 else ""


def is_raster_image(name: str) -> bool:
    """True if the archive entry name looks like an optimizable raster image."""
    return _extension(name) in RASTER_EXTENSIONS


def is_text_entry(name: str) -> bool:
    """True if the entry is a text-based file that may reference assets."""
    return _extension(name) in TEXT_EXTENSIONS


def image_info(data: bytes) -> tuple[int, int, str]:
    """Return ``(width, height, format)`` for an image, ``(0, 0, "")`` if bad."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size[0], img.size[1], (img.format or "").upper()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return 0, 0, ""


def flatten_to_rgb(img: Image.Image) -> Image.Image:
    """Return an RGB copy of ``img`` with transparency flattened on white.

    Kindle e-readers do not support alpha channels, so translucent PNGs
    are composited onto a white background — the standard rendering
    assumption for EPUB content.
    """
    if img.mode == "RGB":
        return img.convert("RGB")
    if img.mode == "L":
        return img.convert("L")
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if has_alpha:
        rgba = img.convert("RGBA")
        base = Image.new("RGB", rgba.size, (255, 255, 255))
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    return img.convert("RGB")


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    """Encode ``img`` as a baseline JPEG at the requested quality."""
    quality = max(1, min(100, int(quality)))
    buffer = io.BytesIO()
    img.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=False,  # baseline JPEG: maximum Kindle compatibility
        subsampling=2 if quality <= 90 else 0,  # 4:2:0 for small files
    )
    return buffer.getvalue()


def _reoptimize_lossless(img: Image.Image, original: bytes) -> bytes:
    """Try to losslessly shrink the original format; return best bytes."""
    fmt = (img.format or "").upper()
    if fmt not in _LOSSLESS_OPTIMIZABLE:
        return original
    # Never drop animation from multi-frame GIFs.
    if fmt == "GIF" and getattr(img, "n_frames", 1) > 1:
        return original
    buffer = io.BytesIO()
    try:
        img.save(buffer, format=fmt, optimize=True)
    except (OSError, ValueError):
        return original
    candidate = buffer.getvalue()
    return candidate if len(candidate) < len(original) else original


def optimize_image(
    data: bytes,
    max_width: int,
    max_height: int,
    quality: int = 80,
) -> OptimizedImage:
    """Optimize one image to fit ``max_width`` x ``max_height`` at ``quality``.

    Returns an :class:`OptimizedImage` — the caller replaces the archive
    entry only when ``changed`` is True.
    """
    try:
        with Image.open(io.BytesIO(data)) as src:
            # Capture the format BEFORE exif_transpose: the transposed copy
            # does not carry the original ``.format`` attribute.
            src_format = (src.format or "UNKNOWN").upper()
            src = ImageOps.exif_transpose(src)  # honor EXIF orientation
            src.load()  # decode now so the buffer can be released
            width, height = src.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return OptimizedImage(data, 0, 0, False, "unreadable image — kept as-is")

    needs_downscale = width > max_width or height > max_height

    # Already small and already JPEG: only re-encode if it actually shrinks,
    # otherwise leave the bytes untouched (avoids generational loss).
    if not needs_downscale and src_format == "JPEG":
        candidate = _encode_jpeg(flatten_to_rgb(src), quality)
        if len(candidate) < len(data):
            return OptimizedImage(
                candidate, width, height, True,
                f"re-encoded JPEG (q{quality}, {len(candidate)} B)", "JPEG",
            )
        return OptimizedImage(data, width, height, False, "already optimal JPEG", "JPEG")

    rgb = flatten_to_rgb(src)
    if needs_downscale:
        rgb.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        width, height = rgb.size

    candidate = _encode_jpeg(rgb, quality)

    # Converting a non-JPEG source only pays off when the result is smaller;
    # tiny PNG icons almost always survive better in their native format.
    if src_format != "JPEG" and len(candidate) >= len(data):
        best_lossless = _reoptimize_lossless(src, data)
        note = f"kept {src_format} (JPEG not smaller)"
        return OptimizedImage(
            best_lossless, width, height, best_lossless != data, note, src_format,
        )

    return OptimizedImage(
        candidate, width, height, True,
        f"{src_format}→JPEG q{quality} ({len(candidate)} B)", "JPEG",
    )
