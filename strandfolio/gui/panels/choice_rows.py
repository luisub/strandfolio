"""Native paged buttons for selectable rows exposed to assistive technology."""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


@dataclass(frozen=True, slots=True)
class ChoiceRow:
    """Identify a row and retain its full accessible label as the layout narrows."""

    key: Hashable
    text: str
    accessibleName: str = ""


class _ChoiceButton(QPushButton):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._label = ""
        self.setObjectName("featureName")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(1)
        self.setCheckable(True)
        self.setAutoDefault(False)

    def setRow(self, row: ChoiceRow) -> None:
        self._label = row.text
        self.setAccessibleName(row.accessibleName or row.text)
        self.setToolTip(escape(row.accessibleName or row.text))
        self._fit()

    def _fit(self) -> None:
        metrics = self.fontMetrics()
        width = max(0, self.width() - 18)
        text = "\n".join(
            metrics.elidedText(line, Qt.TextElideMode.ElideRight, width)
            for line in self._label.splitlines()
        )
        self.setText(text.replace("&", "&&"))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class ChoiceRows(QWidget):
    """Show one page of choices with buttons for mouse and keyboard selection."""

    activated = Signal(object)
    def __init__(
        self, accessibleName: str, parent: QWidget | None = None, *,
        multiple: bool = False, pageSize: int = 18,
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName(accessibleName)
        self._multiple = multiple
        self._pageSize = pageSize
        self._rows: tuple[ChoiceRow, ...] = ()
        self._keys: frozenset[Hashable] = frozenset()
        self._matchingRows: tuple[ChoiceRow, ...] = ()
        self._visibleRows: tuple[ChoiceRow, ...] = ()
        self._page = 0
        self._query = ""
        self.selectedKeys: frozenset[Hashable] = frozenset()
        self.rowButtons: dict[Hashable, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(self._scroll)
        self._scroll.setWidget(content)
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(3)
        rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._buttons = tuple(_ChoiceButton(content) for _ in range(self._pageSize))
        for index, button in enumerate(self._buttons):
            button.toggled.connect(lambda checked, slot=index: self._activate(slot, checked))
            rows.addWidget(button)
        layout.addWidget(self._scroll, 1)
        self._paging = QWidget(self)
        paging = QHBoxLayout(self._paging)
        paging.setContentsMargins(0, 0, 0, 0)
        self._back = QPushButton("Previous", self._paging)
        self._back.setAutoDefault(False)
        self._back.clicked.connect(lambda: self._movePage(-1))
        self._range = QLabel(self._paging)
        self._range.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next = QPushButton("Next", self._paging)
        self._next.setAutoDefault(False)
        self._next.clicked.connect(lambda: self._movePage(1))
        paging.addWidget(self._back)
        paging.addWidget(self._range, 1)
        paging.addWidget(self._next)
        layout.addWidget(self._paging)
        self._renderPage()

    @property
    def rowCount(self) -> int:
        """Return the total row count before filtering or paging."""
        return len(self._rows)

    def setRows(self, rows: tuple[ChoiceRow, ...]) -> None:
        """Replace the available choices and keep any surviving selections and page."""
        if rows == self._rows:
            return
        self._rows = rows
        self._keys = frozenset(row.key for row in rows)
        self.selectedKeys &= self._keys
        self._applyFilter()

    def setFilter(self, query: str) -> None:
        """Narrow visible rows while keeping the selection across all available rows."""
        normalized = query.casefold().strip()
        if normalized != self._query:
            self._query = normalized
            self._page = 0
            self._applyFilter()

    def setSelected(self, key: Hashable | None, *, reveal: bool = True) -> None:
        """Set one checked row and optionally show the page where it appears."""
        self.setChecked(() if key is None else (key,))
        if reveal and key is not None and key not in self.rowButtons:
            for index, row in enumerate(self._matchingRows):
                if row.key == key:
                    self._page = index // self._pageSize
                    self._renderPage()
                    break
        if reveal and key in self.rowButtons:
            self._scroll.ensureWidgetVisible(self.rowButtons[key])

    def setChecked(self, keys: Iterable[Hashable]) -> None:
        """Update checked state without activating the row callbacks."""
        self.selectedKeys = self._keys.intersection(keys)
        for key, button in self.rowButtons.items():
            with QSignalBlocker(button):
                button.setChecked(key in self.selectedKeys)

    def _applyFilter(self) -> None:
        if self._query:
            self._matchingRows = tuple(
                row for row in self._rows if self._query in row.text.casefold()
                or self._query in row.accessibleName.casefold()
            )
        else:
            self._matchingRows = self._rows
        self._page = min(self._page, self._lastPage())
        self._renderPage()

    def _lastPage(self) -> int:
        return max(0, (len(self._matchingRows) - 1) // self._pageSize)

    def _movePage(self, offset: int) -> None:
        self._page = min(max(self._page + offset, 0), self._lastPage())
        self._renderPage()
        self._scroll.verticalScrollBar().setValue(0)

    def _renderPage(self) -> None:
        start = self._page * self._pageSize
        self._visibleRows = self._matchingRows[start:start + self._pageSize]
        self.rowButtons.clear()
        for index, button in enumerate(self._buttons):
            if index < len(self._visibleRows):
                row = self._visibleRows[index]
                button.setRow(row)
                with QSignalBlocker(button):
                    button.setChecked(row.key in self.selectedKeys)
                button.show()
                self.rowButtons[row.key] = button
            else:
                button.hide()
        count = len(self._matchingRows)
        self._paging.setVisible(count > self._pageSize)
        self._range.setText(f"{start + 1:,}–{start + len(self._visibleRows):,} of {count:,}")
        self._back.setEnabled(self._page > 0)
        self._next.setEnabled(self._page < self._lastPage())

    def _activate(self, index: int, checked: bool) -> None:
        if index >= len(self._visibleRows):
            return
        key = self._visibleRows[index].key
        if self._multiple:
            selected = set(self.selectedKeys)
            if checked:
                selected.add(key)
            else:
                selected.discard(key)
            self.setChecked(selected)
        else:
            self.setSelected(key, reveal=False)
        self.activated.emit(key)
