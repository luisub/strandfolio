"""Read-only view of recorded sequence ancestry."""

from __future__ import annotations

from collections.abc import Callable
from itertools import islice
import logging
from pathlib import Path
import sqlite3
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from strandfolio.application.tasks import TaskContext, TaskRequest
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import fileDialogDirectory
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.gui.views.history_diagram import (
    EVIDENCE_LABELS, INPUT_LABELS, OPERATION_LABELS, HistoryDiagram, moleculeUnits,
)
from strandfolio.io.formats import checkExportDestinationVersion, exportDestinationVersion
from strandfolio.io.history_report import writeHistoryReport
from strandfolio.rendering.history import (
    HistoryCanceled,
    HistoryInput,
    HistoryNode,
    HistorySpan,
    HistoryTrace,
    RecordedHistory,
    RecordedRun,
    resolveHistory,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent

    from strandfolio.application.workspace import WorkspaceDocument
    from strandfolio.gui.controller import WorkspaceController


_OWNER = "history"
_ANCESTRY_SLOT = "ancestry"
_TRACE_SLOT = "trace"
_EXPORT_SLOT = "export"
HistoryResult = RecordedHistory | tuple[HistoryTrace, ...] | Path


class _HistorySignals(QObject):
    completed = Signal(object, object, str)


class _HistoryTask(QRunnable):
    def __init__(
        self, request: TaskRequest,
        calculate: Callable[[Callable[[], bool]], HistoryResult],
    ) -> None:
        super().__init__()
        self.request = request
        self._calculate = calculate
        self._canceled = Event()
        self.signals = _HistorySignals()

    def cancel(self) -> None:
        self._canceled.set()

    @Slot()
    def run(self) -> None:
        result = None
        error = ""
        try:
            if not self._canceled.is_set():
                result = self._calculate(self._canceled.is_set)
        except HistoryCanceled:
            pass
        except (OSError, ValueError, KeyError, sqlite3.Error) as problem:
            error = str(problem)
        except Exception:
            logging.exception("Unexpected History task failure")
            error = "Unexpected History task failure. See the application log."
        self.signals.completed.emit(self.request, result, error)


class HistoryAncestryView(QWidget):
    """Show each exact revision and expose input uses for the selected revision."""

    openSnapshotRequested = Signal(str, object)

    def __init__(self, controller: WorkspaceController, parent: QWidget) -> None:
        super().__init__(parent)
        self._controller = controller
        self._workspace = controller.workspace
        self._pcr = controller.pcr
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._worker: _HistoryTask | None = None
        self._context: tuple[TaskContext, tuple[str, ...], tuple[str, ...]] | None = None
        self._runs: tuple[RecordedRun, ...] = ()
        self._history: RecordedHistory | None = None
        self._nodes: dict[str, HistoryNode] = {}
        self._selectedRevisionId: str | None = None
        self._selectedSource: HistoryNode | None = None
        self._traceRangeRevisionId: str | None = None
        self._isShowingTrace = False
        self._hasRead = False
        self._isShuttingDown = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel(self)
        self.status.setObjectName("muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.exportButton = QPushButton("Export provenance…", self)
        self.exportButton.setIcon(workspaceIcon("export"))
        self.exportButton.setToolTip("Save a JSON report for the active record and its ancestry.")
        self.exportButton.clicked.connect(self._exportHistory)

        stage = QSplitter(Qt.Orientation.Horizontal, self)
        stage.setObjectName("analysisPanels")
        stage.setHandleWidth(12)
        stage.setChildrenCollapsible(False)
        canvas = QWidget(stage)
        canvas.setObjectName("workflowCanvas")
        canvasLayout = QVBoxLayout(canvas)
        canvasLayout.setContentsMargins(16, 12, 16, 12)
        header = QWidget(canvas)
        header.setObjectName("historyCanvasHeader")
        views = QHBoxLayout(header)
        views.setContentsMargins(0, 0, 0, 8)
        description = QVBoxLayout()
        description.setSpacing(2)
        canvasTitle = QLabel("Construction history", header)
        canvasTitle.setObjectName("documentTitle")
        description.addWidget(canvasTitle)
        description.addWidget(self.status)
        views.addLayout(description, 1)
        self.listButton = QPushButton("Revision list", header)
        self.listButton.setCheckable(True)
        views.addWidget(self.listButton)
        canvasLayout.addWidget(header)
        self.ancestryPages = QStackedWidget(canvas)
        self.diagram = HistoryDiagram(self.ancestryPages)
        self.diagram.activated.connect(self._selectCard)
        self.revisionList = ChoiceRows("Exact recorded revisions", self.ancestryPages)
        self.revisionList.activated.connect(self._selectRevision)
        self.ancestryPages.addWidget(self.diagram)
        self.ancestryPages.addWidget(self.revisionList)
        self.listButton.toggled.connect(self._changeDisplay)
        canvasLayout.addWidget(self.ancestryPages, 1)
        stage.addWidget(canvas)

        scroll = QScrollArea(stage)
        scroll.setObjectName("analysisInspector")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(370)
        inspector = QWidget(scroll)
        inspector.setObjectName("workflowInspector")
        inspectorLayout = QVBoxLayout(inspector)
        inspectorLayout.setContentsMargins(14, 12, 14, 12)
        inspectorTitle = QLabel("Molecule details", inspector)
        inspectorTitle.setObjectName("analysisPanelHeading")
        inspectorLayout.addWidget(inspectorTitle)
        self.moleculeTitle = QLabel(inspector)
        self.moleculeTitle.setTextFormat(Qt.TextFormat.PlainText)
        self.moleculeTitle.setObjectName("documentTitle")
        self.moleculeTitle.setWordWrap(True)
        self.moleculeTitle.setMaximumHeight(50)
        inspectorLayout.addWidget(self.moleculeTitle)
        self.moleculeSummary = QLabel(inspector)
        self.moleculeSummary.setTextFormat(Qt.TextFormat.PlainText)
        self.moleculeSummary.setObjectName("muted")
        self.moleculeSummary.setWordWrap(True)
        inspectorLayout.addWidget(self.moleculeSummary)
        self.inputHeading = QLabel("Recorded inputs", inspector)
        self.inputHeading.setObjectName("sectionTitle")
        inspectorLayout.addWidget(self.inputHeading)
        self.inputList = ChoiceRows("Recorded input occurrences of the selected revision", inspector)
        self.inputList.setMaximumHeight(120)
        self.inputList.activated.connect(self._selectInput)
        inspectorLayout.addWidget(self.inputList)
        self.mappingSummary = QLabel(inspector)
        self.mappingSummary.setTextFormat(Qt.TextFormat.PlainText)
        self.mappingSummary.setWordWrap(True)
        self.mappingSummary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inspectorLayout.addWidget(self.mappingSummary)
        self.evidenceButton = QPushButton("Operation details", inspector)
        self.evidenceButton.setCheckable(True)
        inspectorLayout.addWidget(self.evidenceButton)
        self.details = QPlainTextEdit(inspector)
        self.details.setObjectName("historyEvidence")
        self.details.setAccessibleName("Recorded operation and intervals")
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(140)
        self.details.hide()
        self.evidenceButton.toggled.connect(self.details.setVisible)
        inspectorLayout.addWidget(self.details, 1)
        scroll.setWidget(inspector)
        stage.addWidget(scroll)
        stage.setStretchFactor(0, 1)
        stage.setStretchFactor(1, 1)
        stage.setSizes([1035, 365])
        layout.addWidget(stage, 1)

        self.traceRange = QWidget(inspector)
        rangeLayout = QGridLayout(self.traceRange)
        rangeLayout.setContentsMargins(0, 0, 0, 0)
        self.traceFrom = QSpinBox(self.traceRange)
        self.traceThrough = QSpinBox(self.traceRange)
        for column, (label, control) in enumerate((("Trace from", self.traceFrom),
                                                   ("Through", self.traceThrough))):
            field = QLabel(label, self.traceRange)
            field.setBuddy(control)
            rangeLayout.addWidget(field, 0, column)
            rangeLayout.addWidget(control, 1, column)
            rangeLayout.setColumnStretch(column, 1)
            control.valueChanged.connect(self._traceBoundsChanged)
        self.traceFrom.setAccessibleName("Trace from position")
        self.traceThrough.setAccessibleName("Trace through position")
        self.traceRangeFeedback = QLabel("From must be at or before Through.", self.traceRange)
        self.traceRangeFeedback.setObjectName("muted")
        self.traceRangeFeedback.setWordWrap(True)
        rangeLayout.addWidget(self.traceRangeFeedback, 2, 0, 1, 2)
        inspectorLayout.addWidget(self.traceRange)

        actions = QVBoxLayout()
        self.inspectSourceButton = QPushButton("Inspect source ancestry", self)
        self.inspectSourceButton.clicked.connect(self._inspectSource)
        self.openSnapshotButton = QPushButton("Open source snapshot", self)
        self.openSnapshotButton.setObjectName("primary")
        self.openSnapshotButton.clicked.connect(self._openSnapshot)
        self.traceButton = QPushButton("Trace to original sources", self)
        self.traceButton.clicked.connect(self._traceSources)
        self.retryButton = QPushButton("Retry ancestry", self)
        self.retryButton.clicked.connect(self._retry)
        self.retryButton.hide()
        actions.addWidget(self.inspectSourceButton)
        actions.addWidget(self.openSnapshotButton)
        actions.addWidget(self.traceButton)
        actions.addWidget(self.retryButton)
        inspectorLayout.addLayout(actions)
        inspectorLayout.addStretch()
        self._setInputs(())
        self._showSelection(None)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        """Clear obsolete results immediately and read ancestry when the pane is shown."""
        if self._isShuttingDown:
            return
        pcrRuns = self._workspace.pcrRuns.runs
        runs = (*self._workspace.digestions.runs, *pcrRuns)
        context = (
            self._workspace.taskContext(True),
            tuple(sorted(run.operationId for run in runs)),
            tuple(
                run.operationId for run in pcrRuns if self._pcr.isRunPendingWrite(run.operationId)
            ),
        )
        if context != self._context:
            self._context = context
            self._runs = runs
            self._cancel()
            self._hasRead = False
            self._selectedRevisionId = None
            self._traceRangeRevisionId = None
            self._clearHistory()
            if self._workspace.activeDocument is None:
                self.status.setText("Open a sequence to inspect its recorded ancestry.")
            else:
                self.status.setText("Reading recorded ancestry…")
        self._schedule()

    def shutdown(self) -> None:
        """Cancel the read and let the pool finish its current file operation on quit."""
        self._isShuttingDown = True
        self._cancel()
        self._pool.waitForDone()
        self._worker = None

    def _cancel(self) -> None:
        if self._worker is not None:
            self._workspace.tasks.cancel(_OWNER, self._worker.request.slot)
            self._worker.cancel()

    def _changeDisplay(self, showList: bool) -> None:
        self.ancestryPages.setCurrentWidget(self.revisionList if showList else self.diagram)
        self.listButton.setText("Show diagram" if showList else "Revision list")

    def _selectCard(self, revisionId: str, inputIndex: int | None) -> None:
        self._selectRevision(revisionId)
        if inputIndex is not None:
            self.inputList.setSelected(inputIndex)
            self._selectInput(inputIndex)

    def _clearHistory(self) -> None:
        self.diagram.setHistory(None)
        self._history = None
        self._nodes.clear()
        self.revisionList.setRows(())
        self._setInputs(())
        self._showSelection(None)
        self.retryButton.hide()

    def _retry(self) -> None:
        if self._worker is not None:
            return
        self._hasRead = False
        self.retryButton.setText("Checking ancestry…")
        self.openSnapshotButton.setEnabled(False)
        self.status.setText("Checking recorded ancestry…")
        self._schedule()
        self._updateTraceControls()

    def _schedule(self) -> None:
        document = self._workspace.activeDocument
        if (self._isShuttingDown or self._hasRead or self._worker is not None
                or document is None or not self.isVisible()):
            return
        revision, project, runs = document.revision, self._workspace.project, self._runs
        snapshots = (revision, *(run.template for run in runs))
        steps = (*self._workspace.digestions.retainedAnnotationSteps,
                 *(step for item in snapshots for step in self._workspace.annotationSteps(item)))
        self._start(
            _ANCESTRY_SLOT, document,
            lambda isCanceled: resolveHistory(
                revision, project, runs=runs, annotationSteps=steps, isCanceled=isCanceled,
            ),
        )

    def _start(
        self, slot: str, document: WorkspaceDocument,
        calculate: Callable[[Callable[[], bool]], HistoryResult],
    ) -> None:
        binding = self._workspace.taskInput("revision", document.documentId)
        request = self._workspace.tasks.begin(
            _OWNER, slot, inputs=(binding,), bindActive=True, affectsClose=False,
        )
        worker = _HistoryTask(request, calculate)
        worker.signals.completed.connect(self._completed)
        self._worker = worker
        self.retryButton.setEnabled(False)
        self._pool.start(worker)

    @Slot(object, object, str)
    def _completed(
        self, request: TaskRequest, result: HistoryResult | None, error: str,
    ) -> None:
        if self._worker is None or self._worker.request is not request:
            return
        self._worker = None
        self.retryButton.setEnabled(True)
        if self._workspace.tasks.finish(request):
            if request.slot == _EXPORT_SLOT:
                if isinstance(result, Path):
                    self._controller.message.emit(f"Exported {result.name}.")
                elif error:
                    self._controller.operationFailed.emit(f"Could not export provenance. {error}")
            elif request.slot == _TRACE_SLOT:
                self._showTrace(result if isinstance(result, tuple) else None, error)
            else:
                self._hasRead = True
                if isinstance(result, RecordedHistory):
                    self._showHistory(result)
                else:
                    self._clearHistory()
                    self.status.setText(f"Could not read recorded ancestry. {error}".strip())
                    self.retryButton.setText("Retry ancestry")
                    self.retryButton.show()
        self._updateTraceControls()
        self._schedule()

    def _showHistory(self, history: RecordedHistory) -> None:
        previous = self._nodes.get(self._selectedRevisionId)
        inputIndex = next(iter(self.inputList.selectedKeys), None)
        self._history = history
        self.diagram.setHistory(history)
        self._nodes.clear()
        rows = []
        for node in history.nodes:
            revision = node.revision
            evidence = EVIDENCE_LABELS[node.status]
            label = f"{revision.name} · {len(revision):,} {moleculeUnits(revision)}"
            rows.append(ChoiceRow(
                revision.revisionId, f"{label}\n{evidence} · {revision.revisionId[:8]}",
                f"{label} · {evidence} · Revision {revision.revisionId}",
            ))
            self._nodes[revision.revisionId] = node
        self.revisionList.setRows(tuple(rows))
        selected = self._nodes.get(self._selectedRevisionId)
        self._selectRevision(selected.revision.revisionId if selected else history.rootRevisionId)
        if (previous is not None and selected is not None
                and previous.inputs == selected.inputs and inputIndex is not None):
            self.inputList.setSelected(inputIndex)
            self._selectInput(inputIndex)
        gaps = sum(node.status not in ("recorded", "source") for node in history.nodes)
        self.retryButton.setVisible(bool(gaps))
        if gaps:
            label = "gap" if gaps == 1 else "gaps"
            self.status.setText(f"Incomplete ancestry · {gaps:,} evidence {label}")
            self.retryButton.setText("Recheck ancestry")
        elif len(history.nodes) == 1:
            self.status.setText("No generating operation is recorded for this revision.")
        else:
            self.status.setText(f"{len(history.nodes):,} exact revisions")

    def _setInputs(self, inputs: tuple[HistoryInput, ...]) -> None:
        rows = []
        for index, source in enumerate(inputs):
            label = f"{INPUT_LABELS[source.role]} · {source.sourceName}"
            text = label
            gap = self._inputGap(source)
            if gap is not None:
                evidence, _ = gap
                text = f"{label}\n{evidence}"
                label += f" · {evidence}"
            rows.append(ChoiceRow(index, text, f"{label} · Revision {source.sourceRevisionId}"))
        self.inputList.setRows(tuple(rows))
        self.inputList.setMaximumHeight(min(3, len(rows)) * 44)
        self.inputList.setSelected(None)
        self.inputHeading.setVisible(bool(inputs))
        self.inputList.setVisible(bool(inputs))

    def _selectRevision(self, revisionId: str) -> None:
        node = self._nodes.get(revisionId)
        if node is not None:
            self._selectedRevisionId = revisionId
            self.revisionList.setSelected(revisionId)
            self.diagram.selectOccurrence(revisionId)
            self._setInputs(node.inputs)
            self._showSelection(node)

    def _selectInput(self, inputIndex: int) -> None:
        node = self._nodes.get(self._selectedRevisionId)
        if node is not None:
            self.diagram.selectOccurrence(node.revision.revisionId, inputIndex)
            self._showSelection(node, node.inputs[inputIndex])

    @staticmethod
    def _interval(span: tuple[int, int]) -> str:
        start, end = span
        return f"{start + 1:,}–{end:,}" if start + 1 != end else f"{end:,}"

    def _inputLines(self, source: HistoryInput, units: str) -> list[str]:
        lines = [
            f"{INPUT_LABELS[source.role]} · {source.sourceName}",
            f"Input revision {source.sourceRevisionId}",
        ]
        gap = self._inputGap(source)
        if gap is not None:
            lines.extend(line for line in gap if line)
        if not source.spans:
            lines.append("No interval is recorded for this input.")
        lines.extend(self._spanLine(span, units) for span in source.spans)
        return lines

    def _inputSnapshot(self, source: HistoryInput) -> HistoryNode | None:
        if source.role in ("template", "parent"):
            return self._nodes.get(source.sourceRevisionId)
        return None

    def _inputGap(self, source: HistoryInput) -> tuple[str, str] | None:
        snapshot = self._inputSnapshot(source)
        if snapshot is None or snapshot.status in ("recorded", "source"):
            return None
        evidence = EVIDENCE_LABELS[snapshot.status]
        scope = "Earlier ancestry" if snapshot.canOpenSnapshot else "Source snapshot unavailable"
        return f"{scope} · {evidence}", snapshot.detail

    def _spanLine(self, span: HistorySpan, units: str) -> str:
        orientation = "Forward" if span.orientation == 1 else "Reverse"
        return (
            f"Input {units} {self._interval(span.sourceSpan)} → "
            f"Product {units} {self._interval(span.productSpan)} · {orientation}"
        )

    def _showSelection(
        self, node: HistoryNode | None, source: HistoryInput | None = None,
    ) -> None:
        if self._worker is not None and self._worker.request.slot == _TRACE_SLOT:
            self._cancel()
        self._isShowingTrace = False
        document = self._workspace.activeDocument
        self._selectedSource = None
        canInspectSource = False
        lines = []
        self.moleculeTitle.setVisible(node is not None)
        self.moleculeSummary.setVisible(node is not None)
        self.mappingSummary.hide()
        self.evidenceButton.setVisible(node is not None)
        self.details.setVisible(node is not None and self.evidenceButton.isChecked())
        if node is not None:
            revision = node.revision
            self.moleculeTitle.setText(source.sourceName if source is not None else revision.name)
            units = moleculeUnits(revision)
            kind = revision.topology or revision.moleculeType
            if source is None:
                lines.append(f"{len(revision):,} {units} · {kind}")
                inputs = node.inputs
                self._selectedSource = node
            else:
                lines.append(f"Used in {revision.name} · {len(revision):,} {units}")
                inputs = (source,)
                self._selectedSource = self._inputSnapshot(source)
                canInspectSource = self._selectedSource is not None
            self.moleculeSummary.setText(lines[0])
            if inputs:
                lines.extend(("", "Recorded input and product coordinates (1-based inclusive)"))
                for item in inputs:
                    lines.extend(("", *self._inputLines(item, units)))
                spans = [self._spanLine(span, units) for span in islice(
                    (span for item in inputs for span in item.spans), 2,
                )]
                count = sum(len(item.spans) for item in inputs)
                if count > len(spans):
                    spans.append(f"All {count:,} mappings in Operation details.")
                self.mappingSummary.setText("\n".join(spans))
                self.mappingSummary.setVisible(bool(spans))
            if node.status not in ("source", "recorded"):
                self.evidenceButton.setChecked(True)
            lines.extend((
                "", f"Revision {revision.revisionId}",
                f"{EVIDENCE_LABELS[node.status]} · "
                f"{'Project snapshot' if node.isPersisted else 'Session snapshot'}",
            ))
            if node.reference is not None:
                lines.extend((
                    f"Operation {node.reference.operationId}",
                    f"Output {node.reference.outputKey}",
                ))
            if (node.candidateRevisionId is not None
                    and node.candidateRevisionId != revision.revisionId):
                lines.append(f"Recorded output revision {node.candidateRevisionId}")
            if node.operation is not None:
                lines.append(OPERATION_LABELS[node.operation])
            if node.createdAt is not None:
                lines.append(f"Recorded {node.createdAt}")
            if node.engineVersion is not None:
                lines.append(f"Engine {node.engineVersion}")
            if node.detail:
                lines.append(node.detail)
            if source is None:
                if revision.sourceArtifactId is not None:
                    lines.append(f"Source artifact {revision.sourceArtifactId}")
                if revision.sourceOrdinal is not None:
                    lines.append(f"Source record {revision.sourceOrdinal}")
                lines.extend(f"{name} · {value}" for name, value in node.settings)
        self.details.setPlainText("\n".join(lines))
        self.inspectSourceButton.setEnabled(canInspectSource)
        self.inspectSourceButton.setVisible(canInspectSource)
        self.openSnapshotButton.setEnabled(
            self._hasRead and self._selectedSource is not None and document is not None
            and self._selectedSource.canOpenSnapshot
            and self._selectedSource.revision.revisionId != document.revision.revisionId
        )
        self.openSnapshotButton.setVisible(self.openSnapshotButton.isEnabled())
        self._updateTraceControls()

    def _traceTarget(self) -> HistoryNode | None:
        node = self._nodes.get(self._selectedRevisionId)
        if (self.inputList.selectedKeys or node is None or node.status != "recorded"
                or not any(source.spans for source in node.inputs)):
            return None
        return node

    def _updateTraceControls(self) -> None:
        exporting = self._worker is not None and self._worker.request.slot == _EXPORT_SLOT
        self.exportButton.setText("Exporting…" if exporting else "Export provenance…")
        self.exportButton.setEnabled(
            self._hasRead and self._history is not None and self._worker is None,
        )
        target = self._traceTarget()
        self.traceButton.setVisible(target is not None)
        self.traceRange.setVisible(target is not None)
        if target is not None and target.revision.revisionId != self._traceRangeRevisionId:
            revision = target.revision
            with QSignalBlocker(self.traceFrom), QSignalBlocker(self.traceThrough):
                self.traceFrom.setRange(1, len(revision))
                self.traceThrough.setRange(1, len(revision))
                self.traceFrom.setValue(1)
                self.traceThrough.setValue(len(revision))
            self._traceRangeRevisionId = revision.revisionId
        hasValidRange = self.traceFrom.value() <= self.traceThrough.value()
        self.traceRangeFeedback.setVisible(target is not None and not hasValidRange)

        if (self._worker is not None and self._worker.request.slot == _TRACE_SLOT
                and self._workspace.tasks.isCurrent(self._worker.request)):
            self.traceButton.setText("Tracing intervals…")
        elif self._isShowingTrace:
            self.traceButton.setText("Show recorded intervals")
        else:
            self.traceButton.setText("Trace to original sources")
        self.traceButton.setEnabled(self._worker is None and target is not None and hasValidRange)

    def _traceBoundsChanged(self) -> None:
        if self._worker is not None and self._worker.request.slot == _TRACE_SLOT:
            self._cancel()
        if self._isShowingTrace and self._selectedRevisionId is not None:
            self._selectRevision(self._selectedRevisionId)
        else:
            self._updateTraceControls()

    def _selectedTraceSpan(self) -> tuple[int, int]:
        return self.traceFrom.value() - 1, self.traceThrough.value()

    def _traceSources(self) -> None:
        target, history = self._traceTarget(), self._history
        document = self._workspace.activeDocument
        if target is None or history is None or document is None or not self.traceButton.isEnabled():
            return
        revision = target.revision
        span = self._selectedTraceSpan()
        if self._isShowingTrace:
            self._selectRevision(revision.revisionId)
        elif self._worker is None:
            self._start(
                _TRACE_SLOT, document,
                lambda isCanceled: history.trace(
                    revision.revisionId, span, isCanceled=isCanceled,
                ),
            )
            self._updateTraceControls()

    def _showTrace(self, sources: tuple[HistoryTrace, ...] | None, error: str) -> None:
        target = self._traceTarget()
        if target is None:
            return
        revision = target.revision
        self.evidenceButton.setChecked(True)
        lines = [
            f"Original sources · {revision.name}",
            f"Product revision {revision.revisionId}",
            f"Requested product {moleculeUnits(revision)} {self._interval(self._selectedTraceSpan())}",
            "Recorded source and product coordinates (1-based inclusive)",
        ]
        if sources is None:
            lines.extend(("", f"Could not trace recorded intervals. {error}".strip()))
        elif not sources:
            lines.extend(("", "No recorded source intervals reach this revision."))
        else:
            for item in sources:
                role = "Source" if item.role == "template" else INPUT_LABELS[item.role]
                lines.extend(("", f"{role} · {item.sourceName}", f"Revision {item.sourceRevisionId}"))
                lines.append(self._spanLine(item.span, moleculeUnits(revision)))
                if item.status not in ("recorded", "source"):
                    lines.append(f"Ancestry stops here · {EVIDENCE_LABELS[item.status]}")
        self.details.setPlainText("\n".join(lines))
        self._isShowingTrace = True

    def _inspectSource(self) -> None:
        if self.inspectSourceButton.isEnabled() and self._selectedSource is not None:
            self._selectRevision(self._selectedSource.revision.revisionId)

    def _openSnapshot(self) -> None:
        if self.openSnapshotButton.isEnabled() and self._selectedSource is not None:
            revision = self._selectedSource.revision
            self.openSnapshotRequested.emit(revision.revisionId, revision)

    def _exportHistory(self) -> None:
        history = self._history
        document = self._workspace.activeDocument
        context = self._workspace.taskContext(True)
        if (history is None or document is None or not self._hasRead or self._worker is not None
                or history.rootRevisionId != document.revision.revisionId
                or self._context is None or self._context[0] != context):
            return
        project = self._workspace.project
        stem = Path(document.revision.name).name or "sequence"
        dialog = QFileDialog(self, "Export provenance", str(fileDialogDirectory() / f"{stem}-provenance.json"))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilter("Provenance report (*.json)")
        dialog.setDefaultSuffix("json")

        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            destination = Path(dialog.selectedFiles()[0])
            if (self._workspace.taskContext(True) != context or self._history is not history
                    or not self._hasRead or self._isShuttingDown or self._worker is not None):
                raise ValueError("The active record or its ancestry changed. Export the report again.")
            paths = self._controller.openSourcePaths()
            pickedVersion = exportDestinationVersion(destination)

            def export(isCanceled: Callable[[], bool]) -> Path:
                def ensureCurrent() -> None:
                    if isCanceled():
                        raise HistoryCanceled()
                    checkExportDestinationVersion(destination, pickedVersion)

                return writeHistoryReport(history, destination, project=project,
                                          protectedPaths=paths, ensureCurrent=ensureCurrent)

            self._start(_EXPORT_SLOT, document, export)
            self._updateTraceControls()
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            self._controller.operationFailed.emit(f"Could not export provenance. {error}")
        finally:
            dialog.deleteLater()
