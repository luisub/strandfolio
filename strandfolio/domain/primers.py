"""Immutable DNA oligos and exact template-bound footprints."""

from dataclasses import dataclass, field
from itertools import groupby
from typing import Literal
from uuid import uuid4

from strandfolio.domain.coordinates import SelectionSegment


PrimerStrand = Literal[1, -1]
_DNA_BASES = frozenset("ACGT")
MAX_OLIGO_BP = 4096


@dataclass(frozen=True, slots=True)
class PrimerEntry:
    """Review one existing oligo before assigning a primer revision to it."""

    role: str
    annealing: str
    tail: str
    modifications: tuple[str, ...] = ()

    @classmethod
    def fromRevision(cls, role: str, primer: "PrimerRevision") -> "PrimerEntry":
        return cls(role, primer.annealingSequence, primer.tailSequence, primer.modifications)

    def __post_init__(self) -> None:
        object.__setattr__(self, "annealing", self.annealing.strip().upper())
        object.__setattr__(self, "tail", self.tail.strip().upper())

    @property
    def sequence(self) -> str:
        """The complete oligo in its own 5′→3′ order."""
        return self.tail + self.annealing

    def toRevision(self, name: str, previous: "PrimerRevision | None" = None) -> "PrimerRevision":
        """Retain the immutable revision for an unchanged entry or create an edited revision."""
        sequence, tailLength = self.sequence, len(self.tail)
        if previous is None:
            return PrimerRevision(name, sequence, tailLength, self.modifications)
        if previous.modifications and self.modifications != previous.modifications:
            raise ValueError("Editing this primer requires preserving its declared modifications.")
        if (type(name) is str and previous.name == name.strip() and previous.sequence == sequence
                and previous.tailLength == tailLength and previous.modifications == self.modifications):
            return previous
        return PrimerRevision(name, sequence, tailLength, self.modifications, primerId=previous.primerId)

    @property
    def composition(self) -> str:
        """Describe base composition of the complete, valid oligo without an assay-risk cutoff."""
        if self.problem:
            return "Composition unavailable"
        bases = self.sequence
        gc = 100 * (bases.count("G") + bases.count("C")) / len(bases)
        longest = max(sum(1 for _ in group) for _, group in groupby(bases))
        return f"GC {gc:.1f}% · longest single-base run {longest} nt"

    @property
    def problem(self) -> str:
        """Identify the first field that cannot be represented by exact DNA PCR."""
        if self.modifications:
            return f"{self.role} primer has declared chemical modifications outside the DNA model."
        if len(self.annealing) + len(self.tail) > MAX_OLIGO_BP:
            return f"{self.role} primer exceeds {MAX_OLIGO_BP:,} nt including its 5′ tail."
        for label, text in (("annealing sequence", self.annealing), ("5′ tail", self.tail)):
            for position, symbol in enumerate(text, start=1):
                if symbol not in _DNA_BASES:
                    return (f"{self.role} {label} has an unsupported character at position {position:,}. "
                            "Exact PCR accepts only A, C, G and T.")
        if not self.annealing:
            return f"Enter the {self.role.lower()} primer's annealing sequence."
        return ""


@dataclass(frozen=True, slots=True)
class PrimerRevision:
    """A complete 5′→3′ oligo and its declared non-annealing 5′ prefix.

    Supply the same primer ID and leave the revision ID unset to represent a
    later revision of an oligo. Declared modifications are retained as labels.
    """

    name: str
    sequence: str
    tailLength: int = 0
    modifications: tuple[str, ...] = ()
    primerId: str = field(default_factory=lambda: str(uuid4()))
    revisionId: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value
               for value in (self.name, self.primerId, self.revisionId)):
            raise ValueError("A primer needs a nonblank name and immutable identities.")
        if type(self.sequence) is not str:
            raise ValueError("A primer sequence must be a DNA string in 5′→3′ order.")
        bases = self.sequence.strip().upper()
        if not 1 <= len(bases) <= MAX_OLIGO_BP or set(bases) - _DNA_BASES:
            raise ValueError(f"Enter 1–{MAX_OLIGO_BP} primer bases using only A, C, G and T.")
        if type(self.tailLength) is not int or not 0 <= self.tailLength < len(bases):
            raise ValueError("A 5′ tail must leave at least one 3′ annealing base.")
        if type(self.modifications) is not tuple or any(
            type(value) is not str or not value.strip() or "\x00" in value
            for value in self.modifications
        ):
            raise ValueError("Primer modifications must be an immutable tuple of names.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "sequence", bases)

    @property
    def annealingSequence(self) -> str:
        """The declared 3′ annealing segment in primer 5′→3′ order."""
        return self.sequence[self.tailLength:]

    @property
    def tailSequence(self) -> str:
        """The declared non-annealing prefix in primer 5′→3′ order."""
        return self.sequence[:self.tailLength]


@dataclass(frozen=True, slots=True)
class SavedPrimerPair:
    """A named library entry with ordered, immutable primer revisions."""

    name: str
    forward: PrimerRevision
    reverse: PrimerRevision
    pairId: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name.strip() or not self.name.strip().isprintable():
            raise ValueError("Give the primer pair a printable, nonblank name.")
        if type(self.pairId) is not str or not self.pairId.strip() or "\x00" in self.pairId:
            raise ValueError("A saved primer pair needs an identity.")
        if type(self.forward) is not PrimerRevision or type(self.reverse) is not PrimerRevision:
            raise ValueError("A saved pair requires both primer revisions.")
        if self.forward.revisionId == self.reverse.revisionId and self.forward != self.reverse:
            raise ValueError("The same primer revision has conflicting contents.")
        object.__setattr__(self, "name", self.name.strip())


@dataclass(frozen=True, slots=True)
class PrimerBinding:
    """An exact footprint on the template's stored forward reference.

    Half-open source segments follow the oligo's 5′→3′ path. Each interval
    uses increasing reference coordinates. Strand is the extension direction.
    """

    templateRevisionId: str
    primerRevisionId: str
    strand: PrimerStrand
    sourceSegments: tuple[SelectionSegment, ...]
    annealingSequence: str
    tailSequence: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value
               for value in (self.templateRevisionId, self.primerRevisionId)):
            raise ValueError("A binding must identify the template and primer revisions.")
        if type(self.strand) is not int or self.strand not in (1, -1):
            raise ValueError("A binding strand must be +1 or -1.")
        if type(self.sourceSegments) is not tuple or not 1 <= len(self.sourceSegments) <= 2:
            raise ValueError("A binding needs one or two ordered source segments.")
        for segment in self.sourceSegments:
            if (type(segment) is not tuple or len(segment) != 2
                    or any(type(value) is not int for value in segment)
                    or not 0 <= segment[0] < segment[1]):
                raise ValueError("A binding segment must be a nonempty reference interval.")
        if any(type(bases) is not str or set(bases) - _DNA_BASES
               for bases in (self.annealingSequence, self.tailSequence)):
            raise ValueError("Binding evidence must carry unambiguous DNA sequences.")
        if sum(end - start for start, end in self.sourceSegments) != len(self.annealingSequence):
            raise ValueError("The annealing sequence must cover the entire binding footprint.")

    @property
    def referenceStart(self) -> int:
        """First coordinate of the footprint when traversed in stored strand order."""
        return self.sourceSegments[0 if self.strand == 1 else -1][0]
