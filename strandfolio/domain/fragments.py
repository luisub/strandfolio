"""Physical, complementary DNA products and their ordered source coordinates."""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Literal
from uuid import NAMESPACE_URL, uuid5

from Bio.Seq import Seq, reverse_complement
from Bio.SeqRecord import SeqRecord

from strandfolio.domain.fragment_annotations import FragmentFeatureMapping, mapDigestAnnotations
from strandfolio.domain.provenance import OutputReference

if TYPE_CHECKING:
    from strandfolio.domain.sequence import SequenceRevision


FragmentTopology = Literal["linear", "circular"]
EndKind = Literal["unknown", "blunt", "5-prime-overhang", "3-prime-overhang"]
FivePrimeChemistry = Literal["unknown", "phosphate"]
ThreePrimeChemistry = Literal["unknown", "hydroxyl"]


class UnsupportedFragmentGeometry(ValueError):
    """The proposed strand cuts cannot be represented as complementary duplexes."""


@dataclass(frozen=True, slots=True)
class FragmentSpan:
    """A nonempty, zero-based half-open nucleotide or alignment interval."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if (type(self.start) is not int or type(self.end) is not int
                or not 0 <= self.start < self.end):
            raise ValueError("A fragment span requires nonnegative, increasing integer boundaries.")

    def __len__(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class FragmentEnd:
    """A terminus with a physical overhang in its own strand's 5′→3′ order."""

    kind: EndKind
    overhangSequence: str = ""
    protrudingStrand: int | None = None
    fivePrimeChemistry: FivePrimeChemistry = "unknown"
    threePrimeChemistry: ThreePrimeChemistry = "unknown"

    def __post_init__(self) -> None:
        if self.kind not in ("unknown", "blunt", "5-prime-overhang", "3-prime-overhang"):
            raise ValueError("Unknown fragment end kind.")
        if type(self.overhangSequence) is not str:
            raise ValueError("An overhang sequence must be a string.")
        if self.kind in ("unknown", "blunt"):
            if self.overhangSequence or self.protrudingStrand is not None:
                raise ValueError("A blunt or unknown end has no modeled protruding strand.")
        elif (
            not self.overhangSequence
            or type(self.protrudingStrand) is not int
            or self.protrudingStrand not in (1, -1)
        ):
            raise ValueError("A sticky end requires physical bases and a strand of +1 or -1.")
        if self.fivePrimeChemistry not in ("unknown", "phosphate"):
            raise ValueError("Unsupported 5′ end chemistry.")
        if self.threePrimeChemistry not in ("unknown", "hydroxyl"):
            raise ValueError("Unsupported 3′ end chemistry.")


@dataclass(frozen=True, slots=True)
class DuplexGeometry:
    """Occupancy in a full forward-oriented aligned reference.

    Each strand is contiguous and their intersection must contain paired bases.
    End values describe linear termini. A circular geometry has no termini.
    """

    forwardSpan: FragmentSpan
    reverseSpan: FragmentSpan
    leftEnd: FragmentEnd | None
    rightEnd: FragmentEnd | None

    def __post_init__(self) -> None:
        if not all(isinstance(span, FragmentSpan) for span in (self.forwardSpan, self.reverseSpan)):
            raise ValueError("Duplex geometry requires a span for each physical strand.")
        if min(self.forwardSpan.start, self.reverseSpan.start) != 0:
            raise ValueError("Duplex geometry starts at the first occupied reference column.")
        if max(self.forwardSpan.start, self.reverseSpan.start) >= min(
            self.forwardSpan.end, self.reverseSpan.end,
        ):
            raise ValueError("Duplex strands must share at least one paired reference column.")
        if any(end is not None and not isinstance(end, FragmentEnd)
               for end in (self.leftEnd, self.rightEnd)):
            raise ValueError("Each duplex terminus must be a fragment end.")

    @property
    def pairedSpan(self) -> FragmentSpan:
        """Return the intersection of the two physical strands in reference columns."""
        return FragmentSpan(
            max(self.forwardSpan.start, self.reverseSpan.start),
            min(self.forwardSpan.end, self.reverseSpan.end),
        )

    def validateAgainst(self, referenceSequence: str, topology: str | None) -> None:
        """Reject ends, topology or strand occupancy inconsistent with the reference."""
        if max(self.forwardSpan.end, self.reverseSpan.end) != len(referenceSequence):
            raise ValueError("Duplex geometry must cover the aligned reference exactly.")
        if topology == "circular":
            if (self.forwardSpan != self.reverseSpan or self.leftEnd is not None
                    or self.rightEnd is not None):
                raise ValueError("A circular duplex must be fully paired and have no termini.")
        elif topology == "linear":
            self._validateEnd(referenceSequence, self.leftEnd, isLeft=True)
            self._validateEnd(referenceSequence, self.rightEnd, isLeft=False)
        else:
            raise ValueError("Duplex geometry requires explicit linear or circular topology.")

    def _validateEnd(
        self, referenceSequence: str, end: FragmentEnd | None, *, isLeft: bool,
    ) -> None:
        if end is None:
            raise ValueError("A linear duplex requires both termini, including unknown ends.")
        expected = _endForSpans(
            referenceSequence, self.forwardSpan, self.reverseSpan, isLeft=isLeft,
        )
        if end.kind == "unknown" and expected.kind == "blunt":
            return
        if (end.kind, end.overhangSequence, end.protrudingStrand) != (
            expected.kind, expected.overhangSequence, expected.protrudingStrand,
        ):
            raise ValueError("The end does not match the physical strand occupancy and bases.")


