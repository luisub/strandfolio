"""Interactive ancestry cards and source intervals from archived History evidence."""

from collections import defaultdict
from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QGraphicsView, QWidget

from strandfolio.domain.sequence import SequenceRevision
from strandfolio.rendering import colors
from strandfolio.rendering.history import RecordedHistory


EVIDENCE_LABELS = {
    "source": "Source", "recorded": "Locally recorded", "missing": "Missing evidence",
    "malformed": "Invalid evidence", "cycle": "Circular link", "unsupported": "Unsupported evidence",
}
OPERATION_LABELS = {
    "digest": "Restriction digest", "pcr": "PCR",
    "annotation": "Annotation edit", "name": "Named revision",
}
INPUT_LABELS = {
    "template": "Template", "parent": "Previous revision",
    "forwardPrimer": "Forward primer", "reversePrimer": "Reverse primer",
}
_BAND_COLORS = {
    "template": colors.GREEN, "parent": colors.GREEN,
    "forwardPrimer": colors.ACCENT, "reversePrimer": colors.AMBER,
}
_CARD_WIDTH = 212
_CARD_HEIGHT = 76
_COLUMN = _CARD_WIDTH + 18
_ROW = _CARD_HEIGHT + 36
CardKey = tuple[str, int | None]


def moleculeUnits(revision: SequenceRevision) -> str:
    """Return the displayed coordinate unit for this snapshot’s molecule type."""
    return {"DNA": "bp", "RNA": "nt", "protein": "aa"}[revision.moleculeType]


@dataclass(frozen=True, slots=True)
class _Card:
    name: str
    kind: str
    summary: str
    identity: str
    length: int = 0
    circular: bool = False
    hasGap: bool = False


class _MoleculeItem(QGraphicsRectItem):
    def __init__(self, card: _Card, bands: list[tuple[int, int, str]], font: QFont) -> None:
        super().__init__(0, 0, _CARD_WIDTH, _CARD_HEIGHT)
        self.card = card
        self._bands = bands
        self._font = font
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(escape(f"{card.name}\n{card.kind} · {card.summary}\n{card.identity}"))

    def paint(self, painter, option, widget=None) -> None:
        bounds = self.rect()
        selected = self.isSelected()
        painter.setPen(QPen(QColor(colors.ACCENT if selected else colors.LINE), 2 if selected else 1))
        painter.setBrush(QColor(colors.SELECTION if selected else colors.CANVAS))
        painter.drawRoundedRect(bounds, 9, 9)
        self._drawMap(painter)
        font = QFont(self._font)
        for label, top, bold, size, color in (
            (self.card.kind, 7, False, 11, colors.AMBER if self.card.hasGap else colors.MUTED),
            (self.card.name, 26, True, 14, colors.INK),
            (self.card.summary, 49, False, 11, colors.MUTED),
        ):
            font.setBold(bold)
            font.setPixelSize(size)
            painter.setFont(font)
            painter.setPen(QColor(color))
            textRect = QRectF(56, top, _CARD_WIDTH - 65, 18)
            text = QFontMetrics(font).elidedText(label, Qt.TextElideMode.ElideMiddle,
                                                 int(textRect.width()))
            painter.drawText(textRect, Qt.AlignmentFlag.AlignVCenter, text)

    def _drawMap(self, painter: QPainter) -> None:
        rect = QRectF(11, 21, 33, 33)
        left, right, y = rect.left(), rect.right(), rect.center().y()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        bandPen = QPen(QColor(colors.LINE), 5)
        bandPen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(bandPen)
        if self.card.circular:
            painter.drawEllipse(rect)
        else:
            painter.drawLine(QPointF(left, y), QPointF(right, y))
        for start, end, color in self._bands:
            bandPen.setColor(QColor(color))
            painter.setPen(bandPen)
            if self.card.circular:
                painter.drawArc(rect, round((0.25 - start / self.card.length) * 5760),
                                -round((end - start) / self.card.length * 5760))
            else:
                scale = rect.width() / self.card.length
                painter.drawLine(QPointF(left + start * scale, y), QPointF(left + end * scale, y))
        painter.setPen(QPen(QColor(colors.MUTED), 1))
        if self.card.circular:
            origin = rect.center().x()
            painter.drawLine(QPointF(origin, rect.top() - 4), QPointF(origin, rect.top() + 3))
        else:
            for x in (left, right):
                painter.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))


