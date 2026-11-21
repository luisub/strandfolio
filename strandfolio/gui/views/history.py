"""Inspect recorded ancestry and analyses for the active revision."""

from PySide6.QtCore import QByteArray, QPointF, QSignalBlocker, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from strandfolio.domain.provenance import IllustrationAttribution
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.panels.pending_saves import PendingSaveButton, SaveNotice
from strandfolio.gui.views.gel_preview import formatPcrSource
from strandfolio.gui.views.history_ancestry import HistoryAncestryView
from strandfolio.gui.views.history_diagram import moleculeUnits
from strandfolio.gui.views.reported_parents import ReportedParentsView
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.operations.pcr import PcrRun
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.rendering.maps import MapFigureSource


class _AttributionLinks(QWidget):
    """Connect the two externally labeled sources to the illustrated record."""

    def paintEvent(self, event) -> None:
        left, middle, right = self.width() // 4, self.width() // 2, 3 * self.width() // 4
        half = self.height() // 2
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#a89bb6"), 1.5))
            painter.drawLine(left, 0, left, half)
            painter.drawLine(right, 0, right, half)
            painter.drawLine(left, half, right, half)
            tip = self.height() - 3
            painter.drawLine(middle, half, middle, tip)
            painter.drawPolyline([QPointF(middle - 4, tip - 5), QPointF(middle, tip), QPointF(middle + 4, tip - 5)])


