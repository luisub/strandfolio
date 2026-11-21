"""Find restriction motifs and simulate bounded ideal complete DNA digestion."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal
from uuid import uuid4

from Bio.Data.IUPACData import ambiguous_dna_values

from strandfolio.domain.coordinates import SelectionSegment
from strandfolio.domain.fragments import (
    DNAFragment,
    UnsupportedFragmentGeometry,
    buildDigestFragments,
    validateDigestFragmentGeometry,
)
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.conversion import reverseComplementBases
from strandfolio.storage.catalogues import (
    CatalogueRelease,
    CleavagePair,
    EnzymeRecord,
    RestrictionCatalogue,
)


_IUPAC_BASES = {
    symbol: frozenset(letters)
    for symbol, letters in ambiguous_dna_values.items() if symbol != "X"
}
_MATCHING_POLICY = "iupac-definite-only"
# Version 2 splits unstranded annotations that cross a newly opened circular end.
# Version 3 preserves uncertain source annotation order on projected features.
# Version 4 checks the physical continuity of uncertain joins and retains PCR partiality.
# Version 5 distinguishes circular end breaks from retained overlaps in projected annotations.
RESTRICTION_ENGINE_VERSION = "5"
_SUPPORTED_ENGINE_VERSIONS = ("1", "2", "3", "4", RESTRICTION_ENGINE_VERSION)
_DIGEST_MODEL = "ideal-complete-double-strand"
_DUPLEX_POLICY = "assume-duplex-when-unknown"
_COORDINATE_CONVENTION = "zero-based-half-open-interbase"

# Catalogue cut geometry alone does not identify the cleavage mechanism or substrate
# requirements. The digest model supports the definitions listed here.
_VALIDATED_DEFINITIONS = {
    "ApoI": ("RAATTY", 1, 5),
    "BamHI": ("GGATCC", 1, 5),
    "BsaI": ("GGTCTC", 7, 11),
    "EcoRI": ("GAATTC", 1, 5),
    "EcoRV": ("GATATC", 3, 3),
    "HindIII": ("AAGCTT", 1, 5),
    "KpnI": ("GGTACC", 5, 1),
    "MfeI": ("CAATTG", 1, 5),
    "MspI": ("CCGG", 1, 3),
    "SacI": ("GAGCTC", 5, 1),
    "SmaI": ("CCCGGG", 3, 3),
    "XhoI": ("CTCGAG", 1, 5),
    "XmaI": ("CCCGGG", 1, 5),
}

SiteCertainty = Literal["definite", "possible"]
OccurrenceCertainty = Literal["definite", "possible", "all"]
OccurrenceFilter = Literal["all", "one", "two", "multiple", "none"]
RestrictionStatus = Literal["completed", "unsupported"]


@dataclass(frozen=True, slots=True)
class RecognitionSite:
    """Evidence for a motif orientation on the stored forward reference.

    ``motif`` is the uppercase catalogue spelling. A palindromic motif is represented once
    with orientation +1. Zero-based ``start`` is canonical. ``end`` is unwrapped
    for circular evidence, and ``sourceParts`` contains the ordered exact segments.
    A definite hit matches every possible concrete spelling of the template window.
    """

    enzymeName: str
    motif: str
    start: int
    end: int
    orientation: Literal[1, -1]
    certainty: SiteCertainty
    sourceParts: tuple[SelectionSegment, ...]


@dataclass(frozen=True, slots=True)
class RestrictionCut:
    """A paired break with all contributing sites in deterministic source order.

    Both boundaries are zero-based interbase positions. On a circle they are
    normalized individually. Signed ``bottomOffset`` retains the physical distance
    from top to bottom through the chosen traversal even when it crosses origin.
    """

    topBoundary: int
    bottomBoundary: int
    bottomOffset: int
    sites: tuple[RecognitionSite, ...]


@dataclass(frozen=True, slots=True)
class RestrictionScan:
    """Restriction site matches for a specific template revision and selected catalogue definitions."""

    template: SequenceRevision
    catalogueRelease: CatalogueRelease
    catalogueMetadata: tuple[tuple[str, str], ...]
    enzymes: tuple[EnzymeRecord, ...]
    sites: tuple[RecognitionSite, ...]
    warnings: tuple[str, ...]
    matchingPolicy: str = _MATCHING_POLICY
    status: RestrictionStatus = "completed"

    def occurrenceCount(
        self, enzymeName: str, *, certainty: OccurrenceCertainty = "definite",
    ) -> int:
        """Count distinct motif spans, merging alternatives or orientations at one span.

        A span qualifies as definite when one motif and its orientation are definite.
        ``possible`` counts spans with possible evidence only. Browsing filters use
        definite occurrences, matching the cleavage policy.
        """
        if certainty not in ("definite", "possible", "all"):
            raise ValueError(f"Unknown recognition certainty: {certainty!r}.")
        if self.status == "unsupported":
            raise ValueError("Occurrence counts require a completed restriction scan.")
        name = enzymeName.casefold()
        if not any(enzyme.name.casefold() == name for enzyme in self.enzymes):
            raise KeyError(enzymeName)
        definite = set()
        possible = set()
        for site in self.sites:
            if site.enzymeName.casefold() == name:
                spans = definite if site.certainty == "definite" else possible
                spans.add((site.start, site.end))
        if certainty == "definite":
            return len(definite)
        if certainty == "possible":
            return len(possible - definite)
        return len(definite | possible)

    def filteredEnzymes(self, occurrenceFilter: OccurrenceFilter) -> tuple[EnzymeRecord, ...]:
        """Filter by definite recognition occurrences, independent of supported cleavage."""
        if occurrenceFilter == "all":
            return self.enzymes
        if occurrenceFilter not in ("one", "two", "multiple", "none"):
            raise ValueError(f"Unknown occurrence filter: {occurrenceFilter!r}.")
        if self.status == "unsupported":
            raise ValueError("Occurrence filters require a completed restriction scan.")
        counts = {"none": 0, "one": 1, "two": 2}
        selected = []
        for enzyme in self.enzymes:
            count = self.occurrenceCount(enzyme.name)
            if occurrenceFilter == "multiple":
                matches = count > 1
            else:
                matches = count == counts[occurrenceFilter]
            if matches:
                selected.append(enzyme)
        return tuple(selected)


@dataclass(frozen=True, slots=True)
class RestrictionDigest:
    """A recorded digest with its site scan, predicted cuts, and products identified by output key.

    An unsupported reaction yields no products. It can retain valid predicted cuts
    for inspection. A supported zero-cut reaction has one uncut product.
    """

    operationId: str
    createdAt: str
    scan: RestrictionScan
    status: RestrictionStatus
    cuts: tuple[RestrictionCut, ...]
    fragments: tuple[DNAFragment, ...]
    warnings: tuple[str, ...]
    engineVersion: str = RESTRICTION_ENGINE_VERSION
    digestModel: str = _DIGEST_MODEL
    duplexPolicy: str = _DUPLEX_POLICY
    coordinateConvention: str = _COORDINATE_CONVENTION

    @property
    def template(self) -> SequenceRevision:
        return self.scan.template

    @property
    def catalogueRelease(self) -> CatalogueRelease:
        return self.scan.catalogueRelease

    @property
    def catalogueMetadata(self) -> tuple[tuple[str, str], ...]:
        return self.scan.catalogueMetadata

    @property
    def enzymes(self) -> tuple[EnzymeRecord, ...]:
        return self.scan.enzymes

    @property
    def sites(self) -> tuple[RecognitionSite, ...]:
        return self.scan.sites

    @property
    def matchingPolicy(self) -> str:
        return self.scan.matchingPolicy


def _readEnzymes(catalogue: RestrictionCatalogue, names: Iterable[str]) -> tuple[EnzymeRecord, ...]:
    if isinstance(names, str):
        raise TypeError("Supply enzyme names as a collection, such as ('EcoRI',).")
    enzymes = {}
    for name in names:
        if type(name) is not str or not name.strip():
            raise ValueError("Each selected enzyme needs a nonempty catalogue name.")
        foldedName = name.casefold()
        if foldedName not in enzymes:
            enzymes[foldedName] = catalogue.get(name)
    return tuple(enzymes.values())


def _pairedSpan(template: SequenceRevision) -> SelectionSegment:
    geometry = template.duplexGeometry
    if geometry is None:
        return 0, len(template)
    return geometry.pairedSpan.start, geometry.pairedSpan.end


def _motifs(enzyme: EnzymeRecord) -> tuple[str, ...]:
    return tuple(dict.fromkeys(enzyme.recognitionSite.upper().split("|")))


def _isIupacMotif(motif: str) -> bool:
    return bool(motif) and set(motif) <= _IUPAC_BASES.keys()


def _motifExpression(motif: str, *, definite: bool) -> str:
    classes = []
    for letter in motif:
        allowed = _IUPAC_BASES[letter]
        matches = [
            symbol for symbol, bases in _IUPAC_BASES.items()
            if (bases <= allowed if definite else bool(bases & allowed))
        ]
        classes.append(f"[{''.join(matches)}]")
    return "".join(classes)


def _orientedMotifs(motif: str) -> tuple[tuple[Literal[1, -1], str], ...]:
    reverse = reverseComplementBases(motif, "DNA")
    return ((1, motif),) if motif == reverse else ((1, motif), (-1, reverse))


def _siteParts(start: int, end: int, sequenceLength: int) -> tuple[SelectionSegment, ...]:
    if end > sequenceLength:
        return (start, sequenceLength), (0, end - sequenceLength)
    return ((start, end),)


def _matchMotif(
    enzymeName: str, motif: str, sequence: str, pairedSpan: SelectionSegment, isCircular: bool,
) -> Iterable[RecognitionSite]:
    length = len(motif)
    sequenceLength = len(sequence)
    pairedStart, pairedEnd = pairedSpan
    if length > pairedEnd - pairedStart:
        return
    window = sequence[pairedStart:pairedEnd]
    if isCircular:
        # Read the origin once to consider every distinct start on the stored circle.
        window += window[:length - 1]
    for orientation, orientedMotif in _orientedMotifs(motif):
        # A lookahead reports overlapping occurrences, including tandem repeats.
        definite = re.compile(_motifExpression(orientedMotif, definite=True))
        possible = re.compile(f"(?=({_motifExpression(orientedMotif, definite=False)}))")
        for match in possible.finditer(window):
            start = pairedStart + match.start()
            end = start + length
            certainty: SiteCertainty = "definite" if definite.fullmatch(match[1]) else "possible"
            yield RecognitionSite(
                enzymeName, motif, start, end, orientation, certainty,
                _siteParts(start, end, sequenceLength),
            )


def _siteOrder(site: RecognitionSite) -> tuple[int, int, str, int, str]:
    return site.start, site.end, site.enzymeName, -site.orientation, site.motif


def scanRestrictionSites(
    template: SequenceRevision, catalogue: RestrictionCatalogue, enzymeNames: Iterable[str],
) -> RestrictionScan:
    """Find definite and possible IUPAC motif evidence without predicting cleavage.

    Search both orientations, including circular origin crossings, case-insensitively.
    Recognition requires paired DNA. A template without stored physical geometry
    uses its entire reference as paired and reports any unknown-strand assumption.
    Enzyme names resolve through the supplied open catalogue; the first selection of
    each name fixes recipe order. A missing catalogue entry raises KeyError.
    """
    release = catalogue.release
    metadata = tuple(sorted(catalogue.metadata.items()))
    enzymes = _readEnzymes(catalogue, enzymeNames)
    return _scanDefinitions(template, release, metadata, enzymes)


def _scanDefinitions(
    template: SequenceRevision, release: CatalogueRelease,
    metadata: tuple[tuple[str, str], ...], enzymes: tuple[EnzymeRecord, ...],
) -> RestrictionScan:
    warnings = []
    if template.moleculeType != "DNA":
        warnings.append("Restriction recognition requires DNA.")
    if template.topology is None:
        warnings.append("Restriction recognition requires explicit linear or circular topology.")
    if warnings:
        return RestrictionScan(
            template, release, metadata, enzymes, (), tuple(warnings), status="unsupported",
        )

    if template.strandState == "unknown" and template.duplexGeometry is None:
        warnings.append("The template has unknown strand state. This model assumes paired DNA.")
    sequence = template.sequence.upper()
    pairedSpan = _pairedSpan(template)
    isCircular = template.topology == "circular"
    status: RestrictionStatus = "completed"
    sites = []
    for enzyme in enzymes:
        for motif in _motifs(enzyme):
            if not _isIupacMotif(motif):
                warnings.append(f"{enzyme.name} has an unsupported recognition motif {motif!r}.")
                status = "unsupported"
                continue
            if isCircular and len(motif) > len(sequence):
                warnings.append(
                    f"{enzyme.name} motif {motif} is longer than the circular template. "
                    "Recognition requires one physical traversal."
                )
                continue
            sites.extend(_matchMotif(enzyme.name, motif, sequence, pairedSpan, isCircular))
    if any(site.certainty == "possible" for site in sites):
        warnings.append(
            "Possible recognition matches remain inspectable. Cleavage uses definite hits only."
        )
    return RestrictionScan(
        template, release, metadata, enzymes, tuple(sorted(sites, key=_siteOrder)),
        tuple(warnings), status=status,
    )


def _unsupportedDefinition(enzyme: EnzymeRecord) -> str | None:
    if enzyme.issues:
        issueCodes = ", ".join(issue.code for issue in enzyme.issues)
        return f"{enzyme.name} has a flagged catalogue definition ({issueCodes})."
    if not enzyme.cleavageKnown or not enzyme.cleavagePairs:
        return f"{enzyme.name} has unknown cleavage geometry."
    if len(enzyme.cleavagePairs) != 1:
        return f"{enzyme.name} has multiple cut pairs, which this digest model does not support."
    expected = _VALIDATED_DEFINITIONS.get(enzyme.name)
    if expected is None:
        return f"{enzyme.name} cleavage is outside the validated restriction panel."

    motif = enzyme.recognitionSite.upper()
    pair = enzyme.cleavagePairs[0]
    if (len(motif) != enzyme.recognitionLength
            or (motif, pair.topOffset, pair.bottomOffset) != expected):
        return f"{enzyme.name} motif or cut offsets differ from the validated definition."
    offset = pair.bottomOffset - pair.topOffset
    expectedType = {1: "5' overhang", 0: "blunt", -1: "3' overhang"}
    polarity = (offset > 0) - (offset < 0)
    if (pair.pairNumber != 1 or pair.biopythonBottomFromEnd != pair.bottomOffset - len(motif)
            or enzyme.overhangLength != abs(offset)
            or enzyme.overhangType != expectedType[polarity]):
        return f"{enzyme.name} has inconsistent cut positions or overhang notation."
    return None


def _siteBoundaries(
    site: RecognitionSite, pair: CleavagePair, template: SequenceRevision,
) -> tuple[int, int]:
    if site.orientation == 1:
        top = site.start + pair.topOffset
        bottom = site.start + pair.bottomOffset
    else:
        top = site.end - pair.bottomOffset
        bottom = site.end - pair.topOffset
    offset = bottom - top
    if template.topology == "circular":
        length = len(template)
        # The motif and physical cut pair must fit on a single template traversal.
        if max(site.end, top, bottom) - min(site.start, top, bottom) <= length:
            return top % length, offset
    else:
        pairedStart, pairedEnd = _pairedSpan(template)
        if pairedStart <= min(top, bottom) and max(top, bottom) <= pairedEnd:
            return top, offset
    raise UnsupportedFragmentGeometry(
        f"{site.enzymeName} occurrence at [{site.start}, {site.end}) has a cut pair "
        f"({top}, {bottom}) outside the supported paired template geometry."
    )


def predictRestrictionCuts(
    scan: RestrictionScan,
) -> tuple[tuple[RestrictionCut, ...], tuple[str, ...]]:
    """Return validated predicted breaks and diagnostics without making products.

    A scan can contain recognition evidence from outside the cleavage model.
    Only definite, supported occurrences contribute breaks.
    """
    enzymePairs = {}
    problems = []
    for enzyme in scan.enzymes:
        issue = _unsupportedDefinition(enzyme)
        if issue is not None:
            problems.append(issue)
        else:
            enzymePairs[enzyme.name] = enzyme.cleavagePairs[0]
    byCut: dict[tuple[int, int], list[RecognitionSite]] = {}
    for site in scan.sites:
        if site.certainty != "definite" or site.enzymeName not in enzymePairs:
            continue
        try:
            boundaries = _siteBoundaries(site, enzymePairs[site.enzymeName], scan.template)
        except UnsupportedFragmentGeometry as error:
            problems.append(str(error))
        else:
            byCut.setdefault(boundaries, []).append(site)

    isCircular = scan.template.topology == "circular"
    cuts = []
    for (top, offset), sites in sorted(byCut.items()):
        bottom = top + offset
        if isCircular:
            bottom %= len(scan.template)
        cuts.append(RestrictionCut(top, bottom, offset, tuple(sites)))
    return tuple(cuts), tuple(problems)


def digestRestriction(
    template: SequenceRevision, catalogue: RestrictionCatalogue, enzymeNames: Iterable[str],
    *, operationId: str | None = None,
) -> RestrictionDigest:
    """Predict ideal complete products for the selected validated enzyme definitions.

    Requires at least one enzyme. Records the exact template, definitions, release,
    policy, attributable breaks, and any products. Sequence and duplex geometry are
    the biological inputs. Out-of-panel definitions and unrepresentable cut geometry
    return an unsupported result. Cleavage uses definite recognition evidence.
    """
    if operationId is None:
        operationId = str(uuid4())
    elif type(operationId) is not str or not operationId.strip() or "\x00" in operationId:
        raise ValueError("A restriction operation needs a nonblank identity.")
    createdAt = datetime.now(timezone.utc).isoformat()
    scan = scanRestrictionSites(template, catalogue, enzymeNames)
    return _digestScan(scan, operationId, createdAt, engineVersion=RESTRICTION_ENGINE_VERSION)


def rerunRestriction(result: RestrictionDigest) -> RestrictionDigest:
    """Calculate a new run from the exact recorded source and enzyme definitions."""
    scan = _scanDefinitions(
        result.template, result.catalogueRelease, result.catalogueMetadata, result.enzymes,
    )
    return _digestScan(
        scan, str(uuid4()), datetime.now(timezone.utc).isoformat(),
        engineVersion=RESTRICTION_ENGINE_VERSION,
    )


def rebuildRestrictionRun(result: RestrictionDigest) -> RestrictionDigest:
    """Rebuild the expected run using its captured inputs and recorded engine version."""
    if result.engineVersion not in _SUPPORTED_ENGINE_VERSIONS:
        raise ValueError("Unsupported recorded restriction engine version.")
    scan = _scanDefinitions(
        result.template, result.catalogueRelease, result.catalogueMetadata, result.enzymes,
    )
    if result.sites != scan.sites:
        raise ValueError("Recorded restriction sites differ from the complete template scan.")
    return _digestScan(scan, result.operationId, result.createdAt, engineVersion=result.engineVersion)


def _digestScan(
    scan: RestrictionScan, operationId: str, createdAt: str, *, engineVersion: str,
) -> RestrictionDigest:
    if not scan.enzymes:
        raise ValueError("A restriction digest needs at least one selected enzyme.")
    cuts, problems = predictRestrictionCuts(scan)
    warnings = scan.warnings + problems
    fragments: tuple[DNAFragment, ...] = ()
    status: RestrictionStatus = "unsupported"
    if scan.status == "completed" and not problems:
        breakPairs = tuple((cut.topBoundary, cut.topBoundary + cut.bottomOffset) for cut in cuts)
        try:
            fragments = buildDigestFragments(
                scan.template, breakPairs, operationId=operationId,
                splitUnstrandedCircularJoins=engineVersion != "1",
                preserveTraversalEvidence=engineVersion not in ("1", "2"),
                preserveAnnotationIntegrity=engineVersion not in ("1", "2", "3"),
                preserveCircularAnnotationContinuity=engineVersion not in ("1", "2", "3", "4"),
            )
        except UnsupportedFragmentGeometry as error:
            warnings += (str(error),)
        else:
            status = "completed"
            warnings += tuple(warning for fragment in fragments for warning in fragment.warnings)
    return RestrictionDigest(
        operationId, createdAt, scan, status, cuts, fragments, tuple(dict.fromkeys(warnings)),
        engineVersion=engineVersion,
    )


def _validateRecordedSites(scan: RestrictionScan) -> None:
    template = scan.template
    motifs = {enzyme.name: _motifs(enzyme) for enzyme in scan.enzymes}
    if not motifs or len({name.casefold() for name in motifs}) != len(scan.enzymes):
        raise ValueError("A restriction run requires distinct enzyme definitions.")
    hasPairedTemplate = template.moleculeType == "DNA" and template.topology is not None
    hasValidMotifs = all(_isIupacMotif(motif) for choices in motifs.values() for motif in choices)
    expectedStatus = "completed" if hasPairedTemplate and hasValidMotifs else "unsupported"
    if scan.status != expectedStatus or (not hasPairedTemplate and scan.sites):
        raise ValueError("Restriction scan status conflicts with its input or definitions.")
    if not scan.sites:
        return

    sequence = template.sequence
    pairedStart, pairedEnd = _pairedSpan(template)
    isCircular = template.topology == "circular"
    patterns: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}
    previousKey = None
    for site in scan.sites:
        key = _siteOrder(site)
        if previousKey is not None and key <= previousKey:
            raise ValueError("Recorded restriction sites must be distinct and in source order.")
        previousKey = key
        width = len(site.motif)
        if (site.motif not in motifs.get(site.enzymeName, ()) or not _isIupacMotif(site.motif)
                or not pairedStart <= site.start < pairedEnd or site.end != site.start + width
                or width > pairedEnd - pairedStart or (not isCircular and site.end > pairedEnd)
                or site.sourceParts != _siteParts(site.start, site.end, len(sequence))):
            raise ValueError("Recorded restriction site conflicts with its motif or source span.")

        oriented = dict(_orientedMotifs(site.motif)).get(site.orientation)
        if oriented is None:
            raise ValueError("Recorded restriction site has an invalid motif orientation.")
        if oriented not in patterns:
            patterns[oriented] = (
                re.compile(_motifExpression(oriented, definite=False)),
                re.compile(_motifExpression(oriented, definite=True)),
            )
        possible, definite = patterns[oriented]
        window = "".join(sequence[start:end] for start, end in site.sourceParts).upper()
        certainty = "definite" if definite.fullmatch(window) else "possible"
        if not possible.fullmatch(window) or site.certainty != certainty:
            raise ValueError("Recorded recognition certainty conflicts with the template bases.")


def validateRestrictionEvidence(result: RestrictionDigest) -> None:
    """Validate stored occurrences and their cut attribution against recorded inputs.

    Inspect the captured motif windows, definitions and strand boundaries to verify
    the persisted cuts and completion status without rescanning the template,
    consulting a live catalogue, or rebuilding products.
    """
    policies = (
        result.matchingPolicy, result.digestModel, result.duplexPolicy, result.coordinateConvention,
    )
    expected = (
        _MATCHING_POLICY, _DIGEST_MODEL, _DUPLEX_POLICY, _COORDINATE_CONVENTION,
    )
    if result.engineVersion not in _SUPPORTED_ENGINE_VERSIONS or policies != expected:
        raise ValueError("Unsupported recorded restriction engine version or policy.")
    _validateRecordedSites(result.scan)
    cuts, problems = predictRestrictionCuts(result.scan)
    if result.cuts != cuts:
        raise ValueError("Recorded restriction cuts differ from their recognition evidence.")
    canDigest = result.scan.status == "completed" and not problems
    if canDigest:
        breakPairs = tuple((cut.topBoundary, cut.topBoundary + cut.bottomOffset) for cut in cuts)
        try:
            validateDigestFragmentGeometry(result.template, breakPairs)
        except UnsupportedFragmentGeometry:
            canDigest = False
    if result.status == "completed" and not canDigest:
        raise ValueError("A completed restriction run contains unsupported site or cut evidence.")
    if result.status == "unsupported" and canDigest:
        if not cuts:
            raise ValueError("A supported zero-cut restriction run must retain the uncut output.")
        raise ValueError("A supported restriction run must retain its cleavage products.")
