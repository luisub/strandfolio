"""Track pairwise alignments between open documents and keep selected results for later inspection."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

from strandfolio.application.tasks import TaskInput, TaskRegistry, TaskRequest
from strandfolio.domain.sequence import MoleculeType, SequenceRevision
from strandfolio.operations.alignment import (
    ALIGNMENT_ENGINE_VERSION,
    PROTEIN_SUBSTITUTION_MATRIX,
    AlignmentCanceled,
    AlignmentResult,
    AlignmentSettings,
    alignPair,
    alignmentFeatures,
    alignmentInput,
)


# Each completed pair retains its own column mappings as long as the query is checked.
MAX_ALIGNMENT_QUERIES = 8
_OWNER = "alignment"


@dataclass(frozen=True, slots=True)
class AlignmentSelection:
    """One selected source tab, whole record or feature, and orientation."""

    documentId: str
    featureId: str | None = None
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class RetainedAlignment:
    """A completed alignment and the reference and query revisions used to compute it."""

    comparisonId: str
    result: AlignmentResult
    referenceRevision: SequenceRevision
    queryRevision: SequenceRevision
    referenceName: str
    queryName: str


@dataclass(frozen=True, slots=True)
class AlignmentCloseEffect:
    """A live alignment that would be lost if its reference or query tab closed."""

    comparisonId: str
    description: str


@dataclass(frozen=True, slots=True)
class AlignmentDocument:
    """An eligible tab, its current revision, and its display name."""

    binding: TaskInput
    revision: SequenceRevision
    name: str


@dataclass(frozen=True, slots=True)
class AlignmentWork:
    """The snapshots and settings needed to calculate one queued alignment."""

    request: TaskRequest
    reference: AlignmentSelection
    query: AlignmentSelection
    referenceDocument: AlignmentDocument
    queryDocument: AlignmentDocument
    settings: AlignmentSettings

    def calculate(
        self, isCanceled: Callable[[], bool], align: Callable[..., AlignmentResult] = alignPair,
    ) -> AlignmentResult:
        """Resolve oriented inputs from the captured snapshots and align them."""
        reference = alignmentInput(
            self.referenceDocument.revision, self.reference.featureId, self.reference.reverse,
        )
        if isCanceled():
            raise AlignmentCanceled()
        query = alignmentInput(
            self.queryDocument.revision, self.query.featureId, self.query.reverse,
        )
        return align(reference, query, self.settings, isCanceled=isCanceled)

    def matches(self, result: AlignmentResult) -> bool:
        """Check the result's settings, engine version, and inputs against the request."""
        isProtein = self.referenceDocument.revision.moleculeType == "protein"
        return (
            result.settings == self.settings
            and (result.engineVersion,) == self.request.versions
            and result.substitutionMatrix == (PROTEIN_SUBSTITUTION_MATRIX if isProtein else None)
            and all(
                (actual.recordId, actual.revisionId, actual.selection.featureId,
                 actual.reverseComplement, actual.moleculeType)
                == (expected.revision.recordId, expected.revision.revisionId, selection.featureId,
                    selection.reverse, expected.revision.moleculeType)
                for actual, expected, selection in (
                    (result.reference, self.referenceDocument, self.reference),
                    (result.query, self.queryDocument, self.query),
                )
            )
        )


@dataclass(slots=True)
class _LivePair:
    selection: AlignmentSelection
    work: AlignmentWork | None = None
    comparison: RetainedAlignment | None = None
    error: str = ""


