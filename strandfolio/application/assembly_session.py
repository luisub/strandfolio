"""Pin source revisions in the backbone and insert slots of an Assembly session."""

from dataclasses import InitVar, dataclass, fields, is_dataclass, replace
from itertools import pairwise
from typing import Literal
from uuid import uuid4

from strandfolio.application.pcr_session import AssemblyPcrInput
from strandfolio.domain.fragments import DNAFragment
from strandfolio.domain.primers import PrimerBinding
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.storage.revision_codec import sameRevisionSnapshot


AssemblyOrientation = Literal["forward", "reverse"]


def isAssemblySource(revision: SequenceRevision) -> bool:
    """A DNA revision can occupy a role if it has no declared circular topology."""
    return (isinstance(revision, SequenceRevision) and revision.moleculeType == "DNA"
            and revision.topology != "circular")


def _sameOutputEvidence(recorded: object, offered: object) -> bool:
    """Compare immutable output fields, preserving IDs and the types of coordinates and flags."""
    if recorded is offered:
        return True
    if type(recorded) is not type(offered):
        return False
    if isinstance(recorded, SequenceRevision):
        return sameRevisionSnapshot(recorded, offered)
    if isinstance(recorded, tuple):
        return len(recorded) == len(offered) and all(
            _sameOutputEvidence(a, b) for a, b in zip(recorded, offered)
        )
    if is_dataclass(recorded):
        return all(_sameOutputEvidence(getattr(recorded, field.name), getattr(offered, field.name))
                   for field in fields(recorded))
    return recorded == offered


@dataclass(frozen=True, slots=True)
class AssemblyDigestInput:
    """A recorded fragment and the restriction run that produced it."""

    run: RestrictionDigest
    fragment: DNAFragment

    @property
    def revision(self) -> SequenceRevision:
        return self.fragment.revision


@dataclass(frozen=True, slots=True)
class AssemblySource:
    """A pinned revision or a product from a recorded run.

    If recordedIndex is supplied, check the product at that index in the run.
    Otherwise find the product by its output key.
    """

    label: str
    value: SequenceRevision | AssemblyDigestInput | AssemblyPcrInput
    recordedIndex: InitVar[int | None] = None

    def __post_init__(self, recordedIndex: int | None) -> None:
        if not isAssemblySource(self.revision):
            raise ValueError("Choose a DNA input that is linear or has unspecified topology.")
        value = self.value
        if isinstance(value, SequenceRevision):
            return
        if isinstance(value, AssemblyDigestInput):
            offered, outputs = value.fragment, value.run.fragments
            parentIds = (offered.sourceRevisionId,)
        else:
            offered, outputs = value.product, value.run.products
            bindings = (offered.forwardBinding, offered.reverseBinding)
            if not all(isinstance(binding, PrimerBinding) for binding in bindings):
                raise ValueError("The selected output has incomplete preparation evidence.")
            parentIds = tuple(binding.templateRevisionId for binding in bindings)
        if value.run.status != "completed":
            raise ValueError("Choose an output from a completed preparation.")
        reference = self.outputReference
        if (reference is None or reference.operationId != value.run.operationId
                or offered.operationId != value.run.operationId or offered.outputKey != reference.outputKey
                or any(parentId != value.run.template.revisionId for parentId in parentIds)):
            raise ValueError("The selected output does not match its recorded preparation.")
        if recordedIndex is None:
            recorded = next((item for item in outputs if item.outputKey == offered.outputKey), None)
        elif 0 <= recordedIndex < len(outputs):
            recorded = outputs[recordedIndex]
        else:
            recorded = None
        if not _sameOutputEvidence(recorded, offered):
            raise ValueError("The selected output differs from the one stored in its preparation.")

    @property
    def revision(self) -> SequenceRevision:
        return self.value if isinstance(self.value, SequenceRevision) else self.value.revision

    @property
    def sourceRevision(self) -> SequenceRevision:
        """The original record pinned for this input, including the template of a recorded run."""
        if isinstance(self.value, SequenceRevision):
            return self.value
        return self.value.run.template

    @property
    def sourceRevisionId(self) -> str:
        return self.sourceRevision.revisionId

    @property
    def outputReference(self) -> OutputReference | None:
        return self.revision.outputReference


