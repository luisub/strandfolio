"""Evaluate a pair of existing DNA primers on one immutable template."""

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, ExactPosition, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord

from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.fragment_annotations import (
    AnnotationPlacement,
    FragmentFeatureMapping,
    groupCircularAnnotationPlacements,
    hasPartialQualifier,
    uncertainCircularJoinBreak,
)
from strandfolio.domain.fragments import DuplexGeometry, FragmentEnd, FragmentSpan
from strandfolio.domain.primers import PrimerBinding, PrimerRevision, PrimerStrand
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.conversion import extractSegmentsSequence, reverseComplementBases


# Version 3 projects uncertain circular joins by physical adjacency and retains inherited partiality.
# Version 4 tests circular joins against their original, unclipped intervals.
PCR_ENGINE_VERSION = "4"
_SUPPORTED_ENGINE_VERSIONS = ("1", "2", "3", PCR_ENGINE_VERSION)
_MAX_TOTAL_PRODUCT_BP = 8_000_000
_MAX_FEATURE_PROJECTIONS = 1_000_000
PCR_ASSUMPTIONS = (
    "Scope: one complete selected template; the target is recorded for context.",
    "Exact binding requires the complete 3′ annealing segment with unambiguous DNA bases.",
    "Both template orientations are searched for the entered primer pair.",
    "Same-oligo pairing requires identical entered oligos; equivalent duplexes are counted once.",
    "Products use the entered forward primer's orientation and allow one traversal of a circle.",
    "Mature complementary duplex is modeled; end geometry and chemistry are unknown.",
    "Templates with unknown strand state are interpreted as duplex DNA.",
    f"Run materialization limits: {_MAX_TOTAL_PRODUCT_BP:,} total product bp and "
    f"{_MAX_FEATURE_PROJECTIONS:,} source feature projections.",
)
_DNA_BASES = frozenset("ACGT")
_SEARCH_CHUNK_BP = 16_384
_AMBIGUOUS_TEMPLATE = (
    "Ambiguous template positions are excluded from exact binding and retained in product interiors."
)

PcrStatus = Literal[
    "completed", "invalid", "unavailable", "unsupported", "canceled", "capped", "failed",
]
PcrSourceRole = Literal["template", "forwardPrimer", "reversePrimer"]
_Pairing = tuple[PrimerBinding, PrimerBinding, tuple[SelectionSegment, ...]]


@dataclass(frozen=True, slots=True)
class PcrSettings:
    """Full-product size window and work limits for a single-template search."""

    bindingMode: str = "exact"
    minProductBp: int = 1
    maxProductBp: int = 100_000
    maxTemplateBp: int = 1_000_000
    maxBindings: int = 4096
    maxPairings: int = 100_000
    maxProducts: int = 256

    def __post_init__(self) -> None:
        if type(self.bindingMode) is not str or not self.bindingMode.strip():
            raise ValueError("PCR settings need a binding mode.")
        limits = (
            (self.minProductBp, self.maxProductBp, 100_000, "Product size range (bp)"),
            (1, self.maxTemplateBp, 1_000_000, "Template limit (bp)"),
            (1, self.maxBindings, 4096, "Binding site limit"),
            (1, self.maxPairings, 100_000, "Site pairing limit"),
            (1, self.maxProducts, 256, "Product count limit"),
        )
        for low, high, ceiling, label in limits:
            if type(low) is not int or type(high) is not int or not 1 <= low <= high <= ceiling:
                raise ValueError(f"{label} requires integer bounds between 1 and {ceiling:,}.")


@dataclass(frozen=True, slots=True)
class PcrSourceMapping:
    """Map one exact half-open source span onto a half-open product span.

    Primer-source spans use each oligo's complete 5′→3′ sequence. Primer and
    template mappings overlap at the two exact annealing footprints.
    """

    role: PcrSourceRole
    sourceRevisionId: str
    sourceSpan: SelectionSegment
    productSpan: SelectionSegment
    orientation: PrimerStrand

    def __post_init__(self) -> None:
        if type(self.role) is not str or self.role not in ("template", "forwardPrimer", "reversePrimer"):
            raise ValueError("Unknown PCR source mapping role.")
        if (type(self.sourceRevisionId) is not str or not self.sourceRevisionId.strip()
                or "\x00" in self.sourceRevisionId):
            raise ValueError("A PCR mapping needs a source revision.")
        if type(self.orientation) is not int or self.orientation not in (1, -1):
            raise ValueError("A PCR source mapping orientation must be +1 or -1.")
        for span in (self.sourceSpan, self.productSpan):
            if (type(span) is not tuple or len(span) != 2
                    or any(type(boundary) is not int for boundary in span)
                    or not 0 <= span[0] < span[1]):
                raise ValueError("A PCR mapping span must be a nonempty interval.")
        if self.sourceSpan[1] - self.sourceSpan[0] != self.productSpan[1] - self.productSpan[0]:
            raise ValueError("An exact PCR mapping preserves the length of its source span.")