@dataclass(frozen=True, slots=True)
class DNAFragment:
    """A digest output keyed within its run and bound to a candidate revision.

    Source spans follow the aligned product reference in order. On an opened
    circle, source positions in staggered ends may contribute to both termini.
    """

    operationId: str
    outputKey: str
    sourceRevisionId: str
    sourceSpans: tuple[FragmentSpan, ...]
    revision: "SequenceRevision"
    warnings: tuple[str, ...] = ()
    featureMappings: tuple[FragmentFeatureMapping, ...] = ()

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value for value in (
            self.operationId, self.outputKey, self.sourceRevisionId,
        )):
            raise ValueError("A DNA fragment requires operation, output and source identities.")
        if (type(self.sourceSpans) is not tuple or not self.sourceSpans
                or any(not isinstance(span, FragmentSpan) for span in self.sourceSpans)):
            raise ValueError("A DNA fragment requires ordered nonempty source spans.")
        if sum(map(len, self.sourceSpans)) != len(self.revision):
            raise ValueError("The source spans must account for all aligned reference columns.")
        if self.revision.moleculeType != "DNA" or self.revision.duplexGeometry is None:
            raise ValueError("A DNA fragment requires a DNA revision with duplex geometry.")
        if self.revision.outputReference != OutputReference(self.operationId, self.outputKey):
            raise ValueError("The candidate revision must identify this operation output.")
        if (type(self.warnings) is not tuple
                or any(type(warning) is not str for warning in self.warnings)):
            raise ValueError("Fragment warnings must be an immutable tuple of strings.")
        if (type(self.featureMappings) is not tuple
                or any(not isinstance(mapping, FragmentFeatureMapping)
                       for mapping in self.featureMappings)
                or tuple(mapping.featureId for mapping in self.featureMappings)
                != tuple(feature.featureId for feature in self.revision.features)):
            raise ValueError("Feature mappings must account for each candidate feature in order.")

    @property
    def geometry(self) -> DuplexGeometry:
        """Return the geometry recorded in the candidate revision."""
        return self.revision.duplexGeometry

    @property
    def topology(self) -> FragmentTopology:
        return self.revision.topology

    @property
    def referenceSequence(self) -> str:
        """Return the full aligned reference in forward 5′→3′ orientation."""
        return self.revision.sequence

    @property
    def forwardSpan(self) -> FragmentSpan:
        return self.geometry.forwardSpan

    @property
    def reverseSpan(self) -> FragmentSpan:
        return self.geometry.reverseSpan

    @property
    def forwardSequence(self) -> str:
        """Return only the physical forward strand in 5′→3′ order."""
        return self.referenceSequence[self.forwardSpan.start:self.forwardSpan.end]

    @property
    def reverseSequence(self) -> str:
        """Return only the physical reverse strand in its own 5′→3′ order."""
        bases = self.referenceSequence[self.reverseSpan.start:self.reverseSpan.end]
        return reverse_complement(bases)

    @property
    def leftEnd(self) -> FragmentEnd | None:
        return self.geometry.leftEnd

    @property
    def rightEnd(self) -> FragmentEnd | None:
        return self.geometry.rightEnd

    @property
    def referenceLength(self) -> int:
        return len(self.revision)

    @property
    def forwardLength(self) -> int:
        return len(self.forwardSpan)

    @property
    def reverseLength(self) -> int:
        return len(self.reverseSpan)

    @property
    def pairedLength(self) -> int:
        return len(self.geometry.pairedSpan)

    @property
    def sourceOriginOffsets(self) -> tuple[int, ...]:
        """Return all product reference indices corresponding to source index zero."""
        origins = []
        offset = 0
        for span in self.sourceSpans:
            if span.start == 0:
                origins.append(offset)
            offset += len(span)
        return tuple(origins)


