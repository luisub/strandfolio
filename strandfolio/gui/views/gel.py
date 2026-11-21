"""Compare pinned DNA samples and reopen their exact revisions or runs."""

from html import escape

from PySide6.QtCore import QSignalBlocker, Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.gui.panels.document_strip import documentLabels
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.panels.pending_saves import PendingSaveButton, SaveNotice
from strandfolio.gui.views.gel_preview import (
    GelPreview,
    exportGelFigure,
    formatPcrSource,
    formatSourceSpans,
)
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.operations.gel import GelLane, GelSelection, GelState
from strandfolio.operations.pcr import PcrProduct, PcrRun
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.rendering.gels import gelProductStatus


class _LanePicker(QDialog):
    """Review a replacement or addition without changing the working lane."""

    def __init__(self, controller: WorkspaceController, lane: GelLane | None, parent: QWidget) -> None:
        super().__init__(parent)
        title = "Add lane" if lane is None else "Replace lane"
        self.setWindowTitle(title)
        self.resize(505, 340)
        layout = dialogLayout(self)
        if lane is not None:
            source = FittedLabel(f"Pinned source · {lane.source.name}", self)
            source.setObjectName("sectionTitle")
            layout.addWidget(source)
            treatment = FittedLabel(f"Current treatment · {lane.treatment}", self)
            treatment.setObjectName("muted")
            layout.addWidget(treatment)
        form = QFormLayout()
        self.kind = QComboBox(self)
        self.kind.setAccessibleName("Lane sample type")
        self.kind.addItem("Sequence or construct", "sequence")
        self.kind.addItem("Completed digest", "digest")
        self.kind.addItem("PCR product", "pcr")
        form.addRow("Sample type", self.kind)
        self.samples = QComboBox(self)
        self.samples.setAccessibleName("Lane sample")
        self.samples.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        if lane is not None:
            self.samples.setPlaceholderText("Choose a sample…")
        form.addRow("Sample", self.samples)
        layout.addLayout(form)
        self.context = QLabel(self)
        self.context.setObjectName("muted")
        self.context.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.context.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context.setTextFormat(Qt.TextFormat.PlainText)
        self.context.setWordWrap(True)
        self._contextArea = QScrollArea(self)
        self._contextArea.setAccessibleName("Sample details")
        self._contextArea.setWidgetResizable(True)
        self._contextArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._contextArea.setWidget(self.context)
        layout.addWidget(self._contextArea)
        self.treatmentButton = QToolButton(self)
        self.treatmentButton.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.treatmentButton.setCheckable(True)
        self.treatmentButton.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(self.treatmentButton, 0, Qt.AlignmentFlag.AlignLeft)
        self.enzymeFilter = QLineEdit(self)
        self.enzymeFilter.setPlaceholderText("Find a treatment enzyme…")
        self.enzymeFilter.setAccessibleName("Find a treatment enzyme")
        layout.addWidget(self.enzymeFilter)
        self.enzymes = ChoiceRows("Lane treatment enzymes", self, multiple=True)
        self.enzymes.setMinimumHeight(105)
        self.enzymeFilter.textChanged.connect(self.enzymes.setFilter)
        layout.addWidget(self.enzymes, 1)
        self.treatmentButton.toggled.connect(self._showTreatment)
        self.enzymes.activated.connect(self._updateTreatmentSummary)
        self.enzymes.activated.connect(self._detail)
        self._updateTreatmentSummary()
        nameForm = QFormLayout()
        self.laneName = QLineEdit(self)
        self.laneName.setAccessibleName("Lane name")
        self.laneName.setPlaceholderText("Optional")
        if lane is not None:
            self.laneName.setText(lane.label)
        nameForm.addRow("Lane name", self.laneName)
        layout.addLayout(nameForm)
        layout.addStretch(0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.confirmButton = buttons.addButton(
            "Save lane" if lane is not None else "Add lane", QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        addDialogFooter(layout, buttons, self.confirmButton)
        self._controller = controller
        self._pinnedLane = lane
        self._workspace = workspace = controller.workspace
        self._projectContext = workspace.taskContext(False)
        self._sequences = tuple(
            (label, workspace.getDocument(label.documentId).revision)
            for label in documentLabels(workspace)
            if workspace.getDocument(label.documentId).revision.moleculeType == "DNA"
        )
        self._digestions = controller.analysis.availableDigestions()
        self._pcrProducts = self._availablePcrProducts()
        self._selectedPcrReference = (
            lane.products[0].outputReference if lane is not None and isinstance(lane.run, PcrRun)
            else None
        )
        self._enzymeRows = tuple(
            ChoiceRow(enzyme.name, f"{enzyme.name} · {enzyme.recognitionSite}")
            for enzyme in controller.analysis.availableEnzymes
        )
        self.enzymes.setRows(self._enzymeRows)
        if lane is not None:
            kind = "sequence"
            if isinstance(lane.run, RestrictionDigest):
                kind = "digest"
            elif isinstance(lane.run, PcrRun):
                kind = "pcr"
            self.kind.setCurrentIndex(self.kind.findData(kind))
        elif not self._sequences:
            if self._digestions:
                self.kind.setCurrentIndex(self.kind.findData("digest"))
            elif self._pcrProducts or controller.pcr.loadingAvailableRuns:
                self.kind.setCurrentIndex(self.kind.findData("pcr"))
        self.kind.currentIndexChanged.connect(self._refresh)
        self.samples.currentIndexChanged.connect(self._detail)
        controller.pcr.historyChanged.connect(self._refreshPcrProducts)
        self._refresh()
        if lane is None and self.kind.currentData() == "sequence":
            index = self.samples.findData(workspace.activeDocumentId)
            if index >= 0:
                self.samples.setCurrentIndex(index)

    def _availablePcrProducts(self) -> tuple[tuple[PcrRun, PcrProduct], ...]:
        return tuple(
            (run, product) for run in self._controller.pcr.availableRuns() for product in run.products
        )

    @Slot()
    def _refreshPcrProducts(self) -> None:
        if not self._inCurrentProject():
            self.reject()
            return
        products = self._availablePcrProducts()
        changed = products != self._pcrProducts
        self._pcrProducts = products
        if self.kind.currentData() == "pcr":
            if changed:
                self._refresh()
            else:
                self._detail()

    def _inCurrentProject(self) -> bool:
        return (self._controller.workspace is self._workspace
                and self._workspace.taskContext(False) == self._projectContext)

    def _refresh(self) -> None:
        kind = self.kind.currentData()
        with QSignalBlocker(self.samples):
            self.samples.clear()
            if kind == "digest":
                for run in self._digestions:
                    names = " + ".join(enzyme.name for enzyme in run.enzymes)
                    self.samples.addItem(f"{run.template.name} · {names} · {run.operationId[:8]}",
                                         run.operationId)
            elif kind == "pcr":
                for run, product in self._pcrProducts:
                    self.samples.addItem(
                        f"{run.template.name} · {product.outputKey} · {product.sizeBp:,} bp"
                        f" · {run.operationId[:8]}",
                        product.revision.outputReference,
                    )
            else:
                for label, _ in self._sequences:
                    self.samples.addItem(label.qualifiedName, label.documentId)
            self.samples.setCurrentIndex(self._sampleIndex(kind))
        showTreatment = bool(kind == "sequence" and self._sequences and self._enzymeRows)
        if showTreatment:
            self._contextArea.setFixedHeight(2 * self.context.fontMetrics().lineSpacing() + 6)
        else:
            self._contextArea.setMinimumHeight(64)
            self._contextArea.setMaximumHeight(175)
        self.treatmentButton.setVisible(showTreatment)
        self._showTreatment()
        self._detail()

    def _sampleIndex(self, kind: str) -> int:
        lane = self._pinnedLane
        if kind == "pcr" and self._selectedPcrReference is not None:
            return next((i for i, (_, product) in enumerate(self._pcrProducts)
                         if product.revision.outputReference == self._selectedPcrReference), -1)
        if lane is None:
            return self.samples.currentIndex()
        if kind == "digest" and isinstance(lane.run, RestrictionDigest):
            return self.samples.findData(lane.run.operationId)
        if kind == "sequence" and lane.run is None:
            return next((i for i, (_, revision) in enumerate(self._sequences)
                         if revision.revisionId == lane.source.revisionId), -1)
        return -1

    def _updateTreatmentSummary(self) -> None:
        selected = sorted(self.enzymes.selectedKeys)
        summary = f"{len(selected)} selected" if selected else "optional"
        self.treatmentButton.setText(f"Restriction enzymes ({summary})")
        self.treatmentButton.setToolTip(escape(", ".join(selected)) if selected
                                        else "Leave empty for untreated DNA.")

    def _showTreatment(self) -> None:
        expanded = self.treatmentButton.isChecked() and not self.treatmentButton.isHidden()
        self.treatmentButton.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.enzymeFilter.setVisible(expanded)
        self.enzymes.setVisible(expanded)
        if self.isVisible():
            self.layout().activate()
            self.resize(self.width(), max(530, self.minimumSizeHint().height()) if expanded
                        else self.sizeHint().height())

    def _detail(self) -> None:
        index = self.samples.currentIndex()
        self.samples.setToolTip(escape(self.samples.currentText()))
        kind = self.kind.currentData()
        self.confirmButton.setEnabled(index >= 0)
        if kind == "pcr":
            self._describePcr(index)
        elif index < 0:
            if self.samples.count():
                self.context.setText("Choose a sample to replace the pinned lane.")
            else:
                self.context.setText({
                    "sequence": "Open a DNA sequence first.",
                    "digest": "No completed digestions are available.",
                }[kind])
        elif kind == "digest":
            run = self._digestions[index]
            sizes = ", ".join(f"{fragment.forwardLength:,}" for fragment in run.fragments)
            self.context.setText(f"Run {run.operationId}\nNominal sizes · {sizes} bp")
        else:
            label, revision = self._sequences[index]
            canPin = not label.isDraft or bool(self.enzymes.selectedKeys)
            self.confirmButton.setEnabled(canPin and revision.topology is not None)
            self.context.setText(
                f"Source revision {revision.revisionId}\n"
                f"{len(revision):,} aligned bases · {revision.topology or 'unknown topology'}"
                + (" · Draft" if label.isDraft else ""),
            )

    def _describePcr(self, index: int) -> None:
        loading = self._controller.pcr.loadingAvailableRuns
        if index >= 0:
            run, product = self._pcrProducts[index]
            self._selectedPcrReference = product.revision.outputReference
            text = (
                f"Run {run.operationId}\nSource revision {run.template.revisionId}\n"
                f"{product.sizeBp:,} bp · {product.outputKey} · {formatPcrSource(product)}"
            )
            if loading:
                text += "\nLoading saved PCR products…"
        elif loading:
            text = "Loading saved PCR products…"
        elif self._pcrProducts:
            text = "Choose a PCR product."
        elif self._controller.pcr.historyReadError:
            text = "The saved PCR list is incomplete. Choose Retry PCR history in History."
        else:
            text = "No completed PCR products are available."
        self.context.setText(text)

    def apply(self, laneId: str | None) -> None:
        """Pin the accepted sample, or stage a digestion for the named lane."""
        target = self.samples.currentData()
        if target is None:
            return
        controller = self._controller
        if not self._inCurrentProject():
            controller.operationFailed.emit("The workspace changed during lane review. Choose it again.")
            return
        analysis = controller.analysis
        label = self.laneName.text().strip() or None
        kind = self.kind.currentData()
        if kind == "digest":
            analysis.pinRecordedDigestion(target, laneId=laneId, label=label)
        elif kind == "pcr":
            analysis.pinRecordedPcrProduct(target.operationId, target.outputKey, laneId, label=label)
        else:
            _, reviewed = self._sequences[self.samples.currentIndex()]
            try:
                current = self._workspace.getDocument(target).revision
            except KeyError:
                current = None
            if current is not reviewed:
                controller.operationFailed.emit("The sample changed during lane review. Choose it again.")
                return
            selected = self.enzymes.selectedKeys
            names = tuple(row.key for row in self._enzymeRows if row.key in selected)
            if names:
                analysis.prepareDigestLane(target, names, laneId=laneId, label=label)
            else:
                analysis.pinUntreated(target, laneId=laneId, label=label)


class GelView(QWidget):
    """Keep the comparison visible independently of the open sequence documents."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._listedState: GelState | None = None
        self.setObjectName("gelWorkspace")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.workflowTools = QWidget(self)
        self.workflowTools.setObjectName("workflowTools")
        tools = QHBoxLayout(self.workflowTools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.addStretch(1)
        self.addLaneButton = QPushButton("Add lane…", self)
        self.addLaneButton.setIcon(workspaceIcon("plus", onAccent=True))
        self.addLaneButton.setObjectName("primary")
        self.addLaneButton.clicked.connect(lambda: self._pickLane(None))
        self.exportButton = QPushButton("Export figure…", self)
        self.exportButton.setIcon(workspaceIcon("export"))
        self.exportButton.clicked.connect(self._export)
        self.retryButton = PendingSaveButton("Retry gel sample save", self, chooseSource=True)
        self.retryButton.retryRequested.connect(
            lambda run: controller.analysis.retryDigestWrite(
                run.operationId, scope="gel", report=self.saveNotice.showMessage,
            )
        )
        for button in (self.retryButton, self.exportButton, self.addLaneButton):
            tools.addWidget(button)
        layout.addWidget(self.workflowTools)
        self.saveNotice = SaveNotice(self)
        layout.addWidget(self.saveNotice)
        controller.projectChanged.connect(self.saveNotice.showMessage)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("analysisPanels")
        splitter.setHandleWidth(12)
        splitter.setChildrenCollapsible(False)
        canvas = QWidget(splitter)
        canvas.setObjectName("workflowCanvas")
        canvasLayout = QVBoxLayout(canvas)
        canvasLayout.setContentsMargins(14, 12, 14, 12)
        heading = QLabel("Predicted gel", canvas)
        heading.setObjectName("analysisPanelHeading")
        canvasLayout.addWidget(heading)
        plotArea = QWidget(canvas)
        plotArea.setMaximumHeight(410)
        self.preview = GelPreview(controller.analysis, plotArea, compareLanes=True)
        self.preview.markerChanged.connect(controller.analysis.setGelMarker)
        self.preview.bandActivated.connect(controller.analysis.setGelSelection)
        canvasLayout.addSpacing(12)
        plot = QHBoxLayout(plotArea)
        plot.setContentsMargins(0, 0, 0, 0)
        plot.addStretch()
        plot.addWidget(self.preview, 1)
        plot.addStretch()
        canvasLayout.addWidget(plotArea, 1)
        canvasLayout.addStretch()
        splitter.addWidget(canvas)
        splitter.addWidget(self._createLaneInspector(splitter))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1035, 365])
        layout.addWidget(splitter, 1)
        controller.analysis.gelChanged.connect(self.refresh)
        controller.analysis.ladderCatalogueChanged.connect(self.refresh)
        controller.analysis.retryChanged.connect(self._updateRetry)
        self._updateRetry()
        self.refresh()

    def _createLaneInspector(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setObjectName("analysisInspector")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(385)
        inspector = QWidget(scroll)
        inspector.setObjectName("workflowInspector")
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(14, 12, 14, 12)
        heading = QLabel("Lane contents", inspector)
        heading.setObjectName("analysisPanelHeading")
        layout.addWidget(heading)
        self.notice = QLabel(inspector)
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.laneList = ChoiceRows("Pinned lanes and individual gel products", inspector)
        self.laneList.setStyleSheet("""
            QPushButton#featureName { text-align: left; padding: 5px 8px; border: 0;
                border-bottom: 1px solid #e5dfed; border-left: 3px solid transparent; }
            QPushButton#featureName:checked { color: #492393; background: #eee8fa;
                border-left-color: #6944ae; }
            QPushButton#featureName:focus { border: 1px solid #6944ae; }
        """)
        self.laneList.activated.connect(self.controller.analysis.setGelSelection)
        layout.addWidget(self.laneList, 1)
        self.detailsButton = QToolButton(inspector)
        self.detailsButton.setText("Source details")
        self.detailsButton.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.detailsButton.setArrowType(Qt.ArrowType.RightArrow)
        self.detailsButton.setCheckable(True)
        self.detailsButton.toggled.connect(lambda opened: self.detailsButton.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        layout.addWidget(self.detailsButton, 0, Qt.AlignmentFlag.AlignLeft)
        self.laneDetails = QLabel(inspector)
        self.laneDetails.setTextFormat(Qt.TextFormat.PlainText)
        self.laneDetails.setWordWrap(True)
        self.laneDetails.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detailsButton.toggled.connect(self.laneDetails.setVisible)
        layout.addWidget(self.laneDetails)

        self.selectedLaneLabel = FittedLabel("", inspector)
        self.selectedLaneLabel.setObjectName("sectionTitle")
        layout.addWidget(self.selectedLaneLabel)
        sources = QHBoxLayout()
        sources.setSpacing(4)
        self.openSourceButton = QPushButton("Open source", inspector)
        self.openSourceButton.setIcon(workspaceIcon("open"))
        self.openSourceButton.setAccessibleName("Open source snapshot")
        self.openDigestButton = QPushButton("Open digestion", inspector)
        self.openDigestButton.setAccessibleName("Open recorded digestion")
        self.openPcrButton = QPushButton("Open PCR", inspector)
        self.openPcrButton.setAccessibleName("Open recorded PCR")
        self.openPcrProductButton = QPushButton("Open product", inspector)
        self.editLaneButton = QPushButton("Replace…", inspector)
        self.removeLaneButton = QPushButton("Remove", inspector)
        self.duplicateLaneButton = QPushButton("Duplicate", inspector)
        self.duplicateLaneButton.setIcon(workspaceIcon("copy"))
        self.earlierLaneButton = QPushButton("Move earlier", inspector)
        self.laterLaneButton = QPushButton("Move later", inspector)
        self.cancelPreparationButton = QPushButton("Cancel preparation", inspector)
        self.undoRemovalButton = QPushButton("Undo removal", inspector)
        for button, callback in (
            (self.openSourceButton, self._openSource),
            (self.openDigestButton, self._openDigest),
            (self.openPcrButton, self._openPcr),
            (self.openPcrProductButton, self._openPcrProduct),
        ):
            button.clicked.connect(callback)
            sources.addWidget(button)
        layout.addLayout(sources)
        actions = QGridLayout()
        for index, (button, callback) in enumerate((
            (self.editLaneButton, lambda: self._pickLane(self._selectedLane())),
            (self.duplicateLaneButton, self._duplicate),
            (self.earlierLaneButton, lambda: self._move(-1)),
            (self.laterLaneButton, lambda: self._move(1)),
            (self.cancelPreparationButton, self._cancelPreparation),
            (self.undoRemovalButton, self.controller.analysis.undoLaneRemoval),
        )):
            button.clicked.connect(callback)
            actions.addWidget(button, index // 2, index % 2)
        layout.addLayout(actions)
        self.removeLaneButton.clicked.connect(self._remove)
        layout.addWidget(self.removeLaneButton)
        layout.addStretch(1)
        scroll.setWidget(inspector)
        return scroll

    def refresh(self) -> None:
        """Apply current marker, samples, selection and asynchronous replacement status."""
        analysis = self.controller.analysis
        state = analysis.gelState
        self.preview.setState(state)
        self.preview.setMaximumWidth(max(505, 70 * (len(state.lanes) + 1) + 65))
        previous = self._listedState
        if previous is None or (previous.marker, previous.settings, previous.lanes) != (
            state.marker, state.settings, state.lanes,
        ):
            self._listLanes()
        self.laneList.setSelected(
            state.selection, reveal=previous is None or previous.selection != state.selection,
        )
        self._listedState = state
        selected = self._selectedLane()
        pending = analysis.preparingGelLaneIds
        self.laneList.setVisible(bool(state.lanes))
        if pending:
            notice = "Preparing a digest. Committed lanes remain available."
        elif not state.lanes:
            notice = "Pin a digest, PCR product or DNA sequence to compare sizes."
        else:
            notice = ""
        self.notice.setText(notice)
        self.notice.setVisible(bool(notice))
        self._updateActions(state, selected)
        self._describeLane(state, selected)

    def _updateRetry(self) -> None:
        self.retryButton.setRuns(self.controller.analysis.pendingSaveRuns("gel"))

    def _listLanes(self) -> None:
        rows = []
        for index, laneLayout in enumerate(self.preview.gelLayout.lanes, 1):
            lane = laneLayout.lane
            label = f"Lane {index} · {lane.label}\n{lane.treatment}"
            if lane.run is None:
                placement, = laneLayout.placements
                label += f" · {placement.product.lengthBp:,} bp"
                if status := gelProductStatus(placement):
                    label += f" · {status}"
            rows.append(ChoiceRow(GelSelection(lane.laneId), label))
            for number, placement in enumerate(laneLayout.placements, 1):
                product = placement.product
                if product.outputReference is None:
                    continue
                if product.pcrProduct is not None:
                    text = f"{product.outputReference.outputKey} · {product.lengthBp:,} bp"
                else:
                    text = f"Fragment {number} · {product.lengthBp:,} nominal bp"
                if status := gelProductStatus(placement):
                    text += f" · {status}"
                reference = GelSelection(lane.laneId, product.outputReference)
                rows.append(ChoiceRow(reference, f"    {text}", f"{text} · Lane {index}"))
        self.laneList.setRows(tuple(rows))
        self.laneList.setMaximumHeight(min(415, 12 + len(rows) * 51))

    def _updateActions(self, state: GelState, lane: GelLane | None) -> None:
        analysis = self.controller.analysis
        isPcr = lane is not None and isinstance(lane.run, PcrRun)
        self.selectedLaneLabel.setVisible(lane is not None)
        if lane is not None:
            number = state.lanes.index(lane) + 1
            self.selectedLaneLabel.setFullText(f"Selected lane {number} · {lane.label}")
        for button in (
            self.openSourceButton, self.editLaneButton, self.duplicateLaneButton,
            self.removeLaneButton, self.earlierLaneButton, self.laterLaneButton,
        ):
            button.setVisible(lane is not None)
        self.openDigestButton.setVisible(lane is not None and isinstance(lane.run, RestrictionDigest))
        self.openPcrButton.setVisible(isPcr)
        self.openPcrProductButton.setVisible(isPcr)
        self.earlierLaneButton.setEnabled(lane is not None and lane is not state.lanes[0])
        self.laterLaneButton.setEnabled(lane is not None and lane is not state.lanes[-1])
        self.undoRemovalButton.setVisible(analysis.canUndoLaneRemoval)
        pending = analysis.preparingGelLaneIds
        self.cancelPreparationButton.setVisible(
            None in pending or (lane is not None and lane.laneId in pending),
        )

    def _describeLane(self, state: GelState, lane: GelLane | None) -> None:
        self.detailsButton.setVisible(lane is not None)
        self.laneDetails.setVisible(lane is not None and self.detailsButton.isChecked())
        if lane is None:
            return
        source = lane.source
        text = (f"{source.name} · {len(source):,} aligned bases · "
                f"{source.topology or 'unknown topology'}\nRevision {source.revisionId}")
        if lane.run is not None:
            text += f"\n{lane.treatment} · Run {lane.run.operationId}"
        if isinstance(lane.run, PcrRun):
            product = lane.products[0].pcrProduct
            text += f"\n{product.outputKey} · {product.sizeBp:,} bp · {formatPcrSource(product)}"
        elif state.selection.outputReference is not None:
            product = next(product for product in lane.products
                           if product.outputReference == state.selection.outputReference)
            intervals = formatSourceSpans(product.sourceSpans)
            count = sum(other.lengthBp == product.lengthBp and other.topology == product.topology
                        for other in lane.products)
            text += f"\nSource bases {intervals} · Output {product.outputReference.outputKey}"
            if count > 1 and product.topology == "linear":
                text += f"\n{count} fragments share this nominal size."
        self.laneDetails.setText(text)

    def _selectedLane(self) -> GelLane | None:
        state = self.controller.analysis.gelState
        if state.selection is None:
            return None
        return next((lane for lane in state.lanes if lane.laneId == state.selection.laneId), None)

    def _pickLane(self, lane: GelLane | None) -> None:
        dialog = _LanePicker(self.controller, lane, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply(lane.laneId if lane is not None else None)
        dialog.deleteLater()

    def _openSource(self) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.openLaneSource(lane.laneId)

    def _openDigest(self) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.openLaneDigestion(lane.laneId)

    def _openPcr(self) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.openLanePcr(lane.laneId)

    def _openPcrProduct(self) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.openLanePcrProduct(lane.laneId)

    def _remove(self) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.removeLane(lane.laneId)

    def _duplicate(self) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.duplicateLane(lane.laneId)

    def _move(self, offset: int) -> None:
        lane = self._selectedLane()
        if lane is not None:
            self.controller.analysis.moveLane(lane.laneId, offset)

    def _cancelPreparation(self) -> None:
        lane = self._selectedLane()
        laneId = lane.laneId if lane is not None else None
        if laneId not in self.controller.analysis.preparingGelLaneIds:
            laneId = None
        self.controller.analysis.cancelGelPreparation(laneId)

    def _export(self) -> None:
        exportGelFigure(self, self.controller, self.controller.analysis.gelState)
