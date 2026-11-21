"""Run pairwise alignments one at a time and send results to Qt if the inputs still match."""

from collections.abc import Callable
import logging
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from strandfolio.application.alignment_session import (
    MAX_ALIGNMENT_QUERIES,
    AlignmentSelection,
    AlignmentSession,
    AlignmentWork,
    RetainedAlignment,
)
from strandfolio.application.workspace import WorkspaceDocument
from strandfolio.domain.sequence import MoleculeType
from strandfolio.operations.alignment import (
    AlignmentCanceled,
    AlignmentResult,
    AlignmentSettings,
    alignPair,
)

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController


class _AlignmentSignals(QObject):
    completed = Signal(int, object, object)


class _AlignmentTask(QRunnable):
    def __init__(self, work: AlignmentWork) -> None:
        super().__init__()
        self.work = work
        self.signals = _AlignmentSignals()
        self._canceled = Event()

    def cancel(self) -> None:
        self._canceled.set()

    @Slot()
    def run(self) -> None:
        result = error = None
        try:
            if not self._canceled.is_set():
                result = self.work.calculate(self._canceled.is_set, alignPair)
        except AlignmentCanceled:
            error = "Alignment canceled."
        except (RuntimeError, ValueError) as problem:
            error = str(problem)
        except Exception:
            logging.exception("Unexpected alignment task failure")
            error = "Unexpected alignment failure. See the application log."
        self.signals.completed.emit(self.work.request.requestId, result, error)


class AlignmentCoordinator(QObject):
    """Adapt the shared alignment session to Qt workers and notifications."""

    changed = Signal()
    shuttingDown = Signal()
    maxQueries = MAX_ALIGNMENT_QUERIES

    def __init__(self, controller: "WorkspaceController") -> None:
        super().__init__(controller)
        self._controller = controller
        self._workspace = controller.workspace
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._worker: _AlignmentTask | None = None
        self._initialized = False
        self._isShuttingDown = False
        controller.collectionChanged.connect(self._collectionChanged)
        controller.nameChanged.connect(self._collectionChanged)

    @property
    def _session(self) -> AlignmentSession:
        return self._workspace.alignment

    def initialize(self) -> None:
        """Set the initial reference and query when Alignment first has an eligible input."""
        if self._initialized:
            return
        self._initialized = True
        self._collectionChanged()

    @property
    def eligibleDocuments(self) -> tuple[WorkspaceDocument, ...]:
        return self._workspace.alignmentDocuments

    @property
    def reference(self) -> AlignmentSelection | None:
        return self._session.reference

    @property
    def referenceMoleculeType(self) -> MoleculeType | None:
        return self._session.referenceMoleculeType

    @property
    def queries(self) -> tuple[AlignmentSelection, ...]:
        return self._session.queries

    @property
    def settings(self) -> AlignmentSettings:
        return self._session.settings

    @property
    def activeQueryId(self) -> str | None:
        return self._session.activeQueryId

    @property
    def activeResult(self) -> AlignmentResult | None:
        return self._session.activeResult

    @property
    def pendingQueryIds(self) -> frozenset[str]:
        return self._session.pendingQueryIds

    @property
    def canRun(self) -> bool:
        return not self._isShuttingDown and self._session.canRun

    @property
    def canRunMultiple(self) -> bool:
        return not self._isShuttingDown and self.reference is not None and 2 <= len(self.queries) <= self.maxQueries

    @property
    def retainedComparisons(self) -> tuple[RetainedAlignment, ...]:
        return self._session.retainedComparisons

    @property
    def retainedComparison(self) -> RetainedAlignment | None:
        return self._session.retainedComparison

    def resultFor(self, queryId: str) -> AlignmentResult | None:
        return self._session.resultFor(queryId)

    def errorFor(self, queryId: str) -> str:
        return self._session.errorFor(queryId)

    def setReference(
        self, documentId: str | None, featureId: str | None = None, reverse: bool = False,
    ) -> bool:
        selection = (
            AlignmentSelection(documentId, featureId, reverse) if documentId is not None else None
        )
        return self._change(lambda: self._session.setReference(selection))

    def setQuery(self, documentId: str, checked: bool) -> bool:
        return self._change(lambda: self._session.setQuery(documentId, checked))

    def setQueryRegion(
        self, documentId: str, featureId: str | None = None, reverse: bool = False,
    ) -> bool:
        selection = AlignmentSelection(documentId, featureId, reverse)
        return self._change(lambda: self._session.setQueryRegion(selection))

    def setSettings(self, settings: AlignmentSettings) -> bool:
        return self._change(lambda: self._session.setSettings(settings))

    def selectResult(self, queryId: str) -> bool:
        return self._change(lambda: self._session.selectResult(queryId))

    def retainResult(self, queryId: str | None = None) -> RetainedAlignment | None:
        comparison = self._session.retainResult(queryId)
        if comparison is not None:
            self.changed.emit()
        return comparison

    def selectRetained(self, comparisonId: str | None) -> bool:
        return self._change(lambda: self._session.selectRetained(comparisonId))

    def runAlignment(self) -> bool:
        """Queue independent comparisons and schedule the first available pair."""
        return self.canRun and self._change(self._session.beginComparisons)

    def cancelAlignment(self) -> None:
        """Withdraw live requests and interrupt the active calculation."""
        self._change(self._session.cancelPending)

    def shutdown(self) -> None:
        """Cancel outstanding calculations and join the one owned worker on quit."""
        self._isShuttingDown = True
        self.shuttingDown.emit()
        self.cancelAlignment()
        if self._worker is not None:
            self._worker.cancel()
        self._pool.waitForDone()
        self.changed.emit()

    def _change(self, operation: Callable[[], bool]) -> bool:
        try:
            updated = operation()
        except ValueError as error:
            self._controller.operationFailed.emit(str(error))
            self.changed.emit()
            return False
        if updated:
            self._schedule()
            self.changed.emit()
        return updated

    @Slot()
    def _collectionChanged(self) -> None:
        self._workspace.synchronizeAlignment()
        if self._initialized:
            self._session.initialize(self._workspace.activeDocumentId)
        self._schedule()
        self.changed.emit()

    def _schedule(self) -> None:
        if self._worker is not None:
            if not self._workspace.tasks.isCurrent(self._worker.work.request):
                self._worker.cancel()
            return
        work = self._session.nextWork
        if work is None or self._isShuttingDown:
            return
        worker = _AlignmentTask(work)
        worker.signals.completed.connect(self._completed)
        self._worker = worker
        self._pool.start(worker)

    @Slot(int, object, object)
    def _completed(self, requestId: int, result: AlignmentResult | None, error: str | None) -> None:
        worker = self._worker
        if worker is None or worker.work.request.requestId != requestId:
            return
        self._worker = None
        self._session.finish(worker.work, result, error)
        self._schedule()
        self.changed.emit()
