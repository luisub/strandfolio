"""Coordinate Sequence restriction tools, recorded digests and Gel handoffs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import Bio
from PySide6.QtCore import QObject, QStandardPaths, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication

from strandfolio.application.tasks import TaskInput, TaskRequest
from strandfolio.application.workspace import WorkspaceDocument
from strandfolio.data import DATABASE_DIRECTORY, HAS_BUNDLED_DATABASES
from strandfolio.domain.fragments import DNAFragment
from strandfolio.domain.provenance import OutputReference
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.io.digestion import SavedDigestion, readDigestion, validateDigestionView, writeDigestion
from strandfolio.io.formats import SequenceImportResult
from strandfolio.operations.gel import (
    GelLane, GelMarker, GelSelection, GelState, GelViewSettings, digestLane, pcrLane, untreatedLane,
)
from strandfolio.operations.pcr import PcrRun
from strandfolio.operations.restriction import (
    RESTRICTION_ENGINE_VERSION, RecognitionSite, RestrictionCut, RestrictionDigest, RestrictionScan,
    digestRestriction, predictRestrictionCuts, rerunRestriction, scanRestrictionSites,
)
from strandfolio.storage.catalogues import (
    DEFAULT_LADDER_ID, CatalogueRelease, EnzymeRecord, LadderProfile,
    RestrictionCatalogue, loadDnaLadders,
)
from strandfolio.storage.project_edits import AnnotationStep
from strandfolio.storage.project_runs import CertifiedRestrictionRun, certifyRestrictionRun


if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController


if HAS_BUNDLED_DATABASES:
    _cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    _enzymeDirectory = Path(_cache) / "strandfolio" / f"enzymes-v1-biopython-{Bio.__version__}"
else:
    _enzymeDirectory = DATABASE_DIRECTORY / "enzymes"
DEFAULT_RESTRICTION_CATALOGUE_PATH = _enzymeDirectory / "restriction_enzymes.sqlite3"
_DEFAULT_OVERLAY_NAMES = ("EcoRI", "BamHI", "KpnI", "SmaI", "SacI", "HindIII", "EcoRV", "XhoI")
_ERRORS = (OSError, ValueError, KeyError, sqlite3.Error)
_ADD_LANE_SLOT = "new-lane"
_ASSEMBLY_READ_SLOT = "read-digests"
_PROJECT_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _UncommittedDigest:
    result: RestrictionDigest
    view: GelViewSettings
    proof: CertifiedRestrictionRun | None
    documentId: str | None
    capture: SequenceImportResult | None = None
    independent: bool = False
    forGel: bool = False


@dataclass(slots=True)
class _PendingView:
    saved: SavedDigestion
    requested: GelViewSettings


class DigestCoordinator(QObject):
    """Run restriction requests and share recorded results with the Sequence, Gel and Assembly views."""

    recipeChanged = Signal()
    scanChanged = Signal()
    gelChanged = Signal()
    ladderCatalogueChanged = Signal()
    historyChanged = Signal()
    assemblyDigestionsChanged = Signal()
    retryChanged = Signal()
    busyChanged = Signal(bool)
    digestionOpened = Signal(object)
    digestCompleted = Signal(object)
    rerunCompleted = Signal(str, object)
    viewSaveFailed = Signal(str, object, object, str)
    sourceOpened = Signal()
    constructOpened = Signal()
    revealSite = Signal(int, int)

    def __init__(
        self, controller: WorkspaceController, restrictionCataloguePath: str | Path,
        ladderCataloguePath: str | Path,
    ) -> None:
        super().__init__(controller)
        self._controller = controller
        self._workspace = controller.workspace
        self._restrictionCataloguePath = Path(restrictionCataloguePath)
        self._ladderCataloguePath = Path(ladderCataloguePath)
        self._enzymes: dict[str, EnzymeRecord] = {}
        self._catalogueRelease: CatalogueRelease | None = None
        self._catalogueError = ""
        self._ladderProfiles: tuple[LadderProfile, ...] | None = None
        self._ladderError = ""
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._work: dict[int, tuple[TaskRequest, CalculationTask, Callable[[object], None]]] = {}
        self._savingRuns: dict[int, TaskRequest] = {}
        self._busy = False
        self._savingDigest = False
        self._pendingGel: frozenset[str | None] = frozenset()
        self._scan: RestrictionScan | None = None
        self._scanCuts: tuple[RestrictionCut, ...] = ()
        self._acceptedScans: dict[
            TaskInput, tuple[RestrictionScan, tuple[RestrictionCut, ...]],
        ] = {}
        self._digestions = self._workspace.digestions
        self._projectDigestIds: set[str] = set()
        self._pendingViews: dict[str, _PendingView] = {}
        self._storedAssemblyDigestions: tuple[RestrictionDigest, ...] | None = None
        self._loadingAssemblyDigestions = False
        self._uncommitted: dict[str, _UncommittedDigest] = {}
        self._retrying: set[str] = set()
        self._gelState = GelState()
        self._gelHasConfiguration = False
        self._pinnedGelViews: dict[str, GelViewSettings] = {}
        self._removedLane: tuple[int, GelLane, GelViewSettings | None] | None = None
        controller.documentChanged.connect(self._activeChanged)
        controller.collectionChanged.connect(self._collectionChanged)

    @property
    def restrictionCataloguePath(self) -> Path:
        return self._restrictionCataloguePath

    @restrictionCataloguePath.setter
    def restrictionCataloguePath(self, path: str | Path) -> None:
        path = Path(path)
        if path == self._restrictionCataloguePath:
            return
        self._restrictionCataloguePath = path
        self._catalogueRelease = None
        self._catalogueError = ""
        self._enzymes.clear()
        self._acceptedScans.clear()
        for slot in ("catalogue", "scan", "run"):
            self._workspace.tasks.cancel("digest", slot)
        for laneId in self._pendingGel:
            self._workspace.tasks.cancel("gel", laneId or _ADD_LANE_SLOT)
        self._syncPending()
        self._activeChanged()

    @property
    def availableEnzymes(self) -> tuple[EnzymeRecord, ...]:
        """Return the available immutable catalogue definitions once loaded."""
        return tuple(self._enzymes.values())

    def _requestCatalogue(self) -> None:
        if self._catalogueError or self._readingCatalogue("catalogue"):
            return
        path = self.restrictionCataloguePath

        def load() -> tuple[CatalogueRelease, tuple[EnzymeRecord, ...]]:
            if HAS_BUNDLED_DATABASES and path == DEFAULT_RESTRICTION_CATALOGUE_PATH and not path.exists():
                from strandfolio.storage.build_restriction_catalogue import buildDatabase

                try:
                    buildDatabase(path)
                except FileExistsError:
                    # Another process may have published its fully validated catalogue first.
                    if not path.is_file():
                        raise
            with RestrictionCatalogue(path) as catalogue:
                count = catalogue.summary()["enzymes"]
                enzymes = tuple(
                    enzyme for offset in range(0, count, 1000)
                    for enzyme in catalogue.search(limit=1000, offset=offset)
                )
                return catalogue.release, enzymes

        self._start("digest", "catalogue", load, self._acceptCatalogue,
                    description="Reading the restriction catalogue", affectsClose=False)

    def _readingCatalogue(self, slot: str) -> bool:
        return any(request.owner == "digest" and request.slot == slot
                   and self._workspace.tasks.isCurrent(request)
                   for request, _worker, _publish in self._work.values())

    @property
    def catalogueError(self) -> str:
        """Return the reason that the last enzyme catalogue read failed."""
        return self._catalogueError

    def retryRestrictionCatalogue(self) -> None:
        """Retry a failed catalogue read on the current eligible document."""
        if self._catalogueError:
            self._catalogueError = ""
            self._requestScan()
            self.recipeChanged.emit()

    def _acceptCatalogue(self, response: tuple[CatalogueRelease, tuple[EnzymeRecord, ...]]) -> None:
        self._catalogueRelease, enzymes = response
        self._enzymes = {enzyme.name.casefold(): enzyme for enzyme in enzymes}
        self._requestScan()
        self.recipeChanged.emit()

    @property
    def ladderProfiles(self) -> tuple[LadderProfile, ...]:
        if self._ladderProfiles is None and not self._ladderError:
            try:
                self._ladderProfiles = loadDnaLadders(self._ladderCataloguePath)
            except _ERRORS as error:
                self._ladderError = f"Could not read the ladder catalogue. {error}"
        return self._ladderProfiles or ()

    @property
    def ladderCatalogueError(self) -> str:
        """Return the reason that the last ladder catalogue read failed."""
        return self._ladderError

    @property
    def canRetryLadders(self) -> bool:
        return bool(self._ladderError) and not self._readingCatalogue("ladders")

    def retryLadderCatalogue(self) -> None:
        """Reload repaired marker data without changing any recorded ladder selection."""
        if not self.canRetryLadders:
            return

        def accept(profiles: tuple[LadderProfile, ...]) -> None:
            self._ladderProfiles = profiles
            self._ladderError = ""
            self.ladderCatalogueChanged.emit()

        self._start("digest", "ladders", lambda: loadDnaLadders(self._ladderCataloguePath),
                    accept, description="Reading the ladder catalogue", affectsClose=False)
        self.ladderCatalogueChanged.emit()

    def resolveGelMarker(self, marker: GelMarker) -> GelMarker:
        """Fill a missing marker from the read-only catalogue at the recorded release."""
        if marker.profile is not None:
            return marker
        for profile in self.ladderProfiles:
            if (profile.ladderId == marker.ladderId
                    and (marker.release is None or profile.release == marker.release)):
                return GelMarker.fromProfile(profile)
        return marker

    @property
    def recipe(self) -> tuple[str, ...]:
        document = self._workspace.activeDocument
        return document.viewState.digestEnzymes if document is not None else ()

    @property
    def overlayNames(self) -> tuple[str, ...]:
        document = self._workspace.activeDocument
        if document is None:
            return ()
        names = document.viewState.restrictionOverlayNames
        if names is not None:
            return names
        return tuple(name for name in _DEFAULT_OVERLAY_NAMES if name.casefold() in self._enzymes)

    @property
    def scan(self) -> RestrictionScan | None:
        return self._scan

    @property
    def displayedSites(self) -> tuple[RecognitionSite, ...]:
        selected = set(self.overlayNames)
        return (() if self._scan is None else
                tuple(site for site in self._scan.sites if site.enzymeName in selected))

    @property
    def displayedCuts(self) -> tuple[RestrictionCut, ...]:
        selected = set(self.overlayNames)
        return tuple(
            cut for cut in self._scanCuts if any(site.enzymeName in selected for site in cut.sites)
        )

    def _normalizeNames(self, names: Iterable[str]) -> tuple[str, ...]:
        result = {}
        for name in names:
            if type(name) is not str or not name.strip():
                raise ValueError("Choose a valid enzyme name.")
            enzyme = self._enzymes.get(name.casefold())
            if enzyme is None:
                raise ValueError(f"Enzyme {name!r} is unavailable in the restriction catalogue.")
            result[enzyme.name] = None
        return tuple(result)

    def _eligibleDocument(self, documentId: str | None = None) -> WorkspaceDocument:
        document = (self._workspace.activeDocument if documentId is None
                    else self._workspace.getDocument(documentId))
        if document is None:
            raise ValueError("Open a DNA sequence.")
        if document.revision.moleculeType != "DNA" or document.revision.topology is None:
            raise ValueError("Restriction recognition requires DNA with explicit linear or circular topology.")
        return document

    @property
    def savingDigest(self) -> bool:
        """Whether the current Digest request has entered its project write."""
        return self._savingDigest

    @property
    def digestUnavailableReason(self) -> str:
        try:
            self._eligibleDocument()
        except _ERRORS as error:
            return str(error)
        if self._savingDigest:
            return "Saving the digest to the project."
        if self._busy:
            return "Digest calculation is pending."
        if self._catalogueRelease is None:
            return self._catalogueError or "Loading the restriction enzyme catalogue."
        if not self._enzymes:
            return self._catalogueError or "The restriction enzyme catalogue is unavailable."
        if not self.recipe:
            return "Add one or more digest enzymes."
        return ""

    def addEnzyme(self, name: str) -> bool:
        """Add one catalogue enzyme to the active recipe once."""
        return self._controller._tryOperation(lambda: self._setRecipe((*self.recipe, name)))

    def removeEnzyme(self, name: str) -> bool:
        return self._controller._tryOperation(lambda: self._setRecipe(
            tuple(value for value in self.recipe if value.casefold() != name.casefold())
        ))

    def _setRecipe(self, names: Iterable[str]) -> None:
        document = self._eligibleDocument()
        names = self._normalizeNames(names)
        state = document.viewState
        if names == state.digestEnzymes:
            return
        if not state.digestEnzymes and names:
            state.selectedDigestEnzyme = names[0]
        elif state.selectedDigestEnzyme not in names:
            state.selectedDigestEnzyme = None
        state.digestEnzymes = names
        if any(request.owner == "digest" and request.slot == "run" and request.inputs
               and self._workspace.tasks.isCurrent(request)
               for request in self._runRequests()):
            self._workspace.tasks.cancel("digest", "run")
        self._syncPending()
        self._requestScan()
        self.recipeChanged.emit()

    def setOverlayNames(self, names: Iterable[str]) -> bool:
        """Change displayed occurrences without changing the reaction recipe."""
        def apply() -> None:
            document = self._eligibleDocument()
            normalized = self._normalizeNames(names)
            if normalized != self.overlayNames:
                document.viewState.restrictionOverlayNames = normalized
                self._requestScan()
                self.scanChanged.emit()

        return self._controller._tryOperation(apply)

    def activateSite(self, site: RecognitionSite) -> bool:
        """Reveal one current occurrence and add its enzyme while keeping DNA selection."""
        if site not in self.displayedSites or not self.addEnzyme(site.enzymeName):
            return False
        self.revealSite.emit(*site.sourceParts[0])
        return True

    def _activeChanged(self) -> None:
        self._syncPending()
        documentId = self._workspace.activeDocumentId
        key = self._workspace.taskInput("template", documentId) if documentId is not None else None
        self._scan, self._scanCuts = self._acceptedScans.get(key, (None, ()))
        self._requestScan()
        self.recipeChanged.emit()
        self.scanChanged.emit()
        self.historyChanged.emit()
        self.retryChanged.emit()

    def _collectionChanged(self) -> None:
        current = {
            self._workspace.taskInput("template", document.documentId)
            for document in self._workspace.documents
        }
        self._acceptedScans = {key: value for key, value in self._acceptedScans.items() if key in current}
        self._syncPending()

    def _requestScan(self) -> None:
        try:
            document = self._eligibleDocument()
        except _ERRORS:
            self._workspace.tasks.cancel("digest", "scan")
            self._syncPending()
            return
        if self._catalogueRelease is None:
            self._requestCatalogue()
            return
        savedNames = () if self._scan is None else tuple(value.name for value in self._scan.enzymes)
        allNames = (*_DEFAULT_OVERLAY_NAMES, *savedNames, *self.overlayNames, *self.recipe)
        names = tuple(dict.fromkeys(name for name in allNames if name.casefold() in self._enzymes))
        if not names or set(names) == set(savedNames):
            return
        inputs = (self._workspace.taskInput("template", document.documentId),)
        versions = (self._catalogueRelease,)
        if any(
            request.owner == "digest" and request.slot == "scan"
            and request.inputs == inputs and request.settings == names and request.versions == versions
            and self._workspace.tasks.isCurrent(request)
            for request, _worker, _publish in self._work.values()
        ):
            return
        path, release, revision = self.restrictionCataloguePath, self._catalogueRelease, document.revision

        def scan() -> tuple[RestrictionScan, tuple[RestrictionCut, ...]]:
            with RestrictionCatalogue(path) as catalogue:
                if catalogue.release != release:
                    raise ValueError("The restriction catalogue changed during this session.")
                result = scanRestrictionSites(revision, catalogue, names)
            cuts, _ = predictRestrictionCuts(result)
            return result, cuts

        self._start(
            "digest", "scan", scan, self._acceptScan, inputs=inputs, settings=names,
            versions=versions, bindActive=True, affectsClose=False,
            description=f"Scanning restriction sites in {revision.name}",
        )

    def _acceptScan(self, response: tuple[RestrictionScan, tuple[RestrictionCut, ...]]) -> None:
        self._scan, self._scanCuts = response
        documentId = self._workspace.activeDocumentId
        self._acceptedScans[self._workspace.taskInput("template", documentId)] = response
        self.scanChanged.emit()

    def _start(
        self, owner: str, slot: str, calculate: Callable[[], object],
        publish: Callable[[object], None], *, description: str,
        inputs: tuple[TaskInput, ...] = (), settings: tuple = (), versions: tuple = (),
        bindActive: bool = False, affectsClose: bool = True,
    ) -> TaskRequest:
        request = self._workspace.tasks.begin(
            owner, slot, inputs=inputs, settings=settings, versions=versions,
            description=description, bindActive=bindActive, affectsClose=affectsClose,
        )
        worker = CalculationTask(
            request.requestId, lambda _isCanceled: calculate(), failureContext="restriction analysis",
        )
        worker.signals.completed.connect(self._onTaskCompleted)
        self._work[request.requestId] = (request, worker, publish)
        self._syncPending()
        self._pool.start(worker)
        return request

    def _runRequests(self) -> Iterable[TaskRequest]:
        yield from (request for request, _worker, _publish in self._work.values())
        yield from self._savingRuns.values()

    def _syncPending(self) -> None:
        current = [request for request in self._savingRuns.values() if self._workspace.tasks.isCurrent(request)]
        for request, worker, _publish in self._work.values():
            if self._workspace.tasks.isCurrent(request):
                current.append(request)
            else:
                self._workspace.tasks.finish(request)
                worker.cancel()
        digest = next((request for request in current if request.owner == "digest" and request.slot == "run"), None)
        busy = digest is not None
        saving = busy and digest.requestId in self._savingRuns
        pending = frozenset(
            None if request.slot == _ADD_LANE_SLOT else request.slot
            for request in current if request.owner == "gel"
        )
        busyChanged = self._busy != busy or self._savingDigest != saving
        pendingChanged = self._pendingGel != pending
        self._busy, self._savingDigest, self._pendingGel = busy, saving, pending
        if busyChanged:
            self.busyChanged.emit(busy)
        if pendingChanged:
            self.gelChanged.emit()

    @Slot(int, object, object)
    def _onTaskCompleted(self, requestId: int, value: object, error: str | None) -> None:
        item = self._work.pop(requestId, None)
        if item is None:
            return
        request, _worker, publish = item
        try:
            if self._workspace.tasks.isCurrent(request):
                isAssemblyRead = request.owner == "assembly" and request.slot == _ASSEMBLY_READ_SLOT
                if isAssemblyRead:
                    self._loadingAssemblyDigestions = False
                if error is None:
                    self._controller._tryOperation(lambda: publish(value))
                elif request.slot == "catalogue":
                    self._catalogueError = f"Could not read the restriction enzyme catalogue. {error}"
                    self.recipeChanged.emit()
                elif request.owner == "digest" and request.slot == "ladders":
                    self._ladderError = f"Could not read the ladder catalogue. {error}"
                    self.ladderCatalogueChanged.emit()
                elif request.slot == "scan":
                    self._controller.message.emit(error)
                elif isAssemblyRead:
                    self._controller.message.emit(f"Could not read stored digestions. {error}")
                    self.assemblyDigestionsChanged.emit()
                else:
                    self._controller.operationFailed.emit(error)
        finally:
            if requestId not in self._savingRuns:
                self._workspace.tasks.finish(request)
            self._syncPending()

    def runDigest(self) -> bool:
        """Start a digest bound to the entire active DNA revision and current recipe."""
        reason = self.digestUnavailableReason
        if reason:
            self._controller.operationFailed.emit(reason)
            return False
        document = self._workspace.activeDocument
        return self._controller._tryOperation(lambda: self._startLiveDigest(document, self.recipe))

    def cancelDigest(self) -> None:
        if not self._savingDigest and self._workspace.tasks.cancel("digest", "run"):
            self._syncPending()
            self._controller.message.emit("Digest canceled.")

    def _startLiveDigest(
        self, document: WorkspaceDocument, names: tuple[str, ...], *,
        forGel: bool = False, laneId: str | None = None, label: str | None = None,
    ) -> None:
        revision = document.revision
        path, release = self.restrictionCataloguePath, self._catalogueRelease
        capture, independent = document.capture, document.isIndependentIntake
        recordInProject = self._workspace.project is not None and not document.isDraft
        view = (GelViewSettings(settings=self.gelState.settings, marker=self.gelState.marker)
                if forGel else GelViewSettings(marker=self.resolveGelMarker(GelMarker())))

        def calculate() -> tuple[RestrictionDigest, CertifiedRestrictionRun | None]:
            with RestrictionCatalogue(path) as catalogue:
                if catalogue.release != release:
                    raise ValueError("The restriction catalogue changed during this session.")
                run = digestRestriction(revision, catalogue, names)
            return run, self._certify(run, recordInProject)

        def show(digestion: SavedDigestion) -> None:
            run = digestion.result
            if not forGel:
                self.digestCompleted.emit(digestion)
            elif run.status == "completed":
                self._pinDigest(digestion, laneId, label, abortPending=False)
            else:
                raise ValueError("\n".join(run.warnings))

        def publish(response: tuple[RestrictionDigest, CertifiedRestrictionRun | None]) -> None:
            run, proof = response
            self._recordRun(request, _UncommittedDigest(
                run, view, proof, document.documentId, capture, independent, forGel,
            ), show)

        owner = "gel" if forGel else "digest"
        slot = (laneId or _ADD_LANE_SLOT) if forGel else "run"
        request = self._start(
            owner, slot, calculate, publish,
            inputs=(self._workspace.taskInput("template", document.documentId),),
            settings=names, versions=(release,), bindActive=not forGel,
            description=f"Digesting {revision.name}" if not forGel
            else f"Preparing Gel sample from {revision.name}",
        )

    @staticmethod
    def _certify(run: RestrictionDigest, recordInProject: bool) -> CertifiedRestrictionRun | None:
        return certifyRestrictionRun(run) if recordInProject and run.status == "completed" else None

    def _recordRun(
        self, request: TaskRequest, pending: _UncommittedDigest, show: Callable[[SavedDigestion], None],
    ) -> None:
        operationId = pending.result.operationId
        try:
            write = self._prepareWrite(pending)
        except _ERRORS:
            self._uncommitted[operationId] = pending
            self.retryChanged.emit()
            raise
        if pending.proof is None:
            digestion = write()
            if digestion.result.status == "completed":
                self._digestions.retain(digestion)
                self.historyChanged.emit()
            show(digestion)
            return

        projectContext = self._workspace.taskContext(False)

        def complete(digestion: SavedDigestion, error: str | None) -> None:
            self._savingRuns.pop(request.requestId, None)
            current = self._workspace.tasks.finish(request)
            self._syncPending()
            if self._workspace.taskContext(False) != projectContext:
                return
            if error is not None:
                self._uncommitted[operationId] = pending
                self.retryChanged.emit()
                if current:
                    self._controller.operationFailed.emit(
                        f"Could not save the digest for {pending.result.template.name}. {error}"
                    )
                return
            self._projectDigestIds.add(operationId)
            self._digestions.retain(digestion)
            self.historyChanged.emit()
            if current:
                self._controller._tryOperation(lambda: show(digestion))

        self._savingRuns[request.requestId] = request
        self._controller.projectTasks.submit(write, complete, write=True)

    def pendingSaveRuns(
        self, scope: Literal["document", "gel", "project"] = "document",
    ) -> tuple[RestrictionDigest, ...]:
        """List failed writes available from this document, explicit gel samples or History."""
        document = self._workspace.activeDocument
        runs = []
        for key, pending in reversed(self._uncommitted.items()):
            if key in self._retrying:
                continue
            if scope == "project":
                eligible = self._workspace.project is not None and pending.proof is not None
            elif scope == "gel":
                eligible = pending.forGel
            else:
                eligible = (document is not None and not pending.forGel
                            and pending.documentId == document.documentId
                            and pending.result.template.revisionId == document.revision.revisionId)
            if eligible:
                runs.append(pending.result)
        return tuple(runs)

    @property
    def provenanceRetryId(self) -> str | None:
        """Identify the latest retryable write on the active document."""
        return next((run.operationId for run in self.pendingSaveRuns()), None)

    def retryDigestWrite(
        self, operationId: str | None = None, *,
        scope: Literal["document", "gel", "project"] = "document",
        report: Callable[[str], None] | None = None,
    ) -> bool:
        """Save a specific captured result once, with feedback in its originating context."""
        runs = self.pendingSaveRuns(scope)
        if operationId is None and scope == "document" and len(runs) == 1:
            operationId = runs[0].operationId
        if operationId is None or not any(run.operationId == operationId for run in runs):
            return False
        pending = self._uncommitted[operationId]
        projectContext = self._workspace.taskContext(False)
        tabContext = self._workspace.taskContext(True)
        try:
            write = self._prepareWrite(pending)
        except _ERRORS as error:
            if report is not None:
                report(str(error))
            elif scope == "document":
                self._controller.operationFailed.emit(str(error))
            return False

        def complete(digestion: SavedDigestion, error: str | None) -> None:
            if self._workspace.taskContext(False) != projectContext:
                return
            self._retrying.discard(operationId)
            if error is not None:
                self.retryChanged.emit()
                message = f"Could not save digest {operationId[:8]} for {pending.result.template.name}. {error}"
                if report is not None:
                    report(message)
                elif scope == "document" and self._workspace.taskContext(True) == tabContext:
                    self._controller.operationFailed.emit(message)
                return
            del self._uncommitted[operationId]
            if pending.proof is not None:
                self._projectDigestIds.add(operationId)
            self._digestions.retain(digestion)
            self.retryChanged.emit()
            self.historyChanged.emit()
            if scope == "document":
                if self._workspace.taskContext(True) != tabContext:
                    return
                self.digestCompleted.emit(digestion)
            elif report is not None:
                text = f"Saved digest {operationId[:8]} for {pending.result.template.name}."
                if scope == "gel":
                    text += " Select it from Completed digest in Gel."
                report(text)

        self._retrying.add(operationId)
        self.retryChanged.emit()
        self._controller.projectTasks.submit(write, complete, write=True)
        return True

    def _prepareWrite(self, pending: _UncommittedDigest) -> Callable[[], SavedDigestion]:
        """Capture the session evidence before running the project write on either thread."""
        run, view, proof = pending.result, pending.view, pending.proof
        if run.fragments and view.selectedOutputKey is None:
            view = replace(view, selectedOutputKey=run.fragments[0].outputKey)
        if run.status != "completed":
            return lambda: SavedDigestion(run, view)
        project = self._workspace.project
        annotationEvidence = self._workspace.annotationEvidence(run.template)
        # Only completed runs destined for project History carry a write certification.
        if proof is None or project is None:
            digestion = self._digestions.build(run, view, pending.capture, annotationEvidence)
            return lambda: digestion
        exactCapture = None
        capture = pending.capture
        if capture is not None and run.template.sourceOrdinal is not None:
            selected = capture.records[run.template.sourceOrdinal - 1]
            if selected.revisionId == run.template.revisionId:
                exactCapture = capture
        evidence = self._workspace.analysisEvidence(run.template)
        hasPcrAncestor = any(isinstance(item, PcrRun) for item in evidence.runs)
        prior = None
        if exactCapture is None and not hasPcrAncestor:
            prior = self._digestions.sourceBundle(run.template.revisionId)
        ancestors = {
            "ancestors": evidence.runs, "ancestorCapture": evidence.capture,
            "ancestorIndependent": evidence.independent,
            "ancestorCertifications": evidence.certifications,
            "ancestorViews": evidence.views,
            "ancestorAnnotations": evidence.annotations,
        } if hasPcrAncestor else {}

        def write() -> SavedDigestion:
            if prior is not None:
                try:
                    project.getRevision(run.template.revisionId)
                except KeyError:
                    project.registerDigestion(prior, timeout=_PROJECT_TIMEOUT_SECONDS)
            project.registerRestrictionRun(
                run, capture=exactCapture, independent=pending.independent and exactCapture is not None,
                view=view, certification=proof, annotationEvidence=annotationEvidence,
                timeout=_PROJECT_TIMEOUT_SECONDS, **ancestors,
            )
            return project.getDigestion(run.operationId)

        return write

    def rerunDigestion(self, digestion: SavedDigestion) -> bool:
        """Rerun a saved digestion with the current engine and its recorded recipe."""
        def start() -> None:
            if self._busy:
                raise ValueError("A digest is already in progress.")
            original, recordInProject = self._findDigestion(digestion.result.operationId)
            document = self._workspace.activeDocument
            documentId = None
            if document is not None and document.revision.revisionId == original.result.template.revisionId:
                documentId = document.documentId

            def calculate() -> tuple[RestrictionDigest, CertifiedRestrictionRun | None]:
                run = rerunRestriction(original.result)
                return run, self._certify(run, recordInProject)

            def publish(response: tuple[RestrictionDigest, CertifiedRestrictionRun | None]) -> None:
                run, proof = response
                view = replace(original.view, selectedOutputKey=None)
                self._recordRun(
                    request, _UncommittedDigest(run, view, proof, documentId),
                    lambda result: self.rerunCompleted.emit(original.result.operationId, result),
                )

            run = original.result
            request = self._start(
                "digest", "run", calculate, publish,
                settings=(run.operationId, run.template.revisionId),
                versions=(RESTRICTION_ENGINE_VERSION, run.catalogueRelease),
                description=f"Rerunning digestion of {run.template.name}", bindActive=True,
            )

        return self._controller._tryOperation(start)

    def _getDigestion(self, operationId: str) -> SavedDigestion:
        digestion, _ = self._findDigestion(operationId)
        return digestion

    def _findDigestion(self, operationId: str) -> tuple[SavedDigestion, bool]:
        """Find the current view and whether the run is recorded in the open project."""
        pending = self._pendingViews.get(operationId)
        if pending is not None:
            return replace(pending.saved, view=pending.requested), True
        project = self._workspace.project
        digestion = None
        if project is not None:
            try:
                digestion = project.getDigestion(operationId)
            except KeyError:
                pass
        recordedInProject = digestion is not None
        if digestion is None:
            digestion = self._digestions.get(operationId)
        if digestion is None:
            raise KeyError(operationId)
        marker = self.resolveGelMarker(digestion.view.marker)
        if digestion.view.marker != marker:
            digestion = replace(digestion, view=replace(digestion.view, marker=marker))
        if recordedInProject:
            self._projectDigestIds.add(operationId)
            self._digestions.retain(digestion)
        return digestion, recordedInProject

    def historyRuns(self) -> tuple[RestrictionDigest, ...]:
        revision = self._controller.session.current
        if revision is None:
            return ()
        return self._listRuns(revision.revisionId)

    def availableDigestions(self) -> tuple[RestrictionDigest, ...]:
        return self._listRuns(None)

    @property
    def loadingAssemblyDigestions(self) -> bool:
        """Indicate that the current project's completed digestions are loading."""
        return self._loadingAssemblyDigestions

    def requestAssemblyDigestions(self) -> None:
        """Load project digestions once without blocking the Assembly source picker."""
        project = self._workspace.project
        if (project is None or self._storedAssemblyDigestions is not None
                or self._loadingAssemblyDigestions):
            return
        self._loadingAssemblyDigestions = True
        self._start(
            "assembly", _ASSEMBLY_READ_SLOT, project.listRestrictionRuns,
            self._acceptAssemblyDigestions, description="Reading stored digestions",
            affectsClose=False,
        )

    def _acceptAssemblyDigestions(self, runs: tuple[RestrictionDigest, ...]) -> None:
        self._storedAssemblyDigestions = runs
        self.assemblyDigestionsChanged.emit()

    def assemblyDigestions(self) -> tuple[RestrictionDigest, ...]:
        """Combine loaded project digestions with the exact runs retained this session."""
        runs = {
            run.operationId: run
            for run in (*(self._storedAssemblyDigestions or ()), *self._digestions.runs)
            if run.status == "completed"
        }
        return tuple(sorted(runs.values(), key=lambda run: run.createdAt, reverse=True))

    def _listRuns(self, revisionId: str | None) -> tuple[RestrictionDigest, ...]:
        project = self._workspace.project
        try:
            prior = () if project is None else project.listRestrictionRuns(revisionId)
        except _ERRORS as error:
            self._controller.message.emit(str(error))
            prior = ()
        current = (
            value for value in self._digestions.runs
            if revisionId is None or value.template.revisionId == revisionId
        )
        runs = {value.operationId: value for value in (*prior, *current)}
        return tuple(sorted(runs.values(), key=lambda item: item.createdAt, reverse=True))

    def reopenDigestion(self, operationId: str) -> bool:
        """Open the recorded run by ID without creating a new operation."""
        return self._controller._tryOperation(
            lambda: self.digestionOpened.emit(self._getDigestion(operationId)),
        )

    def loadDigestion(self, path: str | Path) -> bool:
        """Read and validate a portable file before accepting its values into the workspace."""
        project = self._workspace.project
        documents = self._workspace.documents
        sources = (*[item.revision for item in documents], *[lane.source for lane in self.lanes])
        persistedInputs = []
        for source in sources:
            evidence = self._workspace.annotationEvidence(source)
            persistedInputs.append(evidence.history[0] if evidence is not None else source)

        def calculate() -> tuple[SavedDigestion, tuple[AnnotationStep, ...]]:
            digestion = readDigestion(path)
            steps = project.annotationStepsFor(persistedInputs) if project is not None else ()
            return digestion, steps

        def publish(response: tuple[SavedDigestion, tuple[AnnotationStep, ...]]) -> None:
            digestion, persistedSteps = response
            documents = self._workspace.documents
            snapshots = (*[item.revision for item in documents], *[lane.source for lane in self.lanes])
            self._digestions.checkCompatible(
                digestion, snapshots,
                (item.capture for item in documents if item.capture is not None),
                (*persistedSteps, *(step for snapshot in snapshots
                                    for step in self._workspace.annotationSteps(snapshot))),
            )
            if project is not None:
                digestion = project.registerDigestion(digestion)
            self._digestions.retain(digestion)
            template = digestion.result.template
            if self._controller._changeWorkspace(lambda: self._workspace.openExactRevision(
                template.revisionId, retained=template,
            )):
                self.historyChanged.emit()
                self.digestionOpened.emit(self._getDigestion(digestion.result.operationId))

        self._controller._endImport()
        self._start(
            "digest", "open", calculate, publish,
            settings=(str(path),), bindActive=True,
            description=f"Opening saved digestion {Path(path).name}",
        )
        return True

    def cancelOpenDigestion(self) -> bool:
        canceled = self._workspace.tasks.cancel("digest", "open")
        self._syncPending()
        return canceled

    def updateDigestionView(self, digestion: SavedDigestion) -> bool:
        """Update one run's inspection view without changing the result or pinned lanes."""
        def update() -> None:
            operationId = digestion.result.operationId
            original = self._digestions.get(operationId) or self._getDigestion(operationId)
            view = digestion.view
            validateDigestionView(original.result, view)
            if operationId not in self._projectDigestIds:
                self._digestions.retain(replace(original, view=view))
                return
            project = self._workspace.project
            tabContext = self._workspace.taskContext(True)
            pending = self._pendingViews.get(operationId)
            if pending is None:
                pending = self._pendingViews[operationId] = _PendingView(original, view)
            pending.requested = view

            def complete(_value: object, error: str | None) -> None:
                if self._pendingViews.get(operationId) is not pending:
                    return
                if error is None:
                    pending.saved = replace(pending.saved, view=view)
                if pending.requested is view:
                    del self._pendingViews[operationId]
                    self._digestions.retain(pending.saved)
                    if error is not None:
                        if self._workspace.taskContext(True) == tabContext:
                            self._controller.message.emit(
                                f"Could not save digest preview settings for {original.result.template.name}. {error}"
                            )
                        self.viewSaveFailed.emit(operationId, view, pending.saved.view, error)

            self._controller.projectTasks.submit(
                lambda: project.saveRestrictionView(operationId, view, timeout=_PROJECT_TIMEOUT_SECONDS),
                complete, write=True,
            )

        return self._controller._tryOperation(update)

    def saveDigestion(
        self, digestion: SavedDigestion, destination: str | Path, *,
        confirm: Callable[[SavedDigestion], bool] | None = None,
    ) -> bool:
        """Save the recorded run and its current preview settings. Return whether the write succeeded."""
        try:
            path = Path(destination)
            self._controller.ensureFigureDestination(path)
            original = self._getDigestion(digestion.result.operationId)
            candidate = replace(original, view=digestion.view)
            if writeDigestion(candidate, path, confirm=confirm) is None:
                return False
        except _ERRORS as error:
            self._controller.operationFailed.emit(str(error))
            return False
        self._digestions.retain(candidate)
        self._controller.message.emit(f"Saved digestion to {path.name}.")
        return True

    def originalAvailable(self, digestion: SavedDigestion) -> bool:
        artifactIds = {source.artifact.artifactId for source in digestion.sourceArtifacts}
        if digestion.result.template.sourceArtifactId is not None:
            artifactIds.add(digestion.result.template.sourceArtifactId)
        project = self._workspace.project
        if project is not None:
            for artifactId in artifactIds:
                try:
                    if project.hasOriginalBytes(artifactId):
                        return True
                except _ERRORS:
                    continue
        return any(
            doc.capture is not None and doc.capture.source.artifactId in artifactIds
            for doc in self._workspace.documents
        )

    def _output(self, operationId: str, outputKey: str) -> tuple[SavedDigestion, DNAFragment]:
        digestion = self._getDigestion(operationId)
        for fragment in digestion.result.fragments:
            if fragment.outputKey == outputKey:
                return digestion, fragment
        raise KeyError(OutputReference(operationId, outputKey))

    def copyFragment(self, operationId: str, outputKey: str) -> bool:
        """Copy the recorded physical forward strand in its own 5′→3′ direction."""
        try:
            _, fragment = self._output(operationId, outputKey)
        except _ERRORS as error:
            self._controller.operationFailed.emit(str(error))
            return False
        text = fragment.forwardSequence
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        if clipboard.text() != text:
            self._controller.message.emit("Clipboard unavailable. Copy from the selectable preview.")
            return False
        self._controller.message.emit(f"Copied {len(text):,} nt from the physical forward strand.")
        return True

    def adoptFragment(self, operationId: str, outputKey: str) -> bool:
        """Create or reuse the one draft or named revision belonging to an output."""
        def adopt() -> None:
            digestion, fragment = self._output(operationId, outputKey)
            if not digestion.result.cuts:
                raise ValueError("An uncut digest cannot create a construct.")
            self._workspace.openRestrictionProduct(fragment.revision, digestion)

        accepted = self._controller._changeWorkspace(adopt)
        if accepted:
            self.constructOpened.emit()
        return accepted

    def openSource(self, revisionId: str) -> bool:
        """Open the exact pinned source, reusing an already open clean snapshot."""
        retained = self._digestions.snapshot(revisionId)
        if retained is None:
            retained = next((
                lane.source for lane in self.lanes if lane.source.revisionId == revisionId
            ), None)
        accepted = self._controller._changeWorkspace(
            lambda: self._workspace.openExactRevision(revisionId, retained=retained)
        )
        if accepted:
            self.sourceOpened.emit()
        return accepted

    @property
    def gelState(self) -> GelState:
        marker = self.resolveGelMarker(self._gelState.marker)
        if marker != self._gelState.marker:
            self._gelState = self._gelState.withMarker(marker)
        return self._gelState

    @property
    def lanes(self) -> tuple[GelLane, ...]:
        return self._gelState.lanes

    @property
    def preparingGelLaneIds(self) -> frozenset[str | None]:
        return self._pendingGel

    @property
    def canUndoLaneRemoval(self) -> bool:
        return self._removedLane is not None

    def _lane(self, laneId: str) -> GelLane:
        for lane in self.lanes:
            if lane.laneId == laneId:
                return lane
        raise KeyError(laneId)

    def setGelMarker(self, marker: GelMarker) -> bool:
        def change() -> None:
            state = self.gelState.withMarker(marker)
            self._gelHasConfiguration = True
            self._setGelState(state)

        return self._controller._tryOperation(change)

    def setGelSelection(self, selection: GelSelection | None) -> bool:
        return self._controller._tryOperation(
            lambda: self._setGelState(self.gelState.withSelection(selection)),
        )

    def _setGelState(self, state: GelState) -> None:
        if state != self._gelState:
            self._gelState = state
            self.gelChanged.emit()

    def pinDigestion(
        self, digestion: SavedDigestion, laneId: str | None = None, *, label: str | None = None,
    ) -> bool:
        return self._controller._tryOperation(lambda: self._pinDigest(
            replace(self._getDigestion(digestion.result.operationId), view=digestion.view),
            laneId, label,
        ))

    def pinRecordedDigestion(
        self, operationId: str, laneId: str | None = None, *, label: str | None = None,
    ) -> bool:
        return self._controller._tryOperation(
            lambda: self._pinDigest(self._getDigestion(operationId), laneId, label),
        )

    def _pinDigest(
        self, digestion: SavedDigestion, laneId: str | None, label: str | None,
        *, abortPending: bool = True,
    ) -> None:
        lane = digestLane(digestion.result.template, digestion.result, label=label)
        self._commitLane(lane, laneId, digestion.view, abortPending=abortPending)

    def pinPcrProduct(
        self, run: PcrRun, view: GelViewSettings, laneId: str | None = None,
        *, label: str | None = None,
    ) -> bool:
        """Pin one recorded PCR output and capture its independent preview settings."""
        return self._controller._tryOperation(lambda: self._pinPcr(
            self._controller.pcr.getRun(run.operationId), view, laneId, label,
        ))

    def pinRecordedPcrProduct(
        self, operationId: str, outputKey: str, laneId: str | None = None,
        *, label: str | None = None,
    ) -> bool:
        """Pin a keyed product chosen in the Gel lane picker."""
        def pin() -> None:
            run = self._controller.pcr.getRun(operationId)
            view = replace(
                self._controller.pcr.viewForRun(operationId), selectedOutputKey=outputKey,
            )
            self._pinPcr(run, view, laneId, label)

        return self._controller._tryOperation(pin)

    def _pinPcr(
        self, run: PcrRun, view: GelViewSettings, laneId: str | None, label: str | None,
    ) -> None:
        if view.selectedOutputKey is None:
            raise ValueError("Choose a PCR product before adding it to Gel.")
        lane = pcrLane(run, outputKey=view.selectedOutputKey, label=label)
        self._commitLane(lane, laneId, view)

    def pinUntreated(
        self, documentId: str, laneId: str | None = None, *, label: str | None = None,
    ) -> bool:
        def pin() -> None:
            document = self._workspace.getDocument(documentId)
            if document.isDraft:
                raise ValueError("Name and save the sample source before pinning it in Gel.")
            self._commitLane(untreatedLane(document.revision, label=label), laneId)

        return self._controller._tryOperation(pin)

    def prepareDigestLane(
        self, documentId: str, names: Iterable[str], laneId: str | None = None,
        *, label: str | None = None,
    ) -> bool:
        """Replace only this lane's pending treatment, retaining the last accepted sample."""
        def prepare() -> None:
            document = self._eligibleDocument(documentId)
            if laneId is not None:
                self._lane(laneId)
            recipe = self._normalizeNames(names)
            if not recipe:
                raise ValueError("Choose one or more digest enzymes for the sample treatment.")
            self._startLiveDigest(document, recipe, forGel=True, laneId=laneId, label=label)

        return self._controller._tryOperation(prepare)

    def cancelGelPreparation(self, laneId: str | None = None) -> None:
        if self._workspace.tasks.cancel("gel", laneId or _ADD_LANE_SLOT):
            self._syncPending()

    def _commitLane(
        self, lane: GelLane, laneId: str | None, view: GelViewSettings | None = None,
        *, abortPending: bool = True,
    ) -> None:
        state = self.gelState
        if laneId is None:
            if view is not None and not self._gelHasConfiguration:
                state = replace(state, marker=view.marker, settings=view.settings)
            state = state.appendLane(lane)
        else:
            state = state.replaceLane(laneId, lane)
        actualId = laneId or lane.laneId
        reference = None
        if view is not None and view.selectedOutputKey is not None:
            reference = OutputReference(lane.run.operationId, view.selectedOutputKey)
        state = state.withSelection(GelSelection(actualId, reference))
        if abortPending:
            self._workspace.tasks.cancel("gel", laneId or _ADD_LANE_SLOT)
        if view is not None:
            self._pinnedGelViews[actualId] = view
        else:
            self._pinnedGelViews.pop(actualId, None)
        self._gelHasConfiguration = True
        self._setGelState(state)
        self._syncPending()

    def removeLane(self, laneId: str) -> bool:
        def remove() -> None:
            lane = self._lane(laneId)
            state = self.gelState.removeLane(laneId)
            self._removedLane = (
                self.lanes.index(lane), lane, self._pinnedGelViews.pop(laneId, None),
            )
            self._workspace.tasks.cancel("gel", laneId)
            self._setGelState(state)
            self._syncPending()

        return self._controller._tryOperation(remove)

    def undoLaneRemoval(self) -> bool:
        if self._removedLane is None:
            return False
        index, lane, view = self._removedLane
        state = self.gelState.insertLane(min(index, len(self.lanes)), lane)
        self._removedLane = None
        if view is not None:
            self._pinnedGelViews[lane.laneId] = view
        self._setGelState(state)
        return True

    def duplicateLane(self, laneId: str) -> bool:
        def duplicate() -> None:
            lane = self._lane(laneId)
            copied = replace(lane, laneId=str(uuid4()))
            view = self._pinnedGelViews.get(laneId)
            if view is not None:
                self._pinnedGelViews[copied.laneId] = view
            self._setGelState(self.gelState.insertLane(self.lanes.index(lane) + 1, copied))

        return self._controller._tryOperation(duplicate)

    def moveLane(self, laneId: str, offset: int) -> bool:
        def move() -> None:
            lane = self._lane(laneId)
            lanes = list(self.lanes)
            old = lanes.index(lane)
            new = max(0, min(len(lanes) - 1, old + offset))
            lanes.insert(new, lanes.pop(old))
            self._setGelState(replace(self.gelState, lanes=tuple(lanes)))

        return self._controller._tryOperation(move)

    def openLaneSource(self, laneId: str) -> bool:
        try:
            revisionId = self._lane(laneId).source.revisionId
        except KeyError as error:
            self._controller.operationFailed.emit(str(error))
            return False
        return self.openSource(revisionId)

    def openLaneDigestion(self, laneId: str) -> bool:
        """Reopen a lane's captured digestion and its independent preview settings."""
        def reopen() -> None:
            run = self._lane(laneId).run
            if not isinstance(run, RestrictionDigest):
                raise ValueError("This lane contains no recorded digestion.")
            latest = self._getDigestion(run.operationId)
            self.digestionOpened.emit(replace(latest, view=self._pinnedView(laneId)))

        return self._controller._tryOperation(reopen)

    def _pinnedView(self, laneId: str) -> GelViewSettings:
        view = self._pinnedGelViews[laneId]
        selection = self.gelState.selection
        if selection is not None and selection.laneId == laneId and selection.outputReference:
            return replace(view, selectedOutputKey=selection.outputReference.outputKey)
        return view

    def openLanePcr(self, laneId: str) -> bool:
        """Reopen the recorded PCR run and select its pinned output."""
        try:
            run = self._lane(laneId).run
            if not isinstance(run, PcrRun):
                raise ValueError("This lane contains no recorded PCR product.")
            return self._controller.pcr.openRecordedRun(run.operationId, view=self._pinnedView(laneId))
        except _ERRORS as error:
            self._controller.operationFailed.emit(str(error))
            return False

    def openLanePcrProduct(self, laneId: str) -> bool:
        """Open the exact keyed PCR product pinned in this comparison lane."""
        try:
            lane = self._lane(laneId)
            if not isinstance(lane.run, PcrRun):
                raise ValueError("This lane contains no recorded PCR product.")
            product = lane.selectedProduct(self.gelState.selection)
            if product is None:
                raise ValueError("Choose a PCR product in this lane.")
            reference = product.outputReference
            return self._controller.pcr.adoptProduct(reference.operationId, reference.outputKey)
        except _ERRORS as error:
            self._controller.operationFailed.emit(str(error))
            return False

    def reset(self) -> None:
        """Drop unsaved workflow state when explicitly leaving the project."""
        self._digestions = self._workspace.digestions
        self._projectDigestIds.clear()
        self._pendingViews.clear()
        self._storedAssemblyDigestions = None
        self._loadingAssemblyDigestions = False
        self._acceptedScans.clear()
        self._uncommitted.clear()
        self._retrying.clear()
        self.retryChanged.emit()
        self._pinnedGelViews.clear()
        self._removedLane = None
        self._gelHasConfiguration = False
        self._syncPending()
        self.ladderCatalogueChanged.emit()
        self._setGelState(GelState(marker=self.resolveGelMarker(GelMarker(DEFAULT_LADDER_ID))))