class AlignmentSession:
    """Track one live alignment per query tab, in workspace tab order."""

    def __init__(self, tasks: TaskRegistry) -> None:
        self._tasks = tasks
        self._documents: Mapping[str, AlignmentDocument] = {}
        self._initialized = False
        self._reference: AlignmentSelection | None = None
        self._pairs: dict[str, _LivePair] = {}
        self._settings = AlignmentSettings()
        self._activeQueryId: str | None = None
        self._retained: dict[str, RetainedAlignment] = {}
        self._activeRetainedId: str | None = None
        self._lostRegions: dict[str, str] = {}

    def synchronize(self, documents: Mapping[str, AlignmentDocument]) -> None:
        """Discard only the work and live results whose source binding changed."""
        previous, self._documents = self._documents, documents
        if self._reference is not None:
            documentId = self._reference.documentId
            if documentId not in documents:
                self.setReference(None)
            elif previous[documentId].binding != documents[documentId].binding:
                self._reference = self._availableRegion(self._reference, previous[documentId].revision)
                self._clearPairs()
        for documentId, pair in tuple(self._pairs.items()):
            if documentId not in documents or not self._compatibleQuery(documentId):
                self._clearPair(pair)
                del self._pairs[documentId]
                self._lostRegions.pop(documentId, None)
            elif previous[documentId].binding != documents[documentId].binding:
                self._clearPair(pair)
                pair.selection = self._availableRegion(pair.selection, previous[documentId].revision)
            elif pair.work is not None and not self._tasks.isCurrent(pair.work.request):
                self._clearPair(pair)
        self._selectFirstAvailable()

    def initialize(self, activeDocumentId: str | None) -> None:
        """Prefer the active tab as reference and preselect a query only if one is eligible."""
        if self._initialized or not self._documents:
            return
        self._initialized = True
        referenceId = (activeDocumentId if activeDocumentId in self._documents
                       else next(iter(self._documents)))
        self.setReference(AlignmentSelection(referenceId))
        compatible = [documentId for documentId in self._documents if self._compatibleQuery(documentId)]
        if len(compatible) == 1:
            self.setQuery(compatible[0], True)

    @property
    def reference(self) -> AlignmentSelection | None:
        return self._reference

    @property
    def referenceMoleculeType(self) -> MoleculeType | None:
        if self._reference is None:
            return None
        return self._documents[self._reference.documentId].revision.moleculeType

    def _compatibleQuery(self, documentId: str) -> bool:
        return self._reference is not None and (
            documentId != self._reference.documentId
            and self._documents[documentId].revision.moleculeType == self.referenceMoleculeType
        )

    @property
    def queries(self) -> tuple[AlignmentSelection, ...]:
        return tuple(pair.selection for pair in self._orderedPairs())

    @property
    def settings(self) -> AlignmentSettings:
        return self._settings

    @property
    def activeQueryId(self) -> str | None:
        return self._activeQueryId

    @property
    def activeResult(self) -> AlignmentResult | None:
        retained = self.retainedComparison
        return retained.result if retained is not None else self.resultFor(self._activeQueryId)

    @property
    def pendingQueryIds(self) -> frozenset[str]:
        return frozenset(
            documentId for documentId, pair in self._pairs.items() if pair.work is not None
        )

    @property
    def nextWork(self) -> AlignmentWork | None:
        return next((pair.work for pair in self._orderedPairs() if pair.work is not None), None)

    @property
    def canRun(self) -> bool:
        return self._reference is not None and bool(self._pairs) and not self.pendingQueryIds

    @property
    def retainedComparisons(self) -> tuple[RetainedAlignment, ...]:
        return tuple(self._retained.values())

    @property
    def retainedComparison(self) -> RetainedAlignment | None:
        return self._retained.get(self._activeRetainedId)

    def resultFor(self, queryId: str | None) -> AlignmentResult | None:
        pair = self._pairs.get(queryId)
        return pair.comparison.result if pair is not None and pair.comparison is not None else None

    def errorFor(self, queryId: str) -> str:
        pair = self._pairs.get(queryId)
        return pair.error if pair is not None else ""

    def lostRegion(self, documentId: str) -> str | None:
        """Return the name of a region replaced by the whole record after a revision change."""
        return self._lostRegions.get(documentId)

    def setReference(self, selection: AlignmentSelection | None) -> bool:
        """Set the reference and clear all live results and pending comparisons when it changes."""
        if selection == self._reference:
            return selection is not None and self._lostRegions.pop(selection.documentId, None) is not None
        if selection is not None:
            self._validateSelection(selection)
        if self._reference is not None:
            self._lostRegions.pop(self._reference.documentId, None)
        self._initialized = True
        self._reference = selection
        self._clearPairs()
        for documentId in tuple(self._pairs):
            if not self._compatibleQuery(documentId):
                del self._pairs[documentId]
                self._lostRegions.pop(documentId, None)
        return True

    def setQuery(self, documentId: str, checked: bool) -> bool:
        """Add an eligible whole-record query or remove only that query's live pair."""
        if checked == (documentId in self._pairs):
            return False
        if checked:
            selection = AlignmentSelection(documentId)
            self._validateSelection(selection)
            if not self._compatibleQuery(documentId):
                raise ValueError("Choose a reference and a different query of the same molecule type.")
            if len(self._pairs) >= MAX_ALIGNMENT_QUERIES:
                raise ValueError(f"Choose up to {MAX_ALIGNMENT_QUERIES} queries per alignment run.")
            self._pairs[documentId] = _LivePair(selection)
        else:
            self._clearPair(self._pairs.pop(documentId))
            self._lostRegions.pop(documentId, None)
            self._selectFirstAvailable()
        return True

    def setQueryRegion(self, selection: AlignmentSelection) -> bool:
        """Change a query's region or orientation and clear its pending work and live result."""
        pair = self._pairs.get(selection.documentId)
        if pair is None:
            return False
        if pair.selection == selection:
            return self._lostRegions.pop(selection.documentId, None) is not None
        self._validateSelection(selection)
        self._lostRegions.pop(selection.documentId, None)
        self._clearPair(pair)
        pair.selection = selection
        self._selectFirstAvailable()
        return True

    def setSettings(self, settings: AlignmentSettings) -> bool:
        """Set new scoring settings and clear pending comparisons and live results."""
        if settings == self._settings:
            return False
        self._settings = settings
        self._clearPairs()
        return True

    def selectResult(self, queryId: str) -> bool:
        """Inspect a completed live result."""
        if self.resultFor(queryId) is None:
            return False
        self._activeQueryId, self._activeRetainedId = queryId, None
        return True

    def retainResult(self, queryId: str | None = None) -> RetainedAlignment | None:
        """Keep a live result independently of later changes to its source tabs."""
        pair = self._pairs.get(queryId if queryId is not None else self._activeQueryId)
        if pair is None or pair.comparison is None:
            return None
        comparison = pair.comparison
        self._retained[comparison.comparisonId] = comparison
        return comparison

    def selectRetained(self, comparisonId: str | None) -> bool:
        """Inspect a kept result or return to the currently selected live pair."""
        if comparisonId is not None and comparisonId not in self._retained:
            return False
        self._activeRetainedId = comparisonId
        return True

    def beginComparisons(self) -> bool:
        """Queue unfinished comparisons. If all are complete, queue them again."""
        reference = self._reference
        if reference is None or not self.canRun:
            return False
        self._activeRetainedId = None
        referenceDocument = self._documents[reference.documentId]
        pairs = self._orderedPairs()
        missing = [pair for pair in pairs if pair.comparison is None]
        for pair in missing or pairs:
            query = pair.selection
            queryDocument = self._documents[query.documentId]
            request = self._tasks.begin(
                _OWNER, query.documentId, inputs=(
                    replace(referenceDocument.binding, role="reference"),
                    replace(queryDocument.binding, role="query"),
                ),
                settings=(reference, query, self._settings), versions=(ALIGNMENT_ENGINE_VERSION,),
                description=f"Align {queryDocument.name} to {referenceDocument.name}",
            )
            pair.work = AlignmentWork(
                request, reference, query, referenceDocument, queryDocument, self._settings,
            )
            pair.comparison, pair.error = None, ""
        self._selectFirstAvailable()
        return True

    def finish(
        self, work: AlignmentWork, result: AlignmentResult | None, error: str | None,
    ) -> bool:
        """Record the result or error for one query if its request is still current."""
        pair = self._pairs.get(work.query.documentId)
        if pair is None or pair.work is not work:
            return False
        pair.work = None
        if not self._tasks.finish(work.request):
            return False
        if result is None:
            pair.error = error or "Alignment did not produce a result."
        elif not isinstance(result, AlignmentResult) or not work.matches(result):
            pair.error = "Alignment returned a result for different inputs."
        else:
            pair.comparison = RetainedAlignment(
                str(uuid4()), result, work.referenceDocument.revision, work.queryDocument.revision,
                work.referenceDocument.name, work.queryDocument.name,
            )
        self._selectFirstAvailable()
        return True

    def cancelPending(self) -> bool:
        """Cancel pending comparisons and leave completed results available."""
        pending = self.pendingQueryIds
        for documentId in pending:
            pair = self._pairs[documentId]
            self._clearPair(pair)
            pair.error = "Alignment canceled."
        return bool(pending)

    def closeEffects(self, documentIds: frozenset[str]) -> tuple[AlignmentCloseEffect, ...]:
        """List the results that closing these tabs would discard."""
        reference = self._reference
        if reference is None:
            return ()
        affected = []
        referenceCloses = reference.documentId in documentIds
        for pair in self._orderedPairs():
            if not referenceCloses and pair.selection.documentId not in documentIds:
                continue
            comparison = pair.comparison
            if comparison is not None and comparison.comparisonId not in self._retained:
                affected.append(AlignmentCloseEffect(
                    comparison.comparisonId, f"{comparison.queryName} to {comparison.referenceName}",
                ))
        return tuple(affected)

    def _orderedPairs(self) -> tuple[_LivePair, ...]:
        return tuple(
            self._pairs[documentId] for documentId in self._documents if documentId in self._pairs
        )

    def _selectFirstAvailable(self) -> None:
        if self.resultFor(self._activeQueryId) is None:
            completed = (pair for pair in self._orderedPairs() if pair.comparison is not None)
            self._activeQueryId = next((pair.selection.documentId for pair in completed), None)

    def _clearPair(self, pair: _LivePair) -> None:
        if pair.work is not None:
            self._tasks.cancel(_OWNER, pair.work.request.slot)
        pair.work, pair.comparison, pair.error = None, None, ""

    def _clearPairs(self) -> None:
        for pair in self._pairs.values():
            self._clearPair(pair)
        self._activeQueryId = None

    def _availableRegion(
        self, selection: AlignmentSelection, previous: SequenceRevision,
    ) -> AlignmentSelection:
        if selection.featureId is not None and not self._hasFeature(selection):
            self._lostRegions[selection.documentId] = previous.getFeature(selection.featureId).label
            return AlignmentSelection(selection.documentId)
        if self._documents[selection.documentId].revision.moleculeType != "protein":
            return selection
        return AlignmentSelection(selection.documentId)

    def _hasFeature(self, selection: AlignmentSelection) -> bool:
        document = self._documents[selection.documentId]
        return any(
            feature.featureId == selection.featureId for feature in alignmentFeatures(document.revision)
        )

    def _validateSelection(self, selection: AlignmentSelection) -> None:
        if selection.documentId not in self._documents:
            raise ValueError("Choose an open sequence record for alignment.")
        if selection.reverse and self._documents[selection.documentId].revision.moleculeType == "protein":
            raise ValueError("Reverse complement is available for DNA and RNA.")
        if selection.featureId is not None and not self._hasFeature(selection):
            raise ValueError("Choose an exact supported feature from that record.")
