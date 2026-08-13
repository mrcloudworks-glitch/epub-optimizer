"""Cover detection and 3:4 "blurred sidebars" cover generation.

Kindle renders the cover of a book full-screen. If the cover's aspect
ratio is not exactly 3:4 the device letterboxes/pillarboxes it against a
plain (usually black or white) background — an ugly "sidebar" artifact.

The fix implemented here, mirroring what Calibre's "blur" plugboard does:

a. compute a strict 3:4 canvas sized for the target device,
b. scale the cover to *fill* that canvas and center-crop it, then blur it
   with a Gaussian filter (radius scaled from ~18 px at 800 px height so
   the visual amount stays constant across devices),
c. overlay the original, uncropped cover, scaled to *fit* the canvas,
   perfectly centered,
d. write the composite back into the archive and rewrite every textual
   reference (OPF manifest, XHTML, CSS, NCX …) that points at the old
   cover file.

The result: the sharp original cover floats over a soft, blurred version
of itself — no more hard sidebars, and the cover keeps its original
geometry (it is never stretched).
"""

from __future__ import annotations

import io
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from epub_optimizer.core.devices import DevicePreset
from epub_optimizer.core.image_processor import (
    flatten_to_rgb,
    is_raster_image,
    is_text_entry,
)

#: OPF namespace URI (both EPUB 2 and EPUB 3 use it).
OPF_NS = "http://www.idpf.org/2007/opf"
#: Dublin Core namespace used inside the OPF metadata block.
DC_NS = "http://purl.org/dc/elements/1.1/"

#: Basename prefixes tried (in order) when the OPF declares no cover.
_COVER_NAME_HINTS: tuple[str, ...] = (
    "cover", "front", "frontcover", "front_cover", "titlepage", "title", "coverart",
)

#: Minimum blur radius (px) — below this the "subtle blur" is invisible.
_MIN_BLUR_RADIUS = 15
#: Blur radius at the reference canvas height.
_REFERENCE_BLUR_RADIUS = 18
#: Canvas height (px) the base blur radius is defined against.
_REFERENCE_CANVAS_HEIGHT = 800

