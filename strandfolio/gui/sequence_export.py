"""Review and export saved sequences and retrieve captured source files."""

from html import escape
from pathlib import Path
import sqlite3
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QLabel, QTextEdit, QWidget,
)

from strandfolio.application.workspace import DocumentTarget
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout, fileDialogDirectory
from strandfolio.io.formats import (
    FASTA_EXTENSIONS, GENBANK_EXTENSIONS, TEXT_EXTENSIONS, ExportDestinationChangedError, ExportFormat,
    SequenceExportResult, checkExportDestinationVersion, exportDestinationVersion,
)
from strandfolio.storage.project_exports import ExportDelivery, ExportIntentMissingError
from strandfolio.storage.revision_codec import sameRevisionSnapshot


_FORMATS: tuple[tuple[str, ExportFormat, tuple[str, ...]], ...] = (
    ("GenBank", "genbank", GENBANK_EXTENSIONS),
    ("FASTA", "fasta", FASTA_EXTENSIONS),
    ("Plain text", "text", TEXT_EXTENSIONS),
)


class OriginalRetrieval(QObject):
    """Recover a project's captured source file without serializing a sequence."""

    changed = Signal()

    def __init__(self, parent: QWidget, controller: WorkspaceController) -> None:
        super().__init__(parent)
        self._controller = controller
        self._worker: CalculationTask | None = None

    def canRetrieve(self) -> bool:
        workspace = self._controller.workspace
        document = workspace.activeDocument
        return (
            self._worker is None and workspace.project is not None
            and document is not None and document.revision.sourceArtifactId is not None
        )

    @Slot()
    def retrieve(self) -> None:
        workspace = self._controller.workspace
        document, project = workspace.activeDocument, workspace.project
        if self._worker is not None or document is None or project is None:
            return
        artifactId = document.revision.sourceArtifactId
        if artifactId is None:
            return
        target = workspace.targetFor(document.documentId)
        folder = QFileDialog.getExistingDirectory(
            self.parent(), "Retrieve original", str(project.path.parent),
        )
        if not folder or not workspace.targetIsCurrent(target):
            return
        worker = CalculationTask(0, lambda _: project.retrieveOriginal(artifactId, Path(folder)))
        self._worker = worker
        worker.signals.completed.connect(self._finished)
        self.changed.emit()
        self._controller.message.emit("Verifying the captured original…")
        QThreadPool.globalInstance().start(worker)

    @Slot(int, object, object)
    def _finished(self, request: int, result: Path | None, error: str | None) -> None:
        self._worker = None
        self.changed.emit()
        if error:
            self._controller.operationFailed.emit(f"Could not retrieve the original. {error}")
        elif result is not None:
            self._controller.message.emit(f"Retrieved original {result.name}.")


