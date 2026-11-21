"""Lower collapsible selection bar with expandable Selection/Qualifiers/Source tabs."""

from dataclasses import dataclass

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from strandfolio.domain.coordinates import SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.panels.sequence_conversion import translationInterpretation
from strandfolio.io.formats import SequenceImportResult
from strandfolio.operations.conversion import CdsTranslation, selectedTranslationCodons


@dataclass(frozen=True, slots=True)
class SelectionSummary:
    """Display coordinates and description for a nonempty selection."""

    endpoints: tuple[int, int] | None
    rangeText: str
    text: str


def selectionCountText(revision: SequenceRevision, selected: SequenceSelection) -> str:
    """Format the selection length in the record's units."""
    unit = "aa" if revision.moleculeType == "protein" else "nt"
    return f"{len(selected):,} {unit}"


def selectionSummary(
    revision: SequenceRevision, selected: SequenceSelection,
    feature: SequenceFeature | None, cdsTranslation: CdsTranslation | None,
) -> SelectionSummary:
    """Describe selected intervals, any selected feature, and its cached CDS result."""
    endpoints = selected.displayRange(len(revision), circular=revision.topology == "circular")
    if endpoints is None:
        rangeText = f"{len(selected.segments)} segments (gaps excluded)"
    else:
        first, last = endpoints
        rangeText = f"{first:,} → {last:,}" + (" (wraps origin)" if first > last else "")
    summaryParts = [rangeText, "Stored orientation"]
    if feature is not None:
        partCount = len(feature.parts)
        summaryParts.append(feature.label)
        if selected.wholeFeature:
            summaryParts.append(f"Whole feature ({partCount} segments)")
        else:
            summaryParts.append(f"Segment {selected.partIndex + 1} of {partCount}")
    if cdsTranslation is not None:
        cdsNote = "CDS translation unavailable"
        if cdsTranslation.isValid:
            cdsNote = f"Computed CDS: {len(cdsTranslation.codons):,} aa"
            if cdsTranslation.matchesImported is False:
                cdsNote += " (differs from imported)"
        summaryParts.append(cdsNote)
    return SelectionSummary(endpoints, rangeText, " · ".join(summaryParts))


