"""Paint sequence rows from shared geometry for the viewport and figure export."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF

from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.fragments import DuplexGeometry
from strandfolio.domain.sequence import MoleculeType
from strandfolio.gui import theme
from strandfolio.gui.views.restriction_sites import restrictionSiteColor
from strandfolio.operations.conversion import complementBases
from strandfolio.operations.restriction import RecognitionSite
from strandfolio.rendering import colors
from strandfolio.rendering.layout import (
    RowCdsTrack,
    RowFeatureSpan,
    RowRestrictionSpan,
    SequenceLayout,
    SequenceRowIndex,
    SequenceRowPlan,
)


def featureTrackRect(
    layout: SequenceLayout, span: RowFeatureSpan, plan: SequenceRowPlan, top: int,
) -> QRectF:
    """Return the shared painted and interactive bounds of a feature track."""
    left = layout.baseX(span.start - plan.rowStart)
    right = layout.baseX(span.end - plan.rowStart - 1) + layout.characterWidth
    y = top + plan.featuresTop + span.lane * SequenceRowIndex.FEATURE_STEP
    return QRectF(left, y, max(4.0, right - left), 13)


def restrictionTrackRect(
    layout: SequenceLayout, span: RowRestrictionSpan, plan: SequenceRowPlan, top: int,
) -> QRectF:
    """Return the shared painted and interactive bounds of a recognition label."""
    left = layout.baseX(span.start - plan.rowStart)
    right = layout.restrictionRight(plan.rowStart, span.start, span.end, span.site.enzymeName)
    y = top + plan.restrictionTop + span.lane * SequenceRowIndex.RESTRICTION_STEP
    return QRectF(left, y, right - left, 15)


class SequenceTrackPainter:
    """Draw a captured set of letters and visual preferences on any Qt paint device."""

    def __init__(
        self, layout: SequenceLayout, sequence: str, moleculeType: MoleculeType,
        font: QFont, labelFont: QFont, selection: SequenceSelection | None,
        recipe: frozenset[str], *, duplexGeometry: DuplexGeometry | None = None,
    ) -> None:
        self.layout = layout
        self.sequence = sequence
        self.moleculeType = moleculeType
        self.font = QFont(font)
        self.labelFont = QFont(labelFont)
        self.selection = selection
        self.recipe = recipe
        self.duplexGeometry = duplexGeometry

    def paintRow(
        self, painter: QPainter, plan: SequenceRowPlan, top: int, *,
        interval: SelectionSegment | None = None, showCaret: bool = False,
        hoveredFeatureId: str | None = None, focusedSite: RecognitionSite | None = None,
    ) -> None:
        """Draw an included slice in the native row grid without changing source positions."""
        start, end = interval or (plan.rowStart, plan.rowEnd)
        plan = plan.clippedTo(start, end)
        self._paintCoordinate(painter, plan, top, start, end)
        self._paintFeatures(painter, plan, top, hoveredFeatureId)
        self._paintBases(painter, plan, top, start, end)
        self._paintRestrictions(painter, plan, top, focusedSite)
        for trackIndex, track in enumerate(plan.cdsTracks):
            y = top + plan.translationTop + trackIndex * SequenceRowIndex.TRANSLATION_STEP
            self._paintTranslation(painter, plan, y, track, start, end)

        if showCaret and self.selection is not None:
            caret = self.selection.caret
            if start <= caret < end or caret == end == len(self.sequence):
                x = self.layout.baseX(caret - plan.rowStart)
                painter.setPen(QPen(QColor(theme.ACCENT), 1.5))
                painter.drawLine(QPointF(x, top + plan.forwardY),
                                 QPointF(x, top + plan.translationTop))

    def _paintCoordinate(
        self, painter: QPainter, plan: SequenceRowPlan, top: int, start: int, end: int,
    ) -> None:
        gutter = self.layout.gutter
        rowRight = self.layout.baseX(end - 1 - plan.rowStart) + self.layout.characterWidth
        rowLeft = self.layout.baseX(start - plan.rowStart)
        baseline = top + plan.rowHeight - 1
        painter.setPen(QPen(QColor("#eeebf3"), 1))
        painter.drawLine(QPointF(rowLeft, baseline), QPointF(rowRight, baseline))
        gutterFont = QFont(self.font)
        gutterFont.setPixelSize(10)
        selected = bool(self.selection and self.selection.clippedTo(start, end))
        gutterFont.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        painter.setFont(gutterFont)
        painter.setPen(QColor(colors.ACCENT_INK if selected else theme.MUTED))
        painter.drawText(
            QRectF(4, top + plan.forwardY, gutter - 28, self.layout.strandHeight),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{start + 1:,}",
        )
        font = QFont(self.labelFont)
        font.setWeight(QFont.Weight.Normal)
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        metrics = QFontMetricsF(font)
        previousRight = 0.0
        for coordinate in range((start // 20 + 1) * 20, end + 1, 20):
            label = f"{coordinate:,}"
            center = self.layout.baseX(coordinate - 1 - plan.rowStart) + self.layout.characterWidth / 2
            width = metrics.horizontalAdvance(label) + 2
            left = center - width / 2
            if left >= previousRight + 6 and left + width <= self.layout.viewportWidth:
                painter.drawText(QRectF(left, top + 1, width, 14), Qt.AlignmentFlag.AlignCenter, label)
                previousRight = left + width
        if self.moleculeType == "protein":
            return
        right = self.layout.baseX(end - plan.rowStart - 1) + self.layout.characterWidth + 4
        strands = [(plan.forwardY, "5′", "3′")]
        if plan.complementY is not None:
            strands.append((plan.complementY, "3′", "5′"))
        for y, leftEnd, rightEnd in strands:
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(QRectF(gutter - 20, top + y, 15, self.layout.strandHeight), align, leftEnd)
            painter.drawText(QRectF(right, top + y, 15, self.layout.strandHeight), align, rightEnd)

    def _paintFeatures(
        self, painter: QPainter, plan: SequenceRowPlan, top: int, hoveredFeatureId: str | None,
    ) -> None:
        if not plan.features:
            return
        painter.setFont(self.labelFont)
        metrics = QFontMetricsF(self.labelFont)
        selectedId = self.selection.featureId if self.selection is not None else None
        for span in plan.features:
            rect = featureTrackRect(self.layout, span, plan, top)
            isSelected = span.featureId == selectedId
            isHovered = span.featureId == hoveredFeatureId
            tip = min(6.0, rect.width() * 0.45)
            x1, x2 = rect.left(), rect.right()
            y1, y2, cy = rect.top(), rect.bottom(), rect.center().y()
            if span.strand == 1 and span.endsHere:
                points = ((x1, y1), (x2 - tip, y1), (x2, cy), (x2 - tip, y2), (x1, y2))
            elif span.strand == -1 and span.startsHere:
                points = ((x1 + tip, y1), (x2, y1), (x2, y2), (x1 + tip, y2), (x1, cy))
            else:
                points = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
            fill = QColor(span.color)
            if isSelected or isHovered:
                painter.setPen(QPen(QColor(theme.TEXT if isSelected else theme.ACCENT), 1.5))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
            if rect.width() >= 30:
                text = span.label
                if (span.strand and not span.startsHere and not span.endsHere
                        and metrics.horizontalAdvance(text) + 28 < rect.width()):
                    text += " →" if span.strand == 1 else " ←"
                text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width() - 8)
                painter.setPen(QColor(colors.featureTextColor(span.color)))
                painter.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, text)

    def _paintBases(
        self, painter: QPainter, plan: SequenceRowPlan, top: int, start: int, end: int,
    ) -> None:
        if self.selection:
            for segStart, segEnd in self.selection.clippedTo(start, end):
                left = self.layout.baseX(segStart - plan.rowStart)
                right = self.layout.baseX(segEnd - 1 - plan.rowStart) + self.layout.characterWidth
                rect = QRectF(left, top + plan.forwardY, right - left, plan.translationTop - plan.forwardY)
                painter.fillRect(rect, QColor(theme.SELECTION))
                painter.fillRect(QRectF(left, rect.bottom() - 1, rect.width(), 1), QColor("#ad93d8"))
        painter.setFont(self.font)
        geometry = self.duplexGeometry
        forwardStart, forwardEnd = start, end
        if geometry is not None:
            forwardStart = max(start, geometry.forwardSpan.start)
            forwardEnd = min(end, geometry.forwardSpan.end)
        self._paintLetters(painter, self.sequence[forwardStart:forwardEnd],
                           forwardStart - plan.rowStart, top + plan.forwardY)
        if plan.complementY is not None:
            reverseStart, reverseEnd = start, end
            if geometry is not None:
                reverseStart = max(start, geometry.reverseSpan.start)
                reverseEnd = min(end, geometry.reverseSpan.end)
            complement = complementBases(self.sequence[reverseStart:reverseEnd], self.moleculeType)
            self._paintLetters(painter, complement, reverseStart - plan.rowStart,
                               top + plan.complementY, isComplement=True)

    def _paintLetters(
        self, painter: QPainter, letters: str, column: int, y: int, *, isComplement: bool = False,
    ) -> None:
        # Keep the SVG scene compact by grouping equal paint styles within each strand row.
        byColor: dict[str, list[tuple[int, str]]] = {}
        for position, letter in enumerate(letters, column):
            color = theme.TEXT
            if self.moleculeType != "protein":
                fallback = theme.MUTED if isComplement else theme.TEXT
                color = theme.BASE_COLORS.get(letter.upper(), fallback)
            byColor.setdefault(color, []).append((position, letter))
        height = self.layout.strandHeight
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for color, coloredLetters in byColor.items():
            painter.setPen(QColor(color))
            for position, letter in coloredLetters:
                cell = QRectF(self.layout.baseX(position), y, self.layout.characterWidth, height)
                painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, letter)

    def _paintRestrictions(
        self, painter: QPainter, plan: SequenceRowPlan, top: int,
        focusedSite: RecognitionSite | None,
    ) -> None:
        if not plan.restrictionSpans and not plan.restrictionMarks:
            return
        painter.setFont(self.labelFont)
        metrics = QFontMetricsF(self.labelFont)
        layout = self.layout
        for span in plan.restrictionSpans:
            rect = restrictionTrackRect(layout, span, plan, top)
            color = restrictionSiteColor(span.site, self.recipe, isFocused=span.site == focusedSite)
            fill = QColor(color)
            fill.setAlpha(16)
            line = Qt.PenStyle.SolidLine if span.site.certainty == "definite" else Qt.PenStyle.DashLine
            painter.setPen(QPen(color, 1, line))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 3, 3)
            text = metrics.elidedText(span.site.enzymeName, Qt.TextElideMode.ElideRight, rect.width() - 5)
            painter.drawText(rect.adjusted(3, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, text)
            motifRight = layout.baseX(span.end - plan.rowStart - 1) + layout.characterWidth
            painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(motifRight, rect.bottom()))

        for mark in plan.restrictionMarks:
            column = mark.boundary - plan.rowStart
            if mark.boundary == plan.rowEnd:
                x = layout.baseX(column - 1) + layout.characterWidth
            else:
                x = layout.baseX(column)
            y = top + plan.forwardY + self.layout.strandHeight - 1
            if not mark.isTopStrand and plan.complementY is not None:
                y = top + plan.translationTop - 1
            color = theme.ACCENT if self.recipe & mark.enzymeNames else colors.AMBER
            tip = -4 if mark.isTopStrand else 4
            painter.setPen(QPen(QColor(color), 1.6))
            painter.drawLine(QPointF(x, y), QPointF(x, y - 7))
            painter.drawLine(QPointF(x, y), QPointF(x + tip, y))

    def _paintTranslation(
        self, painter: QPainter, plan: SequenceRowPlan, y: int, track: RowCdsTrack,
        start: int, end: int,
    ) -> None:
        codons = [codon for codon in track.codons if start <= codon.centerPosition < end]
        if not codons:
            return
        labelFont = QFont(self.labelFont)
        labelFont.setPixelSize(10)
        labelFont.setWeight(QFont.Weight.Normal)
        painter.setFont(labelFont)
        painter.setPen(QColor(theme.MUTED))
        arrow = {1: "→", -1: "←"}.get(track.strand, "")
        label = "CDS" if track.frame is None else f"{track.frame:+d}"
        tag = f"{label} {arrow}".strip()
        hasOverride = track.annotatedTable is not None and track.annotatedTable != track.translTable
        if hasOverride:
            tag = f"Table {track.translTable} ({track.annotatedTable})"
        elif track.translTable != 1:
            tag += f" · T{track.translTable}"
        gutterWidth = self.layout.gutter - 14
        metrics = QFontMetricsF(labelFont)
        if hasOverride and metrics.horizontalAdvance(tag) > gutterWidth:
            tag = f"{track.translTable} ({track.annotatedTable})"
        painter.drawText(QRectF(4, y, gutterWidth, SequenceRowIndex.TRANSLATION_STEP),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tag)

        aminoFont = QFont(self.font)
        aminoFont.setPixelSize(min(13, self.font.pixelSize()))
        painter.setFont(aminoFont)
        for codon in codons:
            x = self.layout.baseX(codon.centerPosition - plan.rowStart)
            cell = QRectF(x, y, self.layout.characterWidth, SequenceRowIndex.TRANSLATION_STEP)
            if self.selection and all(self.selection.contains(base) for base in codon.positions):
                painter.fillRect(cell, QColor(theme.SELECTION))
            painter.setPen(QColor(colors.RED if codon.aminoAcid == "*" else colors.ACCENT_INK))
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, codon.aminoAcid)
