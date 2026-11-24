"""Shared heading, content spacing and action row for application-owned dialogs."""

from pathlib import Path
import sys

from PySide6.QtCore import QSize, QStandardPaths, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


def fileDialogDirectory(preferred: Path | None = None) -> Path:
    """Keep Finder-launched dialogs in user folders even while viewing a bundled example."""
    if not getattr(sys, "frozen", False):
        return preferred or Path.cwd()
    if preferred is not None and preferred.is_dir():
        contents = Path(sys.executable).resolve().parents[1]
        if not preferred.resolve().is_relative_to(contents):
            return preferred
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(documents) if documents and Path(documents).is_dir() else Path.home()


def dialogLayout(
    dialog: QDialog, title: str | None = None, *, icon: QIcon | None = None,
    subtitle: QLabel | None = None,
) -> QVBoxLayout:
    """Create the content column and an accessible heading above the dialog's controls."""
    dialog.setProperty("workspaceDialog", True)
    layout = dialog.layout() or QVBoxLayout(dialog)
    if not isinstance(layout, QVBoxLayout):
        raise TypeError("The dialog requires a vertical content layout.")
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(10)
    heading = QLabel(dialog.windowTitle() if title is None else title, dialog)
    heading.setTextFormat(Qt.TextFormat.PlainText)
    heading.setObjectName("dialogHeading")
    heading.setIndent(0)
    heading.setWordWrap(True)
    if title is None:
        dialog.windowTitleChanged.connect(heading.setText)
    if icon is None and subtitle is None:
        layout.insertWidget(0, heading)
    else:
        header = QHBoxLayout()
        header.setSpacing(12)
        if icon is not None:
            mark = QLabel(dialog)
            mark.setObjectName("dialogWorkflowIcon")
            mark.setFixedSize(42, 42)
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setPixmap(icon.pixmap(QSize(22, 22), dialog.devicePixelRatioF()))
            header.addWidget(mark)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(heading)
        if subtitle is not None:
            text.addWidget(subtitle)
        header.addLayout(text, 1)
        layout.insertLayout(0, header)
    return layout


def addDialogFooter(
    layout: QVBoxLayout, controls: QLayout | QWidget, primary: QPushButton | None = None,
) -> None:
    """Separate the action row visually while preserving button order and keyboard defaults."""
    footer = QFrame(layout.parentWidget())
    footer.setObjectName("dialogFooter")
    row = QVBoxLayout(footer)
    row.setContentsMargins(0, 12, 0, 0)
    if isinstance(controls, QLayout):
        row.addLayout(controls)
    else:
        row.addWidget(controls)
    layout.addWidget(footer)
    if primary is not None:
        _styleAction(primary, "dialogPrimary")


def _styleAction(button: QPushButton, role: str) -> None:
    # QDialogButtonBox can polish native buttons before their final action role is assigned.
    button.setProperty(role, True)
    style = button.style()
    style.unpolish(button)
    style.polish(button)
    button.update()


def confirmAction(
    parent: QWidget, title: str, question: str, action: str, *, detail: str = "",
    expandedDetail: str = "",
) -> bool:
    """Ask before a destructive change, with a named action and Cancel as the keyboard default."""
    box = QMessageBox(parent)
    box.setOption(QMessageBox.Option.DontUseNativeDialog)
    box.setProperty("workspaceNotice", True)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(question)
    if detail:
        box.setInformativeText(detail)
    if expandedDetail:
        box.setDetailedText(expandedDetail)
    buttons = QMessageBox.StandardButton
    box.setStandardButtons(buttons.Cancel | buttons.Yes)
    confirm = box.button(buttons.Yes)
    confirm.setText(action)
    _styleAction(confirm, "destructive")
    box.setDefaultButton(buttons.Cancel)
    box.setEscapeButton(buttons.Cancel)
    accepted = box.exec() == buttons.Yes
    box.deleteLater()
    return accepted
