"""Open the bundled Assembly example and save named copies in a project."""

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import QObject, QThreadPool, Qt, Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit

from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.gui.panels.document_dialogs import DocumentDialogs
from strandfolio.io.formats import SequenceImportResult, readSequence
from strandfolio.storage.projects import ProjectRepository

if TYPE_CHECKING:
    from strandfolio.gui.window import MainWindow


class _SaveCopyDialog(QDialog):
    """Keep the entered name editable if saving a copy fails."""

    def __init__(self, window: "MainWindow", source: Path, preview: SequenceImportResult) -> None:
        super().__init__(window)
        self._window = window
        self._source = source
        self._preview = preview
        self._context = window.controller.workspace.taskContext(False)
        self._worker: CalculationTask | None = None
        self.savedCopy: tuple[ProjectRepository, SequenceImportResult, SequenceRevision] | None = None
        self.setObjectName("saveAssemblyExample")
        self.setWindowTitle("Save named copy")
        self.setMinimumWidth(370)
        self.resize(430, 205)
        layout = dialogLayout(self)
        label = QLabel("Record name", self)
        self.nameField = QLineEdit(preview.records[0].name, self)
        self.nameField.setAccessibleName("Record name")
        label.setBuddy(self.nameField)
        layout.addWidget(label)
        layout.addWidget(self.nameField)
        self.notice = QLabel(self)
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self,
        )
        self.buttons.accepted.connect(self._submit)
        self.buttons.rejected.connect(self.reject)
        self.nameField.textChanged.connect(lambda name: self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setEnabled(bool(name.strip())))
        addDialogFooter(layout, self.buttons, self.buttons.button(QDialogButtonBox.StandardButton.Save))
        self.nameField.selectAll()

    def _submit(self) -> None:
        name = self.nameField.text().strip()
        if self._worker is not None or not name:
            return
        workspace = self._window.controller.workspace
        project = workspace.project
        destination = None
        if project is None:
            destination = self._window.documentDialogs.chooseSaveProject(self)
            if destination is None:
                return
        if workspace.taskContext(False) != self._context:
            self.reject()
            return
        source, reviewedHash = self._source, self._preview.source.sha256

        def save(_isCanceled):
            # A fresh capture gives each named copy its own identity and original bytes.
            capture = readSequence(source)
            if capture.source.sha256 != reviewedHash:
                raise ValueError("The example file changed. Reopen the preview before saving.")
            repository = project if project is not None else DocumentDialogs.openSaveProject(destination)
            named = repository.saveRevision(
                capture.records[0], name, requestId=str(uuid4()), capture=capture, independent=True,
            )
            return repository, capture, named

        self._worker = CalculationTask(0, save)
        self._worker.signals.completed.connect(self._finished)
        self.nameField.setEnabled(False)
        self.buttons.setEnabled(False)
        self.notice.setText("Saving copy…")
        self.notice.show()
        QThreadPool.globalInstance().start(self._worker)

    @Slot(int, object, object)
    def _finished(self, request: int, result: object, error: str | None) -> None:
        self._worker = None
        if error is None and result is not None:
            self.savedCopy = result
            self.accept()
            return
        self.notice.setText(error or "The copy could not be saved.")
        self.nameField.setEnabled(True)
        self.buttons.setEnabled(True)
        self.nameField.setFocus()
        self.nameField.selectAll()

    def reject(self) -> None:
        if self._worker is None:
            super().reject()


class AssemblyExampleActions(QObject):
    """Load the packaged product independently of the open documents and live input plan."""

    def __init__(self, window: "MainWindow", source: Path) -> None:
        super().__init__(window)
        self._window = window
        self._source = source
        self._preview: SequenceImportResult | None = None
        self._reader: CalculationTask | None = None
        self._readId = 0
        view = window.assemblyView
        view.exampleRequested.connect(self._open)
        view.saveExampleCopyRequested.connect(self._save)
        view.exampleSequenceRequested.connect(window.controller.openSavedRevision)
        view.exampleHistoryRequested.connect(self._openHistory)
        window.workflows.currentChanged.connect(self._cancelRead)
        window.controller.projectChanged.connect(self._cancelRead)

    @Slot()
    def _cancelRead(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
            self._readId += 1

    @Slot()
    def _open(self) -> None:
        if self._reader is not None:
            return
        source = self._source
        self._readId += 1
        self._reader = CalculationTask(self._readId, lambda _: readSequence(source))
        self._reader.signals.completed.connect(self._loaded)
        QThreadPool.globalInstance().start(self._reader)

    @Slot(int, object, object)
    def _loaded(self, request: int, result: SequenceImportResult | None, error: str | None) -> None:
        if request != self._readId:
            return
        self._reader = None
        view = self._window.assemblyView
        if not view.isVisible() or view.pickerToken is not None:
            return
        if error:
            self._window.controller.operationFailed.emit(error)
            return
        if result is None:
            return
        record = result.records[0]
        attribution = record.illustrationAttribution
        if attribution is None:
            self._window.controller.operationFailed.emit("The example is missing its source credits.")
            return
        self._preview = result
        view.showRecordedExample(record, attribution.backboneSource, attribution.insertLabel)

    @Slot()
    def _save(self) -> None:
        window = self._window
        view = window.assemblyView
        if self._preview is None or not view.isShowingExample:
            return
        workspace = window.controller.workspace
        context = workspace.taskContext(False)
        dialog = _SaveCopyDialog(window, self._source, self._preview)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        savedCopy = dialog.savedCopy
        dialog.deleteLater()
        if not accepted or savedCopy is None:
            return
        project, capture, saved = savedCopy
        if context != workspace.taskContext(False):
            window.controller.message.emit(f"Saved {saved.name} to {project.path}.")
            return
        if window.controller.openSavedCopy(project, capture, saved):
            view.setSavedExample(saved)
            window.controller.message.emit(f"Saved {saved.name}.")

    @Slot(str)
    def _openHistory(self, revisionId: str) -> None:
        window = self._window

        def showHistory(_target) -> None:
            window.workflows.setCurrentWidget(window.historyView)
            window.historyView.showSourceAttribution()

        window.controller.openExactRevision(revisionId, None, afterOpen=showHistory)
