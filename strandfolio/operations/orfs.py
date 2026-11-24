"""Find contiguous candidate coding regions on either strand of a DNA revision."""

from collections.abc import Callable
from dataclasses import dataclass

from Bio.Data import CodonTable

from strandfolio.domain.coordinates import SelectionSegment
from strandfolio.domain.features import ANNOTATION_CURATION_QUALIFIER, ORF_PREDICTION_CURATION
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.conversion import geneticCodeProblem, reverseComplementBases


@dataclass(frozen=True, slots=True)
class OrfOptions:
    """Choose an inclusive minimum peptide length and whether to review no-stop ORFs."""

    minimumAa: int = 30
    includeNoStop: bool = True

    def __post_init__(self) -> None:
        if type(self.minimumAa) is not int or self.minimumAa < 1:
            raise ValueError("Minimum ORF length must be a positive number of amino acids.")
        if type(self.includeNoStop) is not bool:
            raise ValueError("Choose whether to include ORFs without a terminal stop.")


@dataclass(frozen=True, slots=True)
class OrfCandidate:
    """An ATG-initiated candidate with half-open parts in coding order and a signed frame.

    Parts include the terminal stop if present. The peptide length excludes it.
    """

    parts: tuple[SelectionSegment, ...]
    frame: int
    lengthAa: int
    hasStop: bool

    @property
    def strand(self) -> int:
        return 1 if self.frame > 0 else -1


@dataclass(frozen=True, slots=True)
class OrfReview:
    """Transient predictions for one revision, genetic code and search setting."""

    revisionId: str
    tableId: int
    options: OrfOptions
    candidates: tuple[OrfCandidate, ...]


def _candidateParts(
    start: int, end: int, length: int, strand: int,
) -> tuple[SelectionSegment, ...]:
    parts = ((start, min(end, length)),)
    if end > length:
        parts += ((0, end - length),)
    if strand == -1:
        return tuple((length - stop, length - begin) for begin, stop in parts)
    return parts


def _scanStrand(
    bases: str, strand: int, isCircular: bool, table: CodonTable.CodonTable,
    options: OrfOptions, isCanceled: Callable[[], bool],
) -> tuple[OrfCandidate, ...]:
    length = len(bases)
    scan = bases + bases if isCircular else bases
    stops = frozenset(table.stop_codons)
    bestByEnd: dict[tuple[int, bool], tuple[int, int, int]] = {}
    for offset in range(3):
        end = len(scan) - (len(scan) - offset) % 3
        hasStop = False
        hasEndpoint = options.includeNoStop and not isCircular
        # Walk backwards so each start can use the next stop in constant time.
        for index, start in enumerate(range(end - 3, offset - 1, -3)):
            if index % 2048 == 0 and isCanceled():
                raise InterruptedError("ORF search was canceled.")
            codon = scan[start:start + 3]
            if codon in stops:
                end, hasStop, hasEndpoint = start, True, True
            elif codon not in table.forward_table:
                hasEndpoint = False
            elif codon == "ATG" and start < length and hasEndpoint:
                codingLength = end - start
                stopLength = 3 if hasStop else 0
                if codingLength < options.minimumAa * 3 or codingLength + stopLength > length:
                    continue
                # Distinct passes through a circular stop refer to the same physical endpoint.
                key = (end % length if isCircular else end, hasStop)
                previous = bestByEnd.get(key)
                if previous is None or codingLength > previous[1]:
                    bestByEnd[key] = start, codingLength, (offset + 1) * strand
    return tuple(
        OrfCandidate(
            _candidateParts(start, start + codingLength + (3 if hasStop else 0), length, strand),
            frame, codingLength // 3, hasStop,
        )
        for (_, hasStop), (start, codingLength, frame) in bestByEnd.items()
    )


def scanOrfs(
    revision: SequenceRevision, *, tableId: int = 1, options: OrfOptions = OrfOptions(),
    isCanceled: Callable[[], bool] = lambda: False,
) -> OrfReview:
    """Scan six frames using ATG, table-specific stops and breaks at ambiguous codons.

    A declared circle can wrap once. Linear and unknown topology use sequence ends.
    Keep the longest candidate at each in-frame endpoint and omit already annotated
    exact CDS or ORF loci. Raise InterruptedError if the caller cancels the scan.
    """
    if revision.moleculeType != "DNA":
        raise ValueError("ORF discovery requires DNA.")
    if type(tableId) is not int:
        raise ValueError("Choose a genetic code by its integer table ID.")
    if problem := geneticCodeProblem(tableId):
        raise ValueError(problem)
    table = CodonTable.unambiguous_dna_by_id[tableId]
    bases = revision.sequence.upper()
    isCircular = revision.topology == "circular"
    annotated = {
        (tuple((part.start, part.end) for part in feature.parts), feature.strand)
        for feature in revision.features
        if feature.type.casefold() in {"cds", "orf", "misc_feature"}
        and (feature.type.casefold() != "misc_feature"
             or dict(feature.qualifiers).get(ANNOTATION_CURATION_QUALIFIER)
             == (ORF_PREDICTION_CURATION,))
        and feature.operator in (None, "join")
        and all(part.isSelectable and part.strand == feature.strand for part in feature.parts)
        and not revision.hasUnverifiedFeatureTraversal(feature)
    }
    forward = _scanStrand(bases, 1, isCircular, table, options, isCanceled)
    reverse = _scanStrand(
        reverseComplementBases(bases, "DNA"), -1, isCircular, table, options, isCanceled,
    )
    if isCanceled():
        raise InterruptedError("ORF search was canceled.")
    candidates = sorted(
        (item for strand in (forward, reverse) for item in strand
         if (item.parts, item.strand) not in annotated),
        key=lambda item: (-item.lengthAa, item.parts, -item.frame),
    )
    return OrfReview(revision.revisionId, tableId, options, tuple(candidates))