def _endForSpans(
    sequence: str, forward: FragmentSpan, reverse: FragmentSpan, *, isLeft: bool,
) -> FragmentEnd:
    forwardBoundary, reverseBoundary = (
        (forward.start, reverse.start) if isLeft else (forward.end, reverse.end)
    )
    if forwardBoundary == reverseBoundary:
        return FragmentEnd("blunt", fivePrimeChemistry="phosphate", threePrimeChemistry="hydroxyl")
    lower, upper = sorted((forwardBoundary, reverseBoundary))
    protrudingStrand = 1 if (forwardBoundary < reverseBoundary) == isLeft else -1
    kind = "5-prime-overhang" if (protrudingStrand == 1) == isLeft else "3-prime-overhang"
    bases = sequence[lower:upper]
    if protrudingStrand == -1:
        bases = reverse_complement(bases)
    return FragmentEnd(kind, bases, protrudingStrand, "phosphate", "hydroxyl")


def _sourceSpans(
    start: int, end: int, length: int, *, isCircular: bool,
) -> tuple[FragmentSpan, ...]:
    if not isCircular:
        return (FragmentSpan(start, end),)
    spans = []
    while start < end:
        normalized = start % length
        spanLength = min(end - start, length - normalized)
        spans.append(FragmentSpan(normalized, normalized + spanLength))
        start += spanLength
    return tuple(spans)


def _derivedId(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps(["vibegene.digest", *parts], ensure_ascii=True)))


def _createFragment(
    template: "SequenceRevision", source: SeqRecord, operationId: str, ordinal: int,
    sourceSpans: tuple[FragmentSpan, ...], reference: str, geometry: DuplexGeometry, *,
    splitUnstrandedCircularJoins: bool, preserveAnnotationIntegrity: bool,
    preserveCircularAnnotationContinuity: bool, isUncut: bool = False,
) -> DNAFragment:
    from strandfolio.domain.sequence import SequenceRevision

    outputKey = f"fragment-{ordinal}"
    recordId = _derivedId(operationId, outputKey, "record")
    if isUncut:
        record = source
        if template.moleculeDeclaration is None:
            del record.annotations["molecule_type"]
    else:
        record = SeqRecord(
            Seq(reference), id=outputKey, name=f"Fragment {ordinal}",
            description=f"Restriction fragment of {template.name}",
            annotations={"molecule_type": "ds-DNA", "topology": "linear"},
        )
    featureMappings, warnings = mapDigestAnnotations(
        template, record, sourceSpans, geometry, isUncut=isUncut, sourceRecord=source,
        splitUnstrandedCircularJoins=splitUnstrandedCircularJoins,
        preserveAnnotationIntegrity=preserveAnnotationIntegrity,
        preserveCircularAnnotationContinuity=preserveCircularAnnotationContinuity,
        makeFeatureId=lambda sourceId, part: _derivedId(recordId, "feature", sourceId, str(part)),
    )
    revision = SequenceRevision.fromStoredRecord(
        record, moleculeType="DNA", recordId=recordId,
        revisionId=_derivedId(operationId, outputKey, "revision"),
        featureIds=tuple(mapping.featureId for mapping in featureMappings),
        duplexGeometry=geometry, outputReference=OutputReference(operationId, outputKey),
    )
    return DNAFragment(
        operationId, outputKey, template.revisionId, sourceSpans, revision,
        warnings, featureMappings,
    )


def _digestSourceGeometry(template: "SequenceRevision") -> DuplexGeometry:
    if template.moleculeType != "DNA" or template.topology not in ("linear", "circular"):
        raise UnsupportedFragmentGeometry("A fragment template must be DNA with explicit topology.")
    if template.duplexGeometry is not None:
        return template.duplexGeometry
    wholeSpan = FragmentSpan(0, len(template))
    terminal = None if template.topology == "circular" else FragmentEnd("unknown")
    return DuplexGeometry(wholeSpan, wholeSpan, terminal, terminal)


