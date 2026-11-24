"""Compose the synchronized sequence and plasmid workspace with top action bar and collapsible panels."""

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
import sqlite3

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from strandfolio.application.workspace import DocumentTarget
from strandfolio.data import DATABASE_DIRECTORY
from strandfolio.domain.fragments import DNAFragment
from strandfolio.gui.app import buildAppIcon, buildHeaderIconPixmap
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.figure_dialog import FigureScope, exportFigure
from strandfolio.gui.assembly_example import AssemblyExampleActions
from strandfolio.gui.panels.digest import DigestDialog, DigestPanel, digestOutcomeText
from strandfolio.gui.panels.dialog_layout import confirmAction, fileDialogDirectory
from strandfolio.gui.panels.document_dialogs import (
    BatchReviewDialog, DocumentDialogs, TextMoleculeDialog, TextNormalizationDialog,
)
from strandfolio.gui.panels.document_strip import DocumentStrip, documentLabels
from strandfolio.gui.panels.features import FeaturePanel
from strandfolio.gui.panels.map_features import MapFeaturesMenu
from strandfolio.gui.panels.sequence_conversion import SequenceCopyDialog, SequenceTranslationControls
from strandfolio.gui.panels.sequence_details import (
    ImportDetailsDialog, SequenceDetails, selectionCountText, selectionSummary,
)
from strandfolio.gui.panels.sequence_text_size import sequenceTextSizeControl
from strandfolio.gui.sequence_export import OriginalRetrieval, exportSavedSequence
from strandfolio.gui.theme import STYLE
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.gui.views.alignment import AlignmentView
from strandfolio.gui.views.assembly import AssemblyView
from strandfolio.gui.views.gel import GelView
from strandfolio.gui.views.history import HistoryView
from strandfolio.gui.views.overview import SequenceOverview
from strandfolio.gui.views.pcr import PcrView
from strandfolio.gui.views.pcr_result import PcrResultDialog
from strandfolio.gui.panels.saved_primer_pairs import applyPairToEditor
from strandfolio.gui.views.plasmid_map import PlasmidMapView
from strandfolio.gui.views.sequence_view import SequenceView
from strandfolio.io.digestion import SavedDigestion
from strandfolio.io.formats import (
    FASTA_EXTENSIONS,
    GENBANK_EXTENSIONS,
    PlainTextSource,
    SNAPGENE_EXTENSIONS,
    SequenceImportResult,
    TEXT_EXTENSIONS,
    TextNormalization,
)
from strandfolio.operations.gel import GelViewSettings
from strandfolio.operations.pcr import PcrProduct, PcrRun
from strandfolio.rendering.layout import LayerOptions


EXAMPLE_PLASMID = DATABASE_DIRECTORY / "plasmids/maps/pBH4-His-SUMO-GFP-LPETGG.gb"
DESIGN_EXAMPLE = Path(__file__).resolve().parents[1] / "data/examples/pReporter_demo.gb"


