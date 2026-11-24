"""Draw visible alignment rows and translate source locations for the inspector."""

from math import ceil

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip, QWidget

from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.operations.alignment import AlignmentInput, AlignmentResult
from strandfolio.rendering.colors import (
    ACCENT, AMBER, AMBER_SOFT, CANVAS, INK, LINE, MUTED, SELECTION,
)


def formatAlignmentPercent(count: int, total: int) -> str:
    """Display whole percents while reserving 0% and 100% for exact endpoints."""
    if not total:
        return "—"
    percent = (200 * count + total) // (2 * total)
    if percent == 0 and count:
        return "<1%"
    if percent == 100 and count != total:
        return ">99%"
    return f"{percent}%"


def _boundaryLocation(source: AlignmentInput, boundary: int) -> str:
    symbol = "residue" if source.moleculeType == "protein" else "base"
    if boundary in (0, source.sourceLength) and source.topology == "circular":
        return f"at the source origin ({source.sourceLength:,} | 1)"
    if boundary == 0:
        return f"before source {symbol} 1"
    if boundary == source.sourceLength:
        return f"after source {symbol} {boundary:,}"
    return f"between source {symbol}s {boundary:,} and {boundary + 1:,}"


def _sourceLocation(
    source: AlignmentInput, position: int | None, boundaries: tuple[int, ...],
) -> str:
    if position is not None:
        symbol = "residue" if source.moleculeType == "protein" else "base"
        return f"source {symbol} {position + 1:,}"
    if len(boundaries) == 1:
        return f"gap {_boundaryLocation(source, boundaries[0])}"
    if len(boundaries) == 2:
        return (f"gap at a region join (source interbase boundaries "
                f"{boundaries[0]:,} and {boundaries[1]:,})")
    return "gap"


def mappingText(result: AlignmentResult, index: int) -> str:
    """Show both one-based source positions and any applicable gap boundary."""
    column = result.columns[index]
    reference = _sourceLocation(result.reference, column.referencePosition, column.referenceBoundary)
    query = _sourceLocation(result.query, column.queryPosition, column.queryBoundary)
    return (f"Reference {result.referenceAligned[index]} · {reference}\n"
            f"Query {result.queryAligned[index]} · {query}")


