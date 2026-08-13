"""Main application window: file queue, settings, progress and log console."""

from __future__ import annotations

import html
import os
import time

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from epub_optimizer.core.devices import DEFAULT_DEVICE_KEY, DEVICE_PRESETS
from epub_optimizer.ui.drop_zone import DropZone
from epub_optimizer.ui.theme import apply_theme, log_color
from epub_optimizer.ui.worker import OptimizeWorker

#: QSettings keys (persisted between sessions).
_SETTINGS = {
    "device": "device",
    "quality": "quality",
    "blur_cover": "blur_cover",
    "replacement_cover": "replacement_cover",
    "output_dir": "output_dir",
    "dark_mode": "dark_mode",
}

#: Image file filters for the replacement-cover picker.
_IMAGE_FILTER = (
    "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.tif *.tiff);;All files (*)"
)

_QUALITY_MIN, _QUALITY_MAX, _QUALITY_DEFAULT = 50, 100, 80


class MainWindow(QMainWindow):
    """The application's single main window."""

    def __init__(self, settings: QSettings) -> None:
        super().__init__()
        self.settings = settings
        self.worker: OptimizeWorker | None = None
        self.dark = bool(settings.value(_SETTINGS["dark_mode"], True, type=bool))
        self._last_output_dir: str | None = None
        self._files: list[str] = []

        self.setWindowTitle("Kindle EPUB Optimizer")
        self.resize(1080, 720)
        # Small windows are fine: the whole layout lives inside a QScrollArea,
        # so vertical AND horizontal scrollbars appear instead of cutting text.
        self.setMinimumSize(560, 420)

        self._build_ui()
        self._restore_settings()
        self._refresh_theme_ui()
        self._update_actions()
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # The whole interface is built into this widget, which is then placed
        # inside a QScrollArea (see below) so that on small screens neither
        # horizontal nor vertical content is ever clipped.
        central = QWidget()
        central.setObjectName("scrollContent")
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        # A sensible minimum content width: below this the window scrolls
        # horizontally instead of squeezing/cutting the settings text.
        central.setMinimumWidth(680)

        root.addLayout(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        root.addLayout(self._build_progress_row())

        # Scroll area with BOTH axes: scrollbars appear as needed when the
        # window is smaller than the content, and disappear again when the
        # window is large enough (widgetResizable stretches the content to
        # fill the available space whenever possible).
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setWidget(central)
        self.setCentralWidget(self._scroll_area)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("Kindle EPUB Optimizer")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        subtitle = QLabel(
            "Optimize EPUBs for Kindle 10th Gen — downscaled images, "
            "blurred 3:4 covers, maximum ZIP compression"
        )
        subtitle.setStyleSheet("font-size: 11px;")
        subtitle.setWordWrap(True)  # wrap on narrow windows instead of clipping

        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        self.theme_button = QPushButton()
        self.theme_button.setFixedWidth(110)
        self.theme_button.clicked.connect(self._toggle_theme)

        header.addLayout(title_block)
        header.addStretch(1)
        header.addWidget(self.theme_button, alignment=Qt.AlignmentFlag.AlignTop)
        return header

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- Drop zone + file list -----------------------------------
        self.drop_zone = DropZone()
        self.drop_zone.files_selected.connect(self.add_files)
        layout.addWidget(self.drop_zone)

        list_header = QHBoxLayout()
        self.files_label = QLabel("Files (0)")
        self.files_label.setStyleSheet("font-weight: 600;")
        list_header.addWidget(self.files_label)
        list_header.addStretch(1)
        self.add_button = QPushButton("Add Files…")
        self.remove_button = QPushButton("Remove")
        self.clear_button = QPushButton("Clear")
        self.add_button.clicked.connect(self._browse_files)
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button.clicked.connect(self._clear_files)
        for button in (self.add_button, self.remove_button, self.clear_button):
            list_header.addWidget(button)
        layout.addLayout(list_header)

        self.files_list = QListWidget()
        self.files_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self.files_list.setMaximumHeight(170)
        layout.addWidget(self.files_list, stretch=1)

        # --- Settings --------------------------------------------------
        settings_group = QGroupBox("Target device & image quality")
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setSpacing(10)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Device"))
        self.device_combo = QComboBox()
        for preset in DEVICE_PRESETS:
            self.device_combo.addItem(preset.name, userData=preset.key)
        device_row.addWidget(self.device_combo, stretch=1)
        settings_layout.addLayout(device_row)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Image quality"))
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(_QUALITY_MIN, _QUALITY_MAX)
        self.quality_slider.setValue(_QUALITY_DEFAULT)
        self.quality_value = QLabel(f"{_QUALITY_DEFAULT}%")
        self.quality_value.setFixedWidth(44)
        self.quality_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.quality_slider.valueChanged.connect(
            lambda value: self.quality_value.setText(f"{value}%")
        )
        quality_row.addWidget(self.quality_slider, stretch=1)
        quality_row.addWidget(self.quality_value)
        settings_layout.addLayout(quality_row)

        self.blur_checkbox = QCheckBox(
            "Blurred sidebars for cover (3:4 fill, no pillarboxing)"
        )
        self.blur_checkbox.setChecked(True)
        settings_layout.addWidget(self.blur_checkbox)
        layout.addWidget(settings_group)

        # --- Replace cover -------------------------------------------------
        replacement_group = QGroupBox("Replace original cover (optional)")
        replacement_row = QHBoxLayout(replacement_group)
        self.replacement_edit = QLineEdit()
        self.replacement_edit.setReadOnly(True)
        self.replacement_edit.setPlaceholderText("None — keep the book's cover")
        self.replacement_edit.setToolTip(
            "Applied to every EPUB in the queue: the chosen image replaces "
            "the book's original cover, then the 3:4 blurred-sidebar "
            "treatment is applied to it."
        )
        self.replacement_browse = QPushButton("Browse…")
        self.replacement_clear = QPushButton("None")
        self.replacement_browse.clicked.connect(self._choose_replacement_cover)
        self.replacement_clear.clicked.connect(self._reset_replacement_cover)
        replacement_row.addWidget(self.replacement_edit, stretch=1)
        replacement_row.addWidget(self.replacement_browse)
        replacement_row.addWidget(self.replacement_clear)
        layout.addWidget(replacement_group)

        # --- Output folder ---------------------------------------------
        output_group = QGroupBox("Save to")
        output_row = QHBoxLayout(output_group)
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText(
            "Same folder as source — <name>_optimized.epub"
        )
        self.output_browse = QPushButton("Browse…")
        self.output_clear = QPushButton("Auto")
        self.output_browse.clicked.connect(self._choose_output_dir)
        self.output_clear.clicked.connect(self._reset_output_dir)
        output_row.addWidget(self.output_edit, stretch=1)
        output_row.addWidget(self.output_browse)
        output_row.addWidget(self.output_clear)
        layout.addWidget(output_group)

        # --- Actions ----------------------------------------------------
        actions = QHBoxLayout()
        self.start_button = QPushButton("Optimize EPUBs")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setMinimumHeight(36)
        self.start_button.clicked.connect(self._start_batch)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_batch)
        self.open_folder_button = QPushButton("Open Output Folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._open_output_folder)
        actions.addWidget(self.start_button, stretch=2)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.open_folder_button, stretch=1)
        layout.addLayout(actions)

        return panel

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        log_header = QHBoxLayout()
        label = QLabel("Log")
        label.setStyleSheet("font-weight: 600;")
        log_header.addWidget(label)
        log_header.addStretch(1)
        clear_log = QPushButton("Clear log")
        clear_log.clicked.connect(lambda: self.log_console.clear())
        log_header.addWidget(clear_log)
        layout.addLayout(log_header)

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumBlockCount(5000)  # keep memory in check
        layout.addWidget(self.log_console, stretch=1)
        return panel

    def _build_progress_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.status_label = QLabel("Idle")
        self.status_label.setMinimumWidth(200)
        row.addWidget(self.progress_bar, stretch=1)
        row.addWidget(self.status_label)
        return row

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _restore_settings(self) -> None:
        device_key = self.settings.value(_SETTINGS["device"], DEFAULT_DEVICE_KEY, type=str)
        index = self.device_combo.findData(device_key)
        self.device_combo.setCurrentIndex(max(0, index))
        self.quality_slider.setValue(
            self.settings.value(_SETTINGS["quality"], _QUALITY_DEFAULT, type=int)
        )
        self.blur_checkbox.setChecked(
            self.settings.value(_SETTINGS["blur_cover"], True, type=bool)
        )
        replacement = self.settings.value(
            _SETTINGS["replacement_cover"], "", type=str
        )
        if replacement and os.path.isfile(replacement):
            self.replacement_edit.setText(replacement)
        output_dir = self.settings.value(_SETTINGS["output_dir"], "", type=str)
        if output_dir:
            self.output_edit.setText(output_dir)

    def _save_settings(self) -> None:
        self.settings.setValue(_SETTINGS["device"], self.device_combo.currentData())
        self.settings.setValue(_SETTINGS["quality"], self.quality_slider.value())
        self.settings.setValue(
            _SETTINGS["blur_cover"], self.blur_checkbox.isChecked()
        )
        self.settings.setValue(
            _SETTINGS["replacement_cover"], self.replacement_edit.text()
        )
        self.settings.setValue(_SETTINGS["output_dir"], self.output_edit.text())
        self.settings.setValue(_SETTINGS["dark_mode"], self.dark)

    def closeEvent(self, event) -> None:
        self._save_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # File queue
    # ------------------------------------------------------------------

    def add_files(self, paths: list[str]) -> None:
        """Add valid EPUB paths to the queue (deduplicated)."""
        added = 0
        for raw in paths:
            path = os.path.abspath(raw)
            if not path.lower().endswith(".epub"):
                self.append_log(
                    "warning", f"Skipped (not an .epub): {os.path.basename(path)}"
                )
                continue
            if path in self._files:
                continue
            self._files.append(path)
            item = QListWidgetItem(path)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.files_list.addItem(item)
            added += 1
        if added:
            self.append_log("info", f"Added {added} file{'s' if added > 1 else ''}.")
        self.files_label.setText(f"Files ({len(self._files)})")
        self._update_actions()

    def _browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select EPUB files to optimize",
            "",
            "EPUB files (*.epub);;All files (*)",
        )
        if paths:
            self.add_files(paths)

    def _remove_selected(self) -> None:
        selected = [item.data(Qt.ItemDataRole.UserRole) for item in self.files_list.selectedItems()]
        for path in selected:
            if path in self._files:
                self._files.remove(path)
        for item in self.files_list.selectedItems():
            self.files_list.takeItem(self.files_list.row(item))
        self.files_label.setText(f"Files ({len(self._files)})")
        self._update_actions()

    def _clear_files(self) -> None:
        self._files.clear()
        self.files_list.clear()
        self.files_label.setText("Files (0)")
        self._update_actions()

    # ------------------------------------------------------------------
    # Output folder
    # ------------------------------------------------------------------

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select output folder", self.output_edit.text() or ""
        )
        if directory:
            self.output_edit.setText(directory)

    def _reset_output_dir(self) -> None:
        self.output_edit.clear()

    # ------------------------------------------------------------------
    # Replacement cover
    # ------------------------------------------------------------------

    def _choose_replacement_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select replacement cover image", "", _IMAGE_FILTER
        )
        if path:
            self.replacement_edit.setText(path)
            self.append_log("info", f"Replacement cover set: {path}")

    def _reset_replacement_cover(self) -> None:
        self.replacement_edit.clear()
        self.append_log("info", "Replacement cover cleared — original covers kept.")

    def _output_dir_for(self, source: str) -> str:
        chosen = self.output_edit.text().strip()
        return chosen or (os.path.dirname(source) or ".")

    # ------------------------------------------------------------------
    # Batch execution
    # ------------------------------------------------------------------

    def _start_batch(self) -> None:
        if not self._files:
            QMessageBox.information(
                self, "Nothing to do", "Add at least one EPUB file first."
            )
            return
        if self.worker and self.worker.isRunning():
            return

        replacement = self.replacement_edit.text().strip()
        if replacement and not os.path.isfile(replacement):
            QMessageBox.warning(
                self,
                "Replacement cover",
                f"Replacement cover file not found:\n{replacement}",
            )
            return

        self._save_settings()
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting…")
        self._last_output_dir = self._output_dir_for(self._files[0])

        self.worker = OptimizeWorker(
            files=list(self._files),
            device_key=self.device_combo.currentData(),
            quality=self.quality_slider.value(),
            blur_cover=self.blur_checkbox.isChecked(),
            output_dir=self.output_edit.text().strip(),
            replacement_cover_path=replacement or None,
            parent=self,
        )
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_finished.connect(self._on_file_finished)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self.append_log)
        self.worker.batch_finished.connect(self._on_batch_finished)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

        self._set_running(True)

    def _cancel_batch(self) -> None:
        if self.worker:
            self.append_log("warning", "Cancellation requested — finishing current file…")
            self.worker.cancel()
            self.cancel_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_file_started(self, path: str, index: int, total: int) -> None:
        self.append_log(
            "info",
            f"── Processing {index}/{total}: {os.path.basename(path)} "
            f"(→ {self._output_dir_for(path)})",
        )
        self.status_label.setText(f"Processing {index}/{total}…")

    def _on_file_finished(self, path: str, success: bool, summary: str) -> None:
        name = os.path.basename(path)
        if success:
            self.append_log("success", f"{name}: done — {summary}")
            self._last_output_dir = self._output_dir_for(path)
        else:
            self.append_log("error", f"{name}: failed — {summary}")
        self.open_folder_button.setEnabled(True)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

    def _on_batch_finished(self, succeeded: int, failed: int) -> None:
        total = succeeded + failed
        self.status_label.setText(
            f"Finished — {succeeded} succeeded, {failed} failed"
        )
        self.append_log(
            "success",
            f"Batch complete: {succeeded}/{total} files optimized "
            f"({failed} failed).",
        )
        if failed:
            QMessageBox.warning(
                self,
                "Batch finished with errors",
                f"{succeeded} file(s) optimized, {failed} failed.\n"
                "See the log console for details.",
            )

    def _on_cancelled(self) -> None:
        self.append_log("warning", "Batch cancelled by user.")
        self.status_label.setText("Cancelled")
        self.progress_bar.setValue(0)

    def _on_worker_finished(self) -> None:
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        """Enable/disable controls while a batch is running."""
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.drop_zone.setEnabled(not running)
        self.files_list.setEnabled(not running)
        self.add_button.setEnabled(not running)
        self.remove_button.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.device_combo.setEnabled(not running)
        self.quality_slider.setEnabled(not running)
        self.blur_checkbox.setEnabled(not running)
        self.replacement_browse.setEnabled(not running)
        self.replacement_clear.setEnabled(not running)
        self.output_browse.setEnabled(not running)
        self.output_clear.setEnabled(not running)

    def _update_actions(self) -> None:
        has_files = bool(self._files)
        self.start_button.setEnabled(has_files and not (self.worker and self.worker.isRunning()))
        self.remove_button.setEnabled(has_files)
        self.clear_button.setEnabled(has_files)

    def _open_output_folder(self) -> None:
        folder = self._last_output_dir or os.path.expanduser("~")
        if not os.path.isdir(folder):
            folder = os.path.dirname(folder) or os.path.expanduser("~")
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _toggle_theme(self) -> None:
        self.dark = not self.dark
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.dark)
        self._refresh_theme_ui()

    def _refresh_theme_ui(self) -> None:
        self.theme_button.setText("Light mode" if self.dark else "Dark mode")

    # ------------------------------------------------------------------
    # Log console
    # ------------------------------------------------------------------

    def append_log(self, level: str, message: str) -> None:
        """Append a timestamped, color-coded line to the log console."""
        timestamp = time.strftime("%H:%M:%S")
        color = log_color(level, self.dark)
        self.log_console.appendHtml(
            f'<span style="color:{color}">[{timestamp}] '
            f"{html.escape(message)}</span>"
        )
