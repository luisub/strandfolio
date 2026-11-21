"""Record and inspect user-reported parentage of existing named project snapshots."""

from collections.abc import Callable
from html import escape
import sqlite3

from PySide6.QtCore import Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from strandfolio.application.workspace import DocumentTarget
from strandfolio.domain.provenance import DeclaredParent
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import addDialogFooter, confirmAction, dialogLayout
from strandfolio.gui.views.reported_parent_map import ReportedParentMap
from strandfolio.storage.revision_codec import sameRevisionSnapshot


_READ_ERRORS = (KeyError, ValueError, OSError, sqlite3.Error)
_WriteCallback = Callable[[str | None], None]


def _entryText(entry: DeclaredParent, name: str) -> str:
    direction = {None: "Orientation unknown", 1: "Forward", -1: "Reverse"}[entry.orientation]
    if entry.sourceSpan is None:
        return f"{name} · Intervals unreported · {direction}"
    return (f"{name} · Parent {entry.sourceSpan[0] + 1:,}–{entry.sourceSpan[1]:,} · "
            f"Child {entry.productSpan[0] + 1:,}–{entry.productSpan[1]:,} · {direction}")


class _ParentDialog(QDialog):
    """Capture one user's reported occurrence, retaining their input after a rejected save."""

    def __init__(
        self, parent: QWidget, names: dict[str, str], childId: str, childLength: int,
        initial: DeclaredParent | None, save: Callable[[DeclaredParent, _WriteCallback], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit reported parent" if initial is not None else "Add reported parent")
        self._save = save
        self._saving = False
        self.resize(490, 340 if initial is not None and initial.sourceSpan is not None else 260)
        layout = dialogLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.parentChoice = QComboBox(self)
        self.parentChoice.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.parentChoice.setMinimumContentsLength(12)
        for revisionId, name in names.items():
            if revisionId != childId:
                self.parentChoice.addItem(name, revisionId)
                self.parentChoice.setItemData(
                    self.parentChoice.count() - 1, escape(name), Qt.ItemDataRole.ToolTipRole,
                )
        if initial is not None:
            self.parentChoice.setCurrentIndex(self.parentChoice.findData(initial.revisionId))
        form.addRow("Saved parent", self.parentChoice)
        self.identity = QLineEdit(self)
        self.identity.setAccessibleName("Parent revision")
        self.identity.setReadOnly(True)
        self.parentChoice.currentIndexChanged.connect(self._showIdentity)
        self._showIdentity()
        form.addRow("Revision", self.identity)
        layout.addLayout(form)
        self.rangesCheck = QCheckBox("Record contributing intervals", self)
        layout.addWidget(self.rangesCheck)
        self.rangeFields = QWidget(self)
        grid = QGridLayout(self.rangeFields)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(QLabel("From (1-based)"), 0, 1)
        grid.addWidget(QLabel("Through (inclusive)"), 0, 2)
        self.sourceFrom, self.sourceThrough = self._intervalFields(grid, 1, "Parent", 2147483647)
        self.childFrom, self.childThrough = self._intervalFields(grid, 2, "Child", childLength)
        self.orientationChoice = QComboBox(self)
        self.orientationChoice.addItem("Unknown", None)
        self.orientationChoice.addItem("Forward", 1)
        self.orientationChoice.addItem("Reverse", -1)
        form.addRow("Orientation", self.orientationChoice)
        layout.addWidget(self.rangeFields)
        self.rangesCheck.toggled.connect(self.rangeFields.setVisible)
        hasRanges = initial is not None and initial.sourceSpan is not None
        self.rangesCheck.setChecked(hasRanges)
        self.rangeFields.setVisible(hasRanges)
        if hasRanges:
            self.sourceFrom.setValue(initial.sourceSpan[0] + 1)
            self.sourceThrough.setValue(initial.sourceSpan[1])
            self.childFrom.setValue(initial.productSpan[0] + 1)
            self.childThrough.setValue(initial.productSpan[1])
        if initial is not None:
            self.orientationChoice.setCurrentIndex(self.orientationChoice.findData(initial.orientation))
        self.error = QLabel(self)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.saveButton = buttons.addButton(
            "Save reported parent", QDialogButtonBox.ButtonRole.AcceptRole,
        )
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        addDialogFooter(layout, buttons, self.saveButton)

    def _showIdentity(self) -> None:
        self.parentChoice.setToolTip(escape(self.parentChoice.currentText()))
        self.identity.setText(self.parentChoice.currentData() or "")
        self.identity.setCursorPosition(0)

    def _intervalFields(
        self, grid: QGridLayout, row: int, name: str, length: int,
    ) -> tuple[QSpinBox, QSpinBox]:
        grid.addWidget(QLabel(name), row, 0)
        start, end = QSpinBox(self.rangeFields), QSpinBox(self.rangeFields)
        for column, (control, suffix) in enumerate(((start, "from"), (end, "through")), 1):
            control.setRange(1, max(1, min(length, 2147483647)))
            control.setAccessibleName(f"{name} {suffix}")
            grid.addWidget(control, row, column)
        return start, end

    def _submit(self) -> None:
        revisionId = self.parentChoice.currentData()
        try:
            sourceSpan = productSpan = None
            if self.rangesCheck.isChecked():
                sourceSpan = (self.sourceFrom.value() - 1, self.sourceThrough.value())
                productSpan = (self.childFrom.value() - 1, self.childThrough.value())
            entry = DeclaredParent(
                revisionId, sourceSpan, productSpan, self.orientationChoice.currentData(),
            )
            self._save(entry, self._saved)
        except _READ_ERRORS as error:
            self.error.setText(str(error))
            return
        self._saving = True
        self.error.setText("Saving reported parent…")
        self.setEnabled(False)

    def _saved(self, error: str | None) -> None:
        self._saving = False
        self.setEnabled(True)
        if error is None:
            self.accept()
        else:
            self.error.setText(error)

    def reject(self) -> None:
        if not self._saving:
            super().reject()


class ReportedParentsView(QWidget):
    """Manage ordered user reports separately from the app's recorded operations."""

    openSnapshotRequested = Signal(str, object)

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workspace = controller.workspace
        self._target: DocumentTarget | None = None
        self._names: dict[str, str] = {}
        self._entries: tuple[DeclaredParent, ...] = ()
        self._ready = False
        self._generation = 0
        self._worker: CalculationTask | None = None
        self._writeTask: CalculationTask | None = None
        self._writeComplete: _WriteCallback | None = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._closed = False
        layout = QVBoxLayout(self)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.parentList = ChoiceRows("User-reported parent occurrences", self)
        self.parentList.activated.connect(self._select)
        self.parentMap = ReportedParentMap(self)
        layout.addWidget(self.parentMap)
        controls = QHBoxLayout()
        self.addButton = QPushButton("Add parent…", self)
        self.editButton = QPushButton("Edit…", self)
        self.removeButton = QPushButton("Remove…", self)
        self.upButton = QPushButton("Move up", self)
        self.downButton = QPushButton("Move down", self)
        self.openButton = QPushButton("Open parent in Sequence", self)
        self.reloadButton = QPushButton("Reload", self)
        for button, callback in (
            (self.addButton, lambda: self._edit(None)),
            (self.editButton, lambda: self._edit(self._selectedIndex())),
            (self.removeButton, self._remove), (self.upButton, lambda: self._move(-1)),
            (self.downButton, lambda: self._move(1)), (self.openButton, self._open),
            (self.reloadButton, self._reload),
        ):
            controls.addWidget(button)
            button.clicked.connect(callback)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(self.parentList, 1)
        self._updateControls()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._reload()

    def refresh(self) -> None:
        """Invalidate the previous document immediately. Load only when this page is visible."""
        document = self._workspace.activeDocument
        target = self._workspace.targetFor(document.documentId) if document is not None else None
        if self._target != target:
            self._reload()

    def _reload(self) -> None:
        if self._closed:
            return
        self._generation += 1
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
        self._entries = ()
        self._names = {}
        self._ready = False
        self.parentList.setRows(())
        self.parentMap.setRecord(0, (), {})
        document = self._workspace.activeDocument
        self._target = self._workspace.targetFor(document.documentId) if document else None
        self._updateControls()
        project = self._workspace.project
        if project is None or document is None or document.isDraft:
            self.status.setText("Open a named project record to inspect or report its parents.")
            return
        if not self.isVisible():
            return
        revision = document.revision
        self.status.setText("Reading reported parents…")

        def read(isCanceled):
            saved = project.getSavedRevision(revision.recordId)
            if isCanceled() or saved is None or saved.revisionId != revision.revisionId:
                return {}, ()
            if not sameRevisionSnapshot(saved, revision):
                raise ValueError("The open revision differs from the saved project snapshot.")
            entries = project.declaredParents(revision.revisionId)
            # Named snapshots persist. Read their labels after the links so new reports
            # committed by another project handle always have corresponding labels.
            names = project.namedRevisionNames()
            if any(entry.revisionId not in names for entry in entries):
                raise ValueError("A reported parent has no named project snapshot.")
            return names, entries

        self._worker = CalculationTask(self._generation, read)
        self._worker.signals.completed.connect(self._loaded)
        self.reloadButton.setEnabled(False)
        self._pool.start(self._worker)

    @Slot(int, object, object)
    def _loaded(self, generation: int, result: object, error: str | None) -> None:
        if generation != self._generation or self._closed:
            return
        self._worker = None
        if self._target is None or not self._workspace.targetIsCurrent(self._target):
            self._reload()
            return
        if error is not None:
            self.status.setText(f"Could not read reported parents. {error}")
        elif result is not None:
            self._names, self._entries = result
            self._ready = self._target.revisionId in self._names
            if self._ready:
                self._display(0 if self._entries else None)
            else:
                self.status.setText("Save this revision as a named record to report its parents.")
        self._updateControls()

    def _display(self, index: int | None) -> None:
        revision = self._workspace.activeDocument.revision
        rows = []
        for position, entry in enumerate(self._entries):
            text = _entryText(entry, self._names[entry.revisionId])
            rows.append(ChoiceRow(position, text, f"{text} · Revision {entry.revisionId}"))
        self.parentList.setRows(tuple(rows))
        self._select(index)
        self.parentMap.setRecord(len(revision), self._entries, self._names)
        if self._entries:
            self.status.setText(f"User-reported parents of {revision.name}")
        elif len(self._names) > 1:
            self.status.setText(f"No parents reported for {revision.name}.")
        else:
            self.status.setText("Save another named record in this project to report it as a parent.")

    def _selectedIndex(self) -> int | None:
        return next(iter(self.parentList.selectedKeys), None)

    def _select(self, index: int | None) -> None:
        self.parentList.setSelected(index)
        self._updateControls()

    def _updateControls(self) -> None:
        index = self._selectedIndex()
        available = self._ready and self._writeTask is None
        selected = available and index is not None
        self.addButton.setEnabled(available and len(self._names) > 1)
        for button in (self.editButton, self.removeButton, self.openButton):
            button.setEnabled(selected)
        self.upButton.setEnabled(selected and index > 0)
        self.downButton.setEnabled(selected and index + 1 < len(self._entries))
        self.reloadButton.setEnabled(
            self._target is not None and not self._target.isDraft
            and self._workspace.project is not None and self._worker is None
            and self._writeTask is None,
        )

    def _edit(self, index: int | None) -> None:
        target, entries = self._target, self._entries
        if not self._ready or target is None or not self._workspace.targetIsCurrent(target):
            self._reload()
            return

        def save(entry: DeclaredParent, completed: _WriteCallback) -> None:
            position = len(entries) if index is None else index
            amended = list(entries)
            if index is None:
                amended.append(entry)
            else:
                amended[index] = entry
            self._commit(target, entries, tuple(amended), position, completed)

        dialog = _ParentDialog(self, self._names, target.revisionId,
                               len(self._workspace.activeDocument.revision),
                               entries[index] if index is not None else None, save)
        dialog.exec()
        dialog.deleteLater()

    def _commit(
        self, target: DocumentTarget, expected: tuple[DeclaredParent, ...],
        entries: tuple[DeclaredParent, ...], index: int | None,
        completed: _WriteCallback | None = None,
    ) -> None:
        if not self._workspace.targetIsCurrent(target) or self._target != target:
            raise ValueError("The active record changed. Reopen the editor on the intended record.")
        if self._writeTask is not None:
            raise ValueError("A reported-parent save is already in progress.")
        project = self._workspace.project

        def finish(error: str | None) -> None:
            if self._workspace.targetIsCurrent(target) and self._target == target:
                if error is None:
                    self._entries = entries
                    self._display(index)
                else:
                    self.status.setText(error)
            elif self._target is not None and self._target.revisionId == target.revisionId:
                self._reload()
            if completed is not None:
                completed(error)

        self._writeComplete = finish
        self._writeTask = CalculationTask(0, lambda _: project.setDeclaredParents(
            target.revisionId, entries, expected=expected, timeout=1.0,
        ))
        self._writeTask.signals.completed.connect(self._writeFinished)
        self.status.setText("Saving reported parents…")
        self._updateControls()
        self._pool.start(self._writeTask)

    @Slot(int, object, object)
    def _writeFinished(self, requestId: int, result: object, error: str | None) -> None:
        completed, self._writeComplete = self._writeComplete, None
        self._writeTask = None
        if completed is not None:
            completed(error)
        self._updateControls()

    def _move(self, direction: int) -> None:
        index = self._selectedIndex()
        if index is None:
            return
        newIndex = index + direction
        if not 0 <= newIndex < len(self._entries):
            return
        amended = list(self._entries)
        amended[index], amended[newIndex] = amended[newIndex], amended[index]
        try:
            self._commit(self._target, self._entries, tuple(amended), newIndex)
        except _READ_ERRORS as error:
            self.status.setText(str(error))

    def _remove(self) -> None:
        target, entries, index = self._target, self._entries, self._selectedIndex()
        if index is None:
            return
        if confirmAction(
            self, "Remove reported parent", "Remove this reported use of the parent?",
            "Remove parent", detail=self._names[entries[index].revisionId],
        ):
            amended = entries[:index] + entries[index + 1:]
            try:
                selected = min(index, len(amended) - 1) if amended else None
                self._commit(target, entries, amended, selected)
            except _READ_ERRORS as error:
                self.status.setText(str(error))

    def _open(self) -> None:
        index = self._selectedIndex()
        if index is None or self._target is None or not self._workspace.targetIsCurrent(self._target):
            self._reload()
            return
        project = self._workspace.project
        try:
            if project.declaredParents(self._target.revisionId) != self._entries:
                raise ValueError("Reported parents have changed. Reload before opening a parent.")
            revision = project.getRevision(self._entries[index].revisionId)
        except _READ_ERRORS as error:
            self.status.setText(str(error))
            return
        self.openSnapshotRequested.emit(revision.revisionId, revision)

    def shutdown(self) -> None:
        """Release the read worker when the workspace closes."""
        self._closed = True
        if self._worker is not None:
            self._worker.cancel()
        self._pool.waitForDone()
