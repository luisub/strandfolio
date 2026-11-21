"""Dialogs for importing, closing and naming sequence documents."""

from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from strandfolio.application.workspace import ClosePlan, WorkspaceDocument
from strandfolio.domain.sequence import MoleculeType
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout, fileDialogDirectory
from strandfolio.gui.panels.document_strip import documentLabels
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.io.formats import SequenceImportResult, TextNormalization
from strandfolio.storage.projects import DATABASE_FILENAME, ProjectRepository

if TYPE_CHECKING:
    from strandfolio.gui.window import MainWindow


_PROJECT_ERRORS = (OSError, ValueError, KeyError, sqlite3.Error)


def _plainLabel(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class BatchReviewDialog(QDialog):
    """Show the imported records in order before saving originals or opening tabs."""

    def __init__(self, capture: SequenceImportResult, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("batchReviewDialog")
        self.setWindowTitle("Open multiple sequences")
        self.resize(580, 410)
        layout = dialogLayout(self)
        count = len(capture.records)
        source = FittedLabel(capture.source.filename, self)
        source.setObjectName("dialogSectionTitle")
        layout.addWidget(source)
        layout.addWidget(_plainLabel(f"Open each of the {count} sequences in its own tab?", self))
        lines = []
        for ordinal, revision in enumerate(capture.records, 1):
            unit = "aa" if revision.moleculeType == "protein" else "nt"
            original = revision.description if capture.formatName == "fasta" else revision.name
            lines.append(f"{ordinal}. {original}\n{len(revision):,} {unit} · {revision.moleculeType}")
        self.records = QPlainTextEdit("\n\n".join(lines), self)
        self.records.setAccessibleName("Sequences in source order")
        self.records.setReadOnly(True)
        layout.addWidget(self.records, 1)
        if capture.warnings:
            heading = _plainLabel("Import warnings", self)
            heading.setObjectName("dialogSectionTitle")
            layout.addWidget(heading)
            notice = QPlainTextEdit("\n\n".join(capture.warnings), self)
            notice.setAccessibleName("Import warnings")
            notice.setReadOnly(True)
            notice.setMaximumHeight(96)
            layout.addWidget(notice)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.acceptButton = buttons.addButton(
            f"Open {count} sequences", QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.cancelButton = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.acceptButton.setDefault(True)
        addDialogFooter(layout, buttons, self.acceptButton)
        self.acceptButton.setFocus()


class TextMoleculeDialog(QDialog):
    """Require a deliberate alphabet choice for headerless input."""

    def __init__(self, filename: str | None, parent: QWidget) -> None:
        super().__init__(parent)
        self.pastedText = ""
        self._takesPaste = filename is None
        self.setObjectName("pasteSequenceDialog" if self._takesPaste else "textMoleculeDialog")
        self.setWindowTitle("Paste sequence" if self._takesPaste else "Open plain text")
        self.resize(500, 365 if self._takesPaste else 220)
        layout = dialogLayout(self)
        if self._takesPaste:
            self._addPasteControls(layout)
        else:
            layout.addWidget(FittedLabel(filename, self))
        card = QFrame(self)
        card.setObjectName("dialogCard")
        types = QVBoxLayout(card)
        heading = _plainLabel("Molecule type", card)
        heading.setObjectName("dialogSectionTitle")
        types.addWidget(heading)
        choices = QHBoxLayout()
        types.addLayout(choices)
        layout.addWidget(card)
        self.moleculeType: MoleculeType | None = None
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.acceptButton = buttons.addButton("Continue", QDialogButtonBox.ButtonRole.AcceptRole)
        self.acceptButton.setEnabled(False)
        self.acceptButton.setDefault(True)
        self.options: dict[MoleculeType, QRadioButton] = {}
        for moleculeType in ("DNA", "RNA", "protein"):
            option = QRadioButton("Protein" if moleculeType == "protein" else moleculeType, card)
            option.toggled.connect(self._chooseType)
            choices.addWidget(option)
            self.options[moleculeType] = option
        if not self._takesPaste:
            layout.addStretch(1)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        addDialogFooter(layout, buttons, self.acceptButton)

    def _chooseType(self) -> None:
        self.moleculeType = next((key for key, option in self.options.items() if option.isChecked()), None)
        self._updateContinue()

    def _addPasteControls(self, layout: QVBoxLayout) -> None:
        self.pasteButton = QPushButton("Paste from clipboard", self)
        self.pasteButton.setObjectName("pasteSequenceButton")
        self.pasteButton.clicked.connect(self._pasteSequence)
        layout.addWidget(self.pasteButton, 0, Qt.AlignmentFlag.AlignLeft)
        self.pasteDisplay = QPlainTextEdit(self)
        self.pasteDisplay.setObjectName("pastedSequencePreview")
        self.pasteDisplay.setAccessibleName("Pasted sequence preview")
        self.pasteDisplay.setReadOnly(True)
        self.pasteDisplay.setFont(workspaceMonoFont(13))
        self.pasteDisplay.setPlaceholderText("Paste a headerless sequence from the clipboard.")
        layout.addWidget(self.pasteDisplay, 1)
        self._pasteStatus = _plainLabel("", self)
        self._pasteStatus.hide()
        layout.addWidget(self._pasteStatus)
        shortcut = QShortcut(QKeySequence.StandardKey.Paste, self)
        shortcut.activated.connect(self._pasteSequence)

    def _pasteSequence(self) -> None:
        mimeData = QApplication.clipboard().mimeData()
        self.pastedText = mimeData.text() if mimeData is not None and mimeData.hasText() else ""
        # A text editor normalizes some whitespace. Only render an excerpt here
        # and use the untouched clipboard string when the user continues.
        prefixLength = 1600
        self.pasteDisplay.setPlainText(self.pastedText[:prefixLength])
        if len(self.pastedText) > prefixLength:
            notice = f"Showing the first {prefixLength:,} clipboard characters."
        else:
            notice = "" if self.pastedText else "The clipboard has no plain text."
        self._pasteStatus.setText(notice)
        self._pasteStatus.setVisible(bool(notice))
        self._updateContinue()

    def _updateContinue(self) -> None:
        self.acceptButton.setEnabled(
            self.moleculeType is not None and (not self._takesPaste or bool(self.pastedText)),
        )


class TextNormalizationDialog(QDialog):
    """Preview how Open will normalize the captured text."""

    def __init__(
        self, capture: SequenceImportResult, preview: TextNormalization, parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("textNormalizationDialog")
        self.setWindowTitle("Review plain text")
        self.resize(500, 330)
        layout = dialogLayout(self)
        layout.addWidget(FittedLabel(capture.source.filename, self))
        revision = capture.records[0]
        unit = "aa" if revision.moleculeType == "protein" else "nt"
        layout.addWidget(_plainLabel(
            f"{revision.moleculeType} · {len(revision):,} {unit}\n"
            f"Spaces and tabs to remove: {preview.groupingCharactersRemoved:,}\n"
            f"Line breaks to remove: {preview.lineBreaksRemoved:,}", self,
        ))
        prefixLength = 160
        title = "Sequence after formatting removal"
        if len(revision) > prefixLength:
            title += f" (first {prefixLength} letters)"
        previewLabel = _plainLabel(title, self)
        previewLabel.setObjectName("dialogSectionTitle")
        layout.addWidget(previewLabel)
        letters = QPlainTextEdit(preview.sequence[:prefixLength], self)
        letters.setAccessibleName(title)
        letters.setReadOnly(True)
        letters.setFont(workspaceMonoFont(13))
        layout.addWidget(letters, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.acceptButton = buttons.addButton("Open sequence", QDialogButtonBox.ButtonRole.AcceptRole)
        self.acceptButton.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        addDialogFooter(layout, buttons, self.acceptButton)


class PreferredNameDialog(QDialog):
    """Edit one optional preference while keeping the complete original name selectable."""

    def __init__(
        self, originalName: str, canonicalName: str, preferredName: str | None, parent: QWidget,
        *, disambiguator: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("preferredNameDialog")
        self.setWindowTitle("Edit preferred name")
        self.resize(540, 240)
        layout = dialogLayout(self)
        if disambiguator:
            layout.addWidget(FittedLabel(disambiguator, self))
        fields = QFormLayout()
        fields.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fields.setVerticalSpacing(8)
        self.originalName = self._nameField(originalName, readOnly=True)
        self.preferredName = self._nameField(preferredName or "")
        fields.addRow("Original name", self.originalName)
        if canonicalName != originalName:
            fields.addRow("Record name", self._nameField(canonicalName, readOnly=True))
        fields.addRow("Preferred name", self.preferredName)
        layout.addLayout(fields)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.applyButton = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.clearButton = buttons.addButton(
            "Clear preferred name", QDialogButtonBox.ButtonRole.ResetRole,
        )
        self.cancelButton = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.clearButton.clicked.connect(self._clearPreference)
        self.applyButton.setDefault(True)
        self.preferredName.setFocus()
        self.preferredName.selectAll()
        addDialogFooter(layout, buttons, self.applyButton)

    def _nameField(self, value: str, *, readOnly: bool = False) -> QLineEdit:
        field = QLineEdit(self)
        field.setMaxLength(2_147_483_647)
        field.setText(value)
        field.setReadOnly(readOnly)
        field.setCursorPosition(0)
        return field

    def _clearPreference(self) -> None:
        self.preferredName.clear()
        self.accept()


class _SavedSequenceChoice(QRadioButton):
    """Fit the visible label while preserving the complete accessible text."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._label = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setRecord(self, revisionId: str, label: str) -> None:
        self._label = label
        self.setProperty("revisionId", revisionId)
        self.setAccessibleName(label)
        self.setToolTip(escape(label))
        self._fitLabel()

    def _fitLabel(self) -> None:
        label = self.fontMetrics().elidedText(
            self._label, Qt.TextElideMode.ElideMiddle, max(0, self.width() - 28),
        )
        self.setText(label.replace("&", "&&"))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fitLabel()


class SavedSequencesDialog(QDialog):
    """Choose a saved snapshot by revision ID."""

    _PAGE_SIZE = 64

    def __init__(self, records: tuple[tuple[str, str], ...], parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("savedSequencesDialog")
        self.setWindowTitle("Open saved sequences")
        self.resize(620, 340)
        self._records = records
        self._pageIndex = 0
        self.recordButtons: dict[str, _SavedSequenceChoice] = {}

        layout = dialogLayout(self)
        self._scrollArea = QScrollArea(self)
        self._scrollArea.setAccessibleName("Stored project sequences")
        self._scrollArea.setWidgetResizable(True)
        self._scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows = QFrame(self._scrollArea)
        rows.setObjectName("dialogCard")
        rowsLayout = QVBoxLayout(rows)
        rowsLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        rowsLayout.setContentsMargins(10, 8, 10, 8)
        rowsLayout.setSpacing(5)
        self._choices = QButtonGroup(self)
        self._choiceButtons = [_SavedSequenceChoice(rows) for _ in records[:self._PAGE_SIZE]]
        for button in self._choiceButtons:
            self._choices.addButton(button)
            button.installEventFilter(self)
            rowsLayout.addWidget(button)
        self._scrollArea.setWidget(rows)
        layout.addWidget(self._scrollArea, 1)

        pager = QHBoxLayout()
        self._pageLabel = QLabel(self)
        self.previousButton = QPushButton("Previous", self)
        self.previousButton.clicked.connect(lambda: self._showPage(self._pageIndex - 1))
        self.nextButton = QPushButton("Next", self)
        self.nextButton.clicked.connect(lambda: self._showPage(self._pageIndex + 1))
        pager.addWidget(self._pageLabel)
        pager.addStretch(1)
        pager.addWidget(self.previousButton)
        pager.addWidget(self.nextButton)
        layout.addLayout(pager)
        for control in (self._pageLabel, self.previousButton, self.nextButton):
            control.setVisible(len(records) > self._PAGE_SIZE)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.openButton = buttons.addButton("Open sequence", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancelButton = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.openButton.setDefault(True)
        self.openButton.setEnabled(bool(records))
        addDialogFooter(layout, buttons, self.openButton)
        self._showPage(0)

    def _showPage(self, pageIndex: int) -> None:
        start = pageIndex * self._PAGE_SIZE
        stop = min(start + self._PAGE_SIZE, len(self._records))
        self._pageIndex = pageIndex
        self.recordButtons.clear()
        for index, button in enumerate(self._choiceButtons):
            if start + index < stop:
                revisionId, label = self._records[start + index]
                button.setRecord(revisionId, label)
                self.recordButtons[revisionId] = button
                button.show()
            else:
                button.hide()
        self._pageLabel.setText(f"Sequences {start + 1}–{stop} of {len(self._records)}")
        self.previousButton.setEnabled(start > 0)
        self.nextButton.setEnabled(stop < len(self._records))
        self._scrollArea.verticalScrollBar().setValue(0)
        if self._choiceButtons:
            self._choiceButtons[0].setChecked(True)
            self._choiceButtons[0].setFocus()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, _SavedSequenceChoice):
            self._scrollArea.ensureWidgetVisible(watched)
        return super().eventFilter(watched, event)

    @property
    def revisionId(self) -> str:
        return self._choices.checkedButton().property("revisionId")


CloseChoice = Literal["save", "discard"]


@dataclass(frozen=True)
class ClosingDocument:
    documentId: str
    displayName: str
    isDraft: bool


class CloseDocumentsDialog(QDialog):
    """Ask whether to save or discard each unsaved draft before closing its tab."""

    def __init__(
        self, documents: tuple[ClosingDocument, ...], pending: tuple[str, ...],
        closeAction: str, parent: QWidget, *, retainedLanes: tuple[int, ...] = (),
        discardedComparisons: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.setObjectName("closeDocumentsDialog")
        self.setWindowTitle(closeAction)
        self.choices: dict[str, CloseChoice] = {}
        self._drafts = tuple(document for document in documents if document.isDraft)
        layout = dialogLayout(self, "Unsaved sequence" if self._drafts else closeAction)
        buttons = QDialogButtonBox(self)
        self.keepOpenButton = buttons.addButton("Keep open", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)

        if self._drafts:
            if len(self._drafts) > 1:
                self._progress = _plainLabel("", self)
                layout.addWidget(self._progress)
            self._draftName = FittedLabel("", self)
            self._draftName.setObjectName("dialogSectionTitle")
            layout.addWidget(self._draftName)
            layout.addWidget(_plainLabel("Discard closes this sequence without saving.", self))
            self.discardButton = buttons.addButton("Discard", QDialogButtonBox.ButtonRole.DestructiveRole)
            self.saveButton = buttons.addButton("Save as New", QDialogButtonBox.ButtonRole.AcceptRole)
            self.discardButton.clicked.connect(lambda: self._chooseDraft("discard"))
            self.saveButton.clicked.connect(lambda: self._chooseDraft("save"))
            self._showDraft()
        else:
            self.closeButton = buttons.addButton(closeAction, QDialogButtonBox.ButtonRole.AcceptRole)
            self.closeButton.clicked.connect(self.accept)
        if not self._drafts and len(documents) == 1 and closeAction == "Close sequence":
            name = FittedLabel(f"Close {documents[0].displayName}?", self)
            name.setObjectName("dialogSectionTitle")
            layout.addWidget(name)

        consequences = [f"Cancel pending work: {description}" for description in pending]
        consequences.extend(f"Discard alignment: {description}" for description in discardedComparisons)
        if retainedLanes:
            numbers = ", ".join(str(number) for number in retainedLanes)
            noun = "lane" if len(retainedLanes) == 1 else "lanes"
            consequences.append(f"Gel keeps pinned {noun} {numbers}.")
        if consequences:
            area = QScrollArea(self)
            area.setWidgetResizable(True)
            area.setAccessibleName("Work affected by closing")
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content = QFrame(area)
            content.setObjectName("dialogCard")
            details = QVBoxLayout(content)
            details.setContentsMargins(12, 10, 12, 10)
            details.setAlignment(Qt.AlignmentFlag.AlignTop)
            for description in consequences:
                details.addWidget(FittedLabel(description, content))
            area.setWidget(content)
            layout.addWidget(area, 1)

        addDialogFooter(layout, buttons, self.keepOpenButton)
        self.keepOpenButton.setDefault(True)
        self.keepOpenButton.setFocus()
        self.resize(430, self.sizeHint().height())

    def _showDraft(self) -> None:
        index = len(self.choices)
        document = self._drafts[index]
        self._draftName.setFullText(document.displayName)
        if len(self._drafts) > 1:
            self._progress.setText(f"Sequence {index + 1} of {len(self._drafts)}")
        self.keepOpenButton.setFocus()

    def _chooseDraft(self, choice: CloseChoice) -> None:
        document = self._drafts[len(self.choices)]
        self.choices[document.documentId] = choice
        if len(self.choices) == len(self._drafts):
            self.accept()
        else:
            self._showDraft()


class DocumentDialogs:
    """Prompt for project changes and review unsaved documents before closing them."""

    def __init__(self, window: "MainWindow", controller: WorkspaceController) -> None:
        self._window = window
        self.controller = controller
        self._nameEditor: PreferredNameDialog | None = None
        self._isReviewingClose = False
        self._preparedProject: tuple[Path, tuple[int, int]] | None = None
        controller.documentChanged.connect(self._discardNameEditor)

    def _discardNameEditor(self) -> None:
        if self._nameEditor is not None:
            self._nameEditor.reject()

    def openProjectDialog(self, *, create: bool = False) -> None:
        """Validate a project destination and review open tabs before switching."""
        workspace = self.controller.workspace
        project = workspace.project
        activation = self.controller.activationGeneration
        action = "New Project" if create else "Open Project"
        selected = QFileDialog.getExistingDirectory(self._window, action, str(fileDialogDirectory()))
        if (not selected or self.controller.activationGeneration != activation
                or self.controller.workspace.project is not project):
            return
        try:
            path = Path(selected).resolve()
            if create:
                prepared = self._openPreparedProject(path)
                if prepared is None and path.exists() and any(path.iterdir()):
                    raise FileExistsError(f"Project directory is not empty: {path}")
                self.closeDocuments(closeAction=action, newProjectPath=path, preparedProject=prepared)
            elif project is None or path != project.path:
                candidate = ProjectRepository.open(path)
                if self.closeDocuments(closeAction=action) and self.controller.setProject(candidate):
                    self.controller.message.emit(f"Opened project {path.name}.")
        except _PROJECT_ERRORS as error:
            self.controller.operationFailed.emit(str(error))

    @staticmethod
    def _projectIdentity(path: Path) -> tuple[int, int]:
        database = (path / DATABASE_FILENAME).stat(follow_symlinks=False)
        return database.st_dev, database.st_ino

    def _openPreparedProject(self, path: Path) -> ProjectRepository | None:
        if self._preparedProject is None or self._preparedProject[0] != path:
            return None
        try:
            identity = self._projectIdentity(path)
        except FileNotFoundError:
            return None
        if identity == self._preparedProject[1]:
            return ProjectRepository.open(path)
        return None

    def openSavedDialog(self) -> None:
        """Open a named record or captured original by its committed snapshot ID."""
        revisionId = self.chooseSavedRevision()
        if revisionId is not None:
            self.controller.openSavedRevision(revisionId)

    def chooseSavedRevision(self) -> str | None:
        """Return the selected project revision if the workspace still matches the dialog."""
        project = self.controller.workspace.project
        if project is None:
            return None
        activation = self.controller.activationGeneration
        try:
            entries = project.listReopenableRevisions()
        except _PROJECT_ERRORS as error:
            self.controller.operationFailed.emit(str(error))
            return None
        if not entries:
            self.controller.message.emit("This project has no saved records or registered imports.")
            return None
        labels = tuple(
            f"{entry.preferredName or entry.name} · "
            + ("Saved record" if entry.isNamed else f"{entry.sourceFilename}, record {entry.sourceOrdinal}")
            for entry in entries
        )
        counts = Counter(labels)
        records = tuple(
            (entry.revisionId, label if counts[label] == 1 else f"{label} · {entry.revisionId[:8]}")
            for entry, label in zip(entries, labels)
        )
        dialog = SavedSequencesDialog(records, self._window)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        revisionId = dialog.revisionId if accepted else None
        dialog.deleteLater()
        if (self.controller.activationGeneration == activation
                and self.controller.workspace.project is project):
            return revisionId
        return None

    def chooseSaveProject(self, parent: QWidget | None = None) -> Path | None:
        """Choose the destination for a first project save."""
        selected = QFileDialog.getExistingDirectory(
            parent if parent is not None else self._window,
            "Choose or create a project for saving", str(fileDialogDirectory()),
        )
        return Path(selected) if selected else None

    def _promptSaveName(self, document: WorkspaceDocument) -> str | None:
        context = next(
            label.disambiguator for label in documentLabels(self.controller.workspace)
            if label.documentId == document.documentId
        )
        prompt = f"Sequence name · {context}" if context else "Sequence name"
        dialog = QInputDialog(self._window)
        dialog.setObjectName("saveSequenceNameDialog")
        dialog.setWindowTitle("Save as New")
        dialog.setLabelText(prompt)
        dialog.setTextValue(document.revision.name)
        dialog.setOkButtonText("Save")
        # Qt creates its line editor and button layout lazily on the first size query.
        dialog.sizeHint()
        layout = dialogLayout(dialog)
        buttons = dialog.findChild(QDialogButtonBox)
        layout.removeWidget(buttons)
        addDialogFooter(layout, buttons, buttons.button(QDialogButtonBox.StandardButton.Ok))
        dialog.resize(440, dialog.sizeHint().height())
        name = dialog.textValue() if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater()
        return name

    @staticmethod
    def openSaveProject(path: Path) -> ProjectRepository:
        """Open an existing project or initialize an empty chosen directory."""
        if (path / DATABASE_FILENAME).exists():
            return ProjectRepository.open(path)
        return ProjectRepository.create(path)

    def saveAsNew(self, *, inlineName: str | None = None) -> None:
        """Publish the current draft with a unique name inside a writable project."""
        workspace = self.controller.workspace
        document = workspace.activeDocument
        if document is None or not document.isDraft:
            return
        target = workspace.targetFor(document.documentId)
        projectPath = None
        if workspace.project is None:
            projectPath = self.chooseSaveProject()
            if (projectPath is None or workspace is not self.controller.workspace
                    or not workspace.targetIsCurrent(target)):
                return
        name = self._promptSaveName(document) if inlineName is None else inlineName
        if (name is None or workspace is not self.controller.workspace
                or not workspace.targetIsCurrent(target)):
            return
        if not name.strip():
            self.controller.operationFailed.emit("A saved revision requires a nonblank name.")
            return
        try:
            project = self.openSaveProject(projectPath) if projectPath is not None else None
        except _PROJECT_ERRORS as error:
            self.controller.operationFailed.emit(str(error))
            return
        if self.controller.saveDocument(target, name, project):
            self._window.feedback.hide()
            self.controller.message.emit(f"Saved {name.strip()}.")

    def editPreferredName(self, documentId: str) -> None:
        """Use the same record-bound editor for strip and map gestures."""
        if self._nameEditor is not None:
            return
        workspace = self.controller.workspace
        try:
            document = workspace.getDocument(documentId)
        except KeyError:
            return
        target = workspace.targetFor(documentId)
        label = next(label for label in documentLabels(workspace) if label.documentId == documentId)
        originalPreference = workspace.preferredName(document)
        dialog = PreferredNameDialog(
            document.originalName, document.revision.name, originalPreference, self._window,
            disambiguator=label.disambiguator,
        )
        self._nameEditor = dialog
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._nameEditor = None
        if (accepted and workspace is self.controller.workspace
                and workspace.targetIsCurrent(target)):
            self._window.feedback.hide()
            self.controller.setPreferredName(target, dialog.preferredName.text(), expectedName=originalPreference or "")
        dialog.deleteLater()

    def requestClose(self, documentId: str | None = None) -> None:
        """Review closure of the requested tab without bringing it to the foreground."""
        documentId = documentId or self.controller.workspace.activeDocumentId
        if documentId is not None:
            self.closeDocuments((documentId,))

    def closeProject(self) -> None:
        """Finish dirty-tab review before clearing the current project."""
        if self.controller.workspace.project is not None and self.closeDocuments(closeAction="Close Project"):
            self.controller.setProject(None)

    def _promptClose(self, plan: ClosePlan, closeAction: str) -> dict[str, CloseChoice] | None:
        if not plan.pendingTasks and not plan.alignmentEffects and not any(
            target.isDraft for target in plan.documents
        ):
            return {}
        closingIds = {target.documentId for target in plan.documents}
        labels = tuple(
            ClosingDocument(label.documentId, label.qualifiedName, label.isDraft)
            for label in documentLabels(self.controller.workspace) if label.documentId in closingIds
        )
        pending = tuple(dict.fromkeys(task.description for task in plan.pendingTasks))
        closingRecords = {target.recordId for target in plan.documents}
        retainedLanes = tuple(
            index for index, lane in enumerate(self.controller.analysis.gelState.lanes, 1)
            if lane.source.recordId in closingRecords
        ) if closeAction == "Close sequence" else ()
        dialog = CloseDocumentsDialog(
            labels, pending, closeAction, self._window, retainedLanes=retainedLanes,
            discardedComparisons=tuple(effect.description for effect in plan.alignmentEffects),
        )
        result = dialog.choices if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater()
        return result

    def closeDocuments(
        self, documentIds: tuple[str, ...] | None = None, *, closeAction: str = "Close sequence",
        newProjectPath: Path | None = None, preparedProject: ProjectRepository | None = None,
    ) -> bool:
        """Review unsaved drafts. Leave the tabs open if the user cancels or a save fails."""
        if self._isReviewingClose:
            return False
        self._isReviewingClose = True
        try:
            return self._reviewClose(documentIds, closeAction, newProjectPath, preparedProject)
        finally:
            self._isReviewingClose = False

    def _reviewClose(
        self, documentIds: tuple[str, ...] | None, closeAction: str, newProjectPath: Path | None,
        preparedProject: ProjectRepository | None,
    ) -> bool:
        workspace = self.controller.workspace
        while self.controller.workspace is workspace:
            try:
                plan = workspace.planClose(documentIds)
            except KeyError:
                return False
            choices = self._promptClose(plan, closeAction)
            if choices is None:
                return False
            if not workspace.closeIsCurrent(plan):
                continue
            saves = {docId: "" for docId, choice in choices.items() if choice == "save"}
            projectPath = None
            if saves and workspace.project is None:
                projectPath = self.chooseSaveProject()
                if projectPath is None:
                    return False
            for docId in saves:
                if not workspace.closeIsCurrent(plan):
                    break
                name = self._promptSaveName(workspace.getDocument(docId))
                if name is None:
                    return False
                saves[docId] = name
            if not workspace.closeIsCurrent(plan):
                continue
            if any(not name.strip() for name in saves.values()):
                self.controller.operationFailed.emit("A saved revision requires a nonblank name.")
                return False
            try:
                if preparedProject is not None:
                    self._checkPreparedProject(preparedProject)
                saveProject = None
                if projectPath is not None:
                    projectPath = projectPath.resolve()
                    if projectPath == newProjectPath:
                        saveProject = preparedProject or ProjectRepository.create(projectPath)
                        preparedProject = saveProject
                        self._preparedProject = (projectPath, self._projectIdentity(projectPath))
                    else:
                        saveProject = self.openSaveProject(projectPath)
            except _PROJECT_ERRORS as error:
                self.controller.operationFailed.emit(str(error))
                return False
            newProject = None

            def createProject() -> None:
                nonlocal newProject
                if preparedProject is not None:
                    self._checkPreparedProject(preparedProject)
                newProject = preparedProject or ProjectRepository.create(newProjectPath)

            if not self.controller.applyClose(
                plan, saves, saveProject, beforeRemove=createProject if newProjectPath is not None else None,
            ):
                return False
            if newProject is not None:
                if not self.controller.setProject(newProject):
                    return False
                self._preparedProject = None
                self.controller.message.emit(f"Created project {newProject.path.name}.")
            return True
        return False

    def _checkPreparedProject(self, project: ProjectRepository) -> None:
        if self._preparedProject != (project.path, self._projectIdentity(project.path)):
            raise ValueError("The prepared project changed during the close review.")
