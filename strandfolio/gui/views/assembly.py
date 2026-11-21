"""Choose and inspect Assembly inputs for the current session."""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSplitter,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from strandfolio.application.assembly_session import (
    AssemblyDigestInput, AssemblyOccurrence, AssemblySession, AssemblySource, isAssemblySource,
)
from strandfolio.application.pcr_session import AssemblyPcrInput
from strandfolio.application.tasks import TaskInput, TaskRequest
from strandfolio.application.workspace import DocumentTarget
from strandfolio.domain.fragments import DNAFragment
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import confirmAction
from strandfolio.gui.panels.document_strip import documentLabels
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.views.assembly_example import AssemblyDesignExample
from strandfolio.gui.views.assembly_junctions import AssemblyJunctionReview, recordedEndTypes
from strandfolio.gui.views.gel_preview import formatPcrSource, formatSourceSpans
from strandfolio.operations.pcr import PcrProduct
from strandfolio.operations.restriction import RestrictionDigest


_TASK_OWNER = "assembly"
_TASK_SLOT = "source"
_SOURCE_CHANGED = "The selected source changed or closed. Choose a current source."


@dataclass(frozen=True, slots=True)
class _SourceChoice:
    source: AssemblySource
    binding: TaskInput | None = None

    @property
    def key(self) -> TaskInput | OutputReference:
        if self.binding is not None:
            return self.binding
        reference = self.source.outputReference
        assert reference is not None
        return reference


def _label(text: str, parent: QWidget, name: str = "") -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setObjectName(name)
    return label


