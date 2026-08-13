# Kindle EPUB Optimizer

A cross-platform desktop application (Python 3 + **PySide6** + **Pillow**)
that optimizes EPUB files specifically for **Amazon Kindle 10th Generation**
e-readers: images are downscaled and re-encoded for the device's screen, the
cover is rebuilt on a strict **3:4 canvas** with blurred sidebars (no more
pillarboxing), and the archive is re-packed with maximum ZIP compression.

```
Input EPUB (e.g. 1.2 MB)  ──►  600×800 / 1072×1448 images, 3:4 blurred
                                cover, ZIP level 9  ──►  often 60–85% smaller
```

---

## Features

### 1. Asset & image optimization
- Unpacks the `.epub` (a ZIP archive) and parses its contents in memory.
- Downscales every inline content image with Lanczos resampling so no
  dimension exceeds the target device's screen resolution.
- Converts large images to **optimized baseline JPEG** (quality 50–100%,
  default **80%**) — baseline (non-progressive) JPEGs for maximum Kindle
  compatibility. Transparency is flattened onto white.
- Never *grows* the book: tiny PNG icons that would get bigger as JPEG are
  kept losslessly-optimized in their original format.
- Keeps the OPF manifest honest: when an image's content format changes,
  the manifest `media-type` is updated accordingly.
- Re-packs the EPUB with **ZIP compression level 9**; the OCF `mimetype`
  entry stays first and uncompressed, exactly as readers expect.

### 2. Smart cover processing (3:4 aspect ratio)
Kindle renders covers full-screen at a strict **3:4** ratio. Covers that
aren't 3:4 get pillarboxed/letterboxed with a plain background. The app
fixes that automatically:

1. Computes a strict 3:4 canvas for the target device
   (600×800 Basic, 1072×1429 Paperwhite).
2. Scales the original cover to **fill** the canvas, center-crops it and
   applies a **Gaussian blur** (radius ~18 px at 800 px height, scaled so
   the visual blur stays constant on bigger canvases).
3. Overlays the original, uncropped cover, scaled to **fit** the canvas,
   perfectly centered.
4. Replaces the cover asset, rewrites the OPF manifest (`href`,
   `media-type`, `<meta name="cover">`) and every textual reference
   (XHTML, CSS, NCX, SVG) to the old file.

Cover detection is robust: `<meta name="cover">`, EPUB 3
`properties="cover-image"`, OPF guide, filename heuristics, and SVG cover
wrappers are all unwrapped and recognized. Toggle it off to keep covers
untouched (they are still downscaled/compressed like any image).

### 2b. Replace the original cover
Pick any image file (PNG/JPEG/WebP/GIF/BMP/TIFF) in the **"Replace original
cover"** field — it is applied to every EPUB in the batch:

- The chosen image **replaces the book's original cover**, then the 3:4
  blurred-sidebar treatment above is applied **on top of the replacement**
  (sharp replacement cover centered over its own blurred fill — no white
  pillarbox/letterbox bars).
- If a book has **no detectable cover**, the replacement is injected: a new
  image entry is added, registered in the OPF manifest, declared via
  `<meta name="cover">`, and shown on the first spine page.
- Works with the blur toggle on *or* off (blur off = the replacement is
  optimized like any other image, no compositing).
- The original EPUB file is never modified; the composited result lands in
  the output file as usual.

### 3. PySide6 desktop GUI
- Modern dark **and** light themes (toggle in the header, remembered).
- **Drag-and-drop zone** + file browser; single or **batch** processing.
- Device dropdown: `Kindle 10th Gen Basic (600×800)` and
  `Kindle 10th Gen Paperwhite (1072×1448)`.
- **Image quality slider** (50–100%, default 80%) and a
  **"Blurred sidebars for cover"** toggle.
- **"Replace original cover"** picker: choose an image that replaces every
  book's cover (still blurred into a 3:4 canvas), or clear it to keep
  original covers.
- Non-blocking **`QThread` worker** — the UI stays responsive; a progress
  bar and a timestamped, color-coded log console show real-time status.
