"""Native form for creating and editing an annotation on a captured revision."""

from collections.abc import Callable
from functools import partial
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QFrame,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.operations.editing import (
    AnnotationEdit,
    addAnnotation,
    canEditAnnotationLocation,
    editAnnotation,
)


_LOCATION_LINE = re.compile(r"\s*([0-9]+)\s*\.\.\s*([0-9]+)\s*")
_STRANDS = (("Forward (+)", 1), ("Reverse (−)", -1), ("Unstranded", 0), ("Unspecified", None))
AnnotationCommand = Callable[[SequenceRevision, str], AnnotationEdit]


def _formatParts(parts: tuple[SelectionSegment, ...]) -> str:
    return "\n".join(f"{start + 1}..{end}" for start, end in parts)


def _parseParts(text: str) -> tuple[SelectionSegment, ...]:
    parts = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LOCATION_LINE.fullmatch(line)
        if match is None:
            raise ValueError("Enter one location per line in start..end format, such as 2..10.")
        parts.append((int(match[1]) - 1, int(match[2])))
    return tuple(parts)


def _parseQualifiers(text: str) -> dict[str, tuple[str, ...]]:
    qualifiers: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, entry = line.partition("=")
        if not separator or not key.strip():
            raise ValueError("Enter one qualifier per line in name=value format.")
        qualifiers.setdefault(key.strip(), []).append(entry.strip())
    return {key: tuple(entries) for key, entries in qualifiers.items()}


