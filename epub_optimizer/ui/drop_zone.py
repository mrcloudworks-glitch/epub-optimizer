"""Drag-and-drop zone for selecting EPUB files."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFileDialog, QFrame

#: File extension filter (case-insensitive).
_EPUB_EXTENSIONS = {".epub"}


class DropZone(QFrame):
    """A clickable, dashed-border panel that accepts dragged EPUB files.

    Emits :attr:`files_selected` with the list of accepted absolute paths
    whenever files are dropped or picked via the file browser.
    """

    files_selected = Signal(list)  # list[str]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(116)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._hover = False
        self._last_dir = ""

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        """Draw the dashed rounded border + centered hint text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # Dashed border; accent when hovered, dim otherwise.
        pen = QPen()
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setColor(
            self.palette().color(self.palette().ColorRole.Highlight)
            if self._hover
            else self.palette().color(self.palette().ColorRole.PlaceholderText)
        )
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 12, 12)

        # Two-line hint.
        painter.setPen(self.palette().color(self.palette().ColorRole.Text))
        font = painter.font()
        # pointSizeF() is -1 when the font is defined in pixels — fall back
        # to a sane default so the enlarged title is always visible.
        base_size = font.pointSizeF() if font.pointSizeF() > 0 else 12.0
        font.setPointSizeF(base_size + 2)
        font.setBold(True)
        painter.setFont(font)
        title = "Drag & drop EPUB files here"
        painter.drawText(
            rect.adjusted(12, 0, -12, -20),
            Qt.AlignmentFlag.AlignCenter,
            title,
        )
        font.setPointSizeF(base_size)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(self.palette().color(self.palette().ColorRole.PlaceholderText))
        painter.drawText(
            rect.adjusted(12, 20, -12, 0),
            Qt.AlignmentFlag.AlignCenter,
            "or click to browse — multiple files supported",
        )

    # ------------------------------------------------------------------
    # Mouse interaction (click to browse)
    # ------------------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self._browse()

    def _browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select EPUB files to optimize",
            self._last_dir,
            "EPUB files (*.epub);;All files (*)",
        )
        if paths:
            self._last_dir = paths[0].rsplit("/", 1)[0]
            self.files_selected.emit(paths)

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if self._accepted_files(event):
            event.acceptProposedAction()
            self._hover = True
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._accepted_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, _event) -> None:
        self._hover = False
        self.update()

    def dropEvent(self, event) -> None:
        files = self._accepted_files(event)
        if files:
            self._hover = False
            self.update()
            event.acceptProposedAction()
            self.files_selected.emit(files)
        else:
            event.ignore()

    def _accepted_files(self, event) -> list[str]:
        """Extract local ``.epub`` paths from a drag event (deduplicated)."""
        files: list[str] = []
        seen: set[str] = set()
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                continue
            lowered = path.lower()
            if not any(lowered.endswith(ext) for ext in _EPUB_EXTENSIONS):
                continue
            if path in seen:
                continue
            seen.add(path)
            files.append(path)
        return files
