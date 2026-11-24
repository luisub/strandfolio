"""Display a recorded PCR run, its gel preview and its products."""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)

from strandfolio.domain.primers import PrimerBinding, PrimerEntry, PrimerRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.primer_files import savePrimerPair
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.gui.views.primer_structure_review import PrimerStructureReview
from strandfolio.gui.views.gel_preview import (
    GelPreview, exportGelFigure, formatPcrSource, formatSourceIntervals,
)
from strandfolio.operations.conversion import complementBases
from strandfolio.operations.gel import GelMarker, GelSelection, GelState, GelViewSettings, pcrLane
from strandfolio.operations.pcr import PcrProduct, PcrRun
from strandfolio.rendering.gels import gelProductStatus


def _textLabel(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _sequenceBox(name: str, parent: QWidget) -> QPlainTextEdit:
    editor = QPlainTextEdit(parent)
    editor.setAccessibleName(name)
    editor.setReadOnly(True)
    editor.setFont(workspaceMonoFont(13))
    return editor


def _primerSummary(name: str, primer: PrimerRevision) -> str:
    return (
        f"{name} 5′→3′   {primer.sequence}\n"
        f"{len(primer.annealingSequence):,} annealing / {len(primer.tailSequence):,} tail nt"
    )


def _siteSummary(binding: PrimerBinding) -> str:
    direction = "→" if binding.strand == 1 else "←"
    return f"{direction} {formatSourceIntervals(binding.sourceSegments)}"


def _duplexText(role: str, binding: PrimerBinding) -> str:
    annealing = binding.annealingSequence
    tail = binding.tailSequence or "—"
    return (
        f"{role} binding {_siteSummary(binding)}\n"
        f"Primer    5′ {annealing} 3′\n"
        f"Template  3′ {complementBases(annealing, 'DNA')} 5′\n"
        f"Unpaired 5′ tail   {tail}"
    )


class PcrResultDialog(QDialog):
    """Keep the gel, sequence preview and product actions synchronized by output key."""

    showGelRequested = Signal()
    showBindingRequested = Signal(object, str)
    usePrimerPairRequested = Signal(object)
    productSelected = Signal(object)
    assemblyRequested = Signal(object, object)

    def __init__(
        self, controller: WorkspaceController, run: PcrRun, view: GelViewSettings,
        parent: QWidget | None = None, *, persistView: bool = True,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.run = run
        usesTemporaryView = persistView and controller.pcr.previewReadFailed(run.operationId)
        self._persistView = persistView and not usesTemporaryView
        self._products = {product.outputKey: product for product in run.products}
        self._lane = pcrLane(run, label="PCR products")
        if view.selectedOutputKey is None and self._products:
            view = replace(view, selectedOutputKey=next(iter(self._products)))
        self._view = view
        self._gelState = GelState()
        self.setObjectName("pcrResultDialog")
        self.setWindowTitle("Predicted PCR products")
        openingSize = QSize(1040, 682)
        if parent is not None and parent.window().isVisible():
            openingSize = openingSize.boundedTo(parent.window().size())
        self.resize(openingSize)
        layout = dialogLayout(self)
        self._addHeader(layout)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._productArea(splitter))
        splitter.addWidget(self._gelArea(splitter))
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.status = _textLabel("", self)
        self.status.setObjectName("muted")
        self.status.hide()
        layout.addWidget(self.status)
        self._addActions(layout)
        if usesTemporaryView:
            self.context.setText(
                f"{self.context.text()}\nTemporary preview · Retry PCR history in History to restore it."
            )
        if self._persistView:
            self.controller.pcr.viewSaveFailed.connect(self._onViewSaveFailed)
        self._renderGel()
        self._renderProduct()
        self.assayReview.startCheck()

    @property
    def selectedOutputKey(self) -> str | None:
        """Return the selected product's key, even when several products share a band."""
        return self._view.selectedOutputKey

    @property
    def selectedProduct(self) -> PcrProduct | None:
        """Return the recorded product matching the selected output key."""
        return self._products.get(self.selectedOutputKey)

    def _addHeader(self, layout: QVBoxLayout) -> None:
        heading = QHBoxLayout()
        title = FittedLabel(self.run.template.name, self)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title.setObjectName("sectionTitle")
        heading.addWidget(title, 1)
        detailsButton = QPushButton("Run details", self)
        detailsButton.setCheckable(True)
        detailsButton.setAutoDefault(False)
        heading.addWidget(detailsButton)
        layout.addLayout(heading)
        self.context = _textLabel(self.controller.pcr.runOutcome(self.run), self)
        if self.controller.pcr.isRunPendingWrite(self.run.operationId):
            self.context.setText(f"{self.context.text()} · Session only, project save pending")
        self.context.setObjectName("muted")
        layout.addWidget(self.context)
        provenance = _textLabel(
            f"Revision {self.run.template.revisionId} · Engine {self.run.engineVersion}\n"
            f"Run {self.run.operationId}", self,
        )
        provenance.setObjectName("muted")
        provenance.hide()
        layout.addWidget(provenance)
        detailsButton.toggled.connect(provenance.setVisible)

    def _productArea(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 4, 4, 0)
        if self._products:
            heading = QLabel("Products", panel)
            heading.setObjectName("sectionTitle")
            layout.addWidget(heading)
        self.productList = ChoiceRows("PCR products by output key", panel, pageSize=7)
        self.productList.setMaximumHeight(min(161, 30 * len(self._products) + 4))
        self.productList.setVisible(bool(self._products))
        self.productList.activated.connect(self._changeSelection)
        layout.addWidget(self.productList, 1)
        self.productLabel = _textLabel("", panel)
        layout.addWidget(self.productLabel)
        self.assayRemark = _textLabel("Checking primers…", panel)
        self.assayRemark.setAccessibleName("PCR assay remarks")
        layout.addWidget(self.assayRemark)
        tabs = QTabWidget(panel)
        tabs.setObjectName("pcrEvidenceTabs")
        tabs.setStyleSheet("""
            QTabWidget#pcrEvidenceTabs::pane { border: 1px solid #dfd9eb; border-radius: 5px; }
            QTabWidget#pcrEvidenceTabs QPlainTextEdit { border: none; padding: 5px; }
        """)
        tabs.setAccessibleName("PCR sequence and evidence")
        self.sequencePreview = _sequenceBox("Selected PCR product sequence 5′ to 3′", tabs)
        self.productEvidence = _sequenceBox("Selected PCR product source and primer binding", tabs)
        self.bindingInventory = _sequenceBox("Exact binding sites and run limits", tabs)
        self.bindingInventory.setPlainText(self._bindingInventoryText())
        if self._products:
            tabs.addTab(self.sequencePreview, "Product sequence")
            tabs.addTab(self.productEvidence, "Product evidence")
        else:
            self.sequencePreview.hide()
            self.productEvidence.hide()
        tabs.addTab(self.bindingInventory, "Binding sites")
        self._templateRemarks = self._searchRemarks()
        runEvidence = "\n".join((
            f"PCR run {self.run.operationId}", f"Template revision {self.run.template.revisionId}",
            f"Forward revision {self.run.forwardPrimer.revisionId} · Reverse revision {self.run.reversePrimer.revisionId}",
            self.controller.pcr.runOutcome(self.run), *self._templateRemarks,
        ))
        self.assayReview = PrimerStructureReview(tabs)
        self.assayReview.setInputs(self.run.operationId, (
            PrimerEntry.fromRevision("Forward", self.run.forwardPrimer),
            PrimerEntry.fromRevision("Reverse", self.run.reversePrimer),
        ), runEvidence=runEvidence)
        self.assayReview.findingsReady.connect(self._showAssayRemark)
        tabs.addTab(self.assayReview, "Assay checks")
        layout.addWidget(tabs, 2)
        return panel

    def _searchRemarks(self) -> list[str]:
        if self.run.status != "completed":
            return ["Template sites and products unassessed"]
        remarks = []
        for role, primer in (("Forward", self.run.forwardPrimer), ("Reverse", self.run.reversePrimer)):
            count = sum(site.primerRevisionId == primer.revisionId for site in self.run.bindings)
            if count != 1:
                remarks.append(f"{role} · {count:,} exact sites")
        return remarks

    def _showAssayRemark(self, results: tuple, error: str) -> None:
        predicted = [result.name.lower() for result in results if result.predicted is True]
        unavailable = sum(result.predicted is None for result in results)
        remarks = list(self._templateRemarks)
        if error:
            remarks.append("Primer structures unassessed")
        else:
            if predicted:
                remarks.append(f"Possible {', '.join(predicted)}")
            elif not unavailable:
                remarks.append("No structures predicted")
            if unavailable:
                remarks.append(f"{unavailable} checks unassessed")
        self.assayRemark.setText(" · ".join(remarks))

    def _gelArea(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        panel.setMinimumWidth(355)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(7, 4, 0, 0)
        heading = QLabel("Modeled bands", panel)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.preview = GelPreview(self.controller.analysis, panel, minimumPlotHeight=120)
        self.controller.analysis.ladderCatalogueChanged.connect(self._renderGel)
        self.preview.markerChanged.connect(self._changeMarker)
        self.preview.bandActivated.connect(self._activateBand)
        layout.addWidget(self.preview, 1)
        self.bandSummary = _textLabel("", panel)
        self.bandSummary.setObjectName("muted")
        layout.addWidget(self.bandSummary)
        return panel

    def _addActions(self, layout: QVBoxLayout) -> None:
        inspection = QHBoxLayout()
        self.forwardSiteButton = QPushButton("Show forward binding", self)
        self.reverseSiteButton = QPushButton("Show reverse binding", self)
        self.exportButton = QPushButton("Export figure…", self)
        for button, callback in (
            (self.forwardSiteButton, lambda: self._showBinding("forward")),
            (self.reverseSiteButton, lambda: self._showBinding("reverse")),
            (self.exportButton, self._export),
        ):
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            inspection.addWidget(button)
        self.savePairButton = QPushButton("Save primer pair…", self)
        self.savePairButton.setAutoDefault(False)
        self.savePairButton.clicked.connect(lambda: savePrimerPair(
            self.controller, self, recorded=(self.run.forwardPrimer, self.run.reversePrimer),
            sourcePath=self.run.template.sourcePath,
        ))
        inspection.addWidget(self.savePairButton)
        self.usePairButton = QPushButton("Use pair in editor", self)
        self.usePairButton.setAutoDefault(False)
        self.usePairButton.clicked.connect(lambda: self.usePrimerPairRequested.emit(self.run))
        inspection.addWidget(self.usePairButton)
        inspection.addStretch(1)
        layout.addLayout(inspection)

        actions = QHBoxLayout()
        close = QPushButton("Close", self)
        self.copyButton = QPushButton("Copy sequence", self)
        self.assemblyButton = QPushButton("Prepare for Assembly", self)
        self.pinButton = QPushButton("Add product to Gel", self)
        self.openProductButton = QPushButton("Open product", self)
        self.openProductButton.setObjectName("primary")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        actions.addStretch(1)
        for button, callback in (
            (self.copyButton, self._copy), (self.assemblyButton, self._prepareAssembly),
            (self.pinButton, self._pin), (self.openProductButton, self._openProduct),
        ):
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            actions.addWidget(button)
        addDialogFooter(layout, actions, self.openProductButton)

    def _renderGel(self) -> None:
        product = self.selectedProduct
        selection = (
            GelSelection(self._lane.laneId, product.revision.outputReference)
            if product is not None else None
        )
        marker = self.controller.analysis.resolveGelMarker(self._view.marker)
        self._gelState = GelState(marker, self._view.settings, (self._lane,), selection)
        self.preview.setState(self._gelState)
        self._renderRows()

    def _renderRows(self) -> None:
        lane = self.preview.gelLayout.lanes[0]
        statuses = {
            placement.product.outputReference.outputKey: gelProductStatus(placement)
            for placement in lane.placements
        }
        rows = []
        for index, product in enumerate(self._products.values(), 1):
            status = statuses[product.outputKey]
            text = f"Product {index} · {product.sizeBp:,} bp · {formatPcrSource(product)}"
            if status:
                text += f" · {status}"
            rows.append(ChoiceRow(product.outputKey, text, f"{text} · Output {product.outputKey}"))
        self.productList.setRows(tuple(rows))
        self.productList.setSelected(self.selectedOutputKey)
        message = [statuses.get(self.selectedOutputKey, "")]
        for band in lane.bands:
            if len(band.products) > 1 and any(
                contributor.outputReference.outputKey == self.selectedOutputKey
                for contributor in band.products
            ):
                message.append(f"{len(band.products)} products in this band")
                break
        summary = "\n".join(filter(None, message))
        self.bandSummary.setText(summary)
        self.bandSummary.setVisible(bool(summary))

    def _bindingInventoryText(self) -> str:
        run = self.run
        settings = run.settings
        sites = {run.forwardPrimer.revisionId: [], run.reversePrimer.revisionId: []}
        for binding in run.bindings:
            sites[binding.primerRevisionId].append(_siteSummary(binding))
        lines = [
            f"Template limit {settings.maxTemplateBp:,} bp · Binding limit {settings.maxBindings:,}",
            f"Pairing limit {settings.maxPairings:,} · Product limit {settings.maxProducts:,}",
        ]
        for name, primer in (("Forward", run.forwardPrimer), ("Reverse", run.reversePrimer)):
            lines.extend(("", _primerSummary(name, primer), f"Primer revision {primer.revisionId}"))
            noSites = "No exact sites" if run.status == "completed" else "Site evidence unavailable"
            lines.extend(sites[primer.revisionId] or (noSites,))
        if run.target:
            description = f"Selected interval (context) · {formatSourceIntervals(run.target.segments)}"
            if run.target.featureId:
                feature = next((item for item in run.template.features
                                if item.featureId == run.target.featureId), None)
                if feature:
                    description += f" · {feature.label}"
            direction = {1: "forward strand", -1: "reverse strand"}.get(run.target.strand)
            if direction:
                description += f" · {direction}"
            lines.extend(("", description))
        else:
            lines.extend(("", "No target selected for this run."))
        lines.extend(("", *run.assumptions))
        if run.warnings:
            lines.extend(("", *run.warnings))
        return "\n".join(lines)

    def _renderProduct(self) -> None:
        product = self.selectedProduct
        for button in (
            self.openProductButton, self.copyButton, self.assemblyButton, self.pinButton,
            self.forwardSiteButton, self.reverseSiteButton,
        ):
            button.setEnabled(product is not None)
        self.productLabel.setVisible(product is not None and self.run.target is not None)
        if product is None:
            return
        if self.run.target:
            covered, total = product.targetCoverage(self.run.target)
            relation = "Full" if covered == total else "Partial" if covered else "No"
            self.productLabel.setText(f"{relation} target coverage ({covered:,}/{total:,} bp)")
        self.sequencePreview.setPlainText(product.revision.sequence)
        lines = [
            f"Output {product.outputKey}",
            formatPcrSource(product),
            _duplexText("Forward", product.forwardBinding),
            _duplexText("Reverse", product.reverseBinding),
            self._sourceMappingText(product),
            f"Inherited annotations · {len(product.featureMappings):,}",
        ]
        lines.extend(product.warnings)
        self.productEvidence.setPlainText("\n\n".join(lines))

    def _sourceMappingText(self, product: PcrProduct) -> str:
        names = {"template": "Template", "forwardPrimer": "Forward oligo", "reversePrimer": "Reverse oligo"}
        lines = ["Source contributions"]
        for mapping in product.sourceMappings:
            source = formatSourceIntervals((mapping.sourceSpan,))
            destination = formatSourceIntervals((mapping.productSpan,))
            orientation = "forward" if mapping.orientation == 1 else "reverse complement"
            lines.append(f"{names[mapping.role]} {source} → Product {destination} · {orientation}")
        return "\n".join(lines)

    def _applyView(self, view: GelViewSettings) -> None:
        if not self._persistView or self.controller.pcr.updateRunView(self.run.operationId, view):
            self._view = view
            self.status.hide()
        else:
            self._showStatus("Could not save the PCR preview settings.")
        self._renderGel()

    def _changeSelection(self, outputKey: str) -> None:
        if outputKey != self.selectedOutputKey:
            self._applyView(replace(self._view, selectedOutputKey=outputKey))
            self._renderProduct()
            self.productSelected.emit(self.selectedProduct)

    def _changeMarker(self, marker: GelMarker) -> None:
        self._applyView(replace(self._view, marker=marker))

    def _activateBand(self, selection: GelSelection) -> None:
        if selection.outputReference is not None:
            self._changeSelection(selection.outputReference.outputKey)

    def _showBinding(self, role: str) -> None:
        product = self.selectedProduct
        active = self.controller.session.current
        if product is None:
            return
        if active is None or active.revisionId != product.templateRevisionId:
            if not self.controller.pcr.openSource(product.templateRevisionId):
                return
        self.showBindingRequested.emit(product, role)
        self.accept()

    def _showStatus(self, text: str) -> None:
        self.status.setText(text)
        self.status.show()

    def _onViewSaveFailed(self, operationId: str, message: str) -> None:
        if operationId == self.run.operationId:
            self._showStatus(f"Could not save the PCR preview settings to the project. {message}")

    def _copy(self) -> None:
        product = self.selectedProduct
        if product is not None and self.controller.pcr.copyProduct(self.run.operationId, product.outputKey):
            self._showStatus(f"Copied {product.sizeBp:,} bp from the selected product 5′→3′.")

    def _openProduct(self) -> None:
        key = self.selectedOutputKey
        if key is not None and self.controller.pcr.adoptProduct(self.run.operationId, key):
            self.accept()

    def _prepareAssembly(self) -> None:
        self.assemblyRequested.emit(self.selectedOutputKey, self.selectedProduct)

    def _pin(self) -> None:
        if self.selectedOutputKey is not None and self.controller.analysis.pinPcrProduct(self.run, self._view):
            self.showGelRequested.emit()
            self.accept()

    def _export(self) -> None:
        exportGelFigure(
            self, self.controller, self._gelState,
            title=f"{self.run.template.name} · PCR {self.run.operationId}",
        )
