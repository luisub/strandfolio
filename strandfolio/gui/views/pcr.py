"""Primer entry and recorded PCR product previews."""

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from strandfolio.application.pcr_session import MAX_PRIMER_SELECTION_BP, PRIMER_SELECTION_WARNING_BP
from strandfolio.domain.coordinates import SequenceSelection
from strandfolio.domain.primers import PrimerEntry
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.primer_files import loadPrimerPair, savePrimerPair
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.panels.pending_saves import PendingSaveButton
from strandfolio.gui.panels.saved_primer_pairs import SavedPrimerPairsDialog
from strandfolio.gui.panels.sequence_text_size import sequenceTextSizeControl
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.gui.views.gel_preview import formatSourceIntervals
from strandfolio.gui.views.primer_structure_review import PrimerStructureReview
from strandfolio.gui.views.primer_site_comparison import PrimerSiteComparison
from strandfolio.gui.views.pcr_sequence import PcrOverview, PcrSequenceView
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.operations.pcr import PcrProduct, PcrRun
from strandfolio.operations.primer_structures import estimatePrimerMelting


def _label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _setFeedback(label: QLabel, text: str, *, warning: bool = False) -> None:
    style = "notice" if warning else "muted"
    if label.objectName() != style:
        label.setObjectName(style)
        label.style().unpolish(label)
        label.style().polish(label)
        label.updateGeometry()
    label.setText(text)
    label.setVisible(bool(text))


def _disclosure(title: str, details: QWidget, parent: QWidget) -> QPushButton:
    button = QPushButton(parent)
    button.setObjectName("pcrDisclosure")
    button.setCheckable(True)
    button.setAccessibleName(title)
    button.setStyleSheet("QPushButton#pcrDisclosure { text-align: left; padding: 6px 8px; }")
    def reveal(opened: bool) -> None:
        button.setText(f"{'▾' if opened else '▸'}  {title}")
        details.setVisible(opened)
    button.toggled.connect(reveal)
    reveal(False)
    return button


