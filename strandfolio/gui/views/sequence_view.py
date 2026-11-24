"""Virtualized sequence rows with annotations, complement and codon translations."""

from math import ceil

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
)
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QToolTip, QWidget

from strandfolio.gui import theme
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.sequence_conversion import translationInterpretation
from strandfolio.gui.sequence_figure import SequenceDetailFigure
from strandfolio.gui.views.restriction_sites import RestrictionSitePicker
from strandfolio.gui.views.sequence_tracks import SequenceTrackPainter, featureTrackRect, restrictionTrackRect
from strandfolio.operations.restriction import RecognitionSite
from strandfolio.rendering.layout import (
    RowFeatureSpan,
    RowRestrictionSpan,
    SequenceLayout,
    SequenceRowIndex,
    SequenceRowPlan,
)


class SequenceView(QAbstractScrollArea):
    """Paint only visible layered rows; selection belongs to the controller's session."""

    MIN_TEXT_SIZE_PX = 8
    MAX_TEXT_SIZE_PX = 22
    DEFAULT_TEXT_SIZE_PX = 10

    visibleIntervalChanged = Signal(int, int)

    def __init__(
        self, controller: WorkspaceController, parent: QWidget | None = None, *,
        isPcr: bool = False,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self._isPcr = isPcr
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setAccessibleName("Read-only layered sequence")
        self.setAccessibleDescription(
            "Arrow keys move the caret. Shift extends selection. Feature bars select annotations."
        )
        self._font = theme.workspaceMonoFont(self.DEFAULT_TEXT_SIZE_PX)
        self._labelFont = theme.workspaceUiFont()
        self._labelFont.setPixelSize(10)
        self._labelFont.setWeight(QFont.Weight.Medium)
        self._dragging = False
        self._keyboardFocus = False
        self._pointer = QPointF()
        self._hoveredFeatureId: str | None = None
        self._autoScroll = QTimer(self)
        self._autoScroll.setInterval(40)
        self._autoScroll.timeout.connect(self._onAutoScroll)
        self._lastSelection = controller.session.selection
        self._documentId = controller.workspace.activeDocumentId
        self.rowIndex = self._makeRowIndex()
        self._restrictionPicker = RestrictionSitePicker(self, controller, self.viewport().update)
        controller.documentAboutToChange.connect(self._saveViewState)
        controller.documentChanged.connect(self._onDocumentChanged)
        controller.selectionChanged.connect(self._onSelectionChanged)
        controller.layersChanged.connect(self._onLayersChanged)
        controller.translationChanged.connect(self._onLayersChanged)
        controller.analysis.scanChanged.connect(self._onRestrictionScanChanged)
        controller.analysis.revealSite.connect(self.revealInterval)
        self.verticalScrollBar().valueChanged.connect(self._emitVisibleInterval)
        self._updateScrollRange()

    @property
    def visibleRows(self) -> int:
        offset = self.verticalScrollBar().value()
        first = self.rowIndex.rowAtY(offset)
        last = self.rowIndex.rowAtY(offset + max(1, self.viewport().height()) - 1)
        return last - first + 1

    def _viewportAnchor(self) -> tuple[int, int]:
        offset = self.verticalScrollBar().value()
        row = self.rowIndex.rowAtY(offset)
        return row * self.layoutGeometry.basesPerRow, offset - self.rowIndex.rowTop(row)

    def _makeLayout(self) -> SequenceLayout:
        metrics = QFontMetricsF(self._font)
        width = max(1, self.viewport().width())
        return SequenceLayout(
            width,
            max(metrics.horizontalAdvance("M") + 1, self._font.pixelSize() * 0.64),
            gutter=max(82, min(108, round(width * 0.098))),
            strandHeight=max(13, ceil(metrics.height()) + 2),
        )

    def _makeRowIndex(self) -> SequenceRowIndex:
        revision = self.controller.session.current
        options = self.controller.layerOptions
        self._layoutGeneration = self.controller.activationGeneration
        self.layoutGeometry = self._makeLayout()
        if revision is None:
            return SequenceRowIndex(0, self.layoutGeometry, None, (), (), options)
        return SequenceRowIndex(
            len(revision),
            self.layoutGeometry,
            revision.moleculeType,
            revision.features,
            self.controller.cdsTranslations,
            options,
            frameTranslation=None if self._isPcr else self.controller.frameTranslation,
            restrictionSites=self.controller.analysis.displayedSites,
            restrictionCuts=self.controller.analysis.displayedCuts,
            isCircular=revision.topology == "circular",
        )

    def _updateScrollRange(self) -> None:
        bar = self.verticalScrollBar()
        viewportHeight = max(1, self.viewport().height())
        bar.setRange(0, max(0, self.rowIndex.totalHeight - viewportHeight))
        bar.setPageStep(viewportHeight)
        bar.setSingleStep(self.rowIndex.rowHeight(0))

    def _rebuildLayout(
        self, anchorBase: int, anchorOffset: int, *, previousPlan: SequenceRowPlan | None = None,
    ) -> None:
        self.rowIndex = self._makeRowIndex()
        row = min(anchorBase // self.layoutGeometry.basesPerRow, self.rowIndex.rowCount - 1)
        if previousPlan is not None:
            plan = self.rowIndex.rowPlan(row)
            # Keep bases and translation at the same screen height when a new
            # restriction track pushes the contents of this row down.
            if anchorOffset >= previousPlan.forwardY:
                anchorOffset += plan.forwardY - previousPlan.forwardY
            elif anchorOffset >= previousPlan.restrictionTop:
                anchorOffset = min(anchorOffset, plan.forwardY)
        offset = min(anchorOffset, self.rowIndex.rowHeight(row) - 1)
        bar = self.verticalScrollBar()
        with QSignalBlocker(bar):
            self._updateScrollRange()
            bar.setValue(self.rowIndex.rowTop(row) + offset)

    def _saveViewState(self) -> None:
        document = self.controller.workspace.activeDocument
        if document is not None:
            state = document.viewState
            if self._isPcr:
                state.pcrTopBase, state.pcrTopOffset = self._viewportAnchor()
            else:
                state.sequenceTopBase, state.sequenceTopOffset = self._viewportAnchor()

    def _onDocumentChanged(self) -> None:
        self._dragging = False
        self._hoveredFeatureId = None
        self._restrictionPicker.reset()
        self._autoScroll.stop()
        document = self.controller.workspace.activeDocument
        documentId = self.controller.workspace.activeDocumentId
        previousSelection = self._lastSelection
        selection = self.controller.session.selection
        shouldReveal = (
            document is not None and documentId == self._documentId
            and selection is not None and selection.featureId is not None
            and (previousSelection is None or previousSelection.segments != selection.segments)
        )
        self._documentId = documentId
        anchor = (0, 0)
        if document is not None:
            state = document.viewState
            anchor = ((state.pcrTopBase, state.pcrTopOffset) if self._isPcr
                      else (state.sequenceTopBase, state.sequenceTopOffset))
        self._lastSelection = selection
        self._rebuildLayout(*anchor)
        if shouldReveal:
            self.ensureCaretVisible()
        self.viewport().update()
        self._emitVisibleInterval()

    def _onLayersChanged(self) -> None:
        self._rebuildLayout(*self._viewportAnchor())
        self._emitVisibleInterval()
        self.viewport().update()

    def _onRestrictionScanChanged(self) -> None:
        # During a tab switch, a cached overlay can arrive before the new rows.
        # Let the document change handler restore the viewport in that case.
        if self._layoutGeneration != self.controller.activationGeneration:
            return
        anchor = self._viewportAnchor()
        row = anchor[0] // self.layoutGeometry.basesPerRow
        self._rebuildLayout(*anchor, previousPlan=self.rowIndex.rowPlan(row))
        self._emitVisibleInterval()
        self.viewport().update()

    def _onSelectionChanged(self) -> None:
        selected = self.controller.session.selection
        # A document activation also announces its restored selection to the other views.
        if selected != self._lastSelection and not self._dragging:
            self.ensureCaretVisible()
        self._lastSelection = selected
        self.viewport().update()

    def _emitVisibleInterval(self) -> None:
        interval = self.layoutGeometry.visibleInterval(
            self.rowIndex.rowAtY(self.verticalScrollBar().value()),
            self.visibleRows,
            len(self.controller.session.sequence),
        )
        self.visibleIntervalChanged.emit(*interval)

    def setTextSize(self, pixels: int) -> None:
        """Change presentation size while retaining the sequence near the viewport top."""
        anchor = self._viewportAnchor()
        self._font.setPixelSize(max(self.MIN_TEXT_SIZE_PX, min(self.MAX_TEXT_SIZE_PX, pixels)))
        self._rebuildLayout(*anchor)
        if self.controller.session.selection is not None and len(self.controller.session.selection):
            self.ensureCaretVisible()
        self._emitVisibleInterval()
        self.viewport().update()

    def resizeEvent(self, event) -> None:
        anchor = self._viewportAnchor()
        selection = self.controller.session.selection
        selectionBounds = self._caretYBounds() if selection is not None and len(selection) else None
        top = self.verticalScrollBar().value()
        oldBottom = top + event.oldSize().height()
        super().resizeEvent(event)
        self._rebuildLayout(*anchor)
        if (
            selectionBounds is not None
            and selectionBounds[0] < oldBottom
            and selectionBounds[1] > top
        ):
            self.ensureCaretVisible()
        self._emitVisibleInterval()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()
        self._emitVisibleInterval()

    def _caretYBounds(self) -> tuple[int, int] | None:
        selection = self.controller.session.selection
        if selection is None:
            return None
        length = len(self.controller.session.sequence)
        if selection.featureId is not None and len(selection):
            position = min(selection.start, max(0, length - 1))
        else:
            caret = selection.caret - 1 if selection.caret > selection.anchor else selection.caret
            position = min(caret, max(0, length - 1))
        row = position // self.layoutGeometry.basesPerRow
        plan = self.rowIndex.rowPlan(row)
        rowTop = self.rowIndex.rowTop(row)
        selectionTop = rowTop + plan.forwardY
        selectionBottom = rowTop + plan.translationTop
        return selectionTop, selectionBottom

    def ensureCaretVisible(self) -> None:
        bounds = self._caretYBounds()
        if bounds is None:
            return
        selectionTop, selectionBottom = bounds
        bar = self.verticalScrollBar()
        if selectionTop < bar.value():
            bar.setValue(selectionTop)
        elif selectionBottom > bar.value() + self.viewport().height():
            bar.setValue(selectionBottom - self.viewport().height())

    def revealInterval(self, start: int, end: int) -> None:
        """Reveal a half-open interval without moving the nucleotide caret."""
        width = self.layoutGeometry.basesPerRow
        row = start // width
        height = self.viewport().height()
        # Reveal the row's coordinate ruler and annotations whenever its strands also fit.
        first = self.rowIndex.rowTop(row) + max(0, self.rowIndex.rowPlan(row).translationTop - height)
        lastRow = (end - 1) // width
        plan = self.rowIndex.rowPlan(lastRow)
        bottom = plan.translationTop
        last = self.rowIndex.rowTop(lastRow) + bottom
        bar = self.verticalScrollBar()
        if first < bar.value() or last - first > height:
            bar.setValue(first)
        elif last > bar.value() + height:
            bar.setValue(last - height)

    def paintEvent(self, event) -> None:
        with QPainter(self.viewport()) as painter:
            self._paintViewport(painter)

    def _paintViewport(self, painter: QPainter) -> None:
        painter.fillRect(self.viewport().rect(), QColor(theme.PANEL))
        sequence = self.controller.session.sequence
        if not sequence:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                "Open a sequence file\n\nInspect letters, select a region, and copy exactly what you see.",
            )
            return
        offset = self.verticalScrollBar().value()
        firstRow = self.rowIndex.rowAtY(offset)
        vpHeight = self.viewport().height()
        yCursor = self.rowIndex.rowTop(firstRow) - offset
        row = firstRow
        while row < self.rowIndex.rowCount and yCursor < vpHeight:
            plan = self.rowIndex.rowPlan(row)
            self._paintRow(painter, plan, yCursor, sequence)
            yCursor += plan.rowHeight
            row += 1
        if self.hasFocus() and self._keyboardFocus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(theme.ACCENT))
            painter.drawRect(self.viewport().rect().adjusted(0, 0, -1, -1))

    def _paintRow(self, painter: QPainter, plan: SequenceRowPlan, top: int, sequence: str) -> None:
        focused = self.hasFocus()
        self._trackPainter(sequence).paintRow(
            painter, plan, top, showCaret=focused, hoveredFeatureId=self._hoveredFeatureId,
            focusedSite=self._restrictionPicker.focused if focused else None,
        )

    def _trackPainter(self, sequence: str) -> SequenceTrackPainter:
        return SequenceTrackPainter(
            self.layoutGeometry, sequence, self.controller.session.current.moleculeType,
            self._font, self._labelFont, self.controller.session.selection,
            frozenset(self.controller.analysis.recipe),
            duplexGeometry=self.controller.session.current.duplexGeometry,
        )

    def _restrictionRect(self, span: RowRestrictionSpan, plan: SequenceRowPlan, top: int) -> QRectF:
        return restrictionTrackRect(self.layoutGeometry, span, plan, top)

    def detailFigure(self, *, fullRecord: bool) -> SequenceDetailFigure:
        """Capture a full record, exact selection or visible interval for figure preview."""
        revision = self.controller.session.current
        if revision is None:
            raise ValueError("Open a sequence before exporting sequence detail.")
        selection = self.controller.session.selection
        if fullRecord:
            intervals = ((0, len(revision)),)
            scope = "Full record"
        elif selection is not None and len(selection):
            intervals = selection.segments
            scope = "Selected region"
        else:
            intervals = (self.layoutGeometry.visibleInterval(
                self.rowIndex.rowAtY(self.verticalScrollBar().value()),
                self.visibleRows, len(revision),
            ),)
            scope = "Visible region"
        return SequenceDetailFigure(
            revision, self.rowIndex, self._trackPainter(revision.sequence),
            self.controller.displayName, intervals, scope, self.controller.analysis.overlayNames,
        )

    def _restrictionHitAt(self, point: QPointF) -> RecognitionSite | None:
        row = self._rowAtY(point.y())
        plan = self.rowIndex.rowPlan(row)
        top = self.rowIndex.rowTop(row) - self.verticalScrollBar().value()
        return next((
            span.site for span in plan.restrictionSpans if self._restrictionRect(span, plan, top).contains(point)
        ), None)

    def _rowAtY(self, y: float) -> int:
        return self.rowIndex.rowAtY(self.verticalScrollBar().value() + y)

    def _boundaryAt(self, point: QPointF) -> int:
        row = self._rowAtY(point.y())
        return self.layoutGeometry.boundaryAt(point.x(), row, len(self.controller.session.sequence))

    def _featureHitAt(self, point: QPointF) -> tuple[str, str] | None:
        """Resolve the current row geometry, including changes awaiting the next repaint."""
        row = self._rowAtY(point.y())
        plan = self.rowIndex.rowPlan(row)
        rowTop = self.rowIndex.rowTop(row) - self.verticalScrollBar().value()
        for span in reversed(plan.features):
            if self._featureTrackRect(span, plan, rowTop).contains(point):
                strandLabel = {1: "+", -1: "−", 0: "?", None: "?"}[span.strand]
                partLabel = f" ({span.partIndex + 1}/{span.partCount})" if span.partCount > 1 else ""
                tooltip = (
                    f"{span.label} · {span.featureType} · "
                    f"{span.fullStart + 1:,}–{span.fullEnd:,} ({strandLabel}){partLabel}"
                )
                return span.featureId, tooltip
        return None

    def _featureTrackRect(
        self, span: RowFeatureSpan, plan: SequenceRowPlan, rowTop: int,
    ) -> QRectF:
        """Share feature-track geometry between painting and pointer interaction."""
        return featureTrackRect(self.layoutGeometry, span, plan, rowTop)

    def _translationTooltipAt(self, point: QPointF) -> str | None:
        row = self._rowAtY(point.y())
        plan = self.rowIndex.rowPlan(row)
        rowTop = self.rowIndex.rowTop(row) - self.verticalScrollBar().value()
        trackIndex = int((point.y() - rowTop - plan.translationTop) // SequenceRowIndex.TRANSLATION_STEP)
        if not 0 <= trackIndex < len(plan.cdsTracks):
            return None
        track = plan.cdsTracks[trackIndex]
        result = (
            self.controller.cdsForFeature(track.featureId) if track.featureId is not None
            else self.controller.frameTranslation
        )
        if result is None:
            return None
        return f"{track.label} · {translationInterpretation(result)} · * = stop"

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.controller.session.current is None:
            return
        self.setFocus()
        pos = event.position()
        if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            site = self._restrictionHitAt(pos)
            if site is not None:
                self._restrictionPicker.activate(site)
                return
            hit = self._featureHitAt(pos)
            if hit is not None:
                self._dragging = False
                self._restrictionPicker.reset()
                self.controller.selectWholeFeature(hit[0])
                return
        self._restrictionPicker.reset()
        boundary = self._boundaryAt(pos)
        anchor = (
            self.controller.session.selection.anchor
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            else boundary
        )
        self._dragging = True
        self._pointer = pos
        self.controller.select(anchor, boundary)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if not self._dragging:
            site = self._restrictionHitAt(pos)
            hit = self._featureHitAt(pos)
            hovered = hit[0] if hit is not None else None
            if hovered != self._hoveredFeatureId:
                self._hoveredFeatureId = hovered
                self.viewport().update()
            self.viewport().setCursor(
                Qt.CursorShape.PointingHandCursor if site or hovered else Qt.CursorShape.IBeamCursor
            )
            if site is not None:
                QToolTip.showText(event.globalPosition().toPoint(), self._restrictionPicker.tooltip(site), self.viewport())
            elif hit is not None:
                QToolTip.showText(event.globalPosition().toPoint(), hit[1], self.viewport())
            else:
                tooltip = self._translationTooltipAt(pos)
                if tooltip:
                    QToolTip.showText(event.globalPosition().toPoint(), tooltip, self.viewport())
                else:
                    QToolTip.hideText()
            return
        self._pointer = pos
        self.controller.select(self.controller.session.selection.anchor, self._boundaryAt(self._pointer))
        if self._pointer.y() < 0 or self._pointer.y() >= self.viewport().height():
            self._autoScroll.start()
        else:
            self._autoScroll.stop()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self.controller.select(self.controller.session.selection.anchor, self._boundaryAt(event.position()))
        self._dragging = False
        self._autoScroll.stop()

    def _onAutoScroll(self) -> None:
        if not self._dragging or self.controller.session.selection is None:
            self._autoScroll.stop()
            return
        direction = -1 if self._pointer.y() < 0 else 1
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * bar.singleStep())
        self.controller.select(self.controller.session.selection.anchor, self._boundaryAt(self._pointer))

    def mouseDoubleClickEvent(self, event) -> None:
        if self.controller.session.current is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._restrictionHitAt(event.position()) is not None or self._featureHitAt(event.position()) is not None:
            return
        position = min(self._boundaryAt(event.position()), len(self.controller.session.sequence) - 1)
        start = position // 10 * 10
        self.controller.select(start, min(start + 10, len(self.controller.session.sequence)))
        self._dragging = False

    def keyPressEvent(self, event) -> None:
        session = self.controller.session
        if session.current is None:
            return super().keyPressEvent(event)
        if self._restrictionPicker.handleKey(
            event, referenceBase=self._viewportAnchor()[0], reveal=self.revealInterval,
        ):
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.controller.copySelection()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.controller.select(0, len(session.sequence))
            return
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        selected = session.selection
        caret = selected.caret
        width = self.layoutGeometry.basesPerRow
        steps = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: -width,
            Qt.Key.Key_Down: width,
            Qt.Key.Key_PageUp: -width * self.visibleRows,
            Qt.Key.Key_PageDown: width * self.visibleRows,
        }
        if key in steps:
            length = len(session.sequence)
            anchor = selected.anchor
            if (
                shift and session.current.topology == "circular" and selected.featureId is None
                and selected.segments == ((anchor, length), (0, caret))
            ):
                self._restrictionPicker.reset()
                endpoint = min(anchor + length, max(anchor, length + caret + steps[key]))
                if endpoint <= length:
                    self.controller.select(anchor, endpoint)
                elif endpoint == anchor + length:
                    self.controller.selectSegments(
                        ((anchor, length), (0, anchor)), anchor=anchor, caret=anchor,
                    )
                else:
                    self.controller.selectCircular(anchor, endpoint - length)
                return
            if not shift and len(selected) and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                caret = selected.start if key == Qt.Key.Key_Left else selected.end
            else:
                caret += steps[key]
        elif key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            whole = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
            if key == Qt.Key.Key_Home:
                caret = 0 if whole else caret // width * width
            else:
                caret = len(session.sequence) if whole else (caret // width + 1) * width
        elif key != Qt.Key.Key_Escape:
            return super().keyPressEvent(event)
        self._restrictionPicker.reset()
        caret = max(0, min(len(session.sequence), caret))
        self.controller.select(selected.anchor if shift else caret, caret)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._keyboardFocus = event.reason() in (
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason,
        )
        self.viewport().update()

    def focusOutEvent(self, event) -> None:
        self._dragging = False
        self._autoScroll.stop()
        self._restrictionPicker.reset()
        super().focusOutEvent(event)
        self.viewport().update()

    def leaveEvent(self, event) -> None:
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        QToolTip.hideText()
        if self._hoveredFeatureId is not None:
            self._hoveredFeatureId = None
            self.viewport().update()
        super().leaveEvent(event)