- Cancel mid-batch (partial output files are cleaned up).
- **"Save To"** output folder picker (default: next to the source as
  `<name>_optimized.epub`), plus "Open Output Folder" after a run.

---

## Requirements

- Python **3.9+** (developed on 3.11)
- `PySide6 >= 6.5`
- `Pillow >= 10.0`

## Installation & running

```bash
pip install -r requirements.txt
python main.py                 # or:  python -m epub_optimizer
```

Tested on Linux; the code is cross-platform (Windows/macOS need nothing
extra — Qt ships its own platform plugins).

### Packaging as a standalone app (PyInstaller)

```bash
pip install pyinstaller
pyinstaller --windowed --name "KindleEpubOptimizer" \
    --collect-all PySide6 main.py
```

The single executable in `dist/` runs without a Python installation.

---

## How it works

| Stage | What happens |
|-------|--------------|
| Read  | The EPUB is opened as a ZIP; every entry is read into memory (no temp files). |
| OPF   | The package document is located via `META-INF/container.xml` (with a `.opf` fallback). |
| Cover | Detected, rebuilt on a 3:4 canvas with blurred fill, and all references are rewritten (only when the toggle is on). A user-picked replacement image is swapped in first — or injected when the book has no cover. |
| Images| Each raster image is orientation-corrected, downscaled to the device, flattened, and re-encoded as JPEG when that shrinks it. |
| Write | Atomic re-pack (temp file + rename): `mimetype` stored uncompressed first, everything else deflated at level 9. |

### Code layout

```
epub_optimizer/
├── core/                      # GUI-independent pipeline (no Qt imports)
│   ├── devices.py             #   Kindle device presets & 3:4 canvas math
│   ├── image_processor.py     #   Pillow: downscale, flatten, JPEG encode
│   ├── cover_processor.py     #   cover detection, blur composite, OPF surgery, cover injection
│   ├── optimizer.py           #   EpubOptimizer orchestrator (progress/log callbacks)
│   └── exceptions.py          #   error hierarchy incl. OptimizerCancelled
├── ui/                        # PySide6 application layer
│   ├── theme.py               #   dark/light QSS + palettes
│   ├── drop_zone.py           #   drag-and-drop widget
│   ├── worker.py              #   OptimizeWorker(QThread) + output naming rules
│   └── main_window.py         #   main window, settings persistence, log console
└── app.py                     #   QApplication bootstrap
main.py                        # entry point
tests/                         # pytest suite (core + headless GUI)
```

The core never imports Qt: the GUI worker passes plain callbacks
(`progress_cb`, `log_cb`, `cancel_check`) into `EpubOptimizer`, so the
same pipeline could power a future CLI.

---

## Testing

```bash
pip install pytest ruff
pytest tests/                  # 16 tests: pipeline, covers, compression, GUI
ruff check epub_optimizer tests main.py
```

The GUI tests run headless on the Qt **offscreen** platform (no display
needed):

```bash
QT_QPA_PLATFORM=offscreen pytest tests/test_gui.py
```

Test fixtures build realistic EPUBs on the fly (OCF container, OPF with
cover metadata, XHTML pages, CSS, PNG covers/photos/icons) and verify:
device resolution caps, strict-3:4 cover output, stale-reference removal,
manifest media-type updates, `mimetype`-first-stored packing, level-9
deflation, cancellation cleanup, and batch name-collision handling.

---

## Notes & limitations

- Images are only ever **downscaled**, never upscaled; aspect ratios are
  preserved (only the cover canvas adds a blurred fill).
- The original file is never modified — output is always a new
  `_optimized.epub` (or your chosen folder).
- SVG *content* images are not rasterized; only SVG cover wrappers are
  unwrapped to their embedded raster.
- Animated GIFs are kept untouched (Kindle renders the first frame anyway;
  we don't destroy animation for other readers).
- The optimization targets *inline content images*; fonts, audio/video and
  other assets are passed through byte-for-byte, just re-compressed.

---

## License

MIT — use it, fork it, ship it.