class ImportDetailsDialog(QDialog):
    """Present the captured source and its reader notices without letting text size the window."""

    def __init__(self, capture: SequenceImportResult, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import details")
        self.setObjectName("importDetailsDialog")
        layout = dialogLayout(self)
        card = QFrame(self, objectName="dialogCard")
        facts = QFormLayout(card)
        facts.setContentsMargins(14, 12, 14, 12)
        facts.setHorizontalSpacing(20)
        facts.addRow("Source", FittedLabel(capture.source.filename, card))
        names = {"genbank": "GenBank", "snapgene": "SnapGene", "fasta": "FASTA", "text": "Plain text"}
        self.formatName = QLabel(names[capture.formatName], card)
        facts.addRow("Format", self.formatName)
        facts.addRow("Records", QLabel(f"{len(capture.records):,}", card))
        facts.addRow("Original size", QLabel(f"{capture.source.byteCount:,} bytes", card))
        layout.addWidget(card)
        if capture.warnings:
            layout.addWidget(QLabel("Reader notices", self, objectName="dialogSectionTitle"))
            notices = QPlainTextEdit(self)
            notices.setAccessibleName("Reader notices")
            notices.setReadOnly(True)
            notices.setPlainText("\n\n".join(capture.warnings))
            layout.addWidget(notices, 1)
        else:
            layout.addWidget(QLabel("The reader reported no warnings.", self, objectName="muted"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        addDialogFooter(layout, buttons)
        self.resize(510, 352 if capture.warnings else self.sizeHint().height())


class SequenceDetails(QFrame):
    """Compact bottom selection bar with an expandable tabbed inspector drawer."""

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(8)

        # Always-compact primary selection & copy strip
        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(10)

        self.count = QLabel("—", self)
        self.count.setObjectName("selectionPill")
        self.count.setMinimumWidth(68)
        strip.addWidget(self.count)

        self.summary = QLabel("Open a sequence to begin.", self)
        self.summary.setObjectName("muted")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.summary.setWordWrap(True)
        strip.addWidget(self.summary, 1)

        strip.addWidget(QLabel("From", self))
        self.start = QSpinBox(self)
        self.end = QSpinBox(self)
        for spin, name in (
            (self.start, "First selected base or residue"),
            (self.end, "Last selected base or residue"),
        ):
            spin.setSpecialValueText("—")
            spin.setAccessibleName(name)
            spin.setKeyboardTracking(False)
            spin.setFixedWidth(84)
        strip.addWidget(self.start)
        strip.addWidget(QLabel("Through", self))
        strip.addWidget(self.end)

        self.selectRangeButton = QPushButton("Select range", self)
        self.selectRangeButton.clicked.connect(self._selectRange)
        strip.addWidget(self.selectRangeButton)

        self.copySequenceButton = QPushButton("Copy sequence", self)
        self.copySequenceButton.setToolTip("Copy the selected letters in stored orientation.")
        self.copySequenceButton.clicked.connect(controller.copySelection)
        strip.addWidget(self.copySequenceButton)

        self.copyReverseComplementButton = QPushButton("Copy rev-comp", self)
        self.copyReverseComplementButton.setToolTip("Copy selected nucleic-acid segments as 5'→3' reverse complement.")
        self.copyReverseComplementButton.clicked.connect(controller.copyReverseComplement)
        strip.addWidget(self.copyReverseComplementButton)

        self.expandDetailsButton = QPushButton("Details ▴", self)
        self.expandDetailsButton.setCheckable(True)
        self.expandDetailsButton.setToolTip("Expand selection segments, feature qualifiers and source details.")
        self.expandDetailsButton.toggled.connect(self.setExpanded)
        strip.addWidget(self.expandDetailsButton)
        outer.addLayout(strip)

        self.drawer = QTabWidget(self)
        self.drawer.setMaximumHeight(165)
        self.drawer.hide()

        selectionTab = QWidget(self.drawer)
        selectionLayout = QVBoxLayout(selectionTab)
        selectionLayout.setContentsMargins(8, 8, 8, 8)
        self.segmentDetails = QPlainTextEdit(selectionTab)
        self.segmentDetails.setReadOnly(True)
        self.segmentDetails.setPlaceholderText("Select bases or a feature to view ordered intervals and CDS translation status.")
        selectionLayout.addWidget(self.segmentDetails)
        self.drawer.addTab(selectionTab, "Selection / CDS")

        qualifiersTab = QWidget(self.drawer)
        qualifiersLayout = QVBoxLayout(qualifiersTab)
        qualifiersLayout.setContentsMargins(8, 8, 8, 8)
        self.featureQualifiers = QPlainTextEdit(qualifiersTab)
        self.featureQualifiers.setReadOnly(True)
        self.featureQualifiers.setPlaceholderText("Select an annotated feature to inspect all imported qualifiers.")
        qualifiersLayout.addWidget(self.featureQualifiers)
        self.drawer.addTab(qualifiersTab, "Feature Qualifiers")

        sourceTab = QWidget(self.drawer)
        sourceLayout = QHBoxLayout(sourceTab)
        sourceLayout.setContentsMargins(10, 8, 10, 8)
        sourceLayout.setSpacing(16)
        self.metadata = QLabel("No file open", sourceTab)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setWordWrap(True)
        self.metadata.setObjectName("muted")
        sourceLayout.addWidget(self.metadata, 1)
        self.notice = QLabel("Open GenBank, FASTA, SnapGene or plain text.", sourceTab)
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        sourceLayout.addWidget(self.notice, 2)
        self.drawer.addTab(sourceTab, "Source / Limits")

        outer.addWidget(self.drawer)

        self.start.valueChanged.connect(self._updateRangeEnabled)
        self.end.valueChanged.connect(self._updateRangeEnabled)
        controller.documentChanged.connect(self._onDocumentChanged)
        controller.selectionChanged.connect(self._onSelectionChanged)
        controller.translationChanged.connect(self._onSelectionChanged)
        self._onDocumentChanged()

    def setExpanded(self, expanded: bool) -> None:
        with QSignalBlocker(self.expandDetailsButton):
            self.expandDetailsButton.setChecked(expanded)
        self.expandDetailsButton.setText("Details ▾" if expanded else "Details ▴")
        self.drawer.setVisible(expanded)
        if expanded:
            self._onSelectionChanged()

    def _onDocumentChanged(self) -> None:
        revision = self.controller.session.current
        length = len(revision) if revision is not None else 1
        with QSignalBlocker(self.start), QSignalBlocker(self.end):
            self.start.setRange(0, length)
            self.end.setRange(0, length)
            self.start.setValue(1)
            self.end.setValue(1)
        self.start.setEnabled(revision is not None)
        self.end.setEnabled(revision is not None)
        if revision is None:
            self.metadata.setText("No file open")
            self.notice.setText("Open GenBank, FASTA, SnapGene or plain text.")
        else:
            structure = "Protein · residue coordinates"
            if revision.moleculeType != "protein":
                structure = f"Topology: {revision.topology or 'unknown'}\nStrand state: {revision.strandState}"
            result = self.controller.importResult
            origin = "Original file preserved" if result is not None else "Project snapshot"
            self.metadata.setText(f"{revision.moleculeType}\n{structure}\n{origin}")
            self.notice.setText(
                f"{len(revision.features):,} stored annotations.\n"
                "Use Features to inspect or curate labels. Choose Import details to see the source."
            )
        self._onSelectionChanged()

    def _onSelectionChanged(self) -> None:
        session = self.controller.session
        revision, selected = session.current, session.selection
        hasSelection = revision is not None and selected is not None and len(selected) > 0
        self.copySequenceButton.setEnabled(hasSelection)
        self.copyReverseComplementButton.setEnabled(
            hasSelection and revision.moleculeType != "protein",
        )
        if revision is None or selected is None:
            self.count.setText("—")
            self.summary.setText("Open a sequence to begin.")
            self.segmentDetails.clear()
            self.featureQualifiers.clear()
        else:
            countText = selectionCountText(revision, selected)
            self.count.setText(countText)
            if len(selected):
                detailLines = self._frameSelectionDetails(selected)
                feature = None
                cdsTranslation = None
                if selected.featureId is not None:
                    feature = revision.getFeature(selected.featureId)
                    cdsTranslation = self.controller.cdsForFeature(selected.featureId)
                    if cdsTranslation is not None:
                        if detailLines:
                            detailLines.append("")
                        matchText = {
                            True: "matches imported /translation",
                            False: "differs from imported /translation",
                            None: "no imported /translation qualifier",
                        }[cdsTranslation.matchesImported]
                        if cdsTranslation.isValid:
                            detailLines.append(
                                f"CDS translation: {len(cdsTranslation.codons):,} aa · {matchText}"
                            )
                        else:
                            detailLines.append("CDS translation unavailable for this annotation.")
                        detailLines.append(translationInterpretation(cdsTranslation))
                        if cdsTranslation.proteinSequence:
                            detailLines.append(self._proteinPreview(cdsTranslation.proteinSequence))
                        if cdsTranslation.warnings:
                            detailLines.extend(cdsTranslation.warnings)
                    qualifierLines = [f"Type: {feature.type}", f"Location operator: {feature.operator or 'single'}"]
                    qualifierLines.extend(f"{qualifierName}: {'; '.join(qualifierValues)}" for qualifierName, qualifierValues in feature.qualifiers)
                    self.featureQualifiers.setPlainText("\n".join(qualifierLines))
                else:
                    self.featureQualifiers.clear()

                summary = selectionSummary(revision, selected, feature, cdsTranslation)
                self.summary.setText(summary.text)
                endpoints = summary.endpoints
                detailLines.insert(0, "Ordered intervals (1-based inclusive): " + ", ".join(
                    f"{segmentStart + 1:,}–{segmentEnd:,}" for segmentStart, segmentEnd in selected.segments
                ))
                self.segmentDetails.setPlainText("\n".join(detailLines))
            else:
                self.summary.setText(f"Caret at boundary {selected.caret:,} · Drag bases or plasmid ring to select.")
                self.segmentDetails.setPlainText(
                    f"Caret at boundary {selected.caret:,} ({countText} selected)."
                )
                self.featureQualifiers.clear()
                base = min(len(revision), selected.caret + 1)
                endpoints = (base, base)

            with QSignalBlocker(self.start), QSignalBlocker(self.end):
                first, last = endpoints or (0, 0)
                self.start.setValue(first)
                self.end.setValue(last)
        self._updateRangeEnabled()

    def _frameSelectionDetails(self, selected: SequenceSelection) -> list[str]:
        frame = self.controller.frameTranslation
        if frame is None or not self.expandDetailsButton.isChecked():
            return []
        details = [translationInterpretation(frame), *frame.warnings]
        if frame.isValid:
            codons = selectedTranslationCodons(frame, selected, limit=121)
            if codons:
                details.append(self._proteinPreview("".join(codon.aminoAcid for codon in codons)))
                details.append("Complete selected codons only. * = stop · X = ambiguous.")
            else:
                details.append("The selection contains no complete codons in this frame.")
        return details

    @staticmethod
    def _proteinPreview(protein: str) -> str:
        suffix = "…" if len(protein) > 120 else ""
        return f"Translated sequence: {protein[:120]}{suffix}"

    def _updateRangeEnabled(self) -> None:
        revision = self.controller.session.current
        if revision is None:
            self.selectRangeButton.setEnabled(False)
            return
        allowsWrap = revision.topology == "circular"
        first, last = self.start.value(), self.end.value()
        self.selectRangeButton.setEnabled(first > 0 and last > 0 and (allowsWrap or first <= last))
        self.selectRangeButton.setToolTip("Select the inclusive range." if first and last else
                                          "Enter both endpoints to replace the current selection.")

    def _selectRange(self) -> None:
        self.controller.selectDisplayRange(self.start.value(), self.end.value())
