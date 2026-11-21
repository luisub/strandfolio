"""Ordered documents, record preferences and verified save/close actions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5
from weakref import WeakKeyDictionary

from strandfolio.application.alignment_session import (
    AlignmentCloseEffect,
    AlignmentDocument,
    AlignmentSession,
)
from strandfolio.application.digest_session import DigestionSession
from strandfolio.application.pcr_session import PcrSession, RetainedPcrRun, pcrOutput
from strandfolio.application.session import DocumentSession, DocumentViewState
from strandfolio.application.tasks import TaskContext, TaskInput, TaskRegistry, TaskRequest
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.digestion import SavedDigestion
from strandfolio.io.formats import SequenceImportResult
from strandfolio.operations.conversion import DEFAULT_GENETIC_CODE, GeneticCodePreference
from strandfolio.operations.gel import GelViewSettings
from strandfolio.operations.pcr import PcrRun
from strandfolio.storage.project_edits import AnnotationEvidence, AnnotationStep
from strandfolio.storage.projects import ProjectRepository
from strandfolio.storage.revision_codec import sameRevisionSnapshot

if TYPE_CHECKING:
    from strandfolio.operations.editing import AnnotationEdit
    from strandfolio.storage.project_pcr import CertifiedPcrRun


class StaleWorkspaceError(ValueError):
    """A document or pending request changed after a deferred action was prepared."""


@dataclass(slots=True, eq=False)
class WorkspaceDocument:
    """An open tab with its own revision, source capture, and display settings."""

    documentId: str
    session: DocumentSession
    originalName: str
    capture: SequenceImportResult | None = None
    hasUnsavedRoot: bool = False
    isIndependentIntake: bool = False
    viewState: DocumentViewState = field(default_factory=DocumentViewState)
    pendingDigestion: SavedDigestion | None = None
    pendingPcr: PcrRun | None = None

    @property
    def isDraft(self) -> bool:
        """Report whether the tab has a new record or applied edits that need saving."""
        return self.hasUnsavedRoot or self.session.hasEdits

    @property
    def adoptedTemplate(self) -> SequenceRevision | None:
        """Return the exact source of an adopted product draft."""
        if self.pendingDigestion is not None:
            return self.pendingDigestion.result.template
        if self.pendingPcr is not None:
            return self.pendingPcr.template
        return None

    @property
    def revision(self) -> SequenceRevision:
        revision = self.session.current
        if revision is None:
            raise ValueError("This document was closed.")
        return revision

    @property
    def sourceFilename(self) -> str | None:
        if self.capture is not None:
            return self.capture.source.filename
        path = self.revision.sourcePath
        return path.name if path is not None else None

    @property
    def disambiguator(self) -> str:
        """Identify the source and ordinal, or provide a short record ID."""
        filename = self.sourceFilename
        if filename is not None and self.revision.sourceOrdinal is not None:
            return f"{filename} #{self.revision.sourceOrdinal}"
        return self.revision.recordId[:8]


@dataclass(frozen=True, slots=True)
class DocumentTarget:
    """The tab, revision, and workspace versions captured for a deferred action."""

    documentId: str
    recordId: str
    revisionId: str
    sessionVersion: int
    isDraft: bool
    activeVersion: int
    projectVersion: int


@dataclass(frozen=True, slots=True)
class ClosePlan:
    """State to review before closing one or more documents."""

    documents: tuple[DocumentTarget, ...]
    pendingTasks: tuple[TaskRequest, ...]
    closeAll: bool
    workspaceId: str
    activeVersion: int
    projectVersion: int
    collectionVersion: int
    alignmentEffects: tuple[AlignmentCloseEffect, ...] = ()


@dataclass(frozen=True, slots=True)
class _RecordPreferences:
    preferredName: str | None = None
    geneticCode: GeneticCodePreference = DEFAULT_GENETIC_CODE


@dataclass(frozen=True, slots=True)
class _IntakeRebind:
    target: DocumentTarget
    revision: SequenceRevision
    preferences: _RecordPreferences
    isDraft: bool


@dataclass(frozen=True, slots=True)
class _AnnotationPredecessor:
    revision: SequenceRevision
    capture: SequenceImportResult | None = None
    independent: bool = False


@dataclass(frozen=True, slots=True)
class AnalysisEvidence:
    """Locally available analysis runs, ordered from the source capture to the selected product."""

    runs: tuple[PcrRun | SavedDigestion, ...] = ()
    capture: SequenceImportResult | None = None
    independent: bool = False
    certifications: tuple[CertifiedPcrRun, ...] = ()
    views: tuple[tuple[str, GelViewSettings], ...] = ()
    annotations: tuple[AnnotationEvidence, ...] = ()


class Workspace:
    """Track open tabs, the active tab, and background requests."""

    def __init__(self, project: ProjectRepository | None = None) -> None:
        self._workspaceId = str(uuid4())
        self._documents: dict[str, WorkspaceDocument] = {}
        self._activeDocumentId: str | None = None
        self._activeVersion = 0
        self._collectionVersion = 0
        self._project = project
        self._projectVersion = 0
        self._projectRecordIds: set[str] = set()
        self._preferredNames: dict[str, str] = {}
        self._localPreferredNames: dict[str, str] = {}
        self._geneticCodePreferences: dict[str, GeneticCodePreference] = {}
        self._localGeneticCodePreferences: dict[str, GeneticCodePreference] = {}
        self._annotationPredecessors: WeakKeyDictionary[SequenceRevision, _AnnotationPredecessor] = (
            WeakKeyDictionary()
        )
        self.digestions = DigestionSession()
        self.pcrRuns = PcrSession()
        self.tasks = TaskRegistry(self.taskContext, self._taskInputIsCurrent)
        self.alignment = AlignmentSession(self.tasks)

    @property
    def project(self) -> ProjectRepository | None:
        return self._project

    def setProject(self, project: ProjectRepository | None) -> None:
        """Switch projects once all open tabs have been closed."""
        if self._documents:
            raise ValueError("Close the open documents before switching projects.")
        self._project = project
        self._projectVersion += 1
        self._projectRecordIds.clear()
        self._preferredNames.clear()
        self._localPreferredNames.clear()
        self._geneticCodePreferences.clear()
        self._localGeneticCodePreferences.clear()
        self._annotationPredecessors.clear()
        self.digestions = DigestionSession()
        self.pcrRuns = PcrSession()
        self.alignment.cancelPending()
        self.alignment = AlignmentSession(self.tasks)

    @property
    def documents(self) -> tuple[WorkspaceDocument, ...]:
        return tuple(self._documents.values())

    @property
    def alignmentDocuments(self) -> tuple[WorkspaceDocument, ...]:
        """List open sequences with potentially selectable regions in tab order."""
        return tuple(
            document for document in self._documents.values()
            if document.session.current is not None
        )

    def synchronizeAlignment(self) -> None:
        """Update the open alignment inputs and discard live results that are out of date."""
        names = self.documentDisplayNames()
        available = {
            document.documentId: AlignmentDocument(
                self.taskInput("document", document.documentId), document.revision,
                " · ".join(filter(None, names[document.documentId])),
            )
            for document in self.alignmentDocuments
        }
        self.alignment.synchronize(available)

    @property
    def activeDocumentId(self) -> str | None:
        return self._activeDocumentId

    @property
    def activeDocument(self) -> WorkspaceDocument | None:
        if self._activeDocumentId is None:
            return None
        return self._documents[self._activeDocumentId]

    def getDocument(self, documentId: str) -> WorkspaceDocument:
        """Return the specifically targeted open tab, or raise KeyError."""
        return self._documents[documentId]

    def activate(self, documentId: str) -> bool:
        """Activate an open tab and report whether its identity changed."""
        self.getDocument(documentId)
        return self._setActive(documentId)

    def _setActive(self, documentId: str | None) -> bool:
        if documentId == self._activeDocumentId:
            return False
        self._activeDocumentId = documentId
        self._activeVersion += 1
        return True

    def targetFor(self, documentId: str) -> DocumentTarget:
        """Bind a deferred action to the current tab, revision and focus context."""
        document = self.getDocument(documentId)
        revision = document.revision
        return DocumentTarget(
            documentId, revision.recordId, revision.revisionId, document.session.revisionVersion,
            document.isDraft, self._activeVersion, self._projectVersion,
        )

    def targetIsCurrent(self, target: DocumentTarget) -> bool:
        """Reject superseded targets, including changes away from and back to a tab."""
        return (target.documentId in self._documents
                and self._documents[target.documentId].session.current is not None
                and self.targetFor(target.documentId) == target)

    def _editDocument(self, target: DocumentTarget) -> WorkspaceDocument:
        if not self.targetIsCurrent(target):
            raise StaleWorkspaceError("The sequence changed before the annotation action.")
        return self.getDocument(target.documentId)

    def applyAnnotationEdit(
        self, target: DocumentTarget, edit: Callable[[SequenceRevision, str], AnnotationEdit],
    ) -> AnnotationEdit:
        """Apply the annotation edit if the target is current and link it to its previous revision."""
        document = self._editDocument(target)
        previous = document.revision
        result = document.session.applyAnnotationEdit(edit, hasUnsavedRoot=document.hasUnsavedRoot)
        if result.revision is not previous:
            capture = (
                self._exactCapture(document.capture, previous)
                if previous is document.session.baseRevision else None
            )
            self._annotationPredecessors[result.revision] = _AnnotationPredecessor(
                previous, capture, capture is not None and document.isIndependentIntake,
            )
            self.synchronizeAlignment()
        return result

    def undoAnnotationEdit(self, target: DocumentTarget) -> bool:
        """Switch the targeted tab to its previous annotation snapshot."""
        changed = self._editDocument(target).session.undoAnnotationEdit()
        if changed:
            self.synchronizeAlignment()
        return changed

    def redoAnnotationEdit(self, target: DocumentTarget) -> bool:
        """Switch the targeted tab to its next annotation snapshot."""
        changed = self._editDocument(target).session.redoAnnotationEdit()
        if changed:
            self.synchronizeAlignment()
        return changed

    def discardDraft(self, target: DocumentTarget) -> bool:
        """Restore an edited saved record, or close a confirmed unsaved root or product."""
        document = self._editDocument(target)
        if document.hasUnsavedRoot:
            self.closeDocuments(self.planClose((document.documentId,)))
            return True
        draftRecordId = document.session.annotationDraftRecordId
        changed = document.session.discardAnnotationEdits()
        if changed:
            self._localGeneticCodePreferences.pop(draftRecordId, None)
            self.synchronizeAlignment()
        return changed

    def annotationEvidence(self, revision: SequenceRevision) -> AnnotationEvidence | None:
        """Find the annotation edits leading to a revision used by pending or retained work."""
        step = self._annotationPredecessors.get(revision)
        if step is None:
            return None
        history = [revision]
        while step is not None:
            history.append(step.revision)
            capture, independent = step.capture, step.independent
            step = self._annotationPredecessors.get(step.revision)
        return AnnotationEvidence(tuple(reversed(history)), capture, independent)

    def annotationSteps(self, revision: SequenceRevision) -> tuple[AnnotationStep, ...]:
        """Return annotation edit links held in memory for this revision."""
        evidence = self.annotationEvidence(revision)
        if evidence is None:
            return ()
        return tuple(AnnotationStep(parent, child)
                     for parent, child in zip(evidence.history, evidence.history[1:]))

    @staticmethod
    def _exactCapture(
        capture: SequenceImportResult | None, revision: SequenceRevision,
    ) -> SequenceImportResult | None:
        ordinal = revision.sourceOrdinal
        if (
            capture is None or capture.source.artifactId != revision.sourceArtifactId
            or ordinal is None or ordinal > len(capture.records)
        ):
            return None
        original = capture.records[ordinal - 1]
        return capture if sameRevisionSnapshot(original, revision) else None

    def taskContext(self, bindActive: bool) -> TaskContext:
        """Return the project version and, if requested, the active tab ID and versions."""
        if not bindActive:
            return TaskContext(self._projectVersion)
        document = self.activeDocument
        return TaskContext(
            self._projectVersion, self._activeVersion, self._activeDocumentId,
            document.session.revisionVersion if document is not None else None,
        )

    def taskInput(self, role: str, documentId: str) -> TaskInput:
        """Capture the current revision of a tab for one task input role."""
        document = self.getDocument(documentId)
        revision = document.revision
        return TaskInput(
            role, documentId, revision.recordId, revision.revisionId,
            document.session.revisionVersion,
        )

    def _taskInputIsCurrent(self, item: TaskInput) -> bool:
        if item.documentId is None:
            return True
        document = self._documents.get(item.documentId)
        return (document is not None and document.session.current is not None
                and self.taskInput(item.role, item.documentId) == item)

    def hasOpenIntake(self, capture: SequenceImportResult) -> bool:
        """Check open sources in memory before showing a new batch review."""
        return self._findLiveIntake(capture) is not None

    def _findLiveIntake(self, capture: SequenceImportResult) -> WorkspaceDocument | None:
        if capture.sourceOrigin is None:
            return None
        sameSource = (
            document for document in self._documents.values()
            if document.capture is not None and self._sameCapture(document.capture, capture)
        )
        independent = None
        for document in sameSource:
            if not document.isIndependentIntake:
                return document
            independent = independent or document
        return independent

    @staticmethod
    def _sameCapture(left: SequenceImportResult, right: SequenceImportResult) -> bool:
        return (left.sourceOrigin, left.source.sha256, left.formatName, left.interpretation) == (
            right.sourceOrigin, right.source.sha256, right.formatName, right.interpretation,
        ) and tuple(record.contentDigest for record in left.records) == tuple(
            record.contentDigest for record in right.records
        )

    def openImport(
        self, capture: SequenceImportResult, *, requestId: str, independent: bool = False,
    ) -> tuple[WorkspaceDocument, ...]:
        """Open the accepted imported records in source order and activate the first tab."""
        if not isinstance(capture, SequenceImportResult) or not capture.records:
            raise ValueError("Open a validated capture with at least one record.")
        if len({record.revisionId for record in capture.records}) != len(capture.records):
            raise ValueError("Intake records must have separate revision IDs.")
        project = self._project
        known = None
        if not independent and project is not None:
            known = project.findMatchingIntake(capture)
        live = None if independent else self._findLiveIntake(capture)
        if project is not None and known is not None:
            stagedCapture = live.capture if live is not None and not live.isIndependentIntake else None
            stagedCapture = stagedCapture or known
            stagedCodes = self._intakeGeneticCodePreferences(stagedCapture)
            rebindings = ()
            if (live is not None and live.capture is not None and not live.isIndependentIntake
                    and live.capture.source.artifactId != known.source.artifactId):
                rebindings = self._prepareIntakeRebind(project, live.capture, known)
            prepared = self._prepareIntake(known, stored=True, known=True, rebindings=rebindings)
            if stagedCodes:
                project.setGeneticCodePreferences({
                    known.records[ordinal - 1].recordId: preference
                    for ordinal, preference in stagedCodes.items()
                })
            self._applyIntakeRebind(known, rebindings)
            accepted = known
        else:
            if live is not None and live.capture is not None:
                capture = live.capture
                independent = live.isIndependentIntake
            # Validate and stage the tabs before committing the intake.
            prepared = self._prepareIntake(
                capture, stored=project is not None, independent=independent,
            )
            accepted = capture
            stagedCodes = self._intakeGeneticCodePreferences(capture) if project is not None else {}
            if project is not None:
                accepted = project.acceptImport(
                    capture, requestId=requestId, independent=independent,
                    intakeGeneticCodePreferences=stagedCodes,
                )
                if accepted is not capture:
                    # A competing writer or a retried request may have supplied exact IDs.
                    prepared = self._prepareIntake(
                        accepted, stored=True, known=True, independent=independent,
                    )
        documents, preferences = prepared
        self._publish(documents, preferences)
        if stagedCodes:
            self._adoptIntakeGeneticCodePreferences(accepted, stagedCodes)
        return documents

    def _prepareIntake(
        self, capture: SequenceImportResult, *, stored: bool = False, known: bool = False,
        independent: bool = False,
        rebindings: tuple[_IntakeRebind, ...] = (),
    ) -> tuple[tuple[WorkspaceDocument, ...], dict[str, _RecordPreferences]]:
        documents = []
        preferences = {}
        rebound = {binding.revision.revisionId: binding.target.documentId for binding in rebindings}
        for revision in capture.records:
            existing = self._findIntakeDocument(revision.revisionId)
            if existing is None and revision.revisionId in rebound:
                documents.append(self.getDocument(rebound[revision.revisionId]))
                continue
            if stored and self._project is not None and (
                existing is None or revision.recordId not in self._projectRecordIds
            ):
                preferences[revision.recordId] = (
                    self._readRecordPreferences(self._project, revision.recordId)
                    if known else _RecordPreferences()
                )
            if existing is not None:
                documents.append(existing)
                continue
            isDraft = (not known or self._project is None
                       or self._project.getSavedRevision(revision.recordId) is None)
            documents.append(self._newDocument(revision, capture, isDraft, independent=independent))
        return tuple(documents), preferences

    def _findOpen(self, revisionId: str, *, includeDrafts: bool = False) -> WorkspaceDocument | None:
        return next((
            document for document in self._documents.values()
            if (includeDrafts or not document.isDraft) and document.revision.revisionId == revisionId
        ), None)

    def _findIntakeDocument(self, revisionId: str) -> WorkspaceDocument | None:
        existing = self._findOpen(revisionId, includeDrafts=True)
        if existing is not None:
            return existing
        return next((
            document for document in self._documents.values() if document.hasUnsavedRoot
            and document.session.baseRevision is not None
            and document.session.baseRevision.revisionId == revisionId
        ), None)

    @staticmethod
    def _newDocument(
        revision: SequenceRevision,
        capture: SequenceImportResult | None,
        isDraft: bool,
        originalName: str | None = None,
        *,
        independent: bool = False,
    ) -> WorkspaceDocument:
        original = revision
        if capture is not None and revision.sourceOrdinal is not None:
            original = capture.records[revision.sourceOrdinal - 1]
        if originalName is None:
            originalName = original.description if original.sourceFormat == "fasta" else original.name
        return WorkspaceDocument(
            str(uuid4()), DocumentSession(revision), originalName, capture, isDraft, independent,
        )

    def _publish(
        self, documents: tuple[WorkspaceDocument, ...], preferences: Mapping[str, _RecordPreferences],
    ) -> None:
        for recordId, values in preferences.items():
            self._storeRecordPreferences(recordId, values)
        for document in documents:
            if document.documentId not in self._documents:
                self._documents[document.documentId] = document
                self._collectionVersion += 1
        self.activate(documents[0].documentId)
        self.synchronizeAlignment()

    @staticmethod
    def _readRecordPreferences(project: ProjectRepository, recordId: str) -> _RecordPreferences:
        return _RecordPreferences(
            project.getPreferredName(recordId), project.getGeneticCodePreference(recordId),
        )

    def _storeRecordPreferences(self, recordId: str, preferences: _RecordPreferences) -> None:
        self._projectRecordIds.add(recordId)
        self._setName(self._preferredNames, recordId, preferences.preferredName)
        self._geneticCodePreferences[recordId] = preferences.geneticCode

    def refreshRecordPreferences(self, document: WorkspaceDocument) -> tuple[bool, bool]:
        """Refresh registered settings on focus, preserving any draft-local preferences."""
        if self._project is None:
            return False, False
        recordId = document.revision.recordId
        if recordId not in self._projectRecordIds:
            base = document.session.baseRevision
            if base is None or base.recordId not in self._projectRecordIds:
                return False, False
            recordId = base.recordId
        beforeName = self._preferredNames.get(recordId)
        beforeCode = self.geneticCodePreference(document)
        values = self._readRecordPreferences(self._project, recordId)
        self._storeRecordPreferences(recordId, values)
        return beforeName != values.preferredName, beforeCode != self.geneticCodePreference(document)

    def openRevision(
        self, revision: SequenceRevision, *, capture: SequenceImportResult | None = None,
        isDraft: bool = False,
    ) -> WorkspaceDocument:
        """Open an in-memory revision, reusing an existing exact clean tab when possible."""
        existing = None if isDraft else self._findOpen(revision.revisionId)
        document = existing or self._newDocument(revision, capture, isDraft)
        self._publish((document,), {})
        return document

    def openSavedRevision(self, revisionId: str) -> WorkspaceDocument:
        """Open a project revision and restore its original name and display settings."""
        if self._project is None:
            raise ValueError("Open a project before requesting a stored revision.")
        return self._openSnapshot(revisionId, useIntakeDraft=True)

    def openExactRevision(
        self, revisionId: str, *, retained: SequenceRevision | None = None,
    ) -> WorkspaceDocument:
        """Open a clean pinned snapshot without replacing an editable tab."""
        return self._openSnapshot(revisionId, retained=retained)

    def _openSnapshot(
        self, revisionId: str, *, useIntakeDraft: bool = False,
        retained: SequenceRevision | None = None,
    ) -> WorkspaceDocument:
        if retained is not None and retained.revisionId != revisionId:
            raise ValueError("The retained snapshot has the wrong revision ID.")
        existing = self._findOpen(revisionId)
        if existing is not None:
            if retained is not None and not sameRevisionSnapshot(existing.revision, retained):
                raise ValueError("The open revision conflicts with the recorded snapshot.")
            self.activate(existing.documentId)
            return existing
        project = self._project
        if project is None:
            if retained is None:
                raise KeyError(revisionId)
            return self._openRetainedSnapshot(retained)
        try:
            revision = project.getRevision(revisionId)
        except KeyError:
            if retained is None:
                raise
            return self._openRetainedSnapshot(retained)
        if retained is not None and not sameRevisionSnapshot(revision, retained):
            raise ValueError("The stored revision conflicts with the recorded snapshot.")
        capture = None
        originalName = project.getOriginalName(revision.recordId)
        isDraft = False
        if revision.sourceArtifactId is not None:
            try:
                capture = project.getIntake(revision.sourceArtifactId)
            except KeyError:
                # A portable digestion records a source descriptor without the original bytes.
                pass
            isDraft = (useIntakeDraft and capture is not None
                       and any(item.revisionId == revisionId for item in capture.records)
                       and project.getSavedRevision(revision.recordId) is None)
            if isDraft:
                existingDraft = self._findIntakeDocument(revisionId)
                if existingDraft is not None:
                    if existingDraft.revision.revisionId == revisionId:
                        self.activate(existingDraft.documentId)
                        return existingDraft
                    # The edited tab still owns the unnamed root and its first save.
                    # Open the requested original as a clean snapshot alongside it.
                    isDraft = False
        document = self._newDocument(revision, capture, isDraft, originalName if capture is None else None)
        self._publish((document,), {
            revision.recordId: self._readRecordPreferences(project, revision.recordId),
        })
        return document

    def _openRetainedSnapshot(self, revision: SequenceRevision) -> WorkspaceDocument:
        """Open a separate clean tab and reuse its original file capture if still available."""
        capture = None
        independent = False
        for source in self._documents.values():
            capture = self._exactCapture(source.capture, revision)
            if capture is not None:
                independent = source.isIndependentIntake
                break
        document = self._newDocument(revision, capture, False, independent=independent)
        self._publish((document,), {})
        return document

    def openRestrictionProduct(
        self, revision: SequenceRevision, digestion: SavedDigestion,
    ) -> WorkspaceDocument:
        """Open or reuse the draft or named record for one exact restriction output."""
        return self._openProduct(revision, digestion=digestion)

    def openPcrProduct(self, result: PcrRun, outputKey: str) -> WorkspaceDocument:
        """Open or reuse the draft or named record for one exact PCR output."""
        return self._openProduct(pcrOutput(result, outputKey).revision, pcr=result)

    def _openProduct(
        self, revision: SequenceRevision, *, digestion: SavedDigestion | None = None,
        pcr: PcrRun | None = None,
    ) -> WorkspaceDocument:
        reference = revision.outputReference
        if reference is None:
            raise ValueError("A product draft requires its generating output identity.")
        stored = self._project.getSavedRevision(revision.recordId) if self._project else None
        if stored is not None:
            if stored.outputReference != reference:
                origins = self._project.getAnnotationHistory(stored.revisionId)
                if not any(item.outputReference == reference for item in origins):
                    raise ValueError("The named revision has a different generating output.")
            return self.openExactRevision(stored.revisionId, retained=stored)
        for document in self._documents.values():
            base = document.session.baseRevision
            if (document.hasUnsavedRoot and base is not None
                    and base.outputReference == reference and base.recordId == revision.recordId):
                self.activate(document.documentId)
                return document
        document = self._newDocument(revision, None, True)
        document.pendingDigestion = digestion
        document.pendingPcr = pcr
        self._publish((document,), {})
        return document

    def analysisEvidence(
        self, revision: SequenceRevision, *, digestion: SavedDigestion | None = None,
        pcr: PcrRun | None = None,
    ) -> AnalysisEvidence:
        """Collect recorded analyses, annotation edits, and available source data for this revision."""
        reverseRuns: list[PcrRun | SavedDigestion] = []
        certifications: list[CertifiedPcrRun] = []
        views: list[tuple[str, GelViewSettings]] = []
        annotations: dict[str, AnnotationEvidence] = {}
        seen: set[OutputReference] = set()
        capture = None
        independent = False
        template = revision
        while True:
            annotated = self.annotationEvidence(template)
            source = annotated.history[0] if annotated is not None else template
            if annotated is not None:
                annotations[template.revisionId] = annotated
            reference = source.outputReference
            if reference is None:
                break
            if reference in seen:
                raise ValueError("An analysis source refers to a circular generating chain.")
            seen.add(reference)
            entry = self.pcrRuns.get(reference.operationId)
            if pcr is not None and entry is None and pcr.operationId == reference.operationId:
                entry = RetainedPcrRun(pcr)
            if entry is not None:
                result = entry.result
                pcrOutput(result, reference.outputKey)
                if entry.capture is not None:
                    capture, independent = entry.capture, entry.independent
                if entry.certification is not None:
                    certifications.append(entry.certification)
                view = self.pcrRuns.getView(result.operationId)
                if view is not None:
                    views.append((result.operationId, view))
                reverseRuns.append(result)
                template = result.template
                continue
            bundle = self.digestions.get(reference.operationId)
            if digestion is not None and digestion.result.operationId == reference.operationId:
                bundle = bundle or digestion
            if bundle is None:
                break
            reverseRuns.append(bundle)
            earliest = (bundle.ancestorRuns or (bundle.result,))[0]
            for run in (*bundle.ancestorRuns, bundle.result):
                annotated = self.annotationEvidence(run.template)
                if annotated is not None:
                    annotations[run.template.revisionId] = annotated
            template = earliest.template
        return AnalysisEvidence(
            tuple(reversed(reverseRuns)), capture, independent, tuple(certifications), tuple(views),
            tuple(annotations.values()),
        )

    def preferredName(self, document: WorkspaceDocument) -> str | None:
        """Return display metadata shared by every open revision of the record."""
        recordId = document.revision.recordId
        return self._localPreferredNames.get(recordId) or self._preferredNames.get(recordId)

    def displayName(self, document: WorkspaceDocument) -> str:
        """Resolve the full label used across workspace, map and figure output."""
        return self.preferredName(document) or document.revision.name

    def documentDisplayNames(self) -> dict[str, tuple[str, str]]:
        """Resolve each open document's display name and any needed disambiguator."""
        documents = tuple(doc for doc in self._documents.values() if doc.session.current is not None)
        names = tuple(self.displayName(doc) for doc in documents)
        nameCounts = Counter(names)
        sourceCounts = Counter((name, doc.disambiguator) for doc, name in zip(documents, names))
        labels = {}
        for document, name in zip(documents, names):
            source = ""
            if nameCounts[name] > 1:
                source = document.disambiguator
                if sourceCounts[name, source] > 1:
                    source += f" · {document.documentId[:8]}"
            labels[document.documentId] = (name, source)
        return labels

    @staticmethod
    def _setName(names: dict[str, str], recordId: str, name: str | None) -> None:
        if name:
            names[recordId] = name
        else:
            names.pop(recordId, None)

    def setPreferredName(self, target: DocumentTarget, name: str, *, expectedName: str | None = None) -> bool:
        """Update one record's display label without changing its revision or selection."""
        if not self.targetIsCurrent(target):
            raise StaleWorkspaceError("The sequence changed while editing its preferred name.")
        if not isinstance(name, str):
            raise TypeError("The preferred name must be text.")
        trimmed = name.strip() or None
        document = self.getDocument(target.documentId)
        if trimmed == self.preferredName(document):
            return False
        if self._project is not None and target.recordId in self._projectRecordIds:
            if expectedName is None or target.recordId in self._localPreferredNames:
                expectedName = self._preferredNames.get(target.recordId, "")
            self._project.setPreferredName(target.recordId, trimmed, expectedName=expectedName)
            self._setName(self._preferredNames, target.recordId, trimmed)
            self._localPreferredNames.pop(target.recordId, None)
        else:
            self._setName(self._localPreferredNames, target.recordId, trimmed)
        return True

    def geneticCodePreference(self, document: WorkspaceDocument) -> GeneticCodePreference:
        """Resolve local and stored preview settings for the current record or draft."""
        recordId = document.revision.recordId
        base = document.session.baseRevision
        if base is not None and base.recordId != recordId:
            inherited = self._localGeneticCodePreferences.get(
                base.recordId, self._geneticCodePreferences.get(base.recordId, DEFAULT_GENETIC_CODE),
            )
        else:
            inherited = DEFAULT_GENETIC_CODE
        return self._localGeneticCodePreferences.get(
            recordId, self._geneticCodePreferences.get(recordId, inherited),
        )

    def setGeneticCodePreference(
        self, target: DocumentTarget, preference: GeneticCodePreference,
    ) -> bool:
        """Change a nucleotide record's preview, persisting it if already registered."""
        if not self.targetIsCurrent(target):
            raise StaleWorkspaceError("The sequence changed while choosing a genetic code.")
        if not isinstance(preference, GeneticCodePreference):
            raise ValueError("Expected a genetic code preference.")
        document = self.getDocument(target.documentId)
        if document.revision.moleculeType == "protein":
            raise ValueError("Genetic codes apply to DNA and RNA records.")
        canPersist = self._project is not None and target.recordId in self._projectRecordIds
        if preference == self.geneticCodePreference(document) and (
            not canPersist or target.recordId not in self._localGeneticCodePreferences
        ):
            return False
        if canPersist:
            self._project.setGeneticCodePreference(
                target.recordId, preference,
                expected=self._geneticCodePreferences.get(target.recordId, DEFAULT_GENETIC_CODE),
            )
            self._geneticCodePreferences[target.recordId] = preference
            self._localGeneticCodePreferences.pop(target.recordId, None)
        else:
            self._localGeneticCodePreferences[target.recordId] = preference
        return True

    def _intakeGeneticCodePreferences(
        self, capture: SequenceImportResult | None,
    ) -> dict[int, GeneticCodePreference]:
        if capture is None:
            return {}
        return {
            ordinal: self._localGeneticCodePreferences[revision.recordId]
            for ordinal, revision in enumerate(capture.records, start=1)
            if revision.recordId in self._localGeneticCodePreferences
        }

    def _adoptIntakeGeneticCodePreferences(
        self, capture: SequenceImportResult, preferences: Mapping[int, GeneticCodePreference],
    ) -> None:
        self._projectRecordIds.update(revision.recordId for revision in capture.records)
        for ordinal, preference in preferences.items():
            recordId = capture.records[ordinal - 1].recordId
            self._geneticCodePreferences[recordId] = preference
            self._localGeneticCodePreferences.pop(recordId, None)

    def saveDocument(
        self, target: DocumentTarget, name: str, *, requestId: str | None = None,
        project: ProjectRepository | None = None,
    ) -> SequenceRevision:
        """Name a draft in place, attaching the chosen project after the first success."""
        return self._saveDocument(target, name, requestId, project)

    def openSavedCopy(
        self, project: ProjectRepository, capture: SequenceImportResult, saved: SequenceRevision,
    ) -> WorkspaceDocument:
        """Open a saved copy in a clean tab, or activate its tab if already open."""
        if self._project is not None and project is not self._project:
            raise StaleWorkspaceError("The destination project is no longer open.")
        if (len(capture.records) != 1 or saved.sourceArtifactId != capture.source.artifactId
                or saved.recordId != capture.records[0].recordId):
            raise ValueError("The saved copy does not belong to this source capture.")
        existing = self._findOpen(saved.revisionId)
        if existing is not None:
            self.activate(existing.documentId)
            return existing
        document = self._newDocument(saved, capture, False, independent=True)
        if self._project is None:
            self._project = project
            self._projectVersion += 1
        self._publish((document,), {saved.recordId: _RecordPreferences()})
        return document

    def _saveDocument(
        self, target: DocumentTarget, name: str, requestId: str | None,
        project: ProjectRepository | None, closePlan: ClosePlan | None = None,
    ) -> SequenceRevision:
        if not self.targetIsCurrent(target):
            raise StaleWorkspaceError("The sequence changed before it could be saved.")
        document = self.getDocument(target.documentId)
        if not document.isDraft:
            return document.revision
        if self._project is not None:
            if project is not None and project.path != self._project.path:
                raise ValueError("Save into the current project, or switch projects first.")
            project = self._project
        if project is None:
            raise ValueError("Choose a project before saving this sequence.")
        if requestId is None:
            requestKey = f"vibeGene:save:{target.documentId}:{target.revisionId}:{name}"
            requestId = str(uuid5(NAMESPACE_URL, requestKey))
        editEvidence = self.annotationEvidence(document.revision) if document.session.hasEdits else None
        base = document.session.baseRevision
        assert base is not None
        capture = self._exactCapture(document.capture, base)
        independent = document.isIndependentIntake and capture is not None
        preferredName = self._localPreferredNames.get(target.recordId)
        preference = {"preferredName": preferredName} if preferredName else {}
        geneticCode = self._localGeneticCodePreferences.get(target.recordId)
        if geneticCode is None and base.recordId != target.recordId:
            inheritedCode = self.geneticCodePreference(document)
            if inheritedCode != DEFAULT_GENETIC_CODE:
                geneticCode = inheritedCode
        registeredCapture = document.capture
        storedPreferences = _RecordPreferences(
            self._preferredNames.get(target.recordId),
            self._geneticCodePreferences.get(target.recordId, DEFAULT_GENETIC_CODE),
        )
        siblingBindings = ()
        if (editEvidence is None and capture is not None
                and not independent and target.recordId not in self._projectRecordIds):
            known = project.findMatchingIntake(capture)
            if known is not None:
                knownRecord = known.records[document.revision.sourceOrdinal - 1]
                storedPreferences = self._readRecordPreferences(project, knownRecord.recordId)
                if known.source.artifactId != capture.source.artifactId:
                    registeredCapture = known
                    siblingBindings = self._prepareIntakeRebind(
                        project, capture, known, exceptDocument=document,
                    )
        evidence = self.analysisEvidence(
            base, digestion=document.pendingDigestion, pcr=document.pendingPcr,
        )
        analysis = {}
        if any(isinstance(run, PcrRun) for run in evidence.runs):
            analysis = {
                "analysisRuns": evidence.runs[:-1] if document.pendingDigestion else evidence.runs,
                "analysisCapture": evidence.capture,
                "analysisIndependent": evidence.independent,
                "analysisCertifications": evidence.certifications,
                "analysisViews": evidence.views,
            }
        preferenceCapture = capture if editEvidence is None else editEvidence.capture
        if preferenceCapture is None and analysis:
            preferenceCapture = evidence.capture
        intakeGeneticCodes = self._intakeGeneticCodePreferences(preferenceCapture)
        saved = project.saveRevision(
            document.revision, name, requestId=requestId,
            capture=capture if editEvidence is None else None,
            independent=independent and editEvidence is None, digestion=document.pendingDigestion,
            annotationEvidence=editEvidence, analysisAnnotations=evidence.annotations,
            geneticCodePreference=geneticCode,
            intakeGeneticCodePreferences=intakeGeneticCodes,
            **preference, **analysis,
        )
        closeCurrent = closePlan is None or self.closeIsCurrent(closePlan)
        if not self.targetIsCurrent(target):
            raise StaleWorkspaceError("The save committed, but its original tab changed.")
        siblingBindings = tuple(
            binding for binding in siblingBindings if self.targetIsCurrent(binding.target)
        )
        if self._project is None:
            self._project = project
            self._projectVersion += 1
        if registeredCapture is not None and registeredCapture.source.artifactId == saved.sourceArtifactId:
            self._projectRecordIds.update(record.recordId for record in registeredCapture.records)
            self._applyIntakeRebind(registeredCapture, siblingBindings)
        else:
            registeredCapture = None
        document.capture = registeredCapture
        if capture is not None and preferenceCapture is capture:
            preferenceCapture = registeredCapture
        if preferenceCapture is not None:
            self._adoptIntakeGeneticCodePreferences(preferenceCapture, intakeGeneticCodes)
        self._storeRecordPreferences(saved.recordId, _RecordPreferences(
            preferredName or storedPreferences.preferredName, geneticCode or storedPreferences.geneticCode,
        ))
        self._localPreferredNames.pop(target.recordId, None)
        self._localGeneticCodePreferences.pop(target.recordId, None)
        document.session.adoptEquivalentRevision(saved)
        document.hasUnsavedRoot = False
        document.pendingDigestion = None
        document.pendingPcr = None
        savedRevisions = {target.revisionId, saved.revisionId}
        if registeredCapture is not None:
            savedRevisions.update(
                record.revisionId for record in registeredCapture.records if record.recordId == saved.recordId
            )
        for peer in self._documents.values():
            if peer.revision.revisionId in savedRevisions and not peer.session.hasEdits:
                peer.hasUnsavedRoot = False
        self.synchronizeAlignment()
        if not closeCurrent:
            raise StaleWorkspaceError("The save committed, but the close review changed.")
        return saved

    def _prepareIntakeRebind(
        self, project: ProjectRepository, capture: SequenceImportResult,
        registered: SequenceImportResult, *, exceptDocument: WorkspaceDocument | None = None,
    ) -> tuple[_IntakeRebind, ...]:
        """Stage draft tabs without edits or redo history for the registered intake."""
        originalRevisions = {revision.revisionId: index for index, revision in enumerate(capture.records)}
        bindings = []
        for document in self._documents.values():
            index = originalRevisions.get(document.revision.revisionId)
            if (
                document is exceptDocument or not document.hasUnsavedRoot or document.session.canRedo
                or index is None or document.capture is None
                or document.capture.source.artifactId != capture.source.artifactId
            ):
                continue
            revision = registered.records[index]
            existing = self._findOpen(revision.revisionId, includeDrafts=True)
            bindings.append(_IntakeRebind(
                self.targetFor(document.documentId), revision,
                self._readRecordPreferences(project, revision.recordId),
                existing is None and project.getSavedRevision(revision.recordId) is None,
            ))
        return tuple(bindings)

    def _applyIntakeRebind(
        self, capture: SequenceImportResult, bindings: tuple[_IntakeRebind, ...],
    ) -> None:
        for binding in bindings:
            document = self.getDocument(binding.target.documentId)
            recordId = binding.revision.recordId
            localPreference = self._localPreferredNames.pop(binding.target.recordId, None)
            if localPreference is not None:
                self._setName(self._localPreferredNames, recordId, localPreference)
            localCode = self._localGeneticCodePreferences.pop(binding.target.recordId, None)
            if localCode is not None:
                self._localGeneticCodePreferences[recordId] = localCode
            self._storeRecordPreferences(recordId, binding.preferences)
            document.capture = capture
            document.session.adoptEquivalentRevision(binding.revision)
            document.hasUnsavedRoot = binding.isDraft

    def planClose(self, documentIds: Iterable[str] | None = None) -> ClosePlan:
        """Capture the targeted tabs and live requests before asking for a decision."""
        self.synchronizeAlignment()
        closeAll = documentIds is None
        targets = (tuple(self._documents) if documentIds is None else tuple(dict.fromkeys(documentIds)))
        pendingTasks = self.tasks.pendingFor(None if closeAll else frozenset(targets))
        return ClosePlan(
            tuple(self.targetFor(documentId) for documentId in targets), pendingTasks, closeAll,
            self._workspaceId, self._activeVersion, self._projectVersion,
            self._collectionVersion if closeAll else 0,
            self.alignment.closeEffects(frozenset(targets)),
        )

    def closeIsCurrent(self, plan: ClosePlan) -> bool:
        """Check whether the tabs, pending tasks, and alignment results still match the close plan."""
        if plan.workspaceId != self._workspaceId or not all(
            self.targetIsCurrent(target) for target in plan.documents
        ):
            return False
        documentIds = None if plan.closeAll else (target.documentId for target in plan.documents)
        return self.planClose(documentIds) == plan

    def closeDocuments(
        self, plan: ClosePlan, *, saves: Mapping[str, str] | None = None,
        project: ProjectRepository | None = None,
        beforeRemove: Callable[[], None] | None = None,
    ) -> None:
        """Apply reviewed saves first, then close the targets and discard other drafts.

        A failed save leaves every tab open. Earlier committed saves remain clean.
        """
        if not self.closeIsCurrent(plan):
            raise StaleWorkspaceError("Open sequences or pending work changed during the close review.")
        saves = {} if saves is None else dict(saves)
        if saves.keys() - {target.documentId for target in plan.documents if target.isDraft}:
            raise ValueError("Only reviewed drafts can be saved during close.")
        documentIds = tuple(target.documentId for target in plan.documents)
        for documentId in documentIds:
            if documentId in saves:
                target = self.targetFor(documentId)
                if not target.isDraft:
                    raise StaleWorkspaceError("A reviewed draft now has an existing saved record.")
                self._saveDocument(target, saves[documentId], None, project, plan)
                plan = self.planClose(None if plan.closeAll else documentIds)
        if beforeRemove is not None and self.closeIsCurrent(plan):
            beforeRemove()
        if not self.closeIsCurrent(plan):
            raise StaleWorkspaceError("Open sequences or pending work changed before closing.")
        self._removeDocuments(frozenset(documentIds), plan.pendingTasks)

    def _removeDocuments(
        self, documentIds: frozenset[str], pendingTasks: tuple[TaskRequest, ...],
    ) -> None:
        for request in pendingTasks:
            self.tasks.cancel(request.owner, request.slot)
        if self._activeDocumentId in documentIds:
            before = tuple(self._documents)
            index = before.index(self._activeDocumentId)
            candidates = (*before[index + 1:], *reversed(before[:index]))
            self._setActive(next((item for item in candidates if item not in documentIds), None))
        for documentId in documentIds:
            document = self._documents.pop(documentId)
            recordId = document.revision.recordId
            base = document.session.baseRevision
            draftRecordId = document.session.annotationDraftRecordId
            if draftRecordId is not None and base is not None and draftRecordId != base.recordId:
                self._localGeneticCodePreferences.pop(draftRecordId, None)
            document.session.openRevision(None)
            document.viewState = DocumentViewState()
            document.capture = None
            document.pendingDigestion = None
            document.pendingPcr = None
            self._collectionVersion += 1
            if all(item.revision.recordId != recordId for item in self._documents.values()):
                self._localPreferredNames.pop(recordId, None)
                self._localGeneticCodePreferences.pop(recordId, None)
        self.synchronizeAlignment()
