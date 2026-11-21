"""Run PCR simulations and reopen recorded runs and products."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import sqlite3
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication

from strandfolio.application.pcr_session import (
    AssemblyPcrInput, RetainedPcrRun, SelectedPrimerIdea, pcrOutput, primerIdeaFromSelection,
    validatePrimerSelection,
)
from strandfolio.application.tasks import TaskInput, TaskRequest
from strandfolio.domain.coordinates import SequenceSelection
from strandfolio.domain.primers import PrimerEntry, PrimerRevision, PrimerStrand
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.io.formats import SequenceImportResult
from strandfolio.operations.gel import GelMarker, GelViewSettings
from strandfolio.operations.pcr import PCR_ENGINE_VERSION, PcrProduct, PcrRun, PcrSettings, simulatePcr
from strandfolio.storage.project_pcr import CertifiedPcrRun, certifyPcrRun

if TYPE_CHECKING:
    from strandfolio.application.workspace import WorkspaceDocument
    from strandfolio.gui.controller import WorkspaceController


_OWNER = "pcr"
_SLOT = "run"
_ERRORS = (OSError, ValueError, KeyError, sqlite3.Error)
_EMPTY_PRIMERS = ("", "", "", "")
_PROJECT_TIMEOUT_SECONDS = 1.0


@dataclass(slots=True)
class _PrimerInputs:
    texts: tuple[str, str, str, str] = _EMPTY_PRIMERS
    names: tuple[str, str] = ("Forward", "Reverse")
    forward: PrimerRevision | None = None
    reverse: PrimerRevision | None = None
    ideas: dict[PrimerStrand, SelectedPrimerIdea] = field(default_factory=dict)

    @property
    def entries(self) -> tuple[PrimerEntry, PrimerEntry]:
        forwardAnnealing, reverseAnnealing, forwardTail, reverseTail = self.texts
        return (PrimerEntry("Forward", forwardAnnealing, forwardTail),
                PrimerEntry("Reverse", reverseAnnealing, reverseTail))

    def revisions(self) -> tuple[PrimerRevision, PrimerRevision]:
        first, second = self.entries
        forward = first.toRevision(self.names[0], self.forward)
        reverse = second.toRevision(self.names[1], self.reverse)
        self.forward, self.reverse = forward, reverse
        return forward, reverse


@dataclass(frozen=True, slots=True)
class _PendingPcr:
    request: TaskRequest
    worker: CalculationTask
    capture: SequenceImportResult | None
    independent: bool


@dataclass(slots=True)
class PcrEditorResult:
    """The latest accepted run for one editor and its initial project save."""

    run: PcrRun
    saving: bool = False
    saveError: str | None = None


class PcrCoordinator(QObject):
    """Run single-template PCR and route accepted outputs by run and output key."""

    busyChanged = Signal(bool)
    inputsChanged = Signal()
    primerSitePicked = Signal(int)
    statusChanged = Signal()
    runCompleted = Signal(object)
    runOpened = Signal(object, object)
    previewOpened = Signal(object, object)
    historyChanged = Signal()
    retryChanged = Signal()
    viewSaveFailed = Signal(object, str)
    productOpened = Signal()
    sourceOpened = Signal()
    assemblyPrepared = Signal(object)

    def __init__(self, controller: WorkspaceController) -> None:
        super().__init__(controller)
        self._controller = controller
        self._workspace = controller.workspace
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._workers: dict[int, CalculationTask] = {}
        self._pending: _PendingPcr | None = None
        self._inputs: dict[str | None, _PrimerInputs] = {}
        self._uncommitted: dict[str, str] = {}
        self._writing: set[str] = set()
        self._editorResults: dict[str, PcrEditorResult] = {}
        self._storedRuns: dict[str, PcrRun] = {}
        self._loadingHistory: set[str | None] = set()
        self._refreshAfterRead: str | None = None
        self._loadedHistory: set[str | None] = set()
        self._historyErrors: dict[str | None, str] = {}
        self._unreadViews: set[str] = set()
        self._settings = PcrSettings()
        self._status = "idle"
        self._statusMessage = ""
        self._statusRunId: str | None = None
        self._assemblyPreparation: AssemblyPcrInput | None = None
        controller.collectionChanged.connect(self._collectionChanged)
        controller.documentChanged.connect(self._documentChanged)

    @property
    def busy(self) -> bool:
        result = self.editorResult
        return self._pending is not None or bool(result and result.saving and self.resultMatchesDraft)

    @property
    def editorResult(self) -> PcrEditorResult | None:
        """Find the last submission on this exact open document and template revision."""
        document = self._workspace.activeDocument
        if document is None:
            return None
        result = self._editorResults.get(document.documentId)
        if result is not None and result.run.template.revisionId == document.revision.revisionId:
            return result
        return None

    @property
    def resultMatchesDraft(self) -> bool:
        result = self.editorResult
        if result is None or result.run.settings != self.settings or result.run.target != self.target:
            return False
        draft = self._inputs.get(self._workspace.activeDocumentId)
        forward, reverse = result.run.forwardPrimer, result.run.reversePrimer
        return bool(draft and (draft.forward, draft.reverse) == (forward, reverse)
                    and draft.names == (forward.name, reverse.name)
                    and draft.entries == (PrimerEntry.fromRevision("Forward", forward),
                                          PrimerEntry.fromRevision("Reverse", reverse)))

    @property
    def canCancel(self) -> bool:
        """Indicate whether a calculation still depends on live inputs."""
        return self._pending is not None

    @property
    def status(self) -> str:
        return self._status

    @property
    def statusMessage(self) -> str:
        return self._statusMessage

    @property
    def primerTexts(self) -> tuple[str, str, str, str]:
        """Return forward/reverse annealing sequences, then forward/reverse tails."""
        entry = self._inputs.get(self._workspace.activeDocumentId)
        return entry.texts if entry else _EMPTY_PRIMERS

    @property
    def primerEntries(self) -> tuple[PrimerEntry, PrimerEntry]:
        """Describe the currently displayed drafts without creating new revision identities."""
        current = self._inputs.get(self._workspace.activeDocumentId)
        return (current or _PrimerInputs()).entries

    @property
    def primerNames(self) -> tuple[str, str]:
        """FASTA identifiers of the active pair, in forward/reverse order."""
        entry = self._inputs.get(self._workspace.activeDocumentId)
        return entry.names if entry else ("Forward", "Reverse")

    def currentPrimerRevisions(self) -> tuple[PrimerRevision, PrimerRevision]:
        """Capture both named members of the current draft, preserving their identities."""
        entry = self._inputs.get(self._workspace.activeDocumentId)
        if entry is None:
            raise ValueError("Enter both primers first.")
        return entry.revisions()

    def usePrimerPair(self, forward: PrimerRevision, reverse: PrimerRevision) -> None:
        """Install a complete validated pair as one edit in the active draft."""
        if forward.modifications or reverse.modifications:
            raise ValueError("The pair editor supports unmodified DNA.")
        texts = (forward.annealingSequence, reverse.annealingSequence,
                 forward.tailSequence, reverse.tailSequence)
        current = self._inputs.get(self._workspace.activeDocumentId)
        if (current and not current.ideas and current.texts == texts
                and (current.forward, current.reverse) == (forward, reverse)):
            return
        self._inputs[self._workspace.activeDocumentId] = _PrimerInputs(
            texts, (forward.name, reverse.name), forward, reverse,
        )
        self._editInputs("the primer pair changed")

    @property
    def settings(self) -> PcrSettings:
        return self._settings

    @property
    def target(self) -> SequenceSelection | None:
        """Return the explicitly pinned target on this document and revision."""
        doc = self._workspace.activeDocument
        if doc is None:
            return None
        target = doc.viewState.pcrTarget
        return target if target and target.revisionId == doc.revision.revisionId else None

    def pinSelection(self) -> bool:
        """Pin the current selection independently of later canvas gestures."""
        doc = self._workspace.activeDocument
        selection = self._controller.session.selection
        if (doc is None or doc.revision.moleculeType != "DNA" or not selection
                or selection.revisionId != doc.revision.revisionId):
            return False
        if self.target != selection:
            doc.viewState.pcrTarget = selection
            self._editInputs("the target changed")
        return True

    def clearTarget(self) -> None:
        doc = self._workspace.activeDocument
        if doc is not None and doc.viewState.pcrTarget is not None:
            doc.viewState.pcrTarget = None
            self._editInputs("the target changed")

    @property
    def primerComparisonSites(self) -> tuple[SelectedPrimerIdea, ...]:
        """The manually picked sites to compare with subsequent edits of the same draft."""
        doc = self._workspace.activeDocument
        if doc is None or doc.documentId not in self._inputs:
            return ()
        return tuple(idea for idea in self._inputs[doc.documentId].ideas.values()
                     if idea.revisionId == doc.revision.revisionId)

    @property
    def selectedPrimerIdeas(self) -> tuple[SelectedPrimerIdea, ...]:
        """Picked sites whose original binding sequence still matches the draft."""
        texts = self.primerTexts
        return tuple(site for site in self.primerComparisonSites
                     if site.annealingSequence == texts[0 if site.strand == 1 else 1].strip().upper())

    def selectionIdea(self, strand: PrimerStrand) -> SelectedPrimerIdea:
        tail = self.primerTexts[2 if strand == 1 else 3]
        return primerIdeaFromSelection(
            self._controller.session.current, self._controller.session.selection, strand, tail,
        )

    def validateSelection(self) -> None:
        """Raise ValueError for an invalid highlight before the user chooses a role."""
        session = self._controller.session
        validatePrimerSelection(session.current, session.selection)

    def useSelectionAsPrimer(self, strand: PrimerStrand) -> bool:
        try:
            idea = self.selectionIdea(strand)
        except ValueError as error:
            self._controller.message.emit(str(error))
            return False
        entry = self._inputs.setdefault(self._workspace.activeDocumentId, _PrimerInputs())
        index = 0 if strand == 1 else 1
        texts = list(entry.texts)
        texts[index] = idea.annealingSequence
        # The same oligo can occur in several places. Remember the exact chosen location.
        entry.ideas[strand] = idea
        if tuple(texts) == entry.texts:
            self.inputsChanged.emit()
        else:
            self.setPrimerInputs(*texts)
        self.primerSitePicked.emit(strand)
        role = "Forward" if strand == 1 else "Reverse"
        self._controller.message.emit(f"{role} primer set from the {len(idea.annealingSequence)}-nt selection.")
        return True

    @property
    def unavailableReason(self) -> str:
        """Describe why a simulation cannot start in the current context."""
        document = self._workspace.activeDocument
        if document is None:
            return "Open a DNA template."
        if document.revision.moleculeType != "DNA" or document.revision.topology is None:
            return "PCR requires DNA with explicit linear or circular topology."
        if self.busy:
            return "PCR simulation is running."
        forward, reverse = self.primerEntries
        for entry in (forward, reverse):
            if (entry.annealing or entry.tail) and entry.problem:
                return entry.problem
        if not forward.annealing and not reverse.annealing:
            return "Select or enter both primers to simulate PCR."
        if not forward.annealing or not reverse.annealing:
            role = "forward" if not forward.annealing else "reverse"
            return f"Select or enter the {role} primer to simulate PCR."
        return ""

    def setPrimerInputs(
        self, forwardAnnealing: str, reverseAnnealing: str,
        forwardTail: str = "", reverseTail: str = "",
    ) -> bool:
        """Update oligo text, release blank members and invalidate the previous request."""
        documentId = self._workspace.activeDocumentId
        texts = (forwardAnnealing, reverseAnnealing, forwardTail, reverseTail)
        entry = self._inputs.setdefault(documentId, _PrimerInputs())
        if texts != entry.texts:
            entry.texts = texts
            if not forwardAnnealing.strip() and not forwardTail.strip():
                entry.names = ("Forward", entry.names[1])
                entry.forward = None
            if not reverseAnnealing.strip() and not reverseTail.strip():
                entry.names = (entry.names[0], "Reverse")
                entry.reverse = None
            for strand, index in ((1, 0), (-1, 1)):
                if not texts[index].strip():
                    site = entry.ideas.pop(strand, None)
                    if site is not None:
                        self._clearSiteSelection(site)
            self._editInputs("the primers changed")
        return True

    def _clearSiteSelection(self, site: SelectedPrimerIdea) -> None:
        selected = self._controller.session.selection
        revision = self._controller.session.current
        if not selected or revision is None or selected.revisionId != site.revisionId:
            return
        spans = site.sourceSegments if site.strand == 1 else tuple(reversed(site.sourceSegments))
        bounds = selected.displayRange(len(revision), circular=revision.topology == "circular")
        if bounds == (spans[0][0] + 1, spans[-1][1]):
            self._controller.select(selected.caret, selected.caret)

    def clearPrimer(self, role: str) -> None:
        """Clear a member of the active draft, including its tail and saved identity."""
        entry = self._inputs.get(self._workspace.activeDocumentId)
        if entry is None:
            return
        forward, reverse, forwardTail, reverseTail = entry.texts
        if role == "Forward":
            texts = ("", reverse, "", reverseTail)
        elif role == "Reverse":
            texts = (forward, "", forwardTail, "")
        else:
            raise ValueError("Choose the forward or reverse primer.")
        if texts == entry.texts:
            self._editInputs("the primer was cleared")
        else:
            self.setPrimerInputs(*texts)
        self._reportClear(f"{role} primer cleared.")

    def clearDraft(self) -> None:
        """Discard both primer inputs and the live selection in the active editor."""
        self._inputs.pop(self._workspace.activeDocumentId, None)
        selected = self._controller.session.selection
        if selected:
            self._controller.select(selected.caret, selected.caret)
        self._editInputs("the primer draft was cleared")
        self._reportClear("Primer draft cleared.")

    def _reportClear(self, message: str) -> None:
        if self.pendingSaveRuns():
            message += " A previous PCR run needs saving. Choose Retry project save."
        if error := self.historyReadError:
            message += f" {error} Choose Retry PCR history in History."
        self._controller.message.emit(message)

    def setPrimers(
        self, forwardSequence: str, reverseSequence: str,
        forwardTailLength: int = 0, reverseTailLength: int = 0,
    ) -> bool:
        """Set both full oligos in 5′→3′ order with explicit 5′ tail lengths."""
        forwardSequence, reverseSequence = forwardSequence.strip(), reverseSequence.strip()
        if any(
            type(length) is not int or length < 0 or (length != 0 and length >= len(sequence))
            for sequence, length in (
                (forwardSequence, forwardTailLength), (reverseSequence, reverseTailLength),
            )
        ):
            self._cancelPending("the primers changed")
            self._setStatus("invalid", "Each 5′ tail must leave at least one annealing base.")
            return False
        texts = (
            forwardSequence[forwardTailLength:], reverseSequence[reverseTailLength:],
            forwardSequence[:forwardTailLength], reverseSequence[:reverseTailLength],
        )
        entry = self._inputs.get(self._workspace.activeDocumentId)
        if entry and entry.ideas:
            entry.ideas.clear()
            if entry.texts == texts:
                self.inputsChanged.emit()
        return self.setPrimerInputs(*texts)

    def setSettings(self, settings: PcrSettings) -> None:
        """Change the simulation bounds for future requests."""
        if settings != self._settings:
            self._settings = settings
            self._editInputs("the PCR settings changed")

    def _setStatus(self, status: str, message: str, operationId: str | None = None) -> None:
        self._statusRunId = operationId
        if (status, message) != (self._status, self._statusMessage):
            self._status, self._statusMessage = status, message
            self.statusChanged.emit()

    def _editInputs(self, reason: str) -> None:
        if not self._cancelPending(reason):
            self._restoreEditorStatus()
            if self.editorResult and self.editorResult.saving:
                self.busyChanged.emit(self.busy)
        self.inputsChanged.emit()

    def _collectionChanged(self) -> None:
        documentIds = {document.documentId for document in self._workspace.documents}
        self._inputs = {key: value for key, value in self._inputs.items() if key is None or key in documentIds}
        for key in self._editorResults.keys() - documentIds:
            del self._editorResults[key]
        if self._pending is not None and not self._workspace.tasks.isCurrent(self._pending.request):
            self._cancelPending("the template or project changed")

    def _documentChanged(self) -> None:
        doc = self._workspace.activeDocument
        if doc and (entry := self._inputs.get(doc.documentId)):
            entry.ideas = {strand: site for strand, site in entry.ideas.items()
                          if site.revisionId == doc.revision.revisionId}
        self._restoreEditorStatus()
        self.inputsChanged.emit()
        self.busyChanged.emit(self.busy)
        self.historyChanged.emit()

    def runPcr(self) -> bool:
        """Capture both primer revisions, the template, selection and search settings."""
        if self.busy:
            return False
        document = self._workspace.activeDocument
        reason = self.unavailableReason
        if reason:
            unsupported = document is not None and (
                document.revision.moleculeType != "DNA" or document.revision.topology is None
            )
            status = "unsupported" if unsupported else "invalid"
            self._setStatus(status, reason)
            return False
        try:
            forward, reverse = self._inputs[document.documentId].revisions()
        except ValueError as error:
            self._setStatus("invalid", str(error))
            return False
        self._start(document, forward, reverse)
        return True

    def _start(
        self, document: WorkspaceDocument, forward: PrimerRevision, reverse: PrimerRevision,
    ) -> None:
        revision, target, settings = document.revision, self.target, self.settings
        request = self._workspace.tasks.begin(
            _OWNER, _SLOT, inputs=(
                self._workspace.taskInput("template", document.documentId),
                TaskInput("forward-primer", None, forward.primerId, forward.revisionId),
                TaskInput("reverse-primer", None, reverse.primerId, reverse.revisionId),
            ), settings=(target, settings), versions=(PCR_ENGINE_VERSION,), bindActive=True,
            description=f"Simulating PCR for {revision.name}",
        )

        def calculate(isCanceled: Callable[[], bool]) -> tuple[PcrRun, CertifiedPcrRun | None]:
            result = simulatePcr(
                revision, forward, reverse, target=target, settings=settings, isCanceled=isCanceled,
            )
            certification = None
            if not isCanceled() and result.status != "canceled":
                certification = certifyPcrRun(result, isCanceled=isCanceled)
            return result, certification

        worker = CalculationTask(request.requestId, calculate)
        worker.signals.completed.connect(self._onTaskCompleted)
        capture = document.capture
        ordinal = revision.sourceOrdinal
        if capture is not None and (
            ordinal is None or capture.records[ordinal - 1].revisionId != revision.revisionId
        ):
            capture = None
        self._pending = _PendingPcr(
            request, worker, capture, capture is not None and document.isIndependentIntake,
        )
        self._workers[request.requestId] = worker
        self._pool.start(worker)
        self._setStatus("running", f"Simulating PCR for {revision.name}.")
        self.busyChanged.emit(self.busy)

    def cancelPcr(self) -> None:
        """Cancel the active simulation and ignore any late completion."""
        self._cancelPending()

    def _cancelPending(self, reason: str = "") -> bool:
        pending = self._pending
        if pending is None:
            return False
        self._pending = None
        self._workspace.tasks.cancel(_OWNER, _SLOT)
        pending.worker.cancel()
        message = f"PCR canceled because {reason}." if reason else "PCR canceled."
        self._setStatus("canceled", message)
        self.busyChanged.emit(False)
        return True

    def _restoreEditorStatus(self) -> None:
        result = self.editorResult
        if result is None or not self.resultMatchesDraft:
            self._setStatus("idle", "")
        elif result.saving:
            self._setStatus("saving", "Saving the PCR result to the project.", result.run.operationId)
        elif result.saveError is not None:
            message = f"{self._resultMessage(result.run)} Session only. Project save failed. {result.saveError}"
            self._setStatus("unsaved", message, result.run.operationId)
        else:
            self._setStatus(result.run.status, self._resultMessage(result.run), result.run.operationId)

    @Slot(int, object, object)
    def _onTaskCompleted(
        self, requestId: int, response: tuple[PcrRun, CertifiedPcrRun | None] | None,
        error: str | None,
    ) -> None:
        self._workers.pop(requestId, None)
        pending = self._pending
        if pending is None or pending.request.requestId != requestId:
            return
        self._pending = None
        relevant = self._workspace.tasks.finish(pending.request)
        if not relevant or pending.request.settings != (self.target, self.settings):
            self._setStatus("canceled", "PCR canceled because its inputs changed.")
        elif response is not None:
            result, certification = response
            inputs = (result.template.revisionId, result.forwardPrimer.revisionId,
                      result.reversePrimer.revisionId)
            if (inputs != tuple(item.revisionId for item in pending.request.inputs)
                    or (result.target, result.settings) != pending.request.settings
                    or (result.engineVersion,) != pending.request.versions):
                self._setStatus("failed", "PCR returned a result for different inputs.")
            else:
                self._acceptRun(result, pending, certification)
        else:
            self._setStatus("failed", error or "PCR failed before producing a result.")
        self.busyChanged.emit(self.busy)

    @staticmethod
    def runOutcome(result: PcrRun) -> str:
        """Summarize the recorded search outcome and any limits or warnings."""
        if result.status == "completed":
            count = len(result.products)
            noun = "product" if count == 1 else "products"
            sizes = f"{result.settings.minProductBp:,}–{result.settings.maxProductBp:,} bp"
            outcome = f"Search complete · {count:,} exact-match {noun} on the whole template · Product size {sizes}."
            if result.target:
                counts = (p.targetCoverage(result.target) for p in result.products)
                complete = sum(covered == total for covered, total in counts)
                outcome += f" Fully covering the target: {complete:,} of {count:,}."
        elif result.status == "capped":
            outcome = "Search incomplete (capped)."
        else:
            outcome = f"Search {result.status}."
        return " ".join((outcome, *result.warnings))

    @classmethod
    def _resultMessage(cls, result: PcrRun) -> str:
        template = result.template
        return f"{template.name} (revision {template.revisionId[:8]}) · {cls.runOutcome(result)}"

    def _acceptRun(
        self, result: PcrRun, pending: _PendingPcr, certification: CertifiedPcrRun | None,
    ) -> None:
        key = result.operationId
        if result.status == "canceled":
            self._setStatus(result.status, self._resultMessage(result), key)
            self.runCompleted.emit(result)
            return
        entry = RetainedPcrRun(result, pending.capture, pending.independent, certification)
        self._workspace.pcrRuns.retain(entry)
        view = self._defaultView(result)
        self._workspace.pcrRuns.setView(key, view)
        documentId = pending.request.inputs[0].documentId
        assert documentId is not None  # The template is bound to the tab that started PCR.
        editorResult = PcrEditorResult(result, saving=self._workspace.project is not None)
        self._editorResults[documentId] = editorResult
        self._restoreEditorStatus()
        if not editorResult.saving:
            self.historyChanged.emit()
            self.runCompleted.emit(result)
            return
        self._persistRun(entry, view, lambda error: self._finishRunWrite(editorResult, documentId, error))

    def _finishRunWrite(self, result: PcrEditorResult, documentId: str, error: str | None) -> None:
        result.saving = False
        result.saveError = error
        if error is not None:
            self._uncommitted[result.run.operationId] = documentId
            self.retryChanged.emit()
        self.historyChanged.emit()
        if self.editorResult is result and self.resultMatchesDraft:
            self._restoreEditorStatus()
            self.runCompleted.emit(result.run)
            self.busyChanged.emit(self.busy)

    def _persistRun(
        self, entry: RetainedPcrRun, view: GelViewSettings, complete: Callable[[str | None], None],
    ) -> None:
        key = entry.result.operationId
        project = self._workspace.project
        if project is None:
            complete("Open a project before saving this PCR result.")
            return
        try:
            evidence = self._workspace.analysisEvidence(entry.result.template)
            annotationEvidence = self._workspace.annotationEvidence(entry.result.template)
        except _ERRORS as error:
            complete(str(error))
            return

        def persist() -> None:
            project.registerPcrRun(
                entry.result, ancestors=evidence.runs, capture=evidence.capture or entry.capture,
                independent=evidence.independent if evidence.capture is not None else entry.independent,
                view=view, certification=entry.certification,
                ancestorCertifications=evidence.certifications,
                ancestorViews=evidence.views,
                timeout=_PROJECT_TIMEOUT_SECONDS,
                annotationEvidence=annotationEvidence,
                ancestorAnnotations=evidence.annotations,
            )

        def published(_value: object, error: str | None) -> None:
            self._writing.discard(key)
            complete(error)

        self._writing.add(key)
        self._submitProjectTask(persist, published, write=True)

    def _submitProjectTask(
        self, calculate: Callable[[], object], publish: Callable[[object, str | None], None],
        *, write: bool = False,
    ) -> None:
        context = self._workspace.taskContext(False)

        def publishCurrent(value: object, error: str | None) -> None:
            if context == self._workspace.taskContext(False):
                publish(value, error)

        self._controller.projectTasks.submit(calculate, publishCurrent, write=write)

    def pendingSaveRuns(self, *, projectWide: bool = False) -> tuple[PcrRun, ...]:
        """List retryable runs for the original tab, or for explicit project recovery."""
        if self._workspace.project is None:
            return ()
        document = self._workspace.activeDocument
        runs = []
        for key, documentId in reversed(self._uncommitted.items()):
            entry = self._workspace.pcrRuns.get(key)
            if entry is None or key in self._writing:
                continue
            if projectWide or (document is not None and documentId == document.documentId
                               and entry.result.template.revisionId == document.revision.revisionId):
                runs.append(entry.result)
        return tuple(runs)

    @property
    def provenanceRetryId(self) -> str | None:
        """Identify the latest retryable write on the active document."""
        return next((run.operationId for run in self.pendingSaveRuns()), None)

    def isRunPendingWrite(self, operationId: str) -> bool:
        return operationId in self._uncommitted

    def retryRunWrite(
        self, operationId: str | None = None, *, projectWide: bool = False,
        report: Callable[[str], None] | None = None,
    ) -> bool:
        """Retry the selected pending run on its source tab or from project History."""
        key = operationId
        runs = self.pendingSaveRuns(projectWide=projectWide)
        if key is None and not projectWide and len(runs) == 1:
            key = runs[0].operationId
        if key is None or not any(run.operationId == key for run in runs):
            return False
        entry = self._workspace.pcrRuns.get(key)
        if entry is None:
            return False
        view = self.viewForRun(key)
        tabContext = self._workspace.taskContext(True)
        editorResult = self._editorResults.get(self._uncommitted[key])

        def complete(error: str | None) -> None:
            result = entry.result
            if editorResult is not None and editorResult.run.operationId == key:
                editorResult.saveError = error
            if self._statusRunId == key:
                self._restoreEditorStatus()
            if error is None:
                self._uncommitted.pop(key)
                message = f"Saved PCR run {key[:8]} for {result.template.name}."
                self.historyChanged.emit()
            else:
                message = f"Could not save PCR run {key[:8]} for {result.template.name}. {error}"
            if report is not None:
                report(message)
            elif not projectWide and self._workspace.taskContext(True) == tabContext:
                self._controller.message.emit(message)
            self.retryChanged.emit()

        self._persistRun(entry, view, complete)
        self.retryChanged.emit()
        return True

    def _defaultView(self, result: PcrRun) -> GelViewSettings:
        return GelViewSettings(
            marker=self._controller.analysis.resolveGelMarker(GelMarker()),
            selectedOutputKey=result.products[0].outputKey if result.products else None,
        )

    def viewForRun(self, operationId: str) -> GelViewSettings:
        """Get the stored preview definition or initialize it for a retained run."""
        project = self._workspace.project
        view = self._workspace.pcrRuns.getView(operationId)
        if view is None and project is not None:
            if self.previewReadFailed(operationId):
                return self._defaultView(self.getRun(operationId))
            try:
                view = project.getPcrView(operationId)
            except _ERRORS as error:
                message = f"Could not read the saved PCR preview. {error}"
                run = self.getRun(operationId)
                self._unreadViews.add(operationId)
                self._historyErrors[run.template.revisionId] = message
                self._controller.message.emit(message)
                self.historyChanged.emit()
                return self._defaultView(run)
        if view is None:
            view = self._defaultView(self.getRun(operationId))
        marker = self._controller.analysis.resolveGelMarker(view.marker)
        view = view if marker == view.marker else replace(view, marker=marker)
        self._workspace.pcrRuns.setView(operationId, view)
        return view

    def previewReadFailed(self, operationId: str) -> bool:
        """Identify a saved preview whose settings still need to be recovered."""
        return operationId in self._unreadViews

    def updateRunView(self, operationId: str, view: GelViewSettings) -> bool:
        """Update the preview settings and queue a project write when applicable."""
        def update() -> None:
            result = self.getRun(operationId)
            if not isinstance(view, GelViewSettings):
                raise ValueError("Expected PCR gel view settings.")
            if view.selectedOutputKey is not None:
                pcrOutput(result, view.selectedOutputKey)
            self._workspace.pcrRuns.setView(operationId, view)
            self._queueViewWrite(operationId, view)

        return self._controller._tryOperation(update)

    def _queueViewWrite(self, operationId: str, view: GelViewSettings) -> None:
        project = self._workspace.project
        if (project is None or (operationId in self._uncommitted
                                and operationId not in self._writing)):
            return

        def completed(_value: object, error: str | None) -> None:
            if error is not None and self._workspace.pcrRuns.getView(operationId) is view:
                message = f"Could not save PCR preview settings to the project. {error}"
                self._controller.message.emit(message)
                self.viewSaveFailed.emit(operationId, message)

        # Submit every accepted value immediately. No Qt callback is needed to
        # deliver the final value if the project or window is closed meanwhile.
        self._submitProjectTask(
            lambda: project.updatePcrView(operationId, view, timeout=_PROJECT_TIMEOUT_SECONDS),
            completed, write=True,
        )

    def getRun(self, operationId: str) -> PcrRun:
        """Load the completed or incomplete result with this exact operation ID."""
        entry = self._workspace.pcrRuns.get(operationId)
        if entry is not None:
            return entry.result
        result = self._storedRuns.get(operationId)
        if result is not None:
            return result
        project = self._workspace.project
        if project is None:
            raise KeyError(operationId)
        result = project.getPcrRun(operationId)
        self._storedRuns[operationId] = result
        return result

    def getOutput(self, operationId: str, outputKey: str) -> PcrProduct:
        return pcrOutput(self.getRun(operationId), outputKey)

    def historyRuns(self) -> tuple[PcrRun, ...]:
        """List recorded attempts on the active template, including stopped searches."""
        revision = self._controller.session.current
        return () if revision is None else self._listRuns(revision.revisionId)

    def availableRuns(self) -> tuple[PcrRun, ...]:
        """List completed searches across retained sources for Gel and Assembly."""
        return tuple(run for run in self._listRuns(None) if run.status == "completed")

    @property
    def loadingAvailableRuns(self) -> bool:
        """Indicate whether the project-wide PCR list is being loaded."""
        return None in self._loadingHistory

    @property
    def historyReadError(self) -> str:
        """Return the error message from a failed read of saved PCR runs or preview settings."""
        revision = self._controller.session.current
        if revision is not None and revision.revisionId in self._historyErrors:
            return self._historyErrors[revision.revisionId]
        return self._historyErrors.get(None, "")

    def refreshActiveHistory(self) -> None:
        """Re-list the visible revision on a navigation event, including after a project-wide read."""
        revision = self._controller.session.current
        if revision is not None:
            self._requestHistory(revision.revisionId, refresh=True)

    def retryPcrHistory(self) -> None:
        """Retry incomplete project reads while keeping successfully restored runs and views."""
        if self._historyErrors and not self.loadingAvailableRuns:
            self._loadedHistory.discard(None)
            self._requestHistory(None, retry=True)
            self.historyChanged.emit()

    def _listRuns(self, revisionId: str | None) -> tuple[PcrRun, ...]:
        self._requestHistory(revisionId)
        runs = {
            run.operationId: run
            for run in (*self._storedRuns.values(), *self._workspace.pcrRuns.runs)
            if revisionId is None or run.template.revisionId == revisionId
        }
        return tuple(sorted(runs.values(), key=lambda run: run.createdAt, reverse=True))

    def _requestHistory(
        self, revisionId: str | None, *, retry: bool = False, refresh: bool = False,
    ) -> None:
        project = self._workspace.project
        if project is None:
            return
        if revisionId in self._loadingHistory or None in self._loadingHistory:
            if refresh:
                self._refreshAfterRead = revisionId
            return
        if not refresh and (None in self._loadedHistory or revisionId in self._loadedHistory
                            or (revisionId in self._historyErrors and not retry)):
            return
        stored = self._storedRuns.copy()
        known = {
            key for key, run in stored.items()
            if run.status != "completed" or self._workspace.pcrRuns.getView(key) is not None
        }
        known.update(run.operationId for run in self._workspace.pcrRuns.runs)
        self._loadingHistory.add(revisionId)

        def load() -> tuple[dict[str, PcrRun], dict[str, GelViewSettings | None], tuple[str, ...]]:
            runs, views, errors = {}, {}, []
            for operationId in project.listPcrRunIds(revisionId):
                if operationId in known:
                    continue
                try:
                    run = stored.get(operationId) or project.getPcrRun(operationId)
                    runs[operationId] = run
                    if run.status == "completed":
                        views[operationId] = project.getPcrView(operationId)
                except _ERRORS as error:
                    errors.append(f"Run {operationId} · {error}")
            return runs, views, tuple(errors)

        def completed(value: object, error: str | None) -> None:
            self._loadingHistory.discard(revisionId)
            if error is None:
                runs, views, failures = value
                self._storedRuns.update(runs)
                self._unreadViews.update(
                    key for key, run in runs.items() if run.status == "completed" and key not in views
                    and self._workspace.pcrRuns.getView(key) is None
                )
                self._unreadViews.difference_update(views)
                for key, view in views.items():
                    if self._workspace.pcrRuns.getView(key) is None:
                        self._workspace.pcrRuns.setView(key, view or self._defaultView(runs[key]))
            else:
                failures = (error,)
            if failures:
                message = f"Could not read PCR history. {failures[0]}"
                if len(failures) > 1:
                    message += f" ({len(failures)} reads failed.)"
                self._historyErrors[revisionId] = message
                if self.historyReadError == message:
                    self._controller.message.emit(f"{message} Choose Retry PCR history in History.")
            else:
                self._loadedHistory.add(revisionId)
                if revisionId is None:
                    self._historyErrors.clear()
                else:
                    self._historyErrors.pop(revisionId, None)
            followup = self._refreshAfterRead
            if followup is not None and (revisionId is None or revisionId == followup):
                self._refreshAfterRead = None
                active = self._controller.session.current
                if active is not None and active.revisionId == followup:
                    self.refreshActiveHistory()
            self.historyChanged.emit()

        self._submitProjectTask(load, completed)

    def reopenRun(self, operationId: str, outputKey: str | None = None) -> bool:
        """Open one recorded result at an optional product link."""
        def reopen() -> None:
            result = self.getRun(operationId)
            if outputKey is not None:
                pcrOutput(result, outputKey)
            self.runOpened.emit(result, outputKey)

        return self._controller._tryOperation(reopen)

    def openRecordedRun(self, operationId: str, *, view: GelViewSettings) -> bool:
        """Open the exact preview captured by a pinned Gel lane."""
        def reopen() -> None:
            result = self.getRun(operationId)
            if view.selectedOutputKey is not None:
                pcrOutput(result, view.selectedOutputKey)
            self.previewOpened.emit(result, view)

        return self._controller._tryOperation(reopen)

    def copyProduct(self, operationId: str, outputKey: str) -> bool:
        """Copy the chosen mature amplicon's complete 5′→3′ sequence."""
        success = False

        def copy() -> None:
            nonlocal success
            sequence = self.getOutput(operationId, outputKey).revision.sequence
            clipboard = QApplication.clipboard()
            clipboard.setText(sequence)
            success = clipboard.text() == sequence
            message = f"Copied the {len(sequence):,} bp PCR product." if success else (
                "Clipboard unavailable. Copy from the selectable preview."
            )
            self._controller.message.emit(message)

        self._controller._tryOperation(copy)
        return success

    def adoptProduct(self, operationId: str, outputKey: str) -> bool:
        """Open an independent product draft without changing its template."""
        opened = self._controller._changeWorkspace(
            lambda: self._workspace.openPcrProduct(self.getRun(operationId), outputKey),
        )
        if opened:
            self.productOpened.emit()
        return opened

    @property
    def assemblyPreparation(self) -> AssemblyPcrInput | None:
        return self._assemblyPreparation

    def prepareAssembly(
        self, operationId: str, product: PcrProduct, offer: Callable[[AssemblyPcrInput], None],
    ) -> None:
        """Offer the recorded product to the picker before updating the Assembly preparation."""
        preparation = AssemblyPcrInput(self.getRun(operationId), product)
        offer(preparation)
        self._assemblyPreparation = preparation
        self.assemblyPrepared.emit(preparation)

    def openSource(self, revisionId: str) -> bool:
        """Reopen the exact source revision independently of current edits and tabs."""
        retained = self._workspace.pcrRuns.snapshot(revisionId)
        opened = self._controller._changeWorkspace(
            lambda: self._workspace.openExactRevision(revisionId, retained=retained),
        )
        if opened:
            self.sourceOpened.emit()
        return opened

    def reset(self) -> None:
        """Release PCR inputs and retained view state on an explicit project switch."""
        self.cancelPcr()
        self._editorResults.clear()
        self._inputs.clear()
        self._uncommitted.clear()
        self._writing.clear()
        self._storedRuns.clear()
        self._loadingHistory.clear()
        self._refreshAfterRead = None
        self._loadedHistory.clear()
        self._historyErrors.clear()
        self._unreadViews.clear()
        self._assemblyPreparation = None
        self._settings = PcrSettings()
        self._setStatus("idle", "")
        self.retryChanged.emit()
