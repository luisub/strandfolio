"""Review catalogue matches and apply annotation edits to the active sequence revision."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from strandfolio.application.tasks import TaskRequest
from strandfolio.application.workspace import DocumentTarget
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.operations.editing import (
    AnnotationEdit, LabelReview, acceptLabels, dismissLabels, removeAnnotation, scanLabels,
)
from strandfolio.storage.feature_catalogue import FeatureCatalogue

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController


class AnnotationCoordinator(QObject):
    """Keep detection previews separate from commands in the active tab's undo history."""

    changed = Signal()

    def __init__(self, controller: WorkspaceController, cataloguePath: str | Path) -> None:
        super().__init__(controller)
        self._controller = controller
        self._workspace = controller.workspace
        self._cataloguePath = Path(cataloguePath)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._workers: dict[int, CalculationTask] = {}
        self._pending: TaskRequest | None = None
        self._reviewTarget: DocumentTarget | None = None
        self.review: LabelReview | None = None
        self.status = ""
        controller.documentChanged.connect(self._documentChanged)

    @property
    def busy(self) -> bool:
        return self._pending is not None

    @property
    def canUndo(self) -> bool:
        return self._controller.session.canUndo

    @property
    def canRedo(self) -> bool:
        return self._controller.session.canRedo

    @property
    def canDiscard(self) -> bool:
        doc = self._workspace.activeDocument
        return doc is not None and doc.session.hasEdits and not doc.hasUnsavedRoot

    def target(self) -> DocumentTarget | None:
        """Capture the active tab and its revision before opening a modal editor."""
        documentId = self._workspace.activeDocumentId
        return self._workspace.targetFor(documentId) if documentId is not None else None

    @Slot()
    def _documentChanged(self) -> None:
        self._cancelPending()
        self.review = self._reviewTarget = None
        self.status = ""
        self.changed.emit()

    def _cancelPending(self) -> None:
        if self._pending is not None:
            request = self._pending
            self._pending = None
            self._workspace.tasks.finish(request)
            self._workers[request.requestId].cancel()

    def cancel(self) -> None:
        """Withdraw a scan, including a success already queued for GUI delivery."""
        if self.busy:
            self._cancelPending()
            self.status = "Label detection canceled."
            self.changed.emit()

    def detect(self) -> bool:
        """Scan a pinned DNA revision in a worker using a single SQLite read snapshot."""
        document = self._workspace.activeDocument
        if document is None or document.revision.moleculeType != "DNA":
            return False
        self._cancelPending()
        self.review = self._reviewTarget = None
        revision = document.revision
        path = self._cataloguePath
        request = self._workspace.tasks.begin(
            document.documentId, "detect-labels", bindActive=True,
            inputs=(self._workspace.taskInput("sequence", document.documentId),),
            settings=(str(path), revision.topology), versions=("exact-reference-v1",),
            description=f"Detecting labels in {revision.name}",
        )

        def calculate(isCanceled: Callable[[], bool]) -> LabelReview:
            with FeatureCatalogue(path) as catalogue:
                return scanLabels(revision, catalogue, isCanceled=isCanceled)

        worker = CalculationTask(request.requestId, calculate)
        worker.signals.completed.connect(self._completed)
        self._workers[request.requestId] = worker
        self._pending = request
        self.status = "Detecting exact catalogue matches…"
        self._pool.start(worker)
        self.changed.emit()
        return True

    @Slot(int, object, object)
    def _completed(self, requestId: int, result: LabelReview | None, error: str | None) -> None:
        self._workers.pop(requestId, None)
        request = self._pending
        if request is None or requestId != request.requestId:
            return
        self._pending = None
        if not self._workspace.tasks.finish(request):
            self.status = ""
        elif error or result is None:
            self.status = error or "Label detection returned no review."
        else:
            self.review = result
            self._reviewTarget = self.target()
            count = len(result.suggestions)
            self.status = f"{count} regional {'match' if count == 1 else 'matches'}."
        self.changed.emit()

    def apply(
        self, target: DocumentTarget | None,
        command: Callable[[SequenceRevision, str], AnnotationEdit],
        *, onError: Callable[[str], None] | None = None,
    ) -> bool:
        """Apply one reviewed command and publish the resulting selection and warnings."""
        if target is None or target != self.target():
            (onError or self._controller.operationFailed.emit)(
                "The sequence changed. Reopen the annotation editor."
            )
            return False

        def edit() -> None:
            result = self._workspace.applyAnnotationEdit(target, command)
            if result.warnings:
                self._controller.message.emit(" ".join(result.warnings))

        return self._controller._changeWorkspace(edit, onError=onError)

    def remove(self, target: DocumentTarget, featureId: str) -> bool:
        return self.apply(target, lambda rev, recordId: removeAnnotation(
            rev, featureId, recordId=recordId,
        ))

    def accept(self, keys: tuple[str, ...] | None) -> bool:
        """Accept the chosen rows, or only unambiguous new rows when keys is None."""
        return self._decide(lambda revision, review, recordId: acceptLabels(
            revision, review, suggestionIds=keys, recordId=recordId,
        ))

    def dismiss(self, keys: tuple[str, ...]) -> bool:
        """Stage dismissed occurrences with the other edits in the active draft."""
        return self._decide(lambda revision, review, recordId: dismissLabels(
            revision, review, suggestionIds=keys, recordId=recordId,
        ))

    def _decide(
        self, operation: Callable[[SequenceRevision, LabelReview, str], AnnotationEdit],
    ) -> bool:
        review, target = self.review, self._reviewTarget
        if review is None or target is None:
            return False
        applied = self.apply(target, lambda rev, recordId: operation(rev, review, recordId))
        if applied and target != self.target():
            self.detect()
        return applied

    def undo(self) -> bool:
        target = self.target()
        return target is not None and self.canUndo and self._controller._changeWorkspace(
            lambda: self._workspace.undoAnnotationEdit(target)
        )

    def redo(self) -> bool:
        target = self.target()
        return target is not None and self.canRedo and self._controller._changeWorkspace(
            lambda: self._workspace.redoAnnotationEdit(target)
        )

    def discard(self, target: DocumentTarget | None = None) -> bool:
        target = self.target() if target is None else target
        return target is not None and self.canDiscard and self._controller._changeWorkspace(
            lambda: self._workspace.discardDraft(target)
        )

    def shutdown(self) -> None:
        """Cancel calculations before their owning Qt window is destroyed."""
        self._cancelPending()
        self._pool.waitForDone()
