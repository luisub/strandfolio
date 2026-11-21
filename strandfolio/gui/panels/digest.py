"""Restriction recipe controls for Sequence and the saved digestion dialog."""

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from strandfolio.domain.fragments import DNAFragment, FragmentEnd
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import addDialogFooter, confirmAction, dialogLayout, fileDialogDirectory
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.panels.pending_saves import PendingSaveButton
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.gui.views.digest_source_map import DigestSourceMap
from strandfolio.gui.views.gel_preview import GelPreview, exportGelFigure, formatSourceSpans
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.io.digestion import SavedDigestion
from strandfolio.io.formats import exportDestinationVersion
from strandfolio.operations.gel import GelMarker, GelSelection, GelState, GelViewSettings, digestLane
from strandfolio.operations.restriction import (
    RESTRICTION_ENGINE_VERSION,
    RecognitionSite,
    RestrictionDigest,
    RestrictionScan,
    predictRestrictionCuts,
)
from strandfolio.rendering.gels import gelProductStatus
from strandfolio.storage.catalogues import EnzymeRecord


def _textLabel(text: str, parent: QWidget, *, muted: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if muted:
        label.setObjectName("muted")
    return label


def _spanLabel(site: RecognitionSite) -> str:
    return " → ".join(f"{start + 1:,}–{end:,}" for start, end in site.sourceParts)


def digestOutcomeText(run: RestrictionDigest) -> str:
    """Describe the captured result for both the dialog and background notification."""
    source = run.template.name
    if run.status != "completed":
        return f"Unsupported digestion of {source}. {' '.join(run.warnings)}".rstrip()
    if not run.cuts:
        return f"{source} remains uncut. The intact molecule retains its {run.template.topology} topology."
    count = len(run.fragments)
    unit = "fragment" if count == 1 else "fragments"
    return f"Digestion of {source} produced {count:,} {unit}."


def _endLabel(end: FragmentEnd | None, *, includeChemistry: bool = False, concise: bool = False) -> str:
    if end is None:
        return "No terminus"
    kinds = {"unknown": "Unknown", "blunt": "Blunt",
             "5-prime-overhang": "5′ overhang", "3-prime-overhang": "3′ overhang"}
    text = kinds[end.kind]
    if end.overhangSequence:
        text += f" {end.overhangSequence}"
        if not concise:
            strand = "forward" if end.protrudingStrand == 1 else "reverse"
            text += f" ({strand}, 5′→3′)"
    if includeChemistry:
        text += f" · 5′ {end.fivePrimeChemistry} · 3′ {end.threePrimeChemistry}"
    return text


class _EnzymeOverlayDialog(QDialog):
    """Review the sites to display without changing the digestion recipe."""

    def __init__(
        self, scan: RestrictionScan, chosen: tuple[str, ...], parent: QWidget, *,
        definiteCounts: dict[str, int],
    ) -> None:
        super().__init__(parent)
        self._scan = scan
        self._sites: dict[str, list[RecognitionSite]] = {}
        self._enzymes = {enzyme.name: enzyme for enzyme in scan.enzymes}
        for site in scan.sites:
            self._sites.setdefault(site.enzymeName, []).append(site)
        self.setWindowTitle("Enzymes")
        self.setObjectName("enzymeOverlayDialog")
        self.resize(680, 530)
        subtitle = _textLabel(
            "Choose recognition sites to display in Sequence and on the map.", self, muted=True,
        )
        layout = dialogLayout(self, icon=workspaceIcon("digest"), subtitle=subtitle)
        filters = QHBoxLayout()
        for name, count in (("All", -1), ("Unique cutters (1 site)", 1), ("Two cutters (2 sites)", 2),
                            ("Deselect all", None)):
            button = QPushButton(name, self)
            button.clicked.connect(lambda _checked=False, target=count: self._setFilter(target))
            filters.addWidget(button)
        layout.addLayout(filters)
        self.enzymeList = QTableWidget(len(scan.enzymes), 4, self)
        self.enzymeList.setAccessibleName("Display restriction enzyme sites")
        self.enzymeList.setHorizontalHeaderLabels(("Display enzyme", "Motif", "End type", "Definite sites"))
        self.enzymeList.verticalHeader().hide()
        self.enzymeList.verticalHeader().setDefaultSectionSize(29)
        self.enzymeList.setShowGrid(False)
        self.enzymeList.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.enzymeList.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.enzymeList.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.enzymeList.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.enzymeList.setStyleSheet("""
            QTableWidget { background: white; border: 1px solid #e2dcef; border-radius: 5px;
                           selection-background-color: #eee8fc; selection-color: #382063; }
            QHeaderView::section { background: #f4f1f8; color: #52475f; border: none;
                                  border-bottom: 1px solid #ded6ea; padding: 5px; font-weight: 600; }
            QTableWidget::item { border-bottom: 1px solid #efebf4; padding: 3px; }
        """)
        self._counts = {enzyme.name: definiteCounts.get(enzyme.name) for enzyme in scan.enzymes}
        for row, enzyme in enumerate(scan.enzymes):
            count = self._counts[enzyme.name]
            name = QTableWidgetItem(enzyme.name)
            name.setCheckState(Qt.CheckState.Checked if enzyme.name in chosen else Qt.CheckState.Unchecked)
            motif = QTableWidgetItem(enzyme.recognitionSite)
            motif.setFont(workspaceMonoFont(12))
            sites = QTableWidgetItem(f"{count:,}" if count is not None else "Unavailable")
            for column, item in enumerate((name, motif, QTableWidgetItem(enzyme.overhangType), sites)):
                self.enzymeList.setItem(row, column, item)
        layout.addWidget(self.enzymeList, 1)
        inspectPanel = QFrame(self)
        inspectPanel.setObjectName("card")
        details = QVBoxLayout(inspectPanel)
        details.setContentsMargins(10, 8, 10, 8)
        inspection = QHBoxLayout()
        enzymeLabel = QLabel("&Inspect enzyme", self)
        self.inspectSelector = QComboBox(self)
        self.inspectSelector.setAccessibleName("Inspect restriction enzyme")
        for name in self._enzymes:
            self.inspectSelector.addItem(name, name)
        enzymeLabel.setBuddy(self.inspectSelector)
        inspection.addWidget(enzymeLabel)
        inspection.addWidget(self.inspectSelector)
        siteLabel = QLabel("&Occurrence", self)
        self.siteSelector = QComboBox(self)
        self.siteSelector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.siteSelector.setAccessibleName("Recognition occurrence")
        siteLabel.setBuddy(self.siteSelector)
        inspection.addWidget(siteLabel)
        inspection.addWidget(self.siteSelector, 1)
        details.addLayout(inspection)
        self.siteDetails = _textLabel("", self, muted=True)
        details.addWidget(self.siteDetails)
        layout.addWidget(inspectPanel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.applyButton = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        actions = QHBoxLayout()
        self._selectionCount = _textLabel("", self, muted=True)
        actions.addWidget(self._selectionCount, 1)
        actions.addWidget(buttons)
        addDialogFooter(layout, actions, self.applyButton)
        self._updateSelectionCount()
        self.enzymeList.itemChanged.connect(self._updateSelectionCount)
        self.enzymeList.currentCellChanged.connect(
            lambda row, *_: self.inspectSelector.setCurrentIndex(row)
        )
        self.inspectSelector.currentIndexChanged.connect(self._showOccurrences)
        self.siteSelector.currentIndexChanged.connect(self._showOccurrence)
        self._showOccurrences()

    @property
    def selectedNames(self) -> tuple[str, ...]:
        return tuple(name for row, name in enumerate(self._counts)
                     if self.enzymeList.item(row, 0).checkState() == Qt.CheckState.Checked)

    def _setFilter(self, count: int | None) -> None:
        with QSignalBlocker(self.enzymeList):
            for row, occurrences in enumerate(self._counts.values()):
                show = count is not None and (count == -1 or count == occurrences)
                self.enzymeList.item(row, 0).setCheckState(
                    Qt.CheckState.Checked if show else Qt.CheckState.Unchecked
                )
        self._updateSelectionCount()

    def _updateSelectionCount(self) -> None:
        self._selectionCount.setText(f"{len(self.selectedNames):,} of {len(self._counts):,} displayed")

    def _showOccurrences(self) -> None:
        name = self.inspectSelector.currentData()
        with QSignalBlocker(self.enzymeList):
            self.enzymeList.setCurrentCell(self.inspectSelector.currentIndex(), 0)
        with QSignalBlocker(self.siteSelector):
            self.siteSelector.clear()
            for site in self._sites.get(name, ()):
                orientation = "+" if site.orientation == 1 else "−"
                self.siteSelector.addItem(f"{_spanLabel(site)} · {orientation}", site)
        self.siteSelector.setEnabled(self.siteSelector.count() > 0)
        self._showOccurrence()

    def _showOccurrence(self) -> None:
        site = self.siteSelector.currentData()
        if site is None:
            text = "No recognition occurrences to inspect."
            self.siteSelector.setAccessibleDescription(text)
            self.siteDetails.setText(text)
            return
        enzyme = self._enzymes[site.enzymeName]
        cuts, _ = predictRestrictionCuts(replace(self._scan, sites=(site,), enzymes=(enzyme,)))
        strand = "forward" if site.orientation == 1 else "reverse"
        text = f"{site.enzymeName} · {site.motif} · {_spanLabel(site)} · {strand} · {site.certainty}"
        if cuts:
            cut = cuts[0]
            text += (f" · Cuts after base {cut.topBoundary:,} (forward) and "
                     f"{cut.bottomBoundary:,} (reverse), with 0 at the source origin.")
        else:
            text += " · No modeled cleavage for this occurrence."
        self.siteSelector.setAccessibleDescription(text)
        self.siteDetails.setText(text)


class DigestPanel(QWidget):
    """Select a whole-molecule recipe and run it against the current source revision."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.analysis = controller.analysis
        self._scanned: RestrictionScan | None = None
        self._counts: dict[str, int] = {}
        self._choiceRows: tuple[tuple[str, str], ...] = ()
        self._recipeDocumentId: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        top = QHBoxLayout()
        heading = QLabel("Digest", self)
        heading.setObjectName("sectionTitle")
        top.addWidget(heading, 1)
        top.addWidget(_textLabel("Whole molecule", self, muted=True))
        self.overlayButton = QPushButton("Enzymes…", self)
        self.overlayButton.setToolTip("Choose which recognition sites are drawn on the sequence and map.")
        self.overlayButton.clicked.connect(self._showOverlay)
        top.addWidget(self.overlayButton)
        layout.addLayout(top)
        self.digestButton = QPushButton("Digest", self)
        self.digestButton.setObjectName("primary")
        self.digestButton.clicked.connect(self.analysis.runDigest)
        self.cancelButton = QPushButton("Cancel digestion", self)
        self.cancelButton.clicked.connect(self.analysis.cancelDigest)
        self.cancelButton.hide()
        layout.addWidget(self.digestButton)
        layout.addWidget(self.cancelButton)
        self.recipeList = ChoiceRows("Digest recipe", self, pageSize=3)
        self.recipeList.setMinimumHeight(49)
        self.recipeList.activated.connect(self._selectEnzyme)
        layout.addWidget(self.recipeList, 1)
        self.emptyRecipe = _textLabel("Choose an enzyme below to enable Digest.", self, muted=True)
        self.emptyRecipe.setStyleSheet("background: white; border: 1px solid #e4ddec;"
                                      "border-radius: 5px; padding: 7px 9px;")
        layout.addWidget(self.emptyRecipe)
        selectors = QHBoxLayout()
        self.enzymeSelector = QComboBox(self)
        self.enzymeSelector.setAccessibleName("Add digest enzyme")
        self.enzymeSelector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.enzymeSelector.addItem("Add digest enzyme…")
        self.enzymeSelector.currentIndexChanged.connect(self._addCurrent)
        selectors.addWidget(self.enzymeSelector, 1)
        self.removeButton = QPushButton("Remove", self)
        self.removeButton.setAccessibleName("Remove selected digest enzyme")
        self.removeButton.clicked.connect(self._removeCurrent)
        selectors.addWidget(self.removeButton)
        layout.addLayout(selectors)
        self.retryCatalogueButton = QPushButton("Retry enzyme catalogue", self)
        self.retryCatalogueButton.clicked.connect(self.analysis.retryRestrictionCatalogue)
        layout.addWidget(self.retryCatalogueButton)
        self.retryButton = PendingSaveButton("Retry saving digest", self)
        self.retryButton.retryRequested.connect(lambda run: self.analysis.retryDigestWrite(run.operationId))
        layout.addWidget(self.retryButton)
        self.status = _textLabel("", self, muted=True)
        layout.addWidget(self.status)
        self.analysis.recipeChanged.connect(self.refresh)
        self.analysis.scanChanged.connect(self.refresh)
        self.analysis.busyChanged.connect(self._onBusyChanged)
        self.analysis.retryChanged.connect(self._updateRetry)
        self.controller.documentChanged.connect(self.refresh)
        self._updateRetry()
        self.refresh()

    def refresh(self) -> None:
        """Refresh active recipe state and distinguish scan availability from cut counts."""
        revision = self.controller.session.current
        eligible = bool(revision is not None and revision.moleculeType == "DNA" and revision.topology)
        scan = self.analysis.scan
        self._cacheCounts(scan)
        recipe = self.analysis.recipe
        document = self.controller.workspace.activeDocument
        self._recipeDocumentId = document.documentId if document is not None else None
        self.recipeList.setRows(tuple(ChoiceRow(name, self._enzymeText(name)) for name in recipe))
        self.recipeList.setSelected(document.viewState.selectedDigestEnzyme if document else None)
        self.recipeList.setVisible(bool(recipe))
        self.emptyRecipe.setVisible(not recipe)
        available = self.analysis.availableEnzymes if eligible else ()
        choices = tuple((enzyme.name, self._enzymeText(enzyme.name))
                        for enzyme in available if enzyme.name not in recipe)
        self._updateEnzymeChoices(choices)
        self.enzymeSelector.setEnabled(bool(choices))
        self.retryCatalogueButton.setVisible(eligible and bool(self.analysis.catalogueError))
        self.overlayButton.setEnabled(self._scanned is not None and bool(self._scanned.enzymes))
        catalogueError = self.analysis.catalogueError if eligible else ""
        unavailable = catalogueError or self.analysis.digestUnavailableReason
        self.digestButton.setEnabled(not unavailable)
        self.digestButton.setToolTip(unavailable or "Preview the fragments of the whole molecule.")
        self.status.setText("" if unavailable == "Add one or more digest enzymes." else unavailable)
        self.status.setVisible(bool(self.status.text()))
        self._updateRemove()

    def _cacheCounts(self, scan: RestrictionScan | None) -> None:
        if scan is self._scanned:
            return
        self._scanned = scan
        self._counts = {}
        if scan is not None and scan.status == "completed":
            # Match occurrenceCount's distinct-span, case-insensitive definite tally.
            spans: dict[str, set[tuple[int, int]]] = {}
            for site in scan.sites:
                if site.certainty == "definite":
                    spans.setdefault(site.enzymeName.casefold(), set()).add((site.start, site.end))
            self._counts = {
                enzyme.name: len(spans.get(enzyme.name.casefold(), ())) for enzyme in scan.enzymes
            }

    def _updateEnzymeChoices(self, choices: tuple[tuple[str, str], ...]) -> None:
        if choices == self._choiceRows:
            return
        selector = self.enzymeSelector
        sameNames = tuple(name for name, _ in choices) == tuple(
            name for name, _ in self._choiceRows
        )
        with QSignalBlocker(selector):
            if sameNames:
                for index, ((_, oldLabel), (_, label)) in enumerate(zip(self._choiceRows, choices), 1):
                    if label != oldLabel:
                        selector.setItemText(index, label)
            else:
                selected = selector.currentData()
                selector.clear()
                selector.addItem("Add digest enzyme…")
                for name, label in choices:
                    selector.addItem(label, name)
                selector.setCurrentIndex(max(0, selector.findData(selected)))
        self._choiceRows = choices

    def _onBusyChanged(self, isBusy: bool) -> None:
        previous = self.digestButton if isBusy else self.cancelButton
        nextAction = self.cancelButton if isBusy else self.digestButton
        hasFocus = previous.hasFocus()
        self.digestButton.setVisible(not isBusy)
        self.cancelButton.setVisible(isBusy)
        self.cancelButton.setEnabled(not self.analysis.savingDigest)
        self.refresh()
        if hasFocus and nextAction.isEnabled():
            nextAction.setFocus()

    def _updateRetry(self) -> None:
        self.retryButton.setRuns(self.analysis.pendingSaveRuns())

    def _enzymeText(self, name: str) -> str:
        count = self._counts.get(name)
        if count is None:
            return name
        return f"{name} · {count:,} {'site' if count == 1 else 'sites'}"

    def _selectEnzyme(self, name: str) -> None:
        document = self.controller.workspace.activeDocument
        if (document is not None and document.documentId == self._recipeDocumentId
                and name in document.viewState.digestEnzymes):
            document.viewState.selectedDigestEnzyme = name
        self._updateRemove()

    def _selectedEnzyme(self) -> str | None:
        document = self.controller.workspace.activeDocument
        if document is None or document.documentId != self._recipeDocumentId:
            return None
        name = document.viewState.selectedDigestEnzyme
        if name in document.viewState.digestEnzymes and self.recipeList.selectedKeys == {name}:
            return name
        return None

    def _updateRemove(self) -> None:
        self.removeButton.setVisible(bool(self.analysis.recipe))
        self.removeButton.setEnabled(self._selectedEnzyme() is not None)

    def _addCurrent(self, index: int) -> None:
        name = self.enzymeSelector.itemData(index)
        if name is not None:
            with QSignalBlocker(self.enzymeSelector):
                self.enzymeSelector.setCurrentIndex(0)
            self.analysis.addEnzyme(name)

    def _removeCurrent(self) -> None:
        name = self._selectedEnzyme()
        if name is not None:
            self.analysis.removeEnzyme(name)

    def _showOverlay(self) -> None:
        scan = self.analysis.scan
        if scan is None:
            return
        self._cacheCounts(scan)
        dialog = _EnzymeOverlayDialog(
            scan, self.analysis.overlayNames, self, definiteCounts=self._counts,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and self.analysis.scan is scan:
            self.analysis.setOverlayNames(dialog.selectedNames)
        dialog.deleteLater()


def _definitionLine(enzyme: EnzymeRecord) -> str:
    pairs = ", ".join(f"{pair.topOffset:,} / {pair.bottomOffset:,}" for pair in enzyme.cleavagePairs)
    geometry = f"motif-relative F/R cuts {pairs}" if pairs else "unknown cut offsets"
    identifier = f" · REBASE {enzyme.rebaseId}" if enzyme.rebaseId is not None else ""
    return f"{enzyme.name}  {enzyme.recognitionSite}  ·  {geometry}{identifier}"


def _runDetails(run: RestrictionDigest) -> str:
    lines = [
        f"Run {run.operationId}",
        f"Recorded {run.createdAt} · Restriction engine {run.engineVersion}",
        f"Source revision {run.template.revisionId}",
        f"Catalogue {run.catalogueRelease.source} · Biopython "
        f"{run.catalogueRelease.biopythonVersion}",
        f"Catalogue snapshot {run.catalogueRelease.snapshotSha256}",
        f"Model {run.digestModel}",
        f"Matching {run.matchingPolicy} · Strand policy {run.duplexPolicy}",
        "",
        *(_definitionLine(enzyme) for enzyme in run.enzymes),
        "",
        "Cleavage boundaries (zero-based interbase forward / reverse)",
    ]
    if run.cuts:
        lines.extend(
            f"{cut.topBoundary:,} / {cut.bottomBoundary:,}  ·  "
            + ", ".join(sorted({site.enzymeName for site in cut.sites}))
            for cut in run.cuts
        )
    else:
        lines.append("No recorded cleavage events.")
    if run.warnings:
        lines.extend(("", *run.warnings))
    return "\n".join(lines)


class _DigestProductArea(QWidget):
    """Keep the product list beside the details while both have room to be read."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.columns = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(12)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        direction = (QBoxLayout.Direction.LeftToRight if event.size().width() >= 650
                     else QBoxLayout.Direction.TopToBottom)
        if self.columns.direction() != direction:
            self.columns.setDirection(direction)


class DigestDialog(QDialog):
    """Inspect recorded products and adjust the gel preview."""

    showGelRequested = Signal()
    showSequenceRequested = Signal()
    openSourceRequested = Signal(str)
    assemblyRequested = Signal(object, object)

    def __init__(
        self, controller: WorkspaceController, digestion: SavedDigestion,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.analysis = controller.analysis
        self.digestion = digestion
        self._run = digestion.result
        self._fragments = {fragment.outputKey: fragment for fragment in self._run.fragments}
        self._fragmentNumbers = {key: index for index, key in enumerate(self._fragments, 1)}
        self._lane = (
            digestLane(self._run.template, self._run, label="Digest") if self._fragments else None
        )
        self._gelState = GelState()
        self._isRerunning = False
        self._workspaceProject = controller.workspace.project
        self.setObjectName("digestDialog")
        self.setWindowTitle("Digest fragments")
        self.setMinimumWidth(825)
        if parent is not None and parent.isVisible():
            self.resize(min(1090, parent.width() - 32), min(685, parent.height() - 32))
        else:
            self.resize(1090, 685)
        sourceName = FittedLabel(self._run.template.name, self)
        sourceName.setObjectName("muted")
        layout = dialogLayout(self, icon=workspaceIcon("digest"), subtitle=sourceName)
        self._addHeader(layout)
        sourceMapLayout = QVBoxLayout()
        sourceMapLayout.setSpacing(4)
        self.sourceMap = DigestSourceMap(self._run.template, self)
        self.sourceMapCaption = _textLabel("", self, muted=True)
        sourceMapLayout.addWidget(self.sourceMap)
        sourceMapLayout.addWidget(self.sourceMapCaption)
        layout.addLayout(sourceMapLayout)
        layout.addWidget(self._createResults(), 1)
        self._addActions(layout)
        self._renderGel()
        self._renderProduct()
        self.fragmentList.setSelected(self.digestion.view.selectedOutputKey)
        self.fragmentList.activated.connect(self._changeSelection)
        self.analysis.busyChanged.connect(self._updateBusy)

    def _createResults(self) -> QScrollArea:
        results = QScrollArea(self)
        results.setAccessibleName("Digest products and expected bands")
        results.setWidgetResizable(True)
        results.setMinimumHeight(140)
        results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("digestColumns")
        splitter.setHandleWidth(12)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._createProductArea(splitter))
        gelPanel = QFrame(splitter)
        gelPanel.setObjectName("card")
        gelLayout = QVBoxLayout(gelPanel)
        gelLayout.setContentsMargins(10, 10, 10, 10)
        gelTitle = _textLabel("Expected bands", gelPanel)
        gelTitle.setObjectName("sectionTitle")
        gelLayout.addWidget(gelTitle)
        self.preview = GelPreview(self.analysis, gelPanel, minimumPlotHeight=125)
        self.analysis.ladderCatalogueChanged.connect(self._renderGel)
        self.analysis.viewSaveFailed.connect(self._viewSaveFailed)
        self.preview.markerChanged.connect(self._changeMarker)
        self.preview.bandActivated.connect(self._activateBand)
        gelLayout.addWidget(self.preview, 1)
        self.gelSelectionSummary = _textLabel("", gelPanel, muted=True)
        gelLayout.addWidget(self.gelSelectionSummary)
        splitter.addWidget(gelPanel)
        gelPanel.setVisible(self._run.status == "completed")
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([666, 376])
        splitter.setStyleSheet("QSplitter#digestColumns::handle { background: transparent; }")
        results.setWidget(splitter)
        return results

    def _addHeader(self, layout: QVBoxLayout) -> None:
        run = self._run
        names = " + ".join(enzyme.name for enzyme in run.enzymes)
        context = (
            f"{digestOutcomeText(run)}\n{names} · {len(run.cuts):,} cleavage events "
            f"· Run {run.operationId[:8]}"
        )
        original = (
            "Verified original bytes available" if self.analysis.originalAvailable(self.digestion)
            else "Original bytes unavailable"
        )
        header = QHBoxLayout()
        header.addWidget(_textLabel(f"{context} · {original}", self, muted=True), 1)
        self.rerunButton = QPushButton("Run again…", self)
        self.rerunButton.clicked.connect(self._rerun)
        self.rerunButton.setEnabled(run.status == "completed")
        self.cancelRunButton = QPushButton("Cancel calculation", self)
        self.cancelRunButton.clicked.connect(self.analysis.cancelDigest)
        self.cancelRunButton.hide()
        header.addWidget(self.rerunButton, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.cancelRunButton, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

    def _createProductArea(self, parent: QWidget) -> QWidget:
        panel = _DigestProductArea(parent)
        products = QWidget(panel)
        layout = QVBoxLayout(products)
        layout.setContentsMargins(0, 4, 0, 0)
        heading = _textLabel("Products", products)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.fragmentList = ChoiceRows("Digest fragments", products)
        rowHeight = 3 * self.fragmentList.fontMetrics().lineSpacing()
        self.fragmentList.setMinimumHeight(rowHeight + 16)
        self.fragmentList.setStyleSheet("""
            QScrollArea > QWidget > QWidget { background: white; }
            QPushButton#featureName { border: 1px solid #e2dcef; padding: 5px 6px; }
            QPushButton#featureName:checked { background: #eee8fc; border-color: #cbbce9; }
            QPushButton#featureName:focus { border-color: #6236d3; }
        """ + f"QPushButton#featureName {{ min-height: {rowHeight}px; }}")
        layout.addWidget(self.fragmentList, 1)
        products.setVisible(bool(self._fragments))
        panel.columns.addWidget(products, 2)
        inspector = QWidget(panel)
        inspectorLayout = QVBoxLayout(inspector)
        inspectorLayout.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget(inspector)
        self._createFragmentDetails(tabs)
        recorded = QPlainTextEdit(tabs)
        recorded.setAccessibleName("Recorded digestion details")
        recorded.setReadOnly(True)
        recorded.setPlainText(_runDetails(self._run))
        tabs.addTab(recorded, "Recorded run")
        if not self._fragments:
            tabs.setCurrentWidget(recorded)
        inspectorLayout.addWidget(tabs, 1)
        fragmentActions = QHBoxLayout()
        self.copyButton = QPushButton("Copy fragment", inspector)
        self.assemblyButton = QPushButton("Use in Assembly…", inspector)
        for button, callback in ((self.copyButton, self._copy), (self.assemblyButton, self._prepareAssembly)):
            button.clicked.connect(callback)
            fragmentActions.addWidget(button)
        inspectorLayout.addLayout(fragmentActions)
        panel.columns.addWidget(inspector, 3)
        return panel

    def _createFragmentDetails(self, tabs: QTabWidget) -> None:
        page = QFrame(tabs)
        page.setObjectName("dialogCard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(11, 10, 11, 10)
        layout.setSpacing(5)
        self.productHeading = _textLabel("", page)
        self.productHeading.setObjectName("sectionTitle")
        layout.addWidget(self.productHeading)
        self.productCoordinates = FittedLabel("", page)
        self.productCoordinates.setObjectName("muted")
        layout.addWidget(self.productCoordinates)
        self.endSummary = FittedLabel("", page)
        self.endSummary.setObjectName("muted")
        layout.addWidget(self.endSummary)
        self.sequencePreview = QPlainTextEdit(page)
        self.sequencePreview.setReadOnly(True)
        self.sequencePreview.setMinimumHeight(42)
        self.sequencePreview.setAccessibleName("Physical forward strand, 5 prime to 3 prime")
        self.sequencePreview.setFont(workspaceMonoFont(13))
        layout.addWidget(self.sequencePreview, 1)
        self.annotationSummary = FittedLabel("", page)
        self.annotationSummary.setObjectName("sectionTitle")
        layout.addWidget(self.annotationSummary)
        self.annotationNames = FittedLabel("", page)
        self.annotationNames.setObjectName("muted")
        layout.addWidget(self.annotationNames)
        tabs.addTab(page, "Selected fragment")
        self.fragmentEvidence = QPlainTextEdit(tabs)
        self.fragmentEvidence.setReadOnly(True)
        self.fragmentEvidence.setAccessibleName("Physical ends and inherited annotations")
        tabs.addTab(self.fragmentEvidence, "Ends and annotations")

    def _addActions(self, layout: QVBoxLayout) -> None:
        commands = QHBoxLayout()
        commands.setSpacing(5)
        self.createButton = QPushButton("Create construct", self)
        self.createButton.setObjectName("primary")
        self.pinButton = QPushButton("Add digest to Gel", self)
        self.saveButton = QPushButton("Save digestion…", self)
        self.exportButton = QPushButton("Export figure…", self)
        self.sourceButton = QPushButton("Open source snapshot", self)
        for button, callback in (
            (self.sourceButton, self._openSource), (self.exportButton, self._export),
            (self.saveButton, self._save),
        ):
            button.clicked.connect(callback)
            commands.addWidget(button)
        commands.addStretch(1)
        self.pinButton.clicked.connect(self._pin)
        commands.addWidget(self.pinButton)
        layout.addLayout(commands)
        actions = QHBoxLayout()
        self.status = _textLabel("", self, muted=True)
        closeButton = QPushButton("Close", self)
        closeButton.clicked.connect(self.reject)
        self.createButton.clicked.connect(self._create)
        actions.addWidget(closeButton)
        actions.addWidget(self.status, 1)
        actions.addWidget(self.createButton)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        addDialogFooter(layout, actions, self.createButton)
        completed = self._run.status == "completed"
        self.saveButton.setEnabled(completed)
        self.pinButton.setEnabled(completed)
        self.exportButton.setEnabled(completed)

    @property
    def selectedFragment(self) -> DNAFragment | None:
        return self._fragments.get(self.digestion.view.selectedOutputKey)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Reflow changes the product list's viewport after the outer resize.
        QTimer.singleShot(0, self, self._revealFragment)

    def _revealFragment(self) -> None:
        self.fragmentList.setSelected(self.digestion.view.selectedOutputKey)

    def _renderProduct(self) -> None:
        fragment = self.selectedFragment
        self._renderSourceMap(fragment)
        self.copyButton.setEnabled(fragment is not None)
        self.createButton.setEnabled(fragment is not None and bool(self._run.cuts))
        self.assemblyButton.setEnabled(fragment is not None and bool(self._run.cuts))
        if fragment is None:
            self.productHeading.setText("Select a fragment to inspect its physical sequence.")
            self.productCoordinates.hide()
            self.endSummary.hide()
            self.annotationSummary.hide()
            self.annotationNames.hide()
            self.sequencePreview.clear()
            self.fragmentEvidence.clear()
            return
        number = self._fragmentNumbers[fragment.outputKey]
        self.productHeading.setText(f"Fragment {number} · {fragment.forwardLength:,} nt")
        self.productCoordinates.setFullText(
            f"Forward 5′→3′ · Source {formatSourceSpans(fragment.sourceSpans)}"
        )
        self.productCoordinates.show()
        left = _endLabel(fragment.leftEnd, concise=True)
        right = _endLabel(fragment.rightEnd, concise=True)
        self.endSummary.setFullText(f"Left {left}  ·  Right {right}")
        self.endSummary.show()
        features = fragment.revision.features
        self.annotationSummary.setFullText(f"Inherited annotations · {len(features):,}")
        self.annotationSummary.show()
        names = [feature.label for feature in features[:3]]
        if len(features) > len(names):
            names.append(f"+{len(features) - len(names):,} more")
        self.annotationNames.setFullText(" · ".join(names))
        self.annotationNames.setVisible(bool(names))
        self.sequencePreview.setPlainText(fragment.forwardSequence)
        details = [
            f"Output {fragment.outputKey}",
            f"Ordered source bases {formatSourceSpans(fragment.sourceSpans)} · {fragment.topology}",
            f"Forward strand {fragment.forwardLength:,} nt · Reverse strand {fragment.reverseLength:,} nt",
            f"Paired {fragment.pairedLength:,} bp · Aligned span {fragment.referenceLength:,} columns",
            f"Left end · {_endLabel(fragment.leftEnd, includeChemistry=True)}",
            f"Right end · {_endLabel(fragment.rightEnd, includeChemistry=True)}",
            "",
            f"Inherited annotations · {len(fragment.featureMappings)}",
        ]
        for feature, mapping in zip(fragment.revision.features, fragment.featureMappings):
            parts = " → ".join(
                f"{part.start + 1:,}–{part.end:,}" if part.isSelectable else part.locationText
                for part in feature.parts
            )
            strand = {1: "forward", -1: "reverse"}.get(feature.strand, "strand unknown")
            partial = " · partial" if mapping.partial else ""
            details.append(f"{feature.label} · {feature.type} · {parts} · {strand}{partial}")
        if fragment.warnings:
            details.extend(("", *fragment.warnings))
        self.fragmentEvidence.setPlainText("\n".join(details))

    def _renderSourceMap(self, fragment: DNAFragment | None) -> None:
        self.sourceMap.setSourceSpans(fragment.sourceSpans if fragment is not None else ())
        if fragment is None:
            caption = (
                "Select a fragment to show its source bases." if self._run.status == "completed"
                else "No fragments available for this run."
            )
        else:
            if not self._run.cuts:
                product = f"Uncut {self._run.template.topology} source"
            else:
                product = f"Fragment {self._fragmentNumbers[fragment.outputKey]}"
                if self._run.template.topology == "circular" and len(self._run.cuts) == 1:
                    product += " · Linearized from circular source"
            caption = f"{product} · Source bases {formatSourceSpans(fragment.sourceSpans)}"
        self.sourceMapCaption.setText(caption)

    def _renderGel(self) -> None:
        fragment = self.selectedFragment
        selection = (
            GelSelection(self._lane.laneId, fragment.revision.outputReference)
            if self._lane is not None and fragment is not None else None
        )
        view = self.digestion.view
        self._gelState = GelState(self.analysis.resolveGelMarker(view.marker), view.settings,
                                  (self._lane,) if self._lane is not None else (), selection)
        self.preview.setState(self._gelState)
        statuses = {}
        summary = ""
        for laneLayout in self.preview.gelLayout.lanes:
            for placement in laneLayout.placements:
                key = placement.product.outputReference.outputKey
                status = gelProductStatus(placement)
                statuses[key] = status
                if fragment is not None and key == fragment.outputKey and status:
                    summary = f"{fragment.forwardLength:,} nominal bp · {status}"
            for band in laneLayout.bands:
                if len(band.products) > 1 and any(product.fragment == fragment for product in band.products):
                    hint = f"{len(band.products)} fragments share this band. Choose each in Products."
                    summary = f"{summary}\n{hint}" if summary else hint
                    break
        self.gelSelectionSummary.setVisible(bool(summary))
        self.gelSelectionSummary.setText(summary)
        self._refreshFragmentRows(statuses)

    def _refreshFragmentRows(self, statuses: dict[str, str]) -> None:
        rows = []
        for key, fragment in self._fragments.items():
            number = self._fragmentNumbers[key]
            spans = formatSourceSpans(fragment.sourceSpans)
            heading = f"Fragment {number} · {fragment.forwardLength:,} nt · {fragment.topology}"
            positions = f"Source bases {spans}"
            left = _endLabel(fragment.leftEnd, concise=True)
            right = _endLabel(fragment.rightEnd, concise=True)
            ending = f"Left {left} · Right {right}"
            status = statuses.get(key, "")
            rowText = f"{heading}\n{positions}\n{ending}"
            if status:
                rowText += f" · {status}"
            rows.append(ChoiceRow(key, rowText, rowText.replace("\n", " · ")))
        self.fragmentList.setRows(tuple(rows))

    def _changeSelection(self, outputKey: str | None) -> None:
        view = self.digestion.view
        if outputKey == view.selectedOutputKey:
            self._revealFragment()
            return
        digestion = replace(self.digestion, view=replace(view, selectedOutputKey=outputKey))
        if self.analysis.updateDigestionView(digestion):
            self.digestion = digestion
            self.status.clear()
            self._renderProduct()
            self._renderGel()
        else:
            self.status.setText("Could not save the fragment selection.")
        self._revealFragment()

    def _activateBand(self, selection: GelSelection) -> None:
        if selection.outputReference is not None:
            self._changeSelection(selection.outputReference.outputKey)

    def _changeMarker(self, marker: GelMarker) -> None:
        view = replace(self.digestion.view, marker=marker)
        digestion = replace(self.digestion, view=view)
        if self.analysis.updateDigestionView(digestion):
            self.digestion = digestion
            self.status.clear()
        else:
            self.status.setText("Could not save the ladder selection.")
        self._renderGel()

    def _viewSaveFailed(
        self, operationId: str, attempted: GelViewSettings, saved: GelViewSettings, message: str,
    ) -> None:
        if self._run.operationId != operationId or self.digestion.view is not attempted:
            return
        label = "preview settings"
        if saved.marker == attempted.marker:
            label = "fragment selection"
        elif saved.selectedOutputKey == attempted.selectedOutputKey:
            label = "ladder selection"
        self.digestion = replace(self.digestion, view=saved)
        self.status.setText(f"Could not save the {label}. {message}")
        self._renderProduct()
        self._renderGel()
        self._revealFragment()

    def _openSource(self) -> None:
        self.openSourceRequested.emit(self._run.template.revisionId)
        self.accept()

    def _copy(self) -> None:
        fragment = self.selectedFragment
        if fragment is not None and self.analysis.copyFragment(self._run.operationId, fragment.outputKey):
            self.status.setText(f"Copied {fragment.forwardLength:,} nt from the physical forward strand.")
        else:
            self.status.setText("Select the forward sequence in the preview to copy it manually.")

    def _create(self) -> None:
        fragment = self.selectedFragment
        if fragment is not None and self.analysis.adoptFragment(self._run.operationId, fragment.outputKey):
            self.showSequenceRequested.emit()
            self.accept()

    def _prepareAssembly(self) -> None:
        if self._run.status == "completed" and self._run.cuts:
            self.assemblyRequested.emit(self.digestion.view.selectedOutputKey, self.selectedFragment)

    def _pin(self) -> None:
        if self.analysis.pinDigestion(self.digestion):
            self.showGelRequested.emit()
            self.accept()

    def _save(self) -> None:
        name = Path(self._run.template.name).name or "digestion"
        suggested = fileDialogDirectory() / f"{name}.vibedigest"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save digestion", str(suggested), "strandfolio digestion (*.vibedigest)",
        )
        if not filename or self.controller.workspace.project is not self._workspaceProject:
            return
        picked = Path(filename)
        destination = picked if picked.suffix else picked.with_suffix(".vibedigest")

        def confirm(_staged: SavedDigestion) -> bool:
            # Ask again before overwriting a file if adding the extension changed the path.
            if destination != picked and exportDestinationVersion(destination) is not None:
                if not confirmAction(self, "Replace existing file", f"Replace {destination.name}?", "Replace"):
                    return False
            return self.controller.workspace.project is self._workspaceProject

        if self.analysis.saveDigestion(self.digestion, destination, confirm=confirm):
            self.status.setText(f"Saved {destination.name}.")

    def _export(self) -> None:
        exportGelFigure(self, self.controller, self._gelState,
                        title=f"{self._run.template.name} · Digestion {self._run.operationId}")

    def _updateBusy(self, isBusy: bool) -> None:
        if not isBusy:
            self._isRerunning = False
        self.rerunButton.setEnabled(not isBusy and self._run.status == "completed")
        self.rerunButton.setVisible(not self._isRerunning)
        self.cancelRunButton.setVisible(self._isRerunning)
        self.cancelRunButton.setEnabled(not self.analysis.savingDigest)

    def _rerun(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("rerunDigestDialog")
        dialog.setWindowTitle("Run digestion again")
        dialog.resize(605, 400)
        layout = dialogLayout(dialog)
        layout.addWidget(_textLabel(
            "Use the captured source and enzyme definitions to calculate a new run with its own ID.",
            dialog,
        ))
        engine = RESTRICTION_ENGINE_VERSION
        difference = (
            f"Engine {engine} matches the recorded run." if engine == self._run.engineVersion
            else f"Engine {engine} will be used in place of recorded engine {self._run.engineVersion}."
        )
        layout.addWidget(_textLabel(difference, dialog, muted=True))
        recipeLabel = _textLabel("Recorded inputs", dialog)
        recipeLabel.setObjectName("sectionTitle")
        layout.addWidget(recipeLabel)
        recipe = QPlainTextEdit(dialog)
        recipe.setReadOnly(True)
        recipe.setAccessibleName("Proposed digestion inputs and settings")
        recipe.setPlainText("\n".join((
            f"Source {self._run.template.name} · {self._run.template.topology}",
            f"Revision {self._run.template.revisionId}",
            f"Model {self._run.digestModel} · {self._run.matchingPolicy}",
            f"Strand policy {self._run.duplexPolicy}",
            f"Catalogue snapshot {self._run.catalogueRelease.snapshotSha256}",
            "",
            *(_definitionLine(enzyme) for enzyme in self._run.enzymes),
        )))
        layout.addWidget(recipe, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=dialog)
        runButton = buttons.addButton("Run again", QDialogButtonBox.ButtonRole.AcceptRole)
        runButton.setAutoDefault(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        addDialogFooter(layout, buttons, runButton)
        if dialog.exec() == QDialog.DialogCode.Accepted and self.analysis.rerunDigestion(self.digestion):
            self._isRerunning = True
            self._updateBusy(True)
        dialog.deleteLater()
