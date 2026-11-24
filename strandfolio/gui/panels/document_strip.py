"""Horizontally scrollable tabs for open sequence documents."""

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from strandfolio.gui.workspace_icons import workspaceIcon

if TYPE_CHECKING:
    from strandfolio.application.workspace import Workspace


@dataclass(frozen=True)
class DocumentLabel:
    """Display values for one open tab; actions use only its document ID."""

    documentId: str
    displayName: str
    originalName: str
    sourceFilename: str | None
    disambiguator: str
    isDraft: bool
    topology: str | None
    length: int
    unit: str

    @property
    def qualifiedName(self) -> str:
        if self.disambiguator:
            return f"{self.displayName} · {self.disambiguator}"
        return self.displayName


def documentLabels(workspace: "Workspace") -> tuple[DocumentLabel, ...]:
    """Resolve duplicate labels using source, ordinal and open-document identity."""
    labels = []
    for documentId, (name, source) in workspace.documentDisplayNames().items():
        document = workspace.getDocument(documentId)
        labels.append(DocumentLabel(
            documentId, name, document.originalName,
            document.sourceFilename, source, document.isDraft,
            document.revision.topology, len(document.revision),
            "aa" if document.revision.moleculeType == "protein" else "nt",
        ))
    return tuple(labels)


