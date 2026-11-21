"""Interactive circular and linear sequence maps with zoom and annotation labels."""

from html import escape
from math import atan2, cos, pi, radians, sin, tau

from PySide6.QtCore import QByteArray, QPoint, QPointF, QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QResizeEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from strandfolio.application.session import DocumentViewState
from strandfolio.gui import theme
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.views.restriction_sites import MapSiteInspector, RestrictionSitePicker, nearestSite
from strandfolio.operations.restriction import RecognitionSite
from strandfolio.rendering.circular import CircularMapLayout, circularBaseRadius
from strandfolio.rendering.layout import OverviewLayout
from strandfolio.rendering.maps import MapFigureSource


def _shortestAngleDelta(previousAngleRad: float, currentAngleRad: float) -> float:
    return (currentAngleRad - previousAngleRad + pi) % tau - pi


class _MapCanvas(QWidget):
    """Paint, zoom, pan, and hit-test either a circular plasmid or linear map."""

    statusChanged = Signal(str)
    zoomChanged = Signal(float)
    siteInspected = Signal(object)

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        theme.ensureMapFonts()
        self.setMouseTracking(True)
        self.setMinimumSize(280, 260)
        self._visibleInterval: tuple[int, int] = (0, 0)
        self._representation = "auto"
        self.isInspectorMap = True
        self._denseLabels = False
        self._rotationDeg = 0.0
        self._zoom = 1.0
        self._panX = 0.0
        self._panY = 0.0
        self._hoveredFeatureId: str | None = None
        self._nameTextRect = QRectF()
        self._renderer = QSvgRenderer(self)
        self._sceneKey: tuple[MapFigureSource, str | None, RecognitionSite | None, bool] | None = None
        self._circularLayout: CircularMapLayout | None = None
        self._linearGeometry: OverviewLayout | None = None
        self._linearHits: list[tuple[QRectF, str]] = []
        self._markers: tuple[tuple[QPointF, RecognitionSite], ...] = ()
        self._dragging = False
        self._panning = False
        self._panOrigin = QPointF()
        self._panStart = (0.0, 0.0)
        self._dragAnchor = 0
        self._dragLastAngle = 0.0
        self._dragAccumRad = 0.0
        self._restrictionPicker = RestrictionSitePicker(self, controller, self.update)
        self.previewSite: RecognitionSite | None = None

    def setVisibleInterval(self, start: int, end: int) -> None:
        if (start, end) != self._visibleInterval:
            self._visibleInterval = (start, end)
            self.update()

    def setDenseLabels(self, enabled: bool) -> None:
        if enabled != self._denseLabels:
            self._denseLabels = enabled
            self.update()

    def setRotation(self, degrees: float) -> None:
        angle = radians(degrees - self._rotationDeg)
        panX = self._panX * cos(angle) - self._panY * sin(angle)
        panY = self._panX * sin(angle) + self._panY * cos(angle)
        self._panX, self._panY = self._boundedPan(panX, panY)
        self._rotationDeg = degrees % 360.0
        self.update()

    def _boundedPan(self, x: float, y: float) -> tuple[float, float]:
        limit = 0.77 * circularBaseRadius(self.width(), self.height()) * self._zoom + 40.0
        return max(-limit, min(limit, x)), max(-limit, min(limit, y))

    def setZoom(self, zoom: float, anchorPos: QPointF | None = None) -> None:
        newZoom = max(1.0, min(8.0, round(float(zoom), 2)))
        if abs(newZoom - self._zoom) < 1e-4:
            return
        if newZoom <= 1.0:
            self._zoom = 1.0
            self._panX = 0.0
            self._panY = 0.0
        else:
            ratio = newZoom / self._zoom
            if anchorPos is not None:
                dx = anchorPos.x() - self.width() / 2.0
                dy = anchorPos.y() - self.height() / 2.0
                self._panX = dx - (dx - self._panX) * ratio
                self._panY = dy - (dy - self._panY) * ratio
            elif self._zoom <= 1.05 and self._circularLayout is not None:
                # Center the initial button-triggered zoom onto the active selection or viewport arc.
                focusRad = None
                if self._circularLayout.selectionArcs:
                    arc = self._circularLayout.selectionArcs[0]
                    focusRad = arc.startRad + arc.sweepRad * 0.5
                elif self._circularLayout.visibleArc is not None:
                    arc = self._circularLayout.visibleArc
                    focusRad = arc.startRad + arc.sweepRad * 0.5
                if focusRad is not None:
                    rFocus = self._circularLayout.baseBackboneRadius * newZoom * 0.72
                    self._panX = -rFocus * cos(focusRad)
                    self._panY = -rFocus * sin(focusRad)
            else:
                self._panX *= ratio
                self._panY *= ratio
            self._zoom = newZoom
            self._panX, self._panY = self._boundedPan(self._panX, self._panY)
        self.zoomChanged.emit(self._zoom)
        self.update()

    def saveViewState(self, state: DocumentViewState) -> None:
        state.mapZoom = self._zoom
        state.mapPanX = self._panX
        state.mapPanY = self._panY
        state.mapRotationDeg = self._rotationDeg
        state.mapDenseLabels = self._denseLabels
        state.mapRepresentation = self._representation

    def restoreViewState(self, state: DocumentViewState) -> None:
        self.setRepresentation(state.mapRepresentation == "linear")
        self._zoom = state.mapZoom
        self._panX = state.mapPanX
        self._panY = state.mapPanY
        self._rotationDeg = state.mapRotationDeg
        self._denseLabels = state.mapDenseLabels
        self.unsetCursor()
        QToolTip.hideText()
        self.zoomChanged.emit(self._zoom)
        self.updateName()

    def _nameDetails(self) -> list[str]:
        displayName = self.controller.displayName
        originalName = self.controller.originalName
        if originalName and originalName != displayName:
            return [displayName, f"Original name: {originalName}"]
        return [displayName] if displayName else []

    def updateName(self) -> None:
        details = self._nameDetails()
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus if details else Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Map title")
        self._restrictionPicker.setDescription(
            ". ".join([*details, "Press Enter or F2 to edit the preferred name."]) if details else ""
        )
        if not details and self.hasFocus():
            self.clearFocus()
        self.update()

    @property
    def isCircular(self) -> bool:
        revision = self.controller.session.current
        return revision is not None and revision.topology == "circular" and self._representation != "linear"

    def setRepresentation(self, asLinear: bool) -> None:
        self._representation = "linear" if asLinear else "auto"
        self._hoveredFeatureId = None
        self._restrictionPicker.reset()
        self._dragging = False
        self._panning = False
        self._circularLayout = None
        self._linearGeometry = None
        self._linearHits = []
        self._markers = ()
        self._sceneKey = None
        self._nameTextRect = QRectF()
        self.update()

    def figureSource(self) -> MapFigureSource:
        revision = self.controller.session.current
        if revision is None:
            raise ValueError("Open a sequence before exporting a map.")
        analysis = self.controller.analysis
        intervalStart, intervalEnd = self._visibleInterval
        visibleInterval = (min(intervalStart, len(revision)), min(intervalEnd, len(revision)))
        return MapFigureSource(
            revision=revision,
            displayName=self.controller.displayName,
            isCircular=self.isCircular,
            selection=self.controller.session.selection,
            options=self.controller.layerOptions,
            mapFeatureTypes=self.controller.workspace.activeDocument.viewState.mapFeatureTypes,
            visibleInterval=visibleInterval,
            denseLabels=self._denseLabels,
            rotationDeg=self._rotationDeg,
            zoom=self._zoom,
            panX=self._panX,
            panY=self._panY,
            viewportWidth=self.width(),
            viewportHeight=self.height(),
            restrictionSites=analysis.displayedSites,
            restrictionCuts=analysis.displayedCuts,
            restrictionRecipe=analysis.recipe,
            cdsTranslations=self.controller.cdsTranslations,
            frameTranslation=self.controller.frameTranslation,
        )

    def paintEvent(self, event) -> None:
        hasRevision = self.controller.session.current is not None
        focused = (self._restrictionPicker.focused if self.hasFocus() else None) or self.previewSite
        status = self._prepareScene(focused) if hasRevision else ""
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), QColor(theme.PANEL))
            if hasRevision:
                self._renderer.render(painter, QRectF(self.rect()))
                if self.hasFocus() and self._restrictionPicker.focused is None:
                    focusRect = self._nameTextRect.adjusted(-2, -1, 2, 1)
                    painter.setPen(QColor(theme.ACCENT))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(focusRect, 3, 3)
            else:
                painter.setPen(QColor(theme.MUTED))
                painter.drawText(
                    self.rect().adjusted(24, 24, -24, -24),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    "Open a circular plasmid or linear construct to inspect its map.",
                )
        if status is not None:
            self.statusChanged.emit(status)

    def _prepareScene(self, focused: RecognitionSite | None) -> str | None:
        source = self.figureSource()
        sceneKey = (source, self._hoveredFeatureId, focused, self.isInspectorMap)
        if sceneKey == self._sceneKey:
            return None
        scene = source.scene(
            self.width(), self.height(), native=True,
            hoveredFeatureId=self._hoveredFeatureId, focusedSite=focused, inspector=self.isInspectorMap,
        )
        self._renderer.load(QByteArray(scene.svg.encode("utf-8")))
        self._sceneKey = sceneKey
        self._circularLayout = scene.circularLayout
        self._linearGeometry = scene.linearGeometry
        self._linearHits = [
            (QRectF(x, y, w, h), featureId) for x, y, w, h, featureId in scene.linearHits
        ]
        self._nameTextRect = QRectF(*scene.titleBounds).intersected(QRectF(self.rect()))
        self._markers = tuple((QPointF(x, y), site) for x, y, site in scene.restrictionMarkers)
        return scene.status

    def _restrictionMarkers(self) -> tuple[tuple[QPointF, RecognitionSite], ...]:
        visible = frozenset(self.controller.analysis.displayedSites)
        return tuple((center, site) for center, site in self._markers if site in visible)

    def _featureTooltip(self, featureId: str) -> str:
        revision = self.controller.session.current
        if revision is None:
            return ""
        feature = revision.getFeature(featureId)
        name = f"{feature.label} · {feature.type}"
        exactParts = [part for part in feature.parts if part.isSelectable]
        if not exactParts:
            return f"{name} · No mapped intervals"
        coordinates = ", ".join(f"{part.start + 1:,}–{part.end:,}" for part in exactParts)
        length = sum(part.end - part.start for part in exactParts)
        strand = {1: "+", -1: "−", 0: "?", None: "?"}[feature.strand]
        unit = "aa" if revision.moleculeType == "protein" else "nt"
        partCount = len(feature.parts)
        unmapped = partCount - len(exactParts)
        partNote = f" · {partCount:,} parts" if partCount > 1 else ""
        if unmapped:
            partNote = f" mapped{partNote}, {unmapped:,} unmapped"
        return f"{name} · {coordinates} ({strand}, {length:,} {unit}{partNote})"

    def _showNameTooltip(self, globalPosition: QPoint) -> None:
        details = self._nameDetails()
        if details:
            QToolTip.showText(globalPosition, "<br>".join(map(escape, details)), self)

    def _requestNameEdit(self) -> None:
        document = self.controller.workspace.activeDocument
        if document is not None:
            QToolTip.hideText()
            self.controller.editNameRequested.emit(document.documentId)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        if event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
            self.controller.message.emit(" · ".join(self._nameDetails()))
        self.update()

    def focusOutEvent(self, event) -> None:
        self._restrictionPicker.reset()
        super().focusOutEvent(event)
        self.update()

    def keyPressEvent(self, event) -> None:
        markers = self._restrictionMarkers()
        if self._restrictionPicker.handleKey(
            event, referenceBase=self._visibleInterval[0], visibleSites=tuple(site for _, site in markers),
        ):
            self.previewSite = None
            if self._restrictionPicker.focused is not None:
                self.siteInspected.emit(self._restrictionPicker.focused)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self._requestNameEdit()
            event.accept()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if not self.isCircular:
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if delta:
            factor = 1.18 if delta > 0 else (1.0 / 1.18)
            self.setZoom(self._zoom * factor, event.position())
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._panning and self._circularLayout is not None:
            self._panX, self._panY = self._boundedPan(
                self._panStart[0] + pos.x() - self._panOrigin.x(),
                self._panStart[1] + pos.y() - self._panOrigin.y(),
            )
            self.update()
            return
        if self._dragging and self._circularLayout is not None:
            angle = atan2(pos.y() - self._circularLayout.cy, pos.x() - self._circularLayout.cx)
            self._dragAccumRad += _shortestAngleDelta(self._dragLastAngle, angle)
            self._dragLastAngle = angle
            length = self._circularLayout.sequenceLength
            caret = self._circularLayout.boundaryAtPoint(pos.x(), pos.y())
            if abs(self._dragAccumRad) >= tau - radians(0.5):
                self.controller.selectSegments(((0, length),), anchor=self._dragAnchor, caret=self._dragAnchor)
            elif self._dragAccumRad >= 0:
                self.controller.selectCircular(self._dragAnchor, caret)
            else:
                if caret <= self._dragAnchor:
                    self.controller.select(self._dragAnchor, caret)
                else:
                    parts = []
                    if caret < length:
                        parts.append((caret, length))
                    if self._dragAnchor > 0:
                        parts.append((0, self._dragAnchor))
                    self.controller.selectSegments(tuple(parts), anchor=self._dragAnchor, caret=caret)
            return

        if self._dragging and self._linearGeometry is not None:
            self.controller.select(self._dragAnchor, self._linearGeometry.boundaryAt(pos.x()))
            return

        overName = self._nameTextRect.contains(pos)
        site = None if overName else nearestSite(
            self._restrictionMarkers(), pos, focused=self._restrictionPicker.focused,
        )
        hovered: str | None = None
        if not overName and site is None:
            if self._circularLayout is not None:
                hit = self._circularLayout.featureAtPoint(pos.x(), pos.y())
                if hit is not None:
                    hovered = hit.featureId
            else:
                for rect, fid in reversed(self._linearHits):
                    if rect.contains(pos):
                        hovered = fid
                        break

        if hovered != self._hoveredFeatureId:
            self._hoveredFeatureId = hovered
            self.update()
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if hovered or overName or site else Qt.CursorShape.ArrowCursor
        )
        if overName:
            self._showNameTooltip(event.globalPosition().toPoint())
        elif site is not None:
            QToolTip.showText(event.globalPosition().toPoint(), self._restrictionPicker.tooltip(site), self)
        elif hovered:
            QToolTip.showText(event.globalPosition().toPoint(), self._featureTooltip(hovered), self)
        else:
            QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.controller.session.current is None:
            return
        self.previewSite = None
        pos = event.position()
        if self._nameTextRect.contains(pos):
            self._restrictionPicker.reset()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        site = nearestSite(self._restrictionMarkers(), pos, focused=self._restrictionPicker.focused)
        if site is not None:
            self._restrictionPicker.activate(site)
            self.previewSite = None
            self.siteInspected.emit(site)
            event.accept()
            return
        self._restrictionPicker.reset()
        if self._circularLayout is not None:
            hit = self._circularLayout.featureAtPoint(pos.x(), pos.y())
            if hit is not None:
                self.controller.selectWholeFeature(hit.featureId)
                return
            dist = ((pos.x() - self._circularLayout.cx) ** 2 + (pos.y() - self._circularLayout.cy) ** 2) ** 0.5
            nearBackbone = abs(dist - self._circularLayout.backboneRadius) <= 22.0
            if self._zoom > 1.05 and not nearBackbone:
                self._panning = True
                self._panOrigin = pos
                self._panStart = (self._panX, self._panY)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return
            boundary = self._circularLayout.boundaryAtPoint(pos.x(), pos.y())
            self._dragging = True
            self._dragAnchor = boundary
            self._dragLastAngle = atan2(pos.y() - self._circularLayout.cy, pos.x() - self._circularLayout.cx)
            self._dragAccumRad = 0.0
            self.controller.select(boundary, boundary)
            return

        for rect, fid in reversed(self._linearHits):
            if rect.contains(pos):
                self.controller.selectWholeFeature(fid)
                return
        if self._linearGeometry is None:
            return
        boundary = self._linearGeometry.boundaryAt(pos.x())
        self._dragging = True
        self._dragAnchor = boundary
        self.controller.select(boundary, boundary)

    def mouseDoubleClickEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._nameTextRect.contains(event.position())
        ):
            self._requestNameEdit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging or self._panning:
                self.mouseMoveEvent(event)
            self._dragging = False
            if self._panning:
                self._panning = False
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        self.unsetCursor()
        if self._hoveredFeatureId is not None:
            self._hoveredFeatureId = None
            self.update()
        super().leaveEvent(event)