@dataclass(frozen=True, slots=True)
class PcrProduct:
    """A linear PCR product identified by the pair of primer bindings that produced it."""

    operationId: str
    outputKey: str
    revision: SequenceRevision
    orientation: PrimerStrand
    sourceSegments: tuple[SelectionSegment, ...]
    forwardBinding: PrimerBinding
    reverseBinding: PrimerBinding
    sourceMappings: tuple[PcrSourceMapping, ...]
    featureMappings: tuple[FragmentFeatureMapping, ...]
    warnings: tuple[str, ...] = ()

    def targetCoverage(self, target: SequenceSelection) -> tuple[int, int]:
        """Count covered and selected distinct template bases; tails contribute no bases."""
        if target.revisionId != self.templateRevisionId:
            raise ValueError("The target belongs to a different template revision.")
        covered = total = previousEnd = 0
        for start, end in sorted(target.segments):
            start = max(start, previousEnd)
            if end <= start:
                continue
            total += end - start
            covered += sum(max(0, min(end, right) - max(start, left))
                           for left, right in self.sourceSegments)
            previousEnd = end
        return covered, total

    @property
    def sizeBp(self) -> int:
        """Return full product length, including the two primer tails."""
        return len(self.revision)

    @property
    def templateRevisionId(self) -> str:
        """Return the ID of the template revision used to evaluate primer binding."""
        return self.forwardBinding.templateRevisionId


@dataclass(frozen=True, slots=True)
class PcrRun:
    """The input revisions, settings, primer bindings and products recorded for one PCR run."""

    operationId: str
    createdAt: str
    template: SequenceRevision
    forwardPrimer: PrimerRevision
    reversePrimer: PrimerRevision
    target: SequenceSelection | None
    settings: PcrSettings
    status: PcrStatus
    bindings: tuple[PrimerBinding, ...]
    products: tuple[PcrProduct, ...]
    warnings: tuple[str, ...]
    engineVersion: str = PCR_ENGINE_VERSION
    assumptions: tuple[str, ...] = PCR_ASSUMPTIONS