class _RoleCard(QFrame):
    """Show the role and captured fragment represented by this card."""

    def __init__(self, role: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("backboneCard" if role == "Backbone" else "insertCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 14)
        layout.setSpacing(7)
        heading = QHBoxLayout()
        self.role = _label(role, self, "sectionTitle")
        self.orientation = _label("", self, "muted")
        self.orientation.setWordWrap(False)
        heading.addWidget(self.role, 1)
        heading.addWidget(self.orientation)
        layout.addLayout(heading)
        self.name = FittedLabel("", self)
        self.name.setObjectName("documentTitle")
        self.metadata = _label("", self, "muted")
        layout.addWidget(self.name)
        layout.addWidget(self.metadata)
        layout.addStretch(1)
        self.chooseButton = QPushButton(self)
        layout.addWidget(self.chooseButton, alignment=Qt.AlignmentFlag.AlignLeft)

    def showOccurrence(self, role: str, occurrence: AssemblyOccurrence) -> None:
        self.role.setText(role)
        source = occurrence.source
        self.name.setFullText(source.label if source else "No source selected")
        self.orientation.setVisible(source is not None)
        self.metadata.setVisible(source is not None)
        if source is not None:
            direction = "Forward →" if occurrence.orientation == "forward" else "← Reverse"
            self.orientation.setText(direction)
            revision = source.revision
            topology = f"{revision.topology} DNA" if revision.topology else "DNA · topology unspecified"
            self.metadata.setText(f"{len(revision):,} nt · {topology}")
        action = "Change" if source else "Choose"
        self.chooseButton.setText(f"{action} {role.lower()}…")


def _sourceDetails(source: AssemblySource) -> str:
    revision = source.revision
    topology = revision.topology or "topology unspecified"
    details = [f"DNA · {len(revision):,} nt · {topology} · {recordedEndTypes(revision)}"]
    value = source.value
    if isinstance(value, AssemblyDigestInput):
        details.append(f"Source bases {formatSourceSpans(value.fragment.sourceSpans)}")
    elif isinstance(value, AssemblyPcrInput):
        details.append(formatPcrSource(value.product))
    if source.outputReference is not None:
        reference = source.outputReference
        details.append(f"Run {reference.operationId} · Output {reference.outputKey}")
    details.append(f"Source revision {source.sourceRevisionId}")
    if source.sourceRevisionId != revision.revisionId:
        details.append(f"Output revision {revision.revisionId}")
    return "\n".join(details)


def _digestSource(run: RestrictionDigest, fragment: DNAFragment, index: int | None = None) -> AssemblySource:
    label = f"Digest · {run.template.name} · {fragment.outputKey} · {run.operationId[:8]}"
    return AssemblySource(label, AssemblyDigestInput(run, fragment), index)


def _pcrSource(preparation: AssemblyPcrInput, index: int | None = None) -> AssemblySource:
    run, product = preparation.run, preparation.product
    label = f"PCR · {run.template.name} · {product.outputKey} · {run.operationId[:8]}"
    return AssemblySource(label, preparation, index)


class AssemblyView(QWidget):
    """Review sources before pinning a backbone or ordered insert slot."""

    openFileRequested = Signal()
    openSavedRequested = Signal()
    pasteRequested = Signal()
    exampleRequested = Signal()
    saveExampleCopyRequested = Signal()
    exampleHistoryRequested = Signal(str)
    exampleSequenceRequested = Signal(str)

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.session = AssemblySession()
        self._workspace = controller.workspace
        self._nextPickerToken = 0
        self._pickerToken: int | None = None
        self._pickerOpener: QPushButton | None = None
        self._targetId: str | None = None
        self._offeredSource: AssemblySource | None = None
        self._sourceTaskDescription = ""
        self._sourceRequest: TaskRequest | None = None
        self._selectionNotice = ""
        self._retainedChoices: tuple[_SourceChoice, ...] = ()
        self._choices: dict[TaskInput | OutputReference, _SourceChoice] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.workflowTools = QWidget(self, objectName="workflowTools")
        toolbar = QHBoxLayout(self.workflowTools)
        toolbar.setContentsMargins(2, 5, 14, 5)
        toolbar.setSpacing(6)
        toolbar.addStretch()
        self.exampleButton = QPushButton("View design example", self.workflowTools)
        self.exampleButton.clicked.connect(self.exampleRequested)
        self.clearButton = QPushButton("Clear inputs…", self.workflowTools)
        self.clearButton.clicked.connect(self._confirmClear)
        self.backToInputsButton = QPushButton("Back to inputs", self.workflowTools)
        self.backToInputsButton.clicked.connect(self._showInputs)
        self.saveExampleButton = QPushButton("Save named copy…", self.workflowTools, objectName="primary")
        self.saveExampleButton.clicked.connect(self.saveExampleCopyRequested)
        for button in (self.exampleButton, self.clearButton, self.backToInputsButton, self.saveExampleButton):
            toolbar.addWidget(button)
        layout.addWidget(self.workflowTools)
        self.pages = QStackedWidget(self)
        layout.addWidget(self.pages, 1)
        stage = QSplitter(Qt.Orientation.Horizontal, self.pages)
        stage.setChildrenCollapsible(False)
        stage.addWidget(self._createPlan())
        stage.addWidget(self._createInspector())
        stage.setStretchFactor(0, 1)
        stage.setSizes([950, 352])
        self.pages.addWidget(stage)
        self.designExample = AssemblyDesignExample(self.pages)
        self.designExample.historyRequested.connect(self.exampleHistoryRequested)
        self.designExample.sequenceRequested.connect(self.exampleSequenceRequested)
        self.pages.addWidget(self.designExample)
        self.pages.currentChanged.connect(self._updateTools)
        self._updateTools()

        controller.collectionChanged.connect(self._refreshChoices)
        controller.loadingChanged.connect(self.pasteButton.setDisabled)
        controller.nameChanged.connect(self._refreshChoices)
        controller.projectChanged.connect(self._reset)
        controller.analysis.historyChanged.connect(self._refreshRetained)
        controller.analysis.assemblyDigestionsChanged.connect(self._refreshRetained)
        controller.pcr.historyChanged.connect(self._refreshRetained)
        self._refreshPlan()

    @property
    def isShowingExample(self) -> bool:
        """Return whether the bundled example is visible."""
        return self.pages.currentWidget() is self.designExample

    def _updateTools(self) -> None:
        example = self.isShowingExample
        self.exampleButton.setVisible(not example)
        self.clearButton.setVisible(not example)
        self.backToInputsButton.setVisible(example)
        self.saveExampleButton.setVisible(example)

    def _showInputs(self) -> None:
        self.pages.setCurrentIndex(0)

    def showRecordedExample(
        self, product: SequenceRevision, backboneLabel: str, insertLabel: str,
    ) -> None:
        """Show the existing product while retaining all pending or confirmed input work."""
        if self.pickerToken is None:
            self.designExample.setProduct(product, backboneLabel, insertLabel)
            self.pages.setCurrentWidget(self.designExample)

    def setSavedExample(self, revision: SequenceRevision | None) -> None:
        """Retain the exact saved copy for navigation in this project session."""
        self.designExample.setSavedCopy(revision)

    @property
    def pickerToken(self) -> int | None:
        """Identify the current picker so late intake cannot retarget a newer choice."""
        return self._pickerToken

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.pickerToken is not None:
            self._focusPicker()

    def _createPlan(self) -> QWidget:
        panel = QFrame(self)
        panel.setObjectName("workflowCanvas")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        layout.addWidget(_label("Source fragments", panel, "sectionTitle"))

        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.backboneCard = _RoleCard("Backbone", panel)
        self.insertCard = _RoleCard("Insert", panel)
        cards.addWidget(self.backboneCard, 1)
        cards.addWidget(self.insertCard, 1)
        layout.addLayout(cards)
        self.chooseBackboneButton = self.backboneCard.chooseButton
        self.chooseInsertButton = self.insertCard.chooseButton
        self.chooseBackboneButton.clicked.connect(self._chooseBackbone)
        self.chooseInsertButton.clicked.connect(self._chooseCardInsert)
        extra = QHBoxLayout()
        extra.setSpacing(16)
        extra.addStretch(1)
        belowInsert = QHBoxLayout()
        self.addInsertButton = QPushButton("Add insert…", panel)
        self.addInsertButton.clicked.connect(self._chooseInsert)
        belowInsert.addWidget(self.addInsertButton)
        belowInsert.addStretch(1)
        extra.addLayout(belowInsert, 1)
        layout.addLayout(extra)

        self.orderLabel = _label("Input order", panel, "sectionTitle")
        layout.addWidget(self.orderLabel)
        self.occurrencesList = ChoiceRows("Assembly roles in order", panel)
        self.occurrencesList.setObjectName("assemblyOrder")
        self.occurrencesList.setMinimumWidth(160)
        layout.addWidget(self.occurrencesList, 1)
        self.occurrencesList.activated.connect(self._inspectRole)
        layout.addStretch(1)
        return panel

    def _createInspector(self) -> QWidget:
        inspector = QFrame(self)
        inspector.setObjectName("workflowInspector")
        inspector.setMinimumWidth(350)
        layout = QVBoxLayout(inspector)
        layout.setContentsMargins(18, 16, 18, 16)
        self.inspectorTabs = QTabWidget(inspector)
        self.inspectorTabs.setAccessibleName("Assembly inspector")
        self._roleDetails = QWidget(self.inspectorTabs)
        details = QVBoxLayout(self._roleDetails)
        details.setContentsMargins(6, 8, 6, 6)
        self.inspectedRole = _label("", inspector, "documentTitle")
        details.addWidget(self.inspectedRole)
        self.planDetails = _label("", inspector, "muted")
        self.planDetails.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.planDetails)

        self._editControls = QWidget(inspector)
        editLayout = QVBoxLayout(self._editControls)
        editLayout.setContentsMargins(0, 4, 0, 0)
        self.openSourceButton = QPushButton("Open source in Sequence", inspector)
        self.openSourceButton.clicked.connect(self._openSelectedSource)
        editLayout.addWidget(self.openSourceButton, 0, Qt.AlignmentFlag.AlignLeft)
        editing = QHBoxLayout()
        self.flipButton = QPushButton("Flip", inspector)
        self.removeButton = QPushButton("Remove", inspector)
        editing.addWidget(self.flipButton)
        editing.addWidget(self.removeButton)
        editing.addStretch(1)
        editLayout.addLayout(editing)
        order = QHBoxLayout()
        self.moveEarlierButton = QPushButton("Move up", inspector)
        self.moveLaterButton = QPushButton("Move down", inspector)
        order.addWidget(self.moveEarlierButton)
        order.addWidget(self.moveLaterButton)
        order.addStretch(1)
        editLayout.addLayout(order)
        self.moveEarlierButton.clicked.connect(lambda: self._moveSelected(-1))
        self.moveLaterButton.clicked.connect(lambda: self._moveSelected(1))
        self.flipButton.clicked.connect(self._flipSelected)
        self.removeButton.clicked.connect(self._removeSelected)
        details.addWidget(self._editControls)
        details.addStretch(1)
        self.inspectorTabs.addTab(self._roleDetails, "Source details")
        self.junctionReview = AssemblyJunctionReview(self.inspectorTabs)
        self.inspectorTabs.addTab(self.junctionReview, "Planned junctions")
        layout.addWidget(self.inspectorTabs, 1)
        layout.addWidget(self._createPicker(), 1)
        return inspector

    def _createPicker(self) -> QWidget:
        self._picker = QFrame(self)
        layout = QVBoxLayout(self._picker)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pickerTitle = _label("", self._picker, "documentTitle")
        layout.addWidget(self.pickerTitle)
        self.roleSelector = QComboBox(self._picker)
        self.roleSelector.setAccessibleName("Assign to Assembly role")
        self.roleSelector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.roleSelector.setMinimumContentsLength(10)
        layout.addWidget(self.roleSelector)
        self.sourceList = ChoiceRows("Assembly sources", self._picker)
        self.sourceList.setObjectName("assemblyCandidates")
        self.sourceList.setMinimumWidth(160)
        layout.addWidget(self.sourceList, 1)
        self.sourceDetails = _label("", self._picker, "muted")
        self.sourceDetails.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.sourceDetails)

        self._intakeActions = QWidget(self._picker)
        intake = QGridLayout(self._intakeActions)
        intake.setContentsMargins(0, 0, 0, 0)
        self.openFileButton = QPushButton("Open file…", self._picker)
        self.openSavedButton = QPushButton("Open saved…", self._picker)
        self.pasteButton = QPushButton("Paste sequence…", self._picker)
        intake.addWidget(self.openSavedButton, 0, 0)
        intake.addWidget(self.openFileButton, 0, 1)
        intake.addWidget(self.pasteButton, 1, 0, 1, 2)
        layout.addWidget(self._intakeActions)
        actions = QHBoxLayout()
        self.cancelButton = QPushButton("Cancel", self._picker)
        self.confirmButton = QPushButton("Assign source", self._picker)
        self.confirmButton.setObjectName("primary")
        actions.addStretch(1)
        actions.addWidget(self.cancelButton)
        actions.addWidget(self.confirmButton)
        layout.addLayout(actions)
        self.openFileButton.clicked.connect(self.openFileRequested)
        self.openSavedButton.clicked.connect(self.openSavedRequested)
        self.pasteButton.clicked.connect(self.pasteRequested)
        self.cancelButton.clicked.connect(self._cancelFromPicker)
        escape = QShortcut(Qt.Key.Key_Escape, self._picker)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self._cancelFromPicker)
        self.confirmButton.clicked.connect(self._assign)
        self.sourceList.activated.connect(self._sourceSelected)
        self._picker.hide()
        return self._picker

    def _roles(self) -> tuple[tuple[str, AssemblyOccurrence], ...]:
        return (("Backbone", self.session.backbone), *(
            (f"Insert {number}", occurrence)
            for number, occurrence in enumerate(self.session.inserts, start=1)
        ))

    def _refreshPlan(self, occurrenceId: str | None = None) -> None:
        rows = []
        roles = self._roles()
        for role, occurrence in roles:
            text = f"{role} · Choose a source"
            if occurrence.source is not None:
                source = occurrence.source
                text = (f"{role} · {source.label} · {len(source.revision):,} nt"
                        f" · {occurrence.orientation}")
            rows.append(ChoiceRow(occurrence.occurrenceId, text))
        self.occurrencesList.setRows(tuple(rows))
        button = next(iter(self.occurrencesList.rowButtons.values()))
        rowsHeight = (button.sizeHint().height() + 3) * len(rows) + 6
        self.occurrencesList.setMaximumHeight(min(280, rowsHeight))
        if occurrenceId is None:
            occurrenceId = next(iter(self.occurrencesList.selectedKeys), rows[0].key)
        self.occurrencesList.setSelected(occurrenceId)
        self.junctionReview.refresh(self.session, roles)
        hasInputs = self.junctionReview.pairs.rowCount > 0
        if not hasInputs:
            self.inspectorTabs.setCurrentWidget(self._roleDetails)
        self.inspectorTabs.setTabEnabled(self.inspectorTabs.indexOf(self.junctionReview), hasInputs)
        self._refreshPlanControls()

    def _inspectRole(self) -> None:
        self.inspectorTabs.setCurrentWidget(self._roleDetails)
        self._refreshPlanControls()

    def _selectedRoleIndex(self) -> int:
        selected = self.occurrencesList.selectedKeys
        return next(
            (index for index, item in enumerate(self.session.occurrences)
             if item.occurrenceId in selected),
            0,
        )

    def _refreshPlanControls(self) -> None:
        row = self._selectedRoleIndex()
        occurrences = self.session.occurrences
        selected = occurrences[row]
        source = selected.source
        canEdit = self.pickerToken is None
        self.exampleButton.setEnabled(canEdit)
        self.inspectorTabs.setVisible(canEdit)
        insertIndex = max(1, row)
        insert = occurrences[insertIndex]
        insertTitle = "Insert" if len(occurrences) == 2 else f"Insert {insertIndex}"
        self.backboneCard.showOccurrence("Backbone", self.session.backbone)
        self.insertCard.showOccurrence(insertTitle, insert)
        self.chooseBackboneButton.setEnabled(canEdit)
        self.chooseInsertButton.setEnabled(canEdit)
        hasInputs = any(item.source is not None for item in occurrences)
        self.clearButton.setEnabled(canEdit and hasInputs)
        self.addInsertButton.setVisible(self.session.inserts[0].source is not None)
        self.addInsertButton.setEnabled(canEdit)
        self.orderLabel.setVisible(hasInputs)
        self.occurrencesList.setVisible(hasInputs)
        self.inspectedRole.setText("Backbone" if row == 0 else insertTitle)
        self.inspectedRole.setVisible(source is not None)
        self.planDetails.setText(
            _sourceDetails(source) if source is not None else "Choose a source to inspect its details."
        )
        self._editControls.setVisible(source is not None)
        canMove = row > 0 and len(occurrences) > 2
        self.moveEarlierButton.setVisible(canMove)
        self.moveLaterButton.setVisible(canMove)
        self.moveEarlierButton.setEnabled(row > 1)
        self.moveLaterButton.setEnabled(row < len(occurrences) - 1)

    def _chooseBackbone(self) -> None:
        self._openPicker(
            "Backbone", self.session.backbone.occurrenceId, opener=self.chooseBackboneButton,
        )

    def _openSelectedSource(self) -> None:
        occurrence = self.session.occurrences[self._selectedRoleIndex()]
        if self.pickerToken is None and occurrence.source is not None:
            revision = occurrence.source.sourceRevision
            self.controller.openExactRevision(revision.revisionId, revision)

    def _chooseInsert(self) -> None:
        insert = self.session.inserts[-1]
        if insert.source is None:
            self._openPicker(
                f"Insert {len(self.session.inserts)}", insert.occurrenceId,
                opener=self.addInsertButton,
            )
        else:
            self._openPicker("New insert", None, opener=self.addInsertButton)

    def _chooseCardInsert(self) -> None:
        role, occurrence = self._roles()[max(1, self._selectedRoleIndex())]
        self._openPicker(role, occurrence.occurrenceId, opener=self.chooseInsertButton)

    def stagePcr(self, preparation: AssemblyPcrInput) -> None:
        """Offer the exact selected product and wait for a confirmed role."""
        source = _pcrSource(preparation)
        self._openPicker("Assign PCR product", None, offeredSource=source)

    def stageDigest(self, run: RestrictionDigest, fragment: DNAFragment) -> None:
        """Offer one recorded linear product and keep other pins untouched."""
        source = _digestSource(run, fragment)
        self._openPicker("Assign digest product", None, offeredSource=source)

    def _openPicker(
        self, role: str, occurrenceId: str | None, *, offeredSource: AssemblySource | None = None,
        opener: QPushButton | None = None,
    ) -> None:
        self._showInputs()
        self.cancelPicker()
        self._nextPickerToken += 1
        self._pickerToken = self._nextPickerToken
        self._pickerOpener = opener
        self._targetId = occurrenceId
        self._offeredSource = offeredSource
        isHandoff = offeredSource is not None
        replacing = any(
            item.occurrenceId == occurrenceId and item.source is not None
            for item in self.session.occurrences
        )
        action = "replacement" if replacing else "selection"
        self._sourceTaskDescription = f"Assembly {role.lower()} {action}"
        self.pickerTitle.setText(role if isHandoff else f"Choose source · {role}")
        self.roleSelector.setVisible(isHandoff)
        self._intakeActions.setVisible(not isHandoff)
        self.openSavedButton.setEnabled(self._workspace.project is not None)
        if isHandoff:
            self.roleSelector.clear()
            defaultIndex = len(self.session.occurrences)
            for index, (name, occurrence) in enumerate(self._roles()):
                self.roleSelector.addItem(name, occurrence.occurrenceId)
                if occurrence.source is None and index > 0:
                    defaultIndex = min(defaultIndex, index)
            self.roleSelector.addItem("New insert", None)
            self.roleSelector.setCurrentIndex(defaultIndex)
        self._picker.show()
        self._refreshPlanControls()
        if not isHandoff:
            self.controller.analysis.requestAssemblyDigestions()
        self._refreshRetained()
        self._focusPicker()

    def finishSourceIntake(self, pickerToken: int, target: DocumentTarget) -> None:
        """Offer the newly opened record for explicit assignment in the original picker."""
        if (self.pickerToken != pickerToken or self._offeredSource is not None
                or not self._workspace.targetIsCurrent(target)):
            return
        revision = self._workspace.getDocument(target.documentId).revision
        if not isAssemblySource(revision):
            self.sourceList.setSelected(None)
            self._selectionNotice = (
                f"Opened {revision.name}. Choose a linear DNA record or DNA with unspecified topology."
            )
        self._refreshChoices(preferredDocumentId=target.documentId)
        if self.isVisible():
            self._focusPicker()

    def _focusPicker(self) -> None:
        if self._offeredSource is not None:
            self.roleSelector.setFocus()
            return
        buttons = self.sourceList.rowButtons
        selected = self._selectedSource()
        button = buttons.get(selected.key) if selected is not None else None
        (button or next(iter(buttons.values()), self.openFileButton)).setFocus()

    def _refreshRetained(self) -> None:
        if self.pickerToken is None:
            return
        if self._offeredSource is not None:
            self._retainedChoices = (_SourceChoice(self._offeredSource),)
        else:
            choices = []
            omitted = 0
            runs = (*self.controller.analysis.assemblyDigestions(), *self.controller.pcr.availableRuns())
            for run in runs:
                if isinstance(run, RestrictionDigest):
                    outputs, outputType = run.fragments, DNAFragment
                else:
                    outputs, outputType = run.products, PcrProduct
                for index, output in enumerate(outputs):
                    if not isinstance(output, outputType) or not isinstance(output.revision, SequenceRevision):
                        omitted += 1
                        continue
                    if not isAssemblySource(output.revision):
                        continue
                    try:
                        if isinstance(run, RestrictionDigest):
                            source = _digestSource(run, output, index)
                        else:
                            source = _pcrSource(AssemblyPcrInput(run, output), index)
                    except ValueError:
                        omitted += 1
                        continue
                    choices.append(_SourceChoice(source))
            self._retainedChoices = tuple(choices)
            if omitted:
                noun = "output" if omitted == 1 else "outputs"
                self.controller.operationFailed.emit(
                    f"Assembly skipped {omitted} recorded {noun} with inconsistent source evidence."
                )
        self._refreshChoices()

    def _refreshChoices(self, *, preferredDocumentId: str | None = None) -> None:
        if self.pickerToken is None:
            return
        selected = self._selectedSource()
        selectedKey = selected.key if selected is not None else None
        choices = []
        if self._offeredSource is None:
            for label in documentLabels(self._workspace):
                doc = self._workspace.getDocument(label.documentId)
                if isAssemblySource(doc.revision):
                    binding = self._workspace.taskInput(_TASK_SLOT, doc.documentId)
                    source = AssemblySource(label.qualifiedName, doc.revision)
                    choices.append(_SourceChoice(source, binding))
                    if doc.documentId == preferredDocumentId:
                        selectedKey = binding
        else:
            selectedKey = self._offeredSource.outputReference
        choices.extend(self._retainedChoices)
        request = self._sourceRequest
        if (request is not None and selectedKey == request.inputs[0]
                and not self._workspace.tasks.isCurrent(request)):
            selectedKey = None

        self._choices = {choice.key: choice for choice in choices}
        self.sourceList.setRows(tuple(
            ChoiceRow(key, f"{choice.source.label} · {len(choice.source.revision):,} nt")
            for key, choice in self._choices.items()
        ))
        reveal = preferredDocumentId is not None or self._offeredSource is not None
        self.sourceList.setSelected(selectedKey, reveal=reveal)
        if (selected is not None and selected.binding is not None
                and selectedKey not in self._choices):
            self._selectionNotice = _SOURCE_CHANGED
        self._sourceSelected()

    def _selectedSource(self) -> _SourceChoice | None:
        key = next(iter(self.sourceList.selectedKeys), None)
        return self._choices.get(key)

    def _sourceSelected(self) -> None:
        choice = self._selectedSource()
        binding = choice.binding if choice is not None else None
        if binding is None:
            self._cancelSourceRequest()
        elif self._sourceRequest is None or self._sourceRequest.inputs != (binding,):
            self._sourceRequest = self._workspace.tasks.begin(
                _TASK_OWNER, _TASK_SLOT, inputs=(binding,),
                description=self._sourceTaskDescription,
            )
        if choice is not None:
            self._selectionNotice = ""
            detail = _sourceDetails(choice.source)
        elif self._selectionNotice:
            detail = self._selectionNotice
        elif (self.controller.analysis.loadingAssemblyDigestions
              or self.controller.pcr.loadingAvailableRuns):
            detail = "Loading recorded inputs…"
        elif not self.sourceList.rowCount:
            detail = "Choose a DNA source or send a recorded Digest or PCR output."
        else:
            detail = "Select a DNA record or recorded output."
        self.sourceDetails.setText(detail)
        self.confirmButton.setEnabled(choice is not None)

    def _cancelSourceRequest(self) -> None:
        if self._sourceRequest is not None:
            self._workspace.tasks.cancel(_TASK_OWNER, _TASK_SLOT)
            self._sourceRequest = None

    def _assign(self) -> None:
        choice = self._selectedSource()
        if choice is None or self.pickerToken is None:
            return
        request = self._sourceRequest
        if request is not None and not self._workspace.tasks.finish(request):
            self._selectionNotice = _SOURCE_CHANGED
            self.sourceList.setSelected(None)
            self._refreshChoices()
            return
        targetId = self._targetId
        if self._offeredSource is not None:
            targetId = self.roleSelector.currentData()
        occurrence = self.session.assign(choice.source, targetId)
        self.cancelPicker()
        self._refreshPlan(occurrence.occurrenceId)
        self.occurrencesList.rowButtons[occurrence.occurrenceId].setFocus()

    def _cancelFromPicker(self) -> None:
        opener = self._pickerOpener or self.chooseInsertButton
        self.cancelPicker()
        if opener.isVisible() and opener.isEnabled():
            opener.setFocus(Qt.FocusReason.OtherFocusReason)

    def cancelPicker(self) -> None:
        """Withdraw the pending selection without changing any confirmed pin."""
        if self.pickerToken is None:
            return
        self._cancelSourceRequest()
        self._pickerToken = None
        self._pickerOpener = None
        self._targetId = None
        self._offeredSource = None
        self._retainedChoices = ()
        self._choices.clear()
        self._selectionNotice = ""
        self.sourceList.setRows(())
        self._picker.hide()
        self._refreshPlanControls()

    def _moveSelected(self, offset: int) -> None:
        occurrence = self.session.occurrences[self._selectedRoleIndex()]
        self.session.reorder(occurrence.occurrenceId, offset)
        self._refreshPlan(occurrence.occurrenceId)

    def _flipSelected(self) -> None:
        occurrence = self.session.occurrences[self._selectedRoleIndex()]
        self.session.flip(occurrence.occurrenceId)
        self._refreshPlan(occurrence.occurrenceId)

    def _removeSelected(self) -> None:
        row = self._selectedRoleIndex()
        self.session.remove(self.session.occurrences[row].occurrenceId)
        remaining = self.session.occurrences
        self._refreshPlan(remaining[min(row, len(remaining) - 1)].occurrenceId)

    def _confirmClear(self) -> None:
        if confirmAction(
            self, "Clear Assembly inputs", "Clear the backbone and all insert choices?",
            "Clear inputs",
        ):
            self.session.clear()
            self._refreshPlan()

    def _reset(self) -> None:
        self.cancelPicker()
        self.setSavedExample(None)
        self.session.clear()
        self._refreshPlan()
