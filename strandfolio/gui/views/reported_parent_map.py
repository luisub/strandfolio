"""Display user-reported intervals on the coordinate axis of an existing record."""

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QResizeEvent
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from strandfolio.domain.provenance import DeclaredParent
from strandfolio.gui.theme import ACCENT, BACKGROUND, MUTED, PANEL, SELECTION, TEXT


class ReportedParentMap(QAbstractScrollArea):
    """Draw each reported occurrence on its own row, using one child coordinate scale."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._childLength = 0
        self._rows: list[tuple[str, tuple[int, int], int | None]] = []
        self.setAccessibleName("Reported intervals on the child record")
        self.setAccessibleDescription("The reported parent list gives each exact interval and orientation.")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self.hide()

    def setRecord(
        self, childLength: int, entries: tuple[DeclaredParent, ...], names: dict[str, str],
    ) -> None:
        """Draw supplied child intervals. Parent links with unknown locations stay in the list."""
        self._childLength = childLength
        self._rows = [
            (names.get(entry.revisionId, entry.revisionId), entry.productSpan, entry.orientation)
            for entry in entries if entry.productSpan is not None
        ]
        self.setVisible(bool(self._rows) and childLength > 0)
        self.setFixedHeight((1 + min(4, len(self._rows))) * self._rowHeight() + 2)
        self.verticalScrollBar().setValue(0)
        self._updateScrollRange()
        self.viewport().update()

    def _rowHeight(self) -> int:
        return self.fontMetrics().height() + 9

    def _updateScrollRange(self) -> None:
        visible = max(1, self.viewport().height() // self._rowHeight() - 1)
        self.verticalScrollBar().setRange(0, max(0, len(self._rows) - visible))
        self.verticalScrollBar().setPageStep(visible)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._updateScrollRange()

    def paintEvent(self, event) -> None:
        with QPainter(self.viewport()) as painter:
            painter.fillRect(self.viewport().rect(), QColor(PANEL))
            if not self._rows or not self._childLength:
                return
            metrics = painter.fontMetrics()
            width = self.viewport().width()
            rowHeight = self._rowHeight()
            left = min(200, width // 3)
            trackWidth = max(1, width - left - 12)
            align = Qt.AlignmentFlag
            painter.setPen(QColor(MUTED))
            painter.drawText(QRect(8, 0, left - 16, rowHeight), align.AlignVCenter, "Parent")
            header = QRect(left, 0, trackWidth, rowHeight)
            painter.drawText(header, align.AlignLeft | align.AlignVCenter, "1")
            if trackWidth > metrics.horizontalAdvance("Child positions") + 130:
                painter.drawText(header, align.AlignCenter, "Child positions")
            painter.drawText(header, align.AlignRight | align.AlignVCenter, f"{self._childLength:,}")

            first = self.verticalScrollBar().value()
            visible = self.viewport().height() // rowHeight
            for index, (name, span, orientation) in enumerate(self._rows[first:first + visible]):
                top = (index + 1) * rowHeight
                middle = top + rowHeight / 2
                prefix = {1: "→ ", -1: "← ", None: ""}[orientation]
                label = metrics.elidedText(f"{prefix}{name}", Qt.TextElideMode.ElideRight, left - 16)
                painter.setPen(QColor(TEXT))
                painter.drawText(QRect(8, top, left - 16, rowHeight), align.AlignVCenter, label)
                painter.fillRect(QRectF(left, middle - 2, trackWidth, 4), QColor(SELECTION))
                start = left + trackWidth * span[0] / self._childLength
                end = left + trackWidth * span[1] / self._childLength
                barWidth = max(1.0, end - start)
                painter.fillRect(QRectF(min(start, left + trackWidth - barWidth), middle - 5,
                                        barWidth, 10), QColor(ACCENT))
                if orientation is not None and barWidth >= 12:
                    tip = end - 3 if orientation == 1 else start + 3
                    tail = tip - 4 * orientation
                    painter.setPen(QColor(BACKGROUND))
                    painter.drawLine(QPointF(tail, middle - 3), QPointF(tip, middle))
                    painter.drawLine(QPointF(tip, middle), QPointF(tail, middle + 3))