class _SearchStopped(Exception):
    def __init__(self, status: PcrStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def _checkApplicability(
    template: SequenceRevision, forward: PrimerRevision, reverse: PrimerRevision,
    target: SequenceSelection | None, settings: PcrSettings,
) -> None:
    if target is not None and (
        target.revisionId != template.revisionId
        or max(target.anchor, target.caret) > len(template)
        or any(end > len(template) for _, end in target.segments)
    ):
        raise _SearchStopped("invalid", "The target must belong to the exact template revision.")
    if forward.revisionId == reverse.revisionId and forward != reverse:
        raise _SearchStopped("invalid", "A primer revision ID identifies conflicting oligos.")
    if settings.bindingMode != "exact":
        raise _SearchStopped("unavailable", "Only exact binding is available.")
    if template.moleculeType != "DNA" or template.topology not in ("linear", "circular"):
        raise _SearchStopped("unsupported", "Select DNA with an explicit linear or circular topology.")
    geometry = template.duplexGeometry
    if geometry is not None and len(geometry.pairedSpan) != len(template):
        raise _SearchStopped("unsupported", "Template geometry has unpaired ends.")
    if forward.modifications or reverse.modifications:
        raise _SearchStopped("unsupported", "Primer modifications are outside the exact DNA model.")
    if len(template) > settings.maxTemplateBp:
        raise _SearchStopped("capped", f"Template exceeds the {settings.maxTemplateBp:,} bp limit.")


def _segments(
    start: int, width: int, length: int, orientation: PrimerStrand,
) -> tuple[SelectionSegment, ...]:
    end = start + width
    if end <= length:
        return ((start, end),)
    segments = ((start, length), (0, end - length))
    return segments if orientation == 1 else segments[::-1]


def _findStarts(
    sequence: str, motif: str, circular: bool, checkpoint: Callable[[], None],
) -> Iterator[int]:
    length, width = len(sequence), len(motif)
    if width > length:
        return
    search = sequence + sequence[:width - 1] if circular else sequence
    lastStart = length if circular else length - width + 1
    for blockStart in range(0, lastStart, _SEARCH_CHUNK_BP):
        blockEnd = min(blockStart + _SEARCH_CHUNK_BP, lastStart) + width - 1
        checkpoint()
        position = search.find(motif, blockStart, blockEnd)
        while position >= 0:
            yield position
            checkpoint()
            position = search.find(motif, position + 1, blockEnd)


def _collectBindings(
    template: SequenceRevision, sequence: str, primers: tuple[PrimerRevision, ...],
    settings: PcrSettings, checkpoint: Callable[[], None],
) -> tuple[PrimerBinding, ...]:
    bindings = []
    circular = template.topology == "circular"
    for primer in primers:
        annealing = primer.annealingSequence
        for strand, motif in ((1, annealing), (-1, reverseComplementBases(annealing, "DNA"))):
            for start in _findStarts(sequence, motif, circular, checkpoint):
                if len(bindings) == settings.maxBindings:
                    raise _SearchStopped(
                        "capped", f"Binding site limit reached ({settings.maxBindings:,}).",
                    )
                bindings.append(PrimerBinding(
                    template.revisionId, primer.revisionId, strand,
                    _segments(start, len(annealing), len(sequence), strand),
                    annealing, primer.tailSequence,
                ))
    return tuple(bindings)


@dataclass(frozen=True, slots=True)
class ExactPrimerPreview:
    """Exact locations of a supplied oligo on one template for the PCR editor."""

    sites: tuple[PrimerBinding, ...] = ()
    summary: str = ""


def previewPrimerBindings(
    template: SequenceRevision, primer: PrimerRevision, settings: PcrSettings,
) -> ExactPrimerPreview:
    """Inspect one entered oligo independently with the same matcher as a recorded run."""
    try:
        _checkApplicability(template, primer, primer, None, settings)
    except _SearchStopped as error:
        return ExactPrimerPreview(summary=str(error))
    # Bound synchronous work while the user types, including painting dense matches.
    limit = min(128, settings.maxBindings)
    try:
        sites = _collectBindings(template, template.sequence.upper(), (primer,),
                                 replace(settings, maxBindings=limit), lambda: None)
    except _SearchStopped:
        return ExactPrimerPreview(summary=f"More than {limit:,} exact sites (preview limit)")
    return ExactPrimerPreview(sites, f"{len(sites)} exact {'site' if len(sites) == 1 else 'sites'}")


def _pairSegments(
    first: PrimerBinding, second: PrimerBinding, template: SequenceRevision,
) -> tuple[SelectionSegment, ...]:
    plus, minus = (first, second) if first.strand == 1 else (second, first)
    distance = minus.referenceStart - plus.referenceStart
    if template.topology == "circular":
        distance %= len(template)
    span = distance + len(minus.annealingSequence)
    if distance < len(plus.annealingSequence) or span > len(template):
        return ()
    return _segments(plus.referenceStart, span, len(template), first.strand)


def _eligiblePairings(
    bindings: tuple[PrimerBinding, ...], template: SequenceRevision, forward: PrimerRevision,
    reverse: PrimerRevision, settings: PcrSettings, checkpoint: Callable[[], None],
) -> Iterator[_Pairing]:
    forwardSites = [site for site in bindings if site.primerRevisionId == forward.revisionId]
    reverseSites = {1: [], -1: []}
    for site in bindings:
        if site.primerRevisionId == reverse.revisionId:
            reverseSites[site.strand].append(site)
    checkedPairings = 0
    indistinguishable = forward.sequence == reverse.sequence and forward.tailLength == reverse.tailLength
    tailLength = forward.tailLength + reverse.tailLength
    for first in forwardSites:
        # Interchanging chemically identical oligos presents the same duplex in reverse.
        if indistinguishable and first.strand == -1:
            continue
        for second in reverseSites[-first.strand]:
            checkpoint()
            checkedPairings += 1
            if checkedPairings > settings.maxPairings:
                raise _SearchStopped("capped", f"Binding pair limit reached ({settings.maxPairings:,}).")
            segments = _pairSegments(first, second, template)
            if segments:
                size = sum(end - start for start, end in segments) + tailLength
                if settings.minProductBp <= size <= settings.maxProductBp:
                    yield first, second, segments


def _searchEvidence(
    template: SequenceRevision, sourceSequence: str, forward: PrimerRevision,
    reverse: PrimerRevision, settings: PcrSettings, checkpoint: Callable[[], None],
) -> tuple[tuple[PrimerBinding, ...], tuple[_Pairing, ...]]:
    primers = tuple(dict.fromkeys((forward, reverse)))
    bindings = _collectBindings(template, sourceSequence.upper(), primers, settings, checkpoint)
    pairings = []
    totalProductBp = 0
    featureCount = len(template.features)
    tailLength = forward.tailLength + reverse.tailLength
    for first, second, segments in _eligiblePairings(
        bindings, template, forward, reverse, settings, checkpoint,
    ):
        if len(pairings) == settings.maxProducts:
            raise _SearchStopped("capped", f"Product count limit reached ({settings.maxProducts:,}).")
        totalProductBp += sum(end - start for start, end in segments) + tailLength
        if totalProductBp > _MAX_TOTAL_PRODUCT_BP:
            raise _SearchStopped(
                "capped", f"Total product limit reached ({_MAX_TOTAL_PRODUCT_BP:,} bp).",
            )
        if (len(pairings) + 1) * featureCount > _MAX_FEATURE_PROJECTIONS:
            raise _SearchStopped(
                "capped", f"Feature projection limit reached ({_MAX_FEATURE_PROJECTIONS:,}).",
            )
        pairings.append((first, second, segments))
    return bindings, tuple(pairings)


def _sourceMappings(
    templateRevisionId: str, forward: PrimerRevision, reverse: PrimerRevision,
    segments: tuple[SelectionSegment, ...], orientation: PrimerStrand,
) -> tuple[PcrSourceMapping, ...]:
    offset = forward.tailLength
    mappings = []
    for start, end in segments:
        productEnd = offset + end - start
        mappings.append(PcrSourceMapping(
            "template", templateRevisionId, (start, end), (offset, productEnd), orientation,
        ))
        offset = productEnd
    productEnd = offset + reverse.tailLength
    return (
        *mappings,
        PcrSourceMapping("forwardPrimer", forward.revisionId, (0, len(forward.sequence)),
                         (0, len(forward.sequence)), 1),
        PcrSourceMapping("reversePrimer", reverse.revisionId, (0, len(reverse.sequence)),
                         (productEnd - len(reverse.sequence), productEnd), -1),
    )


def _outputKey(forward: PrimerBinding, reverse: PrimerBinding) -> str:
    orientation = "plus" if forward.strand == 1 else "minus"
    return f"{orientation}-f{forward.referenceStart}-r{reverse.referenceStart}"


def _matureSequence(
    sourceBases: str, forward: PrimerRevision, reverse: PrimerRevision,
    segments: tuple[SelectionSegment, ...], orientation: PrimerStrand,
) -> str:
    retained = extractSegmentsSequence(sourceBases, segments, strand=orientation)
    return (
        forward.sequence
        + retained[len(forward.annealingSequence):-len(reverse.annealingSequence)]
        + reverseComplementBases(reverse.sequence, "DNA")
    )


def _projectPart(
    part: SimpleLocation, partIndex: int, mappings: tuple[PcrSourceMapping, ...],
) -> list[AnnotationPlacement]:
    projected = []
    # Preserve the biological extraction order independently of product orientation.
    for mapping in sorted(mappings, key=lambda item: item.sourceSpan, reverse=part.strand == -1):
        sourceStart = max(int(part.start), mapping.sourceSpan[0])
        sourceEnd = min(int(part.end), mapping.sourceSpan[1])
        if sourceStart >= sourceEnd:
            continue
        offset = mapping.productSpan[0]
        if mapping.orientation == 1:
            start = offset + sourceStart - mapping.sourceSpan[0]
            end = offset + sourceEnd - mapping.sourceSpan[0]
        else:
            start = offset + mapping.sourceSpan[1] - sourceEnd
            end = offset + mapping.sourceSpan[1] - sourceStart
        strand = part.strand
        if strand in (1, -1):
            strand *= mapping.orientation
        projected.append(AnnotationPlacement(
            partIndex, sourceStart, sourceEnd, SimpleLocation(start, end, strand=strand),
        ))
    return projected


def _projectAnnotations(
    source: SeqRecord, template: SequenceRevision, output: SeqRecord,
    mappings: tuple[PcrSourceMapping, ...], checkpoint: Callable[[], None], *,
    engineVersion: str = PCR_ENGINE_VERSION,
) -> tuple[tuple[tuple[str, bool], ...], tuple[str, ...]]:
    links, warnings = [], []
    preserveAnnotationIntegrity = engineVersion not in ("1", "2")
    circularLength = len(template) if template.topology == "circular" else None
    if set(source.annotations) - {"molecule_type", "topology"} or source.dbxrefs:
        warnings.append("Record annotations and cross-references remain on the PCR template.")
    for feature, view in zip(source.features, template.features, strict=True):
        checkpoint()
        parts = () if feature.location is None else feature.location.parts
        if not parts or any(
            type(part.start) is not ExactPosition or type(part.end) is not ExactPosition
            or part.ref is not None or part.ref_db is not None or part.start >= part.end
            for part in parts
        ):
            warnings.append(f"Feature {view.label!r} has an unsupported PCR location; see the template.")
            continue
        projected = [
            mapped for index, part in enumerate(parts) for mapped in _projectPart(part, index, mappings)
        ]
        if not projected:
            continue
        unverifiedTraversal = template.hasUnverifiedFeatureTraversal(view)
        if circularLength is not None and engineVersion not in ("1", "2", "3"):
            groups = groupCircularAnnotationPlacements(
                parts, projected, circularLength, mappings[0].orientation,
                unverifiedTraversal=unverifiedTraversal,
            )
        else:
            groups = _annotationGroups(
                projected, mappings[0].orientation, circularLength,
                preserveAnnotationIntegrity=preserveAnnotationIntegrity,
                unverifiedTraversal=unverifiedTraversal,
            )
        retainedLength = sum(len(item.location) for item in projected)
        clipped = len(groups) > 1 or retainedLength != sum(map(len, parts))
        partial = clipped or (preserveAnnotationIntegrity and hasPartialQualifier(feature))
        operator = (
            feature.location.operator if isinstance(feature.location, CompoundLocation) else "join"
        )
        for group in groups:
            location = group[0] if len(group) == 1 else CompoundLocation(group, operator=operator)
            qualifiers = deepcopy(feature.qualifiers)
            if clipped:
                previous = qualifiers.get("vibegene_partial", ())
                if not isinstance(previous, (list, tuple)):
                    previous = (previous,)
                if "pcr" not in previous:
                    qualifiers["vibegene_partial"] = [*previous, "pcr"]
            child = SeqFeature(location, type=feature.type, id=feature.id, qualifiers=qualifiers)
            if child.type == "CDS" and "translation" in child.qualifiers and (
                partial or child.extract(output.seq).upper() != feature.extract(source.seq).upper()
            ):
                del child.qualifiers["translation"]
                warning = f"Removed the stale translation of changed CDS {view.label!r}."
                if warning not in warnings:
                    warnings.append(warning)
            output.features.append(child)
            links.append((view.featureId, partial))
    return tuple(links), tuple(warnings)


def _annotationGroups(
    placements: list[AnnotationPlacement], orientation: PrimerStrand, circularLength: int | None,
    *, preserveAnnotationIntegrity: bool, unverifiedTraversal: bool,
) -> list[list[SimpleLocation]]:
    if circularLength is None:
        return [[placement.location for placement in placements]]
    groups = [[placements[0].location]]
    for previous, current in zip(placements, placements[1:]):
        before, after = previous.location, current.location
        isStranded = after.strand in (1, -1)
        samePart = previous.sourcePart == current.sourcePart
        sameStrand = before.strand == after.strand
        adjacent = previous.sourceEnd % circularLength == current.sourceStart
        strand = after.strand if isStranded else orientation
        crossesEnd = after.end > before.end if strand == -1 else after.start < before.start
        split = sameStrand and crossesEnd and (samePart or adjacent or isStranded)
        if preserveAnnotationIntegrity:
            physicalBreak = uncertainCircularJoinBreak(
                previous, current, circularLength, orientation, unverifiedTraversal=unverifiedTraversal,
            )
            if physicalBreak is not None:
                split = physicalBreak
        if split:
            groups.append([])
        groups[-1].append(after)
    return groups


def _productRecord(sequence: str, templateName: str) -> SeqRecord:
    name = f"{templateName}_PCR"
    output = SeqRecord(Seq(sequence), id=name, name=name, description="Predicted PCR product")
    output.annotations.update({"molecule_type": "ds-DNA", "topology": "linear"})
    return output


def _buildProduct(
    operationId: str, template: SequenceRevision, source: SeqRecord, sourceSequence: str,
    forward: PrimerRevision, reverse: PrimerRevision, first: PrimerBinding, second: PrimerBinding,
    segments: tuple[SelectionSegment, ...], checkpoint: Callable[[], None],
) -> PcrProduct:
    checkpoint()
    orientation = first.strand
    sequence = _matureSequence(sourceSequence, forward, reverse, segments, orientation)
    outputKey = _outputKey(first, second)
    mappings = _sourceMappings(template.revisionId, forward, reverse, segments, orientation)
    output = _productRecord(sequence, template.name)
    links, warnings = _projectAnnotations(source, template, output, mappings[:-2], checkpoint)
    span = FragmentSpan(0, len(output))
    revision = SequenceRevision.fromSeqRecord(
        output, moleculeType="DNA",
        duplexGeometry=DuplexGeometry(span, span, FragmentEnd("unknown"), FragmentEnd("unknown")),
        outputReference=OutputReference(operationId, outputKey),
    )
    featureMappings = tuple(
        FragmentFeatureMapping(feature.featureId, sourceFeatureId, partial)
        for feature, (sourceFeatureId, partial) in zip(revision.features, links, strict=True)
    )
    return PcrProduct(
        operationId, outputKey, revision, orientation, segments, first, second, mappings,
        featureMappings, warnings,
    )


def simulatePcr(
    template: SequenceRevision, forwardPrimer: PrimerRevision, reversePrimer: PrimerRevision,
    *, target: SequenceSelection | None = None, settings: PcrSettings | None = None,
    isCanceled: Callable[[], bool] | None = None,
) -> PcrRun:
    """Search the entire template for inward-facing, nonoverlapping exact footprints.

    The selected target is captured as context. The product size window includes
    the tails. Cancellation and hard work limits discard provisional outputs.
    """
    settings = settings if settings is not None else PcrSettings()
    operationId = str(uuid4())
    createdAt = datetime.now(timezone.utc).isoformat()

    def checkpoint() -> None:
        if isCanceled is not None and isCanceled():
            raise _SearchStopped("canceled", "PCR search canceled.")

    bindings, products, warnings = (), (), ()
    try:
        checkpoint()
        _checkApplicability(template, forwardPrimer, reversePrimer, target, settings)
        sourceSequence = template.sequence
        if set(sourceSequence.upper()) - _DNA_BASES:
            warnings = (_AMBIGUOUS_TEMPLATE,)
        bindings, pairings = _searchEvidence(
            template, sourceSequence, forwardPrimer, reversePrimer, settings, checkpoint,
        )
        if pairings:
            source = template.toSeqRecord(preserveTraversalEvidence=True)
            products = tuple(
                _buildProduct(
                    operationId, template, source, sourceSequence, forwardPrimer, reversePrimer,
                    first, second, segments, checkpoint,
                )
                for first, second, segments in pairings
            )
        checkpoint()
    except _SearchStopped as error:
        status, bindings, products = error.status, (), ()
        warnings = (*warnings, str(error))
    except (ValueError, RuntimeError) as error:
        status, bindings, products = "failed", (), ()
        warnings = (*warnings, f"PCR calculation failed: {error}")
    else:
        status = "completed"
    return PcrRun(
        operationId, createdAt, template, forwardPrimer, reversePrimer, target, settings,
        status, bindings, products, warnings,
    )


def validatePcrEvidence(
    run: PcrRun, *, checkCompleteness: bool = False, isCanceled: Callable[[], bool] | None = None,
) -> None:
    """Check a recorded PCR run. First registration also checks that all results were recorded.

    Reopening checks the stored evidence. When checkCompleteness is enabled, compare the stored
    results and projected annotations with the bounded search without assigning new IDs.
    Raise InterruptedError if the caller cancels the check.
    """
    def checkpoint() -> None:
        if isCanceled is not None and isCanceled():
            raise InterruptedError("PCR evidence validation canceled.")

    checkpoint()
    if run.engineVersion not in _SUPPORTED_ENGINE_VERSIONS or run.assumptions != PCR_ASSUMPTIONS:
        raise ValueError("Unsupported PCR engine version or assumptions.")
    if (
        type(run.operationId) is not str or not run.operationId.strip() or "\x00" in run.operationId
        or type(run.createdAt) is not str or datetime.fromisoformat(run.createdAt).utcoffset() is None
        or type(run.bindings) is not tuple or type(run.products) is not tuple
        or type(run.warnings) is not tuple or any(type(warning) is not str for warning in run.warnings)
    ):
        raise ValueError("Invalid PCR run identity or evidence.")
    if run.status not in (
        "completed", "invalid", "unavailable", "unsupported", "canceled", "capped", "failed",
    ):
        raise ValueError("Unknown PCR completion status.")
    if run.status != "completed":
        if run.bindings or run.products:
            raise ValueError("Incomplete PCR runs cannot carry completed output evidence.")
        _validateStoppedRun(run, checkCompleteness, checkpoint)
        return
    try:
        _checkApplicability(run.template, run.forwardPrimer, run.reversePrimer, run.target, run.settings)
    except _SearchStopped as error:
        raise ValueError(f"Invalid completed PCR run: {error}") from error
    primers = {primer.revisionId: primer for primer in (run.forwardPrimer, run.reversePrimer)}
    bindings = set(run.bindings)
    if len(bindings) != len(run.bindings) or len(bindings) > run.settings.maxBindings:
        raise ValueError("Invalid or duplicated PCR binding evidence.")
    sourceSequence = run.template.sequence
    templateLength = len(run.template)
    for binding in run.bindings:
        checkpoint()
        primer = primers.get(binding.primerRevisionId)
        bindingLength = len(binding.annealingSequence)
        if (
            binding.templateRevisionId != run.template.revisionId or primer is None
            or binding.annealingSequence != primer.annealingSequence
            or binding.tailSequence != primer.tailSequence
            or bindingLength > templateLength
            or (len(binding.sourceSegments) > 1 and run.template.topology != "circular")
            or binding.sourceSegments != _segments(
                binding.referenceStart, bindingLength, templateLength, binding.strand,
            )
        ):
            raise ValueError("A PCR binding does not match its recorded input revisions.")
        footprint = extractSegmentsSequence(sourceSequence, binding.sourceSegments, strand=binding.strand)
        if footprint.upper() != binding.annealingSequence:
            raise ValueError("The recorded template bases do not match the PCR binding.")
    keys = {product.outputKey for product in run.products}
    if len(keys) != len(run.products) or len(keys) > run.settings.maxProducts:
        raise ValueError("Invalid or duplicated PCR output keys.")
    if (
        sum(product.sizeBp for product in run.products) > _MAX_TOTAL_PRODUCT_BP
        or len(run.products) * len(run.template.features) > _MAX_FEATURE_PROJECTIONS
    ):
        raise ValueError("Recorded PCR outputs exceed the run materialization limits.")
    templateFeatures = {feature.featureId for feature in run.template.features}
    for product in run.products:
        checkpoint()
        _validateProduct(run, product, bindings, sourceSequence, templateFeatures)
    if checkCompleteness:
        _validateCompleteSearch(run, sourceSequence, checkpoint)
    checkpoint()


def _validateStoppedRun(
    run: PcrRun, checkCompleteness: bool, checkpoint: Callable[[], None],
) -> None:
    if run.status in ("canceled", "failed"):
        return
    try:
        _checkApplicability(run.template, run.forwardPrimer, run.reversePrimer, run.target, run.settings)
        if run.status == "capped":
            if not checkCompleteness:
                return
            _searchEvidence(
                run.template, run.template.sequence, run.forwardPrimer, run.reversePrimer,
                run.settings, checkpoint,
            )
    except _SearchStopped as error:
        if error.status == run.status:
            return
    raise ValueError("PCR completion status disagrees with the recorded inputs or limits.")


def _validateCompleteSearch(
    run: PcrRun, sourceSequence: str, checkpoint: Callable[[], None],
) -> None:
    try:
        bindings, pairings = _searchEvidence(
            run.template, sourceSequence, run.forwardPrimer, run.reversePrimer, run.settings,
            checkpoint,
        )
    except _SearchStopped as error:
        raise ValueError("A complete PCR run exceeds the recorded search limits.") from error
    if bindings != run.bindings or pairings != tuple(
        (product.forwardBinding, product.reverseBinding, product.sourceSegments)
        for product in run.products
    ):
        raise ValueError("PCR run is missing expected bindings or products.")
    if run.warnings != ((_AMBIGUOUS_TEMPLATE,) if set(sourceSequence.upper()) - _DNA_BASES else ()):
        raise ValueError("PCR run warnings do not match its template.")
    if run.products:
        source = run.template.toSeqRecord(preserveTraversalEvidence=run.engineVersion != "1")
        for product in run.products:
            checkpoint()
            _validateProductAnnotations(
                run.template, source, product, checkpoint, engineVersion=run.engineVersion,
            )


def _validateProductAnnotations(
    template: SequenceRevision, source: SeqRecord, product: PcrProduct, checkpoint: Callable[[], None],
    *, engineVersion: str,
) -> None:
    expected = _productRecord(product.revision.sequence, template.name)
    links, warnings = _projectAnnotations(
        source, template, expected, product.sourceMappings[:-2], checkpoint,
        engineVersion=engineVersion,
    )
    actual = product.revision.toSeqRecord()
    if (
        actual.features != expected.features or actual.annotations != expected.annotations
        or (actual.id, actual.name, actual.description) != (
            expected.id, expected.name, expected.description,
        )
        or actual.dbxrefs or product.revision.sourceArtifactId is not None
        or tuple((item.sourceFeatureId, item.partial) for item in product.featureMappings) != links
        or product.warnings != warnings
    ):
        raise ValueError("PCR product annotations do not match the recorded source mapping.")


def _validateProduct(
    run: PcrRun, product: PcrProduct, bindings: set[PrimerBinding], sourceSequence: str,
    templateFeatures: set[str],
) -> None:
    forward, reverse = run.forwardPrimer, run.reversePrimer
    first, second = product.forwardBinding, product.reverseBinding
    if (
        first not in bindings or second not in bindings or first.strand == second.strand
        or first.primerRevisionId != forward.revisionId or second.primerRevisionId != reverse.revisionId
        or type(product.orientation) is not int or product.orientation != first.strand
        or product.operationId != run.operationId
        or product.outputKey != _outputKey(first, second)
        or product.revision.outputReference != OutputReference(run.operationId, product.outputKey)
    ):
        raise ValueError("PCR product has an invalid input or output identity.")
    segments = _pairSegments(first, second, run.template)
    span = FragmentSpan(0, len(product.revision))
    geometry = DuplexGeometry(span, span, FragmentEnd("unknown"), FragmentEnd("unknown"))
    if (
        not segments or segments != product.sourceSegments
        or product.revision.topology != "linear" or product.revision.duplexGeometry != geometry
        or not run.settings.minProductBp <= product.sizeBp <= run.settings.maxProductBp
        or product.sourceMappings != _sourceMappings(
            run.template.revisionId, forward, reverse, segments, first.strand,
        )
        or product.revision.sequence != _matureSequence(
            sourceSequence, forward, reverse, segments, first.strand,
        )
    ):
        raise ValueError("PCR product does not match its recorded geometry, mappings or bases.")
    if (
        type(product.featureMappings) is not tuple
        or type(product.warnings) is not tuple
        or any(type(warning) is not str for warning in product.warnings)
        or tuple(item.featureId for item in product.featureMappings)
        != tuple(feature.featureId for feature in product.revision.features)
        or any(item.sourceFeatureId not in templateFeatures for item in product.featureMappings)
    ):
        raise ValueError("PCR product has an invalid feature mapping.")
