"""Keep PCR runs in the workspace and make their products available in Assembly."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.primers import MAX_OLIGO_BP, PrimerEntry, PrimerStrand
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.formats import SequenceImportResult
from strandfolio.operations.conversion import extractSegmentsSequence, reverseComplementBases
from strandfolio.operations.gel import GelViewSettings
from strandfolio.operations.pcr import PcrProduct, PcrRun

if TYPE_CHECKING:
    from strandfolio.storage.project_pcr import CertifiedPcrRun


# Limit the stretch copied by a canvas action. Entered and saved oligos have their own limit.
PRIMER_SELECTION_WARNING_BP = 60
MAX_PRIMER_SELECTION_BP = 80


@dataclass(frozen=True, slots=True)
class SelectedPrimerIdea:
    """The explicit location copied into a draft, in the primer's own direction."""

    revisionId: str
    strand: PrimerStrand
    sourceSegments: tuple[SelectionSegment, ...]
    annealingSequence: str


def primerIdeaFromSelection(
    revision: SequenceRevision | None, selection: SequenceSelection | None,
    strand: PrimerStrand, tail: str,
) -> SelectedPrimerIdea:
    """Copy one continuous template stretch, independent of annotation part order."""
    if strand not in (1, -1):
        raise ValueError("Choose a forward or reverse primer.")
    revisionId, segments, annealing = _primerInputFromSelection(revision, selection, strand, tail)
    return SelectedPrimerIdea(revisionId, strand, segments, annealing)


def validatePrimerSelection(
    revision: SequenceRevision | None, selection: SequenceSelection | None,
) -> None:
    """Raise ValueError if the highlighted stretch cannot be used on its own."""
    _primerInputFromSelection(revision, selection, None, "")


def _primerInputFromSelection(
    revision: SequenceRevision | None, selection: SequenceSelection | None,
    strand: PrimerStrand | None, tail: str,
) -> tuple[str, tuple[SelectionSegment, ...], str]:
    if revision is None or revision.moleculeType != "DNA":
        raise ValueError("Open a DNA template to use a primer selection.")
    if not selection or selection.revisionId != revision.revisionId:
        raise ValueError("Select bases on the active template.")
    if len(selection) > MAX_PRIMER_SELECTION_BP:
        raise ValueError(
            f"{len(selection):,} bases selected. Set this region as the PCR target, "
            f"or select up to {MAX_PRIMER_SELECTION_BP} bases for one primer."
        )
    role = {None: "Selected", 1: "Forward", -1: "Reverse"}[strand]
    if len(selection) + len(tail.strip()) > MAX_OLIGO_BP:
        raise ValueError(f"{role} primer exceeds {MAX_OLIGO_BP:,} nt including its 5′ tail.")
    endpoints = selection.displayRange(len(revision), circular=revision.topology == "circular")
    if endpoints is None:
        raise ValueError("Select a single continuous stretch, including across the circular origin.")
    first, last = endpoints
    segments = ((first - 1, last),) if first <= last else ((first - 1, len(revision)), (0, last))
    bases = extractSegmentsSequence(revision, segments)
    if strand == -1:
        bases = reverseComplementBases(bases, "DNA")
        segments = tuple(reversed(segments))
    entry = PrimerEntry(role, bases, tail)
    if entry.problem:
        raise ValueError(entry.problem)
    return revision.revisionId, segments, entry.annealing


@dataclass(frozen=True, slots=True)
class RetainedPcrRun:
    """A PCR run and any source capture needed to save it to a project."""

    result: PcrRun
    capture: SequenceImportResult | None = None
    independent: bool = False
    certification: "CertifiedPcrRun | None" = None


@dataclass(frozen=True, slots=True)
class AssemblyPcrInput:
    """A PCR product selected for Assembly, with the run that produced it."""

    run: PcrRun
    product: PcrProduct

    @property
    def outputReference(self) -> OutputReference:
        return OutputReference(self.run.operationId, self.product.outputKey)

    @property
    def sourceRevisionId(self) -> str:
        return self.run.template.revisionId

    @property
    def revision(self) -> SequenceRevision:
        return self.product.revision


def pcrOutput(run: PcrRun, outputKey: str) -> PcrProduct:
    """Resolve an individual completed output, irrespective of its size or sequence."""
    if run.status == "completed":
        for product in run.products:
            if product.outputKey == outputKey:
                return product
    raise KeyError(OutputReference(run.operationId, outputKey))


class PcrSession:
    """Keep accepted PCR runs available after changing or closing their sources."""

    def __init__(self) -> None:
        self._runs: dict[str, RetainedPcrRun] = {}
        self._views: dict[str, GelViewSettings] = {}

    @property
    def runs(self) -> tuple[PcrRun, ...]:
        return tuple(value.result for value in self._runs.values())

    def get(self, operationId: str) -> RetainedPcrRun | None:
        return self._runs.get(operationId)

    def getView(self, operationId: str) -> GelViewSettings | None:
        """Return the last accepted preview settings for this operation."""
        return self._views.get(operationId)

    def setView(self, operationId: str, view: GelViewSettings) -> None:
        """Remember the gel preview settings for this run."""
        self._views[operationId] = view

    def snapshot(self, revisionId: str) -> SequenceRevision | None:
        for entry in self._runs.values():
            run = entry.result
            if run.template.revisionId == revisionId:
                return run.template
            for product in run.products:
                if product.revision.revisionId == revisionId:
                    return product.revision
        return None

    def retain(self, entry: RetainedPcrRun) -> None:
        existing = self._runs.get(entry.result.operationId)
        if existing is not None and existing is not entry:
            raise ValueError(f"Conflicting PCR operation ID {entry.result.operationId!r}.")
        self._runs[entry.result.operationId] = entry
