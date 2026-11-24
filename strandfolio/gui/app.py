"""Standalone Qt application startup; importing this module does not start Qt."""

import argparse
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QApplication

APP_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024)


def getIconDirectory() -> Path:
    """Return the package-relative directory holding bundled application icons."""
    return Path(__file__).resolve().parents[1] / "data" / "icons"


def _scaledAppIconPath(iconDir: Path, size: int) -> Path:
    return iconDir / "scales" / f"icon_strandfolio_app_{size}x{size}.png"


def getAppIconPath() -> Path | None:
    """Return the native or primary application icon path, or None if unavailable."""
    iconDir = getIconDirectory()
    raster = _scaledAppIconPath(iconDir, 1024)
    vector = iconDir / "icon_strandfolio_app.svg"
    if sys.platform == "darwin":
        candidates = (iconDir / "icon_strandfolio_app.icns", raster, vector)
    elif sys.platform == "win32":
        candidates = (iconDir / "icon_strandfolio_app.ico", raster, vector)
    else:
        candidates = (vector, raster)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def getHeaderIconPath() -> Path | None:
    """Return the compact plasmid icon path for the workspace header."""
    iconDir = getIconDirectory()
    for name in ("icon_strandfolio_gui.svg", "icon_strandfolio_gui.png"):
        candidate = iconDir / name
        if candidate.is_file():
            return candidate
    return None


def buildAppIcon() -> "QIcon":
    """Return a multi-scale QIcon combining native and scaled PNG assets."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    iconPath = getAppIconPath()
    icon = QIcon(str(iconPath)) if iconPath is not None else QIcon()
    iconDir = getIconDirectory()
    if (iconDir / "scales").is_dir():
        knownSizes = {(size.width(), size.height()) for size in icon.availableSizes()}
        for size in APP_ICON_SIZES:
            if (size, size) in knownSizes:
                continue
            scaledPath = _scaledAppIconPath(iconDir, size)
            if scaledPath.is_file():
                icon.addFile(str(scaledPath), QSize(size, size))
    return icon


def buildHeaderIconPixmap(sizePx: int = 26, devicePixelRatio: float = 2.0) -> "QPixmap":
    """Render the plasmid header icon at the requested display scale."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon, QPixmap

    iconPath = getHeaderIconPath()
    if iconPath is None:
        return QPixmap()
    icon = QIcon(str(iconPath))
    if icon.isNull():
        return QPixmap()
    return icon.pixmap(QSize(sizePx, sizePx), devicePixelRatio)


def _cocoaSessionUnavailable() -> bool:
    """Detect a missing Quartz GUI session before Qt tries to register with AppKit."""
    platform = os.environ.get("QT_QPA_PLATFORM", "").partition(";")[0].partition(":")[0]
    if sys.platform != "darwin" or platform not in ("", "cocoa"):
        return False

    from ctypes import CDLL, c_void_p

    try:
        graphics = CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        foundation = CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        copySession = graphics.CGSessionCopyCurrentDictionary
        copySession.argtypes = []
        copySession.restype = c_void_p
        release = foundation.CFRelease
        release.argtypes = [c_void_p]
        release.restype = None
    except (OSError, AttributeError):
        # An unavailable probe says nothing about the caller's actual GUI session.
        return False
    session = copySession()
    if not session:
        return True
    release(session)
    return False


def configureWorkspaceApplication(application: "QApplication") -> None:
    """Apply native workspace typography and appearance to an existing GUI application."""
    from PySide6.QtCore import Qt

    from .theme import workspacePalette, workspaceUiFont

    application.styleHints().setColorScheme(Qt.ColorScheme.Light)
    application.setStyle("Fusion")
    application.setFont(workspaceUiFont())
    application.setPalette(workspacePalette(application.palette()))


def main() -> int:
    """Launch the inspector, optionally opening a sequence file, and return Qt's exit status."""
    parser = argparse.ArgumentParser(description="strandfolio sequence, digestion, PCR and alignment workspace")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("sequence", nargs="?", help="Optional sequence or .vibedigest file to open")
    source.add_argument("--example", action="store_true",
                        help="Open the synthetic pReporter_demo from the Sequence design plate")
    parser.add_argument("--molecule-type", dest="moleculeType", choices=("DNA", "RNA", "protein"),
                        help="Choose the alphabet for headerless .txt input or override FASTA "
                             "inference. GenBank and SnapGene use their declared DNA metadata.")
    arguments = parser.parse_args()
    try:
        from PySide6.QtWidgets import QApplication
    except ModuleNotFoundError as error:
        if (error.name or "").split(".")[0] not in {"PySide6", "shiboken6"}:
            raise
        print(
            "strandfolio requires PySide6. Install the project environment using "
            "docs/installation/README.md, then run the launcher with that environment's Python.",
            file=sys.stderr,
        )
        return 1

    if _cocoaSessionUnavailable():
        print(
            "strandfolio cannot access a macOS desktop from this process. "
            "Launch it from Terminal in a logged-in Mac desktop session.",
            file=sys.stderr,
        )
        return 1

    from PySide6.QtCore import QThreadPool

    from .window import MainWindow

    application = QApplication(sys.argv[:1])
    application.setApplicationName("strandfolio")
    application.setApplicationDisplayName("strandfolio")
    configureWorkspaceApplication(application)
    appIcon = buildAppIcon()
    if not appIcon.isNull():
        application.setWindowIcon(appIcon)
    window = MainWindow()
    window.show()
    if arguments.example:
        window.openDesignExample()
    elif arguments.sequence:
        if Path(arguments.sequence).suffix.lower() == ".vibedigest":
            window.controller.analysis.loadDigestion(Path(arguments.sequence))
        else:
            window.controller.loadSequence(arguments.sequence, arguments.moleculeType)
    status = application.exec()
    # Quit has closed the UI. Keep Qt and its signal objects alive until pending
    # reads and approved writes finish, including exports on the shared pool.
    for pool in (*window.findChildren(QThreadPool), QThreadPool.globalInstance()):
        pool.waitForDone()
    return status