class _WorkflowNavigation(QTabBar):
    """Separate the sequence, analysis, and output tools within the native tab bar."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor("#e2dcef"))
        for index in (0, 3):
            rect = self.tabRect(index)
            if rect.isValid():
                edge = rect.right() if self.layoutDirection() == Qt.LayoutDirection.LeftToRight else rect.left()
                painter.drawLine(edge, rect.center().y() - 7, edge, rect.center().y() + 7)
        if self.hasFocus() and self.currentIndex() >= 0:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor("#5932b9"))
            current = self.tabRect(self.currentIndex()).adjusted(2, 6, -2, -6)
            painter.drawRoundedRect(current, 6, 6)
        painter.end()


class MainWindow(QMainWindow):
    """Connect the workspace's views, controls and controllers."""

    _COMPACT_BREAKPOINT = 1040

    def __init__(self) -> None:
        super().__init__()
        self.controller = WorkspaceController(self)
        self._manualTrackMode: str | None = None
        self._activeViewMode = "split"
        self._displayedDocumentId: str | None = None
        self._annotationStatus = ""
        self._intakeDialog: QDialog | None = None
        self._reviewingImport = False
        self.setWindowTitle("strandfolio · Sequence workspace")
        appIcon = buildAppIcon()
        if not appIcon.isNull():
            self.setWindowIcon(appIcon)
        self.resize(1280, 820)
        self.setMinimumSize(920, 600)
        self.setStyleSheet(STYLE)

        central = QWidget(self)
        central.setObjectName("workspaceChrome")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.documentDialogs = DocumentDialogs(self, self.controller)
        self.documentStrip = DocumentStrip(central)
        self.documentStrip.activateRequested.connect(self._activateFromStrip)
        self.documentStrip.closeRequested.connect(self.documentDialogs.requestClose)
        self.documentStrip.editNameRequested.connect(self.documentDialogs.editPreferredName)
        self.documentStrip.nameFocused.connect(self.statusBar().showMessage)
        layout.addWidget(self._createHeader())

        self.feedback = QLabel(central)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setObjectName("notice")
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        layout.addWidget(self.feedback)

        self.workflows = QTabWidget(central)
        self.workflows.setObjectName("workflows")
        self.workflows.setAccessibleName("Workflows")
        self.workflows.setTabBar(_WorkflowNavigation(self.workflows))
        self.workflows.tabBar().setObjectName("workflowNavigation")
        self.workflows.setIconSize(QSize(15, 15))
        layout.addWidget(self.workflows, 1)
        self.sequencePage = QWidget()
        sequenceLayout = QVBoxLayout(self.sequencePage)
        sequenceLayout.setContentsMargins(0, 0, 0, 0)
        self.sequencePages = QStackedWidget(self.sequencePage)
        sequenceLayout.addWidget(self.sequencePages)
        self.emptyPage = self._createEmptyPage()
        self.sequencePages.addWidget(self.emptyPage)
        recordPage = QWidget(self.sequencePages)
        self.sequencePages.addWidget(recordPage)
        recordLayout = QVBoxLayout(recordPage)
        recordLayout.setContentsMargins(0, 0, 0, 0)
        recordLayout.setSpacing(2)
        self.recordControls = self._createRecordControls()
        self.statusBar().addPermanentWidget(self.recordControls)
        self.productNameBar = self._createProductNameBar()
        recordLayout.addWidget(self.productNameBar)
        self.mainSplitter = QSplitter(Qt.Orientation.Horizontal, recordPage)
        self.mainSplitter.setChildrenCollapsible(False)
        self.canvasStack = QStackedWidget()
        self.sequenceWorkspace = self._createSequenceWorkspace()
        self.canvasStack.addWidget(self.sequenceWorkspace)
        self.mapCard = self._createMapCard()
        self.sidebar = self._createSidebar()
        self.mainSplitter.addWidget(self.canvasStack)
        self.mainSplitter.addWidget(self.sidebar)
        self.mainSplitter.setStretchFactor(0, 1)
        self.mainSplitter.setStretchFactor(1, 0)
        self.mainSplitter.setSizes([956, 324])
        recordLayout.addWidget(self.mainSplitter, 1)

        self.details = SequenceDetails(self.controller, recordPage)
        recordLayout.addWidget(self.details)
        self.translationControls = SequenceTranslationControls(self.controller, recordPage)
        recordLayout.addWidget(self.translationControls)
        self._setInspectorVisible(False)
        self.sequenceTools = self._createSequenceTools()
        self._copyDialog: SequenceCopyDialog | None = None
        self.pcrView = PcrView(self.controller, self)
        self.alignmentView = AlignmentView(self.controller, self)
        self.assemblyView = AssemblyView(self.controller, self)
        self.gelView = GelView(self.controller, self)
        self.historyView = HistoryView(self.controller, self)
        self._workflowTools = {
            self.sequencePage: self.sequenceTools,
            self.pcrView: self.pcrView.workflowTools,
            self.assemblyView: self.assemblyView.workflowTools,
            self.gelView: self.gelView.workflowTools,
            self.historyView: self.historyView.workflowTools,
        }
        for tools in self._workflowTools.values():
            tools.setParent(self.workflows)
            tools.layout().setContentsMargins(0, 5, 10, 5)
            tools.hide()
        for name, page in (
            ("Sequence", self.sequencePage), ("PCR", self.pcrView),
            ("Alignment", self.alignmentView), ("Assembly", self.assemblyView),
            ("Gel", self.gelView), ("History", self.historyView),
        ):
            self.workflows.addTab(page, workspaceIcon(name.lower()), name)
        self.workflows.currentChanged.connect(self._onWorkflowChanged)
        analysis = self.controller.analysis
        pcr = self.controller.pcr
        self._digestDialog: DigestDialog | None = None
        self._pcrDialog: PcrResultDialog | None = None
        analysis.digestionOpened.connect(self._showDigestion)
        analysis.digestCompleted.connect(self._onDigestCompleted)
        analysis.rerunCompleted.connect(self._onDigestRerun)
        analysis.sourceOpened.connect(self._showSequence)
        analysis.constructOpened.connect(self._showSequence)
        self.controller.sequenceOpened.connect(self._showSequence)
        self.historyView.openDigestRequested.connect(analysis.reopenDigestion)
        self.historyView.openSourceRequested.connect(analysis.openSource)
        self.historyView.openSavedRequested.connect(self._chooseDigestion)
        self.historyView.openAncestrySourceRequested.connect(self.controller.openExactRevision)
        AssemblyExampleActions(self, DESIGN_EXAMPLE)
        self.assemblyView.openFileRequested.connect(self._openAssemblyFile)
        self.assemblyView.openSavedRequested.connect(self._openAssemblySaved)
        self.assemblyView.pasteRequested.connect(
            lambda: self._pasteSequenceDialog(assemblyPickerToken=self.assemblyView.pickerToken),
        )
        pcr.runCompleted.connect(self._onPcrCompleted)
        pcr.runOpened.connect(self._showRecordedPcr)
        pcr.previewOpened.connect(self._showPinnedPcr)
        pcr.productOpened.connect(self._showSequence)
        pcr.sourceOpened.connect(self._showSequence)
        self.historyView.openPcrRequested.connect(pcr.reopenRun)
        self.historyView.usePrimerPairRequested.connect(self._reusePrimerPair)
        self.historyView.openPcrSourceRequested.connect(pcr.openSource)

        self.setCentralWidget(central)
        self._createMenu()
        self.controller.collectionChanged.connect(self._updateCollection)
        self.controller.documentChanged.connect(self._updateDocument)
        self.controller.selectionChanged.connect(self._updateCopyFormats)
        self.controller.translationChanged.connect(self._updateCopyFormats)
        self.controller.nameChanged.connect(self._updateNames)
        self.controller.editNameRequested.connect(self.documentDialogs.editPreferredName)
        self.controller.closeRequested.connect(self.documentDialogs.requestClose)
        self.controller.batchReviewRequested.connect(self._reviewBatch)
        self.controller.textTypeRequested.connect(self._chooseTextType)
        self.controller.normalizationReviewRequested.connect(self._reviewTextNormalization)
        self.controller.layersChanged.connect(self._updateLayerControls)
        self.controller.loadingChanged.connect(self._onLoadingChanged)
        self.controller.message.connect(self.statusBar().showMessage)
        self.controller.importFailed.connect(self._showImportError)
        self.controller.operationFailed.connect(self._showOperationError)
        self.controller.annotations.changed.connect(self._updateAnnotationActions)
        self._applyViewMode("split")
        self._updateCollection()
        self._updateDocument()

    def _createHeader(self) -> QWidget:
        header = QFrame(self)
        header.setObjectName("workspaceHeader")
        header.setMinimumHeight(40)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 3, 14, 1)
        layout.setSpacing(7)

        self.brandIcon = QLabel(header)
        self.brandIcon.setObjectName("brandIcon")
        self.brandIcon.setFixedSize(25, 25)
        self.brandIcon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        headerPixmap = buildHeaderIconPixmap(24, self.devicePixelRatioF())
        if not headerPixmap.isNull():
            self.brandIcon.setPixmap(headerPixmap)
        layout.addWidget(self.brandIcon)

        brand = QLabel("strandfolio", header)
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(5)
        divider = QFrame(header)
        divider.setObjectName("brandDivider")
        divider.setFixedSize(1, 17)
        layout.addWidget(divider)
        layout.addSpacing(4)
        layout.addWidget(self.documentStrip, 1, Qt.AlignmentFlag.AlignBottom)

        self.openButton = QPushButton("Open…", header)
        self.openButton.setIcon(workspaceIcon("open"))
        self.openButton.setIconSize(QSize(14, 14))
        self.openButton.setToolTip("Open a GenBank, FASTA, SnapGene or plain-text file.")
        self.openButton.clicked.connect(self._openFromHeader)
        layout.addWidget(self.openButton)

        self.cancelImportButton = QPushButton("Cancel", header)
        self.cancelImportButton.clicked.connect(self.controller.cancelImport)
        self.cancelImportButton.hide()
        layout.addWidget(self.cancelImportButton)

        self.closeDocumentButton = QPushButton("Close", header)
        self.closeDocumentButton.setObjectName("headerAction")
        self.closeDocumentButton.setToolTip("Close the open sequence without modifying source files.")
        self.closeDocumentButton.clicked.connect(self.controller.closeDocument)
        layout.addWidget(self.closeDocumentButton)

        self.exportFigureButton = QPushButton("Export figure…", header)
        self.exportFigureButton.setObjectName("headerAction")
        self.exportFigureButton.setIcon(workspaceIcon("export"))
        self.exportFigureButton.setIconSize(QSize(14, 14))
        self.exportFigureButton.setToolTip("Preview the map or sequence tracks and save an SVG or PNG.")
        self.exportFigureButton.clicked.connect(self.exportSequenceFigure)
        layout.addWidget(self.exportFigureButton)

        self.importInfoButton = QPushButton(workspaceIcon("info"), "", header)
        self.importInfoButton.setObjectName("headerInfo")
        self.importInfoButton.setAccessibleName("Import details")
        self.importInfoButton.setFixedSize(28, 28)
        self.importInfoButton.setToolTip("View detected format and preservation/fidelity notices.")
        self.importInfoButton.clicked.connect(self._showImportDetails)
        layout.addWidget(self.importInfoButton)

        return header

    def _createEmptyPage(self) -> QWidget:
        page = QWidget(self.sequencePage)
        page.setObjectName("startPage")
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel = QFrame(page)
        panel.setObjectName("startPanel")
        panel.setFixedWidth(574)
        layout.addWidget(panel)
        content = QVBoxLayout(panel)
        content.setContentsMargins(24, 22, 24, 18)
        content.setSpacing(0)
        opener = QHBoxLayout()
        titleBlock = QVBoxLayout()
        titleBlock.setSpacing(5)
        title = QLabel("Open a sequence", panel)
        title.setObjectName("startTitle")
        titleBlock.addWidget(title)
        formats = QLabel("GenBank, FASTA, SnapGene or plain text", panel)
        formats.setObjectName("muted")
        titleBlock.addWidget(formats)
        opener.addLayout(titleBlock, 1)
        openButton = QPushButton(workspaceIcon("open", onAccent=True), "Open sequence…", panel)
        openButton.setObjectName("primary")
        openButton.setMinimumHeight(34)
        openButton.clicked.connect(self.openFileDialog)
        opener.addWidget(openButton)
        content.addLayout(opener)
        content.addSpacing(19)
        divider = QFrame(panel)
        divider.setObjectName("sectionDivider")
        divider.setFixedHeight(1)
        content.addWidget(divider)
        content.addSpacing(15)
        examples = QLabel("Example sequences", panel)
        examples.setObjectName("startSection")
        content.addWidget(examples)

        def addExample(name: str, description: str, label: str, activate) -> None:
            row = QHBoxLayout()
            row.setSpacing(11)
            icon = QLabel(panel)
            icon.setObjectName("exampleRecordIcon")
            icon.setFixedSize(32, 32)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setPixmap(workspaceIcon("circular").pixmap(
                QSize(18, 18), self.devicePixelRatioF(), QIcon.Mode.Selected,
            ))
            row.addWidget(icon)
            details = QVBoxLayout()
            details.setSpacing(2)
            heading = QLabel(name, panel)
            heading.setObjectName("sectionTitle")
            details.addWidget(heading)
            detail = QLabel(description, panel)
            detail.setObjectName("muted")
            details.addWidget(detail)
            row.addLayout(details, 1)
            button = QPushButton(label, panel)
            button.setMinimumHeight(30)
            button.clicked.connect(activate)
            row.addWidget(button)
            content.addSpacing(11)
            content.addLayout(row)

        addExample("pReporter_demo", "Synthetic circular reporter", "Open design example", self.openDesignExample)
        if EXAMPLE_PLASMID.is_file():
            addExample("Annotated GFP plasmid", "Deposited GenBank record", "Open example",
                       lambda: self.controller.loadSequence(EXAMPLE_PLASMID, afterIntake=self._showExample))
        return page

    def openDesignExample(self) -> None:
        """Open the illustrated sample through ordinary GenBank intake."""
        self.controller.loadSequence(
            DESIGN_EXAMPLE, afterIntake=partial(self._showExample, usePlateSelection=True),
        )

    def _showExample(self, target: DocumentTarget, *, usePlateSelection: bool = False) -> None:
        workspace = self.controller.workspace
        if workspace.activeDocumentId != target.documentId or not workspace.targetIsCurrent(target):
            return
        self._showSequence()
        revision = self.controller.session.current
        coding = next((f for f in revision.features if f.type == "CDS" and f.parts), None)
        if coding is None:
            return
        span = coding.parts[0]
        if span.start is None or span.end is None:
            return
        view = self.sequenceView
        if usePlateSelection:
            # The illustration shows the first 60 reporter bases and one row of context.
            self.controller.select(span.start, min(span.end, span.start + 60))
            row = max(0, span.start // view.layoutGeometry.basesPerRow - 1)
            view.verticalScrollBar().setValue(view.rowIndex.rowTop(row))
        else:
            view.revealInterval(span.start, span.end)
        view.setFocus(Qt.FocusReason.OtherFocusReason)

    def _createRecordControls(self) -> QWidget:
        controls = QWidget(self)
        controls.setObjectName("recordControls")
        bar = QHBoxLayout(controls)
        bar.setContentsMargins(4, 1, 6, 1)
        bar.setSpacing(4)
        self.selectionStatus = QLabel(controls)
        self.selectionStatus.setObjectName("selectionStatus")
        self.selectionStatus.setAccessibleName("Selected coordinates and length")
        bar.addWidget(self.selectionStatus)
        self.subtitle = QLabel(self)
        self.subtitle.setObjectName("muted")
        self.subtitle.setTextFormat(Qt.TextFormat.PlainText)
        bar.addWidget(self.subtitle)
        bar.addSpacing(8)
        self.splitViewButton = QPushButton("Split", self)
        self.sequenceViewButton = QPushButton("Tracks", self)
        self.mapViewButton = QPushButton("Map", self)
        self._viewGroup = QButtonGroup(self)
        for mode, button, tooltip in (
            ("split", self.splitViewButton, "Show sequence tracks and a map preview."),
            ("sequence", self.sequenceViewButton, "Show sequence tracks without a map preview."),
            ("map", self.mapViewButton, "Expand the whole-molecule map."),
        ):
            button.setCheckable(True)
            button.setToolTip(tooltip)
            self._viewGroup.addButton(button)
            button.clicked.connect(
                lambda checked, selectedMode=mode: self._selectUserViewMode(selectedMode, checked)
            )
            bar.addWidget(button)
        self.sidebarToggleButton = QPushButton("Inspector", self)
        self.sidebarToggleButton.setCheckable(True)
        self.sidebarToggleButton.setChecked(True)
        self.sidebarToggleButton.setToolTip("Show or hide the whole-molecule inspector.")
        self.sidebarToggleButton.toggled.connect(self._setSidebarVisible)
        bar.addWidget(self.sidebarToggleButton)
        self.inspectorToggleButton = QPushButton("Selection", self)
        self.inspectorToggleButton.setCheckable(True)
        self.inspectorToggleButton.setToolTip("Show or hide range selection and copy controls.")
        self.inspectorToggleButton.toggled.connect(self._setInspectorVisible)
        bar.addWidget(self.inspectorToggleButton)
        return controls

    def _createSequenceTools(self) -> QWidget:
        tools = QWidget(self.workflows)
        tools.setObjectName("sequenceTools")
        row = QHBoxLayout(tools)
        row.setContentsMargins(2, 5, 14, 5)
        row.setSpacing(6)
        self.goToButton = QPushButton(workspaceIcon("target"), "Go to…", tools)
        self.goToButton.setToolTip("Enter the first and last coordinate in the selection controls.")
        self.goToButton.clicked.connect(self._focusRange)
        row.addWidget(self.goToButton)
        self.digestPanel.overlayButton.setIcon(workspaceIcon("digest"))
        row.addWidget(self.digestPanel.overlayButton)
        self.layersButton = QPushButton(workspaceIcon("layers"), "Layers", tools)
        layersMenu = QMenu("Layers", self.layersButton)
        layersMenu.setObjectName("layersMenu")
        self.featuresCheckBox = QCheckBox("Features")
        self.complementCheckBox = QCheckBox("Complement")
        self.translationsCheckBox = QCheckBox("Translations")
        self.primersCheckBox = QCheckBox("Primers")
        for checkbox in (
            self.featuresCheckBox, self.complementCheckBox, self.translationsCheckBox,
            self.primersCheckBox,
        ):
            action = QWidgetAction(layersMenu)
            action.setDefaultWidget(checkbox)
            layersMenu.addAction(action)
            checkbox.toggled.connect(self._onLayerToggled)
        layersMenu.addSeparator()
        self.mapFeaturesMenu = MapFeaturesMenu(self.controller, layersMenu)
        layersMenu.addMenu(self.mapFeaturesMenu)
        self._updateLayerControls()
        self.layersButton.setMenu(layersMenu)
        row.addWidget(self.layersButton)
        self.compactToolsButton = QPushButton("Tools", tools)
        compactMenu = QMenu(self.compactToolsButton)
        compactMenu.addAction(self.goToButton.icon(), self.goToButton.text(), self._focusRange)
        enzymeButton = self.digestPanel.overlayButton
        enzymeAction = compactMenu.addAction(enzymeButton.icon(), enzymeButton.text(), enzymeButton.click)
        compactMenu.aboutToShow.connect(lambda: enzymeAction.setEnabled(enzymeButton.isEnabled()))
        compactMenu.addMenu(layersMenu)
        self.compactToolsButton.setMenu(compactMenu)
        self.compactToolsButton.setAccessibleName("Sequence tools")
        self.compactToolsButton.hide()
        row.addWidget(self.compactToolsButton)
        zoom, self.textSizeSpinBox = sequenceTextSizeControl(tools)
        self.textSizeSpinBox.valueChanged.connect(self.sequenceView.setTextSize)
        row.addWidget(zoom)
        self.copyFormatsButton = QPushButton(workspaceIcon("copy", onAccent=True), "Copy…", tools)
        self.copyFormatsButton.setObjectName("primary")
        self.copyFormatsButton.setAccessibleName("Copy sequence formats")
        self.copyFormatsButton.setToolTip("Preview and copy the selected sequence in another format.")
        self.copyFormatsButton.clicked.connect(self._showCopyFormats)
        row.addWidget(self.copyFormatsButton)
        return tools

    def _focusRange(self) -> None:
        self.inspectorToggleButton.setChecked(True)
        self.details.start.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.details.start.selectAll()

    def _createSidebar(self) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("sequenceInspector")
        sidebar.setMinimumWidth(160)
        sidebar.setMaximumWidth(480)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.mapCard)
        self.inspectorScroll = QScrollArea(sidebar)
        self.inspectorScroll.setObjectName("moleculeInspectorScroll")
        self.inspectorScroll.setAccessibleName("Digest and feature inspector")
        self.inspectorScroll.setWidgetResizable(True)
        self.inspectorScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sections = QWidget()
        sections.setObjectName("inspectorSections")
        sectionsLayout = QVBoxLayout(sections)
        sectionsLayout.setContentsMargins(14, 10, 14, 8)
        sectionsLayout.setSpacing(12)
        self.digestPanel = DigestPanel(self.controller, sections)
        sectionsLayout.addWidget(self.digestPanel)
        divider = QFrame(sections)
        divider.setObjectName("sectionDivider")
        divider.setFixedHeight(1)
        sectionsLayout.addWidget(divider)
        self.features = FeaturePanel(self.controller, sections)
        sectionsLayout.addWidget(self.features, 1)
        self.inspectorScroll.setWidget(sections)
        layout.addWidget(self.inspectorScroll, 1)
        return sidebar

    def _createProductNameBar(self) -> QWidget:
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 0, 4, 0)
        label = QLabel("Construct name", bar)
        self.productName = QLineEdit(bar)
        self.productName.setAccessibleName(label.text())
        label.setBuddy(self.productName)
        self.saveProduct = QPushButton("Save construct", bar)
        self.cancelProduct = QPushButton("Cancel", bar)
        self.productName.textChanged.connect(
            lambda name: self.saveProduct.setEnabled(bool(name.strip()))
        )
        self.saveProduct.clicked.connect(
            lambda: self.documentDialogs.saveAsNew(inlineName=self.productName.text())
        )
        self.productName.returnPressed.connect(self.saveProduct.click)
        self.cancelProduct.clicked.connect(self.controller.cancelProductAdoption)
        for widget in (label, self.productName, self.saveProduct, self.cancelProduct):
            row.addWidget(widget)
        bar.hide()
        return bar

    def _onWorkflowChanged(self) -> None:
        workflow = self.workflows.currentWidget()
        inSequence = workflow is self.sequencePage
        tools = self._workflowTools.get(workflow)
        if inSequence and self.controller.session.current is None:
            tools = None
        if self.workflows.cornerWidget() is not tools:
            previous = self.workflows.cornerWidget()
            if previous is not None:
                previous.hide()
            self.workflows.setCornerWidget(tools)
            if tools is not None:
                tools.show()
        self.recordControls.setVisible(inSequence and self.controller.session.current is not None)
        self.exportFigureButton.setVisible(inSequence and self.controller.session.current is not None)
        self.importInfoButton.setVisible(inSequence and self.controller.session.current is not None)

    def _showSequence(self) -> None:
        self.workflows.setCurrentWidget(self.sequencePage)

    def _showAssembly(self) -> None:
        self.workflows.setCurrentWidget(self.assemblyView)

    def _updateCopyFormats(self) -> None:
        session = self.controller.session
        revision, selected = session.current, session.selection
        hasSelection = revision is not None and selected is not None and len(selected) > 0
        self.copyFormatsButton.setEnabled(hasSelection)
        self.selectionStatus.setVisible(hasSelection)
        if hasSelection:
            feature = revision.findFeature(selected.featureId)
            cdsTranslation = self.controller.cdsForFeature(feature.featureId) if feature else None
            summary = selectionSummary(revision, selected, feature, cdsTranslation)
            countText = selectionCountText(revision, selected)
            self.selectionStatus.setText(f"Selected {summary.rangeText}  ·  {countText}")
            self.selectionStatus.setToolTip(summary.text)
        else:
            self.selectionStatus.clear()
            self.selectionStatus.setToolTip("")
        self._updateRecordSubtitle()

    def _updateRecordSubtitle(self) -> None:
        self.subtitle.setVisible(
            self.selectionStatus.isHidden() or self.width() >= self._COMPACT_BREAKPOINT,
        )

    def _showCopyFormats(self) -> None:
        selection = self.controller.session.selection
        if self._copyDialog is not None or selection is None or not len(selection):
            return
        dialog = SequenceCopyDialog(self.controller, self)
        self._copyDialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self._onCopyFormatsClosed)
        dialog.open()

    def _onCopyFormatsClosed(self) -> None:
        if self.sender() is self._copyDialog:
            self._copyDialog = None

    def _activateFromStrip(self, documentId: str) -> None:
        self.controller.selectDocument(documentId)
        if self.workflows.currentWidget() in (self.alignmentView, self.assemblyView, self.gelView):
            self._showSequence()

    def _onDigestCompleted(self, digestion: SavedDigestion) -> None:
        if self.workflows.currentWidget() is self.sequencePage and QApplication.activeModalWidget() is None:
            self._showDigestion(digestion)
            return
        self._reportDigestOutcome(digestion)

    def _onDigestRerun(self, originalId: str, digestion: SavedDigestion) -> None:
        dialog = self._digestDialog
        if (dialog is not None and dialog is QApplication.activeModalWidget()
                and dialog.digestion.result.operationId == originalId):
            self._showDigestion(digestion)
        else:
            self._reportDigestOutcome(digestion)

    def _reportDigestOutcome(self, digestion: SavedDigestion) -> None:
        run = digestion.result
        message = digestOutcomeText(run)
        active = self.controller.session.current
        if (active is not None and active.revisionId == run.template.revisionId
                and self.controller.workspace.digestions.get(run.operationId) is not None):
            message += " Open the result in History."
        self.statusBar().showMessage(message)

    def _showDigestion(self, digestion) -> None:
        if self._digestDialog is not None:
            self._digestDialog.close()
        dialog = DigestDialog(self.controller, digestion, self)
        self._digestDialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.openSourceRequested.connect(self.controller.analysis.openSource)
        dialog.showGelRequested.connect(lambda: self.workflows.setCurrentWidget(self.gelView))
        dialog.showSequenceRequested.connect(self._showSequence)
        dialog.assemblyRequested.connect(self._offerResultToAssembly)
        dialog.finished.connect(self._onDigestClosed)
        dialog.show()

    def _onDigestClosed(self) -> None:
        if self.sender() is self._digestDialog:
            self._digestDialog = None

    def _onPcrCompleted(self, run: PcrRun) -> None:
        if run.status != "completed":
            return
        if self.workflows.currentWidget() is self.pcrView and QApplication.activeModalWidget() is None:
            self._showRecordedPcr(run, None)
        else:
            self.statusBar().showMessage(f"{self.controller.pcr.statusMessage} Open the result in PCR or History.")

    def _showRecordedPcr(self, run: PcrRun, outputKey: str | None) -> None:
        if run.status != "completed":
            self.statusBar().showMessage(f"PCR search {run.status}. Only completed runs have a gel preview.")
            return
        view = self.controller.pcr.viewForRun(run.operationId)
        if outputKey is not None:
            view = replace(view, selectedOutputKey=outputKey)
        self._showPcrResult(run, view, persistView=True)

    def _showPinnedPcr(self, run: PcrRun, view: GelViewSettings) -> None:
        self._showPcrResult(run, view, persistView=False)

    def _showPcrResult(self, run: PcrRun, view: GelViewSettings, *, persistView: bool) -> None:
        if self._pcrDialog is not None:
            self._pcrDialog.close()
        dialog = PcrResultDialog(self.controller, run, view, self, persistView=persistView)
        self._pcrDialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.showGelRequested.connect(lambda: self.workflows.setCurrentWidget(self.gelView))
        dialog.showBindingRequested.connect(self._showPcrBinding)
        dialog.usePrimerPairRequested.connect(self._reusePrimerPair)
        dialog.assemblyRequested.connect(self._offerResultToAssembly)
        previewProduct = partial(self.pcrView.previewRunProduct, run.operationId)
        dialog.productSelected.connect(previewProduct)
        dialog.finished.connect(self._onPcrClosed)
        previewProduct(dialog.selectedProduct)
        dialog.show()

    def _offerResultToAssembly(
        self, selectedKey: str | None, output: PcrProduct | DNAFragment | None,
    ) -> None:
        """Leave the result dialog open if the Assembly picker rejects its selected product."""
        dialog = self.sender()
        if dialog is None or (dialog is not self._pcrDialog and dialog is not self._digestDialog):
            return
        try:
            if (selectedKey is None or not isinstance(output, (PcrProduct, DNAFragment))
                    or selectedKey != output.outputKey):
                raise ValueError("The selected row does not match its recorded preparation output.")
            if dialog is self._pcrDialog and isinstance(output, PcrProduct):
                self.controller.pcr.prepareAssembly(dialog.run.operationId, output, self.assemblyView.stagePcr)
            elif dialog is self._digestDialog and isinstance(output, DNAFragment):
                self.assemblyView.stageDigest(dialog.digestion.result, output)
            else:
                raise ValueError("The selected output has the wrong preparation type.")
        except (ValueError, KeyError, OSError, sqlite3.Error) as error:
            dialog.status.setText(str(error))
            dialog.status.show()
            self.controller.operationFailed.emit(str(error))
            return
        self._showAssembly()
        dialog.accept()

    def _reusePrimerPair(self, run: PcrRun) -> None:
        origin = self.sender()
        parent = origin if isinstance(origin, PcrResultDialog) else self
        current = self.controller.session.current
        if current is None or current.moleculeType != "DNA":
            self.statusBar().showMessage("Open a DNA template in the primer editor first.")
            return
        if applyPairToEditor(self.controller, run.forwardPrimer, run.reversePrimer, parent,
                             otherTemplate=current.revisionId != run.template.revisionId):
            if isinstance(origin, PcrResultDialog):
                origin.accept()
            self.workflows.setCurrentWidget(self.pcrView)
            self.pcrView.forwardAnnealing.setFocus()
            self.statusBar().showMessage(f"Loaded the recorded pair on {self.controller.displayName}.")

    def _showPcrBinding(self, product: PcrProduct, role: str) -> None:
        self.workflows.setCurrentWidget(self.pcrView)
        self.pcrView.showProduct(product, role)

    def _onPcrClosed(self) -> None:
        if self.sender() is self._pcrDialog:
            self._pcrDialog = None

    def _chooseDigestion(self) -> None:
        project = self.controller.workspace.project
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open saved digestion", "", "strandfolio digestion (*.vibedigest *.VIBEDIGEST)",
        )
        if filename and self.controller.workspace.project is project:
            self.controller.analysis.loadDigestion(Path(filename))

    def _createSequenceWorkspace(self) -> QWidget:
        workspace = QWidget(self)
        workspace.setObjectName("sequenceWorkspace")
        workspace.setMinimumWidth(340)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(4, 0, 4, 2)
        layout.setSpacing(2)
        self.sequenceView = SequenceView(self.controller, workspace)
        self.overview = SequenceOverview(self.controller, self.sequenceView, workspace)
        layout.addWidget(self.overview)
        layout.addWidget(self.sequenceView, 1)
        return workspace

    def _createMapCard(self) -> QFrame:
        mapCard = QFrame(self)
        mapCard.setObjectName("sequenceMapCard")
        mapLayout = QVBoxLayout(mapCard)
        mapLayout.setContentsMargins(1, 1, 1, 1)
        self.plasmidMap = PlasmidMapView(self.controller, mapCard)
        self.plasmidMap.canvas.setMinimumSize(100, 138)
        self.plasmidMap.expandButton.clicked.connect(self._toggleExpandedMap)
        self.sequenceView.visibleIntervalChanged.connect(self.plasmidMap.setVisibleInterval)
        mapLayout.addWidget(self.plasmidMap)
        return mapCard

    def _toggleExpandedMap(self) -> None:
        if self._activeViewMode == "map":
            self._applyViewMode(self._manualTrackMode or self._automaticTrackMode())
        else:
            self.mapViewButton.click()

    def _setSidebarVisible(self, visible: bool) -> None:
        self.sidebar.setVisible(visible)

    def _setInspectorVisible(self, visible: bool) -> None:
        self.details.setVisible(visible)
        self.translationControls.setVisible(visible)

    def _onLayerToggled(self) -> None:
        self.controller.setLayerOptions(
            LayerOptions(
                showFeatures=self.featuresCheckBox.isChecked(),
                showPrimers=self.primersCheckBox.isChecked(),
                showComplement=self.complementCheckBox.isChecked(),
                showTranslations=self.translationsCheckBox.isChecked(),
            )
        )

    def _updateLayerControls(self) -> None:
        """Reflect the controller's shared layer preferences without recursive updates."""
        options = self.controller.layerOptions
        for checkbox, checked in (
            (self.featuresCheckBox, options.showFeatures),
            (self.primersCheckBox, options.showPrimers),
            (self.complementCheckBox, options.showComplement),
            (self.translationsCheckBox, options.showTranslations),
        ):
            with QSignalBlocker(checkbox):
                checkbox.setChecked(checked)

    def _selectUserViewMode(self, mode: str, checked: bool) -> None:
        if checked:
            if mode != "map":
                self._manualTrackMode = mode
            if mode != self._activeViewMode:
                self._applyViewMode(mode)

    def _automaticTrackMode(self) -> str:
        return "split" if self.width() >= self._COMPACT_BREAKPOINT else "sequence"

    def _applyViewMode(self, mode: str) -> None:
        self._activeViewMode = mode
        button = {
            "split": self.splitViewButton,
            "sequence": self.sequenceViewButton,
            "map": self.mapViewButton,
        }[mode]
        with QSignalBlocker(button):
            button.setChecked(True)
        self.plasmidMap.setExpanded(mode == "map")
        if mode == "map":
            if self.canvasStack.indexOf(self.mapCard) < 0:
                self.canvasStack.addWidget(self.mapCard)
            self.mapCard.setMinimumHeight(0)
            self.mapCard.setMaximumHeight(16777215)
            self.canvasStack.setCurrentWidget(self.mapCard)
        else:
            self.canvasStack.setCurrentWidget(self.sequenceWorkspace)
            if self.mapCard.parentWidget() is not self.sidebar:
                self.sidebar.layout().insertWidget(0, self.mapCard)
            self.mapCard.setMinimumHeight(238)
            self.mapCard.setMaximumHeight(244)
            self.mapCard.setVisible(mode == "split")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # The workflow labels stay available on the same row as Copy and text size.
        compactTools = self.width() < 1140
        for button in (self.goToButton, self.digestPanel.overlayButton, self.layersButton):
            button.setVisible(not compactTools)
        self.compactToolsButton.setVisible(compactTools)
        self._updateRecordSubtitle()
        if self._manualTrackMode is None and self._activeViewMode != "map":
            target = self._automaticTrackMode()
            if target != self._activeViewMode:
                self._applyViewMode(target)

    def _createMenu(self) -> None:
        menu = self.menuBar().addMenu("File")
        openAction = menu.addAction("Open sequence…", self.openFileDialog)
        openAction.setShortcut(QKeySequence.StandardKey.Open)
        self.pasteSequenceAction = menu.addAction("Paste sequence…", self._pasteSequenceDialog)
        menu.addAction("Import independent copy…", lambda: self.openFileDialog(independent=True))
        self.openSavedAction = menu.addAction("Open saved sequences…", self.documentDialogs.openSavedDialog)
        self.closeSequenceAction = menu.addAction("Close sequence", self.controller.closeDocument)
        self.closeSequenceAction.setShortcut(QKeySequence.StandardKey.Close)
        menu.addSeparator()
        menu.addAction("New Project…", lambda: self.documentDialogs.openProjectDialog(create=True))
        menu.addAction("Open Project…", self.documentDialogs.openProjectDialog)
        self.closeProjectAction = menu.addAction("Close Project", self.documentDialogs.closeProject)
        self.saveAsNewAction = menu.addAction("Save as New…", self.documentDialogs.saveAsNew)
        menu.addSeparator()
        self.exportSequenceAction = menu.addAction(
            "Export sequence…", partial(exportSavedSequence, self, self.controller),
        )
        self.originalRetrieval = OriginalRetrieval(self, self.controller)
        self.retrieveOriginalAction = menu.addAction("Retrieve original…", self.originalRetrieval.retrieve)
        self.originalRetrieval.changed.connect(self._updateRetrievalAction)
        self.exportFigureAction = menu.addAction("Export sequence figure…", self.exportSequenceFigure)
        self.importDetailsAction = menu.addAction("Import details…", self._showImportDetails)
        menu.addSeparator()
        quitAction = menu.addAction("Quit", self.close)
        quitAction.setShortcut(QKeySequence.StandardKey.Quit)
        quitAction.setMenuRole(QAction.MenuRole.QuitRole)

        self._openMenu = QMenu(self.openButton)
        self._openMenu.addAction("Open sequence…", self.openFileDialog)
        self._openMenu.addAction(self.pasteSequenceAction)
        self._openMenu.addAction(self.openSavedAction)

        annotations = self.controller.annotations
        editMenu = self.menuBar().addMenu("Edit")
        self.undoAnnotationAction = editMenu.addAction("Undo annotation edit", annotations.undo)
        self.undoAnnotationAction.setShortcut(QKeySequence.StandardKey.Undo)
        self.redoAnnotationAction = editMenu.addAction("Redo annotation edit", annotations.redo)
        self.redoAnnotationAction.setShortcut(QKeySequence.StandardKey.Redo)
        editMenu.addSeparator()
        self.discardAnnotationAction = editMenu.addAction(
            "Discard annotation draft…", self._confirmDiscardAnnotationDraft,
        )
        self._updateAnnotationActions()

    def _updateAnnotationActions(self) -> None:
        annotations = self.controller.annotations
        self.undoAnnotationAction.setEnabled(annotations.canUndo)
        self.redoAnnotationAction.setEnabled(annotations.canRedo)
        self.discardAnnotationAction.setEnabled(annotations.canDiscard)
        statusBar = self.statusBar()
        if annotations.status:
            statusBar.showMessage(annotations.status)
        elif self._annotationStatus and statusBar.currentMessage() == self._annotationStatus:
            statusBar.clearMessage()
        self._annotationStatus = annotations.status

    def _confirmDiscardAnnotationDraft(self) -> None:
        annotations = self.controller.annotations
        target = annotations.target()
        if target is None or not annotations.canDiscard:
            return
        if confirmAction(
            self, "Discard annotation draft",
            "Discard the unsaved annotation edits and restore the saved parent?",
            "Discard edits",
        ):
            annotations.discard(target)

    def exportSequenceFigure(self) -> None:
        """Preview a snapshot of the active map or selected sequence tracks for publication."""
        workspace = self.controller.workspace
        document = workspace.activeDocument
        if document is None:
            self.statusBar().showMessage("Open a sequence before exporting a figure.")
            return
        target = workspace.targetFor(document.documentId)
        identity = f"Record {target.recordId} · Revision {target.revisionId}"
        mapSource = self.plasmidMap.figureSource()
        mapScope = FigureScope(
            "Circular map" if mapSource.isCircular else "Linear map",
            f"{mapSource.description}\n{identity}", mapSource.renderSvg, mapSource.defaultWidth,
        )
        detailScopes = []
        for fullRecord, label in ((False, "current region"), (True, "full record")):
            source = self.sequenceView.detailFigure(fullRecord=fullRecord)
            detailScopes.append(FigureScope(
                f"Sequence detail · {label}", f"{source.description}\n{identity}",
                source.renderSvg, source.defaultWidth,
            ))
        scopes = (*detailScopes, mapScope) if self._activeViewMode == "sequence" else (
            mapScope, *detailScopes,
        )
        name = self.controller.displayName
        destination = exportFigure(
            self, self.controller, title=f"Export figure · {name}",
            suggestedStem=f"{Path(name).name or 'sequence'}_figure", scopes=scopes,
            beforePublish=partial(self.controller.checkFigureTarget, target),
        )
        if destination is not None:
            self.feedback.hide()

    def _showImportDetails(self) -> None:
        result = self.controller.importResult
        if result is None:
            self.statusBar().showMessage("Open a sequence first.")
            return
        dialog = ImportDetailsDialog(result, self)
        dialog.exec()
        dialog.deleteLater()

    def _openFromHeader(self) -> None:
        if self.controller.workspace.project is None:
            self.openFileDialog()

    def _openAssemblyFile(self) -> None:
        token = self.assemblyView.pickerToken
        if token is not None:
            self.openFileDialog(assemblyPickerToken=token)

    def _openAssemblySaved(self) -> None:
        token = self.assemblyView.pickerToken
        if token is None:
            return
        revisionId = self.documentDialogs.chooseSavedRevision()
        if revisionId is not None and token == self.assemblyView.pickerToken:
            self.controller.openExactRevision(
                revisionId, None, afterOpen=partial(self.assemblyView.finishSourceIntake, token),
            )

    def openFileDialog(
        self, *, independent: bool = False, assemblyPickerToken: int | None = None,
    ) -> None:
        """Ask for a source path; parsing is delegated to the background intake task."""
        activation = self.controller.activationGeneration
        project = self.controller.workspace.project
        current = self.controller.importResult
        directory = fileDialogDirectory(
            current.sourcePath.parent if current is not None and current.sourcePath else None
        )
        formatFilters = {}
        allPatterns = []
        for label, extensions, formatName in (
            ("GenBank", GENBANK_EXTENSIONS, "genbank"),
            ("FASTA", FASTA_EXTENSIONS, "fasta"),
            ("SnapGene", SNAPGENE_EXTENSIONS, "snapgene"),
            ("Plain text", TEXT_EXTENSIONS, "text"),
        ):
            patterns = [f"*{case}" for ext in extensions for case in (ext, ext.upper())]
            formatFilters[f"{label} ({' '.join(patterns)})"] = formatName
            allPatterns.extend(patterns)
        digestionFilter = "strandfolio digestion (*.vibedigest *.VIBEDIGEST)"
        allowDigestion = not independent and assemblyPickerToken is None
        if allowDigestion:
            allPatterns.extend(("*.vibedigest", "*.VIBEDIGEST"))
        filters = [f"Supported files ({' '.join(allPatterns)})", *formatFilters]
        if allowDigestion:
            filters.append(digestionFilter)
        filters.append("All files (*)")
        filename, selectedFilter = QFileDialog.getOpenFileName(
            self, "Import independent copy" if independent else "Open sequence",
            str(directory), ";;".join(filters),
        )
        if (filename and self.controller.activationGeneration == activation
                and self.controller.workspace.project is project
                and (assemblyPickerToken is None or assemblyPickerToken == self.assemblyView.pickerToken)):
            if allowDigestion and (Path(filename).suffix.lower() == ".vibedigest"
                                   or selectedFilter == digestionFilter):
                self.controller.analysis.loadDigestion(Path(filename))
            else:
                afterIntake = (
                    partial(self.assemblyView.finishSourceIntake, assemblyPickerToken)
                    if assemblyPickerToken is not None else None
                )
                self.controller.loadSequence(
                    filename, formatName=formatFilters.get(selectedFilter), independent=independent,
                    afterIntake=afterIntake,
                )

    def _pasteSequenceDialog(self, *, assemblyPickerToken: int | None = None) -> None:
        """Collect an explicit clipboard capture for the current workspace."""
        activation = self.controller.activationGeneration
        project = self.controller.workspace.project
        dialog = TextMoleculeDialog(None, self)
        def finishPaste(accepted: bool) -> None:
            if (not accepted or dialog.moleculeType is None
                    or self.controller.activationGeneration != activation
                    or self.controller.workspace.project is not project
                    or (assemblyPickerToken is not None
                        and assemblyPickerToken != self.assemblyView.pickerToken)):
                return
            afterIntake = (
                partial(self.assemblyView.finishSourceIntake, assemblyPickerToken)
                if assemblyPickerToken is not None else None
            )
            self.controller.loadPastedText(
                dialog.pastedText, dialog.moleculeType, afterIntake=afterIntake,
            )

        self._showIntakeDialog(dialog, finishPaste)

    def _showIntakeDialog(
        self, dialog: QDialog, onFinished: Callable[[bool], None], *, reviewingImport: bool = False,
    ) -> None:
        """Show one modal prompt without nesting the application's event loop."""
        if self._intakeDialog is not None:
            self._intakeDialog.reject()
        self._intakeDialog = dialog
        self._reviewingImport = reviewingImport

        def finished(result: int) -> None:
            if self._intakeDialog is not dialog:
                return
            self._intakeDialog = None
            self._reviewingImport = False
            onFinished(result == QDialog.DialogCode.Accepted)

        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(finished)
        dialog.open()

    def _reviewBatch(self, requestId: int, capture: SequenceImportResult) -> None:
        self._showIntakeDialog(
            BatchReviewDialog(capture, self), partial(self.controller.finishReview, requestId, capture),
            reviewingImport=True,
        )

    def _chooseTextType(self, requestId: int, source: PlainTextSource) -> None:
        if source.sourcePath is None:
            self.controller.cancelImport()
            return
        dialog = TextMoleculeDialog(source.sourcePath.name, self)

        def choose(accepted: bool) -> None:
            self.controller.chooseTextType(requestId, source, dialog.moleculeType if accepted else None)

        self._showIntakeDialog(dialog, choose, reviewingImport=True)

    def _reviewTextNormalization(
        self, requestId: int, capture: SequenceImportResult, preview: TextNormalization,
    ) -> None:
        self._showIntakeDialog(
            TextNormalizationDialog(capture, preview, self),
            partial(self.controller.finishReview, requestId, capture), reviewingImport=True,
        )

    def _updateCollection(self) -> None:
        workspace = self.controller.workspace
        doc = workspace.activeDocument
        hasDoc = doc is not None
        hasCapture = hasDoc and doc.capture is not None
        self.closeDocumentButton.setEnabled(hasDoc)
        self.closeSequenceAction.setEnabled(hasDoc)
        self.exportFigureButton.setEnabled(hasDoc)
        self.exportFigureAction.setEnabled(hasDoc)
        self.importInfoButton.setEnabled(hasCapture)
        self.importDetailsAction.setEnabled(hasCapture)
        self.saveAsNewAction.setEnabled(hasDoc and doc.isDraft)
        hasProject = workspace.project is not None
        self.exportSequenceAction.setEnabled(hasProject and hasDoc and not doc.isDraft)
        self._updateRetrievalAction()
        self.openSavedAction.setEnabled(hasProject)
        self.closeProjectAction.setEnabled(hasProject)
        self.openButton.setMenu(self._openMenu if hasProject else None)
        self.openButton.setToolTip(
            "Open a sequence, saved digestion or stored project snapshot."
            if hasProject else "Open a sequence or saved digestion."
        )
        self._updateNames()

    def _updateRetrievalAction(self) -> None:
        self.retrieveOriginalAction.setEnabled(self.originalRetrieval.canRetrieve())

    def _updateNames(self) -> None:
        workspace = self.controller.workspace
        self.documentStrip.setDocuments(documentLabels(workspace), workspace.activeDocumentId)
        if workspace.activeDocument is None:
            self.setWindowTitle("strandfolio · Sequence workspace")
            return
        displayName = self.controller.displayName
        self.setWindowTitle(f"{displayName} · strandfolio")

    def _updateDocument(self) -> None:
        revision = self.controller.session.current
        self._updateCopyFormats()
        doc = self.controller.workspace.activeDocument
        documentSwitched = self._displayedDocumentId != self.controller.workspace.activeDocumentId
        self._displayedDocumentId = self.controller.workspace.activeDocumentId
        base = doc.session.baseRevision if doc is not None and doc.hasUnsavedRoot else None
        naming = base is not None and base.outputReference is not None
        self.productNameBar.setVisible(naming)
        if naming and documentSwitched:
            self.productName.clear()
            self.saveProduct.setEnabled(False)
            self.productName.setFocus()
        self.feedback.hide()
        isNucleic = revision is None or revision.moleculeType != "protein"
        self.complementCheckBox.setEnabled(isNucleic)
        self.translationsCheckBox.setEnabled(isNucleic)
        self.sequencePages.setCurrentIndex(0 if revision is None else 1)
        self._onWorkflowChanged()
        if revision is None:
            hasProject = self.controller.workspace.project is not None
            self.subtitle.setText(
                "Open a sequence file or stored project snapshot."
                if hasProject else "Open GenBank, FASTA, SnapGene or plain text to begin."
            )
        else:
            unit = "aa" if revision.moleculeType == "protein" else "nt"
            topology = revision.topology or "topology unknown"
            if revision.moleculeType == "protein":
                topology = "protein"
            draftStatus = ""
            if self.controller.annotations.canUndo:
                draftStatus = " · Annotation draft"
            elif doc is not None and doc.isDraft:
                draftStatus = " · Unsaved draft"
            self.subtitle.setText(
                f"{revision.moleculeType} · {len(revision):,} {unit} · {topology.capitalize()} "
                f"· {len(revision.features):,} features{draftStatus}"
            )

    def _onLoadingChanged(self, loading: bool) -> None:
        self.cancelImportButton.setVisible(loading)
        self.pasteSequenceAction.setEnabled(not loading)
        if loading:
            self.feedback.hide()
        # A new request supersedes a pending Paste prompt. Ending a request
        # dismisses only its review, including cancellation after a tab switch.
        if self._intakeDialog is not None and loading != self._reviewingImport:
            self._intakeDialog.reject()

    def _showImportError(self, message: str) -> None:
        retained = (
            "The current sequence has been preserved."
            if self.controller.session.current is not None
            else "No sequence is open."
        )
        self.feedback.setText(f"Could not open sequence: {message}\n{retained}")
        self.feedback.show()
        self.statusBar().showMessage("Import failed. Check the format and sequence content.")

    def _showOperationError(self, message: str) -> None:
        self.feedback.setText(f"Could not complete the operation: {message}")
        self.feedback.show()
        self.statusBar().showMessage("Workspace operation failed.")

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.controller.refreshRecordPreferences()
            self.historyView.refreshVisibleAnalyses()

    def closeEvent(self, event) -> None:
        if self.documentDialogs.closeDocuments(closeAction="Quit"):
            if self._intakeDialog is not None:
                self._intakeDialog.reject()
            self.controller.cancelImport()
            self.controller.alignment.shutdown()
            self.controller.annotations.shutdown()
            self.controller.orfs.shutdown()
            self.historyView.shutdown()
            super().closeEvent(event)
        else:
            event.ignore()