class _DocumentButton(QPushButton):
    editRequested = Signal()
    focusRequested = Signal(int)
    contextRequested = Signal()
    revealed = Signal()
    nameFocused = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._showFocusedName = False
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("documentName")

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.editRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.contextRequested.emit()
        event.accept()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._showFocusedName = event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
        )

    def showFocus(self) -> None:
        if self.hasFocus():
            self.revealed.emit()
            if self._showFocusedName:
                self.nameFocused.emit(self.statusTip())

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            self.focusRequested.emit(key)
        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.click()
        elif key == Qt.Key.Key_F2:
            self.editRequested.emit()
        elif key == Qt.Key.Key_Menu or (
            key == Qt.Key.Key_F10 and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        ):
            self.contextRequested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class _DocumentTab(QFrame):
    """A focusable tab name and a separate close button for one document."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("documentTab")
        self._name = ""
        self._suffix = ""
        self.nameButton = _DocumentButton(self)
        self.nameButton.setIconSize(QSize(14, 14))
        self.length = QLabel(self)
        self.length.setObjectName("documentLength")
        self.length.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.closeButton = QToolButton(self)
        self.closeButton.setIcon(workspaceIcon("close"))
        self.closeButton.setIconSize(QSize(12, 12))
        self.closeButton.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.closeButton.setFixedSize(20, 22)
        self.closeButton.setObjectName("documentClose")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 5, 2)
        layout.setSpacing(4)
        layout.addWidget(self.nameButton)
        layout.addWidget(self.length)
        layout.addWidget(self.closeButton)

    def setLabel(self, label: DocumentLabel) -> None:
        self.nameButton.setIcon(workspaceIcon(label.topology or "record"))
        self.length.setText(f"{label.length:,}")
        self._name = label.displayName
        self._suffix = f" · {label.disambiguator}" if label.disambiguator else ""
        if label.isDraft:
            self._suffix += " •"
        details = [label.displayName, f"{label.length:,} {label.unit}"]
        if label.topology:
            details.append(label.topology.capitalize())
        if label.originalName != label.displayName:
            details.append(f"Original name: {label.originalName}")
        if label.sourceFilename:
            details.append(f"Source: {label.sourceFilename}")
        if label.disambiguator:
            details.append(label.disambiguator)
        if label.isDraft:
            details.append("Unsaved sequence")
        self.nameButton.setToolTip("<br>".join(map(escape, details)))
        self.nameButton.setStatusTip(" · ".join(details))
        self.nameButton.setAccessibleName(". ".join(details))
        self.setToolTip(self.nameButton.toolTip())
        closeName = f"Close {label.qualifiedName}"
        self.closeButton.setAccessibleName(closeName)
        self.closeButton.setToolTip(escape(closeName))

    def fitLabel(self, maximumWidth: int) -> None:
        metrics = self.nameButton.fontMetrics()
        textWidth = metrics.horizontalAdvance(self._name + self._suffix)
        name, suffix = self._name, self._suffix
        buttonPadding = self.nameButton.iconSize().width() + 16
        if textWidth > maximumWidth - buttonPadding:
            textWidth = maximumWidth - buttonPadding
            suffixWidth = max(textWidth // 2, textWidth - metrics.horizontalAdvance(name))
            suffix = metrics.elidedText(suffix, Qt.TextElideMode.ElideMiddle, suffixWidth)
            name = metrics.elidedText(
                name, Qt.TextElideMode.ElideMiddle, textWidth - metrics.horizontalAdvance(suffix),
            )
        self.nameButton.setText((name + suffix).replace("&", "&&"))
        self.nameButton.setFixedWidth(max(64, textWidth + buttonPadding))
        self.layout().activate()
        size = self.layout().sizeHint()
        self.setFixedSize(size.width(), max(34, size.height()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.nameButton.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DocumentStrip(QWidget):
    """Show, reveal, activate, rename and close open documents by stable ID."""

    activateRequested = Signal(str)
    closeRequested = Signal(str)
    editNameRequested = Signal(str)
    nameFocused = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Open sequence documents")
        self._tabs: dict[str, _DocumentTab] = {}
        self._activeDocumentId: str | None = None
        self._orderedIds: tuple[str, ...] = ()
        self._nameMenu = QMenu(self)
        self._menuDocumentId: str | None = None
        editAction = self._nameMenu.addAction("Edit preferred name…")
        editAction.triggered.connect(self._editMenuTarget)

        self.scrollArea = QScrollArea(self)
        self.scrollArea.setFrameShape(QFrame.Shape.NoFrame)
        self.scrollArea.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scrollArea.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._contents = QWidget()
        self._tabsLayout = QHBoxLayout(self._contents)
        self._tabsLayout.setContentsMargins(0, 0, 0, 0)
        self._tabsLayout.setSpacing(4)
        self.scrollArea.setWidget(self._contents)
        self._scrollBar = self.scrollArea.horizontalScrollBar()
        self.scrollLeftButton = self._scrollButton("‹", "Scroll sequence tabs left", -1)
        self.scrollRightButton = self._scrollButton("›", "Scroll sequence tabs right", 1)
        self._scrollBar.valueChanged.connect(self._updateScrollButtons)
        self._scrollBar.rangeChanged.connect(self._updateScrollButtons)
        self.scrollArea.viewport().installEventFilter(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.scrollLeftButton)
        layout.addWidget(self.scrollArea, 1)
        layout.addWidget(self.scrollRightButton)
        self._updateScrollButtons()
        QApplication.instance().focusChanged.connect(self._onFocusChanged)

    def _onFocusChanged(self, previous: QWidget | None, current: QWidget | None) -> None:
        if not isinstance(current, _DocumentButton) or not self.isAncestorOf(current):
            return
        if previous is not None and not previous.isVisible():
            # Hiding a workflow can pass focus through this button on the way to the new page.
            QTimer.singleShot(0, current, current.showFocus)
        else:
            current.showFocus()

    def _scrollButton(self, text: str, name: str, direction: int) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setAutoRepeat(True)
        button.clicked.connect(lambda: self._scrollBy(direction * 136))
        return button

    def nameButton(self, documentId: str) -> QPushButton:
        return self._tabs[documentId].nameButton

    def closeButton(self, documentId: str) -> QToolButton:
        return self._tabs[documentId].closeButton

    def setDocuments(self, labels: tuple[DocumentLabel, ...], activeDocumentId: str | None) -> None:
        """Refresh content without rebuilding surviving controls or obscuring the focused tab."""
        priorActiveId = self._activeDocumentId
        hadFocus = priorActiveId in self._tabs and any(
            button.hasFocus() for button in (
                self._tabs[priorActiveId].nameButton, self._tabs[priorActiveId].closeButton,
            )
        )
        orderedIds = tuple(label.documentId for label in labels)
        for documentId in self._tabs.keys() - set(orderedIds):
            tab = self._tabs.pop(documentId)
            self._tabsLayout.removeWidget(tab)
            tab.hide()
            tab.deleteLater()
        for index, label in enumerate(labels):
            documentId = label.documentId
            tab = self._tabs.get(documentId)
            if tab is None:
                tab = self._addTab(documentId, index)
            tab.setLabel(label)
            active = documentId == activeDocumentId
            with QSignalBlocker(tab.nameButton):
                tab.nameButton.setChecked(active)
            if tab.property("active") != active:
                tab.setProperty("active", active)
                tab.style().unpolish(tab)
                tab.style().polish(tab)
        self._orderedIds = orderedIds
        self._activeDocumentId = activeDocumentId
        self.setVisible(bool(labels))
        self._fitTabs()
        if activeDocumentId != priorActiveId and activeDocumentId is not None:
            self._reveal(activeDocumentId)
            if hadFocus and priorActiveId not in self._tabs:
                self.nameButton(activeDocumentId).setFocus(Qt.FocusReason.TabFocusReason)

    def _addTab(self, documentId: str, index: int) -> _DocumentTab:
        tab = _DocumentTab(self._contents)
        self._tabs[documentId] = tab
        self._tabsLayout.insertWidget(index, tab)
        tab.nameButton.toggled.connect(lambda checked: self._onTabToggled(documentId, checked))
        tab.nameButton.editRequested.connect(lambda: self.editNameRequested.emit(documentId))
        tab.nameButton.contextRequested.connect(lambda: self._showMenu(documentId))
        tab.nameButton.revealed.connect(lambda: self._reveal(documentId))
        tab.nameButton.nameFocused.connect(self.nameFocused)
        tab.nameButton.focusRequested.connect(lambda key: self._moveFocus(documentId, key))
        tab.closeButton.clicked.connect(lambda: self.closeRequested.emit(documentId))
        for widget in (tab, tab.nameButton, tab.closeButton):
            widget.installEventFilter(self)
        tab.show()
        return tab

    def _onTabToggled(self, documentId: str, checked: bool) -> None:
        if checked or documentId == self._activeDocumentId:
            self.activateRequested.emit(documentId)
        tab = self._tabs.get(documentId)
        if tab is not None:
            with QSignalBlocker(tab.nameButton):
                tab.nameButton.setChecked(documentId == self._activeDocumentId)
            self._reveal(documentId)

    def _moveFocus(self, documentId: str, key: int) -> None:
        index = self._orderedIds.index(documentId)
        last = len(self._orderedIds) - 1
        destination = {
            Qt.Key.Key_Left: max(0, index - 1),
            Qt.Key.Key_Right: min(last, index + 1),
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_End: last,
        }[key]
        self.nameButton(self._orderedIds[destination]).setFocus(Qt.FocusReason.TabFocusReason)

    def _showMenu(self, documentId: str) -> None:
        self._menuDocumentId = documentId
        button = self.nameButton(documentId)
        self._nameMenu.popup(button.mapToGlobal(button.rect().bottomLeft()))

    def _editMenuTarget(self) -> None:
        if self._menuDocumentId in self._tabs:
            self.editNameRequested.emit(self._menuDocumentId)

    def _reveal(self, documentId: str) -> None:
        tab = self._tabs.get(documentId)
        if tab is not None:
            self.scrollArea.ensureWidgetVisible(tab, 4, 0)

    def _fitTabs(self) -> None:
        available = self.scrollArea.viewport().width()
        count = max(1, len(self._tabs))
        maximum = max(170, min(270, available // count - 72))
        for tab in self._tabs.values():
            tab.fitLabel(maximum)
        self._tabsLayout.activate()
        size = self._tabsLayout.sizeHint()
        self._contents.resize(size)
        self.scrollArea.setFixedHeight(max(size.height(), self.scrollLeftButton.sizeHint().height()))
        self._updateScrollButtons()

    def _scrollBy(self, delta: int) -> None:
        self._scrollBar.setValue(self._scrollBar.value() + delta)

    def _updateScrollButtons(self) -> None:
        overflow = self._contents.width() > self.contentsRect().width()
        self.scrollLeftButton.setVisible(overflow)
        self.scrollRightButton.setVisible(overflow)
        self.scrollLeftButton.setEnabled(self._scrollBar.value() > self._scrollBar.minimum())
        self.scrollRightButton.setEnabled(self._scrollBar.value() < self._scrollBar.maximum())

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize and watched is self.scrollArea.viewport():
            self._fitTabs()
            if self._activeDocumentId is not None:
                self._reveal(self._activeDocumentId)
        if event.type() == QEvent.Type.Wheel:
            delta = event.pixelDelta().x() or event.angleDelta().x()
            if not delta and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta = event.pixelDelta().y() or event.angleDelta().y()
            if delta:
                self._scrollBy(-delta)
                return True
        return super().eventFilter(watched, event)
