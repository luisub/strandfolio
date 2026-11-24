"""Shared marker and gel preview controls, with a comparison workspace for pinned lanes."""

from collections.abc import Iterable
from html import escape
import json
from math import ceil

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from strandfolio.domain.fragments import FragmentSpan
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.digest_controller import DigestCoordinator
from strandfolio.gui.figure_dialog import FigureScope, exportFigure
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.operations.gel import GEL_LEGEND, GelLane, GelMarker, GelSelection, GelState
from strandfolio.operations.pcr import PcrProduct, PcrRun
from strandfolio.rendering.gels import (
    GEL_PCR_LEGEND,
    GelBand,
    GelLayout,
    buildGelLayout,
    gelBandIsSelected,
    gelData,
    gelFigureWidth,
    gelLabelPositions,
    gelLaneIdentity,
    gelSvg,
)


def formatSourceSpans(spans: tuple[FragmentSpan, ...]) -> str:
    """Format ordered half-open source spans as one-based inclusive intervals."""
    return formatSourceIntervals((span.start, span.end) for span in spans)


def formatSourceIntervals(intervals: Iterable[tuple[int, int]]) -> str:
    """Format ordered half-open source intervals as one-based inclusive intervals."""
    return " → ".join(f"{start + 1:,}–{end:,}" for start, end in intervals)


def formatPcrSource(product: PcrProduct) -> str:
    """Show canonical source intervals in product traversal order and their orientation."""
    spans = formatSourceIntervals(product.sourceSegments)
    orientation = "forward" if product.orientation == 1 else "reverse"
    return f"Source bases {spans} · {orientation} orientation"


