"""PCR canvas for pinned targets, recorded products and exact sites of entered oligos."""

from typing import NamedTuple

from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from strandfolio.application.pcr_session import SelectedPrimerIdea
from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.primers import PrimerEntry, PrimerStrand
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.views.overview import SequenceOverview
from strandfolio.gui.views.sequence_view import SequenceView
from strandfolio.operations.pcr import ExactPrimerPreview, PcrProduct, previewPrimerBindings
from strandfolio.rendering.colors import ACCENT, AMBER, GREEN
from strandfolio.rendering.layout import SequenceRowPlan


class _DraftMarker(NamedTuple):
    role: PrimerStrand
    strand: PrimerStrand
    segments: tuple[SelectionSegment, ...]

    @property
    def color(self) -> str:
        return GREEN if self.role == 1 else AMBER


# Keep enough contrast for the four nucleotide ink colors on either strand.
_PICKED_FILL = {1: "#b5ead8", -1: "#ffdc9a"}
_EDITED_FILL = {1: "#d9f1e8", -1: "#fff0d3"}


class PcrSequenceView(SequenceView):
    """Use the shared row geometry for painted and clickable primer footprints."""

    primerSelected = Signal(int)
    comparisonDismissed = Signal()
    pickingChanged = Signal()
    pickLengthChanged = Signal(int)

    def __init__(self, controller: WorkspaceController, parent: QWidget) -> None:
        self._product: PcrProduct | None = None
        self._pressedIdea: tuple[QPointF, tuple[int, ...], int] | None = None
        self._pickingPrimer: PrimerStrand | None = None
        self._gestureSelection: SequenceSelection | None = None
        self._extendingWithKeys = False
        self._previewKey = None
        self._previews: tuple[ExactPrimerPreview, ...] = ()
        self._markers: set[_DraftMarker] = set()
        self._pickedSites: dict[_DraftMarker, bool] = {}
        super().__init__(controller, parent, isPcr=True)
        controller.pcr.inputsChanged.connect(self.pickPrimer)
        controller.selectionChanged.connect(self._reportPickLength)

    def _reportPickLength(self) -> None:
        if self._pickingPrimer and not self._extendingWithKeys:
            selected = self.controller.session.selection
            fresh = self._dragging or selected is self._gestureSelection
            self.pickLengthChanged.emit(len(selected) if selected and fresh else 0)

    @property
    def pickingPrimer(self) -> PrimerStrand | None:
        return self._pickingPrimer

    def startOrUseSelection(self, strand: PrimerStrand) -> None:
        """Use a range just selected on this canvas, or wait for a new gesture."""
        selection = self.controller.session.selection
        if (self._pickingPrimer is None and selection and selection is self._gestureSelection
                and selection.featureId is None):
            if self._useSelection(strand):
                return
        self.pickPrimer(strand)

    def _collapseRange(self) -> None:
        selection = self.controller.session.selection
        if selection:
            # The user may have scrolled to the next site since highlighting this range.
            bar = self.verticalScrollBar()
            scroll = bar.value()
            self.controller.select(selection.caret, selection.caret)
            bar.setValue(scroll)

    def cancelPrimerPick(self) -> None:
        self.pickPrimer(None)
        self._collapseRange()
        self.comparisonDismissed.emit()

    def pickPrimer(self, strand: PrimerStrand | None = None) -> None:
        """Arm one role for the next canvas gesture; preserve the existing draft and target."""
        if strand not in (None, 1, -1):
            raise ValueError("Choose a forward or reverse primer.")
        template = self.controller.session.current
        if template is None or template.moleculeType != "DNA":
            strand = None
        self._gestureSelection = None
        if strand is not None and self._pickingPrimer == strand:
            return
        previous, self._pickingPrimer = self._pickingPrimer, strand
        self._pressedIdea = None
        self._dragging = False
        self._autoScroll.stop()
        self._restrictionPicker.reset()
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if strand else Qt.CursorShape.IBeamCursor)
        if strand:
            self._collapseRange()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        if previous != strand:
            self.pickLengthChanged.emit(0)
            self.pickingChanged.emit()

    def _useSelection(self, strand: PrimerStrand) -> bool:
        selection = self.controller.session.selection
        if not selection or not self.controller.pcr.useSelectionAsPrimer(strand):
            return False
        self.pickPrimer(None)
        if selection is self.controller.session.selection:
            self._collapseRange()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _onDocumentChanged(self) -> None:
        self.pickPrimer(None)
        super()._onDocumentChanged()

    def hideEvent(self, event) -> None:
        self.pickPrimer(None)
        super().hideEvent(event)

    def updateDraftSites(
        self, revision: SequenceRevision | None, entries: tuple[PrimerEntry, PrimerEntry],
    ) -> tuple[str, ...]:
        """Cache sites on input changes, outside paint and pointer event handling."""
        pcr = self.controller.pcr
        key = (self.controller.workspace.activeDocumentId, revision.revisionId if revision else None,
               entries, pcr.settings)
        if key != self._previewKey:
            self._previews = tuple(
                previewPrimerBindings(revision, entry.toRevision(entry.role), pcr.settings)
                if revision is not None and not entry.problem else ExactPrimerPreview()
                for entry in entries
            )
            self._previewKey = key
        self._markers = {
            _DraftMarker(role, site.strand, site.sourceSegments)
            for role, preview in zip((1, -1), self._previews, strict=True) for site in preview.sites
        }
        exactPicks = pcr.selectedPrimerIdeas
        self._pickedSites = {
            _DraftMarker(site.strand, site.strand, site.sourceSegments): site in exactPicks
            for site in pcr.primerComparisonSites
        }
        self._markers.update(self._pickedSites)
        self.viewport().update()
        return tuple(preview.summary for preview in self._previews)

    def setProduct(self, product: PcrProduct | None) -> None:
        self._product = product
        self.viewport().update()

    def _paintRow(self, painter: QPainter, plan: SequenceRowPlan, top: int, sequence: str) -> None:
        product = self._product
        if product is not None:
            tint = QColor(ACCENT)
            tint.setAlpha(36)
            for start, end in product.sourceSegments:
                rect = self._footprintRect(plan, top, start, end)
                if rect is not None:
                    painter.fillRect(rect, tint)
        # The shared renderer paints the live selection and nucleotide letters over the bands.
        for marker, exact in self._pickedSites.items():
            fill = QColor((_PICKED_FILL if exact else _EDITED_FILL)[marker.role])
            for start, end in marker.segments:
                rect = self._footprintRect(plan, top, start, end, marker.strand)
                if rect is not None:
                    if plan.complementY is None:
                        if marker.strand == 1:
                            rect.setBottom(rect.center().y())
                        else:
                            rect.setTop(rect.center().y())
                    painter.fillRect(rect, fill)
        super()._paintRow(painter, plan, top, sequence)
        target = self.controller.pcr.target
        if target:
            painter.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DotLine))
            for start, end in target.segments:
                rect = self._footprintRect(plan, top, start, end)
                if rect is not None:
                    painter.drawLine(QLineF(rect.left(), rect.bottom(), rect.right(), rect.bottom()))
        if product is not None:
            for binding, color in ((product.forwardBinding, GREEN), (product.reverseBinding, AMBER)):
                self._drawFootprint(painter, plan, top, binding.sourceSegments, binding.strand, color)
        for marker in self._markers:
            if marker not in self._pickedSites:
                self._drawFootprint(painter, plan, top, marker.segments, marker.strand, marker.color, dashed=True)
        for marker, exact in self._pickedSites.items():
            self._drawFootprint(painter, plan, top, marker.segments, marker.strand, marker.color,
                                dashed=not exact, picked=True)

    def _footprintRect(
        self, plan: SequenceRowPlan, top: int, start: int, end: int, strand: int = 1,
    ) -> QRectF | None:
        start, end = max(start, plan.rowStart), min(end, plan.rowEnd)
        if start >= end:
            return None
        geometry = self.layoutGeometry
        left = geometry.baseX(start - plan.rowStart)
        right = geometry.baseX(end - 1 - plan.rowStart) + geometry.characterWidth
        y = plan.complementY if strand == -1 and plan.complementY is not None else plan.forwardY
        return QRectF(left, top + y, right - left, geometry.strandHeight)

    def _drawFootprint(
        self, painter: QPainter, plan: SequenceRowPlan, top: int, segments: tuple[SelectionSegment, ...],
        strand: int, name: str, *, dashed: bool = False, picked: bool = False,
    ) -> None:
        color = QColor(name)
        pen = QPen(color, 3 if picked else 1.5, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        for index, (start, end) in enumerate(segments):
            rect = self._footprintRect(plan, top, start, end, strand)
            if rect is None:
                continue
            y = rect.top() + pen.widthF() / 2 if strand == 1 else rect.bottom() - pen.widthF() / 2
            painter.setPen(pen)
            painter.drawLine(QLineF(rect.left(), y, rect.right(), y))
            terminal = end <= plan.rowEnd if strand == 1 else start >= plan.rowStart
            if index == len(segments) - 1 and terminal:
                x = rect.right() if strand == 1 else rect.left()
                back = x - strand * min(6.0, rect.width() * 0.45)
                depth = 4 if picked else 3
                if picked:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(color)
                    painter.drawPolygon(QPolygonF([
                        QPointF(x, y), QPointF(back, y - depth), QPointF(back, y + depth),
                    ]))
                else:
                    painter.drawLine(QLineF(back, y + strand * depth, x, y))

    def _rolesAt(self, pos: QPointF) -> tuple[int, ...]:
        if not self._markers:
            return ()
        row = self.rowIndex.rowAtY(self.verticalScrollBar().value() + pos.y())
        plan = self.rowIndex.rowPlan(row)
        top = self.rowIndex.rowTop(row) - self.verticalScrollBar().value()
        roles = set()
        for marker in self._markers:
            for start, end in marker.segments:
                rect = self._footprintRect(plan, top, start, end, marker.strand)
                if rect is None or not rect.contains(pos):
                    continue
                if plan.complementY is not None or (pos.y() < rect.center().y()) == (marker.strand == 1):
                    roles.add(marker.role)
                    break
        return tuple(role for role in (1, -1) if role in roles)

    def mousePressEvent(self, event) -> None:
        self._pressedIdea = None
        if event.button() == Qt.MouseButton.LeftButton:
            self._gestureSelection = None
        if self._pickingPrimer and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._dragging = True
            self._pointer = event.position()
            boundary = self._boundaryAt(self._pointer)
            self.controller.select(boundary, boundary)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and not event.modifiers():
            roles = self._rolesAt(event.position())
            if roles:
                self._pressedIdea = (event.position(), roles, self.controller.activationGeneration)
                self.setFocus()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._pickingPrimer and not self._dragging:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._pressedIdea is not None:
            start, _, generation = self._pressedIdea
            if self.controller.activationGeneration != generation:
                self._pressedIdea = None
                return
            if (start - event.position()).manhattanLength() < QApplication.startDragDistance():
                return
            self._pressedIdea = None
            self._restrictionPicker.reset()
            self._dragging = True
            anchor = self._boundaryAt(start)
            self.controller.select(anchor, anchor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        strand = self._pickingPrimer
        if strand:
            if self._dragging and event.button() == Qt.MouseButton.LeftButton:
                super().mouseReleaseEvent(event)
                if self._pickingPrimer == strand:
                    self._useSelection(strand)
            return
        pressed, self._pressedIdea = self._pressedIdea, None
        if pressed is None:
            placingRange = self._dragging and event.button() == Qt.MouseButton.LeftButton
            super().mouseReleaseEvent(event)
            if placingRange:
                self._gestureSelection = self.controller.session.selection
                if not self._gestureSelection and not event.modifiers():
                    self.comparisonDismissed.emit()
            return
        if (event.button() != Qt.MouseButton.LeftButton or self.controller.activationGeneration != pressed[2]
                or self._rolesAt(event.position()) != pressed[1]):
            return
        if len(pressed[1]) == 1:
            self.primerSelected.emit(pressed[1][0])
            return
        # Either member of the pair can occupy this footprint, even on the same strand.
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.aboutToHide.connect(menu.deleteLater)
        key = self._previewKey
        for role, label in ((1, "forward"), (-1, "reverse")):
            action = menu.addAction(f"Select {label} primer")
            action.triggered.connect(lambda _checked=False, chosen=role: (
                self._previewKey == key and self.controller.activationGeneration == pressed[2]
                and self.primerSelected.emit(chosen)
            ))
        menu.popup(event.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, event) -> None:
        if self._pickingPrimer:
            self.mousePressEvent(event)
        else:
            before = self.controller.session.selection
            super().mouseDoubleClickEvent(event)
            selected = self.controller.session.selection
            if selected is not before:
                self._gestureSelection = selected

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancelPrimerPick()
            event.accept()
            return
        selected = self.controller.session.selection
        if self._pickingPrimer and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if selected is self._gestureSelection:
                self._useSelection(self._pickingPrimer)
            event.accept()
            return
        extending = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and key in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
            Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End,
        )
        if not extending and not event.matches(QKeySequence.StandardKey.SelectAll):
            return super().keyPressEvent(event)
        self._extendingWithKeys = True
        try:
            if self._pickingPrimer and extending and selected and selected is not self._gestureSelection:
                # Begin at the current caret even if a whole feature was highlighted before arming.
                self.controller.select(selected.caret, selected.caret)
            super().keyPressEvent(event)
            self._gestureSelection = self.controller.session.selection
        finally:
            self._extendingWithKeys = False
        self._reportPickLength()

    def contextMenuEvent(self, event) -> None:
        hit = self._featureHitAt(event.pos()) if event.reason() == QContextMenuEvent.Reason.Mouse else None
        if self.showSelectionMenu(event.globalPos(), hit[0] if hit else None):
            event.accept()
        else:
            super().contextMenuEvent(event)

    def showSelectionMenu(self, position: QPoint, featureId: str | None = None) -> bool:
        """Offer PCR actions for the selected bases or the feature under the pointer."""
        self.pickPrimer(None)
        pcr = self.controller.pcr
        selection = self.controller.session.selection
        revision = self.controller.session.current
        pinned = pcr.target
        if revision is None or revision.moleculeType != "DNA" or not (selection or pinned or featureId):
            return False
        generation = self.controller.activationGeneration
        texts = pcr.primerTexts
        def current() -> bool:
            return (generation == self.controller.activationGeneration and pcr.primerTexts == texts
                    and selection == self.controller.session.selection and pcr.target == pinned)
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.aboutToHide.connect(menu.deleteLater)
        feature = revision.getFeature(featureId) if featureId else None
        selectedFeature = feature and selection and selection.featureId == featureId and (
            selection.wholeFeature or len(feature.parts) == 1
        )
        if feature and not selectedFeature:
            pinFeature = menu.addAction(f"Set {feature.label} as PCR target")
            pinFeature.setEnabled(bool(feature.parts) and all(part.isSelectable for part in feature.parts))

            def pinClickedFeature() -> None:
                if current():
                    self.controller.selectWholeFeature(featureId)
                    selected = self.controller.session.selection
                    if selected and selected.featureId == featureId:
                        pcr.pinSelection()

            pinFeature.triggered.connect(pinClickedFeature)
        if selection:
            pin = menu.addAction("Set selection as PCR target")
            pin.setEnabled(selection != pinned)
            pin.triggered.connect(lambda: current() and pcr.pinSelection())
        if pinned:
            clear = menu.addAction("Clear PCR target")
            clear.triggered.connect(lambda: current() and pcr.clearTarget())
        if selection:
            menu.addSeparator()
            for label, strand in (("forward", 1), ("reverse", -1)):
                action = menu.addAction(f"Set {label} primer from selection")
                try:
                    pcr.selectionIdea(strand)
                except ValueError as error:
                    action.setEnabled(False)
                    action.setToolTip(str(error))
                action.triggered.connect(lambda _checked=False, side=strand: current() and self._useSelection(side))
        menu.setToolTipsVisible(True)
        menu.popup(position)
        return True


class PcrOverview(SequenceOverview):
    """Share the sequence canvas's feature and range actions on the whole-record map."""

    _locatorY = {1: 87, -1: 99}

    def __init__(self, controller: WorkspaceController, canvas: PcrSequenceView, parent: QWidget) -> None:
        super().__init__(controller, canvas, parent)
        self._canvas = canvas
        self.setFixedHeight(122)
        self.setAccessibleDescription(
            "The axis shows the viewport, selection and picked primer sites. F marks forward and R marks reverse."
        )
        self.setToolTip("Click a primer locator to see its picked section. Drag on the map to select a range.")
        controller.pcr.inputsChanged.connect(self.update)

    def _paintOverview(self, painter: QPainter) -> None:
        super()._paintOverview(painter)
        geometry = self.geometryForSequence()
        sites = self.controller.pcr.primerComparisonSites
        if geometry is None or not sites:
            return
        exact = self.controller.pcr.selectedPrimerIdeas
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        for site in sites:
            forward = site.strand == 1
            y = self._locatorY[site.strand]
            color = QColor(GREEN if forward else AMBER)
            matches = site in exact
            style = Qt.PenStyle.SolidLine if matches else Qt.PenStyle.DashLine
            painter.setPen(QPen(color, 2, style))
            for start, end in site.sourceSegments:
                painter.drawLine(QLineF(geometry.positionX(start), y, geometry.positionX(end), y))
            # The tip is pinned to the actual 3′ boundary even on a subpixel or origin-spanning site.
            start, end = site.sourceSegments[-1]
            tip = geometry.positionX(end if forward else start)
            back = tip - 6 * site.strand
            painter.setPen(QPen(color, 1))
            painter.setBrush(color if matches else QColor("white"))
            painter.drawPolygon(QPolygonF([QPointF(tip, y), QPointF(back, y - 3), QPointF(back, y + 3)]))
            labelLeft = tip + 2 if forward else tip - 12
            painter.drawText(QRectF(labelLeft, y - 6, 10, 12), Qt.AlignmentFlag.AlignCenter, "F" if forward else "R")

    def _locatorAt(self, point: QPointF) -> tuple[SelectedPrimerIdea, SelectionSegment] | None:
        geometry = self.geometryForSequence()
        if geometry is None:
            return None
        for site in self.controller.pcr.primerComparisonSites:
            if abs(point.y() - self._locatorY[site.strand]) > 6:
                continue
            for index, (start, end) in enumerate(site.sourceSegments):
                left, right = geometry.positionX(start), geometry.positionX(end)
                if index == len(site.sourceSegments) - 1:
                    tip = right if site.strand == 1 else left
                    left = min(left, tip - (6 if site.strand == 1 else 12))
                    right = max(right, tip + (12 if site.strand == 1 else 6))
                if left - 2 <= point.x() <= right + 2:
                    return site, (start, end)
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not event.modifiers():
            hit = self._locatorAt(event.position())
            if hit is not None:
                site, interval = hit
                if self._canvas.pickingPrimer is not None:
                    self._canvas.cancelPrimerPick()
                self._canvas.primerSelected.emit(site.strand)
                self._canvas.revealInterval(*interval)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not event.buttons() and self._locatorAt(event.position()) is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event) -> None:
        hit = self._featureAt(event.pos()) if event.reason() == QContextMenuEvent.Reason.Mouse else None
        if self._canvas.showSelectionMenu(event.globalPos(), hit.feature.featureId if hit else None):
            event.accept()
        else:
            super().contextMenuEvent(event)