#: Image extensions that may appear inside an SVG cover wrapper.
_SVG_EMBEDDED_IMAGE = re.compile(
    r"""(?:xlink:href|href)\s*=\s*["']([^"']+\.(?:png|jpe?g|gif|webp|bmp))["']""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CoverInfo:
    """Where the cover lives in the archive and how it was identified."""

    #: Archive entry path (normalized, zip-relative).
    zip_path: str
    #: OPF manifest item id (None when only located via filename heuristics).
    item_id: str | None
    #: Human-readable description of how the cover was found (for logging).
    method: str


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    """Normalize an archive path for comparison (decode, unify slashes)."""
    return posixpath.normpath(urllib.parse.unquote(path).replace("\\", "/"))


def _resolve_from(base_dir: str, href: str) -> str:
    """Resolve a (possibly %-encoded) href relative to ``base_dir``."""
    href = (href or "").strip().replace("\\", "/")
    if not href:
        return ""
    if href.startswith("/"):  # malformed, but seen in the wild
        return _norm(href.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, urllib.parse.unquote(href)))


# ---------------------------------------------------------------------------
# OPF introspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ManifestItem:
    item_id: str
    href: str
    media_type: str
    properties: str


def _parse_opf(
    opf_text: str,
) -> tuple[list[_ManifestItem], str | None, str | None, list[str]]:
    """Parse an OPF document.

    Returns ``(items, cover_meta_id, guide_cover_href, spine_ids)``.
    """
    items: list[_ManifestItem] = []
    cover_meta_id: str | None = None
    guide_cover_href: str | None = None
    spine_ids: list[str] = []
    try:
        root = ET.fromstring(opf_text)
    except ET.ParseError:
        return items, cover_meta_id, guide_cover_href, spine_ids

    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "item":
            items.append(
                _ManifestItem(
                    item_id=element.get("id", ""),
                    href=element.get("href", ""),
                    media_type=element.get("media-type", ""),
                    properties=element.get("properties", ""),
                )
            )
        elif tag == "meta" and (element.get("name") or "").strip().lower() == "cover":
            cover_meta_id = element.get("content")
        elif tag == "reference" and (element.get("type") or "").strip().lower() == "cover":
            guide_cover_href = element.get("href")
        elif tag == "itemref" and element.get("idref"):
            spine_ids.append(element.get("idref", ""))
    return items, cover_meta_id, guide_cover_href, spine_ids


def _spine_documents(
    opf_text: str, opf_dir: str, entries: dict[str, bytes]
) -> list[str]:
    """Return the archive paths of the XHTML documents in reading order."""
    items, _, _, spine_ids = _parse_opf(opf_text)
    href_by_id = {item.item_id: item.href for item in items if item.item_id}
    norm_entries = {_norm(name): name for name in entries}
    documents: list[str] = []
    for spine_id in spine_ids:
        href = href_by_id.get(spine_id, "")
        path = _resolve_from(opf_dir, href)
        if path in norm_entries and norm_entries[path].lower().endswith(
            (".xhtml", ".html", ".htm")
        ):
            documents.append(norm_entries[path])
    return documents


def _image_inside_svg(
    svg_bytes: bytes, svg_dir: str, norm_entries: dict[str, str]
) -> str | None:
    """Return the raster image embedded by an SVG cover wrapper, if any."""
    text = _decode(svg_bytes)
    for match in _SVG_EMBEDDED_IMAGE.finditer(text):
        href = match.group(1)
        path = _norm(_resolve_from(svg_dir, href))
        real = norm_entries.get(path)
        if real and is_raster_image(real):
            return real
    return None


# ---------------------------------------------------------------------------
# Cover discovery
# ---------------------------------------------------------------------------


def find_cover(entries: dict[str, bytes], opf_zip_path: str) -> CoverInfo | None:
    """Locate the primary cover image inside an unpacked EPUB.

    Detection order (most authoritative first):

    1. ``<meta name="cover" content="item-id"/>`` in the OPF metadata;
    2. a manifest item carrying ``properties="cover-image"`` (EPUB 3);
    3. the OPF guide ``<reference type="cover" .../>``;
    4. filename heuristics (``cover.*``, ``front.*``, …);
    5. the first image referenced by the first spine document.

    SVG cover *wrappers* (EPUB 3) are unwrapped transparently: when a
    candidate resolves to an ``.svg``, the raster it embeds is returned.
    """
    opf_dir = posixpath.dirname(opf_zip_path)
    opf_text = _decode(entries.get(opf_zip_path, b""))
    items, cover_meta_id, guide_href, _ = _parse_opf(opf_text)
    norm_entries = {_norm(name): name for name in entries}

    def locate(href: str) -> str | None:
        return norm_entries.get(_resolve_from(opf_dir, href))

    def as_raster(
        path: str | None, method: str, item_id: str | None = None
    ) -> CoverInfo | None:
        """Return a CoverInfo for a path, unwrapping SVG wrappers if needed."""
        if not path:
            return None
        if is_raster_image(path):
            return CoverInfo(path, item_id, method)
        if path.lower().endswith(".svg"):
            embedded = _image_inside_svg(
                entries.get(path, b""), posixpath.dirname(path), norm_entries
            )
            if embedded:
                return CoverInfo(embedded, item_id, f"{method} (via SVG wrapper)")
        return None

    # 1. Declared via <meta name="cover">.
    if cover_meta_id:
        for item in items:
            if item.item_id == cover_meta_id:
                return as_raster(locate(item.href), 'OPF <meta name="cover">', item.item_id)

    # 2. EPUB 3 manifest properties.
    for item in items:
        if "cover-image" in (item.properties or "").split():
            found = as_raster(
                locate(item.href), "manifest properties=cover-image", item.item_id
            )
            if found:
                return found

    # 3. OPF guide.
    if guide_href:
        found = as_raster(
            locate(guide_href), "OPF guide <reference type=cover>", None
        )
        if found:
            return found

    # 4. Filename heuristics.
    candidates: list[tuple[int, str]] = []
    for name in entries:
        base = posixpath.basename(name).lower()
        if not is_raster_image(name):
            continue
        for rank, hint in enumerate(_COVER_NAME_HINTS):
            if base.startswith(hint):
                candidates.append((rank, name))
                break
    if candidates:
        candidates.sort(key=lambda pair: (pair[0], pair[1]))
        path = candidates[0][1]
        return CoverInfo(
            path, _item_id_for_href(items, path, opf_dir), "filename heuristic"
        )

    # 5. Images inside the first spine documents (last resort). A document
    #    is only treated as a cover page when its name looks like a cover
    #    OR it embeds exactly one image — this avoids mistaking the first
    #    illustration of a chapter for the book cover.
    for document in _spine_documents(opf_text, opf_dir, entries):
        doc_base = posixpath.basename(document).lower()
        looks_like_cover_page = doc_base.startswith(("cover", "title", "front"))
        doc_text = _decode(entries.get(document, b""))
        doc_dir = posixpath.dirname(document)
        referenced: list[str] = []
        for match in re.finditer(
            r"""(?:src|href)\s*=\s*["']([^"']+\.(?:png|jpe?g|gif|webp|bmp))["']""",
            doc_text,
            flags=re.IGNORECASE,
        ):
            path = _norm(_resolve_from(doc_dir, match.group(1)))
            real = norm_entries.get(path)
            if real and is_raster_image(real):
                referenced.append(real)
        if referenced and (looks_like_cover_page or len(referenced) == 1):
            return CoverInfo(referenced[0], None, "first image in first spine page")

    return None


def _item_id_for_href(
    items: list[_ManifestItem], zip_path: str, opf_dir: str
) -> str | None:
    """Find the manifest item id whose href resolves to ``zip_path``."""
    for item in items:
        if _resolve_from(opf_dir, item.href) == _norm(zip_path):
            return item.item_id
    return None


# ---------------------------------------------------------------------------
# Blurred-cover compositing
# ---------------------------------------------------------------------------


def build_blurred_cover(
    data: bytes, preset: DevicePreset, quality: int
) -> tuple[bytes, int, int]:
    """Build the 3:4 cover composite; returns ``(jpeg_bytes, width, height)``.

    * Overlay    — the original cover scaled to *fit* (contain) the canvas.
    * Background — the cover scaled to *fill* the canvas, center-cropped,
      then Gaussian-blurred with a radius that scales with the canvas so
      the blur looks the same on every device.
    """
    canvas_width, canvas_height = preset.cover_canvas
    try:
        with Image.open(io.BytesIO(data)) as src:
            src = ImageOps.exif_transpose(src)
            src.load()
            cover = flatten_to_rgb(src)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Cover image could not be decoded") from exc

    # Overlay: original cover, contained and centered (never distorted).
    overlay = cover.copy()
    overlay.thumbnail((canvas_width, canvas_height), Image.Resampling.LANCZOS)

    # Background: fill + center-crop, then blur.
    scale = max(canvas_width / cover.width, canvas_height / cover.height)
    filled = cover.resize(
        (round(cover.width * scale), round(cover.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (filled.width - canvas_width) // 2
    top = (filled.height - canvas_height) // 2
    background = filled.crop((left, top, left + canvas_width, top + canvas_height))

    radius = max(
        _MIN_BLUR_RADIUS,
        round(_REFERENCE_BLUR_RADIUS * canvas_height / _REFERENCE_CANVAS_HEIGHT),
    )
    background = background.filter(ImageFilter.GaussianBlur(radius))

    # Composite: blurred backdrop + sharp, centered cover on top.
    canvas = background.copy()
    canvas.paste(
        overlay,
        ((canvas_width - overlay.width) // 2, (canvas_height - overlay.height) // 2),
    )

    quality = max(1, min(100, int(quality)))
    buffer = io.BytesIO()
    canvas.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=False,
        subsampling=2 if quality <= 90 else 0,
    )
    return buffer.getvalue(), canvas_width, canvas_height


# ---------------------------------------------------------------------------
# Archive surgery (rename cover, rewrite references)
# ---------------------------------------------------------------------------


def apply_cover(
    entries: dict[str, bytes],
    opf_zip_path: str,
    cover: CoverInfo,
    new_data: bytes,
) -> str:
    """Swap the cover entry and update every reference to it.

    Returns the archive path of the new cover file.
    """
    opf_dir = posixpath.dirname(opf_zip_path)
    old_zip_path = cover.zip_path
    old_rel = posixpath.relpath(old_zip_path, opf_dir)
    old_base = posixpath.basename(old_zip_path)

    stem, _ = posixpath.splitext(old_base)
    new_base = f"{stem}_optimized.jpg"
    new_zip_path = posixpath.join(posixpath.dirname(old_zip_path), new_base)
    new_rel = posixpath.relpath(new_zip_path, opf_dir)

    # Register the new entry, drop the old one.
    entries[new_zip_path] = new_data
    del entries[old_zip_path]

    # Rewrite the OPF manifest item (href + media-type) and make sure the
    # <meta name="cover"> declaration exists.
    if opf_zip_path in entries:
        opf_text = _decode(entries[opf_zip_path])
        opf_text = _update_manifest_item(opf_text, cover.item_id, new_rel)
        opf_text = _ensure_cover_meta(opf_text, cover.item_id, new_rel, opf_dir)
        entries[opf_zip_path] = opf_text.encode("utf-8")

    # Rewrite every text entry referencing the old filename.
    _rewrite_text_references(entries, old_rel, new_rel, old_base, new_base)

    return new_zip_path


def _update_manifest_item(opf_text: str, item_id: str | None, new_rel: str) -> str:
    """Point the cover's manifest <item> at the new file with a JPEG media type."""
    if not item_id:
        return opf_text

    id_marker = f'id="{item_id}"'
    encoded_new_rel = urllib.parse.quote(new_rel, safe="/")

    def _replace(tag: str) -> str:
        if id_marker not in tag:
            return tag
        tag = _replace_attr_value(tag, "href", encoded_new_rel)
        return _replace_attr_value(tag, "media-type", "image/jpeg")

    return re.sub(r"<item\b[^>]*>", lambda m: _replace(m.group(0)), opf_text, flags=re.IGNORECASE)


def _replace_attr_value(tag: str, attr: str, new_value: str) -> str:
    """Replace one attribute's value inside a tag, tolerating quote styles."""
    match = re.search(
        rf'({attr}\s*=\s*["\'])([^"\']*)(["\'])', tag, flags=re.IGNORECASE
    )
    if not match:
        return tag
    return tag[: match.start()] + match.group(1) + new_value + match.group(3) + tag[match.end():]


def update_manifest_media_type(
    opf_text: str, opf_dir: str, zip_path: str, media_type: str
) -> str:
    """Update the media-type of every manifest item resolving to ``zip_path``.

    Keeps the OPF honest when an image's *content* format changes (e.g. a
    PNG re-encoded as JPEG) even though the archive entry keeps its
    original filename to avoid rewriting every reference to it.
    """
    target = _norm(zip_path)

    def _replace(tag: str) -> str:
        href = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not href or _resolve_from(opf_dir, href.group(1)) != target:
            return tag
        return _replace_attr_value(tag, "media-type", media_type)

    return re.sub(
        r"<item\b[^>]*>", lambda m: _replace(m.group(0)), opf_text, flags=re.IGNORECASE
    )


def _ensure_cover_meta(
    opf_text: str, item_id: str | None, new_rel: str, opf_dir: str
) -> str:
    """Insert ``<meta name="cover" .../>`` when the OPF does not declare one."""
    if re.search(r"<meta\b[^>]*name\s*=\s*[\"']cover[\"']", opf_text, re.IGNORECASE):
        return opf_text

    # The id may be unknown (heuristic discovery) — derive it from the href.
    cover_id = item_id
    if not cover_id:
        target = _norm(posixpath.join(opf_dir, new_rel))
        for match in re.finditer(r"<item\b[^>]*>", opf_text, re.IGNORECASE):
            tag = match.group(0)
            href = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if href and _resolve_from(opf_dir, href.group(1)) == target:
                id_match = re.search(r'id\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
                cover_id = id_match.group(1) if id_match else None
                break

    if not cover_id:
        return opf_text

    meta_tag = f'<meta name="cover" content="{cover_id}"/>'
    closing = re.search(r"</metadata\s*>", opf_text, re.IGNORECASE)
    if closing:
        return opf_text[: closing.start()] + meta_tag + opf_text[closing.start() :]
    opening = re.search(r"<metadata\b[^>]*>", opf_text, re.IGNORECASE)
    if opening:
        return opf_text[: opening.end()] + meta_tag + opf_text[opening.end() :]
    return opf_text


def _rewrite_text_references(
    entries: dict[str, bytes],
    old_rel: str,
    new_rel: str,
    old_base: str,
    new_base: str,
) -> None:
    """Rewrite cover references (relative path *and* bare filename) in text."""
    tokens = (
        (old_rel, new_rel),
        (urllib.parse.quote(old_rel, safe="/"), urllib.parse.quote(new_rel, safe="/")),
        (old_base, new_base),
        (urllib.parse.quote(old_base, safe="/"), urllib.parse.quote(new_base, safe="/")),
    )

    for name in list(entries):
        if not is_text_entry(name):
            continue
        text = _decode(entries[name])
        if not text:
            continue
        changed = False
        for old_token, new_token in tokens:
            if old_token and old_token != new_token and old_token in text:
                text = text.replace(old_token, new_token)
                changed = True
        if changed:
            entries[name] = text.encode("utf-8")


def _decode(data: bytes) -> str:
    """Decode entry bytes to text, tolerating BOMs and binary content."""
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return ""
