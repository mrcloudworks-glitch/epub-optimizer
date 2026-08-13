"""Kindle EPUB Optimizer — a cross-platform PySide6 GUI that optimizes
EPUB files for Amazon Kindle 10th-generation e-readers.

The package is split into two layers:

* :mod:`epub_optimizer.core` — the GUI-independent optimization pipeline
  (device presets, image processing, cover compositing, ZIP repacking).
  It has no Qt dependency, so it can be unit-tested headlessly.
* :mod:`epub_optimizer.ui`   — the PySide6 desktop application (main
  window, drag-and-drop zone, background worker thread, theming).
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
