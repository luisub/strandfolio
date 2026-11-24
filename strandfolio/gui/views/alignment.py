"""Native pairwise sequence comparisons and source-coordinate inspection."""

from bisect import bisect_right
from dataclasses import dataclass
from html import escape
from textwrap import shorten

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QListView,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QStyle, QStyledItemDelegate, QStyleOptionComboBox, QStylePainter, QVBoxLayout, QWidget,
)

from strandfolio.application.alignment_session import AlignmentSelection
from strandfolio.application.workspace import WorkspaceDocument
from strandfolio.domain.sequence import MoleculeType, SequenceRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.alignment_regions import (
    AlignmentRegionButton, AlignmentRegionDialog, regionCaption, regionIntervals,
)
from strandfolio.gui.panels.document_strip import documentLabels
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.views.alignment_overview import AlignmentOverview
from strandfolio.gui.views.alignment_tracks import (
    AlignmentTracks, formatAlignmentPercent, mappingText,
)
from strandfolio.gui.views.multiple_alignment import MultipleAlignmentDialog
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.operations.alignment import (
    PROTEIN_SUBSTITUTION_MATRIX, AlignmentCodon, AlignmentInput, AlignmentResult, AlignmentSettings,
    alignmentTranslations,
)


_ID_ROLE = Qt.ItemDataRole.UserRole
_KIND_ROLE = int(_ID_ROLE) + 1
_SHORT_LABEL_ROLE = int(_ID_ROLE) + 2
_FRAMES = (("Source frame", "source"), ("Frame +1", 1), ("Frame +2", 2), ("Frame +3", 3),
           ("Frame −1", -1), ("Frame −2", -2), ("Frame −3", -3), ("Off", "off"))
_MODES = {
    "global": ("Global", "Compare both inputs end to end."),
    "local": ("Local", "Find the highest-scoring region shared by the inputs."),
    "fit": ("Find query in reference", "Place the entire query using the selected orientations. "
            "Unused reference flanks are free."),
}


def _label(parent: QWidget, text: str = "", *, muted: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if muted:
        label.setObjectName("muted")
    return label


def _tooltip(*lines: str) -> str:
    return "<p>" + "<br/>".join(escape(line) for line in lines if line) + "</p>"


class _AlignmentChoice(QComboBox):
    """Keep compact selections identifiable while keeping the full menu descriptions."""

    def wheelEvent(self, event) -> None:
        if self.hasFocus() or self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

    def showPopup(self) -> None:
        choices = self.view()
        naturalWidth = choices.sizeHintForColumn(0) + choices.verticalScrollBar().sizeHint().width() + 12
        desktopWidth = self.screen().availableGeometry().width()
        choices.setMinimumWidth(min(desktopWidth, max(self.width(), naturalWidth)))
        super().showPopup()

    def paintEvent(self, event) -> None:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, self,
        )
        rect = QRect(9, 0, max(0, self.width() - arrow.width() - 13), self.height())
        rect = self.style().visualRect(self.layoutDirection(), self.rect(), rect)
        caption = self.currentData(_SHORT_LABEL_ROLE) or option.currentText
        text = self.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, rect.width())
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawItemText(rect, Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter,
                             option.palette, self.isEnabled(), text, QPalette.ColorRole.ButtonText)


class _AlignmentScore(QDoubleSpinBox):
    """Let an unfocused scoring field pass wheel scrolling to the comparison panel."""

    def wheelEvent(self, event) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _combo(parent: QWidget, name: str, objectName: str = "") -> QComboBox:
    combo = _AlignmentChoice(parent)
    combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    combo.setAccessibleName(name)
    combo.setObjectName(objectName)
    combo.setMinimumContentsLength(7)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    choices = combo.view()
    choices.setObjectName("alignmentChoices")
    choices.setAccessibleName(f"{name} choices")
    choices.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    choices.setItemDelegate(QStyledItemDelegate(choices))
    return combo


def _orientation(parent: QWidget, name: str, objectName: str = "") -> QComboBox:
    combo = _combo(parent, name, objectName)
    combo.addItem("Stored orientation (+)", False)
    combo.addItem("Reverse complement (−)", True)
    combo.setFixedWidth(combo.fontMetrics().horizontalAdvance("Rev. comp. (−)") + 30)
    return combo


def _unit(moleculeType: MoleculeType) -> str:
    return "aa" if moleculeType == "protein" else "nt"


def _syncRegionControls(
    role: str, revision: SequenceRevision, selection: AlignmentSelection,
    button: AlignmentRegionButton, orientation: QComboBox, summary: FittedLabel,
) -> None:
    feature = revision.getFeature(selection.featureId) if selection.featureId is not None else None
    segments = tuple((part.start, part.end) for part in feature.parts) if feature is not None else ()
    length = sum(end - start for start, end in segments) if segments else len(revision)
    size = f"{length:,} {_unit(revision.moleculeType)}"
    if feature is None:
        caption = f"Whole record · {size}"
        button.setRegion(role, caption, caption)
        summary.setFullText("Orientation")
        labels = (("Stored orientation (+)", "Stored (+)"), ("Reverse complement (−)", "Rev. comp. (−)"))
    else:
        details = f"{regionIntervals(segments)} · {size}"
        button.setRegion(role, feature.label, f"{feature.label} · {details}")
        summary.setFullText(details)
        same, opposite = ("−", "+") if feature.strand == -1 else ("+", "−")
        labels = ((f"Follow feature strand ({same})", f"Feature ({same})"),
                  (f"Opposite of feature strand ({opposite})", f"Opposite ({opposite})"))
    with QSignalBlocker(orientation):
        for index, (full, short) in enumerate(labels):
            orientation.setItemText(index, full)
            orientation.setItemData(index, short, _SHORT_LABEL_ROLE)
        orientation.setCurrentIndex(int(selection.reverse))
    orientation.setToolTip(orientation.currentText())


def _inputLabel(name: str, source: AlignmentInput) -> str:
    region = f" · {regionCaption(source.featureLabel, source.selection.segments)}"
    if source.moleculeType == "protein":
        return f"{name}{region}"
    strand = "−" if source.strand == -1 else "+"
    orientation = " · Reverse complement" if source.reverseComplement else ""
    return f"{name}{region} · {strand} strand{orientation}"


