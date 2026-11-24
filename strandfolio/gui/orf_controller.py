"""Run an ORF search for the active DNA revision and curate its transient results."""

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from strandfolio.application.tasks import TaskRequest
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.operations.editing import acceptOrfs
from strandfolio.operations.orfs import OrfCandidate, OrfOptions, OrfReview, scanOrfs

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController


class OrfCoordinator(QObject):
    """Keep candidate scans independent of stored annotations until the user adds rows."""

    changed = Signal()

    def __init__(self, controller: "WorkspaceController") -> None:
        super().__init__(controller)
        self._controller = controller
        self._workspace = controller.workspace
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._workers: dict[int, CalculationTask] = {}
        self._pending: TaskRequest | None = None
        self._documentId: str | None = None
        self.review: OrfReview | None = None
        self.status = ""
        controller.documentChanged.connect(self._documentChanged)
        controller.geneticCodeChanged.connect(self._geneticCodeChanged)

    @property
    def busy(self) -> bool:
        return self._pending is not None

    @property
    def options(self) -> OrfOptions:
        document = self._workspace.activeDocument
        return document.viewState.orfOptions if document is not None else OrfOptions()

    @property
    def tableId(self) -> int:
        return self._controller.geneticCodePreference.tableId or 1

    def _clearPending(self) -> None:
        request, self._pending = self._pending, None
        if request is not None:
            self._workspace.tasks.finish(request)
            self._workers[request.requestId].cancel()

    @Slot()
    def _documentChanged(self) -> None:
        document = self._workspace.activeDocument
        previousId, self._documentId = self._documentId, self._workspace.activeDocumentId
        self._clearPending()
        self.review = None
        self.status = ""
        if (document is not None and document.documentId != previousId
                and document.revision.moleculeType == "DNA" and not document.revision.features):
            self.scan(self.options)
        else:
            self.changed.emit()

    @Slot()
    def _geneticCodeChanged(self) -> None:
        if self.busy or self.review is not None:
            self.scan(self.options)
        else:
            self.changed.emit()

    def scan(self, options: OrfOptions) -> bool:
        """Start a scan pinned to the current tab, revision, code and settings."""
        document = self._workspace.activeDocument
        if document is None or document.revision.moleculeType != "DNA":
            return False
        self._clearPending()
        self.review = None
        document.viewState.orfOptions = options
        revision, tableId = document.revision, self.tableId
        request = self._workspace.tasks.begin(
            document.documentId, "find-orfs", bindActive=True,
            inputs=(self._workspace.taskInput("sequence", document.documentId),),
            settings=(options, tableId), affectsClose=False,
        )
        worker = CalculationTask(request.requestId, lambda isCanceled: scanOrfs(
            revision, tableId=tableId, options=options, isCanceled=isCanceled,
        ), failureContext="ORF search")
        worker.signals.completed.connect(self._completed)
        self._workers[request.requestId] = worker
        self._pending = request
        self.status = "Searching six reading frames…"
        self._pool.start(worker)
        self.changed.emit()
        return True

    def cancel(self) -> None:
        """Withdraw the current scan and ignore its queued result."""
        self._clearPending()
        self.status = "ORF search canceled."
        self.changed.emit()

    @Slot(int, object, object)
    def _completed(self, requestId: int, result: OrfReview | None, error: str | None) -> None:
        self._workers.pop(requestId, None)
        request = self._pending
        if request is None or requestId != request.requestId:
            return
        self._pending = None
        if (not self._workspace.tasks.finish(request)
                or request.settings != (self.options, self.tableId)):
            self.status = ""
        elif error or result is None:
            self.status = error or "ORF search returned no review."
        else:
            self.review = result
            self.status = ""
        self.changed.emit()

    def dismiss(self, review: OrfReview, selected: frozenset[OrfCandidate]) -> None:
        """Hide rows from the current review without editing the document."""
        if self.review is review and selected:
            self.review = replace(review, candidates=tuple(
                candidate for candidate in review.candidates if candidate not in selected
            ))
            self.changed.emit()

    def add(
        self, review: OrfReview, selected: frozenset[OrfCandidate],
        *, onError: Callable[[str], None],
    ) -> bool:
        """Apply one undoable edit using only the current review and genetic code."""
        if self.review is not review or review.tableId != self.tableId:
            onError("The ORF review changed. Scan again before adding annotations.")
            return False
        annotations = self._controller.annotations
        return annotations.apply(annotations.target(), lambda revision, recordId: acceptOrfs(
            revision, review, selected, recordId=recordId,
        ), onError=onError)

    def shutdown(self) -> None:
        self._clearPending()
        self._pool.waitForDone()
