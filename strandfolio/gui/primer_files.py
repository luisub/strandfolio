"""Review and exchange a complete pair beside the editable PCR draft."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSpinBox, QWidget,
)

from strandfolio.domain.primers import PrimerEntry, PrimerRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import (
    addDialogFooter, confirmAction, dialogLayout, fileDialogDirectory,
)
from strandfolio.io.formats import checkExportDestination, exportDestinationVersion
from strandfolio.io.primer_pairs import PairRecord, readPrimerPair, writePrimerPair


_FILTER = "Primer pair FASTA (*.fasta *.fa *.fas *.fna)"


class PrimerPairDialog(QDialog):
    """Confirm record roles and declared tail lengths before replacing a draft."""

    def __init__(self, records: tuple[PairRecord, PairRecord], parent: QWidget) -> None:
        super().__init__(parent)
        self.records = records
        self.setWindowTitle("Load primer pair")
        self.resize(480, 370)
        layout = dialogLayout(self)
        form = QFormLayout()
        self.forwardRecord = QComboBox(self)
        self.forwardRecord.setAccessibleName("Forward record")
        self.forwardRecord.addItem("Choose forward record…", None)
        for index, record in enumerate(records):
            self.forwardRecord.addItem(record.primer.name, index)
        form.addRow("Forward record", self.forwardRecord)
        self.reverseName = QLabel(self)
        self.reverseName.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow("Reverse record", self.reverseName)
        layout.addLayout(form)
        self.tails = []
        for record in records:
            primer = record.primer
            group = QFormLayout()
            name = QLabel(primer.name, self)
            name.setTextFormat(Qt.TextFormat.PlainText)
            preview = QPlainTextEdit(primer.sequence, self)
            preview.setReadOnly(True)
            preview.setTabChangesFocus(True)
            preview.setMaximumHeight(55)
            preview.setAccessibleName(f"{primer.name} complete oligo, 5′ to 3′")
            group.addRow(name, preview)
            tail = QSpinBox(self)
            tail.setRange(0, len(primer.sequence) - 1)
            tail.setValue(primer.tailLength)
            tail.setSuffix(" nt")
            tail.setAccessibleName(f"{primer.name} 5′ tail length")
            group.addRow("5′ tail length", tail)
            self.tails.append(tail)
            layout.addLayout(group)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        self.applyButton = QPushButton("Use pair", self)
        self.applyButton.setDefault(True)
        self.applyButton.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addWidget(cancel)
        actions.addStretch(1)
        actions.addWidget(self.applyButton)
        addDialogFooter(layout, actions, self.applyButton)
        self.forwardRecord.currentIndexChanged.connect(self._updateRoles)
        for index, record in enumerate(records):
            if record.role == "forward":
                self.forwardRecord.setCurrentIndex(index + 1)
        self._updateRoles()

    def _updateRoles(self) -> None:
        index = self.forwardRecord.currentData()
        self.applyButton.setEnabled(index is not None)
        self.reverseName.setText("Choose the forward record first." if index is None
                                 else self.records[1 - index].primer.name)

    def pair(self) -> tuple[PrimerRevision, PrimerRevision]:
        index = self.forwardRecord.currentData()
        if index is None:
            raise ValueError("Choose the forward record.")
        primers = []
        for position in (index, 1 - index):
            primer = self.records[position].primer
            primers.append(PrimerRevision(primer.name, primer.sequence, self.tails[position].value()))
        return primers[0], primers[1]


def _draftKey(controller: WorkspaceController) -> tuple:
    return (controller.activationGeneration, controller.workspace.project,
            controller.pcr.primerNames, controller.pcr.primerTexts)


def loadPrimerPair(controller: WorkspaceController, parent: QWidget) -> None:
    draft = _draftKey(controller)
    filename, _ = QFileDialog.getOpenFileName(parent, "Load primer pair", str(fileDialogDirectory()), _FILTER)
    if not filename or draft != _draftKey(controller):
        return
    try:
        dialog = PrimerPairDialog(readPrimerPair(Path(filename)), parent)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and draft == _draftKey(controller):
                controller.pcr.usePrimerPair(*dialog.pair())
                controller.message.emit(f"Loaded primer pair from {Path(filename).name}.")
        finally:
            dialog.deleteLater()
    except (OSError, ValueError) as error:
        controller.operationFailed.emit(f"Could not load the primer pair. {error}")


def savePrimerPair(
    controller: WorkspaceController, parent: QWidget, *,
    recorded: tuple[PrimerRevision, PrimerRevision] | None = None,
    sourcePath: Path | None = None,
) -> None:
    """Export the editable draft or the immutable pair captured by a result dialog."""
    draft = _draftKey(controller)
    if recorded is None:
        entries, names = controller.pcr.primerEntries, controller.pcr.primerNames
    else:
        forward, reverse = recorded
        entries = (PrimerEntry.fromRevision("Forward", forward), PrimerEntry.fromRevision("Reverse", reverse))
        names = (forward.name, reverse.name)
    protected = tuple(doc.revision.sourcePath for doc in controller.workspace.documents
                      if doc.revision.sourcePath is not None)
    if sourcePath is not None:
        protected += (sourcePath,)
    filename, _ = QFileDialog.getSaveFileName(
        parent, "Save primer pair", str(fileDialogDirectory() / "primers.fasta"), _FILTER,
        options=QFileDialog.Option.DontConfirmOverwrite,
    )
    if not filename or (recorded is None and draft != _draftKey(controller)):
        return
    try:
        chosen = Path(filename)
        path = chosen if chosen.suffix else chosen.with_suffix(".fasta")
        if path.suffix.lower() not in (".fa", ".fas", ".fasta", ".fna"):
            raise ValueError("Choose a .fasta, .fa, .fas or .fna filename.")
        version = exportDestinationVersion(path)
        def checkDestination() -> None:
            if recorded is None and _draftKey(controller) != draft:
                raise ValueError("The active primer pair changed.")
            controller.ensureFigureDestination(path)
            checkExportDestination(path, protected)
        checkDestination()
        if version is not None and not confirmAction(
            parent, "Replace existing file", f"Replace {path.name}?", "Replace",
        ):
            return
        writePrimerPair(path, names, entries, version, checkDestination)
        controller.message.emit(f"Saved primer pair to {path.name}.")
    except (OSError, ValueError) as error:
        controller.operationFailed.emit(f"Primer pair export. {error}")