@dataclass(frozen=True, slots=True)
class AssemblyOccurrence:
    """An Assembly slot whose ID survives reordering and source changes."""

    occurrenceId: str
    source: AssemblySource | None = None
    orientation: AssemblyOrientation = "forward"


class AssemblySession:
    """Maintain one backbone and an ordered list of insert slots in memory."""

    def __init__(self) -> None:
        self._backbone = self._emptySlot()
        self._inserts = [self._emptySlot()]

    @staticmethod
    def _emptySlot() -> AssemblyOccurrence:
        return AssemblyOccurrence(uuid4().hex)

    @property
    def backbone(self) -> AssemblyOccurrence:
        return self._backbone

    @property
    def inserts(self) -> tuple[AssemblyOccurrence, ...]:
        return tuple(self._inserts)

    @property
    def occurrences(self) -> tuple[AssemblyOccurrence, ...]:
        return (self._backbone, *self._inserts)

    @property
    def plannedJunctions(self) -> tuple[tuple[AssemblyOccurrence, AssemblyOccurrence], ...]:
        """List adjacent slots in circular order, including empty slots and the last-to-first pair."""
        return tuple(pairwise((*self.occurrences, self.backbone)))

    def assign(self, source: AssemblySource, occurrenceId: str | None) -> AssemblyOccurrence:
        """Pin a source in an existing slot, or append a new insert when ID is absent."""
        if occurrenceId is None:
            occurrence = replace(self._emptySlot(), source=source)
            self._inserts.append(occurrence)
            return occurrence
        occurrence = AssemblyOccurrence(occurrenceId, source)
        self._replaceOccurrence(occurrence)
        return occurrence

    def reorder(self, occurrenceId: str, offset: int) -> None:
        """Move an insert relative to its current position."""
        index = self._insertIndex(occurrenceId)
        destination = index + offset
        if not 0 <= destination < len(self._inserts):
            raise ValueError("The destination must be within the current insert list.")
        self._inserts.insert(destination, self._inserts.pop(index))

    def flip(self, occurrenceId: str) -> None:
        """Reverse a pinned orientation choice while preserving its recorded input."""
        occurrence = next(
            (item for item in self.occurrences if item.occurrenceId == occurrenceId), None,
        )
        if occurrence is None or occurrence.source is None:
            raise ValueError("Choose a source before setting its orientation.")
        orientation = "reverse" if occurrence.orientation == "forward" else "forward"
        self._replaceOccurrence(replace(occurrence, orientation=orientation))

    def remove(self, occurrenceId: str) -> None:
        """Remove an insert or release the backbone pin."""
        if occurrenceId == self._backbone.occurrenceId:
            self._backbone = self._emptySlot()
            return
        self._inserts.pop(self._insertIndex(occurrenceId))
        if not self._inserts:
            self._inserts.append(self._emptySlot())

    def clear(self) -> None:
        """Release all pins and return to the two empty starting roles."""
        self._backbone = self._emptySlot()
        self._inserts = [self._emptySlot()]

    def _replaceOccurrence(self, occurrence: AssemblyOccurrence) -> None:
        if occurrence.occurrenceId == self._backbone.occurrenceId:
            self._backbone = occurrence
        else:
            self._inserts[self._insertIndex(occurrence.occurrenceId)] = occurrence

    def _insertIndex(self, occurrenceId: str) -> int:
        for index, item in enumerate(self._inserts):
            if item.occurrenceId == occurrenceId:
                return index
        raise ValueError("This Assembly insert slot is no longer available.")