class _GelCanvas(QWidget):
    """Draw bands on a shared size scale with mouse and keyboard selection."""

    bandActivated = Signal(object)
    focusTargetChanged = Signal(int, int)
    _AXIS_WIDTH = 61
    _LANE_WIDTH = 142
    _COMPACT_PLOT_TOP = 38
    _COMPACT_PLOT_PADDING = 47

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._compact = compact
        self._bandLabels = compact
        self._axisWidth = 49 if compact else self._AXIS_WIDTH
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Gel bands")
        self.setAccessibleDescription("Use arrow keys to focus a band and Enter or Space to select.")
        self._state: GelState | None = None
        self.gelLayout: GelLayout | None = None
        self._focusedBand = 0
        self._targets: list[tuple[QRectF, QRectF, GelLane, GelBand]] = []

    def setState(self, state: GelState) -> None:
        previous = self._state
        if previous is None or (previous.marker, previous.settings, previous.lanes) != (
            state.marker, state.settings, state.lanes,
        ):
            self.gelLayout = buildGelLayout(state)
            if previous is None or previous.settings != state.settings or previous.lanes != state.lanes:
                self._focusedBand = 0
            self._targets.clear()
            bandCounts = [len(self.gelLayout.marker.bands)]
            bandCounts.extend(len(lane.bands) for lane in self.gelLayout.lanes)
            if self._compact:
                tickCount = len(self.gelLayout.ticks)
                spaces = max(tickCount, *bandCounts) - 1
                self.setMinimumHeight(max(165, self._COMPACT_PLOT_PADDING + 14 * spaces))
                self._axisWidth = max(
                    49, *(self.fontMetrics().horizontalAdvance(f"{tick.sizeBp:,}") + 10
                          for tick in self.gelLayout.ticks),
                )
            else:
                self.setMinimumHeight(max(255, 74 + 14 * max(bandCounts)))
        self._state = state
        self.setBandLabels(self._bandLabels)

    def setBandLabels(self, visible: bool) -> None:
        self._bandLabels = visible
        if self._state is not None:
            width = self._LANE_WIDTH if visible else 65
            if visible and self._compact:
                lanes = (self.gelLayout.marker, *self.gelLayout.lanes)
                labelWidth = max((self.fontMetrics().horizontalAdvance(band.label)
                                  for lane in lanes for band in lane.bands), default=0)
                width = max(115, ceil((labelWidth + 18) / 0.59))
            self.setMinimumWidth(self._axisWidth + (len(self._state.lanes) + 1) * width)
        self.update()

    def _plotGeometry(self) -> tuple[int, int, float]:
        plotTop, padding = ((self._COMPACT_PLOT_TOP, self._COMPACT_PLOT_PADDING)
                            if self._compact else (54, 74))
        laneWidth = (self.width() - self._axisWidth) / (len(self._state.lanes) + 1)
        return plotTop, self.height() - padding, laneWidth

    def selectedBandRect(self) -> QRectF | None:
        """Locate the selected band and its label on the current canvas."""
        if self._state is None or self._state.selection is None:
            return None
        plotTop, plotHeight, laneWidth = self._plotGeometry()
        for index, lane in enumerate(self.gelLayout.lanes, 1):
            if lane.lane.laneId != self._state.selection.laneId:
                continue
            yPositions = gelLabelPositions(tuple(band.position for band in lane.bands), plotHeight, 14)
            for band, labelPosition in zip(lane.bands, yPositions):
                if gelBandIsSelected(self._state.selection, lane.lane, band):
                    bandY = plotTop + band.position * plotHeight
                    labelY = plotTop + labelPosition * plotHeight
                    top = min(bandY, labelY) - 8
                    bottom = max(bandY, labelY) + 8
                    return QRectF(self._axisWidth + index * laneWidth, top, laneWidth, bottom - top)
        return None

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.gelLayout is None or self._state is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#261d32"))
        plotTop, plotHeight, laneWidth = self._plotGeometry()
        axisWidth = self._axisWidth
        selection = self._state.selection
        if selection is not None:
            for index, lane in enumerate(self._state.lanes, 1):
                if lane.laneId == selection.laneId:
                    left = axisWidth + index * laneWidth
                    painter.fillRect(QRectF(left, 2, laneWidth - 2, self.height() - 4),
                                     QColor("#362a44"))
                    break
        self._targets.clear()
        painter.setPen(QColor("#c1b1cf"))
        painter.drawText(9, 22, "bp")
        ticks = self.gelLayout.ticks
        tickPositions = tuple(tick.position for tick in ticks)
        tickLabelHeight = 14 if self._compact else 16
        if self._compact:
            tickPositions = gelLabelPositions(tickPositions, plotHeight, tickLabelHeight)
        for tick, labelPosition in zip(ticks, tickPositions):
            y = plotTop + tick.position * plotHeight
            labelY = plotTop + labelPosition * plotHeight
            painter.setPen(QColor("#4d3f5a"))
            painter.drawLine(QPointF(axisWidth - 3, y),
                             QPointF(self.width() - 8 if self._bandLabels else axisWidth + 2, y))
            if abs(y - labelY) >= 1:
                painter.drawLine(QPointF(axisWidth - 6, labelY), QPointF(axisWidth - 3, y))
            painter.setPen(QColor("#c1b1cf"))
            painter.drawText(QRectF(0, labelY - tickLabelHeight / 2, axisWidth - 7, tickLabelHeight),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{tick.sizeBp:,}")
        self._drawLane(painter, "Marker" if self._bandLabels else "M", self.gelLayout.marker.bands, None,
                       axisWidth, laneWidth, plotTop, plotHeight)
        for index, laneLayout in enumerate(self.gelLayout.lanes, 1):
            title = f"{index}  {laneLayout.lane.label}" if self._bandLabels else str(index)
            self._drawLane(painter, title, laneLayout.bands, laneLayout.lane,
                           axisWidth + index * laneWidth, laneWidth, plotTop, plotHeight)
        if self.hasFocus() and self._targets:
            self._focusedBand = min(self._focusedBand, len(self._targets) - 1)
            bandRect, labelRect, _, _ = self._targets[self._focusedBand]
            painter.setPen(QPen(QColor("#efe6f7"), 1, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(bandRect.united(labelRect).adjusted(-2, 0, 2, 0), 2, 2)

    def _drawLane(
        self, painter: QPainter, name: str, bands: tuple[GelBand, ...], lane: GelLane | None,
        left: float, laneWidth: float, plotTop: int, plotHeight: int,
    ) -> None:
        label = self.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(laneWidth - 10))
        painter.setPen(QColor("#efe6f7"))
        painter.drawText(QRectF(left, 6, laneWidth - 8, 22), Qt.AlignmentFlag.AlignCenter, label)
        bandWidth = laneWidth * (0.41 if self._bandLabels else 0.55)
        bandLeft = left + (6 if self._bandLabels else (laneWidth - bandWidth) / 2)
        wellTop = 26 if self._compact else 36
        painter.fillRect(QRectF(bandLeft, wellTop, bandWidth, 7), QColor("#715f81"))
        labelPositions = gelLabelPositions(tuple(band.position for band in bands), plotHeight, 14)
        for band, labelPosition in zip(bands, labelPositions):
            y = plotTop + band.position * plotHeight
            labelY = plotTop + labelPosition * plotHeight
            selected = lane is not None and gelBandIsSelected(self._state.selection, lane, band)
            color = QColor("#cfb8ff" if selected else "#efe6f7")
            painter.fillRect(QRectF(bandLeft, y - 1.5, bandWidth, 3), color)
            bandRect = QRectF(bandLeft - 1, y - 5, bandWidth + 2, 10)
            labelRect = bandRect
            if self._bandLabels:
                painter.setPen(QPen(color, 1))
                bandRight = bandLeft + bandWidth
                painter.drawLine(QPointF(bandRight + 2, y), QPointF(bandRight + 9, labelY))
                labelRect = QRectF(bandRight + 11, labelY - 7, laneWidth - bandWidth - 17, 14)
                painter.drawText(labelRect, Qt.AlignmentFlag.AlignVCenter, band.label)
            if lane is not None:
                self._targets.append((bandRect, labelRect, lane, band))

    def _activateFocused(self) -> None:
        _, _, lane, band = self._targets[self._focusedBand]
        selection = self._state.selection
        contributors = tuple(product.outputReference for product in band.products)
        if selection is not None and selection.laneId == lane.laneId and (
            selection.outputReference in contributors
        ):
            self.bandActivated.emit(selection)
        else:
            self.bandActivated.emit(GelSelection(lane.laneId, contributors[0]))
        self.update()

    def _focusBand(self, index: int) -> None:
        self._focusedBand = index
        bandRect, labelRect, lane, band = self._targets[index]
        center = bandRect.united(labelRect).center()
        self.setAccessibleDescription(
            f"{lane.label} · {band.sizeBp:,} bp · {len(band.products)} contributors. "
            "Press Enter or Space to select.",
        )
        self.focusTargetChanged.emit(round(center.x()), round(center.y()))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position()
            hits = [
                (abs(point.y() - rect.center().y()), index)
                for index, target in enumerate(self._targets) for rect in target[:2]
                if rect.contains(point)
            ]
            if hits:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self._focusBand(min(hits)[1])
                self._activateFocused()
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if self._targets:
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down):
                step = -1 if key in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
                self._focusBand((self._focusedBand + step) % len(self._targets))
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self._activateFocused()
                return
        super().keyPressEvent(event)


class GelPreview(QWidget):
    """Choose a marker and inspect samples plotted on a shared size scale."""

    markerChanged = Signal(object)
    bandActivated = Signal(object)

    def __init__(
        self, analysis: DigestCoordinator, parent: QWidget | None = None,
        *, minimumPlotHeight: int = 180, compareLanes: bool = False,
    ) -> None:
        super().__init__(parent)
        self._analysis = analysis
        self._marker: GelMarker | None = None
        self._compact = not compareLanes
        self._shownState: GelState | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self._compact:
            layout.setSpacing(3)
        markerControls = QHBoxLayout()
        markerLabel = QLabel("&Ladder", self)
        self.ladderSelector = QComboBox(self)
        self.ladderSelector.setAccessibleName("Ladder")
        self.ladderSelector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.ladderSelector.setMinimumWidth(130)
        markerLabel.setBuddy(self.ladderSelector)
        self.ladderSelector.currentIndexChanged.connect(self._changeMarker)
        markerControls.addWidget(markerLabel)
        markerControls.addWidget(self.ladderSelector, 1)
        self.markerInfoButton = QPushButton("Marker details", self)
        self.markerInfoButton.setAutoDefault(False)
        self.markerInfoButton.clicked.connect(self._showMarker)
        markerControls.addWidget(self.markerInfoButton)
        self.retryLaddersButton = QPushButton("Retry ladder catalogue", self)
        self.retryLaddersButton.setAutoDefault(False)
        self.retryLaddersButton.clicked.connect(analysis.retryLadderCatalogue)
        layout.addLayout(markerControls)
        layout.addWidget(self.retryLaddersButton, 0, Qt.AlignmentFlag.AlignLeft)

        self.markerStatus = QLabel(self)
        self.markerStatus.setObjectName("muted")
        self.markerStatus.setTextFormat(Qt.TextFormat.PlainText)
        self.markerStatus.setWordWrap(True)
        layout.addWidget(self.markerStatus)
        self.canvas = _GelCanvas(self, compact=self._compact)
        if compareLanes:
            self.bandSizesButton = QCheckBox("Band sizes", self)
            self.bandSizesButton.setAccessibleName("Show sizes on gel bands")
            self.bandSizesButton.toggled.connect(self.canvas.setBandLabels)
            markerControls.addWidget(self.bandSizesButton)
        self.canvas.bandActivated.connect(self.bandActivated)
        self._plotScroll = QScrollArea(self)
        self._plotScroll.setAccessibleName("Gel plot")
        self._plotScroll.setWidgetResizable(True)
        self._plotScroll.setMinimumHeight(minimumPlotHeight)
        self._plotScroll.setWidget(self.canvas)
        self.canvas.focusTargetChanged.connect(self._plotScroll.ensureVisible)
        layout.addWidget(self._plotScroll, 1)
        analysis.ladderCatalogueChanged.connect(self._refreshLadders)
        self._refreshLadders()

    def _refreshLadders(self) -> None:
        if self._marker is not None:
            self._updateMarkerChoices()
        error = self._analysis.ladderCatalogueError
        self.retryLaddersButton.setVisible(bool(error))
        self.retryLaddersButton.setToolTip(error)
        self.retryLaddersButton.setEnabled(self._analysis.canRetryLadders)

    def setState(self, state: GelState) -> None:
        """Refresh the marker and plot without changing the supplied selected output."""
        if self._marker != state.marker or self.ladderSelector.currentData() != state.marker:
            self._marker = state.marker
            self._refreshLadders()
        self.canvas.setState(state)
        if self._compact and state != self._shownState:
            self._shownState = state
            QTimer.singleShot(0, self, self._revealSelectedBand)
        assumptions = f"Plot range {state.settings.minimumBp:,}–{state.settings.maximumBp:,} bp.\n{GEL_LEGEND}"
        if any(isinstance(lane.run, PcrRun) for lane in state.lanes):
            assumptions += f"\n{GEL_PCR_LEGEND}"
        self.canvas.setToolTip(assumptions)
        self._plotScroll.setAccessibleDescription(assumptions)
        offScale = self.gelLayout.marker.offScale
        detail = ""
        if state.marker.profile is not None:
            notice = ""
            if offScale:
                sizes = sorted({item.size for item in offScale})
                noun = "band" if len(sizes) == 1 else "bands"
                notice = f"{len(sizes):,} marker {noun} off-scale"
                detail = "Outside plot range · " + ", ".join(f"{size:,}" for size in sizes) + " bp"
        else:
            release = state.marker.release
            recordedRelease = f" · {release.name}" if release is not None else ""
            notice = f"Ladder profile unavailable · {state.marker.ladderId}{recordedRelease}"
        self.markerStatus.setText(notice)
        self.markerStatus.setToolTip(detail)
        self.markerStatus.setAccessibleDescription(detail)
        self.markerStatus.setVisible(bool(notice))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._compact:
            QTimer.singleShot(0, self, self._revealSelectedBand)

    def _revealSelectedBand(self) -> None:
        band = self.canvas.selectedBandRect()
        if band is not None:
            center = band.center()
            self._plotScroll.ensureVisible(
                round(center.x()), round(center.y()), min(round(band.width() / 2), 60),
                round(band.height() / 2),
            )

    @property
    def gelLayout(self) -> GelLayout:
        """Return the shared layout after the preview has received its first state."""
        return self.canvas.gelLayout

    def _updateMarkerChoices(self) -> None:
        current = self._marker
        markers = [GelMarker.fromProfile(profile) for profile in self._analysis.ladderProfiles]
        currentIndex = next((index for index, marker in enumerate(markers) if marker == current), None)
        with QSignalBlocker(self.ladderSelector):
            self.ladderSelector.clear()
            if currentIndex is None:
                name = (current.profile.name + " (recorded)" if current.profile is not None
                        else current.ladderId + " (unavailable)")
                self.ladderSelector.addItem(name, current)
                currentIndex = 0
            for marker in markers:
                self.ladderSelector.addItem(marker.profile.name, marker)
            self.ladderSelector.setCurrentIndex(currentIndex)
            for index in range(self.ladderSelector.count()):
                text = self.ladderSelector.itemText(index)
                self.ladderSelector.setItemData(index, text, Qt.ItemDataRole.ToolTipRole)
        self.ladderSelector.setToolTip(self.ladderSelector.currentText())
        self.ladderSelector.setEnabled(len(markers) > 0)
        self.markerInfoButton.setEnabled(current.profile is not None)

    def _changeMarker(self, index: int) -> None:
        marker = self.ladderSelector.itemData(index)
        if marker is not None and marker != self._marker:
            self.markerChanged.emit(marker)

    def _showMarker(self) -> None:
        profile = self._marker.profile
        if profile is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Marker details")
        dialog.resize(490, 450)
        layout = dialogLayout(dialog)
        title = QLabel(f"{profile.name} · {profile.release.name}", dialog)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        components = QTextBrowser(dialog)
        components.setAccessibleName("Marker components")
        rows = []
        for index, component in enumerate(profile.components):
            nominal = f"{component.nominalSize:,}" if component.nominalSize is not None else "—"
            shade = "#faf8fd" if index % 2 else "#ffffff"
            rows.append(f'<tr bgcolor="{shade}"><td align="right">{component.size:,}</td>'
                        f'<td align="right">{nominal}</td><td>{escape(component.sourceId)}</td></tr>')
        components.setHtml(
            '<table width="100%" cellspacing="0" cellpadding="6"><thead>'
            '<tr bgcolor="#eee9f6"><th align="right">Actual bp</th><th align="right">Nominal bp</th>'
            '<th align="left">Source</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
        )
        layout.addWidget(components)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        addDialogFooter(layout, buttons)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()


def exportGelFigure(
    parent: QWidget, controller: WorkspaceController, state: GelState,
    *, title: str = "Gel comparison",
) -> None:
    """Preview and atomically write a gel figure or its pinned data."""
    project = controller.workspace.project
    laneCount = len(state.lanes)
    laneLabel = "lane" if laneCount == 1 else "lanes"
    scope = "Marker only" if laneCount == 0 else f"{laneCount} pinned {laneLabel}"
    description = [title, f"{scope} · {state.settings.minimumBp:,}–{state.settings.maximumBp:,} bp"]
    if laneCount <= 2:
        description.extend(f"{number} · {gelLaneIdentity(lane)}"
                           for number, lane in enumerate(state.lanes, 1))
    else:
        description.append("The lane key identifies every pinned revision and recorded run.")

    def checkProject() -> None:
        if controller.workspace.project is not project:
            raise ValueError("The project changed while choosing the destination. Try again.")

    def dataPayload() -> bytes:
        return (json.dumps(gelData(state), ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    exportFigure(
        parent, controller, title="Export gel figure", suggestedStem="gel",
        scopes=(FigureScope(
            scope, "\n".join(description), lambda width: gelSvg(state, width=width),
            gelFigureWidth(state),
        ),),
        dataPayload=dataPayload,
        protectedPaths=tuple(lane.source.sourcePath for lane in state.lanes
                             if lane.source.sourcePath is not None),
        beforePublish=checkProject,
    )