class AnnotationEditor(QDialog):
    """Collect an explicit annotation edit and keep invalid submissions open."""

    def __init__(
        self, revision: SequenceRevision, selection: SequenceSelection,
        feature: SequenceFeature | None,
        applyEdit: Callable[[AnnotationCommand, Callable[[str], None]], bool],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit label" if feature is not None else "Annotate")
        self.setMinimumWidth(425)
        self.resize(565, 565)
        self._feature = feature
        self._applyEdit = applyEdit
        self._canEditLocation = (
            feature is None or canEditAnnotationLocation(revision, feature.featureId)
        )
        self._initialStrand = feature.strand if feature is not None else selection.strand

        layout = dialogLayout(self)
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        fields = QWidget(scroll)
        fields.setBackgroundRole(QPalette.ColorRole.Base)
        fieldColumn = QVBoxLayout(fields)
        fieldColumn.setContentsMargins(0, 0, 6, 0)
        form = QFormLayout()
        fieldColumn.addLayout(form)
        fieldColumn.addStretch(1)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(fields)
        layout.addWidget(scroll, 1)
        self.labelEdit = QLineEdit(feature.label if feature is not None else "", self)
        self._addField(form, "Label", self.labelEdit)
        if feature is not None:
            source = feature.labelSource
            if source is None:
                source = (
                    "Source feature ID" if feature.sourceId not in ("", "<unknown id>")
                    else "Feature type"
                )
            sourceLabel = QLabel(source, self)
            sourceLabel.setWordWrap(True)
            sourceLabel.setTextFormat(Qt.TextFormat.PlainText)
            sourceLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow("Current label source", sourceLabel)
        self.typeEdit = QLineEdit(feature.type if feature is not None else "misc_feature", self)
        self._addField(form, "Feature type", self.typeEdit)

        self.strandSelector = QComboBox(self)
        for label, strand in _STRANDS:
            if (
                revision.moleculeType != "protein" or strand in (None, 0)
                or strand == self._initialStrand
            ):
                self.strandSelector.addItem(label, strand)
        self.strandSelector.setCurrentIndex(self.strandSelector.findData(self._initialStrand))
        self.strandSelector.setEnabled(self._canEditLocation)
        self._addField(form, "Strand", self.strandSelector)

        self.locationEdit = QPlainTextEdit(self)
        self.locationEdit.setMaximumHeight(86)
        locationHelp = QLabel(self)
        locationHelp.setWordWrap(True)
        locationHelp.setObjectName("muted")
        if self._canEditLocation:
            parts = selection.segments if feature is None else tuple(
                (part.start, part.end) for part in feature.parts
            )
            self.locationEdit.setPlainText(_formatParts(parts))
            rangeTip = "List one start..end range per line in strand order."
            if revision.topology == "circular":
                rangeTip += " List both sides of an origin crossing as separate ranges."
            locationHelp.setText(f"Positions 1 to {len(revision):,}, inclusive. {rangeTip}")
        else:
            self.locationEdit.setPlainText("\n".join(part.locationText for part in feature.parts))
            self.locationEdit.setReadOnly(True)
            locationHelp.setText("The original location is preserved when you edit other fields.")
        self._initialLocation = self.locationEdit.toPlainText()
        self._automaticLocation = self._initialLocation
        self.locationEdit.setAccessibleDescription(locationHelp.text())
        self._addField(form, "Ordered locations", self.locationEdit)
        form.addRow("", locationHelp)
        self.strandSelector.currentIndexChanged.connect(self._onStrandChanged)
        self._previousStrand = self._initialStrand

        self.colorEdit = QLineEdit((feature.color or "") if feature is not None else "", self)
        self.colorEdit.setPlaceholderText("#RRGGBB, or default when empty")
        self._addField(form, "Color", self.colorEdit)
        self.qualifierEdit = QPlainTextEdit(self)
        self.qualifierEdit.setMaximumHeight(70)
        self.qualifierEdit.setPlaceholderText("One name=value entry per line")
        self._addField(form, "Additional qualifiers", self.qualifierEdit)

        self.error = QLabel(self)
        self.error.setObjectName("notice")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        self.cancelButton = QPushButton("Cancel", self)
        self.cancelButton.clicked.connect(self.reject)
        actions.addWidget(self.cancelButton)
        actions.addStretch(1)
        self.applyButton = QPushButton("Apply", self)
        self.applyButton.setDefault(True)
        self.applyButton.clicked.connect(self._submit)
        actions.addWidget(self.applyButton)
        addDialogFooter(layout, actions, self.applyButton)
        self.labelEdit.setFocus()
        self.labelEdit.selectAll()

    @staticmethod
    def _addField(form: QFormLayout, title: str, widget: QWidget) -> None:
        widget.setAccessibleName(title)
        form.addRow(title, widget)

    def _onStrandChanged(self) -> None:
        strand = self.strandSelector.currentData()
        location = self.locationEdit.toPlainText()
        if (strand == -1) != (self._previousStrand == -1) and location == self._automaticLocation:
            # Reorient the prefilled parts and leave explicitly entered order alone.
            self._automaticLocation = "\n".join(reversed(location.splitlines()))
            self.locationEdit.setPlainText(self._automaticLocation)
        self._previousStrand = strand

    def _command(self) -> AnnotationCommand:
        feature = self._feature
        label = self.labelEdit.text()
        featureType = self.typeEdit.text()
        strand = self.strandSelector.currentData()
        color = self.colorEdit.text().strip()
        qualifiers = _parseQualifiers(self.qualifierEdit.toPlainText()) or None
        location = self.locationEdit.toPlainText()
        shouldSetLocation = self._canEditLocation and (
            feature is None or strand != self._initialStrand or location != self._initialLocation
        )
        parts = _parseParts(location) if shouldSetLocation else None
        if feature is None:
            command = partial(
                addAnnotation, label=label.strip(), featureType=featureType.strip(), parts=parts,
                strand=strand, color=color or None, qualifiers=qualifiers,
            )
        else:
            command = partial(
                editAnnotation, featureId=feature.featureId,
                label=label.strip() if label != feature.label else None,
                featureType=featureType.strip() if featureType != feature.type else None,
                parts=parts, strand=strand if shouldSetLocation else None,
                color=color if color != (feature.color or "") else None, qualifiers=qualifiers,
            )
        return lambda revision, recordId: command(revision, recordId=recordId)

    def _submit(self) -> None:
        self.error.hide()
        try:
            command = self._command()
        except ValueError as error:
            self._showError(str(error))
            return
        if self._applyEdit(command, self._showError):
            self.accept()

    def _showError(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