class PlasmidMapView(QWidget):
    """Compose the synchronized circular/linear map canvas with zoom, rotation and label controls."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(6)
        self.heading = QLabel("Whole molecule", self)
        self.heading.setObjectName("sectionTitle")
        bar.addWidget(self.heading)
        self.summary = QLabel("", self)
        self.summary.setObjectName("muted")
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar.addWidget(self.summary, 1)
        bar.addStretch()
        self.linearViewButton = QPushButton("Linear", self)
        self.linearViewButton.setCheckable(True)
        self.linearViewButton.setToolTip("Show the stored sequence from coordinate 1 on a linear axis.")
        self.linearViewButton.setAccessibleName("Show map as a linear axis")
        self.optionsButton = QPushButton("Options", self)
        self.optionsButton.setCheckable(True)
        self.optionsButton.setAccessibleName("Show map display options")
        self.optionsButton.setToolTip("Change map orientation, zoom, labels or representation.")
        bar.addWidget(self.optionsButton)
        self.expandButton = QPushButton("Expand", self)
        self.expandButton.setAccessibleName("Toggle expanded whole-molecule map")
        bar.addWidget(self.expandButton)
        layout.addLayout(bar)

        self._mapTools = QWidget(self)
        circularControls = QHBoxLayout(self._mapTools)
        circularControls.setContentsMargins(2, 0, 2, 0)
        circularControls.setSpacing(4)

        self.zoomOutButton = QPushButton("−", self)
        self.zoomOutButton.setToolTip("Zoom out circular map.")
        self.zoomOutButton.setFixedWidth(28)
        self.zoomResetButton = QPushButton("1.0×", self)
        self.zoomResetButton.setToolTip("Reset map zoom and pan.")
        self.zoomInButton = QPushButton("+", self)
        self.zoomInButton.setToolTip("Zoom in to inspect crowded repeats, primers and short features.")
        self.zoomInButton.setFixedWidth(28)

        self.allLabelsCheckBox = QCheckBox("All labels", self)
        self.allLabelsCheckBox.setToolTip("Show all non-grouped labels on the map.")
        self.allLabelsCheckBox.setAccessibleName("Show all plasmid map labels")

        self.rotateLeftButton = QPushButton("↺", self)
        self.rotateLeftButton.setToolTip("Rotate circular map 45° counter-clockwise (view preference only).")
        self.rotateLeftButton.setFixedWidth(28)
        self.rotateRightButton = QPushButton("↻", self)
        self.rotateRightButton.setToolTip("Rotate circular map 45° clockwise (view preference only).")
        self.rotateRightButton.setFixedWidth(28)
        self.rotateResetButton = QPushButton("0°", self)
        self.rotateResetButton.setToolTip("Reset map origin to 12 o'clock.")

        for widget in (
            self.zoomOutButton, self.zoomResetButton, self.zoomInButton,
            self.allLabelsCheckBox, self.rotateLeftButton, self.rotateRightButton, self.rotateResetButton,
        ):
            circularControls.addWidget(widget)
        self._bar = bar
        self._compact = False
        bar.insertWidget(2, self._mapTools)
        bar.insertWidget(3, self.linearViewButton)
        self.optionsButton.toggled.connect(self._updateMapControls)

        self.canvas = _MapCanvas(controller, self)
        layout.addWidget(self.canvas, 1)
        self.siteInspector = MapSiteInspector(controller, self)
        layout.addWidget(self.siteInspector)
        self.siteInspector.siteChanged.connect(self._previewSite)
        self.canvas.siteInspected.connect(self.siteInspector.inspect)
        controller.analysis.scanChanged.connect(self._updateSites)
        controller.analysis.recipeChanged.connect(self._updateSites)

        self.allLabelsCheckBox.toggled.connect(self.canvas.setDenseLabels)
        self.linearViewButton.toggled.connect(self._setLinearView)
        self.zoomInButton.clicked.connect(lambda: self.canvas.setZoom(self.canvas._zoom * 1.4))
        self.zoomOutButton.clicked.connect(lambda: self.canvas.setZoom(self.canvas._zoom / 1.4))
        self.zoomResetButton.clicked.connect(lambda: self.canvas.setZoom(1.0))
        self.rotateLeftButton.clicked.connect(lambda: self._nudgeRotation(-45.0))
        self.rotateRightButton.clicked.connect(lambda: self._nudgeRotation(45.0))
        self.rotateResetButton.clicked.connect(lambda: self._setRotation(0.0))
        # QLabel.setText can invalidate the layout of a window that is still painting.
        self.canvas.statusChanged.connect(self._setSummary, Qt.ConnectionType.QueuedConnection)
        self.canvas.zoomChanged.connect(self._onZoomChanged)
        controller.documentAboutToChange.connect(self._saveViewState)
        controller.documentChanged.connect(self._onDocumentChanged)
        controller.nameChanged.connect(self.canvas.updateName)
        controller.selectionChanged.connect(self.canvas.update)
        controller.layersChanged.connect(self.canvas.update)
        controller.mapFeaturesChanged.connect(self.canvas.update)
        controller.translationChanged.connect(self.canvas.update)
        self._onDocumentChanged()

    def _setSummary(self, status: str) -> None:
        self.summary.setText(status)
        self.heading.setToolTip(status)
        self.heading.setAccessibleDescription(status)

    def setExpanded(self, expanded: bool) -> None:
        """Use the main canvas or inspector surface as the map moves between views."""
        self.expandButton.setText("Back to tracks" if expanded else "Expand")
        self.canvas.isInspectorMap = not expanded
        self._updateSites()
        self._updateMapControls()
        self.canvas.update()

    def _updateSites(self) -> None:
        sites = () if self.canvas.isInspectorMap else self.controller.analysis.displayedSites
        if self.canvas.previewSite not in sites:
            self.canvas.previewSite = None
        self.siteInspector.setSites(sites)
        self.canvas.update()

    def _previewSite(self, site: RecognitionSite) -> None:
        self.canvas.previewSite = site
        self.canvas.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = event.size().width() < 680
        if compact != self._compact:
            self._compact = compact
            if compact:
                self.layout().insertWidget(1, self._mapTools)
            else:
                self._bar.insertWidget(2, self._mapTools)
            self._updateMapControls()

    def _onZoomChanged(self, zoom: float) -> None:
        self.zoomResetButton.setText(f"{zoom:.1f}×")
        self.zoomOutButton.setEnabled(self.canvas.isCircular and zoom > 1.05)

    def _setLinearView(self, asLinear: bool) -> None:
        self.canvas.setRepresentation(asLinear)
        self._updateMapControls()

    def _nudgeRotation(self, delta: float) -> None:
        self._setRotation(self.canvas._rotationDeg + delta)

    def _setRotation(self, degrees: float) -> None:
        self.canvas.setRotation(degrees)

    def setVisibleInterval(self, start: int, end: int) -> None:
        self.canvas.setVisibleInterval(start, end)

    def figureSource(self) -> MapFigureSource:
        """Capture the active map at its current revision and presentation state."""
        return self.canvas.figureSource()

    @property
    def currentLayout(self) -> CircularMapLayout | None:
        return self.canvas._circularLayout

    def _saveViewState(self) -> None:
        document = self.controller.workspace.activeDocument
        if document is not None:
            self.canvas.saveViewState(document.viewState)

    def _onDocumentChanged(self) -> None:
        document = self.controller.workspace.activeDocument
        state = document.viewState if document is not None else DocumentViewState()
        with QSignalBlocker(self.allLabelsCheckBox), QSignalBlocker(self.linearViewButton):
            self.allLabelsCheckBox.setChecked(state.mapDenseLabels)
            self.linearViewButton.setChecked(state.mapRepresentation == "linear")
        self.canvas.restoreViewState(state)
        self.canvas.previewSite = None
        self._updateSites()
        self._updateMapControls()

    def _updateMapControls(self) -> None:
        revision = self.controller.session.current
        hasCircularTopology = revision is not None and revision.topology == "circular"
        isCircular = self.canvas.isCircular
        showOptions = not self._compact or self.optionsButton.isChecked()
        self.summary.setVisible(not self._compact)
        self.optionsButton.setVisible(self._compact and hasCircularTopology)
        self.linearViewButton.setVisible(hasCircularTopology and showOptions)
        self._mapTools.setVisible(isCircular and showOptions)
        if self.canvas.isInspectorMap or revision is None:
            self.heading.setText("Whole molecule")
        elif isCircular:
            self.heading.setText("Circular map")
        elif revision.moleculeType == "protein":
            self.heading.setText("Residue map")
        else:
            self.heading.setText("Linear map" if revision.topology else "Coordinate map")
        for control in (
            self.zoomOutButton, self.zoomResetButton, self.zoomInButton,
            self.rotateLeftButton, self.rotateRightButton, self.rotateResetButton, self.allLabelsCheckBox,
        ):
            control.setEnabled(isCircular)
            control.setVisible(isCircular)
        self._onZoomChanged(self.canvas._zoom)