class SequenceExportDialog(QDialog):
    """Stage and read back the saved revision off-thread while the user reviews the file."""

    reviewReady = Signal(object)

    def __init__(
        self, parent: QWidget, controller: WorkspaceController, target: DocumentTarget,
        destination: Path, formatName: ExportFormat, destinationVersion: tuple[int, ...] | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export sequence")
        self.setObjectName("sequenceExportDialog")
        self.resize(570, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._controller = controller
        self._target = target
        self._decision = Event()
        self.destroyed.connect(self._decision.set)
        QCoreApplication.instance().aboutToQuit.connect(self.reject)
        self._approved = False
        self._project = controller.workspace.project
        self._requestId = str(uuid4())
        self._worker: CalculationTask | None = None
        document = controller.workspace.activeDocument
        assert document is not None and self._project is not None
        revision = document.revision
        project = self._project
        protectedPaths = controller.openSourcePaths()

        layout = dialogLayout(self)
        self.status = QLabel("Checking the exchange file…", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.report = QTextEdit(self)
        self.report.setAccessibleName("Export details and format losses")
        self.report.setReadOnly(True)
        self.report.document().setDefaultStyleSheet(
            "h3 { font-size: 13px; color: #403049; margin: 10px 0 3px; }"
            "p { margin: 2px 0; white-space: pre-wrap; }"
        )
        self._fileSummary = (
            f"<h3>Destination</h3><p>{escape(str(destination))}</p>"
            f"<h3>Source revision</h3><p>{escape(revision.name)}<br>{escape(revision.revisionId)}</p>"
        )
        self.report.setHtml(self._fileSummary)
        layout.addWidget(self.report, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.cancelButton = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.exportButton = buttons.addButton(
            "Export" if destinationVersion is None else "Replace file",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.exportButton.setEnabled(False)
        self.exportButton.clicked.connect(self._approve)
        buttons.rejected.connect(self.reject)
        self.cancelButton.setDefault(True)
        addDialogFooter(layout, buttons, self.exportButton)
        self.reviewReady.connect(self._showReport)
        controller.documentChanged.connect(self._checkTarget)

        def confirm(report: SequenceExportResult) -> bool:
            self.reviewReady.emit(report)
            self._decision.wait()
            if self._approved:
                checkExportDestinationVersion(destination, destinationVersion)
            return self._approved

        def publish(_isCanceled):
            try:
                saved = project.getRevision(target.revisionId)
            except KeyError as error:
                raise ValueError("This revision has no saved snapshot in the current project.") from error
            if not sameRevisionSnapshot(saved, revision):
                raise ValueError("The open revision differs from its saved snapshot.")
            try:
                return project.exportSequence(
                    revision, destination, requestId=self._requestId, formatName=formatName,
                    confirm=confirm, protectedPaths=protectedPaths,
                )
            except ExportDestinationChangedError as error:
                return error
            except (OSError, ValueError, KeyError, sqlite3.Error) as error:
                if self._approved:
                    try:
                        hasIntent = project.hasExportIntent(self._requestId)
                    except (OSError, ValueError, sqlite3.Error):
                        # A failed commit can be ambiguous until the database can be read.
                        hasIntent = None
                    if hasIntent is False:
                        return ExportIntentMissingError(str(error))
                raise

        self._start(CalculationTask(0, publish))

    def _start(self, worker: CalculationTask) -> None:
        self._worker = worker
        worker.signals.completed.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _checkTarget(self) -> None:
        if not self._approved and not self._controller.workspace.targetIsCurrent(self._target):
            self.reject()

    @Slot(object)
    def _showReport(self, report: SequenceExportResult) -> None:
        if self._decision.is_set():
            return
        sections = []
        if report.warnings:
            sections.append("<h3>Format warnings</h3>")
            sections.extend(f"<p>{escape(warning)}</p>" for warning in report.warnings)
        sections.append(self._fileSummary)
        if report.emittedName is not None:
            sections.append(f"<p>Name in export: {escape(report.emittedName)}</p>")
        if report.serializationDetails:
            sections.append("<h3>File details</h3>")
            sections.extend(f"<p>{escape(detail)}</p>" for detail in report.serializationDetails)
        self.report.setHtml("".join(sections))
        self.status.setText("Review the file before exporting.")
        self.exportButton.setEnabled(True)

    def _approve(self) -> None:
        if not self._controller.workspace.targetIsCurrent(self._target):
            self.reject()
            return
        self._approved = True
        self.exportButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        self.status.setText("Saving and registering the verified file…")
        self._decision.set()

    def reject(self) -> None:
        """Cancel before approval and let the worker release its staged bytes."""
        if self._worker is None:
            super().reject()
        elif not self._approved:
            self._decision.set()
            self.cancelButton.setEnabled(False)
            self.exportButton.setEnabled(False)
            self.status.setText("Canceling export…")

    @Slot(int, object, object)
    def _finished(
        self, request: int,
        delivery: ExportDelivery | ExportDestinationChangedError | ExportIntentMissingError | None,
        error: str | None,
    ) -> None:
        self._worker = None
        if isinstance(delivery, ExportDestinationChangedError):
            self._controller.operationFailed.emit(str(delivery))
            super().reject()
        elif isinstance(delivery, ExportIntentMissingError):
            self._controller.operationFailed.emit(f"Could not export sequence. {delivery}")
            super().reject()
        elif error and self._approved:
            self.status.setText("Export could not be confirmed.")
            self.report.setPlainText(f"{error}\n\nExport request {self._requestId}")
            self.cancelButton.setText("Close")
            self.cancelButton.setEnabled(True)
            self.exportButton.setText("Retry registration")
            self.exportButton.setEnabled(True)
            self.exportButton.clicked.disconnect()
            self.exportButton.clicked.connect(self._retry)
        elif error:
            self._controller.operationFailed.emit(f"Could not export sequence. {error}")
            super().reject()
        elif delivery is None:
            super().reject()
        else:
            self._controller.message.emit(f"Exported {delivery.destinationPath.name}.")
            super().accept()

    def _retry(self) -> None:
        def register(_isCanceled):
            try:
                return self._project.retryExport(self._requestId)
            except ExportIntentMissingError as error:
                return error

        self.exportButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        self.status.setText("Verifying the previously confirmed delivery…")
        self._start(CalculationTask(1, register))


def exportSavedSequence(parent: QWidget, controller: WorkspaceController) -> None:
    """Choose an exchange format and review its exact staged output for a saved record."""
    workspace = controller.workspace
    document = workspace.activeDocument
    if document is None or document.isDraft or workspace.project is None:
        controller.message.emit("Open a saved project sequence to export.")
        return
    target = workspace.targetFor(document.documentId)
    choices = {
        f"{label} ({' '.join('*' + suffix for suffix in extensions)})": (formatName, extensions[0])
        for label, formatName, extensions in _FORMATS
        if formatName != "genbank" or document.revision.moleculeType == "DNA"
    }
    stem = Path(document.revision.name).name or "sequence"
    filename, selected = QFileDialog.getSaveFileName(
        parent, "Export sequence", str(fileDialogDirectory() / stem), ";;".join(choices),
    )
    if not filename or not workspace.targetIsCurrent(target):
        return
    formatName, suffix = choices[selected or next(iter(choices))]
    destination = Path(filename)
    if not destination.suffix:
        destination = destination.with_suffix(suffix)
    try:
        destinationVersion = exportDestinationVersion(destination)
    except OSError as error:
        controller.operationFailed.emit(f"Could not inspect export destination. {error}")
        return
    dialog = SequenceExportDialog(parent, controller, target, destination, formatName, destinationVersion)
    dialog.open()
