"""GUI smoke tests (headless, using the offscreen Qt platform).

These verify the window constructs, the theme toggles, the file queue
logic and that a batch run through the worker produces an output file.
They are skipped automatically when PySide6 is not installed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication

from epub_optimizer.core.devices import DEVICE_PRESETS
from epub_optimizer.ui.main_window import MainWindow
from epub_optimizer.ui.theme import apply_theme
from tests.fixtures import build_epub


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app, dark=True)
    yield app


@pytest.fixture()
def window(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    win.show()
    yield win
    win.close()


def test_window_constructs(window) -> None:
    assert window.windowTitle() == "Kindle EPUB Optimizer"
    assert window.device_combo.count() == len(DEVICE_PRESETS) == 2
    assert window.quality_slider.value() == 80
    assert window.blur_checkbox.isChecked() is True
    assert window.start_button.isEnabled() is False  # empty queue


def test_theme_toggle(window) -> None:
    initial = window.dark
    window._toggle_theme()
    assert window.dark is not initial
    window._toggle_theme()
    assert window.dark is initial


def test_scroll_area_prevents_clipping(window, qapp) -> None:
    """Small windows must scroll on BOTH axes instead of cutting text."""
    scroll = window._scroll_area
    assert scroll is not None
    assert scroll.widgetResizable() is True
    assert (
        scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert (
        scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )

    # The scroll content has a fixed minimum width so text is never squeezed.
    assert scroll.widget().minimumWidth() >= 680

    # Shrink the window to its minimum: both scrollbars must become active
    # (maximum > 0) so every control stays reachable.
    window.resize(1, 1)  # clamped to the window's minimum size
    qapp.processEvents()
    assert window.width() <= 700, f"window did not shrink: {window.width()}"
    assert scroll.verticalScrollBar().maximum() > 0, (
        "vertical scrollbar missing on a small window"
    )
    assert scroll.horizontalScrollBar().maximum() > 0, (
        "horizontal scrollbar missing on a small window"
    )

    # Enlarge the window again: scrollbars go away (content fills the space).
    window.resize(1200, 900)
    qapp.processEvents()
    assert scroll.verticalScrollBar().maximum() == 0
    assert scroll.horizontalScrollBar().maximum() == 0


def test_add_remove_clear_files(window, tmp_path) -> None:
    epub = tmp_path / "book.epub"
    build_epub(str(epub))

    window.add_files([str(epub)])
    assert len(window._files) == 1
    assert window.start_button.isEnabled() is True

    # duplicates are ignored
    window.add_files([str(epub)])
    assert len(window._files) == 1

    # non-epub files are rejected
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    window.add_files([str(txt)])
    assert len(window._files) == 1

    window._clear_files()
    assert len(window._files) == 0
    assert window.start_button.isEnabled() is False


def test_batch_run_through_worker(window, qapp, tmp_path) -> None:
    epub = tmp_path / "book.epub"
    build_epub(str(epub))
    window.add_files([str(epub)])

    window._start_batch()
    assert window.worker is not None

    # Run the event loop until the worker thread has exited (with a safety
    # net in case something goes wrong).
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(
        lambda: loop.quit()
        if window.worker is None or not window.worker.isRunning()
        else None
    )
    poll.start(25)
    QTimer.singleShot(60000, loop.quit)
    loop.exec()
    poll.stop()

    # Deliver the signals the worker queued during its run.
    for _ in range(5):
        qapp.processEvents()

    output = tmp_path / "book_optimized.epub"
    assert output.exists() and output.stat().st_size > 0
    assert window.start_button.isEnabled() is True  # re-enabled after run


def test_replacement_cover_batch_run(window, qapp, tmp_path) -> None:
    """Setting a replacement cover passes it through to the worker."""
    import zipfile

    from PIL import Image

    epub = tmp_path / "book.epub"
    build_epub(str(epub))
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (900, 1200), (12, 140, 90)).save(str(replacement), format="PNG")

    window.replacement_edit.setText(str(replacement))
    window.add_files([str(epub)])
    assert window.start_button.isEnabled() is True

    window._start_batch()
    assert window.worker is not None
    assert window.worker._replacement_cover_path == str(replacement)

    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(
        lambda: loop.quit()
        if window.worker is None or not window.worker.isRunning()
        else None
    )
    poll.start(25)
    QTimer.singleShot(60000, loop.quit)
    loop.exec()
    poll.stop()
    for _ in range(5):
        qapp.processEvents()

    output = tmp_path / "book_optimized.epub"
    assert output.exists() and output.stat().st_size > 0
    with zipfile.ZipFile(str(output)) as archive:
        names = archive.namelist()
    assert any(n.endswith("cover_optimized.jpg") for n in names), (
        "replacement cover must be composited into the output"
    )

    # Clearing the replacement resets the field.
    window._reset_replacement_cover()
    assert window.replacement_edit.text() == ""


def test_same_stem_batch_collision_is_unique(window, tmp_path) -> None:
    """Two sources sharing a stem must not clobber each other's output."""
    from epub_optimizer.ui.worker import OptimizeWorker

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    epub_a = dir_a / "book.epub"
    epub_b = dir_b / "book.epub"
    build_epub(str(epub_a))
    build_epub(str(epub_b))
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    worker = OptimizeWorker(
        files=[str(epub_a), str(epub_b)],
        device_key="kindle_10th_basic",
        quality=80,
        blur_cover=True,
        output_dir=str(out_dir),
    )
    assert worker._output_path_for(str(epub_a)).endswith("book_optimized.epub")
    second = worker._output_path_for(str(epub_b))
    assert second != str(out_dir / "book_optimized.epub")
    assert second.endswith("book_optimized_2.epub")
    # Even a duplicate path inside the same batch must not overwrite.
    assert worker._output_path_for(str(epub_a)).endswith("book_optimized_3.epub")

    # A NEW batch (fresh worker) re-optimizing the same source overwrites
    # the deterministic <stem>_optimized.epub name.
    rerun = OptimizeWorker(
        files=[str(epub_a)],
        device_key="kindle_10th_basic",
        quality=80,
        blur_cover=True,
        output_dir=str(out_dir),
    )
    assert rerun._output_path_for(str(epub_a)).endswith("book_optimized.epub")
