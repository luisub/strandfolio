"""Show the pairwise alignment's viewport and the extent of its differences."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from strandfolio.operations.alignment import AlignmentResult
from strandfolio.rendering.colors import ACCENT, ACCENT_INK, AMBER, LINE


class AlignmentOverview(QWidget):
    """Pan through alignment columns independently of the inspected column."""

    panRequested = Signal(int)
    stepRequested = Signal(int, bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._length = 0
        self._marks = bytearray()
        self._visible = (0, 0)
        self._selected = -1
        self.setFixedHeight(25)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Alignment overview")
        self.setToolTip(
            "Click, drag or use arrow and page keys to scroll. "
            "Top marks show substitutions. Bottom marks show insertions and deletions."
        )

    def setResult(self, result: AlignmentResult | None) -> None:
        self._length = result.alignedLength if result is not None else 0
        size = min(512, self._length)
        self._marks = bytearray(size)
        if size:
            for event in result.differences:
                first = event.start * size // self._length
                last = (event.end - 1) * size // self._length
                flag = 1 if event.kind == "substitution" else 2
                for index in range(first, last + 1):
                    self._marks[index] |= flag
        self._visible = (0, 0)
        self._selected = -1
        self.update()

    def setVisibleRange(self, first: int, end: int, text: str) -> None:
        self._visible = (first, end)
        self.setAccessibleDescription(text)
        self.update()

    def setSelected(self, index: int) -> None:
        self._selected = index
        self.update()

    def _strip(self) -> QRectF:
        return QRectF(5, 5, max(1, self.width() - 10), 15)

    def paintEvent(self, event) -> None:
        if not self._length:
            return
        painter = QPainter(self)
        strip = self._strip()
        painter.fillRect(strip, QColor(LINE))
        binWidth = strip.width() / len(self._marks)
        for index, kind in enumerate(self._marks):
            x = strip.left() + index * binWidth
            if kind & 1:
                painter.fillRect(QRectF(x, strip.top() + 1, max(2, binWidth), 5), QColor(AMBER))
            if kind & 2:
                painter.fillRect(QRectF(x, strip.bottom() - 6, max(2, binWidth), 5), QColor(ACCENT))
        columnWidth = strip.width() / self._length
        first, end = self._visible
        if end > first:
            left = strip.left() + first * columnWidth
            width = min(strip.right() - left, max(3, (end - first) * columnWidth))
            painter.setPen(QPen(QColor(ACCENT_INK), 2))
            painter.drawRect(QRectF(left, strip.top() - 1, width, strip.height() + 2))
        if self._selected >= 0:
            x = strip.left() + (self._selected + 0.5) * columnWidth
            painter.setPen(QPen(QColor(ACCENT_INK), 2))
            painter.drawLine(round(x), 2, round(x), self.height() - 3)
        if self.hasFocus():
            painter.setPen(QPen(QColor(ACCENT_INK), 1, Qt.PenStyle.DotLine))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _panAt(self, x: float) -> None:
        if self._length:
            strip = self._strip()
            fraction = (x - strip.left()) / strip.width()
            self.panRequested.emit(min(self._length - 1, max(0, int(fraction * self._length))))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._panAt(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._panAt(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            direction = -1 if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp) else 1
            self.stepRequested.emit(direction, key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown))
            event.accept()
            return
        super().keyPressEvent(event)
