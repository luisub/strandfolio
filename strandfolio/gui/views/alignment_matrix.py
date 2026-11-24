"""Render shared alignment columns and navigate them at overview and base resolution."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHeaderView, QSlider, QStyle, QStyledItemDelegate, QTableView, QWidget

from strandfolio.gui.panels.alignment_regions import regionCaption
from strandfolio.rendering.colors import ACCENT, ACCENT_INK, AMBER, AMBER_SOFT, INK, LINE, MUTED, SELECTION

if TYPE_CHECKING:
    from strandfolio.operations.multiple_alignment import MultipleAlignmentResult


class MatrixModel(QAbstractTableModel):
    """Serve visible cells directly from the validated rows without per-cell widget allocation."""

    def __init__(self, result: MultipleAlignmentResult, names: tuple[str, ...], parent: QTableView) -> None:
        super().__init__(parent)
        self.result = result
        self.names = names
        self._gapBrush = QBrush(QColor(SELECTION))
        self._differenceBrush = QBrush(QColor(AMBER_SOFT))
        self._gapInk = QBrush(QColor(ACCENT_INK))
        self._differenceInk = QBrush(QColor(AMBER))

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.result.inputs)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self.result.columnCount

    def rowIdentity(self, row: int) -> str:
        source = self.result.inputs[row]
        region = regionCaption(source.featureLabel, source.selection.segments)
        strand = ""
        if source.moleculeType != "protein":
            strand = " · reverse strand" if source.strand == -1 else " · forward strand"
        return f"{self.result.rowId(row)} · {self.names[row]} · {region}{strand}"

    def cellDetail(self, row: int, column: int) -> str:
        position = self.result.sourcePosition(row, column)
        symbol = self.result.alignedRows[row][column]
        unit = "residue" if self.result.inputs[row].moleculeType == "protein" else "base"
        location = "Gap" if position is None else f"{symbol} · source {unit} {position + 1:,}"
        return f"{self.result.rowId(row)} · Column {column + 1:,} · {location}"

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            column = section + 1
            if role in (Qt.ItemDataRole.AccessibleTextRole, Qt.ItemDataRole.ToolTipRole):
                return str(column)
            if role == Qt.ItemDataRole.DisplayRole and (column == 1 or column % 5 == 0):
                return str(column)
        elif role == Qt.ItemDataRole.DisplayRole:
            return f"{self.result.rowId(section)} · {self.names[section]}"
        elif role == Qt.ItemDataRole.AccessibleTextRole:
            return self.rowIdentity(section)
        elif role == Qt.ItemDataRole.ToolTipRole:
            return escape(self.rowIdentity(section))
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.result.alignedRows[row][column]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role in (Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ForegroundRole):
            symbol = self.result.alignedRows[row][column]
            background = role == Qt.ItemDataRole.BackgroundRole
            if symbol == "-":
                return self._gapBrush if background else self._gapInk
            if row and symbol != self.result.alignedRows[0][column]:
                return self._differenceBrush if background else self._differenceInk
        if role in (Qt.ItemDataRole.AccessibleTextRole, Qt.ItemDataRole.ToolTipRole):
            return self.cellDetail(row, column)
        return None


class MatrixTable(QTableView):
    """Keep the current cell in view when the window is resized."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setItemDelegate(_SymbolDelegate(self))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.currentIndex().isValid():
            self.scrollTo(self.currentIndex())


class _SymbolDelegate(QStyledItemDelegate):
    """Paint matches as text and give gaps, differences and the current cell distinct marks."""

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        table = self.parent()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        currentColumn = index.column() == table.currentIndex().column()
        painter.save()
        background = "#f1edf9" if currentColumn else ("#faf9fd" if index.row() % 2 else "#ffffff")
        painter.fillRect(rect, QColor(background))
        painter.setPen(QColor("#eeeaf4"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        fill = index.data(Qt.ItemDataRole.BackgroundRole)
        ink = index.data(Qt.ItemDataRole.ForegroundRole)
        if selected or fill is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            mark = QRectF(rect).adjusted(2, 7, -2, -7)
            painter.setBrush(QColor(ACCENT) if selected else fill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(mark, 4, 4)
        painter.setFont(table.font())
        painter.setPen(QColor("white") if selected else (ink.color() if ink is not None else QColor(INK)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, index.data())
        painter.restore()


class SourceHeader(QHeaderView):
    """Show a stable sequence name and comparison role beside each row."""

    def __init__(self, table: QTableView) -> None:
        super().__init__(Qt.Orientation.Vertical, table)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setDefaultSectionSize(40)
        self.setFixedWidth(154)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:
        model = self.model()
        if not isinstance(model, MatrixModel) or not 0 <= logicalIndex < len(model.names):
            return super().paintSection(painter, rect, logicalIndex)
        painter.save()
        painter.fillRect(rect, QColor("#f8f6fc"))
        painter.setPen(QColor(LINE))
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(INK))
        nameRect = rect.adjusted(12, 4, -10, -18)
        name = painter.fontMetrics().elidedText(
            model.names[logicalIndex], Qt.TextElideMode.ElideMiddle, nameRect.width(),
        )
        painter.drawText(nameRect, Qt.AlignmentFlag.AlignVCenter, name)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(ACCENT_INK if logicalIndex == 0 else MUTED))
        role = "Reference" if logicalIndex == 0 else f"Query {logicalIndex}"
        painter.drawText(rect.adjusted(12, 22, -10, -3), Qt.AlignmentFlag.AlignVCenter, role)
        painter.restore()


class ColumnOverview(QSlider):
    """Locate differences across the matrix and move the current row to a column."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._marks = bytearray()
        self.setFixedHeight(26)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Alignment column overview")
        self.setToolTip("Click or drag to a column. Use the arrow keys for single columns.")

    def setResult(self, result: MultipleAlignmentResult) -> None:
        count = result.columnCount
        # Fixed-size display bins bound paint cost even when the matrix is zoomed out.
        self._marks = bytearray(min(512, count))
        reference = result.alignedRows[0]
        for row in result.alignedRows:
            for index, symbol in enumerate(row):
                if symbol == "-":
                    self._marks[index * len(self._marks) // count] |= 1
                elif symbol != reference[index]:
                    self._marks[index * len(self._marks) // count] |= 2
        self.setRange(1, count)
        self.setPageStep(max(1, count // 20))
        self.setAccessibleDescription(f"{count:,} columns. Violet marks gaps and gold marks differences from the reference.")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(5, 7, max(1, self.width() - 10), 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eeeaf5"))
        painter.drawRoundedRect(track, 3, 3)
        for index, kind in enumerate(self._marks):
            x = track.left() + index * track.width() / len(self._marks)
            width = max(2, track.width() / len(self._marks))
            if kind & 1:
                painter.fillRect(QRectF(x, track.top() + 1, width, 5), QColor("#9168ce"))
            if kind & 2:
                painter.fillRect(QRectF(x, track.center().y(), width, 5), QColor("#b87928"))
        fraction = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        x = track.left() + fraction * track.width()
        painter.setPen(QPen(QColor("white"), 1.5))
        painter.setBrush(QColor(ACCENT))
        painter.drawRoundedRect(QRectF(x - 3.5, 2, 7, 22), 3, 3)
        if self.hasFocus():
            painter.setPen(QPen(QColor(ACCENT_INK), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)

    def _pick(self, x: float) -> None:
        fraction = min(1.0, max(0.0, (x - 5) / max(1, self.width() - 10)))
        self.setValue(self.minimum() + round(fraction * (self.maximum() - self.minimum())))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._pick(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)
