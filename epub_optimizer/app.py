"""Application bootstrap: QApplication setup, theme, main window."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from epub_optimizer import __version__
from epub_optimizer.ui.main_window import MainWindow
from epub_optimizer.ui.theme import apply_theme

#: Application identity (also used for the QSettings store location).
APP_NAME = "Kindle EPUB Optimizer"
ORG_NAME = "EpubOptimizer"


def main(argv: list[str] | None = None) -> int:
    """Create the QApplication, show the main window and run the event loop."""
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)

    settings = QSettings()
    dark = bool(settings.value("dark_mode", True, type=bool))
    apply_theme(app, dark)

    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
