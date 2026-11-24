"""Align two sequences and map the alignment columns to their source coordinates."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import astuple, dataclass, replace
from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
import subprocess
import sys
from time import monotonic
from typing import Literal

from Bio import __version__ as BIOPYTHON_VERSION
from Bio.Align import PairwiseAligner, substitution_matrices
from Bio.Data import CodonTable, IUPACData
from Bio.Seq import Seq

from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import UNVERIFIED_TRAVERSAL_WARNING, MoleculeType, SequenceRevision
from strandfolio.operations.conversion import (
    cdsTranslationSettings, extractSegmentsSequence, reverseComplementBases,
)


MAX_ALIGNMENT_CELLS = 25_000_000
MAX_ALIGNMENT_INPUT_LENGTH = 20_000
ALIGNMENT_ENGINE_VERSION = f"Biopython {BIOPYTHON_VERSION} / first traceback v1"
PROTEIN_SUBSTITUTION_MATRIX = "BLOSUM62"

# Larger cancellable calls isolate the native score matrix so cancellation can stop it.
_INLINE_CELLS = 250_000
_FEATURE_TYPES = frozenset(("gene", "cds", "orf", "rep_origin"))
_CANCEL_CHECK_COLUMNS = 512
_SCORE_SCALE = 1000

AlignmentMode = Literal["global", "local", "fit"]
AlignmentFrame = Literal["source", "off", -3, -2, -1, 1, 2, 3]
DifferenceKind = Literal["substitution", "insertion", "deletion"]
CodonEffect = Literal["unchanged", "synonymous", "changed", "unpaired", "unknown"]
_Path = tuple[tuple[int, int], ...]
_PathResult = tuple[float, str, _Path]


class AlignmentCanceled(Exception):
    """The caller canceled the comparison before accepting a result."""


@dataclass(frozen=True, slots=True)
class AlignmentSettings:
    """Explicit substitution and affine gap scores for a pairwise comparison.

    Scores support up to three decimal places. A gap of length k scores
    openGapScore + (k - 1) × extendGapScore. Equal open and extend scores
    request a linear gap penalty. Match and mismatch scores apply to exact
    nucleotide symbols. Proteins use the recorded substitution matrix.
    Fit mode allows free reference flanks while aligning the entire query.
    """

    mode: AlignmentMode = "global"
    matchScore: float = 2
    mismatchScore: float = -1
    openGapScore: float = -4
    extendGapScore: float = -1

    def __post_init__(self) -> None:
        if self.mode not in ("global", "local", "fit"):
            raise ValueError("Choose global, local or query placement alignment.")
        scores = self.matchScore, self.mismatchScore, self.openGapScore, self.extendGapScore
        if any(
            type(score) not in (int, float) or abs(score) > 1000 or not isfinite(score)
            for score in scores
        ):
            raise ValueError("Alignment scores must be finite numbers from −1000 to 1000.")
        if self.matchScore < 0 or any(score > 0 for score in scores[1:]):
            raise ValueError(
                "Use a nonnegative match score and nonpositive mismatch and gap scores."
            )
        if any(round(score * _SCORE_SCALE) / _SCORE_SCALE != score for score in scores):
            raise ValueError("Use at most three decimal places for alignment scores.")


@dataclass(frozen=True, slots=True)
class AlignmentInput:
    """An oriented region from one revision, created by alignmentInput."""

    recordId: str
    revisionId: str
    name: str
    sourceLength: int
    topology: Literal["linear", "circular"] | None
    selection: SequenceSelection
    featureLabel: str | None
    sequence: str
    reverseComplement: bool
    codingOffset: int | None
    moleculeType: MoleculeType = "DNA"
    sourceTranslationTable: int | None = 1
    sourceTranslationProblem: str | None = None

    def translationTable(self, frame: AlignmentFrame) -> int | None:
        """Return the effective NCBI code for this row and frame."""
        if self.moleculeType == "protein" or frame == "off":
            return None
        return self.sourceTranslationTable if frame == "source" else 1

    @property
    def strand(self) -> int:
        """Return the stored-coordinate traversal direction, forward for a protein."""
        return -1 if self.selection.strand == -1 else 1

    def __len__(self) -> int:
        return len(self.sequence)


@dataclass(frozen=True, slots=True)
class AlignmentColumn:
    """Stored zero-based symbol positions and gap-side boundaries.

    Each residue or base has a position and an empty boundary tuple. A gap
    has no position and typically one boundary. At a discontinuous feature
    join it has two boundaries, ordered from the preceding to following part.
    """

    referencePosition: int | None
    queryPosition: int | None
    referenceBoundary: tuple[int, ...]
    queryBoundary: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AlignmentDifference:
    """A contiguous difference of one kind over half-open alignment columns."""

    kind: DifferenceKind
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """The aligned reference and query, score, and mappings to their source coordinates.

    Spans are zero-based, half-open offsets within each oriented input.
    """

    reference: AlignmentInput
    query: AlignmentInput
    settings: AlignmentSettings
    score: float
    algorithm: str
    referenceAligned: str
    queryAligned: str
    referenceSpan: SelectionSegment
    querySpan: SelectionSegment
    columns: tuple[AlignmentColumn, ...]
    differences: tuple[AlignmentDifference, ...]
    pairedLength: int
    matches: int
    engineVersion: str = ALIGNMENT_ENGINE_VERSION
    substitutionMatrix: str | None = None

    @property
    def alignedLength(self) -> int:
        """Return alignment columns, including mismatch and gap columns."""
        return len(self.columns)

    @property
    def gapColumns(self) -> int:
        """Count columns containing a gap in either input."""
        return self.alignedLength - self.pairedLength

    @property
    def identity(self) -> float | None:
        """Return percent exact symbol matches among paired positions, if any."""
        return 100 * self.matches / self.pairedLength if self.pairedLength else None

    @property
    def referenceCoverage(self) -> float:
        """Return paired positions as a percentage of the selected reference length."""
        return 100 * self.pairedLength / len(self.reference)

    @property
    def referenceIntervals(self) -> tuple[SelectionSegment, ...]:
        """Map the aligned reference span to stored intervals in traversal order."""
        start, end = self.referenceSpan
        intervals = []
        offset = 0
        for lower, upper in self.reference.selection.segments:
            length = upper - lower
            left, right = max(0, start - offset), min(length, end - offset)
            if left < right:
                if self.reference.strand == -1:
                    intervals.append((upper - right, upper - left))
                else:
                    intervals.append((lower + left, lower + right))
            offset += length
        return tuple(intervals)

    @property
    def hasRepeatedExactPlacement(self) -> bool:
        """Detect another exact copy of a query placed end to end."""
        if self.settings.mode != "fit" or self.matches != len(self.query) or self.gapColumns:
            return False
        reference, query = self.reference.sequence, self.query.sequence
        return reference.find(query, reference.find(query) + 1) >= 0


@dataclass(frozen=True, slots=True)
class AlignmentCodon:
    """A codon, its possible residues (including stop), alignment columns and source positions."""

    aminoAcid: str
    triplet: str
    footprint: tuple[int, int, int]
    sourcePositions: tuple[int, int, int]
    effect: CodonEffect = "unpaired"
    assignments: frozenset[str] = frozenset()


def _featureSegments(feature: SequenceFeature) -> tuple[SelectionSegment, ...]:
    if (
        feature.type.casefold() not in _FEATURE_TYPES
        or feature.strand not in (-1, 1)
        or feature.operator not in (None, "join")
    ):
        return ()
    segments = []
    for part in feature.parts:
        if (
            not part.isSelectable or part.start is None or part.end is None
            or part.strand != feature.strand
        ):
            return ()
        segments.append((part.start, part.end))
    return tuple(segments)


def alignmentFeatures(revision: SequenceRevision) -> tuple[SequenceFeature, ...]:
    """List exact local genes, CDSs, ORFs and replication origins with a known strand."""
    if revision.moleculeType == "protein":
        return ()
    return tuple(
        feature for feature in revision.features
        if _featureSegments(feature) and not revision.hasUnverifiedFeatureTraversal(feature)
    )


def alignmentInput(
    revision: SequenceRevision,
    featureId: str | None = None,
    reverseComplement: bool = False,
    *,
    checkProteinMatrix: bool = True,
) -> AlignmentInput:
    """Capture the selected input without modifying its sequence or features.

    A whole record uses its stored origin. A feature follows its ordered
    location parts and annotated strand. Reverse complementation flips the
    complete nucleotide selection, including the traversal order of joined
    parts. Proteins use whole records in stored order. The pairwise workflow
    checks BLOSUM62 here. Other engines can validate their own alphabet.

    Raises:
        KeyError: The selected feature is absent from this exact revision.
        ValueError: The molecule, feature geometry or selected length is unsupported.
    """
    moleculeType = revision.moleculeType
    if moleculeType == "protein" and (featureId is not None or reverseComplement):
        raise ValueError("Choose the whole protein in stored order; it has no reverse complement.")
    feature = revision.getFeature(featureId) if featureId is not None else None
    segments = ((0, len(revision)),)
    strand = 1
    codingOffset = None if moleculeType == "protein" else 0
    sourceTable = None if moleculeType == "protein" else 1
    translationProblem = None
    if feature is not None:
        if revision.hasUnverifiedFeatureTraversal(feature):
            raise ValueError(
                f"{UNVERIFIED_TRAVERSAL_WARNING} Choose the whole record or another region."
            )
        segments = _featureSegments(feature)
        if not segments or feature.strand not in (-1, 1):
            raise ValueError("Choose an exact gene, CDS, ORF or replication origin with one known strand.")
        strand = feature.strand
        featureType = feature.type.casefold()
        qualifiers = dict(feature.qualifiers)
        if featureType == "rep_origin":
            codingOffset = sourceTable = None
            translationProblem = "Replication origins have no annotated coding frame."
        elif featureType == "cds":
            settings = cdsTranslationSettings(qualifiers)
            translationProblem = settings.problem
            if translationProblem is None and qualifiers.get("vibegene_partial"):
                translationProblem = "Only part of this feature was retained."
            sourceTable = settings.tableId if translationProblem is None else None
            codingOffset = settings.codonStart - 1 if sourceTable is not None else None
        elif qualifiers.get("vibegene_partial"):
            codingOffset = sourceTable = None
            translationProblem = "Only part of this feature was retained."
    if reverseComplement:
        segments = segments[::-1]
        strand = -strand
    anchor = segments[0][0 if strand == 1 else 1]
    caret = segments[-1][1 if strand == 1 else 0]
    selection = SequenceSelection(
        revision.revisionId, anchor, caret,
        featureId=featureId, segments=segments, wholeFeature=feature is not None,
        strand=None if moleculeType == "protein" else strand,
    )
    _checkInputLength(len(selection))
    sequence = extractSegmentsSequence(
        revision, segments, strand=strand, moleculeType=moleculeType,
    ).upper()
    if moleculeType == "protein" and checkProteinMatrix:
        unsupported = set(sequence).difference(_proteinScores().alphabet)
        if unsupported:
            raise ValueError(
                f"{PROTEIN_SUBSTITUTION_MATRIX} has no scores for "
                f"{', '.join(sorted(unsupported))} in {revision.name}."
            )
    return AlignmentInput(
        revision.recordId, revision.revisionId, revision.name, len(revision), revision.topology,
        selection, feature.label if feature is not None else None,
        sequence, reverseComplement, codingOffset, moleculeType, sourceTable, translationProblem,
    )


def _checkInputLength(length: int) -> None:
    if not 0 < length <= MAX_ALIGNMENT_INPUT_LENGTH:
        raise ValueError(
            f"Choose a nonempty region with a length up to {MAX_ALIGNMENT_INPUT_LENGTH:,} symbols."
        )


def _pairCells(referenceLength: int, queryLength: int) -> int:
    _checkInputLength(referenceLength)
    _checkInputLength(queryLength)
    cells = (referenceLength + 1) * (queryLength + 1)
    if cells > MAX_ALIGNMENT_CELLS:
        raise ValueError(
            f"This pair exceeds {MAX_ALIGNMENT_CELLS:,} alignment cells. Choose a shorter region."
        )
    return cells


def _checkCanceled(isCanceled: Callable[[], bool] | None) -> None:
    if isCanceled is not None and isCanceled():
        raise AlignmentCanceled("Alignment canceled.")


@lru_cache(maxsize=1)
def _proteinScores() -> substitution_matrices.Array:
    try:
        matrix = substitution_matrices.load(PROTEIN_SUBSTITUTION_MATRIX)
    except OSError as error:
        raise RuntimeError(
            f"The installed Biopython package could not read {PROTEIN_SUBSTITUTION_MATRIX}."
        ) from error
    return matrix * _SCORE_SCALE


def _biopythonPath(
    reference: str, query: str, settings: AlignmentSettings, moleculeType: MoleculeType,
) -> _PathResult:
    # Integer scaling keeps the C engine's score comparisons exact at this precision.
    fit = settings.mode == "fit"
    aligner = PairwiseAligner(
        mode="global" if fit else settings.mode, epsilon=0.5,
        open_gap_score=round(settings.openGapScore * _SCORE_SCALE),
        extend_gap_score=round(settings.extendGapScore * _SCORE_SCALE),
    )
    if moleculeType == "protein":
        aligner.substitution_matrix = _proteinScores()
    else:
        aligner.match_score = round(settings.matchScore * _SCORE_SCALE)
        aligner.mismatch_score = round(settings.mismatchScore * _SCORE_SCALE)
    if fit:
        # Deletions at either query end consume only the reference flanks.
        aligner.end_deletion_score = 0
    # Take one traceback in the installed engine's deterministic order. Counting
    # paths or scanning ties can overflow even for short, repetitive inputs.
    alignments = aligner.align(reference, query)
    score = alignments.score / _SCORE_SCALE
    if fit and score <= 0:
        return score, aligner.algorithm, ()
    alignment = next(alignments, None)
    path = () if alignment is None else tuple(
        (int(left), int(right)) for left, right in alignment.coordinates.T
    )
    if fit and path:
        # Drop free flanks before building columns and source mappings.
        first = int(path[1][1] == 0)
        stop = len(path) - int(path[-2][1] == len(query))
        path = path[first:stop]
    return score, aligner.algorithm, path


def _writePath() -> None:
    """Read one alignment request from stdin and write its traceback to stdout."""
    reference, query, settings, moleculeType = json.load(sys.stdin)
    _pairCells(len(reference), len(query))
    path = _biopythonPath(reference, query, AlignmentSettings(*settings), moleculeType)
    json.dump(path, sys.stdout)


def _cancellablePath(
    reference: str,
    query: str,
    settings: AlignmentSettings,
    moleculeType: MoleculeType,
    isCanceled: Callable[[], bool],
) -> _PathResult:
    score, algorithm, path = runAlignmentWorker(
        "pairwise",
        (reference, query, astuple(settings), moleculeType), isCanceled, engineName="Biopython",
    )
    return score, algorithm, tuple((left, right) for left, right in path)


def runAlignmentWorker(
    operation: Literal["pairwise", "shared-columns"], request: object,
    isCanceled: Callable[[], bool] | None,
    *, engineName: str, timeoutSeconds: float | None = None,
) -> object:
    """Exchange JSON with an isolated alignment engine and reap it on cancellation or timeout."""
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).with_name("strandfolio-alignment-worker")
        command = [str(helper), operation]
        directory = None
    else:
        command = [sys.executable, "-m", "strandfolio.operations.alignment_worker", operation]
        # Source checkouts and installed wheels can both be launched from another directory.
        directory = Path(__file__).resolve().parents[2]
    payload = json.dumps(request).encode()
    deadline = None if timeoutSeconds is None else monotonic() + timeoutSeconds
    _checkCanceled(isCanceled)
    with subprocess.Popen(
        command, cwd=directory,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as worker, ThreadPoolExecutor(max_workers=1) as pipes:
        # Use a separate thread for pipe I/O so cancellation or timeout can stop the child
        # even when a write exceeds pipe capacity.
        message = pipes.submit(worker.communicate, payload)
        try:
            while not message.done():
                _checkCanceled(isCanceled)
                if deadline is not None and monotonic() >= deadline:
                    raise RuntimeError(f"{engineName} exceeded its {timeoutSeconds:g}-second time limit.")
                wait((message,), timeout=0.01)
        finally:
            if worker.poll() is None:
                worker.kill()
        output, errors = message.result()
    _checkCanceled(isCanceled)
    if worker.returncode:
        diagnostics = errors.decode(errors="replace").strip().splitlines()
        reason = diagnostics[-1][:300] if diagnostics else f"worker exit {worker.returncode}"
        raise RuntimeError(f"{engineName} stopped before producing a result. {reason}") from (
            subprocess.CalledProcessError(worker.returncode, command, stderr=errors)
        )
    try:
        return json.loads(output)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError(f"{engineName} returned unreadable alignment data.") from error


def alignmentSourcePositions(source: AlignmentInput) -> tuple[int, ...]:
    """Map ungapped row offsets to zero-based original revision positions in traversal order."""
    if source.strand == 1:
        return tuple(
            position
            for start, end in source.selection.segments
            for position in range(start, end)
        )
    return tuple(
        position
        for start, end in source.selection.segments
        for position in range(end - 1, start - 1, -1)
    )


def _gapBoundaries(positions: tuple[int, ...], offset: int, strand: int) -> tuple[int, ...]:
    if offset == 0:
        return (positions[0] + (strand == -1),)
    preceding = positions[offset - 1] + (strand == 1)
    if offset == len(positions):
        return (preceding,)
    following = positions[offset] + (strand == -1)
    return (preceding,) if preceding == following else (preceding, following)


def _alignedRows(reference: str, query: str, path: _Path) -> tuple[str, str]:
    referenceChunks = []
    queryChunks = []
    for (refStart, queryStart), (refEnd, queryEnd) in zip(path, path[1:]):
        span = max(refEnd - refStart, queryEnd - queryStart)
        referenceChunks.append(reference[refStart:refEnd] or "-" * span)
        queryChunks.append(query[queryStart:queryEnd] or "-" * span)
    return "".join(referenceChunks), "".join(queryChunks)


def _summarize(
    reference: AlignmentInput,
    query: AlignmentInput,
    settings: AlignmentSettings,
    pathResult: _PathResult,
    isCanceled: Callable[[], bool] | None,
) -> AlignmentResult:
    score, algorithm, path = pathResult
    referenceAligned, queryAligned = _alignedRows(reference.sequence, query.sequence, path)
    refStart, queryStart = path[0] if path else (0, 0)
    refEnd, queryEnd = path[-1] if path else (0, 0)
    refOffset, queryOffset = refStart, queryStart
    refPositions, queryPositions = alignmentSourcePositions(reference), alignmentSourcePositions(query)
    columns = []
    differences: list[AlignmentDifference] = []
    paired = matches = 0
    for index, (refBase, queryBase) in enumerate(zip(referenceAligned, queryAligned, strict=True)):
        if index % _CANCEL_CHECK_COLUMNS == 0:
            _checkCanceled(isCanceled)
        refPosition = refPositions[refOffset] if refBase != "-" else None
        queryPosition = queryPositions[queryOffset] if queryBase != "-" else None
        columns.append(AlignmentColumn(
            refPosition, queryPosition,
            _gapBoundaries(refPositions, refOffset, reference.strand)
            if refPosition is None else (),
            _gapBoundaries(queryPositions, queryOffset, query.strand)
            if queryPosition is None else (),
        ))
        refOffset += refPosition is not None
        queryOffset += queryPosition is not None
        if refPosition is not None and queryPosition is not None:
            paired += 1
        if refBase == queryBase:
            matches += 1
            continue
        kind: DifferenceKind = "substitution"
        if refPosition is None:
            kind = "insertion"
        elif queryPosition is None:
            kind = "deletion"
        if differences and differences[-1].kind == kind and differences[-1].end == index:
            previous = differences[-1]
            differences[-1] = AlignmentDifference(kind, previous.start, index + 1)
        else:
            differences.append(AlignmentDifference(kind, index, index + 1))
    _checkCanceled(isCanceled)
    return AlignmentResult(
        reference, query, settings, score, algorithm, referenceAligned, queryAligned,
        (refStart, refEnd), (queryStart, queryEnd),
        tuple(columns), tuple(differences), paired, matches,
        substitutionMatrix=(
            PROTEIN_SUBSTITUTION_MATRIX if reference.moleculeType == "protein" else None
        ),
    )


def alignPair(
    reference: AlignmentInput,
    query: AlignmentInput,
    settings: AlignmentSettings = AlignmentSettings(),
    *,
    isCanceled: Callable[[], bool] | None = None,
) -> AlignmentResult:
    """Align a single pair and take the first native traceback without enumerating ties.

    The score matrix is bounded by (reference length + 1) × (query length + 1).
    Local and fit alignments with no positive scoring span have zero columns and coverage.
    For cancellable calls, larger score matrices run in a child process that is
    terminated on cancellation. The callback runs on the calling thread.

    Raises:
        AlignmentCanceled: The caller canceled while the comparison was running.
        ValueError: The inputs differ in molecule type or exceed a supported work bound.
        RuntimeError: The isolated engine exited without returning a result.
    """
    _checkCanceled(isCanceled)
    if reference.moleculeType != query.moleculeType:
        raise ValueError("Choose a reference and query of the same molecule type.")
    cells = _pairCells(len(reference), len(query))
    if isCanceled is not None and cells > _INLINE_CELLS:
        path = _cancellablePath(
            reference.sequence, query.sequence, settings, reference.moleculeType, isCanceled,
        )
    else:
        path = _biopythonPath(reference.sequence, query.sequence, settings, reference.moleculeType)
    _checkCanceled(isCanceled)
    return _summarize(reference, query, settings, path, isCanceled)


@lru_cache(maxsize=64)
def _codonLookup(tableId: int) -> dict[str, str]:
    table = CodonTable.unambiguous_dna_by_id[tableId]
    return table.forward_table | dict.fromkeys(table.stop_codons, "*")


@lru_cache(maxsize=4096)
def _codonAssignments(dnaTriplet: str, tableId: int) -> tuple[str, frozenset[str]]:
    lookup = _codonLookup(tableId)
    if dnaTriplet in lookup:
        residue = lookup[dnaTriplet]
        return residue, frozenset((residue,))
    assignments = frozenset(CodonTable.list_possible_proteins(
        dnaTriplet, lookup, IUPACData.ambiguous_dna_values,
    ))
    symbol = next(iter(assignments)) if len(assignments) == 1 else str(
        Seq(dnaTriplet).translate(table=tableId)
    )
    return symbol, assignments


def _translationTrack(
    source: AlignmentInput,
    aligned: str,
    span: SelectionSegment,
    frame: AlignmentFrame,
) -> list[AlignmentCodon | None]:
    track: list[AlignmentCodon | None] = [None] * len(aligned)
    tableId = source.translationTable(frame)
    if tableId is None or not aligned:
        return track
    if frame == "source":
        offset = source.codingOffset
        if offset is None:
            return track
        reverse = source.selection.wholeFeature and source.reverseComplement
    else:
        offset = abs(frame) - 1
        reverse = frame < 0
    sequence = (
        reverseComplementBases(source.sequence, source.moleculeType) if reverse else source.sequence
    )
    positions = alignmentSourcePositions(source)
    columns = [column for column, base in enumerate(aligned) if base != "-"]
    start, end = span
    for index in range(offset, len(sequence) - 2, 3):
        if reverse:
            first = len(sequence) - index - 1
            sourceIndexes = first, first - 1, first - 2
        else:
            sourceIndexes = index, index + 1, index + 2
        first, middle, last = sourceIndexes
        if not (start <= first < end and start <= last < end):
            continue
        footprint = columns[first - start], columns[middle - start], columns[last - start]
        triplet = sequence[index:index + 3]
        symbol, assignments = _codonAssignments(triplet.replace("U", "T"), tableId)
        track[footprint[1]] = AlignmentCodon(
            symbol, triplet, footprint, (positions[first], positions[middle], positions[last]),
            assignments=assignments,
        )
    return track


def alignmentTranslations(
    result: AlignmentResult, frame: AlignmentFrame = "source",
) -> tuple[tuple[AlignmentCodon | None, ...], tuple[AlignmentCodon | None, ...]]:
    """Translate selected ungapped inputs and project complete codons to the result.

    Each track is indexed by alignment column. Codons occupy their middle
    base's column and keep all three column and source indexes. Local clipping
    leaves the phase anchored to the entire selected input. Source frame uses
    +1 on a whole input and follows the original coding strand of a feature.
    The source frame uses a supported CDS's annotated genetic code. Explicit
    frames use the standard code relative to the oriented input. Effects compare
    all possible ordinary codon assignments at matching footprints, including stop.
    A protein input has no codons and returns an empty marker at each column.
    """
    if frame not in ("source", "off", -3, -2, -1, 1, 2, 3) or type(frame) not in (int, str):
        raise ValueError("Choose source, off or a reading frame from +1 to +3 or −1 to −3.")
    reference = _translationTrack(
        result.reference, result.referenceAligned, result.referenceSpan, frame,
    )
    query = _translationTrack(result.query, result.queryAligned, result.querySpan, frame)
    for column, (refCodon, queryCodon) in enumerate(zip(reference, query, strict=True)):
        if refCodon is None or queryCodon is None or refCodon.footprint != queryCodon.footprint:
            continue
        if refCodon.assignments.isdisjoint(queryCodon.assignments):
            effect: CodonEffect = "changed"
        elif len(refCodon.assignments) > 1 or len(queryCodon.assignments) > 1:
            effect = "unknown"
        elif refCodon.triplet != queryCodon.triplet:
            effect = "synonymous"
        else:
            effect = "unchanged"
        reference[column] = replace(refCodon, effect=effect)
        query[column] = replace(queryCodon, effect=effect)
    return tuple(reference), tuple(query)