class PcrView(QWidget):
    """Inspect and simulate two existing 5′→3′ primers on the selected DNA revision."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.pcr = controller.pcr
        self._completedRunId: str | None = None
        self._sessionPairs = {}
        self.setObjectName("pcrWorkspace")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workflowTools = QWidget(self)
        self.workflowTools.setObjectName("workflowTools")
        toolbar = QHBoxLayout(self.workflowTools)
        toolbar.setContentsMargins(2, 0, 14, 0)
        toolbar.addStretch(1)
        zoom, self.textSize = sequenceTextSizeControl(self.workflowTools, viewName="PCR sequence")
        toolbar.addWidget(zoom)
        self.runControl = QStackedWidget(self.workflowTools)
        self.simulateButton = QPushButton("Simulate PCR", self.runControl)
        self.simulateButton.setIcon(workspaceIcon("play", onAccent=True))
        self.simulateButton.setObjectName("primary")
        self.simulateButton.clicked.connect(self.pcr.runPcr)
        self.cancelButton = QPushButton(workspaceIcon("close"), "Cancel PCR", self.runControl)
        self.cancelButton.setAccessibleName("Cancel PCR")
        self.cancelButton.clicked.connect(self.pcr.cancelPcr)
        self.runControl.addWidget(self.simulateButton)
        self.runControl.addWidget(self.cancelButton)
        toolbar.addWidget(self.runControl)
        layout.addWidget(self.workflowTools)

        stage = QSplitter(Qt.Orientation.Horizontal, self)
        stage.setChildrenCollapsible(False)
        stage.setHandleWidth(1)
        sequenceArea = QWidget(stage)
        sequenceArea.setObjectName("workflowCanvas")
        sequenceLayout = QVBoxLayout(sequenceArea)
        sequenceLayout.setContentsMargins(14, 12, 14, 8)
        self.templateLabel = FittedLabel("", sequenceArea)
        self.templateLabel.setObjectName("documentTitle")
        self.templateLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        heading = QHBoxLayout()
        heading.addWidget(self.templateLabel, 1)
        self.templateMetadata = _label("", sequenceArea)
        self.templateMetadata.setObjectName("muted")
        heading.addWidget(self.templateMetadata)
        sequenceLayout.addLayout(heading)
        self.canvas = PcrSequenceView(controller, sequenceArea)
        self.canvas.primerSelected.connect(self._focusSelectedPrimer)
        self.overview = PcrOverview(controller, self.canvas, sequenceArea)
        sequenceLayout.addWidget(self.overview)
        self.targetLabel = FittedLabel("", sequenceArea)
        self.targetLabel.setObjectName("muted")
        sequenceLayout.addWidget(self.targetLabel)
        self.siteComparison = PrimerSiteComparison(controller, sequenceArea)
        self.siteComparison.pickRequested.connect(self.canvas.startOrUseSelection)
        self.siteComparison.revealRequested.connect(self.canvas.revealInterval)
        self.canvas.comparisonDismissed.connect(self.siteComparison.dismiss)
        sequenceLayout.addWidget(self.siteComparison)
        self.pickBanner = QWidget(sequenceArea)
        prompt = QHBoxLayout(self.pickBanner)
        prompt.setContentsMargins(0, 0, 0, 0)
        self.pickReadout = QLabel(self.pickBanner)
        self.pickReadout.setObjectName("selectionCount")
        self.pickReadout.setAccessibleName("Primer selection length and estimated melting temperature")
        prompt.addWidget(self.pickReadout)
        prompt.addStretch(1)
        self.cancelPick = QPushButton("Cancel", self.pickBanner)
        self.cancelPick.setAccessibleName("Cancel primer selection")
        self.cancelPick.setToolTip("Cancel primer selection (Esc)")
        self.cancelPick.clicked.connect(self.canvas.cancelPrimerPick)
        prompt.addWidget(self.cancelPick)
        sequenceLayout.addWidget(self.pickBanner)
        self.textSize.valueChanged.connect(self.canvas.setTextSize)
        sequenceLayout.addWidget(self.canvas, 1)
        self.overlayLabel = _label("", sequenceArea)
        self.overlayLabel.setObjectName("muted")
        self.overlayLabel.setToolTip("Forward primer in green · Reverse primer in amber")
        self.overlayLabel.setAccessibleDescription(self.overlayLabel.toolTip())
        self.overlayLabel.hide()
        sequenceLayout.addWidget(self.overlayLabel)
        stage.addWidget(sequenceArea)
        stage.addWidget(self._createInspector(stage))
        stage.setStretchFactor(0, 1)
        stage.setSizes([950, 324])
        layout.addWidget(stage, 1)

        self.status = _label("", sequenceArea)
        self.showResultButton = QPushButton("Show result", sequenceArea)
        self.showResultButton.clicked.connect(self._openCompletedRun)
        self.retryButton = PendingSaveButton("Retry project save", self)
        self.retryButton.retryRequested.connect(lambda run: self.pcr.retryRunWrite(run.operationId))
        feedback = QHBoxLayout()
        feedback.addWidget(self.status, 1)
        feedback.addWidget(self.showResultButton)
        feedback.addWidget(self.retryButton)
        sequenceLayout.addLayout(feedback)
        self.pcr.inputsChanged.connect(self._refreshInputs)
        self.pcr.statusChanged.connect(self._updateStatus)
        self.pcr.busyChanged.connect(self._updateStatus)
        self.pcr.retryChanged.connect(self._updateStatus)
        self.pcr.runCompleted.connect(self._recordCompletedRun)
        self.canvas.pickingChanged.connect(self._onPickChanged)
        controller.nameChanged.connect(self._updateContext)
        self.canvas.selectionReadoutChanged.connect(self._updateSelectionControls)
        self._refreshInputs()

    def _createInspector(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(290)
        scroll.setMaximumWidth(365)
        inspector = QWidget(scroll)
        inspector.setObjectName("workflowInspector")
        sidebar = QVBoxLayout(inspector)
        sidebar.setContentsMargins(17, 15, 17, 15)
        sidebar.setSpacing(9)
        headingRow = QHBoxLayout()
        pairHeading = _label("Draft primers · 5′→3′", inspector)
        pairHeading.setObjectName("analysisPanelHeading")
        headingRow.addWidget(pairHeading, 1)
        self.clearDraftButton = QPushButton("Clear draft", inspector)
        self.clearDraftButton.setToolTip("Clear the selection and both draft primers, including their 5′ ends.")
        self.clearDraftButton.clicked.connect(self._clearDraft)
        headingRow.addWidget(self.clearDraftButton)
        sidebar.addLayout(headingRow)
        self.selectionLabel = _label("", inspector)
        sidebar.addWidget(self.selectionLabel)
        self.selectionHint = _label("", inspector)
        sidebar.addWidget(self.selectionHint)
        self.setPrimerButtons = {}
        for role, strand in (("Forward", 1), ("Reverse", -1)):
            button = QPushButton(f"Select {role.lower()} primer", inspector)
            button.setCheckable(True)
            button.setToolTip("Use highlighted bases or drag to select a new site.")
            button.setAccessibleDescription(button.toolTip())
            button.clicked.connect(lambda checked, direction=strand: self._togglePrimerPick(direction, checked))
            self.setPrimerButtons[strand] = button
            sidebar.addWidget(button)
            endField = self._oligoField(f"{role} 5′ end", inspector, optional=True)
            endField.setPlaceholderText("5′ end")
            endField.setMaximumWidth(84)
            endField.setToolTip("Optional unpaired 5′ end of the primer.")
            field = self._oligoField(f"{role} annealing", inspector)
            field.setPlaceholderText("Binding sequence")
            field.setAccessibleDescription(
                "Uses the selected top-strand sequence." if strand == 1 else
                "Uses the reverse complement of the selected top-strand sequence."
            )
            if strand == 1:
                self.forwardAnnealing = field
                self.forwardTail = endField
            else:
                self.reverseAnnealing = field
                self.reverseTail = endField
            for editor in (endField, field):
                editor.textEdited.connect(lambda _text, direction=strand: self.siteComparison.setRole(direction))
            bases = QHBoxLayout()
            bases.setSpacing(4)
            bases.addWidget(endField)
            bases.addWidget(field, 1)
            sidebar.addLayout(bases)
        self.primerLengths = _label("", inspector)
        self.primerLengths.setObjectName("muted")
        sidebar.addWidget(self.primerLengths)
        self.structureReview = PrimerStructureReview(inspector, live=True)
        sidebar.addWidget(self.structureReview)
        self.savedPairsButton = QPushButton("Saved pairs…", inspector)
        self.savedPairsButton.setAccessibleName("Manage saved primer pairs")
        self.savedPairsButton.clicked.connect(self._openSavedPairs)
        sidebar.addWidget(self.savedPairsButton)
        actions = QHBoxLayout()
        self.loadPairButton = QPushButton("Load FASTA…", inspector)
        self.loadPairButton.setAccessibleName("Load primer pair from FASTA")
        self.loadPairButton.clicked.connect(lambda: loadPrimerPair(self.controller, self))
        self.savePairButton = QPushButton("Save FASTA…", inspector)
        self.savePairButton.setAccessibleName("Save primer pair as FASTA")
        self.savePairButton.clicked.connect(lambda: savePrimerPair(self.controller, self))
        actions.addWidget(self.loadPairButton)
        actions.addWidget(self.savePairButton)
        sidebar.addLayout(actions)

        self.primerReview = QPlainTextEdit(inspector)
        self.primerReview.setReadOnly(True)
        self.primerReview.setTabChangesFocus(True)
        self.primerReview.setFont(workspaceMonoFont(12))
        self.primerReview.setAccessibleName("Complete entered primers in 5′ to 3′ order")
        self.primerReview.setFixedHeight(110)
        self.reviewButton = _disclosure("Review full primers", self.primerReview, inspector)
        sidebar.addWidget(self.reviewButton)
        sidebar.addWidget(self.primerReview)
        sidebar.addStretch(1)
        QWidget.setTabOrder(self.reviewButton, self.primerReview)
        scroll.setWidget(inspector)
        return scroll

    def _openSavedPairs(self) -> None:
        dialog = SavedPrimerPairsDialog(self.controller, self._sessionPairs, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def _oligoField(
        self, label: str, parent: QWidget, *, optional: bool = False,
    ) -> QLineEdit:
        field = QLineEdit(parent)
        # Keep an overlength paste visible for correction. The DNA model enforces its limit.
        field.setMaxLength(2_147_483_647)
        field.setFont(workspaceMonoFont(13))
        field.setAccessibleName(label if optional else f"{label} sequence")
        field.textChanged.connect(self._setPrimers)
        if not optional:
            role = label.split()[0]
            action = field.addAction(workspaceIcon("close"), QLineEdit.ActionPosition.TrailingPosition)
            action.setText(f"Clear {role.lower()} primer and tail")
            action.setToolTip(action.text())
            action.triggered.connect(lambda: self.pcr.clearPrimer(role))
        return field

    def _setPrimers(self) -> None:
        self.pcr.setPrimerInputs(*(field.text() for field in self._primerFieldsInOrder()))

    def _primerFieldsInOrder(self) -> tuple[QLineEdit, ...]:
        return self.forwardAnnealing, self.reverseAnnealing, self.forwardTail, self.reverseTail

    def _refreshInputs(self) -> None:
        texts = self.pcr.primerTexts
        for field, value in zip(self._primerFieldsInOrder(), texts, strict=True):
            if value != field.text():
                with QSignalBlocker(field):
                    field.setText(value)
        entries = self.pcr.primerEntries
        for field, entry in zip((self.forwardAnnealing, self.reverseAnnealing), entries, strict=True):
            field.actions()[0].setVisible(bool(entry.annealing or entry.tail))
        self.siteComparison.refresh()
        counts, reviewed = [], []
        siteSummaries = self.canvas.updateDraftSites(self.controller.session.current, entries)
        exact = self.pcr.selectedPrimerIdeas
        editedRoles = {site.strand for site in self.pcr.primerComparisonSites if site not in exact}
        for strand, entry, name, sites in zip((1, -1), entries, self.pcr.primerNames, siteSummaries, strict=True):
            problem = entry.problem
            if problem:
                if entry.tail or entry.annealing:
                    counts.append(problem)
                reviewed.append(problem)
            else:
                length = f"{len(entry.sequence):,} nt"
                counts.append(f"{entry.role} · {length}"
                              + (f" · {sites}" if sites else "")
                              + (" · site differs" if strand in editedRoles else ""))
                label = entry.role if name == entry.role else f"{entry.role} · {name}"
                reviewed.append(f"{label} 5′→3′ · {len(entry.sequence):,} nt "
                                f"({len(entry.tail):,} nt 5′ tail)\n{entry.sequence}")
        self.primerLengths.setText("\n".join(counts))
        self.primerLengths.setVisible(bool(counts))
        self.primerReview.setPlainText("\n\n".join(reviewed))
        self.structureReview.setInputs(self.controller.workspace.activeDocumentId, entries)
        self._restoreEditorResult()
        self._updateContext()
        self._updateStatus()

    def _focusSelectedPrimer(self, strand: int) -> None:
        self.siteComparison.setRole(strand)
        field = self.forwardAnnealing if strand == 1 else self.reverseAnnealing
        field.setFocus(Qt.FocusReason.OtherFocusReason)
        field.selectAll()

    def _togglePrimerPick(self, strand: int, checked: bool) -> None:
        if checked:
            self.canvas.startOrUseSelection(strand)
        else:
            self.canvas.cancelPrimerPick()

    def _clearDraft(self) -> None:
        self.canvas.cancelPrimerPick()
        self.pcr.clearDraft()

    def _onPickChanged(self) -> None:
        self.siteComparison.setPicking(self.canvas.pickingPrimer is not None)
        self._updateContext()
        self._updateStatus()

    def _updateSelectionControls(self) -> None:
        session = self.controller.session
        revision = session.current
        selection = self.canvas.readoutSelection
        target = self.pcr.target
        dna = revision is not None and revision.moleculeType == "DNA"
        self.targetLabel.setFullText(f"PCR target · {self._intervalText(target, revision)} nt" if target else "")
        self.targetLabel.setVisible(bool(target))
        picking = self.canvas.pickingPrimer
        self.clearDraftButton.setEnabled(any(self.pcr.primerTexts) or picking is not None or bool(dna and selection))
        for strand, button in self.setPrimerButtons.items():
            button.setEnabled(dna)
            button.setChecked(strand == picking)
        self.pickBanner.setVisible(picking is not None)
        showSelection = bool(dna and selection and picking is None and selection != target)

        hint = ""
        warning = False
        estimate = None
        length = len(selection) if dna and selection else 0
        if length:
            if length > MAX_PRIMER_SELECTION_BP:
                if picking is not None:
                    hint = f"Primer limit · {MAX_PRIMER_SELECTION_BP} nt"
                    warning = True
                elif selection == target:
                    hint = f"Select up to {MAX_PRIMER_SELECTION_BP} nt for a primer."
                else:
                    hint = (f"Select up to {MAX_PRIMER_SELECTION_BP} nt for a primer. "
                            "Right-click to set a target.")
            elif length > PRIMER_SELECTION_WARNING_BP:
                hint = "Long primer selection"
                warning = picking is not None
            if length <= MAX_PRIMER_SELECTION_BP:
                try:
                    if picking is None:
                        bases = self.pcr.validateSelection()
                        role = "Selected"
                    else:
                        bases = self.pcr.selectionIdea(picking).annealingSequence
                        role = "Forward" if picking == 1 else "Reverse"
                    if length >= 2 and (picking is not None or showSelection):
                        estimate = estimatePrimerMelting(PrimerEntry(role, bases, ""))
                except ValueError as error:
                    hint = str(error)
                    warning = picking is not None
        elif dna and picking is None and any(
            len(entry.annealing) > MAX_PRIMER_SELECTION_BP for entry in self.pcr.primerEntries
        ):
            hint = f"To replace a primer, select up to {MAX_PRIMER_SELECTION_BP} nt."
        elif dna and picking is None:
            longMembers = [f"{entry.role} · {len(entry.annealing)} nt"
                           for entry in self.pcr.primerEntries
                           if len(entry.annealing) > PRIMER_SELECTION_WARNING_BP]
            if longMembers:
                hint = "Long primer · " + " / ".join(longMembers)
        _setFeedback(self.selectionHint, hint, warning=warning)
        tmText = "—"
        tmHelp = "Tm estimate for the selected bases with their perfect complement."
        if estimate is not None and estimate.tmC is not None:
            approximate = "≈" if estimate.approximate else ""
            tmText = f"{approximate}{estimate.tmC:.0f} °C"
        elif estimate is not None and estimate.reason:
            tmHelp = estimate.reason
        elif length == 1:
            tmHelp = "Select at least two bases for a Tm estimate."

        lengthText = f"{length:,} nt"
        readoutText = f"{lengthText}, Tm: {tmText}"
        selectionText = location = ""
        selectionHelp = ""
        if showSelection:
            selectionText = lengthText
            location = self._intervalText(selection, revision)
            selectionHelp = location
            if length <= MAX_PRIMER_SELECTION_BP:
                selectionText = readoutText
                selectionHelp += f"\n{tmHelp}"
        self.selectionLabel.setText(selectionText)
        self.selectionLabel.setToolTip(selectionHelp)
        self.selectionLabel.setAccessibleDescription(location)
        self.selectionLabel.setVisible(showSelection)

        self.pickReadout.setText(readoutText if picking is not None else "0 nt, Tm: —")
        self.pickReadout.setToolTip(tmHelp)
        self.pickReadout.setAccessibleDescription(f"{self.pickReadout.text()}. {tmHelp}")

    @staticmethod
    def _intervalText(selection: SequenceSelection, revision: SequenceRevision) -> str:
        endpoints = selection.displayRange(len(revision), circular=revision.topology == "circular")
        if endpoints:
            start, end = endpoints
            span = f"{start:,}–{end:,}" + (" (wraps)" if start > end else "")
        else:
            span = formatSourceIntervals(selection.segments)
        if selection.featureId:
            span = f"{revision.getFeature(selection.featureId).label} · {span}"
        return span

    def _updateContext(self) -> None:
        revision = self.controller.session.current
        self._updateSelectionControls()
        if revision is None:
            self.templateLabel.setFullText("PCR template")
            self.templateMetadata.clear()
            self.templateMetadata.setToolTip("")
        else:
            unit = "aa" if revision.moleculeType == "protein" else "nt"
            self.templateLabel.setFullText(self.controller.displayName)
            self.templateMetadata.setText(
                f"{len(revision):,} {unit} · "
                f"{revision.topology or 'Unknown topology'} {revision.moleculeType}",
            )
            self.templateMetadata.setToolTip(f"Revision {revision.revisionId}")

    def _updateStatus(self) -> None:
        reason = self.pcr.unavailableReason
        self.savePairButton.setEnabled(all(not entry.problem for entry in self.pcr.primerEntries))
        runButton = self.cancelButton if self.pcr.canCancel else self.simulateButton
        previousRunButton = self.runControl.currentWidget()
        moveFocus = previousRunButton.hasFocus()
        self.simulateButton.setEnabled(not reason)
        if previousRunButton is not runButton:
            self.runControl.setCurrentWidget(runButton)
            if moveFocus:
                runButton.setFocus(Qt.FocusReason.OtherFocusReason)
        runHint = self.pcr.statusMessage if self.pcr.status == "saving" else reason
        runHint = runHint or "Search both orientations of this template for exact primer matches."
        self.simulateButton.setToolTip(runHint)
        self.simulateButton.setAccessibleDescription(runHint)
        self.showResultButton.setVisible(self._completedRunId is not None and not self.pcr.busy)
        self.retryButton.setRuns(self.pcr.pendingSaveRuns())
        message = self.pcr.statusMessage or reason
        if not self.pcr.statusMessage and reason:
            picking = self.canvas.pickingPrimer is not None and reason.startswith("Select or enter")
            shownInInspector = any(
                entry.problem == reason and (entry.annealing or entry.tail) for entry in self.pcr.primerEntries
            )
            if picking or shownInInspector:
                message = ""
        result = self.pcr.editorResult
        warning = self.pcr.status in ("invalid", "capped", "failed", "unsupported", "unsaved") or (
            self.pcr.status == "completed" and result is not None and bool(result.run.warnings)
        )
        _setFeedback(self.status, message, warning=warning)

    def _restoreEditorResult(self) -> None:
        result = self.pcr.editorResult
        run = result.run if result and result.run.status == "completed" else None
        self._completedRunId = run.operationId if run else None
        matches = self.pcr.resultMatchesDraft
        self.showResultButton.setText("Show previous result" if run and not matches else "Show result")
        product = None
        if run and matches:
            view = self.controller.workspace.pcrRuns.getView(run.operationId)
            if view is not None and view.selectedOutputKey is not None:
                product = self.pcr.getOutput(run.operationId, view.selectedOutputKey)
        self.showProduct(product)

    def _recordCompletedRun(self, run: PcrRun) -> None:
        self._restoreEditorResult()
        if run.status not in ("completed", "canceled") and run.template is self.controller.session.current:
            captured = (PrimerEntry.fromRevision("Forward", run.forwardPrimer),
                        PrimerEntry.fromRevision("Reverse", run.reversePrimer))
            self.structureReview.setInputs(run.operationId, captured, runEvidence=(
                f"PCR run {run.operationId}\n{self.pcr.runOutcome(run)}\n"
                "Template site and product counts unavailable for this stopped search."
            ))
            self.structureReview.startCheck()
        self._updateStatus()

    def _openCompletedRun(self) -> None:
        if self._completedRunId is not None:
            self.pcr.reopenRun(self._completedRunId)

    def previewRunProduct(self, operationId: str, product: PcrProduct | None) -> None:
        """Follow dialog selection only for the active editor's current run."""
        result = self.pcr.editorResult
        if result is not None and result.run.operationId == operationId and self.pcr.resultMatchesDraft:
            self.showProduct(product)

    def showProduct(self, product: PcrProduct | None, role: str | None = None) -> None:
        """Highlight the recorded product only on its exact open template revision."""
        revision = self.controller.session.current
        if product is not None and (revision is None or product.templateRevisionId != revision.revisionId):
            product = None
        self.canvas.setProduct(product)
        self.overlayLabel.setVisible(product is not None)
        if product is None:
            return
        label = "Recorded product" if role is not None else "Product"
        self.overlayLabel.setText(f"{label} · {formatSourceIntervals(product.sourceSegments)}")
        if role is not None:
            binding = product.forwardBinding if role == "forward" else product.reverseBinding
            start, end = binding.sourceSegments[-1]
            terminalBase = end - 1 if binding.strand == 1 else start
            self.canvas.revealInterval(terminalBase, terminalBase + 1)
