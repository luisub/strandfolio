"""Project surviving feature locations into a physical digest product."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING

from Bio.SeqFeature import CompoundLocation, ExactPosition, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord

if TYPE_CHECKING:
    from strandfolio.domain.fragments import DuplexGeometry, FragmentSpan
    from strandfolio.domain.sequence import SequenceRevision


_PARTIAL_QUALIFIER = "vibegene_partial"
_DIGEST_PARTIAL = "digest"


@dataclass(frozen=True, slots=True)
class FragmentFeatureMapping:
    """Link one retained candidate annotation to an annotation on the template."""

    featureId: str
    sourceFeatureId: str
    partial: bool

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value for value in (
            self.featureId, self.sourceFeatureId,
        )) or type(self.partial) is not bool:
            raise ValueError("A digest feature mapping needs valid identities and a partial flag.")


@dataclass(frozen=True, slots=True)
class AnnotationPlacement:
    """One source interval and its occupied interval on a projected molecule."""

    sourcePart: int
    sourceStart: int
    sourceEnd: int
    location: SimpleLocation


def hasPartialQualifier(feature: SeqFeature, *, includePcr: bool = True) -> bool:
    """Recognize the partial-feature markers written by the recorded projection rules."""
    value = feature.qualifiers.get(_PARTIAL_QUALIFIER, ())
    values = value if isinstance(value, (list, tuple)) else (value,)
    return _DIGEST_PARTIAL in values or (includePcr and "pcr" in values)


def uncertainCircularJoinBreak(
    previous: AnnotationPlacement, current: AnnotationPlacement, length: int, orientation: int,
    *, unverifiedTraversal: bool,
) -> bool | None:
    """Apply the historical uncertain-join rule used when replaying saved products."""
    if previous.sourcePart == current.sourcePart or not (
        unverifiedTraversal or (
            previous.location.strand in (None, 0) and current.location.strand in (None, 0)
        )
    ):
        return None
    forward = previous.sourceEnd % length == current.sourceStart
    reverse = current.sourceEnd % length == previous.sourceStart
    before, after = previous.location, current.location
    if orientation == -1:
        before, after = after, before
    return (forward or reverse) and not (
        (forward and before.end == after.start) or (reverse and after.end == before.start)
    )


def _uncertainAdjacentDirections(
    before: SimpleLocation, after: SimpleLocation, length: int, unverifiedTraversal: bool,
) -> tuple[int, ...]:
    if not (unverifiedTraversal or before.strand in (None, 0) and after.strand in (None, 0)):
        return ()
    directions = []
    if before.end % length == after.start:
        directions.append(1)
    if after.end % length == before.start:
        directions.append(-1)
    return tuple(directions)


def _expectedJoinOffset(
    before: SimpleLocation, after: SimpleLocation, length: int, direction: int,
) -> int:
    # Overlapping parts within the same turn intentionally refer to the same bases.
    if before.start < after.end and after.start < before.end:
        return 0
    crossesOrigin = after.start < before.start if direction == 1 else after.end > before.end
    return direction * length if crossesOrigin else 0


def groupCircularAnnotationPlacements(
    parts: list[SimpleLocation], placements: list[AnnotationPlacement], length: int,
    orientation: int, *, unverifiedTraversal: bool,
) -> list[list[SimpleLocation]]:
    """Group surviving intervals on a linear view of a circular source.

    On each mapped span, the product coordinate in source-forward orientation
    differs from the source coordinate by a constant. That offset changes at
    the source origin. Compare the change between placements with what the
    original join implies to distinguish a retained origin from a new end.
    """
    adjacent = [()]
    adjacent.extend(
        _uncertainAdjacentDirections(before, after, length, unverifiedTraversal)
        for before, after in zip(parts, parts[1:])
    )
    adjacent.append(())
    ordered = []
    for partIndex, items in groupby(placements, key=lambda item: item.sourcePart):
        pieces = list(items)
        # Infer only the order of pieces of one part so its joined edge can be
        # tested. Either direction may fit a full turn. Keep the default order
        # when that's ambiguous, and preserve the recorded source part order.
        directions = set(adjacent[partIndex] + adjacent[partIndex + 1])
        if len(pieces) > 1 and len(directions) == 1:
            reverse = directions == {-1}
            pieces.sort(key=lambda item: item.sourceEnd if reverse else item.sourceStart,
                        reverse=reverse)
        ordered.extend(pieces)

    if not ordered:
        return []
    offsets = [
        (piece.location.start if orientation == 1 else -piece.location.end) - piece.sourceStart
        for piece in ordered
    ]
    groups = [[ordered[0].location]]
    for index, (previous, current) in enumerate(zip(ordered, ordered[1:])):
        delta = offsets[index + 1] - offsets[index]
        before, after = parts[previous.sourcePart], parts[current.sourcePart]
        if previous.sourcePart == current.sourcePart:
            crossesEnd = delta != 0
        else:
            directions = (
                adjacent[current.sourcePart] if current.sourcePart == previous.sourcePart + 1 else ()
            )
            if not directions and before.strand == after.strand and after.strand in (1, -1):
                directions = (after.strand,)
            crossesEnd = bool(directions) and all(
                delta != _expectedJoinOffset(before, after, length, direction)
                for direction in directions
            )
        if crossesEnd:
            groups.append([])
        groups[-1].append(current.location)
    return groups


def _mappedPart(
    part: SimpleLocation, partIndex: int, sourceSpans: "tuple[FragmentSpan, ...]",
    geometry: "DuplexGeometry",
) -> list[AnnotationPlacement]:
    occupied = {1: geometry.forwardSpan, -1: geometry.reverseSpan}.get(part.strand)
    placements = []
    offset = 0
    for span in sourceSpans:
        start = max(int(part.start), span.start)
        end = min(int(part.end), span.end)
        productStart, productEnd = offset + start - span.start, offset + end - span.start
        if occupied is not None:
            productStart = max(productStart, occupied.start)
            productEnd = min(productEnd, occupied.end)
        if productStart < productEnd:
            placements.append(AnnotationPlacement(
                partIndex, span.start + productStart - offset, span.start + productEnd - offset,
                SimpleLocation(productStart, productEnd, strand=part.strand),
            ))
        offset += len(span)
    if part.strand == -1:
        placements.sort(key=lambda item: (-item.sourceEnd, -int(item.location.end)))
    else:
        placements.sort(key=lambda item: (item.sourceStart, int(item.location.start)))
    return placements


def _splitPlacements(
    placements: list[AnnotationPlacement], *, circularSourceLength: int | None,
    splitUnstrandedCircularJoins: bool, preserveAnnotationIntegrity: bool, unverifiedTraversal: bool,
) -> list[list[SimpleLocation]]:
    if not placements:
        return []
    groups = [[placements[0].location]]
    for previous, current in zip(placements, placements[1:]):
        strand = current.location.strand
        isSamePart = current.sourcePart == previous.sourcePart
        canSplit = previous.location.strand == strand and (
            isSamePart or (circularSourceLength is not None and strand in (1, -1))
        )
        if strand == -1:
            isReordered = current.location.end > previous.location.start
            repeatsSource = current.sourceEnd > previous.sourceStart
        else:
            isReordered = current.location.start < previous.location.end
            repeatsSource = current.sourceStart < previous.sourceEnd
        splitsAtEnds = canSplit and (isReordered or (isSamePart and repeatsSource))
        physicalBreak = None
        if preserveAnnotationIntegrity and circularSourceLength is not None:
            physicalBreak = uncertainCircularJoinBreak(
                previous, current, circularSourceLength, 1, unverifiedTraversal=unverifiedTraversal,
            )
        if physicalBreak is not None:
            splitsAtEnds = physicalBreak
        elif (splitUnstrandedCircularJoins and circularSourceLength is not None and not isSamePart
                and previous.location.strand in (None, 0) and strand in (None, 0)):
            # For an unstranded join, part order alone cannot establish that a cut
            # broke the join. Compare adjacent source bases with their new positions.
            adjacentForward = previous.sourceEnd % circularSourceLength == current.sourceStart
            adjacentReverse = current.sourceEnd % circularSourceLength == previous.sourceStart
            splitsAtEnds = (
                (adjacentForward and current.location.start < previous.location.end)
                or (adjacentReverse and previous.location.start < current.location.end)
            )
        if splitsAtEnds:
            groups.append([])
        groups[-1].append(current.location)
    return groups


def _projectFeature(
    feature: SeqFeature, label: str, sourceSpans: "tuple[FragmentSpan, ...]",
    geometry: "DuplexGeometry", *, circularSourceLength: int | None,
    splitUnstrandedCircularJoins: bool, preserveAnnotationIntegrity: bool,
    preserveCircularAnnotationContinuity: bool, unverifiedTraversal: bool,
) -> tuple[list[SeqFeature], bool, tuple[str, ...]]:
    location = feature.location
    parts = () if location is None else location.parts
    if not parts or any(
        type(part.start) is not ExactPosition or type(part.end) is not ExactPosition
        or part.ref is not None or part.ref_db is not None or part.start >= part.end
        for part in parts
    ):
        return [], False, (
            f"Feature {label!r} has an unsupported location. Inspect it on the source revision.",
        )

    placements = [
        placement for index, part in enumerate(parts)
        for placement in _mappedPart(part, index, sourceSpans, geometry)
    ]
    if preserveCircularAnnotationContinuity and circularSourceLength is not None:
        groups = groupCircularAnnotationPlacements(
            parts, placements, circularSourceLength, 1, unverifiedTraversal=unverifiedTraversal,
        )
    else:
        groups = _splitPlacements(
            placements, circularSourceLength=circularSourceLength,
            splitUnstrandedCircularJoins=splitUnstrandedCircularJoins,
            preserveAnnotationIntegrity=preserveAnnotationIntegrity, unverifiedTraversal=unverifiedTraversal,
        )
    if not groups:
        return [], False, ()
    wasPartial = hasPartialQualifier(feature, includePcr=preserveAnnotationIntegrity)
    clipped = (
        len(groups) > 1
        or sum(len(placement.location) for placement in placements) != sum(map(len, parts))
    )
    partial = wasPartial or clipped
    operator = location.operator if isinstance(location, CompoundLocation) else "join"
    projected = []
    warnings = ()
    for group in groups:
        qualifiers = deepcopy(feature.qualifiers)
        if partial:
            if clipped and not hasPartialQualifier(feature, includePcr=False):
                old = qualifiers.get(_PARTIAL_QUALIFIER, ())
                if not isinstance(old, (tuple, list)):
                    old = (old,)
                qualifiers[_PARTIAL_QUALIFIER] = [*old, _DIGEST_PARTIAL]
            if feature.type == "CDS" and "translation" in qualifiers:
                del qualifiers["translation"]
                warnings = (f"Removed the stale translation of partial or split CDS {label!r}.",)
        mappedLocation = group[0] if len(group) == 1 else CompoundLocation(group, operator=operator)
        projected.append(SeqFeature(
            mappedLocation, type=feature.type, id=feature.id, qualifiers=qualifiers,
        ))
    return projected, partial, warnings


def mapDigestAnnotations(
    template: "SequenceRevision", output: SeqRecord, sourceSpans: "tuple[FragmentSpan, ...]",
    geometry: "DuplexGeometry", *, isUncut: bool,
    makeFeatureId: Callable[[str, int], str],
    splitUnstrandedCircularJoins: bool = True,
    sourceRecord: SeqRecord | None = None,
    preserveAnnotationIntegrity: bool = True,
    preserveCircularAnnotationContinuity: bool = True,
) -> tuple[tuple[FragmentFeatureMapping, ...], tuple[str, ...]]:
    """Keep exact local coordinates and provenance, reporting unsupported transfer.

    A stranded annotation follows retained bases on its physical strand. For
    unstranded annotations the aligned reference is used. References, external
    accessions and other record-level metadata remain on the digest source when
    bases were cut. An uncut product keeps its source metadata and locations.
    sourceRecord can supply the template copy prepared for this engine version.
    """
    source = (
        sourceRecord if sourceRecord is not None else template.toSeqRecord(preserveTraversalEvidence=True)
    )
    circularSourceLength = len(template) if template.topology == "circular" else None
    warnings = []
    if not isUncut and (set(source.annotations) - {"molecule_type", "topology"} or source.dbxrefs):
        warnings.append("Record annotations and cross-references remain on the digest source.")
    outputFeatures = []
    mappings = []
    for projection, original in zip(template.features, source.features, strict=True):
        if isUncut:
            projected, partial = [original], hasPartialQualifier(
                original, includePcr=preserveAnnotationIntegrity,
            )
        else:
            projected, partial, featureWarnings = _projectFeature(
                original, projection.label, sourceSpans, geometry,
                circularSourceLength=circularSourceLength,
                splitUnstrandedCircularJoins=splitUnstrandedCircularJoins,
                preserveAnnotationIntegrity=preserveAnnotationIntegrity,
                preserveCircularAnnotationContinuity=preserveCircularAnnotationContinuity,
                unverifiedTraversal=template.hasUnverifiedFeatureTraversal(projection),
            )
            warnings.extend(featureWarnings)
        for partIndex, feature in enumerate(projected):
            outputFeatures.append(feature)
            mappings.append(FragmentFeatureMapping(
                makeFeatureId(projection.featureId, partIndex), projection.featureId, partial,
            ))
    output.features = outputFeatures
    return tuple(mappings), tuple(warnings)
