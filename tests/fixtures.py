"""Fixtures: build realistic test EPUBs (and their images) on the fly."""

from __future__ import annotations

import io
import zipfile

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Image generation helpers
# ---------------------------------------------------------------------------


def _gradient_image(size: tuple[int, int], seed: int = 0) -> bytes:
    """A deterministic gradient+noise RGB image (compresses realistically)."""
    width, height = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    rng = __import__("random").Random(seed)
    for y in range(height):
        base = int(255 * y / max(height - 1, 1))
        draw.line(
            [(0, y), (width, y)],
            fill=(base, 255 - base, (base * 3) % 256),
        )
    pixels = img.load()
    for _ in range(max(1, width * height // 40)):
        x, y = rng.randrange(width), rng.randrange(height)
        r, g, b = pixels[x, y]
        delta = rng.randint(-18, 18)
        pixels[x, y] = (
            max(0, min(255, r + delta)),
            max(0, min(255, g + delta)),
            max(0, min(255, b + delta)),
        )
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _cover_image(size: tuple[int, int] = (1200, 1800), seed: int = 7) -> bytes:
    """A book-cover-ish portrait image with a title band."""
    width, height = size
    img = Image.new("RGB", size, (24, 30, 60))
    draw = ImageDraw.Draw(img)
    rng = __import__("random").Random(seed)
    # vertical gradient wash
    for y in range(height):
        t = y / max(height - 1, 1)
        color = (
            int(24 + 140 * t),
            int(30 + 60 * t),
            int(60 + 120 * t),
        )
        draw.line([(0, y), (width, y)], fill=color)
    # decorative stripes
    for _ in range(12):
        x = rng.randrange(width)
        draw.rectangle([x, 0, x + rng.randint(4, 40), height], fill=(rng.randint(0, 255),) * 3)
    # title band
    band_h = max(40, height // 10)
    draw.rectangle([0, height // 2 - band_h // 2, width, height // 2 + band_h // 2], fill=(10, 10, 14))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _icon_image(size: tuple[int, int] = (48, 48), seed: int = 3) -> bytes:
    """A small RGBA PNG (typical favicon/decoration sprite)."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rng = __import__("random").Random(seed)
    for _ in range(30):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        r = rng.randint(2, 8)
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255), rng.randint(90, 220)),
        )
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# EPUB builder
# ---------------------------------------------------------------------------


def build_epub(
    dest_path: str,
    *,
    include_cover: bool = True,
    cover_size: tuple[int, int] = (1200, 1800),
    epub3: bool = False,
) -> None:
    """Write a small but structurally valid EPUB to ``dest_path``.

    The generated book contains:
    * ``mimetype`` (stored first, uncompressed — per OCF),
    * ``META-INF/container.xml`` pointing at ``OEBPS/content.opf``,
    * a title page that embeds the cover via ``<img>``,
    * a chapter with a large inline PNG and a tiny RGBA icon,
    * a CSS file referencing one of the images,
    * the images themselves.
    """
    manifest = [
        '<item id="css" href="styles.css" media-type="text/css"/>',
        '<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="photo1" href="images/photo1.png" media-type="image/png"/>',
        '<item id="icon" href="images/icon.png" media-type="image/png"/>',
    ]
    if include_cover:
        manifest.append(
            '<item id="cover-image" href="images/cover.png" media-type="image/png"/>'
        )

    metadata = (
        '    <meta name="cover" content="cover-image"/>' if include_cover else ""
    )

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:creator>Fixture Generator</dc:creator>
    <dc:identifier id="uid">urn:uuid:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</dc:identifier>
    <dc:language>en</dc:language>
{metadata}
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    {chr(10).join(manifest)}
  </manifest>
  <spine toc="ncx">
    <itemref idref="titlepage"/>
    <itemref idref="chapter1"/>
  </spine>
  <guide>
    <reference type="cover" title="Cover" href="titlepage.xhtml"/>
  </guide>
</package>
"""
    if include_cover and epub3:
        # mark the item with properties="cover-image" for the EPUB 3 variant
        opf = opf.replace(
            '<item id="cover-image" href="images/cover.png" media-type="image/png"/>',
            '<item id="cover-image" href="images/cover.png" media-type="image/png" properties="cover-image"/>',
        )

    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    titlepage = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Test Book</title></head>
<body>
  <div>
    <img src="images/cover.png" alt="Cover"/>
  </div>
</body>
</html>
"""

    chapter = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
  <h1>Chapter 1</h1>
  <p>A picture is worth a thousand words:</p>
  <img src="images/photo1.png" alt="Photo"/>
  <img src="images/icon.png" alt="Icon" class="icon"/>
</body>
</html>
"""

    css = """
body { font-family: serif; }
img.icon { width: 24px; height: 24px; }
.hero { background-image: url('images/photo1.png'); }
"""

    ncx = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"/></head>
  <docTitle><text>Test Book</text></docTitle>
  <navMap>
    <navPoint id="np1" playOrder="1"><navLabel><text>Chapter 1</text></navLabel><content src="chapter1.xhtml"/></navPoint>
  </navMap>
</ncx>
"""

    entries: list[tuple[str, bytes, bool]] = [
        ("mimetype", b"application/epub+zip", False),
        ("META-INF/container.xml", container.encode("utf-8"), True),
        ("OEBPS/content.opf", opf.encode("utf-8"), True),
        ("OEBPS/toc.ncx", ncx.encode("utf-8"), True),
        ("OEBPS/titlepage.xhtml", titlepage.encode("utf-8"), True),
        ("OEBPS/chapter1.xhtml", chapter.encode("utf-8"), True),
        ("OEBPS/styles.css", css.encode("utf-8"), True),
        ("OEBPS/images/photo1.png", _gradient_image((1600, 900), seed=11), True),
        ("OEBPS/images/icon.png", _icon_image(), True),
    ]
    if include_cover:
        entries.append(("OEBPS/images/cover.png", _cover_image(cover_size), True))

    with zipfile.ZipFile(
        dest_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for name, data, deflate in entries:
            archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED)
