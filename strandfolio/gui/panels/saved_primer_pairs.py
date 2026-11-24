"""Manage named primer pairs in the current session or project."""

from collections import Counter
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QWidget,
)
from shiboken6 import isValid

from strandfolio.domain.primers import PrimerEntry, PrimerRevision, SavedPrimerPair
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import confirmAction, dialogLayout


class _PairEditor(QDialog):
    def __init__(
        self, parent: QWidget, name: str, primers: tuple[PrimerRevision, PrimerRevision] | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit primer pair" if name else "New primer pair")
        self.setMinimumWidth(450)
        layout = dialogLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.pairName = QLineEdit(name)
        form.addRow("Pair name", self.pairName)
        self.fields = []
        for i, role in enumerate(("Forward", "Reverse")):
            member = primers[i] if primers else None
            memberName = QLineEdit(member.name if member else role)
            bases = QLineEdit(member.annealingSequence if member else "")
            tail = QLineEdit(member.tailSequence if member else "")
            form.addRow(f"{role} name", memberName)
            form.addRow(f"{role} annealing (5′→3′)", bases)
            form.addRow(f"{role} 5′ tail", tail)
            self.fields.append((memberName, bases, tail))
        layout.addLayout(form)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.pairName.selectAll()

    def values(self, primers):
        name = self.pairName.text().strip()
        if not name:
            raise ValueError("Enter a name for the pair.")
        members = []
        for i, (role, fields) in enumerate(zip(("Forward", "Reverse"), self.fields, strict=True)):
            memberName, bases, tail = (field.text() for field in fields)
            entry = PrimerEntry(role, bases, tail)
            if entry.problem:
                raise ValueError(entry.problem)
            members.append(entry.toRevision(memberName, primers[i] if primers else None))
        return name, members[0], members[1]


class SavedPrimerPairsDialog(QDialog):
    """Select, revise and reuse stored pairs while keeping historical runs immutable."""

    def __init__(self, controller: WorkspaceController, sessionPairs: dict, parent: QWidget) -> None:
        super().__init__(parent)
        self.controller = controller
        self.project = controller.workspace.project
        self.sessionPairs = sessionPairs
        self.context = controller.workspace.taskContext(True)
        self.editor = None
        self.rows = {}
        self.setWindowTitle("Saved primer pairs")
        self.setMinimumWidth(490)
        layout = dialogLayout(self)
        scope = f"Project · {self.project.path.name}" if self.project else "Current session"
        destination = controller.displayName if controller.session.current else "untitled primer draft"
        heading = QLabel(f"{scope} · Editor {destination}")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.list = QListWidget()
        self.list.setAccessibleName("Named primer pairs")
        layout.addWidget(self.list)
        actions = QHBoxLayout()
        self.newButton = QPushButton("New pair…")
        self.editButton = QPushButton("Edit…")
        self.duplicateButton = QPushButton("Duplicate…")
        self.deleteButton = QPushButton("Delete…")
        for button, mode in ((self.newButton, "new"), (self.editButton, "edit"),
                             (self.duplicateButton, "duplicate")):
            button.clicked.connect(lambda checked=False, action=mode: self._openEditor(action))
            actions.addWidget(button)
        self.deleteButton.clicked.connect(self._delete)
        actions.addWidget(self.deleteButton)
        layout.addLayout(actions)
        self.updateButton = QPushButton("Update selected pair from editor…")
        self.updateButton.clicked.connect(lambda: self._openEditor("update"))
        layout.addWidget(self.updateButton)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        self.reloadButton = QPushButton("Reload list")
        self.reloadButton.clicked.connect(self._load)
        footer.addWidget(self.reloadButton)
        footer.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.useButton = QPushButton("Use in editor")
        self.useButton.clicked.connect(self._use)
        footer.addWidget(self.useButton)
        layout.addLayout(footer)
        self.list.currentRowChanged.connect(self._selectionChanged)
        self.list.itemDoubleClicked.connect(self._use)
        controller.documentChanged.connect(self._checkContext)
        controller.projectChanged.connect(self._checkContext)
        self._load()

    def _checkContext(self) -> None:
        if self.context != self.controller.workspace.taskContext(True):
            if self.editor:
                self.editor.reject()
            self.reject()

    def _selected(self):
        item = self.list.currentItem()
        return self.rows.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selectionChanged(self) -> None:
        pair = self._selected()
        for button in (self.editButton, self.duplicateButton, self.deleteButton, self.useButton):
            button.setEnabled(pair is not None)
        self.updateButton.setEnabled(pair is not None and all(
            not member.problem for member in self.controller.pcr.primerEntries
        ))
        self.summary.setText(
            f"Forward · {pair.forward.name}  5′–{pair.forward.sequence}–3′\n"
            f"Reverse · {pair.reverse.name}  5′–{pair.reverse.sequence}–3′" if pair else ""
        )

    def _showRows(self, pairs, selected=None) -> None:
        self.rows = {pair.pairId: pair for pair in pairs}
        self.list.clear()
        counts = Counter(pair.name for pair in pairs)
        for pair in pairs:
            label = pair.name if counts[pair.name] == 1 else f"{pair.name} · {pair.pairId[:8]}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pair.pairId)
            self.list.addItem(item)
            if pair.pairId == selected:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and pairs:
            self.list.setCurrentRow(0)
        self._selectionChanged()
        self.notice.setText("" if pairs else "No saved primer pairs")

    def _load(self, selected=None) -> None:
        if self.project is None:
            self._showRows(tuple(self.sessionPairs.values()), selected)
            return
        self.notice.setText("Loading saved pairs…")
        self.setEnabled(False)
        def loaded(pairs, error):
            if not isValid(self) or self.context != self.controller.workspace.taskContext(True):
                return
            self.setEnabled(True)
            if error:
                self.notice.setText(error)
                return
            self._showRows(pairs, selected)
        self.controller.projectTasks.submit(self.project.listPrimerPairs, loaded)

    def _openEditor(self, mode: str) -> None:
        existing = self._selected()
        if mode == "new":
            try:
                members = self.controller.pcr.currentPrimerRevisions()
            except ValueError:
                members = None
            name = ""
        elif existing is None:
            return
        elif mode == "update":
            try:
                members = self.controller.pcr.currentPrimerRevisions()
            except ValueError as error:
                self.notice.setText(str(error))
                return
            name = existing.name
        else:
            members = (existing.forward, existing.reverse)
            name = f"{existing.name} copy" if mode == "duplicate" else existing.name
        editor = _PairEditor(self, name, members)
        if mode == "update":
            editor.setWindowTitle("Update saved pair from editor")
        if mode == "new" and members is None:
            texts, names = self.controller.pcr.primerTexts, self.controller.pcr.primerNames
            for i, (memberName, bases, tail) in enumerate(editor.fields):
                memberName.setText(names[i])
                bases.setText(texts[i])
                tail.setText(texts[i + 2])
        self.editor = editor
        def save():
            try:
                name, forward, reverse = editor.values(members)
                pair = (replace(existing, name=name, forward=forward, reverse=reverse)
                        if mode in ("edit", "update") else SavedPrimerPair(name, forward, reverse))
            except ValueError as error:
                editor.notice.setText(str(error))
                return
            def stored(_result, error):
                if not isValid(editor) or self.editor is not editor:
                    return
                editor.setEnabled(True)
                if error:
                    editor.notice.setText(error)
                    return
                editor.accept()
                self._load(pair.pairId)
            if self.project is None:
                self.sessionPairs[pair.pairId] = pair
                stored(pair, None)
            else:
                editor.setEnabled(False)
                project = self.project
                expected = existing if mode in ("edit", "update") else None
                self.controller.projectTasks.submit(
                    lambda: project.savePrimerPair(pair, expected=expected, timeout=1), stored, write=True,
                )
        editor.buttons.accepted.connect(save)
        editor.finished.connect(self._editorClosed)
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.open()

    def _editorClosed(self) -> None:
        self.editor = None

    def _delete(self) -> None:
        pair = self._selected()
        if pair is None or not confirmAction(
            self, "Delete saved pair", f"Remove {pair.name} from Saved pairs?", "Delete pair",
            detail="Previously recorded runs and exported files keep their copies.",
        ) or self.context != self.controller.workspace.taskContext(True):
            return
        if self.project is None:
            self.sessionPairs.pop(pair.pairId, None)
            self._load()
            return
        self.setEnabled(False)
        project = self.project
        def deleted(_value, error):
            if not isValid(self):
                return
            self.setEnabled(True)
            if error:
                self.notice.setText(error)
            else:
                self._load()
        self.controller.projectTasks.submit(lambda: project.deletePrimerPair(pair, timeout=1), deleted, write=True)

    def _use(self) -> None:
        pair = self._selected()
        if pair and applyPairToEditor(self.controller, pair.forward, pair.reverse, self):
            self.accept()


def applyPairToEditor(
    controller: WorkspaceController, forward: PrimerRevision, reverse: PrimerRevision, parent: QWidget,
    *, otherTemplate: bool = False,
) -> bool:
    """Apply a named pair only to the reviewed active document and draft."""
    pcr = controller.pcr
    context = controller.workspace.taskContext(True)
    before = (pcr.primerTexts, pcr.primerNames)
    incoming = (forward.annealingSequence, reverse.annealingSequence, forward.tailSequence, reverse.tailSequence)
    if otherTemplate or any(before[0]) and before != (incoming, (forward.name, reverse.name)):
        destination = controller.displayName if controller.session.current else "the current primer draft"
        if not confirmAction(parent, "Use pair in editor",
                             f"Replace the current primer draft on {destination}?", "Use pair"):
            return False
    if context != controller.workspace.taskContext(True) or before != (pcr.primerTexts, pcr.primerNames):
        return False
    try:
        pcr.usePrimerPair(forward, reverse)
    except ValueError as error:
        controller.operationFailed.emit(str(error))
        return False
    return True