class AlignmentTracks(QAbstractScrollArea):
    """Render visible rows without creating widgets or rich-text spans for each symbol."""

    columnPicked = Signal(int)
    visibleColumnsChanged = Signal(int, int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("alignmentTracks")
        self.setAccessibleName("Aligned sequence tracks")
        self.setAccessibleDescription("Use the alignment column field to read both source locations.")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(270)
        self._font = workspaceMonoFont(13)
        metrics = QFontMetrics(self._font)
        self._characterWidth = metrics.horizontalAdvance("A") + 1
        self._lineHeight = metrics.height() + 3
        self._numberWidth = metrics.horizontalAdvance("1,000,000")
        self._tracksLeft = metrics.horizontalAdvance("Query DNA") + self._numberWidth + 28
        self._rightGutter = self._numberWidth + 14
        self._columnsPerRow = 10
        self._result: AlignmentResult | None = None
        self._translations: tuple | None = None
        self._selected = 0
        self._highlight: tuple[int, int] | None = None
        self.verticalScrollBar().valueChanged.connect(self._refreshViewport)
        self.viewport().setMouseTracking(True)

    def minimumSizeHint(self) -> QSize:
        return QSize(self.minimumWidth(), 130)

    def setResult(self, result: AlignmentResult | None, translations: tuple | None) -> None:
        oldRows = self._rowHeight
        oldTop = self.verticalScrollBar().value() // oldRows if result is self._result else 0
        self._result = result
        self._translations = translations
        if result is not None:
            metrics = QFontMetrics(self._font)
            largest = max(result.reference.sourceLength, result.query.sourceLength, 1)
            self._numberWidth = metrics.horizontalAdvance(f"{largest:,}")
            self._tracksLeft = metrics.horizontalAdvance("Query DNA") + self._numberWidth + 28
            self._rightGutter = self._numberWidth + 14
            self._fitColumns()
        self._updateScrollRange()
        self.verticalScrollBar().setValue(oldTop * self._rowHeight)
        self._refreshViewport()

    def _refreshViewport(self) -> None:
        self.viewport().update()
        length = 0 if self._result is None else self._result.alignedLength
        scroll = self.verticalScrollBar().value()
        first = scroll // self._rowHeight * self._columnsPerRow
        end = ceil((scroll + self.viewport().height()) / self._rowHeight) * self._columnsPerRow
        self.visibleColumnsChanged.emit(min(first, length), min(end, length))

    def panToColumn(self, index: int) -> None:
        row = index // self._columnsPerRow
        center = row * self._rowHeight + self._rowHeight // 2
        self.verticalScrollBar().setValue(center - self.viewport().height() // 2)

    def panStep(self, direction: int, page: bool) -> None:
        bar = self.verticalScrollBar()
        distance = bar.pageStep() if page else self._rowHeight
        bar.setValue(bar.value() + direction * distance)

    def setColumn(self, index: int, highlight: tuple[int, int] | None, *, reveal: bool) -> None:
        self._selected = index
        self._highlight = highlight
        if reveal:
            top = index // self._columnsPerRow * self._rowHeight
            scroll = self.verticalScrollBar()
            if top < scroll.value() or top + self._rowHeight > scroll.value() + self.viewport().height():
                scroll.setValue(top)
        self.viewport().update()

    @property
    def _rowHeight(self) -> int:
        return (5 if self._translations is not None else 3) * self._lineHeight + 9

    def _updateScrollRange(self) -> None:
        count = self._result.alignedLength if self._result is not None else 0
        rows = ceil(count / self._columnsPerRow)
        page = self.viewport().height()
        bar = self.verticalScrollBar()
        bar.setSingleStep(self._lineHeight)
        bar.setPageStep(page)
        bar.setRange(0, max(0, rows * self._rowHeight - page))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        scroll = self.verticalScrollBar()
        anchor = scroll.value() // self._rowHeight * self._columnsPerRow
        self._fitColumns()
        self._updateScrollRange()
        scroll.setValue(anchor // self._columnsPerRow * self._rowHeight)
        self._refreshViewport()

    def _fitColumns(self) -> None:
        available = self.viewport().width() - self._tracksLeft - self._rightGutter
        groupWidth = 10 * self._characterWidth + 5
        self._columnsPerRow = max(10, 10 * ((available + 5) // groupWidth))

    def _x(self, offset: int) -> int:
        return self._tracksLeft + offset * self._characterWidth + (offset // 10) * 5

    def _paintTrack(
        self, painter: QPainter, result: AlignmentResult, start: int, end: int, y: int,
        *, isQuery: bool, isTranslation: bool,
    ) -> None:
        side = "Query" if isQuery else "Ref"
        alphabet = result.reference.moleculeType
        name = f"{side} {'AA' if isTranslation or alphabet == 'protein' else alphabet}"
        painter.setPen(QColor(MUTED))
        self._drawLabel(painter, 10, y, self._tracksLeft - 10, name)
        if not isTranslation:
            positions = [
                column.queryPosition if isQuery else column.referencePosition
                for column in result.columns[start:end]
            ]
            mapped = [position + 1 for position in positions if position is not None]
            first, last = (f"{mapped[0]:,}", f"{mapped[-1]:,}") if mapped else ("—", "—")
            self._drawLabel(painter, self._tracksLeft - self._numberWidth - 7, y,
                            self._numberWidth, first, alignRight=True)
            self._drawLabel(painter, self._x(end - start) + 6, y, self._rightGutter, last)
        row = result.queryAligned if isQuery else result.referenceAligned
        codons = self._translations[int(isQuery)] if isTranslation else None
        for index in range(start, end):
            x = self._x(index - start)
            if index == self._selected:
                painter.fillRect(x, y, self._characterWidth, self._lineHeight, QColor(SELECTION))
            if codons is not None:
                codon = codons[index]
                if codon is None:
                    continue
                painter.setPen(QColor(ACCENT if codon.effect in ("changed", "unpaired") else MUTED))
                symbol = codon.aminoAcid
                if codon.effect == "unpaired":
                    painter.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DotLine))
                    painter.drawLine(x, y + self._lineHeight - 2, x + self._characterWidth,
                                     y + self._lineHeight - 2)
            else:
                symbol = row[index]
                isDifferent = result.referenceAligned[index] != result.queryAligned[index]
                painter.setPen(QColor(AMBER if isDifferent else INK))
            self._drawLabel(painter, x, y, self._characterWidth, symbol)

    def _drawLabel(
        self, painter: QPainter, x: int, y: int, width: int, text: str, *, alignRight: bool = False,
    ) -> None:
        alignment = Qt.AlignmentFlag.AlignRight if alignRight else Qt.AlignmentFlag.AlignLeft
        painter.drawText(QRect(x, y, width, self._lineHeight), alignment | Qt.AlignmentFlag.AlignVCenter, text)

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(CANVAS))
        result = self._result
        if result is None:
            return
        painter.setFont(self._font)
        scroll = self.verticalScrollBar().value()
        start = scroll // self._rowHeight * self._columnsPerRow
        y = -(scroll % self._rowHeight)
        while y < self.viewport().height() and start < result.alignedLength:
            end = min(start + self._columnsPerRow, result.alignedLength)
            if self._highlight is not None:
                left = max(start, self._highlight[0])
                right = min(end, self._highlight[1])
                if left < right:
                    x = self._x(left - start)
                    width = self._x(right - start - 1) + self._characterWidth - x
                    painter.fillRect(x, y + self._lineHeight, width, self._rowHeight - 8 - self._lineHeight,
                                     QColor(AMBER_SOFT))
            painter.setFont(self.font())
            painter.setPen(QColor(MUTED))
            self._drawLabel(painter, 10, y, self._tracksLeft + 130, f"Columns {start + 1:,}–{end:,}")
            painter.setFont(self._font)
            line = y + self._lineHeight
            if self._translations is not None:
                self._paintTrack(painter, result, start, end, line, isQuery=False, isTranslation=True)
                line += self._lineHeight
            self._paintTrack(painter, result, start, end, line, isQuery=False, isTranslation=False)
            self._paintTrack(painter, result, start, end, line + self._lineHeight,
                             isQuery=True, isTranslation=False)
            if self._translations is not None:
                self._paintTrack(painter, result, start, end, line + 2 * self._lineHeight,
                                 isQuery=True, isTranslation=True)
            y += self._rowHeight
            painter.setPen(QColor(LINE))
            painter.drawLine(0, y - 4, self.viewport().width(), y - 4)
            start = end

    def _hit(self, position) -> tuple[int, int] | None:
        if self._result is None or position.x() < self._tracksLeft:
            return None
        y = position.y() + self.verticalScrollBar().value()
        row, localY = divmod(y, self._rowHeight)
        groupWidth = 10 * self._characterWidth + 5
        group, localX = divmod(position.x() - self._tracksLeft, groupWidth)
        offset = group * 10 + localX // self._characterWidth
        index = row * self._columnsPerRow + offset
        line = (localY // self._lineHeight) - 1
        trackCount = 4 if self._translations is not None else 2
        if (localX < 10 * self._characterWidth and offset < self._columnsPerRow
                and 0 <= line < trackCount and index < self._result.alignedLength):
            return index, line
        return None

    def mousePressEvent(self, event) -> None:
        hit = self._hit(event.position().toPoint())
        if hit is not None and event.button() == Qt.MouseButton.LeftButton:
            self.columnPicked.emit(hit[0])
            event.accept()
            return
        super().mousePressEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            hit = self._hit(event.pos())
            if hit is not None and self._result is not None:
                index, line = hit
                text = f"Column {index + 1:,}\n{mappingText(self._result, index)}"
                if self._translations is not None and line in (0, 3):
                    codon = self._translations[int(line == 3)][index]
                    if codon is not None:
                        effect = codon.effect.capitalize()
                        text += f"\n{codon.triplet} → {codon.aminoAcid} · {effect}"
                QToolTip.showText(event.globalPos(), text, self.viewport())
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().viewportEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if self._result is not None and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            index = self._selected + (-1 if key == Qt.Key.Key_Left else 1)
            self.columnPicked.emit(min(self._result.alignedLength - 1, max(0, index)))
            event.accept()
            return
        super().keyPressEvent(event)
