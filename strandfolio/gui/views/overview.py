"""A selectable coordinate overview synchronized with the visible sequence rows."""

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF, QResizeEvent
from PySide6.QtWidgets import QApplication, QSlider, QToolTip, QVBoxLayout, QWidget

from strandfolio.application.map_features import isWholeRecordSource, mapFeatures
from strandfolio.gui import theme
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.views.restriction_sites import RestrictionSitePicker, nearestSite
from strandfolio.gui.views.sequence_view import SequenceView
from strandfolio.operations.restriction import RecognitionSite
from strandfolio.rendering import colors
from strandfolio.rendering.layout import (
    LinearFeatureSpan, OverviewLayout, annotationColor, buildLinearFeatureSpans,
)


class SequenceOverview(QWidget):
    """Show sequence extent, viewport and multi-segment selection without asserting topology."""

    def __init__(
        self, controller: WorkspaceController, tracks: SequenceView, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._visible = (0, 0)
        self._featureSpans: tuple[LinearFeatureSpan, ...] = ()
        self._selectedFeatureId: str | None = None
        self._siteLabelHits: list[tuple[QRectF, RecognitionSite]] = []
        self._dragging = False
        self._pressPoint = QPointF()
        self._anchor = 0
        self._pressedFeature: LinearFeatureSpan | None = None
        self.setMouseTracking(True)
        self.setFixedHeight(100)
        self.setAccessibleName("Sequence coordinate overview")
        self.setAccessibleDescription("The axis tracks the viewport and nucleotide selection.")
        self.setToolTip("Click a feature to select it. Drag on the overview to select a range.")
        self._scrollBar = tracks.verticalScrollBar()
        self.positionSlider = QSlider(Qt.Orientation.Horizontal, self)
        self.positionSlider.setObjectName("overviewPosition")
        self.positionSlider.setAccessibleName("Scroll through the full sequence")
        self.positionSlider.setToolTip("Scroll the sequence while keeping the selection")
        self.positionSlider.setFixedHeight(16)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.addStretch()
        layout.addWidget(self.positionSlider)
        self.positionSlider.valueChanged.connect(self._scrollBar.setValue)
        tracks.visibleIntervalChanged.connect(self.setVisibleInterval)
        self._restrictionPicker = RestrictionSitePicker(self, controller, self.update)
        controller.analysis.scanChanged.connect(self._updateFocusPolicy)
        controller.documentChanged.connect(self._onDocumentChanged)
        controller.layersChanged.connect(self._updateFeatures)
        controller.mapFeaturesChanged.connect(self._updateFeatures)
        controller.selectionChanged.connect(self._selectionChanged)
        self._updateFeatures()

    def _updateFocusPolicy(self) -> None:
        self._siteLabelHits.clear()
        self.setFocusPolicy(
            Qt.FocusPolicy.TabFocus if self.controller.analysis.displayedSites else Qt.FocusPolicy.NoFocus
        )

    def _restrictionMarkers(
        self, geometry: OverviewLayout,
    ) -> tuple[tuple[QPointF, RecognitionSite], ...]:
        return tuple(
            (QPointF(geometry.positionX(site.start), 53), site)
            for site in self.controller.analysis.displayedSites
        )

    def geometryForSequence(self) -> OverviewLayout | None:
        length = len(self.controller.session.sequence)
        return OverviewLayout(length, 20, max(1, self.width() - 40)) if length else None

    def _onDocumentChanged(self) -> None:
        self._dragging = False
        self._pressedFeature = None
        self._restrictionPicker.reset()
        self._updateFeatures()

    def _updateFeatures(self) -> None:
        self._siteLabelHits.clear()
        revision = self.controller.session.current
        geometry = self.geometryForSequence()
        selection = self.controller.session.selection
        self._selectedFeatureId = selection.featureId if selection else None
        if geometry is None or revision is None:
            self._featureSpans = ()
        else:
            types = self.controller.workspace.activeDocument.viewState.mapFeatureTypes
            features = mapFeatures(revision, types, self._selectedFeatureId)
            self._featureSpans = buildLinearFeatureSpans(
                geometry, tuple(f for f in features if not isWholeRecordSource(f, len(revision))),
                self.controller.layerOptions, maxLanes=1, selectedId=self._selectedFeatureId,
            )
        self.update()

    def _selectionChanged(self) -> None:
        selection = self.controller.session.selection
        if (selection.featureId if selection else None) != self._selectedFeatureId:
            self._updateFeatures()
        else:
            self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._updateFeatures()

    def setVisibleInterval(self, start: int, end: int) -> None:
        self._visible = (start, end)
        with QSignalBlocker(self.positionSlider):
            self.positionSlider.setRange(0, self._scrollBar.maximum())
            self.positionSlider.setPageStep(self._scrollBar.pageStep())
            self.positionSlider.setSingleStep(self._scrollBar.singleStep())
            self.positionSlider.setValue(self._scrollBar.value())
        self.positionSlider.setEnabled(self._scrollBar.maximum() > 0)
        self.update()

    def paintEvent(self, event) -> None:
        with QPainter(self) as painter:
            self._paintOverview(painter)

    def _paintOverview(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(colors.CANVAS))
        font = theme.workspaceUiFont()
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        geometry = self.geometryForSequence()
        if geometry is None:
            painter.drawText(20, 56, "Your sequence overview will appear here.")
            return
        visibleStart, visibleEnd = self._visible
        visible = f"Visible {visibleStart + 1:,}–{visibleEnd:,}" if visibleEnd > visibleStart else "Overview"
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(colors.INK))
        painter.drawText(20, 17, visible)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        revision = self.controller.session.current
        unit = {"DNA": "bp", "RNA": "nt", "protein": "aa"}[revision.moleculeType]
        painter.drawText(
            QRectF(geometry.left, 2, geometry.width, 20), Qt.AlignmentFlag.AlignRight,
            f"{geometry.sequenceLength:,} {unit}",
        )
        painter.fillRect(QRectF(geometry.left, 67, geometry.width, 2), QColor(colors.LINE))
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = geometry.left + geometry.width * fraction
            painter.fillRect(QRectF(x, 65, 1, 7), QColor("#c5bdcf"))
        self._paintSites(painter, geometry, self._restrictionMarkers(geometry))
        self._paintFeatures(painter)
        painter.setFont(font)
        painter.setPen(Qt.PenStyle.NoPen)
        selected = self.controller.session.selection
        if selected is not None:
            painter.setBrush(QColor(theme.ACCENT))
            for segStart, segEnd in selected.segments:
                x = geometry.positionX(segStart)
                span = max(2.0, geometry.positionX(segEnd) - x)
                painter.drawRoundedRect(QRectF(x, 79, span, 3), 1, 1)
        x = geometry.positionX(visibleStart)
        span = max(2.0, geometry.positionX(visibleEnd) - x)
        painter.setPen(QPen(QColor(colors.ACCENT), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(x, 58, span, 20), 3, 3)

        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(theme.ACCENT))
            painter.drawRoundedRect(QRectF(self.rect().adjusted(1, 1, -2, -2)), 9, 9)

    def _paintSites(
        self, painter: QPainter, geometry: OverviewLayout,
        markers: tuple[tuple[QPointF, RecognitionSite], ...],
    ) -> None:
        """Stagger neighboring labels above the track and retain dense sites as ticks."""
        font = QFont(painter.font())
        font.setPixelSize(11)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        recipe = self.controller.analysis.recipe
        focused = self._restrictionPicker.focused
        ordered = sorted(markers, key=lambda mark: (
            mark[0].x(), mark[1] != focused, mark[1].enzymeName not in recipe,
        ))
        self._siteLabelHits.clear()
        rightEdges = [geometry.left - 4, geometry.left - 4]
        for point, site in ordered:
            color = self._restrictionPicker.color(site)
            style = Qt.PenStyle.SolidLine if site.certainty == "definite" else Qt.PenStyle.DashLine
            weight = 2 if site == focused or site.enzymeName in recipe else 1
            painter.setPen(QPen(color, weight, style))
            label = f"{site.enzymeName} {site.start + 1:,}"
            width = metrics.horizontalAdvance(label) + 2
            left = max(geometry.left, min(point.x() - width / 2, geometry.left + geometry.width - width))
            lane = next((i for i, edge in enumerate(rightEdges) if left >= edge + 4), None)
            y = point.y()
            if lane is not None:
                rect = QRectF(left, 22 + lane * 14, width, 14)
                rightEdges[lane] = rect.right()
                self._siteLabelHits.append((rect, site))
                y = rect.bottom()
            painter.drawLine(QPointF(point.x(), y), QPointF(point.x(), 77))
            if site == focused:
                painter.drawLine(QPointF(point.x() - 3, y), QPointF(point.x() + 3, y))
        for rect, site in self._siteLabelHits:
            painter.fillRect(rect, QColor(colors.CANVAS))
            painter.setPen(self._restrictionPicker.color(site))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{site.enzymeName} {site.start + 1:,}")

    def _siteAt(self, geometry: OverviewLayout, point: QPointF) -> RecognitionSite | None:
        marker = nearestSite(self._restrictionMarkers(geometry), point,
                             focused=self._restrictionPicker.focused)
        if marker is not None:
            return marker
        return next((site for rect, site in self._siteLabelHits if rect.contains(point)), None)

    def _paintFeatures(self, painter: QPainter) -> None:
        """Show a compact track of non-overlapping annotations across the whole record."""
        metrics = QFontMetricsF(painter.font())
        for span in self._featureSpans:
            x0, x1 = span.xStart, span.xEnd
            tip = min(5, (x1 - x0) / 3)
            leftTip = tip if span.strand == -1 else 0
            rightTip = tip if span.strand == 1 else 0
            points = ((x0, 68), (x0 + leftTip, 60), (x1 - rightTip, 60),
                      (x1, 68), (x1 - rightTip, 76), (x0 + leftTip, 76))
            fill = annotationColor(span.feature)
            painter.setPen(QPen(QColor(colors.LINE), 0.6))
            painter.setBrush(QColor(fill))
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
            if x1 - x0 >= 40:
                label = metrics.elidedText(span.feature.label, Qt.TextElideMode.ElideRight, x1 - x0 - 12)
                painter.setPen(QColor(colors.featureTextColor(fill)))
                painter.drawText(QRectF(x0 + 5, 60, x1 - x0 - 10, 16), Qt.AlignmentFlag.AlignCenter, label)

    def _featureAt(self, point: QPointF) -> LinearFeatureSpan | None:
        if 60 <= point.y() <= 76:
            return next((span for span in self._featureSpans if span.xStart <= point.x() < span.xEnd), None)
        return None

    def mousePressEvent(self, event) -> None:
        geometry = self.geometryForSequence()
        if geometry is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            site = self._siteAt(geometry, event.position())
            if site is not None:
                self._restrictionPicker.activate(site)
                event.accept()
                return
        self._restrictionPicker.reset()
        boundary = geometry.boundaryAt(event.position().x())
        current = self.controller.session.selection
        extend = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._anchor = current.anchor if extend else boundary
        self._pressPoint = event.position()
        self._pressedFeature = None if extend else self._featureAt(self._pressPoint)
        self._dragging = True
        if self._pressedFeature is None:
            self.controller.select(self._anchor, boundary)

    def mouseMoveEvent(self, event) -> None:
        geometry = self.geometryForSequence()
        if self._dragging and geometry is not None:
            distance = (event.position() - self._pressPoint).manhattanLength()
            if self._pressedFeature is not None and distance < QApplication.startDragDistance():
                return
            self._pressedFeature = None
            self.controller.select(self._anchor, geometry.boundaryAt(event.position().x()))
        elif geometry is not None:
            site = self._siteAt(geometry, event.position())
            feature = self._featureAt(event.position())
            if site is None and feature is None:
                self.unsetCursor()
                QToolTip.hideText()
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                label = self._restrictionPicker.tooltip(site) if site else feature.feature.label
                QToolTip.showText(event.globalPosition().toPoint(), label, self)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self.mouseMoveEvent(event)
            if self._pressedFeature is not None:
                self.controller.selectWholeFeature(self._pressedFeature.feature.featureId)
        self._pressedFeature = None
        self._dragging = False

    def keyPressEvent(self, event) -> None:
        if not self._restrictionPicker.handleKey(event, referenceBase=self._visible[0]):
            super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        self._restrictionPicker.reset()
        super().focusOutEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        self.unsetCursor()
        super().leaveEvent(event)
