"""Capture and export the native sequence detail grid as a vector figure."""

from math import ceil
import re

from PySide6.QtCore import QBuffer, QIODevice, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtSvg import QSvgGenerator

from strandfolio.domain.coordinates import SelectionSegment
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui import theme
from strandfolio.gui.views.sequence_tracks import SequenceTrackPainter
from strandfolio.rendering.layout import SequenceRowIndex


_HEADER_HEIGHT = 117
_SEGMENT_HEADER_HEIGHT = 23
_PADDING = 12
# Text, paths and their style groups all become nodes in the Qt SVG preview.
_MAX_ESTIMATED_DRAW_ITEMS = 30_000
_PHYSICAL_SVG_SIZE = re.compile(r'(<svg\b[^>]*?)width="[^"]+" height="[^"]+"')
_INVALID_XML_CHARACTERS = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def _coordinates(start: int, end: int) -> str:
    return f"{start + 1:,}–{end:,}" if end - start != 1 else f"{end:,}"


class SequenceDetailFigure:
    """Capture the sequence rows, coordinates and visible tracks for export."""

    def __init__(
        self, revision: SequenceRevision, rowIndex: SequenceRowIndex, tracks: SequenceTrackPainter,
        displayName: str, intervals: tuple[SelectionSegment, ...], scope: str,
        restrictionNames: tuple[str, ...],
    ) -> None:
        if not intervals or any(not 0 <= start < end <= len(revision) for start, end in intervals):
            raise ValueError("Sequence detail requires a nonempty region in the current revision.")
        self.revision = revision
        self.intervals = intervals
        self.scope = scope
        self.displayName = displayName or revision.name
        self._rowIndex = rowIndex
        self._tracks = tracks
        self._restrictionNames = restrictionNames if rowIndex.hasRestrictionSites else ()
        self._length = sum(end - start for start, end in intervals)
        self._unit = "aa" if revision.moleculeType == "protein" else "nt"
        self._direction = "Stored protein N→C" if self._unit == "aa" else "Stored strand 5′→3′"
        if rowIndex.hasComplement:
            self._direction += " · aligned complement 3′→5′"
        frame = rowIndex.frameTranslation
        if frame is not None:
            self._direction += f" · frame {frame.frame:+d}, table {frame.translTable}"
        if scope == "Selected region" and tracks.selection is not None:
            strand = {1: "+", -1: "−"}.get(tracks.selection.strand)
            if strand is not None:
                self._direction += f" · selected strand {strand}"
        options = rowIndex.options
        layers = []
        if options.showFeatures:
            layers.append("features")
        if options.showPrimers:
            layers.append("primer annotations")
        if rowIndex.hasCdsTranslations:
            layers.append("codons with effective genetic code")
        if rowIndex.hasRestrictionSites:
            layers.append("recognition boxes")
        if rowIndex.hasRestrictionCuts:
            layers.append("predicted cut ticks")
        self._layers = " · ".join(layers)
        layout = rowIndex.layout
        minimumRowWidth = layout.baseX(layout.basesPerRow - 1) + layout.characterWidth + 23
        self._rowWidth = max(ceil(layout.viewportWidth), ceil(minimumRowWidth))
        self.defaultWidth = max(320, self._rowWidth)

    @property
    def description(self) -> str:
        """Describe the source scope, width-independent row scale and active layers."""
        bounds = ", ".join(_coordinates(*interval) for interval in self.intervals[:4])
        if len(self.intervals) > 4:
            bounds += f" and {len(self.intervals) - 4:,} more"
        scope = f"{self.scope} · {bounds} · {self._length:,} {self._unit}"
        scale = f"{self._rowIndex.layout.basesPerRow:,} {self._unit} per row"
        lines = [scope, self._direction, scale]
        if self._layers:
            lines.append(f"Enabled record layers · {self._layers}")
        return "\n".join(lines)

    def _rowScale(self, width: int) -> float:
        if type(width) is not int or width <= 0:
            raise ValueError("Choose a positive output width for the sequence figure.")
        return min(1.0, width / self._rowWidth)

    def heightForWidth(self, width: int) -> int:
        """Calculate logical figure bounds without building its rows or allocating a raster."""
        scale = self._rowScale(width)
        height = _HEADER_HEIGHT + _PADDING
        for start, end in self.intervals:
            rows = self._rowIndex.layout.rowsCovering(start, end)
            rowHeight = self._rowIndex.rowTop(rows.stop) - self._rowIndex.rowTop(rows.start)
            height += _SEGMENT_HEADER_HEIGHT + ceil(rowHeight * scale)
        return height

    def renderSvg(self, width: int) -> str:
        """Emit the captured scene as SVG with a viewBox and inline paint styles."""
        self._checkSceneSize()
        height = self.heightForWidth(width)
        buffer = QBuffer()
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError("Could not open the sequence figure buffer.")
        generator = QSvgGenerator()
        generator.setOutputDevice(buffer)
        generator.setSize(QSize(width, height))
        generator.setViewBox(QRect(0, 0, width, height))
        generator.setTitle(self.displayName)
        intervals = ", ".join(_coordinates(*interval) for interval in self.intervals)
        metadata = [
            self.description,
            f"Coordinates (one-based inclusive, in displayed order) {intervals}",
            f"Record {self.revision.recordId}\nRevision {self.revision.revisionId}",
        ]
        if self._restrictionNames:
            metadata.append(f"Active restriction filter {', '.join(self._restrictionNames)}")
        generator.setDescription("\n".join(metadata))
        painter = QPainter(generator)
        try:
            self._paint(painter, width, height)
        finally:
            painter.end()
        # QSvgGenerator writes millimetres derived from its DPI setting. Use explicit
        # pixel dimensions so reopening the same SVG also gives the requested PNG size.
        svg = bytes(buffer.data()).decode("utf-8")
        svg, sizeCount = _PHYSICAL_SVG_SIZE.subn(rf'\g<1>width="{width}" height="{height}"', svg, 1)
        if sizeCount != 1:
            raise ValueError("Could not set the sequence figure's pixel dimensions.")
        return _INVALID_XML_CHARACTERS.sub("\ufffd", svg)

    def _checkSceneSize(self) -> None:
        error = (
            "This sequence detail is too large to preview. "
            "Select a smaller region, hide layers, or use the map."
        )
        rowIndex = self._rowIndex
        estimated = self._length * (1 + rowIndex.hasComplement)
        if estimated > _MAX_ESTIMATED_DRAW_ITEMS:
            raise ValueError(error)
        selection = self._tracks.selection
        for start, end in self.intervals:
            for row in rowIndex.layout.rowsCovering(start, end):
                plan = rowIndex.rowPlan(row)
                first, last = max(start, plan.rowStart), min(end, plan.rowEnd)
                plan = plan.clippedTo(first, last)
                estimated += 5 if rowIndex.hasComplement else 3
                estimated += last // 20 - first // 20
                estimated += 2 * len(plan.features) + 3 * len(plan.restrictionSpans)
                estimated += 2 * len(plan.restrictionMarks)
                estimated += sum(1 + 2 * len(track.codons) for track in plan.cdsTracks)
                if selection is not None:
                    estimated += sum(
                        1 + (b - 1) // 10 - a // 10 for a, b in selection.clippedTo(first, last)
                    )
                if estimated > _MAX_ESTIMATED_DRAW_ITEMS:
                    raise ValueError(error)

    def _paint(self, painter: QPainter, width: int, height: int) -> None:
        painter.fillRect(QRect(0, 0, width, height), QColor(theme.PANEL))
        titleFont = QFont(self._tracks.labelFont)
        titleFont.setPixelSize(19)
        textFont = QFont(self._tracks.labelFont)
        textFont.setBold(False)
        textFont.setPixelSize(11)
        name = " ".join(self.displayName.split())
        self._paintLabel(painter, name, width, 8, 28, titleFont, theme.TEXT)
        descriptor = self.description.splitlines()
        identity = f"Revision {self.revision.revisionId} · {self.revision.topology or 'unknown topology'}"
        for index, line in enumerate((*descriptor, identity)):
            self._paintLabel(painter, line, width, 36 + index * 15, 15, textFont, theme.MUTED)

        scale = self._rowScale(width)
        top = _HEADER_HEIGHT
        for index, (start, end) in enumerate(self.intervals, 1):
            coordinates = f"Coordinates {_coordinates(start, end)}"
            if len(self.intervals) > 1:
                coordinates = f"Segment {index} / {len(self.intervals)} · {coordinates}"
            self._paintLabel(painter, coordinates, width, top, _SEGMENT_HEADER_HEIGHT,
                             self._tracks.labelFont, theme.TEXT)
            top += _SEGMENT_HEADER_HEIGHT
            rows = self._rowIndex.layout.rowsCovering(start, end)
            origin = self._rowIndex.rowTop(rows.start)
            painter.save()
            painter.translate(0, top)
            painter.scale(scale, scale)
            for row in rows:
                plan = self._rowIndex.rowPlan(row)
                interval = (max(start, plan.rowStart), min(end, plan.rowEnd))
                self._tracks.paintRow(painter, plan, self._rowIndex.rowTop(row) - origin,
                                      interval=interval)
            painter.restore()
            top += ceil((self._rowIndex.rowTop(rows.stop) - origin) * scale)

    @staticmethod
    def _paintLabel(
        painter: QPainter, text: str, width: int, top: int, height: int, font: QFont, color: str,
    ) -> None:
        available = max(1, width - 2 * _PADDING)
        metrics = QFontMetricsF(font)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, available)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRectF(_PADDING, top, available, height),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)
