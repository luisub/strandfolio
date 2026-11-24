"""Revision, selection and presentation state shared by notebooks and Qt."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from strandfolio.application.map_features import LANDMARK_TYPES
from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.conversion import (
    CdsTranslation,
    DEFAULT_GENETIC_CODE,
    FrameTranslation,
    GeneticCodePreference,
    previewConversion,
    translateAllCds,
    translateFrame,
)
from strandfolio.operations.orfs import OrfOptions

if TYPE_CHECKING:
    from strandfolio.operations.editing import AnnotationEdit


@dataclass(slots=True)
class DocumentViewState:
    """Per-document presentation settings, independent of its revision."""

    sequenceTopBase: int = 0
    sequenceTopOffset: int = 0
    pcrTopBase: int = 0
    pcrTopOffset: int = 0
    pcrTarget: SequenceSelection | None = None
    mapZoom: float = 1.0
    mapPanX: float = 0.0
    mapPanY: float = 0.0
    mapRotationDeg: float = 0.0
    mapDenseLabels: bool = False
    mapFeatureTypes: frozenset[str] | None = LANDMARK_TYPES
    mapRepresentation: Literal["auto", "linear"] = "auto"
    featureSearch: str = ""
    featureKeyOnly: bool = False
    featurePage: int = 0
    featureScroll: int = 0
    expandedFeatureIds: set[str] = field(default_factory=set)
    digestEnzymes: tuple[str, ...] = ()
    restrictionOverlayNames: tuple[str, ...] | None = None
    translationFrame: int | None = None
    orfOptions: OrfOptions = OrfOptions()
    selectedDigestEnzyme: str | None = None


class DocumentSession:
    """Manage the current revision, its selection, and its annotation edit history."""

    def __init__(self, revision: SequenceRevision | None = None) -> None:
        self._current = None
        self._sequence = ""
        self._selection = None
        self._revisionVersion = 0
        self._history: list[tuple[SequenceRevision, SequenceSelection | None]] = []
        self._historyIndex = 0
        self._draftRecordId: str | None = None
        self._translationPreference = DEFAULT_GENETIC_CODE
        self._cdsTranslations: tuple[CdsTranslation, ...] | None = None
        self._frameTranslation: FrameTranslation | None = None
        self.openRevision(revision)

    @property
    def current(self) -> SequenceRevision | None:
        return self._current

    @property
    def revisionVersion(self) -> int:
        """Return the version counter, which advances even when undo restores an older revision."""
        return self._revisionVersion

    @property
    def sequence(self) -> str:
        """Return an immutable display projection captured once on opening."""
        return self._sequence

    @property
    def selection(self) -> SequenceSelection | None:
        return self._selection

    @property
    def selectedText(self) -> str:
        """Return original-case letters on the physical forward strand in selected part order."""
        if self._current is None:
            return ""
        return previewConversion(self._current, self._selection, "stored").text

    def cdsTranslationsFor(self, preference: GeneticCodePreference) -> tuple[CdsTranslation, ...]:
        """Return the mapped CDS interpretations shared by each view of this revision."""
        self._setTranslationPreference(preference)
        if self._current is None:
            return ()
        if self._cdsTranslations is None:
            self._cdsTranslations = translateAllCds(self._current, preference=preference)
        return self._cdsTranslations

    def frameTranslationFor(self, frame: int, preference: GeneticCodePreference) -> FrameTranslation:
        """Return a mapped free frame, retaining the most recent request for this revision."""
        self._setTranslationPreference(preference)
        if self._current is None:
            raise ValueError("Open a sequence before translating a frame.")
        if self._frameTranslation is None or self._frameTranslation.frame != frame:
            self._frameTranslation = translateFrame(self._current, frame, preference=preference)
        return self._frameTranslation

    def _setTranslationPreference(self, preference: GeneticCodePreference) -> None:
        if preference != self._translationPreference:
            self._translationPreference = preference
            self._cdsTranslations = None
            self._frameTranslation = None

    @property
    def annotationDraftRecordId(self) -> str | None:
        """Return the reserved draft ID, including during undo before a redo."""
        return self._draftRecordId

    @property
    def baseRevision(self) -> SequenceRevision | None:
        """Return the starting snapshot for a new draft or the current edit history."""
        return self._history[0][0] if self._history else None

    @property
    def hasEdits(self) -> bool:
        """Report whether the current revision has applied annotation edits."""
        return self._historyIndex > 0

    @property
    def canUndo(self) -> bool:
        return self.hasEdits

    @property
    def canRedo(self) -> bool:
        return self._historyIndex + 1 < len(self._history)

    @property
    def appliedEditHistory(self) -> tuple[SequenceRevision, ...]:
        """Return the base and applied snapshots, omitting any unapplied redo branch."""
        if not self.hasEdits:
            return ()
        return tuple(revision for revision, _ in self._history[:self._historyIndex + 1])

    def openRevision(self, revision: SequenceRevision | None) -> None:
        """Replace the snapshot and reset selection and history. None closes it."""
        if revision is not None and not isinstance(revision, SequenceRevision):
            raise TypeError("Expected a SequenceRevision or None.")
        self._setRevision(revision)
        self._history = [(revision, self._selection)] if revision is not None else []
        self._historyIndex = 0
        self._draftRecordId = None

    def _setRevision(self, revision: SequenceRevision | None) -> None:
        self._revisionVersion += 1
        self._current = revision
        self._sequence = revision.sequence if revision is not None else ""
        self._cdsTranslations = None
        self._frameTranslation = None
        self._selection = (
            SequenceSelection(revision.revisionId, 0, 0) if revision is not None else None
        )

    def adoptEquivalentRevision(self, revision: SequenceRevision) -> None:
        """Rebind selection when a named or registered copy has the same coordinates."""
        previous = self._current
        selection = self._selection
        if previous is revision and not self.hasEdits and not self.canRedo:
            return
        self.openRevision(revision)
        if (previous is None or selection is None or previous.sequence != revision.sequence
                or previous.topology != revision.topology):
            return
        featureId = selection.featureId
        if featureId is not None:
            for old, new in zip(previous.features, revision.features):
                if old.featureId == featureId:
                    if replace(old, featureId=new.featureId) != new:
                        return
                    featureId = new.featureId
                    break
            else:
                return
        self._selection = replace(selection, revisionId=revision.revisionId, featureId=featureId)

    def applyAnnotationEdit(
        self, edit: Callable[[SequenceRevision, str], "AnnotationEdit"], *, hasUnsavedRoot: bool,
    ) -> "AnnotationEdit":
        """Apply an annotation command and reserve the draft's record ID on its first edit."""
        previous = self._current
        if previous is None:
            raise ValueError("Open a sequence before editing annotations.")
        recordId = self._draftRecordId
        if recordId is None:
            recordId = previous.recordId if hasUnsavedRoot else str(uuid4())
        result = edit(previous, recordId)
        revision = result.revision
        if revision is previous:
            return result
        if (revision.recordId != recordId or revision.revisionId == previous.revisionId
                or revision.sequence != previous.sequence or revision.topology != previous.topology):
            raise ValueError("An annotation edit must return a new revision of the active draft.")
        self._draftRecordId = recordId
        selection = self._selection
        self._history[self._historyIndex] = (previous, selection)
        del self._history[self._historyIndex + 1:]
        self._setRevision(revision)
        if selection is not None:
            retainsReverseTraversal = selection.retainsReverseTraversal
            if selection.isCompound and selection.strand == -1:
                feature = previous.findFeature(selection.featureId)
                if feature is not None and not previous.hasUnverifiedFeatureTraversal(feature):
                    retainsReverseTraversal = True
            self._selection = replace(
                selection, revisionId=revision.revisionId, featureId=None, partIndex=None,
                wholeFeature=False, strand=None,
                retainsReverseTraversal=retainsReverseTraversal,
            )
            self._restoreFeatureSelection(selection.featureId, selection)
        if result.featureId is not None and (
            selection is None or result.featureId != selection.featureId
        ):
            self._restoreFeatureSelection(result.featureId)
        self._history.append((revision, self._selection))
        self._historyIndex += 1
        return result

    def _restoreFeatureSelection(
        self, featureId: str | None, previous: SequenceSelection | None = None,
    ) -> None:
        if featureId is None or self._current is None:
            return
        try:
            partIndex = previous.partIndex if previous is not None else None
            if partIndex is not None:
                feature = self._current.getFeature(featureId)
                matchingParts = [
                    index for index, part in enumerate(feature.parts)
                    if part.isSelectable and (part.start, part.end) == previous.segments[0]
                ]
                if partIndex not in matchingParts:
                    # Follow a surviving interval, or select the feature's new full location.
                    partIndex = next(iter(matchingParts), None)
            self.selectFeature(featureId, partIndex)
        except (KeyError, ValueError):
            # Removed and nonselectable source features leave the exact bases selected.
            pass

    def undoAnnotationEdit(self) -> bool:
        """Restore the previous immutable snapshot and its selection."""
        return self._moveEditHistory(self._historyIndex - 1) if self.canUndo else False

    def redoAnnotationEdit(self) -> bool:
        """Restore the next immutable snapshot from the current edit branch."""
        return self._moveEditHistory(self._historyIndex + 1) if self.canRedo else False

    def _moveEditHistory(self, index: int) -> bool:
        revision = self._current
        assert revision is not None
        self._history[self._historyIndex] = (revision, self._selection)
        self._historyIndex = index
        revision, selection = self._history[index]
        self._setRevision(revision)
        self._selection = selection
        return True

    def discardAnnotationEdits(self) -> bool:
        """Drop every edit and restore the base revision and its selection."""
        if not (self.hasEdits or self.canRedo):
            return False
        origin, selection = self._history[0]
        self.openRevision(origin)
        self._selection = selection
        return True

    def select(self, anchor: int, caret: int) -> bool:
        """Select exact contiguous boundaries, returning whether state changed.

        Raises:
            ValueError: No document is open or a boundary is outside [0, length].
        """
        if self._current is None:
            raise ValueError("Open a sequence before selecting bases.")
        selection = SequenceSelection(self._current.revisionId, anchor, caret)
        if selection.end > len(self._sequence):
            raise ValueError("Selection exceeds the sequence length.")
        changed = selection != self._selection
        self._selection = selection
        return changed

    def selectSegments(
        self,
        segments: Sequence[SelectionSegment],
        *,
        anchor: int | None = None,
        caret: int | None = None,
        featureId: str | None = None,
        partIndex: int | None = None,
        wholeFeature: bool = False,
        strand: int | None = None,
    ) -> bool:
        """Select explicit ordered intervals on the current revision without filling gaps."""
        if self._current is None:
            raise ValueError("Open a sequence before selecting bases.")
        segmentTuple = tuple(segments)
        if not segmentTuple:
            raise ValueError("At least one selection segment is required.")
        length = len(self._sequence)
        for segStart, segEnd in segmentTuple:
            if type(segStart) is not int or type(segEnd) is not int:
                raise ValueError("Selection segment boundaries must be integers.")
            if not 0 <= segStart <= segEnd <= length:
                raise ValueError("Selection exceeds the sequence length.")
        resolvedAnchor = segmentTuple[0][0] if anchor is None else anchor
        resolvedCaret = segmentTuple[-1][1] if caret is None else caret
        if (
            type(resolvedAnchor) is not int
            or type(resolvedCaret) is not int
            or not 0 <= resolvedAnchor <= length
            or not 0 <= resolvedCaret <= length
        ):
            raise ValueError("Selection boundaries must lie within the sequence.")
        selection = SequenceSelection(
            self._current.revisionId,
            resolvedAnchor,
            resolvedCaret,
            featureId=featureId,
            partIndex=partIndex,
            segments=segmentTuple,
            wholeFeature=wholeFeature,
            strand=strand,
        )
        changed = selection != self._selection
        self._selection = selection
        return changed

    def selectCircular(self, anchor: int, caret: int) -> bool:
        """Select forward along the molecule, wrapping across the origin when anchor > caret."""
        if self._current is None:
            raise ValueError("Open a sequence before selecting bases.")
        length = len(self._sequence)
        if (
            type(anchor) is not int
            or type(caret) is not int
            or not 0 <= anchor <= length
            or not 0 <= caret <= length
        ):
            raise ValueError("Selection boundaries must lie within the sequence.")
        if anchor <= caret:
            return self.select(anchor, caret)
        if self._current.topology != "circular":
            raise ValueError("Origin-wrapping selection requires circular topology.")
        if anchor == length and caret == 0:
            segments: tuple[SelectionSegment, ...] = ((0, 0),)
        else:
            parts: list[SelectionSegment] = []
            if anchor < length:
                parts.append((anchor, length))
            if caret > 0:
                parts.append((0, caret))
            segments = tuple(parts)
        return self.selectSegments(segments, anchor=anchor, caret=caret)

    def selectWholeFeature(self, featureId: str) -> bool:
        """Select every exact local segment of a feature in part order, excluding gaps."""
        if self._current is None:
            raise ValueError("Open a sequence before selecting a feature.")
        feature = self._current.getFeature(featureId)
        if not feature.parts or any(not part.isSelectable for part in feature.parts):
            raise ValueError(
                "This feature contains uncertain or remote segments and cannot be selected exactly."
            )
        segments = tuple((part.start, part.end) for part in feature.parts)
        if len(segments) == 1:
            return self.selectFeature(featureId, 0)
        return self.selectSegments(
            segments,
            anchor=segments[0][0],
            caret=segments[-1][1],
            featureId=featureId,
            partIndex=None,
            wholeFeature=True,
            strand=feature.strand,
        )

    def selectFeature(self, featureId: str, partIndex: int | None = 0) -> bool:
        """Select one exact local feature segment (or the whole feature when partIndex is None).

        Compound features are never flattened to their bounding range. Arbitrary
        select() clears this feature link.

        Raises:
            KeyError: The feature does not belong to the current snapshot.
            ValueError: No document is open, or the requested segment is unsupported.
        """
        if self._current is None:
            raise ValueError("Open a sequence before selecting a feature.")
        if partIndex is None:
            return self.selectWholeFeature(featureId)
        feature = self._current.getFeature(featureId)
        if type(partIndex) is not int or not 0 <= partIndex < len(feature.parts):
            raise ValueError("Choose an existing feature segment.")
        part = feature.parts[partIndex]
        if not part.isSelectable:
            raise ValueError("This feature segment has no exact local selectable interval.")
        selection = SequenceSelection(
            self._current.revisionId,
            part.start,
            part.end,
            featureId,
            partIndex,
            segments=((part.start, part.end),),
            wholeFeature=False,
            strand=part.strand,
        )
        changed = selection != self._selection
        self._selection = selection
        return changed