def _columnSummary(result: AlignmentResult) -> str:
    if not result.alignedLength:
        return "No positive-scoring placement" if result.settings.mode == "fit" else "No aligned columns"
    matched = formatAlignmentPercent(result.matches, result.alignedLength)
    gaps = formatAlignmentPercent(result.gapColumns, result.alignedLength)
    caption = "region identity" if result.settings.mode == "fit" else "matching columns"
    return f"{matched} {caption} · {gaps} gaps"


class _QueryCheck(QCheckBox):
    """Elide a query label while retaining its full accessible name."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._queryText = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setQueryText(self, text: str) -> None:
        self._queryText = text
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        indicator = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, self)
        text = self.fontMetrics().elidedText(
            self._queryText, Qt.TextElideMode.ElideMiddle, max(0, self.width() - indicator - 10),
        )
        self.setText(text)


class _PairMetrics(QWidget):
    """Expose the same comparison measurements visually and as one accessible description."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        self.summary = _label(self)
        self.summary.setObjectName("sectionTitle")
        self.placement = _label(self, muted=True)
        self.placementNote = _label(self, muted=True)
        self.details = _label(self, muted=True)
        for row, label in enumerate((self.summary, self.placement, self.placementNote)):
            grid.addWidget(label, row, 0, 1, 2)
        self._coverage = []
        for column, caption in enumerate(("Reference coverage", "Query coverage")):
            grid.addWidget(_label(self, caption, muted=True), 3, column)
            value = _label(self)
            value.setObjectName("sectionTitle")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            counts = _label(self, muted=True)
            grid.addWidget(value, 4, column)
            grid.addWidget(counts, 5, column)
            self._coverage.append((value, counts))
        grid.addWidget(self.details, 6, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

    def setResult(self, result: AlignmentResult) -> None:
        unit = _unit(result.reference.moleculeType)
        self.summary.setText(_columnSummary(result))
        columns = result.alignedLength
        columnCounts = f"{result.matches:,} match · {result.gapColumns:,} gaps · {columns:,} columns"
        score = f"Score {result.score:g}"
        self.summary.setToolTip(f"{columnCounts}\n{score}" if columns else score)
        placement = ""
        if result.settings.mode == "fit" and columns:
            placement = f"Reference {regionIntervals(result.referenceIntervals)}"
            if unit == "nt":
                strand = "−" if result.reference.strand == -1 else "+"
                placement += f" · {strand} strand"
        self.placement.setText(placement)
        self.placement.setVisible(bool(placement))
        note = "Multiple exact placements" if result.hasRepeatedExactPlacement else ""
        self.placementNote.setText(note)
        self.placementNote.setVisible(bool(note))
        paired = result.pairedLength
        identity = formatAlignmentPercent(result.matches, paired)
        details = f"Paired identity {identity} · {result.matches:,} of {paired:,} {unit} match" if paired else ""
        self.details.setText(details)
        self.details.setVisible(bool(details))
        coverage = []
        for side, source, (value, counts) in zip(("Reference", "Query"),
                                                (result.reference, result.query), self._coverage, strict=True):
            total = len(source)
            value.setText(formatAlignmentPercent(paired, total))
            counts.setText(f"{paired:,} of {total:,} {unit} paired")
            coverage.append(f"{side} coverage {value.text()} · {counts.text()}")
        self.setAccessibleName("\n".join(filter(None, (
            self.summary.text(), placement, note, *coverage, details, columnCounts if columns else "", score,
        ))))


class _DifferencesModel(QAbstractListModel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._result: AlignmentResult | None = None

    def setResult(self, result: AlignmentResult | None) -> None:
        self.beginResetModel()
        self._result = result
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._result.differences) if self._result is not None and not parent.isValid() else 0

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> str | None:
        result = self._result
        if result is None or not index.isValid() or not 0 <= index.row() < len(result.differences):
            return None
        entry = result.differences[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            first = mappingText(result, entry.start)
            if entry.end > entry.start + 1:
                first += f"\n\nLast column\n{mappingText(result, entry.end - 1)}"
            return first
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return None
        column = f"{entry.start + 1:,}"
        if entry.end > entry.start + 1:
            column += f"–{entry.end:,}"
        ref = self._describeChange(result.referenceAligned, entry.start, entry.end)
        query = self._describeChange(result.queryAligned, entry.start, entry.end)
        return f"{entry.kind.capitalize()} · {column} · {ref} → {query}"

    @staticmethod
    def _describeChange(aligned: str, start: int, end: int) -> str:
        if aligned[start] == "-":
            return "—"
        return aligned[start:end] if end - start <= 8 else f"{aligned[start:start + 7]}…"


@dataclass
class _QueryRow:
    widget: QWidget
    check: _QueryCheck
    length: QLabel
    region: AlignmentRegionButton
    orientation: QComboBox
    summary: FittedLabel
    notice: QLabel


class AlignmentView(QWidget):
    """Select open revisions of the same molecule type and inspect each comparison."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.alignment = controller.alignment
        self._shown = False
        self._names: dict[str, str] = {}
        self._queries: dict[str, _QueryRow] = {}
        self.queryChecks: dict[str, QCheckBox] = {}
        self.queryOrientations: dict[str, QComboBox] = {}
        self._referenceChoices: tuple[tuple[str, str], ...] = ()
        self._regionDialog: AlignmentRegionDialog | None = None
        self._resultRows: tuple = ()
        self.multipleDialog: MultipleAlignmentDialog | None = None
        self._result: AlignmentResult | None = None
        self._columnsByResult: dict[int, int] = {}
        self._differenceStarts: tuple[int, ...] = ()
        self._selectedDifference = -1
        self._codonsByColumn: tuple[dict[int, AlignmentCodon], ...] = ()
        self._differenceModel = _DifferencesModel(self)
        self.setObjectName("alignmentWorkspace")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        stage = QSplitter(Qt.Orientation.Horizontal, self)
        stage.setObjectName("analysisPanels")
        stage.setHandleWidth(12)
        stage.setChildrenCollapsible(False)
        stage.addWidget(self._createCanvas(stage))
        stage.addWidget(self._createInspector(stage))
        stage.setStretchFactor(0, 1)
        stage.setStretchFactor(1, 1)
        stage.setSizes([1100, 302])
        layout.addWidget(stage)
        self.alignment.changed.connect(self._refresh)
        self._showResult(None)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._shown:
            self._shown = True
            self.alignment.initialize()
            self._refresh()

    def hideEvent(self, event) -> None:
        if self._regionDialog is not None:
            self._regionDialog.reject()
        super().hideEvent(event)

    def _createCanvas(self, parent: QWidget) -> QWidget:
        canvas = QWidget(parent)
        canvas.setObjectName("workflowCanvas")
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(12, 10, 12, 10)
        title = _label(canvas, "Aligned sequences")
        title.setObjectName("documentTitle")
        heading = QHBoxLayout()
        heading.addWidget(title)
        heading.addStretch(1)
        self._createTranslationControls(canvas, heading)
        layout.addLayout(heading)
        self.pairLabel = FittedLabel("", canvas)
        self.pairLabel.setObjectName("muted")
        self.pairLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.pairLabel)
        self._createTrackControls(canvas, layout)
        self.emptyLabel = _label(canvas, "Choose a reference and queries to compare.", muted=True)
        self.emptyLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.emptyLabel, 1)
        self.tracks = AlignmentTracks(canvas)
        self.tracks.columnPicked.connect(self._selectColumn)
        self.tracks.visibleColumnsChanged.connect(self._showVisibleColumns)
        self.overview.panRequested.connect(self.tracks.panToColumn)
        self.overview.stepRequested.connect(self.tracks.panStep)
        layout.addWidget(self.tracks, 1)
        self.mappingLabel = _label(canvas)
        self.mappingLabel.setObjectName("alignmentMapping")
        self.mappingLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.mappingLabel)
        return canvas

    def _createTrackControls(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self._trackControls = QWidget(parent)
        controls = QVBoxLayout(self._trackControls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(3)
        headings = QHBoxLayout()
        headings.addWidget(_label(self._trackControls, "Alignment overview"))
        headings.addStretch(1)
        self.visibleColumnsLabel = _label(self._trackControls, muted=True)
        self.visibleColumnsLabel.setWordWrap(False)
        headings.addWidget(self.visibleColumnsLabel)
        controls.addLayout(headings)
        self.overview = AlignmentOverview(self._trackControls)
        controls.addWidget(self.overview)
        bar = QHBoxLayout()
        self.columnSelector = QSpinBox(self._trackControls)
        self.columnSelector.setObjectName("alignmentColumn")
        self.columnSelector.setAccessibleName("Go to alignment column")
        self.columnSelector.setKeyboardTracking(False)
        self.columnSelector.setGroupSeparatorShown(True)
        self.columnSelector.valueChanged.connect(lambda value: self._selectColumn(value - 1))
        columnLabel = _label(self._trackControls, "Go to column")
        columnLabel.setBuddy(self.columnSelector)
        bar.addWidget(columnLabel)
        bar.addWidget(self.columnSelector)
        bar.addStretch(1)
        self.differenceCount = _label(self._trackControls)
        bar.addWidget(self.differenceCount)
        for text, direction, shortcut in (("Previous", -1, "Alt+Up"), ("Next", 1, "Alt+Down")):
            button = QPushButton(text, self._trackControls)
            name = f"{text} difference"
            button.setAccessibleName(name)
            button.setToolTip(f"{name} ({shortcut})")
            button.clicked.connect(lambda checked=False, step=direction: self._moveDifference(step))
            key = QShortcut(QKeySequence(shortcut), self)
            key.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            key.activated.connect(lambda step=direction: self._moveDifference(step))
            bar.addWidget(button)
            if direction < 0:
                self.previousDifferenceButton = button
            else:
                self.nextDifferenceButton = button
        controls.addLayout(bar)
        layout.addWidget(self._trackControls)

    def _createTranslationControls(self, parent: QWidget, layout: QHBoxLayout) -> None:
        self.translationSelector = _combo(parent, "Translation", "alignmentTranslation")
        self.translationSelector.setToolTip(
            "Source frame follows the feature's coding offset and annotated CDS genetic code. "
            "Whole records use +1. Explicit frames use the standard genetic code."
        )
        for text, value in _FRAMES:
            self.translationSelector.addItem(text, value)
        self.translationSelector.currentIndexChanged.connect(lambda: self._updateTranslation(reveal=False))
        self.translationLabel = _label(parent, "Translation")
        self.translationLabel.setBuddy(self.translationSelector)
        layout.addWidget(self.translationLabel)
        layout.addWidget(self.translationSelector)

    def _showVisibleColumns(self, first: int, end: int) -> None:
        text = f"Showing columns {first + 1:,}–{end:,} of {self._result.alignedLength:,}" if end > first else ""
        self.visibleColumnsLabel.setText(text)
        self.overview.setVisibleRange(first, end, text)

    def _createInspector(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setObjectName("alignmentPanel")
        panel.setMinimumWidth(299)
        panel.setMaximumWidth(370)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        panel.setStyleSheet("""
            QFrame#alignmentPanel { background: #faf9fe; border: 1px solid #e2dcef; border-radius: 10px; }
            QFrame#alignmentActions { background: transparent; border: none; border-bottom: 1px solid #e5dfef; }
            QScrollArea#analysisInspector { background: transparent; border: 0; padding: 0; }
            QFrame#alignmentQueries, QFrame#alignmentSummary, QFrame#alignmentScoring {
                background: white; border: 1px solid #ddd6ea; border-radius: 7px; }
            QCheckBox#alignmentQuery:checked { color: #442e74; font-weight: 600; }
            QCheckBox#alignmentQuery::indicator:unchecked {
                width: 13px; height: 13px; border: 1px solid #a79ab6;
                border-radius: 3px; background: white; }
            QPushButton#alignmentSettings { background: transparent; color: #554177;
                text-align: left; border: 1px solid transparent; padding: 2px 4px; }
            QPushButton#alignmentSettings:hover, QPushButton#alignmentSettings:focus {
                border-color: #c7b4e7; background: #f0ebfa; }
            QPushButton#alignmentRun, QPushButton#alignmentCancel { min-height: 24px; padding: 4px 9px; }
            QPushButton#alignmentRunMultiple { background: white; border: 1px solid #cfc0e3;
                color: #513975; min-height: 22px; padding: 3px 9px; }
            QPushButton#alignmentRunMultiple:hover, QPushButton#alignmentRunMultiple:focus {
                background: #eee8fa; border-color: #8465b6; }
            QPushButton#alignmentRunMultiple:disabled { background: #f4f2f7;
                border-color: #e0dce6; color: #82798b; }
            QListView#alignmentDifferences::item, QListWidget#alignmentResults::item {
                padding: 3px 5px; border-bottom: 1px solid #e9e5f0; }
            QListView#alignmentDifferences::item:selected, QListWidget#alignmentResults::item:selected {
                background: #eee8fc; color: #412671; border-left: 3px solid #6940c7; }
        """)
        column = QVBoxLayout(panel)
        column.setContentsMargins(2, 2, 2, 2)
        column.setSpacing(0)
        header = QFrame(panel)
        header.setObjectName("alignmentActions")
        actions = QVBoxLayout(header)
        actions.setContentsMargins(14, 10, 14, 8)
        actions.setSpacing(5)
        heading = _label(header, "Compare sequences")
        heading.setObjectName("sectionTitle")
        actions.addWidget(heading)
        self.modeSelector = _combo(header, "Pairwise mode", "alignmentMode")
        for mode, (caption, helpText) in _MODES.items():
            self.modeSelector.addItem(caption, mode)
            self.modeSelector.setItemData(self.modeSelector.count() - 1, helpText, Qt.ItemDataRole.ToolTipRole)
        self.modeSelector.currentIndexChanged.connect(self._changeSettings)
        actions.addWidget(self.modeSelector)

        self.runButton = QPushButton("Compare with reference", header)
        self.runButton.setIcon(workspaceIcon("play", onAccent=True))
        self.runButton.setIconSize(QSize(14, 14))
        self.runButton.setObjectName("alignmentRun")
        self.runButton.setToolTip("Run an independent pairwise alignment for each checked query.")
        self.runButton.clicked.connect(self.alignment.runAlignment)
        actions.addWidget(self.runButton)
        self.cancelButton = QPushButton("Cancel pairwise comparison", header)
        self.cancelButton.setObjectName("alignmentCancel")
        self.cancelButton.setAccessibleName("Cancel pairwise alignment")
        self.cancelButton.clicked.connect(self.alignment.cancelAlignment)
        self.cancelButton.hide()
        actions.addWidget(self.cancelButton)
        self.multipleButton = QPushButton("Build multiple alignment", header)
        self.multipleButton.setObjectName("alignmentRunMultiple")
        self.multipleButton.setIcon(workspaceIcon("layers"))
        self.multipleButton.setIconSize(QSize(14, 14))
        self.multipleButton.setToolTip(
            "Align the reference and two to eight checked queries in shared columns using Kalign."
        )
        self.multipleButton.clicked.connect(self._openMultipleAlignment)
        actions.addWidget(self.multipleButton)
        self.multipleHint = _label(header, muted=True)
        actions.addWidget(self.multipleHint)
        column.addWidget(header)

        scroll = QScrollArea(panel)
        scroll.setObjectName("analysisInspector")
        scroll.setAccessibleName("Alignment inputs and results")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspector = QWidget(scroll)
        inspector.setObjectName("workflowInspector")
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(14, 8, 14, 12)
        self._createInputs(inspector, layout)
        self._createSettings(inspector, layout)
        self.statusLabel = _label(inspector)
        self.statusLabel.setObjectName("alignmentStatus")
        layout.addWidget(self.statusLabel)
        self._createResultControls(inspector, layout)
        layout.addStretch(1)
        scroll.setWidget(inspector)
        column.addWidget(scroll, 1)
        return panel

    def _createInputs(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self.referenceSelector = _combo(parent, "Reference sequence", "alignmentReference")
        self.referenceSelector.currentIndexChanged.connect(self._changeReference)
        referenceLabel = _label(parent, "Reference sequence")
        referenceLabel.setBuddy(self.referenceSelector)
        layout.addWidget(referenceLabel)
        layout.addWidget(self.referenceSelector)
        self.referenceRegionButton = AlignmentRegionButton(parent)
        self.referenceRegionButton.setObjectName("alignmentReferenceRegion")
        self.referenceRegionButton.clicked.connect(lambda: self._chooseRegion())
        layout.addWidget(self.referenceRegionButton)
        self.referenceOrientationSelector = _orientation(
            parent, "Reference orientation", "alignmentReferenceOrientation",
        )
        self.referenceOrientationSelector.currentIndexChanged.connect(self._changeReferenceOrientation)
        options = QHBoxLayout()
        self.referenceRegionSummary = FittedLabel("", parent)
        self.referenceRegionSummary.setObjectName("muted")
        options.addWidget(self.referenceRegionSummary, 1)
        options.addWidget(self.referenceOrientationSelector)
        layout.addLayout(options)
        self.referenceNotice = _label(parent, muted=True)
        layout.addWidget(self.referenceNotice)
        compareLabel = _label(parent, "Queries")
        layout.addWidget(compareLabel)
        self.queryTable = QFrame(parent)
        self.queryTable.setAccessibleName("Queries")
        self.queryTable.setObjectName("alignmentQueries")
        self._queryLayout = QVBoxLayout(self.queryTable)
        self._queryLayout.setContentsMargins(7, 4, 7, 4)
        self._queryLayout.setSpacing(4)
        layout.addWidget(self.queryTable)

    def _createSettings(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self.settingsButton = QPushButton("▸  Pairwise settings", parent)
        self.settingsButton.setAccessibleName("Pairwise settings")
        self.settingsButton.setObjectName("alignmentSettings")
        self.settingsButton.setCheckable(True)
        layout.addWidget(self.settingsButton)
        panel = QFrame(parent)
        panel.setObjectName("alignmentScoring")
        panel.hide()
        self.settingsButton.toggled.connect(panel.setVisible)
        self.settingsButton.toggled.connect(lambda opened: self.settingsButton.setText(
            f"{'▾' if opened else '▸'}  Pairwise settings"
        ))
        form = QFormLayout(panel)
        self._settingsForm = form
        form.setContentsMargins(10, 8, 10, 8)
        form.setHorizontalSpacing(9)
        form.setVerticalSpacing(5)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.matrixLabel = _label(panel, PROTEIN_SUBSTITUTION_MATRIX)
        self.matrixLabel.setToolTip("B, Z, X and * have matrix scores. J, O and U have no scores.")
        form.addRow("Matrix", self.matrixLabel)
        self.matchScore = self._createScore(form, "Match", "alignmentMatchScore", 0, 1000)
        self.mismatchScore = self._createScore(form, "Mismatch", "alignmentMismatchScore", -1000, 0)
        self.gapOpenScore = self._createScore(form, "Gap open", "alignmentGapOpenScore", -1000, 0)
        self.gapExtendScore = self._createScore(form, "Gap extension", "alignmentGapExtendScore", -1000, 0)
        layout.addWidget(panel)

    def _createScore(
        self, form: QFormLayout, text: str, objectName: str, minimum: int, maximum: int,
    ) -> QDoubleSpinBox:
        score = _AlignmentScore(form.parentWidget())
        score.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        score.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        score.setObjectName(objectName)
        score.setAccessibleName(f"{text} score")
        score.setRange(minimum, maximum)
        score.setDecimals(3)
        score.setKeyboardTracking(False)
        score.valueChanged.connect(self._changeSettings)
        form.addRow(text, score)
        return score

    def _createResultControls(self, parent: QWidget, layout: QVBoxLayout) -> None:
        self.resultLabel = _label(parent, "Results")
        self.resultLabel.setObjectName("sectionTitle")
        layout.addWidget(self.resultLabel)
        self.resultList = QListWidget(parent)
        self.resultList.setObjectName("alignmentResults")
        self.resultList.setAccessibleName("Comparison results")
        self.resultList.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.resultList.currentItemChanged.connect(self._selectResult)
        layout.addWidget(self.resultList)
        self._metricsPanel = QFrame(parent)
        self._metricsPanel.setObjectName("alignmentSummary")
        metrics = QVBoxLayout(self._metricsPanel)
        metrics.setContentsMargins(10, 8, 10, 8)
        metrics.setSpacing(5)
        self.metricsLabel = _PairMetrics(self._metricsPanel)
        metrics.addWidget(self.metricsLabel)
        self.settingsLabel = _label(self._metricsPanel, muted=True)
        self.settingsLabel.setObjectName("alignmentAppliedSettings")
        self.settingsLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        metrics.addWidget(self.settingsLabel)
        layout.addWidget(self._metricsPanel)
        self.retainButton = QPushButton("Keep for this session", parent)
        self.retainButton.setObjectName("alignmentRetain")
        self.retainButton.setToolTip("Keep both exact input revisions and settings for this session.")
        self.retainButton.clicked.connect(lambda: self.alignment.retainResult())
        layout.addWidget(self.retainButton)
        self.differenceLabel = _label(parent)
        self.differenceLabel.setObjectName("sectionTitle")
        layout.addWidget(self.differenceLabel)
        self.differencesList = QListView(parent)
        self.differencesList.setObjectName("alignmentDifferences")
        self.differencesList.setAccessibleName("Sequence differences")
        self.differencesList.setUniformItemSizes(True)
        self.differencesList.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.differencesList.setModel(self._differenceModel)
        self.differencesList.selectionModel().currentChanged.connect(self._differenceChanged)
        layout.addWidget(self.differencesList)

    def _createQuery(self, documentId: str) -> _QueryRow:
        widget = QWidget(self.queryTable)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        check = _QueryCheck(widget)
        check.setObjectName("alignmentQuery")
        check.toggled.connect(lambda checked: self.alignment.setQuery(documentId, checked))
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(check, 1)
        length = QLabel(widget)
        length.setObjectName("muted")
        heading.addWidget(length)
        layout.addLayout(heading)
        region = AlignmentRegionButton(widget)
        region.clicked.connect(lambda: self._chooseRegion(documentId))
        layout.addWidget(region)
        orientation = _orientation(widget, "Query orientation")
        orientation.currentIndexChanged.connect(lambda: self._changeQueryOrientation(documentId))
        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        summary = FittedLabel("", widget)
        summary.setObjectName("muted")
        options.addWidget(summary, 1)
        options.addWidget(orientation)
        layout.addLayout(options)
        notice = _label(widget, muted=True)
        layout.addWidget(notice)
        self.queryChecks[documentId] = check
        self.queryOrientations[documentId] = orientation
        return _QueryRow(widget, check, length, region, orientation, summary, notice)

    def _changeReference(self) -> None:
        self.alignment.setReference(self.referenceSelector.currentData())

    def _changeReferenceOrientation(self) -> None:
        selection = self.alignment.reference
        if selection is not None:
            self.alignment.setReference(selection.documentId, selection.featureId,
                                        self.referenceOrientationSelector.currentData())

    def _changeQueryOrientation(self, documentId: str) -> None:
        selection = self._regionSelection(documentId)
        if selection is not None:
            self.alignment.setQueryRegion(documentId, selection.featureId,
                                          self._queries[documentId].orientation.currentData())

    def _regionSelection(self, queryId: str | None) -> AlignmentSelection | None:
        if queryId is None:
            return self.alignment.reference
        return next((choice for choice in self.alignment.queries if choice.documentId == queryId), None)

    def _chooseRegion(self, queryId: str | None = None) -> None:
        selection = self._regionSelection(queryId)
        if selection is None or self._regionDialog is not None:
            return
        document = next(doc for doc in self.alignment.eligibleDocuments if doc.documentId == selection.documentId)
        revision = document.revision
        role = "reference" if queryId is None else "query"
        dialog = self._regionDialog = AlignmentRegionDialog(
            role, self._names[selection.documentId], revision, selection.featureId, self,
        )

        def finish(code: int) -> None:
            self._regionDialog = None
            dialog.deleteLater()
            if code != QDialog.DialogCode.Accepted or self._regionSelection(queryId) != selection:
                return
            if not any(doc.documentId == selection.documentId and doc.revision is revision
                       for doc in self.alignment.eligibleDocuments):
                return
            setter = self.alignment.setReference if queryId is None else self.alignment.setQueryRegion
            setter(selection.documentId, dialog.featureId, selection.reverse)

        dialog.finished.connect(finish)
        dialog.open()

    def _syncRegionNotice(self, label: QLabel, documentId: str | None) -> None:
        lost = self.alignment.lostRegion(documentId) if documentId is not None else None
        label.setText(f"{lost} is unavailable. Using the whole record in stored orientation." if lost is not None else "")
        label.setVisible(lost is not None)

    def _changeSettings(self) -> None:
        self.alignment.setSettings(AlignmentSettings(
            mode=self.modeSelector.currentData(), matchScore=self.matchScore.value(),
            mismatchScore=self.mismatchScore.value(), openGapScore=self.gapOpenScore.value(),
            extendGapScore=self.gapExtendScore.value(),
        ))

    def _syncInputs(self) -> None:
        state = self.alignment
        documents = state.eligibleDocuments
        self._names = {label.documentId: label.qualifiedName
                       for label in documentLabels(self.controller.workspace)}
        referenceId = state.reference.documentId if state.reference is not None else None
        reference = next((doc for doc in documents if doc.documentId == referenceId), None)
        choices = tuple((doc.documentId, f"{self._names[doc.documentId]} · {doc.revision.moleculeType}")
                        for doc in documents)
        with QSignalBlocker(self.referenceSelector):
            if choices != self._referenceChoices:
                self.referenceSelector.clear()
                self.referenceSelector.addItem("Choose a reference…", None)
                for documentId, name in choices:
                    self.referenceSelector.addItem(name, documentId)
                self._referenceChoices = choices
            self.referenceSelector.setCurrentIndex(self.referenceSelector.findData(referenceId))
            self.referenceSelector.setEnabled(bool(choices))
        self.referenceSelector.setToolTip(_tooltip(self.referenceSelector.currentText()))
        if reference is not None:
            _syncRegionControls("reference", reference.revision, state.reference,
                                self.referenceRegionButton, self.referenceOrientationSelector,
                                self.referenceRegionSummary)
        canChooseRegion = reference is not None and state.referenceMoleculeType != "protein"
        self.referenceRegionButton.setEnabled(canChooseRegion)
        self.referenceRegionButton.setVisible(reference is not None)
        self.referenceOrientationSelector.setVisible(canChooseRegion)
        self.referenceRegionSummary.setVisible(canChooseRegion)
        self._syncRegionNotice(self.referenceNotice, referenceId)
        usesMatrix = state.referenceMoleculeType == "protein"
        self._settingsForm.setRowVisible(self.matrixLabel, usesMatrix)
        self._settingsForm.setRowVisible(self.matchScore, not usesMatrix)
        self._settingsForm.setRowVisible(self.mismatchScore, not usesMatrix)
        self._syncQueries(tuple(doc for doc in documents if doc is not reference))

    def _syncQueries(self, documents: tuple[WorkspaceDocument, ...]) -> None:
        selections = {selection.documentId: selection for selection in self.alignment.queries}
        atLimit = len(selections) >= self.alignment.maxQueries
        ids = {doc.documentId for doc in documents}
        for documentId in tuple(self._queries):
            if documentId not in ids:
                row = self._queries.pop(documentId)
                self._queryLayout.removeWidget(row.widget)
                row.widget.hide()
                row.widget.deleteLater()
                del self.queryChecks[documentId], self.queryOrientations[documentId]
        for index, document in enumerate(documents):
            documentId = document.documentId
            row = self._queries.get(documentId)
            if row is None:
                row = self._queries[documentId] = self._createQuery(documentId)
                self._queryLayout.insertWidget(index, row.widget)
            selection = selections.get(documentId)
            name = self._names[documentId]
            moleculeType = document.revision.moleculeType
            queryText = f"{name} · {moleculeType} · {len(document.revision):,} {_unit(moleculeType)}"
            row.check.setQueryText(name)
            row.length.setText(f"{len(document.revision):,} {_unit(moleculeType)}")
            row.length.setToolTip(_tooltip(queryText))
            row.check.setAccessibleName(f"Compare with {name}")
            reason = ""
            if self.alignment.reference is None:
                reason = "Choose a reference sequence first."
            elif moleculeType != self.alignment.referenceMoleculeType:
                reason = f"Choose a matching {self.alignment.referenceMoleculeType} query."
            elif atLimit and selection is None:
                reason = f"Choose up to {self.alignment.maxQueries} queries per run."
            row.check.setAccessibleDescription(reason or queryText)
            row.check.setToolTip(_tooltip(queryText, reason))
            row.check.setEnabled(not reason)
            row.orientation.setAccessibleName(f"Orientation of {name}")
            with QSignalBlocker(row.check):
                row.check.setChecked(selection is not None)
            if selection is not None:
                _syncRegionControls(f"query for {name}", document.revision, selection,
                                    row.region, row.orientation, row.summary)
            row.region.setEnabled(moleculeType != "protein")
            row.region.setVisible(selection is not None)
            row.orientation.setVisible(selection is not None and moleculeType != "protein")
            row.summary.setVisible(selection is not None and moleculeType != "protein")
            self._syncRegionNotice(row.notice, documentId)

    def _refresh(self) -> None:
        if not self._shown:
            return
        self._syncInputs()
        state = self.alignment
        settings = state.settings
        with QSignalBlocker(self.modeSelector):
            self.modeSelector.setCurrentIndex(self.modeSelector.findData(settings.mode))
        self.modeSelector.setToolTip(_MODES[settings.mode][1])
        for spin, value in ((self.matchScore, settings.matchScore),
                            (self.mismatchScore, settings.mismatchScore),
                            (self.gapOpenScore, settings.openGapScore),
                            (self.gapExtendScore, settings.extendGapScore)):
            with QSignalBlocker(spin):
                spin.setValue(value)
        busy = bool(state.pendingQueryIds)
        transferToCancel = busy and self.runButton.hasFocus()
        transferToRun = state.canRun and self.cancelButton.hasFocus()
        self.runButton.setVisible(not busy)
        self.runButton.setEnabled(state.canRun)
        self.multipleButton.setEnabled(state.canRunMultiple)
        self.multipleHint.setText("One shared matrix." if state.canRunMultiple
                                  else "One matrix · Select at least two queries.")
        self.cancelButton.setEnabled(busy)
        self.cancelButton.setVisible(busy)
        if transferToCancel:
            self.cancelButton.setFocus()
        elif transferToRun:
            self.runButton.setFocus()
        retained = state.retainedComparison
        live = state.activeResult if retained is None else None
        alreadyKept = any(item.result is live for item in state.retainedComparisons)
        self.retainButton.setVisible(live is not None or retained is not None)
        self.retainButton.setEnabled(live is not None and not alreadyKept)
        self.retainButton.setText("Kept for this session" if retained is not None or alreadyKept
                                  else "Keep for this session")
        self._syncResults()
        self._syncStatus()
        if state.activeResult is not self._result:
            self._showResult(state.activeResult)
        if state.activeResult is not None:
            self._setPairLabel(state.activeResult)

    def _openMultipleAlignment(self) -> None:
        if self.multipleDialog is not None:
            self.multipleDialog.raise_()
            self.multipleDialog.activateWindow()
            return
        if not self.alignment.canRunMultiple:
            return
        dialog = MultipleAlignmentDialog(self.controller, self.window())
        self.multipleDialog = dialog
        dialog.finished.connect(self._closeMultipleAlignment)
        dialog.show()

    def _closeMultipleAlignment(self) -> None:
        if self.multipleDialog is not None:
            self.multipleDialog.deleteLater()
            self.multipleDialog = None

    def _syncStatus(self) -> None:
        state = self.alignment
        errors = tuple(filter(None, (state.errorFor(query.documentId) for query in state.queries)))
        if not state.eligibleDocuments:
            status = "Open two DNA, RNA or protein records of the same type."
        elif state.reference is None:
            status = "Choose a reference sequence."
        elif not any(box.isEnabled() for box in self.queryChecks.values()):
            status = f"Open another {state.referenceMoleculeType} sequence to compare."
        elif not state.queries:
            status = "Check at least one query."
        elif state.pendingQueryIds:
            count = len(state.pendingQueryIds)
            status = f"Aligning {count} {'query' if count == 1 else 'queries'}…"
        elif len(errors) == 1:
            status = str(errors[0])
        elif errors:
            status = "Review the unsuccessful comparisons in Results."
        elif state.activeResult is None:
            status = "Use Compare with reference to run the selected pairs."
        else:
            status = ""
        self.statusLabel.setText(status)
        self.statusLabel.setVisible(bool(status))
        result = state.activeResult
        prompt = status or "Choose a completed comparison in Results."
        if result is not None and result.alignedLength == 0:
            prompt = ("No positive-scoring query placement." if result.settings.mode == "fit"
                      else "No positive-scoring alignment span.")
        self.emptyLabel.setText(prompt)

    def _syncResults(self) -> None:
        state = self.alignment
        rows = []
        available = set()
        hasLive = False
        for selection in state.queries if state.reference is not None else ():
            documentId = selection.documentId
            name = self._names.get(documentId, documentId)
            result = state.resultFor(documentId)
            error = state.errorFor(documentId)
            if result is not None:
                rows.append(("live", documentId, self._resultText(name, result), True))
                available.add(id(result))
                hasLive = True
            elif documentId in state.pendingQueryIds:
                rows.append(("live", documentId, f"{name} · Running…", False))
            elif error:
                rows.append(("live", documentId, f"{name}\n{error}", False))
            else:
                rows.append(("live", documentId, f"{name} · Ready to run", False))
        if state.retainedComparison is not None and not hasLive:
            rows.append(("liveReset", "", "Live results", True))
        for kept in state.retainedComparisons:
            result = kept.result
            label = f"Kept · {_inputLabel(kept.referenceName, result.reference)} ↔ {kept.queryName}"
            rows.append(("retained", kept.comparisonId, self._resultText(label, result), True))
            available.add(id(result))
        self._columnsByResult = {key: column for key, column in self._columnsByResult.items() if key in available}
        self._displayResultRows(tuple(rows))
        showList = bool(rows) and (len(rows) > 1 or not hasLive or bool(state.retainedComparisons))
        self.resultLabel.setVisible(showList)
        self.resultList.setVisible(showList)

    @staticmethod
    def _resultText(name: str, result: AlignmentResult) -> str:
        if result.query.featureLabel:
            name += f" · {regionCaption(result.query.featureLabel, result.query.selection.segments)}"
        return f"{name}\n{len(result.query):,} {_unit(result.query.moleculeType)} · {_columnSummary(result)}"

    def _displayResultRows(self, rows: tuple) -> None:
        state = self.alignment
        retained = state.retainedComparison
        selected = (("retained", retained.comparisonId) if retained is not None
                    else ("live", state.activeQueryId))
        with QSignalBlocker(self.resultList):
            if rows != self._resultRows:
                self.resultList.clear()
                for kind, key, text, canSelect in rows:
                    item = QListWidgetItem(text, self.resultList)
                    item.setData(_ID_ROLE, key)
                    item.setData(_KIND_ROLE, kind)
                    item.setToolTip(_tooltip(*text.splitlines()))
                    if not canSelect:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self._resultRows = rows
                height = 4 + sum(self.resultList.sizeHintForRow(i) for i in range(min(len(rows), 4)))
                self.resultList.setFixedHeight(min(126, height))
            selectedRow = next((i for i, (kind, key, _, enabled) in enumerate(rows)
                                if enabled and (kind, key) == selected), -1)
            self.resultList.setCurrentRow(selectedRow)

    def _selectResult(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None or not (current.flags() & Qt.ItemFlag.ItemIsSelectable):
            return
        kind = current.data(_KIND_ROLE)
        if kind == "retained":
            self.alignment.selectRetained(current.data(_ID_ROLE))
        elif kind == "liveReset":
            self.alignment.selectRetained(None)
        else:
            self.alignment.selectResult(current.data(_ID_ROLE))

    def _showResult(self, result: AlignmentResult | None) -> None:
        self._result = result
        available = result is not None
        hasColumns = available and result.alignedLength > 0
        for widget in (self.pairLabel, self._metricsPanel):
            widget.setVisible(available)
        for widget in (
            self._trackControls, self.tracks, self.mappingLabel, self.differenceLabel,
        ):
            widget.setVisible(hasColumns)
        self.columnSelector.setEnabled(hasColumns)
        self.emptyLabel.setVisible(not hasColumns)
        hasDifferences = available and bool(result.differences)
        self.differencesList.setVisible(hasDifferences)
        for widget in (self.differenceCount, self.nextDifferenceButton, self.previousDifferenceButton):
            widget.setVisible(hasDifferences)
        with QSignalBlocker(self.differencesList.selectionModel()):
            self._differenceModel.setResult(result)
        self.overview.setResult(result)
        self._selectedDifference = -1
        self._differenceStarts = () if result is None else tuple(event.start for event in result.differences)
        if result is None:
            self.pairLabel.setFullText("")
            self.metricsLabel.setAccessibleName("")
            for label in (self.settingsLabel, self.mappingLabel, self.differenceLabel):
                label.clear()
            self._updateTranslation()
            return
        self._setPairSummary(result)
        count = len(result.differences)
        differenceType = "residue" if result.reference.moleculeType == "protein" else "nucleotide"
        if not hasColumns:
            self.differenceLabel.clear()
        elif count:
            self.differenceLabel.setText(f"Differences · {count:,}")
        else:
            self.differenceLabel.setText(f"No {differenceType} differences.")
        if count:
            self.differencesList.setFixedHeight(4 + self.differencesList.sizeHintForRow(0) * min(8, count))
        first = self._differenceStarts[0] if count else 0
        column = self._columnsByResult.get(id(result), first)
        with QSignalBlocker(self.columnSelector):
            self.columnSelector.setRange(int(hasColumns), result.alignedLength)
            self.columnSelector.setValue(column + 1 if hasColumns else 0)
        self._updateTranslation()

    def _setPairLabel(self, result: AlignmentResult) -> None:
        state = self.alignment
        kept = state.retainedComparison
        if kept is not None:
            referenceName, queryName = kept.referenceName, kept.queryName
        else:
            referenceName = self._names.get(state.reference.documentId, result.reference.name)
            queryName = self._names.get(state.activeQueryId, result.query.name)
        reference = _inputLabel(referenceName, result.reference)
        query = _inputLabel(queryName, result.query)
        self.pairLabel.setFullText(f"{reference} ↔ {query}")
        self.pairLabel.setToolTip(_tooltip(
            reference, f"Reference revision {result.reference.revisionId}",
            query, f"Query revision {result.query.revisionId}",
        ))

    def _setPairSummary(self, result: AlignmentResult) -> None:
        moleculeType = result.reference.moleculeType
        self.metricsLabel.setResult(result)
        settings = result.settings
        scoring = result.substitutionMatrix or (
            f"Match {settings.matchScore:g} · Mismatch {settings.mismatchScore:g}"
        )
        self.settingsLabel.setText(
            f"{moleculeType} · {_MODES[settings.mode][0]} · {scoring} · "
            f"Gap open {settings.openGapScore:g} · Gap extension {settings.extendGapScore:g}"
        )
        normalization = ("Compared in uppercase with U preserved." if moleculeType == "RNA"
                         else "Compared in uppercase.")
        self.settingsLabel.setToolTip(
            f"{normalization} First optimal traceback.\n{result.engineVersion}\n{result.algorithm}"
        )

    def _updateTranslation(self, *, reveal: bool = True) -> None:
        result = self._result
        canTranslate = result is not None and result.alignedLength > 0 and result.reference.moleculeType != "protein"
        self.translationLabel.setVisible(canTranslate)
        self.translationSelector.setVisible(canTranslate)
        frame = self.translationSelector.currentData()
        translations = alignmentTranslations(result, frame) if canTranslate and frame != "off" else None
        self._codonsByColumn = () if translations is None else tuple(
            {index: codon for codon in side if codon is not None for index in codon.footprint}
            for side in translations
        )
        self.tracks.setResult(result, translations)
        self._selectColumn(self.columnSelector.value() - 1, reveal=reveal)

    def _selectColumn(self, index: int, *, reveal: bool = True) -> None:
        result = self._result
        if result is None or not 0 <= index < result.alignedLength:
            self.mappingLabel.clear()
            self.mappingLabel.setToolTip("")
            self.mappingLabel.setAccessibleDescription("")
            self.tracks.setAccessibleDescription("Use the alignment column field to read both source locations.")
            self.tracks.setColumn(0, None, reveal=False)
            self.overview.setSelected(-1)
            self._selectedDifference = -1
            self._syncDifference()
            return
        with QSignalBlocker(self.columnSelector):
            self.columnSelector.setValue(index + 1)
        self._columnsByResult[id(result)] = index
        eventIndex = bisect_right(self._differenceStarts, index) - 1
        startEnd = None
        if eventIndex >= 0:
            event = result.differences[eventIndex]
            if index < event.end:
                startEnd = event.start, event.end
        self._selectedDifference = eventIndex if startEnd is not None else -1
        self._syncDifference()
        self.overview.setSelected(index)
        self.tracks.setColumn(index, startEnd, reveal=reveal)
        lines = mappingText(result, index).splitlines()
        details = lines.copy()
        if self._codonsByColumn:
            frame = self.translationSelector.currentData()
            for side, source in enumerate((result.reference, result.query)):
                codons = self._codonsByColumn[side]
                tableId = source.translationTable(frame)
                if tableId is None:
                    reason = source.sourceTranslationProblem or "No source genetic code was recorded."
                    prefix = f"{lines[side]} · Translation unavailable. "
                    details[side] = prefix + reason
                    lines[side] = prefix + shorten(
                        reason, width=56, placeholder=" …", break_long_words=False, break_on_hyphens=False,
                    )
                    continue
                lines[side] += f" · Genetic code {tableId}"
                codon = codons.get(index)
                if codon is not None:
                    lines[side] += f" · {codon.triplet} → {codon.aminoAcid} · {codon.effect.capitalize()}"
                    if len(codon.assignments) > 1:
                        lines[side] += " · Possible " + "/".join(sorted(codon.assignments))
                details[side] = lines[side]
        fullText = "\n".join(details)
        self.mappingLabel.setText("\n".join(lines))
        self.mappingLabel.setToolTip(_tooltip(*details) if lines != details else "")
        self.mappingLabel.setAccessibleDescription(fullText if lines != details else "")
        self.tracks.setAccessibleDescription(f"Column {index + 1:,}\n{fullText}")

    def _syncDifference(self) -> None:
        count = len(self._differenceStarts)
        selected = self._selectedDifference
        control = self.differencesList.selectionModel()
        with QSignalBlocker(control):
            index = self._differenceModel.index(selected, 0) if selected >= 0 else QModelIndex()
            self.differencesList.setCurrentIndex(index)
        self.differenceCount.setText(f"Difference {selected + 1:,} of {count:,}" if selected >= 0 else f"{count:,} differences")
        canMove = count > 1 or (count == 1 and selected == -1)
        self.previousDifferenceButton.setEnabled(canMove)
        self.nextDifferenceButton.setEnabled(canMove)

    def _differenceChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        if current.isValid():
            self._selectDifference(current.row())

    def _selectDifference(self, index: int) -> None:
        if 0 <= index < len(self._differenceStarts):
            self._selectColumn(self._differenceStarts[index])
            self.differencesList.scrollTo(self._differenceModel.index(index, 0))

    def _moveDifference(self, direction: int) -> None:
        count = len(self._differenceStarts)
        if count:
            selected = self._selectedDifference
            if selected == -1:
                current = self.columnSelector.value() - 1
                selected = bisect_right(self._differenceStarts, current)
                if direction > 0:
                    selected -= 1
            self._selectDifference((selected + direction) % count)
