"""Native translation controls and a read-only sequence conversion preview."""

from PySide6.QtCore import QMimeData, QModelIndex, QSignalBlocker, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.operations.conversion import (
    CdsTranslation,
    ConversionPreview,
    FrameTranslation,
    geneticCodeOptions,
    previewConversion,
)

_FRAME_OPTIONS = (1, 2, 3, -1, -2, -3)
_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1
_SEARCH_HELP = "Search by table ID, name, organism group or compartment."


def _frameNumber(frame: int) -> str:
    return f"{'+' if frame > 0 else '−'}{abs(frame)}"


def _strandName(strand: int | None) -> str:
    return {1: "Forward strand", -1: "Reverse strand"}.get(strand, "Strand unavailable")


def translationInterpretation(result: CdsTranslation | FrameTranslation) -> str:
    """Describe the effective code and translation source consistently across the UI."""
    if isinstance(result, CdsTranslation):
        offset = result.codonStart if result.codonStart is not None else "unavailable"
        context = f"{_strandName(result.strand)} · /codon_start {offset}"
    else:
        context = f"Frame {_frameNumber(result.frame)} · {_strandName(result.strand)}"
    if result.tableSource == "override":
        table = f"Table {result.translTable} (preview) · annotated table {result.annotatedTable}"
    elif result.tableSource == "annotation":
        table = (
            f"Table {result.annotatedTable} (annotation)" if result.annotatedTable is not None
            else "Annotated genetic code unavailable"
        )
    elif result.tableSource == "document":
        table = f"Table {result.translTable} (document default)"
    else:
        table = f"Table {result.translTable} (standard default)"
    return f"{context} · {table}"


def _addFrames(combo: QComboBox) -> None:
    for frame in _FRAME_OPTIONS:
        combo.addItem(f"{_frameNumber(frame)} · {_strandName(frame // abs(frame))}", frame)


class _CodeCompleter(QCompleter):
    def pathFromIndex(self, index: QModelIndex) -> str:
        return index.data(Qt.ItemDataRole.DisplayRole)


class _SequenceCopyPreview(QPlainTextEdit):
    """Lay out long previews in bounded blocks and copy the selected letters exactly."""

    _LINE_WIDTH = 64

    def setSequence(self, sequence: str) -> None:
        # QTextDocument's viewport layout works on paragraphs. A single huge
        # block can stall display even when only its first few letters are visible.
        self.setPlainText("\n".join(
            sequence[start:start + self._LINE_WIDTH]
            for start in range(0, len(sequence), self._LINE_WIDTH)
        ))

    def createMimeDataFromSelection(self) -> QMimeData:
        mime = super().createMimeDataFromSelection()
        # Materialize Qt's lazy text payload before replacing it with letters only.
        mime.data("text/plain")
        for mimeType in mime.formats():
            mime.removeFormat(mimeType)
        mime.setText(self.textCursor().selectedText().replace("\u2029", ""))
        return mime