class _SourceAttributionPage(QSplitter):
    """Display the file author's source labels independently of recorded project events."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("analysisPanels")
        self.setHandleWidth(12)
        self.setChildrenCollapsible(False)
        self.addWidget(self._createDiagram())
        self.addWidget(self._createInspector())
        self.setSizes([1035, 365])

    def _createDiagram(self) -> QWidget:
        canvas = QWidget()
        canvas.setObjectName("workflowCanvas")
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(20, 16, 20, 18)
        heading = QLabel("Illustrated sources", canvas)
        heading.setObjectName("analysisPanelHeading")
        layout.addWidget(heading)
        layout.addStretch(1)
        diagram = QWidget(canvas)
        diagram.setMaximumWidth(660)
        diagramLayout = QVBoxLayout(diagram)
        diagramLayout.setContentsMargins(0, 0, 0, 0)
        diagramLayout.setSpacing(0)
        sources = QHBoxLayout()
        sources.setSpacing(18)
        backbone, self.backboneSource = self._card("Backbone source", "backboneCard", diagram)
        insert, self.insertSource = self._card("Insert source", "insertCard", diagram)
        self.insertLabel = FittedLabel("", insert)
        self.insertLabel.setObjectName("muted")
        insert.layout().addWidget(self.insertLabel)
        sources.addWidget(backbone, 1)
        sources.addWidget(insert, 1)
        diagramLayout.addLayout(sources)
        links = _AttributionLinks(diagram)
        links.setFixedHeight(65)
        diagramLayout.addWidget(links)
        product, self.illustratedRecord = self._card("Illustrated record", "card", diagram)
        self.recordSummary = FittedLabel("", product)
        self.recordSummary.setObjectName("muted")
        product.layout().addWidget(self.recordSummary)
        recordRow = QHBoxLayout()
        recordRow.addStretch(1)
        recordRow.addWidget(product, 2)
        recordRow.addStretch(1)
        diagramLayout.addLayout(recordRow)
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(diagram, 8)
        centered.addStretch(1)
        layout.addLayout(centered)
        layout.addStretch(2)
        return canvas

    def _createInspector(self) -> QScrollArea:
        sidebar = QScrollArea()
        sidebar.setObjectName("analysisInspector")
        sidebar.setMinimumWidth(248)
        sidebar.setMaximumWidth(370)
        sidebar.setWidgetResizable(True)
        sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details = QWidget(sidebar)
        details.setObjectName("workflowInspector")
        info = QVBoxLayout(details)
        info.setContentsMargins(14, 12, 14, 12)
        title = QLabel("Molecule details", details)
        title.setObjectName("analysisPanelHeading")
        info.addWidget(title)
        self.recordName = FittedLabel("", details)
        self.recordName.setObjectName("documentTitle")
        info.addWidget(self.recordName)
        self.moleculeInfo = QLabel(details)
        self.moleculeInfo.setObjectName("muted")
        info.addWidget(self.moleculeInfo)
        self.mapPreview = QSvgWidget(details)
        self.mapPreview.setFixedHeight(172)
        self.mapPreview.setAccessibleName("Annotated map of the illustrated record")
        self._mapIdentity: tuple[str, str] | None = None
        info.addWidget(self.mapPreview)
        info.addSpacing(12)
        source = QLabel("GenBank illustration credit", details)
        source.setObjectName("sectionTitle")
        info.addWidget(source)
        self.credit = QLabel(details)
        self.credit.setTextFormat(Qt.TextFormat.PlainText)
        self.credit.setWordWrap(True)
        info.addWidget(self.credit)
        info.addStretch()
        sidebar.setWidget(details)
        return sidebar

    def _card(self, role: str, name: str, parent: QWidget) -> tuple[QFrame, FittedLabel]:
        card = QFrame(parent)
        card.setObjectName(name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        title = QLabel(role, card)
        title.setObjectName("sectionTitle")
        header = QHBoxLayout()
        header.setSpacing(6)
        icon = QLabel(card)
        icon.setPixmap(workspaceIcon("record").pixmap(16, 16))
        header.addWidget(icon)
        header.addWidget(title, 1)
        layout.addLayout(header)
        value = FittedLabel("", card)
        value.setObjectName("documentTitle")
        layout.addWidget(value)
        return card, value

    def setAttribution(
        self, name: str, revision: SequenceRevision, attribution: IllustrationAttribution,
    ) -> None:
        self.recordName.setFullText(name)
        summary = [f"{len(revision):,} {moleculeUnits(revision)}", revision.moleculeType]
        if revision.topology:
            summary.append(revision.topology)
        self.moleculeInfo.setText(" · ".join(summary))
        self.recordSummary.setFullText(" · ".join(summary))
        if self._mapIdentity != (revision.revisionId, name):
            source = MapFigureSource(revision, name, revision.topology == "circular", mapFeatureTypes=None)
            scene = source.scene(310, 172, native=True, inspector=True)
            self.mapPreview.load(QByteArray(scene.svg.encode("utf-8")))
            self.mapPreview.renderer().setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            self.mapPreview.setAccessibleDescription(source.description)
            self._mapIdentity = (revision.revisionId, name)
        self.credit.setText(attribution.illustration)
        self.credit.setAccessibleName(f"GenBank illustration credit · {attribution.illustration}")
        self.backboneSource.setFullText(attribution.backboneSource)
        self.insertSource.setFullText(attribution.insertSource)
        self.insertLabel.setFullText(f"Illustrated insert · {attribution.insertLabel}")
        self.illustratedRecord.setFullText(attribution.recordName)


class HistoryView(QWidget):
    """Browse the active revision's ancestry and reopen its recorded analyses."""

    openAncestrySourceRequested = Signal(str, object)
    openDigestRequested = Signal(str)
    openPcrRequested = Signal(str, object)
    openSourceRequested = Signal(str)
    openPcrSourceRequested = Signal(str)
    usePrimerPairRequested = Signal(object)
    openSavedRequested = Signal()

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._runs: dict[str, RestrictionDigest | PcrRun] = {}
        self._selectedId: str | None = None
        self._selectedOutputKey: str | None = None
        self._activeContext = None
        self._awaitingSourceRuns = False
        self.setObjectName("historyWorkspace")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.pages = QStackedWidget(self)
        self.pages.setAccessibleName("History views")
        self.ancestry = HistoryAncestryView(controller, self.pages)
        self.ancestry.openSnapshotRequested.connect(self.openAncestrySourceRequested)
        self.analysisPage = self._createAnalysisPage()
        self.reportedParents = ReportedParentsView(controller, self.pages)
        self.reportedParents.openSnapshotRequested.connect(self.openAncestrySourceRequested)
        self.sourceAttribution = _SourceAttributionPage(self.pages)
        self.workflowTools = QWidget(self)
        self.workflowTools.setObjectName("workflowTools")
        toolbar = QHBoxLayout(self.workflowTools)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch()
        self.pageSelector = QComboBox(self.workflowTools)
        self.pageSelector.setAccessibleName("History view")
        for page, name in ((self.ancestry, "Ancestry"), (self.analysisPage, "Recorded analyses"),
                           (self.reportedParents, "Reported parents")):
            self.pages.addWidget(page)
            self.pageSelector.addItem(name)
        self.pages.addWidget(self.sourceAttribution)
        toolbar.addWidget(self.pageSelector)
        self.retryPcrHistoryButton = QPushButton("Retry PCR history", self.workflowTools)
        self.retryPcrHistoryButton.clicked.connect(controller.pcr.retryPcrHistory)
        toolbar.addWidget(self.retryPcrHistoryButton)
        self.pendingSaveButton = PendingSaveButton("Retry project save", self.analysisPage, chooseSource=True)
        self.pendingSaveButton.retryRequested.connect(self._retryProjectSave)
        self.analysisPage.layout().insertWidget(0, self.pendingSaveButton)
        self.saveNotice = SaveNotice(self.analysisPage)
        self.analysisPage.layout().insertWidget(1, self.saveNotice)
        controller.projectChanged.connect(self.saveNotice.showMessage)
        controller.analysis.retryChanged.connect(self._refreshPendingSaves)
        controller.pcr.retryChanged.connect(self._refreshPendingSaves)
        self._refreshPendingSaves()
        self.openSavedButton = QPushButton("Open saved digestion…", self.workflowTools)
        self.openSavedButton.clicked.connect(self.openSavedRequested)
        toolbar.addWidget(self.openSavedButton)
        toolbar.addWidget(self.ancestry.exportButton)
        layout.addWidget(self.workflowTools)
        self.pageSelector.activated.connect(self._keepPage)
        self.pageSelector.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self.pages.currentChanged.connect(self._keepPage)
        self.pages.currentChanged.connect(self.refreshVisibleAnalyses)
        self.pages.currentChanged.connect(self._refreshAnalyses)
        self.ancestry.diagram.activated.connect(self._keepPage)
        self.ancestry.revisionList.activated.connect(self._keepPage)
        self.ancestry.listButton.clicked.connect(self._keepPage)
        self.ancestry.inputList.activated.connect(self._keepPage)
        layout.addWidget(self.pages, 1)

        controller.analysis.historyChanged.connect(self.refresh)
        controller.pcr.historyChanged.connect(self.refresh)
        controller.documentChanged.connect(self.refresh)
        controller.nameChanged.connect(self.refresh)
        self.refresh()

    def _refreshPendingSaves(self) -> None:
        runs = (*self.controller.analysis.pendingSaveRuns("project"),
                *self.controller.pcr.pendingSaveRuns(projectWide=True))
        self.pendingSaveButton.setRuns(tuple(sorted(runs, key=lambda run: run.createdAt, reverse=True)))

    def _retryProjectSave(self, run: PcrRun | RestrictionDigest) -> None:
        if isinstance(run, PcrRun):
            self.controller.pcr.retryRunWrite(
                run.operationId, projectWide=True, report=self.saveNotice.showMessage,
            )
        else:
            self.controller.analysis.retryDigestWrite(
                run.operationId, scope="project", report=self.saveNotice.showMessage,
            )

    def _createAnalysisPage(self) -> QWidget:
        page = QWidget(self.pages)
        layout = QVBoxLayout(page)
        self.status = QLabel(page)
        self.status.setObjectName("muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.runList = ChoiceRows("Recorded analyses for the active revision", page)
        self.runList.activated.connect(self._selectRun)
        layout.addWidget(self.runList, 1)

        self.runDetails = QPlainTextEdit(page)
        self.runDetails.setObjectName("historyEvidence")
        self.runDetails.setAccessibleName("Selected analysis details")
        self.runDetails.setReadOnly(True)
        self.runDetails.setMaximumHeight(115)
        layout.addWidget(self.runDetails)
        self.outputList = ChoiceRows("Recorded PCR outputs", page)
        self.outputList.activated.connect(self._selectOutput)
        layout.addWidget(self.outputList, 1)
        actions = QHBoxLayout()
        self.openDigestButton = QPushButton("Open recorded digestion", page)
        self.openDigestButton.setObjectName("primary")
        self.openDigestButton.clicked.connect(self._openRun)
        self.openPcrButton = QPushButton("Open recorded PCR", page)
        self.openPcrButton.setObjectName("primary")
        self.openPcrButton.clicked.connect(self._openRun)
        self.openSourceButton = QPushButton("Open source snapshot", page)
        self.openSourceButton.clicked.connect(self._openSource)
        actions.addWidget(self.openDigestButton)
        actions.addWidget(self.openPcrButton)
        self.usePairButton = QPushButton("Use pair in editor", page)
        self.usePairButton.clicked.connect(self._reusePrimers)
        actions.addWidget(self.usePairButton)
        actions.addWidget(self.openSourceButton)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def refresh(self) -> None:
        """Refresh exact ancestry and retain the selected run where it still applies."""
        revision = self.controller.session.current
        attribution = revision.illustrationAttribution if revision is not None else None
        index = self.pages.indexOf(self.sourceAttribution)
        with QSignalBlocker(self.pageSelector):
            if attribution is not None:
                self.sourceAttribution.setAttribution(self.controller.displayName, revision, attribution)
                if self.pageSelector.count() == index:
                    self.pageSelector.addItem("Source attribution")
            elif self.pageSelector.count() > index:
                self.pageSelector.removeItem(index)
        context = self.controller.workspace.taskContext(True)
        if context != self._activeContext:
            keepAnalyses = (
                self.isVisible() and self.pages.currentWidget() is self.analysisPage
                and self._activeContext is not None and self._activeContext.activeDocumentId is not None
                and self._activeContext.projectVersion == context.projectVersion
            )
            self._activeContext = context
            keepReported = self.pages.currentWidget() is self.reportedParents
            self._awaitingSourceRuns = (
                revision is not None and revision.outputReference is None
                and attribution is None and not keepReported
            )
            if not keepReported:
                page = self.analysisPage if revision is None else self.ancestry
                if attribution is not None and revision.outputReference is None:
                    page = self.sourceAttribution
                if keepAnalyses and revision is not None:
                    page = self.analysisPage
                with QSignalBlocker(self.pages):
                    self.pages.setCurrentWidget(page)
            self.refreshVisibleAnalyses()
        runs = self._activeRuns() if self._awaitingSourceRuns else None
        if runs:
            self._awaitingSourceRuns = False
            with QSignalBlocker(self.pages):
                self.pages.setCurrentWidget(self.analysisPage)
        self.ancestry.refresh()
        if self.reportedParents.isVisible():
            self.reportedParents.refresh()
        self._refreshAnalyses(loadedRuns=runs)

    def showSourceAttribution(self) -> bool:
        """Select the active record's file attribution when its illustration block is complete."""
        revision = self.controller.session.current
        if revision is None or revision.illustrationAttribution is None:
            return False
        self._awaitingSourceRuns = False
        self.pages.setCurrentWidget(self.sourceAttribution)
        return True

    def _keepPage(self) -> None:
        """Keep the page or diagram the user chose when a background history read completes."""
        self._awaitingSourceRuns = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refreshVisibleAnalyses()
        self._refreshAnalyses()

    def refreshVisibleAnalyses(self) -> None:
        """Request current project runs when returning to the visible analysis list."""
        if self.isVisible() and (self.pages.currentWidget() is self.analysisPage or self._awaitingSourceRuns):
            self.controller.pcr.refreshActiveHistory()

    def _activeRuns(self) -> tuple[RestrictionDigest | PcrRun, ...]:
        return (*self.controller.analysis.historyRuns(), *self.controller.pcr.historyRuns())

    @Slot()
    def _refreshAnalyses(
        self, *, loadedRuns: tuple[RestrictionDigest | PcrRun, ...] | None = None,
    ) -> None:
        self.pageSelector.setCurrentIndex(self.pages.currentIndex())
        self.ancestry.exportButton.setVisible(self.pages.currentWidget() is self.ancestry)
        error = self.controller.pcr.historyReadError
        self.retryPcrHistoryButton.setVisible(bool(error))
        self.retryPcrHistoryButton.setToolTip(error)
        self.retryPcrHistoryButton.setEnabled(not self.controller.pcr.loadingAvailableRuns)
        self.openSavedButton.setVisible(self.pages.currentWidget() is self.analysisPage)
        if self.pages.currentWidget() is not self.analysisPage:
            return
        revision = self.controller.session.current
        if loadedRuns is None:
            loadedRuns = self._activeRuns() if revision is not None else ()
        runs = sorted(loadedRuns, key=lambda run: run.createdAt, reverse=True)
        self._runs = {run.operationId: run for run in runs}
        rows = []
        for run in runs:
            if isinstance(run, PcrRun):
                description = f"PCR · {run.status.capitalize()}"
                if run.status == "completed":
                    count = len(run.products)
                    description += f" · {count:,} exact-match {'product' if count == 1 else 'products'}"
                if self.controller.pcr.isRunPendingWrite(run.operationId):
                    description += " · Session only"
            else:
                enzymes = " + ".join(enzyme.name for enzyme in run.enzymes)
                description = f"Digest · {enzymes} · {len(run.cuts):,} cuts · {len(run.fragments):,} products"
            text = f"{description} · {run.createdAt} · Run {run.operationId[:8]}"
            rows.append(ChoiceRow(run.operationId, text, f"{text} · {run.operationId}"))
        self.runList.setRows(tuple(rows))
        self.runList.setMaximumHeight(min(300, 8 + 30 * len(rows)))
        selectionExists = self._selectedId in self._runs
        if not selectionExists:
            self._selectedId = runs[0].operationId if runs else None
            self._selectedOutputKey = None
        self.runList.setSelected(self._selectedId, reveal=not selectionExists)
        if revision is None:
            status = "Open a sequence to inspect its recorded analyses."
        elif not runs and error:
            status = f"{self.controller.displayName} · No analyses loaded for this revision."
        elif not runs:
            status = f"{self.controller.displayName} · No recorded analyses for this revision."
        else:
            status = f"{self.controller.displayName} · Revision {revision.revisionId}"
        self.status.setText(f"{status}\n{error}" if error else status)
        self.runList.setVisible(bool(runs))
        self._updateSelection()

    def shutdown(self) -> None:
        """Cancel any active ancestry read before closing the workspace."""
        self.ancestry.shutdown()
        self.reportedParents.shutdown()

    def _selectedRun(self) -> RestrictionDigest | PcrRun | None:
        return self._runs.get(self._selectedId)

    def _selectRun(self, operationId: str) -> None:
        if operationId != self._selectedId:
            self._selectedOutputKey = None
        self._selectedId = operationId
        self._updateSelection()

    def _selectOutput(self, outputKey: str) -> None:
        self._selectedOutputKey = outputKey

    def _updateSelection(self) -> None:
        run = self._selectedRun()
        isPcr = isinstance(run, PcrRun)
        completedPcr = isPcr and run.status == "completed"
        self.openDigestButton.setVisible(not isPcr)
        self.openDigestButton.setEnabled(run is not None and not isPcr)
        self.usePairButton.setVisible(isPcr)
        self.openPcrButton.setVisible(completedPcr)
        self.openPcrButton.setEnabled(completedPcr)
        self.openSourceButton.setEnabled(run is not None)
        self.runDetails.setVisible(run is not None)
        self.outputList.setVisible(completedPcr and bool(run.products))
        if isPcr:
            self.runDetails.setPlainText(
                f"{self.controller.pcr.runOutcome(run)}\n"
                f"Template {run.template.name} · Revision {run.template.revisionId}\n"
                f"Run {run.operationId} · Engine {run.engineVersion}",
            )
            self._updateOutputs(run)
        else:
            self.outputList.setRows(())
            if run is not None:
                self.runDetails.setPlainText(
                    f"Source {run.template.name} · Revision {run.template.revisionId}\n"
                    f"Run {run.operationId} · Engine {run.engineVersion} · "
                    f"Biopython catalogue {run.catalogueRelease.biopythonVersion}",
                )

    def _updateOutputs(self, run: PcrRun) -> None:
        rows = []
        for index, product in enumerate(run.products, 1):
            text = f"Product {index} · {product.sizeBp:,} bp · {formatPcrSource(product)}"
            rows.append(ChoiceRow(product.outputKey, text, f"{text} · Output {product.outputKey}"))
        self.outputList.setRows(tuple(rows))
        self.outputList.setMaximumHeight(min(184, 8 + 30 * len(rows)))
        keys = {row.key for row in rows}
        if not rows:
            self._selectedOutputKey = None
        elif self._selectedOutputKey not in keys:
            lastKey = self.controller.pcr.viewForRun(run.operationId).selectedOutputKey
            self._selectedOutputKey = lastKey if lastKey in keys else rows[0].key
        self.outputList.setSelected(self._selectedOutputKey)

    def _reusePrimers(self) -> None:
        run = self._selectedRun()
        if isinstance(run, PcrRun):
            self.usePrimerPairRequested.emit(run)

    def _openRun(self) -> None:
        run = self._selectedRun()
        if isinstance(run, PcrRun) and run.status == "completed":
            self.openPcrRequested.emit(run.operationId, self._selectedOutputKey)
        elif isinstance(run, RestrictionDigest):
            self.openDigestRequested.emit(run.operationId)

    def _openSource(self) -> None:
        run = self._selectedRun()
        if isinstance(run, PcrRun):
            self.openPcrSourceRequested.emit(run.template.revisionId)
        elif run is not None:
            self.openSourceRequested.emit(run.template.revisionId)