class HistoryDiagram(QGraphicsView):
    """Show resolved revisions and individually recorded input uses in ancestor-first order."""

    activated = Signal(str, object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAccessibleName("Recorded ancestry diagram")
        self.setAccessibleDescription("Use the Revision list for keyboard access to each snapshot.")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFrameShape(self.Shape.NoFrame)
        self.setBackgroundBrush(QColor(colors.CANVAS))
        self.setScene(QGraphicsScene(self))
        self._items: dict[CardKey, _MoleculeItem] = {}
        self._inputKeys: dict[tuple[str, int], CardKey] = {}
        self._selected: _MoleculeItem | None = None

    def setHistory(self, history: RecordedHistory | None) -> None:
        self._items.clear()
        self._inputKeys.clear()
        self._selected = None
        self.scene().clear()
        self.scene().setSceneRect(QRectF())
        if history is None:
            return
        # The resolver provides ancestor-first snapshots and cuts cycles at an explicit gap.
        # Primer occurrences have recorded intervals, but no complete sequence snapshot.
        levels: dict[CardKey, int] = {}
        cards = {}
        uses = defaultdict(list)
        products = defaultdict(list)
        edges = []
        for node in history.nodes:
            revision = node.revision
            key = (revision.revisionId, None)
            level = 0
            for source in node.inputs:
                parent = (source.sourceRevisionId, None)
                if source.role in ("parent", "template") and parent in levels:
                    level = max(level, levels[parent] + 1)
                else:
                    level = max(level, 1)
            for index, source in enumerate(node.inputs):
                parent = (source.sourceRevisionId, None)
                if source.role not in ("parent", "template") or parent not in levels:
                    parent = (revision.revisionId, index)
                    missing = source.role in ("parent", "template")
                    cards[parent] = _Card(
                        source.sourceName, INPUT_LABELS[source.role],
                        "Snapshot unavailable" if missing else "Recorded input",
                        source.sourceRevisionId, hasGap=missing,
                    )
                    levels[parent] = level - 1
                self._inputKeys[(revision.revisionId, index)] = parent
                color = _BAND_COLORS[source.role]
                uses[parent].extend((*span.sourceSpan, color) for span in source.spans)
                products[key].extend((*span.productSpan, color) for span in source.spans)
                edges.append((parent, key, index, len(node.inputs)))
            levels[key] = level
            kind = OPERATION_LABELS.get(node.operation, "Source molecule")
            gap = node.status not in ("source", "recorded")
            if gap:
                kind = EVIDENCE_LABELS[node.status]
            kindLabel = revision.topology or revision.moleculeType
            summary = f"{len(revision):,} {moleculeUnits(revision)} · {kindLabel}"
            cards[key] = _Card(revision.name, kind, summary, revision.revisionId,
                               len(revision), revision.topology == "circular", gap)
        rows = defaultdict(list)
        for key, level in levels.items():
            rows[level].append(key)
        for level, keys in rows.items():
            for column, key in enumerate(keys):
                card = cards[key]
                bands = (uses[key] or products[key]) if card.length else []
                item = _MoleculeItem(card, bands, self.font())
                item.setData(0, key[0])
                item.setData(1, key[1])
                item.setPos((column - (len(keys) - 1) / 2) * _COLUMN, level * _ROW)
                self.scene().addItem(item)
                self._items[key] = item
        pen = QPen(QColor(colors.MUTED), 1.25)
        for parent, child, index, count in edges:
            source = self._items[parent].sceneBoundingRect()
            target = self._items[child].sceneBoundingRect()
            offset = (index + 1) / (count + 1)
            start = QPointF(source.center().x(), source.bottom())
            end = QPointF(target.left() + target.width() * offset, target.top() - 2)
            midY = (start.y() + end.y()) / 2
            path = QPainterPath(start)
            path.cubicTo(start.x(), midY, end.x(), midY, end.x(), end.y())
            path.moveTo(end.x() - 3, end.y() - 5)
            path.lineTo(end)
            path.lineTo(end.x() + 3, end.y() - 5)
            self.scene().addPath(path, pen).setZValue(-1)
        self.scene().setSceneRect(self.scene().itemsBoundingRect().adjusted(-18, -18, 18, 18))

    def selectOccurrence(self, revisionId: str, inputIndex: int | None = None) -> None:
        """Highlight the chosen revision or the exact use selected in the inspector."""
        key = self._inputKeys.get((revisionId, inputIndex), (revisionId, None))
        item = self._items.get(key)
        if self._selected is not None:
            self._selected.setSelected(False)
        self._selected = item
        if item is not None:
            item.setSelected(True)
            self.ensureVisible(item, 8, 8)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, _MoleculeItem):
                self.activated.emit(item.data(0), item.data(1))
                event.accept()
                return
        # Background clicks keep the same inspected molecule.
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