class SequenceTranslationControls(QFrame):
    """Per-document preview settings, available below the Sequence inspector."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 7, 12, 7)
        outer.setSpacing(4)
        controls = QHBoxLayout()
        outer.addLayout(controls)

        codeLabel = QLabel("Genetic code", self)
        self.geneticCodeCombo = QComboBox(self)
        self.geneticCodeCombo.setObjectName("geneticCodeCombo")
        self.geneticCodeCombo.setEditable(True)
        self.geneticCodeCombo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.geneticCodeCombo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.geneticCodeCombo.setMinimumContentsLength(23)
        self.geneticCodeCombo.setAccessibleName(codeLabel.text())
        self.geneticCodeCombo.setToolTip(_SEARCH_HELP)
        self.geneticCodeCombo.lineEdit().setClearButtonEnabled(True)
        self.geneticCodeCombo.lineEdit().setPlaceholderText("Search table names or IDs")
        codeLabel.setBuddy(self.geneticCodeCombo)
        self.geneticCodeCombo.addItem("Use annotated codes", None)
        self.geneticCodeCombo.setItemData(0, "Use annotated codes", _SEARCH_ROLE)
        self._codeOptions = {option.tableId: option for option in geneticCodeOptions()}
        for option in self._codeOptions.values():
            label = f"{option.tableId} · {option.name}"
            index = self.geneticCodeCombo.count()
            self.geneticCodeCombo.addItem(label, option.tableId)
            aliases = ", ".join(option.aliases)
            self.geneticCodeCombo.setItemData(index, f"{label} {aliases}", _SEARCH_ROLE)
            reason = f"\n{option.unavailableReason}" if option.unavailableReason else ""
            self.geneticCodeCombo.setItemData(index, f"{label}\n{aliases}{reason}", Qt.ItemDataRole.ToolTipRole)
        completer = _CodeCompleter(self.geneticCodeCombo.model(), self.geneticCodeCombo)
        completer.setCompletionRole(_SEARCH_ROLE)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.geneticCodeCombo.setCompleter(completer)
        self.geneticCodeCombo.lineEdit().editingFinished.connect(self._restoreCodeText)
        self.geneticCodeCombo.currentIndexChanged.connect(self._selectCode)
        controls.addWidget(codeLabel)
        controls.addWidget(self.geneticCodeCombo, 1)

        self.overrideAnnotatedCheckBox = QCheckBox("Preview this code for annotated CDSs", self)
        self.overrideAnnotatedCheckBox.setObjectName("overrideAnnotatedCode")
        self.overrideAnnotatedCheckBox.setToolTip("Use the selected table for preview while retaining each CDS annotation.")
        self.overrideAnnotatedCheckBox.toggled.connect(self._toggleOverride)
        controls.addWidget(self.overrideAnnotatedCheckBox)

        frameLabel = QLabel("Translation track", self)
        self.frameCombo = QComboBox(self)
        self.frameCombo.setObjectName("sequenceTranslationFrame")
        self.frameCombo.setAccessibleName(frameLabel.text())
        self.frameCombo.addItem("Annotated CDS / +1", None)
        self.frameCombo.setToolTip("Use frame +1 when the record has no CDS annotations. Free frames start at the full record’s 5′ end on the chosen strand.")
        _addFrames(self.frameCombo)
        frameLabel.setBuddy(self.frameCombo)
        self.frameCombo.currentIndexChanged.connect(self._selectFrame)
        controls.addSpacing(8)
        controls.addWidget(frameLabel)
        controls.addWidget(self.frameCombo)

        self.interpretation = QLabel(self)
        self.interpretation.setObjectName("muted")
        self.interpretation.setTextFormat(Qt.TextFormat.PlainText)
        self.interpretation.setWordWrap(True)
        outer.addWidget(self.interpretation)

        controller.documentChanged.connect(self.refresh)
        controller.translationChanged.connect(self.refresh)
        controller.selectionChanged.connect(self._updateInterpretation)
        self.refresh()

    def _selectCode(self, index: int) -> None:
        if index >= 0 and not self.controller.setGeneticCodePreference(self.geneticCodeCombo.itemData(index)):
            self.refresh()

    def _toggleOverride(self, enabled: bool) -> None:
        if not self.controller.setGeneticCodePreference(self.controller.geneticCodePreference.tableId, enabled):
            self.refresh()

    def _selectFrame(self, index: int) -> None:
        if index >= 0 and not self.controller.setTranslationFrame(self.frameCombo.itemData(index)):
            self.refresh()

    def _restoreCodeText(self) -> None:
        index = self.geneticCodeCombo.currentIndex()
        if index >= 0:
            self.geneticCodeCombo.setEditText(self.geneticCodeCombo.itemText(index))
        else:
            self.geneticCodeCombo.setEditText(f"{self.controller.geneticCodePreference.tableId} · Unavailable")

    def refresh(self) -> None:
        revision = self.controller.session.current
        isNucleic = revision is not None and revision.moleculeType in ("DNA", "RNA")
        preference = self.controller.geneticCodePreference
        codeIndex = self.geneticCodeCombo.findData(preference.tableId)
        with QSignalBlocker(self.geneticCodeCombo), QSignalBlocker(self.overrideAnnotatedCheckBox), QSignalBlocker(self.frameCombo):
            self.geneticCodeCombo.setCurrentIndex(codeIndex)
            self._restoreCodeText()
            self.overrideAnnotatedCheckBox.setChecked(preference.overrideAnnotated)
            self.frameCombo.setCurrentIndex(self.frameCombo.findData(self.controller.translationFrame))
        self.geneticCodeCombo.setToolTip(f"{self.geneticCodeCombo.currentText()}\n{_SEARCH_HELP}")
        self.geneticCodeCombo.setEnabled(isNucleic)
        self.overrideAnnotatedCheckBox.setEnabled(isNucleic and preference.tableId is not None)
        self.frameCombo.setEnabled(isNucleic)
        self._updateInterpretation()

    def _updateInterpretation(self) -> None:
        session = self.controller.session
        revision = session.current
        selected = session.selection
        cds = self.controller.cdsForFeature(selected.featureId) if selected and selected.featureId else None
        frame = self.controller.frameTranslation
        preference = self.controller.geneticCodePreference
        if revision is None or revision.moleculeType == "protein":
            text = "Genetic codes apply to DNA and RNA." if revision is not None else "Open DNA or RNA to choose a genetic code."
        elif frame is not None:
            text = translationInterpretation(frame)
            if frame.warnings:
                text += " · " + " ".join(frame.warnings)
        elif cds is not None:
            text = self._selectedCdsInterpretation(cds)
        elif preference.tableId is None:
            text = "Each CDS uses its annotated table. Table 1 is the default for unannotated CDSs and free frames."
        else:
            text = f"Table {preference.tableId} applies to free frames and "
            text += "all CDS previews." if preference.overrideAnnotated else "CDSs without an annotated table."
            option = self._codeOptions.get(preference.tableId)
            if option is None:
                text += f" Genetic code {preference.tableId} is unavailable."
            elif option.unavailableReason:
                text += f" {option.unavailableReason}"
        if frame is not None and cds is not None:
            text += f"\n{self._selectedCdsInterpretation(cds)}"
        self.interpretation.setText(text)

    @staticmethod
    def _selectedCdsInterpretation(cds: CdsTranslation) -> str:
        text = f"Selected CDS {cds.label} · {translationInterpretation(cds)}"
        if not cds.isValid and cds.warnings:
            text += f" · {cds.warnings[0]}"
        return text


class _CopyFormatDelegate(QStyledItemDelegate):
    """Draw the format name and strand direction over the native selection and focus."""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(2 * QFontMetrics(option.font).height() + 8)
        return size

    def paint(self, painter, option, index):
        style = QStyleOptionViewItem(option)
        self.initStyleOption(style, index)
        name = style.text
        style.text = ""
        focused = bool(style.state & QStyle.StateFlag.State_HasFocus)
        style.state &= ~QStyle.StateFlag.State_HasFocus
        style.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, style, painter, style.widget)
        bounds = style.rect.adjusted(9, 3, -8, -3)
        selected = bool(style.state & QStyle.StateFlag.State_Selected)
        roles = QPalette.ColorRole
        painter.save()
        if focused:
            painter.setPen(style.palette.color(roles.Link))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(style.rect.adjusted(0, 0, -1, -1), 4, 4)
        nameFont = QFont(style.font)
        nameFont.setWeight(QFont.Weight.DemiBold)
        painter.setFont(nameFont)
        painter.setPen(style.palette.color(roles.Link if selected else roles.Text))
        name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, bounds.width())
        painter.drawText(bounds, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, name)
        painter.setFont(style.font)
        painter.setPen(style.palette.color(roles.Link if selected else roles.ButtonText))
        painter.drawText(bounds, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                         index.data(Qt.ItemDataRole.AccessibleDescriptionRole))
        painter.restore()


class SequenceCopyDialog(QDialog):
    """Preview and copy selected conversions while keeping the dialog open."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("sequenceCopyDialog")
        self.setWindowTitle("Copy sequence")
        self.resize(760, 425)

        revision = controller.session.current
        selection = controller.session.selection
        molecule = revision.moleculeType
        isNucleic = molecule in ("DNA", "RNA")
        unit = "nt" if isNucleic else "aa"
        intervals = ", ".join(f"{start + 1:,}–{end:,}" for start, end in selection.segments)

        layout = dialogLayout(self)
        context = QLabel(f"{controller.displayName} · {intervals} · {len(selection):,} {unit} selected", self)
        context.setObjectName("muted")
        context.setTextFormat(Qt.TextFormat.PlainText)
        context.setWordWrap(True)
        layout.addWidget(context)

        body = QHBoxLayout()
        body.setSpacing(14)
        layout.addLayout(body, 1)
        formatColumn = QVBoxLayout()
        body.addLayout(formatColumn)
        formatLabel = QLabel("&Format", self)
        formatLabel.setObjectName("sectionTitle")
        formatColumn.addWidget(formatLabel)
        self.formats = QListWidget(self)
        self.formats.setObjectName("sequenceCopyFormats")
        self.formats.setAccessibleName("Copy format")
        self.formats.setItemDelegate(_CopyFormatDelegate(self.formats))
        self.formats.setFixedWidth(176)
        formatLabel.setBuddy(self.formats)
        formatColumn.addWidget(self.formats, 1)
        self._addFormat("stored", f"Stored {molecule}")
        if isNucleic:
            self._addFormat("complement", "Complement")
            self._addFormat("reverse", "Reverse complement")
            if molecule == "DNA":
                self._addFormat("rna", "Transcribed RNA")
            else:
                self._addFormat("dna", "Back-transcribed DNA")
            self._addFormat("protein", "Translated protein")

        previewColumn = QVBoxLayout()
        body.addLayout(previewColumn, 1)
        previewLabel = QLabel("Preview", self)
        previewLabel.setObjectName("sectionTitle")
        previewColumn.addWidget(previewLabel)
        self._translationControls = QWidget(self)
        sourceRow = QHBoxLayout(self._translationControls)
        sourceRow.setContentsMargins(0, 0, 0, 0)
        sourceLabel = QLabel("Translate", self._translationControls)
        self.translationSource = QComboBox(self._translationControls)
        self.translationSource.setObjectName("sequenceCopyTranslationSource")
        self.translationSource.setAccessibleName("Protein translation source")
        self.translationSource.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        sourceLabel.setBuddy(self.translationSource)
        sourceRow.addWidget(sourceLabel)
        sourceRow.addWidget(self.translationSource, 1)
        previewColumn.addWidget(self._translationControls)
        candidates = controller.copyTranslationFeatures
        for feature in candidates:
            self.translationSource.addItem(f"{feature.label} · {_strandName(feature.strand)}", feature.featureId)
        _addFrames(self.translationSource)
        initialSource = controller.translationFrame
        if initialSource is None:
            selectedIndex = self.translationSource.findData(selection.featureId)
            if selectedIndex >= 0:
                initialSource = selection.featureId
            else:
                initialSource = candidates[0].featureId if candidates else 1
        self.translationSource.setCurrentIndex(self.translationSource.findData(initialSource))

        self.preview = _SequenceCopyPreview(self)
        self.preview.setObjectName("sequenceCopyPreview")
        self.preview.setFont(workspaceMonoFont(13))
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview.setReadOnly(True)
        previewColumn.addWidget(self.preview, 1)
        self.interpretation = QLabel(self)
        self.interpretation.setObjectName("sequenceCopyInterpretation")
        self.interpretation.setTextFormat(Qt.TextFormat.PlainText)
        self.interpretation.setWordWrap(True)
        previewColumn.addWidget(self.interpretation)

        actions = QHBoxLayout()
        self._feedback = QLabel(self)
        self._feedback.setTextFormat(Qt.TextFormat.PlainText)
        self._feedback.setWordWrap(True)
        self.closeButton = QPushButton("Close", self)
        self.closeButton.clicked.connect(self.reject)
        actions.addWidget(self.closeButton)
        actions.addWidget(self._feedback, 1)
        self.copyButton = QPushButton("Copy", self)
        self.copyButton.setObjectName("primary")
        self.copyButton.setDefault(True)
        self.copyButton.clicked.connect(self._copy)
        actions.addWidget(self.copyButton)
        addDialogFooter(layout, actions, self.copyButton)

        self._result: ConversionPreview
        self.formats.currentRowChanged.connect(self._refresh)
        self.translationSource.currentIndexChanged.connect(self._refresh)
        controller.translationChanged.connect(self._refresh)
        controller.documentChanged.connect(self.reject)
        controller.selectionChanged.connect(self.reject)
        self.formats.setCurrentRow(0)

    def _addFormat(self, key: str, label: str) -> None:
        item = QListWidgetItem(label, self.formats)
        item.setData(Qt.ItemDataRole.UserRole, key)
        direction = previewConversion(self.controller.session.current, None, key).orientation
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, direction)

    def _refresh(self) -> None:
        item = self.formats.currentItem()
        if item is None:
            return
        copyFormat = item.data(Qt.ItemDataRole.UserRole)
        protein = copyFormat == "protein"
        self._translationControls.setVisible(protein)
        source = self.translationSource.currentData()
        self._result = self.controller.copyPreview(
            copyFormat,
            featureId=source if protein and isinstance(source, str) else None,
            frame=source if protein and isinstance(source, int) else None,
        )
        result = self._result
        self.preview.setSequence(result.text)
        self.preview.setAccessibleName(f"{item.text()} preview")
        unit = "residues / stops" if result.moleculeType == "protein" and protein else (
            "aa" if result.moleculeType == "protein" else "nt"
        )
        details = [f"{result.moleculeType} · {result.orientation} · {len(result.text):,} {unit}"]
        if result.translation is not None:
            details.append(translationInterpretation(result.translation))
            details.append("Complete selected codons only. * = stop · X = ambiguous.")
        elif copyFormat == "rna":
            details.append("Uses the selected DNA as the coding strand. T becomes U.")
        elif copyFormat == "dna":
            details.append("U becomes T.")
        details.extend(result.warnings)
        self.interpretation.setText("\n".join(details))
        self.copyButton.setText(f"Copy {item.text()}")
        self.copyButton.setEnabled(result.isAvailable)
        self._feedback.clear()

    def _copy(self) -> None:
        if self.controller.copyConversion(self._result):
            self._feedback.setText("Copied to clipboard.")
        else:
            self.preview.setFocus()
            self.preview.selectAll()
            self._feedback.setText("Clipboard unavailable. Copy the selected preview with your system shortcut.")