def _supportedBoundaries(
    template: "SequenceRevision", sourceGeometry: DuplexGeometry,
    cuts: tuple[tuple[int, int], ...],
) -> tuple[tuple[tuple[int, int], bool], ...]:
    circular = template.topology == "circular"
    paired = sourceGeometry.pairedSpan
    for forward, reverse in cuts:
        if type(forward) is not int or type(reverse) is not int:
            raise UnsupportedFragmentGeometry("Strand cuts must use integer interbase coordinates.")
        if circular:
            if not 0 <= forward < len(template) or abs(reverse - forward) >= len(template):
                raise UnsupportedFragmentGeometry("Circular cuts must fit within one template turn.")
        elif not paired.start <= min(forward, reverse) <= max(forward, reverse) <= paired.end:
            raise UnsupportedFragmentGeometry("A strand cut lies outside the paired template.")
    boundaries = tuple((cut, True) for cut in sorted(cuts))
    if circular and cuts:
        forward, reverse = boundaries[0][0]
        boundaries = (*boundaries, ((forward + len(template), reverse + len(template)), True))
    else:
        forward, reverse = sourceGeometry.forwardSpan, sourceGeometry.reverseSpan
        boundaries = (
            ((forward.start, reverse.start), False), *boundaries, ((forward.end, reverse.end), False),
        )
    for (left, _), (right, _) in zip(boundaries, boundaries[1:]):
        if max(left) >= min(right):
            raise UnsupportedFragmentGeometry(
                "Overlapping strand cuts or a terminal cut leave a product without paired bases."
            )
    return boundaries


def validateDigestFragmentGeometry(
    template: "SequenceRevision", cuts: tuple[tuple[int, int], ...],
) -> None:
    """Raise UnsupportedFragmentGeometry if paired boundaries cannot partition the duplex.

    Cuts are interbase forward and reverse coordinates. On a circle, the forward
    cut is normalized and the reverse cut retains its signed stagger. Validation
    uses only the template topology and strand spans.
    """
    _supportedBoundaries(template, _digestSourceGeometry(template), cuts)


def buildDigestFragments(
    template: "SequenceRevision", cuts: tuple[tuple[int, int], ...], *, operationId: str,
    splitUnstrandedCircularJoins: bool = True, preserveTraversalEvidence: bool = True,
    preserveAnnotationIntegrity: bool = True,
    preserveCircularAnnotationContinuity: bool = True,
) -> tuple[DNAFragment, ...]:
    """Build ordered complementary duplex products from distinct paired breaks.

    The caller supplies conventional pairs of interbase cuts up to one stagger
    per break. A circular pair uses a forward boundary normalized to [0, N)
    and a reverse boundary with the signed stagger preserved on the same turn.
    Source termini retain their geometry and chemistry when known. New cleavage
    ends use the idealized 5′ phosphate and 3′ hydroxyl chemistry of the digest.
    Disabling unstranded join splitting preserves engine 1 annotation projection.
    Engines 1 and 2 copied annotations without adding SnapGene traversal evidence.
    Engine 4 preserves inherited PCR partiality and uses physical adjacency for uncertain joins.
    Engine 5 distinguishes cuts within a circular join from an intact overlap.

    Raises:
        UnsupportedFragmentGeometry: Substrate or overlapping/tied cuts would
            require invalid fragments, internal gaps or unpaired-only products.
    """
    sourceGeometry = _digestSourceGeometry(template)
    boundaries = _supportedBoundaries(template, sourceGeometry, cuts)
    isCircular = template.topology == "circular"
    sequence = template.sequence
    source = template.toSeqRecord(preserveTraversalEvidence=preserveTraversalEvidence)
    if not cuts:
        return (_createFragment(
            template, source, operationId, 1, (FragmentSpan(0, len(template)),),
            sequence, sourceGeometry,
            splitUnstrandedCircularJoins=splitUnstrandedCircularJoins,
            preserveAnnotationIntegrity=preserveAnnotationIntegrity,
            preserveCircularAnnotationContinuity=preserveCircularAnnotationContinuity, isUncut=True,
        ),)

    products = []
    for ordinal, (left, right) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        (forwardStart, reverseStart), leftIsCut = left
        (forwardEnd, reverseEnd), rightIsCut = right
        start, end = min(forwardStart, reverseStart), max(forwardEnd, reverseEnd)
        sourceSpans = _sourceSpans(start, end, len(template), isCircular=isCircular)
        reference = "".join(sequence[span.start:span.end] for span in sourceSpans)
        forward = FragmentSpan(forwardStart - start, forwardEnd - start)
        reverse = FragmentSpan(reverseStart - start, reverseEnd - start)
        geometry = DuplexGeometry(
            forward, reverse,
            _endForSpans(reference, forward, reverse, isLeft=True)
            if leftIsCut else sourceGeometry.leftEnd,
            _endForSpans(reference, forward, reverse, isLeft=False)
            if rightIsCut else sourceGeometry.rightEnd,
        )
        products.append(_createFragment(
            template, source, operationId, ordinal, sourceSpans, reference, geometry,
            splitUnstrandedCircularJoins=splitUnstrandedCircularJoins,
            preserveAnnotationIntegrity=preserveAnnotationIntegrity,
            preserveCircularAnnotationContinuity=preserveCircularAnnotationContinuity,
        ))
    return tuple(products)
