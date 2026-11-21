"""Shared geometry for grouped sequence rows, layered tracks and coordinate overviews."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from math import ceil

from strandfolio.domain.features import SequenceFeature, isFeatureColor
from strandfolio.domain.sequence import MoleculeType
from strandfolio.operations.conversion import CdsTranslation, CodonPlacement, FrameTranslation
from strandfolio.operations.restriction import RecognitionSite, RestrictionCut


FEATURE_COLORS: dict[str, str] = {
    "CDS": "#307f77",
    "promoter": "#87568c",
    "enhancer": "#43704f",
    "regulatory": "#43704f",
    "rep_origin": "#396c91",
    "polyA_signal": "#b43468",
    "terminator": "#b43468",
    "protein_bind": "#226880",
    "primer_bind": "#74558a",
    "misc_feature": "#b88540",
}
DEFAULT_FEATURE_COLOR = "#58658a"


def featureColor(featureType: str) -> str:
    """Return a consistent presentation color for a feature type."""
    return FEATURE_COLORS.get(featureType, DEFAULT_FEATURE_COLOR)


def annotationColor(feature: SequenceFeature) -> str:
    """Prefer the local override, then ApE's color for the annotated strand."""
    if feature.color:
        return feature.color
    qualifiers = dict(feature.qualifiers)
    forward = next(iter(qualifiers.get("ApEinfo_fwdcolor", ())), None)
    reverse = next(iter(qualifiers.get("ApEinfo_revcolor", ())), None)
    imported = {1: forward, -1: reverse}.get(feature.strand, forward if forward == reverse else None)
    return imported if isFeatureColor(imported) else featureColor(feature.type)


@dataclass(frozen=True, slots=True)
class LayerOptions:
    """View-only visibility settings for sequence and map layers."""

    showFeatures: bool = True
    showPrimers: bool = False
    showComplement: bool = True
    showTranslations: bool = True


@dataclass(frozen=True, slots=True)
class RowFeatureSpan:
    """One exact feature segment clipped to a single sequence row and assigned a lane."""

    featureId: str
    label: str
    featureType: str
    partIndex: int
    partCount: int
    start: int
    end: int
    fullStart: int
    fullEnd: int
    strand: int | None
    lane: int
    color: str

    @property
    def startsHere(self) -> bool:
        return self.start == self.fullStart

    @property
    def endsHere(self) -> bool:
        return self.end == self.fullEnd


@dataclass(frozen=True, slots=True)
class RowCdsTrack:
    """Annotated CDS or free-frame codons intersecting a single sequence row."""

    featureId: str | None
    label: str
    strand: int | None
    color: str
    codons: tuple[CodonPlacement, ...]
    translTable: int
    annotatedTable: int | None
    frame: int | None


@dataclass(frozen=True, slots=True)
class RowRestrictionSpan:
    """A recognition motif clipped to this row, with room for its enzyme label."""

    site: RecognitionSite
    start: int
    end: int
    lane: int


@dataclass(frozen=True, slots=True)
class RowRestrictionMark:
    """A predicted interbase cut on one of the two stored-coordinate strands."""

    boundary: int
    isTopStrand: bool
    enzymeNames: frozenset[str]


@dataclass(frozen=True, slots=True)
class SequenceRowPlan:
    """Vertical offsets and packed track items for one row."""

    rowIndex: int
    rowStart: int
    rowEnd: int
    rowHeight: int
    featureLaneCount: int
    featuresTop: int
    restrictionTop: int
    forwardY: int
    complementY: int | None
    translationTop: int
    features: tuple[RowFeatureSpan, ...]
    cdsTracks: tuple[RowCdsTrack, ...]
    restrictionSpans: tuple[RowRestrictionSpan, ...]
    restrictionMarks: tuple[RowRestrictionMark, ...]

    def clippedTo(self, start: int, end: int) -> "SequenceRowPlan":
        """Clip tracks to an included slice while retaining the native row grid and lanes."""
        if not self.rowStart <= start < end <= self.rowEnd:
            raise ValueError("Sequence detail bounds must lie within the row.")
        if start == self.rowStart and end == self.rowEnd:
            return self
        features = tuple(
            replace(span, start=max(start, span.start), end=min(end, span.end))
            for span in self.features if span.start < end and span.end > start
        )
        restrictionSpans = tuple(
            replace(span, start=max(start, span.start), end=min(end, span.end))
            for span in self.restrictionSpans if span.start < end and span.end > start
        )
        return replace(
            self, features=features, restrictionSpans=restrictionSpans,
            restrictionMarks=tuple(
                mark for mark in self.restrictionMarks if start <= mark.boundary <= end
            ),
        )


@dataclass(frozen=True, slots=True)
class SequenceLayout:
    """Map interbase boundaries to grouped monospace cells, independent of Qt."""

    viewportWidth: float
    characterWidth: float
    gutter: int = 82
    strandHeight: int = 18

    def __post_init__(self) -> None:
        if self.viewportWidth <= 0 or self.characterWidth <= 0 or self.strandHeight <= 0:
            raise ValueError("Layout dimensions must be positive.")

    @property
    def basesPerRow(self) -> int:
        available = max(0, self.viewportWidth - self.gutter - 24)
        groups = max(1, min(20, int(available // (11 * self.characterWidth))))
        return groups * 10

    def baseX(self, column: int) -> float:
        """Return the left edge of a base, with a one-cell gap every ten bases."""
        return self.gutter + (column + column // 10) * self.characterWidth

    def boundaryAt(self, x: float, row: int, sequenceLength: int) -> int:
        """Snap a pointer to the nearest interbase boundary, including row ends."""
        row = max(0, row)
        remaining = max(0, min(self.basesPerRow, sequenceLength - row * self.basesPerRow))
        column = min(range(remaining + 1), key=lambda c: abs(self.baseX(c) - x))
        return min(sequenceLength, row * self.basesPerRow + column)

    def restrictionRight(self, rowStart: int, start: int, end: int, name: str) -> float:
        """Fit a clickable enzyme label against the row's right edge."""
        left = self.baseX(start - rowStart)
        motifRight = self.baseX(end - rowStart - 1) + self.characterWidth
        return max(motifRight, min(self.viewportWidth - 4.0, left + len(name) * 7 + 8))

    def rowCount(self, sequenceLength: int) -> int:
        return max(1, ceil(sequenceLength / self.basesPerRow))

    def rowsCovering(self, start: int, end: int) -> range:
        """Return the native row indexes intersecting a nonempty half-open interval."""
        if not 0 <= start < end:
            raise ValueError("Sequence detail needs a nonempty interval.")
        width = self.basesPerRow
        return range(start // width, (end - 1) // width + 1)

    def visibleInterval(self, firstRow: int, rowCount: int, sequenceLength: int) -> tuple[int, int]:
        """Return the half-open sequence interval covered by visible rows."""
        start = min(sequenceLength, max(0, firstRow) * self.basesPerRow)
        end = min(sequenceLength, start + max(0, rowCount) * self.basesPerRow)
        return start, end


class SequenceRowIndex:
    """Index variable-height layered rows with an O(1) fast path for plain rows."""

    _TOP_PAD = 17
    _BOTTOM_PAD = 6
    FEATURE_STEP = 16
    _LANE_GAP = 2
    TRANSLATION_STEP = 16
    RESTRICTION_STEP = 17

    def __init__(
        self,
        sequenceLength: int,
        layout: SequenceLayout,
        moleculeType: MoleculeType | None,
        features: tuple[SequenceFeature, ...],
        cdsTranslations: tuple[CdsTranslation, ...],
        options: LayerOptions,
        *,
        frameTranslation: FrameTranslation | None = None,
        restrictionSites: tuple[RecognitionSite, ...] = (),
        restrictionCuts: tuple[RestrictionCut, ...] = (),
        isCircular: bool = False,
    ) -> None:
        self.sequenceLength = max(0, sequenceLength)
        self.layout = layout
        self.moleculeType = moleculeType
        self.options = options
        self.rowCount = layout.rowCount(max(1, self.sequenceLength))
        self.hasComplement = options.showComplement and moleculeType in ("DNA", "RNA")
        self.frameTranslation = frameTranslation if (
            options.showTranslations and frameTranslation is not None and frameTranslation.isValid
        ) else None
        self._baseRowHeight = (
            self._TOP_PAD
            + layout.strandHeight * (2 if self.hasComplement else 1)
            + (self.TRANSLATION_STEP if self.frameTranslation is not None else 0)
            + self._BOTTOM_PAD
        )

        self._rowFeatures: dict[int, tuple[RowFeatureSpan, ...]] = {}
        self._rowLaneCounts: dict[int, int] = {}
        self._rowCds: dict[int, tuple[RowCdsTrack, ...]] = {}
        self._rowRestrictionSpans: dict[int, tuple[RowRestrictionSpan, ...]] = {}
        self._rowRestrictionMarks: dict[int, tuple[RowRestrictionMark, ...]] = {}
        self._restrictionLaneCounts: dict[int, int] = {}

        if self.sequenceLength > 0:
            self._indexFeatures(features)
            if frameTranslation is None:
                self._indexTranslations(cdsTranslations)
            self._indexRestrictionSites(restrictionSites, restrictionCuts, isCircular)

        self.isUniform = not (self._rowFeatures or self._rowCds or self._rowRestrictionSpans)
        self._rowTops: list[int] = []
        if self.isUniform:
            self.totalHeight = self.rowCount * self._baseRowHeight
        else:
            tops = [0]
            running = 0
            for row in range(self.rowCount):
                running += self.rowHeight(row)
                tops.append(running)
            self._rowTops = tops
            self.totalHeight = running

    @property
    def hasRestrictionSites(self) -> bool:
        return bool(self._rowRestrictionSpans)

    @property
    def hasRestrictionCuts(self) -> bool:
        return bool(self._rowRestrictionMarks)

    @property
    def hasCdsTranslations(self) -> bool:
        return bool(self._rowCds)

    def _indexFeatures(self, features: tuple[SequenceFeature, ...]) -> None:
        if not features or (not self.options.showFeatures and not self.options.showPrimers):
            return
        basesPerRow = self.layout.basesPerRow
        byRow: dict[int, list[
            tuple[int, int, SequenceFeature, int, tuple[int, int, int], int, int, int | None]
        ]] = {}
        for featureIndex, feature in enumerate(features):
            isPrimer = feature.type == "primer_bind"
            if isPrimer and not self.options.showPrimers:
                continue
            if not isPrimer and not self.options.showFeatures:
                continue
            totalSpan = sum(
                part.end - part.start
                for part in feature.parts
                if part.isSelectable and part.start is not None and part.end is not None
            )
            priority = (1 if isPrimer else 0, -totalSpan, featureIndex)
            for partIndex, part in enumerate(feature.parts):
                if not part.isSelectable or part.start is None or part.end is None:
                    continue
                firstRow = part.start // basesPerRow
                lastRow = (part.end - 1) // basesPerRow
                for row in range(firstRow, lastRow + 1):
                    rowStart = row * basesPerRow
                    rowEnd = min(self.sequenceLength, rowStart + basesPerRow)
                    segmentStart = max(rowStart, part.start)
                    segmentEnd = min(rowEnd, part.end)
                    if segmentStart < segmentEnd:
                        byRow.setdefault(row, []).append(
                            (segmentStart, segmentEnd, feature, partIndex, priority,
                             part.start, part.end, part.strand)
                        )
        for row, entries in byRow.items():
            # Sweep from left to right; imported order and length must not add false overlaps.
            entries.sort(key=lambda item: (item[0], item[4], -item[1]))
            laneEnds: list[int] = []
            spans: list[RowFeatureSpan] = []
            for (
                segmentStart, segmentEnd, feature, partIndex, _, fullStart, fullEnd, partStrand
            ) in entries:
                assigned = -1
                for laneIndex, occupiedUntil in enumerate(laneEnds):
                    if occupiedUntil <= segmentStart:
                        laneEnds[laneIndex] = segmentEnd
                        assigned = laneIndex
                        break
                if assigned < 0:
                    assigned = len(laneEnds)
                    laneEnds.append(segmentEnd)
                spans.append(
                    RowFeatureSpan(
                        featureId=feature.featureId,
                        label=feature.label,
                        featureType=feature.type,
                        partIndex=partIndex,
                        partCount=len(feature.parts),
                        start=segmentStart,
                        end=segmentEnd,
                        fullStart=fullStart,
                        fullEnd=fullEnd,
                        strand=partStrand if partStrand is not None else feature.strand,
                        lane=assigned,
                        color=annotationColor(feature),
                    )
                )
            self._rowFeatures[row] = tuple(spans)
            self._rowLaneCounts[row] = len(laneEnds)

    def _indexTranslations(self, translations: tuple[CdsTranslation, ...]) -> None:
        if not self.options.showTranslations:
            return
        basesPerRow = self.layout.basesPerRow
        byRow: dict[int, list[RowCdsTrack]] = {}
        for translation in translations:
            codonsByRow: dict[int, list[CodonPlacement]] = {}
            for codon in translation.codons:
                rowsTouched = {pos // basesPerRow for pos in codon.positions}
                for row in rowsTouched:
                    codonsByRow.setdefault(row, []).append(codon)
            for row, codons in codonsByRow.items():
                track = RowCdsTrack(
                    featureId=translation.featureId,
                    label=translation.label,
                    strand=translation.strand,
                    color=featureColor("CDS"),
                    codons=tuple(codons),
                    translTable=translation.translTable,
                    annotatedTable=translation.annotatedTable,
                    frame=None,
                )
                byRow.setdefault(row, []).append(track)
        self._rowCds = {row: tuple(tracks) for row, tracks in byRow.items()}

    def _translationTracks(self, row: int, start: int, end: int) -> tuple[RowCdsTrack, ...]:
        frame = self.frameTranslation
        if frame is None:
            return self._rowCds.get(row, ())
        # A contiguous codon can intersect this row while its center is in an adjacent row.
        codons = frame.codonsInRange(max(0, start - 1), min(self.sequenceLength, end + 1))
        if not codons:
            return ()
        return (RowCdsTrack(
            featureId=None,
            label=f"Frame {frame.frame:+d}",
            strand=frame.strand,
            color=featureColor("CDS"),
            codons=codons,
            translTable=frame.translTable,
            annotatedTable=None,
            frame=frame.frame,
        ),)

    def _indexRestrictionSites(
        self, sites: tuple[RecognitionSite, ...], cuts: tuple[RestrictionCut, ...],
        isCircular: bool,
    ) -> None:
        width = self.layout.basesPerRow
        byRow: dict[int, list[tuple[int, int, RecognitionSite]]] = {}
        for site in sites:
            for start, end in site.sourceParts:
                for row in range(start // width, (end - 1) // width + 1):
                    rowStart = row * width
                    byRow.setdefault(row, []).append((max(start, rowStart), min(end, rowStart + width), site))
        for row, pieces in byRow.items():
            spans = []
            laneRights: list[float] = []
            rowStart = row * width
            for start, end, site in sorted(pieces, key=lambda piece: (piece[0], piece[1])):
                left = self.layout.baseX(start - rowStart)
                right = self.layout.restrictionRight(rowStart, start, end, site.enzymeName)
                lane = next((i for i, used in enumerate(laneRights) if used + 3 <= left), len(laneRights))
                if lane == len(laneRights):
                    laneRights.append(right)
                else:
                    laneRights[lane] = right
                spans.append(RowRestrictionSpan(site, start, end, lane))
            self._rowRestrictionSpans[row] = tuple(spans)
            self._restrictionLaneCounts[row] = len(laneRights)

        displayed = frozenset(sites)
        marksByRow: dict[int, list[RowRestrictionMark]] = {}
        for cut in cuts:
            names = frozenset(site.enzymeName for site in cut.sites if site in displayed)
            if not names:
                continue
            for boundary, isTopStrand in ((cut.topBoundary, True), (cut.bottomBoundary, False)):
                positions = (0, self.sequenceLength) if isCircular and boundary == 0 else (boundary,)
                for position in positions:
                    mark = RowRestrictionMark(position, isTopStrand, names)
                    first = max(0, (position - 1) // width)
                    last = min(self.rowCount - 1, position // width)
                    for row in range(first, last + 1):
                        marksByRow.setdefault(row, []).append(mark)
        self._rowRestrictionMarks = {row: tuple(marks) for row, marks in marksByRow.items()}

    def rowHeight(self, row: int) -> int:
        if self.isUniform:
            return self._baseRowHeight
        lanes = self._rowLaneCounts.get(row, 0)
        cdsCount = len(self._rowCds.get(row, ()))
        lanesH = (lanes * self.FEATURE_STEP + self._LANE_GAP) if lanes else 0
        cdsH = cdsCount * self.TRANSLATION_STEP
        restrictionH = self._restrictionLaneCounts.get(row, 0) * self.RESTRICTION_STEP
        return self._baseRowHeight + lanesH + cdsH + restrictionH

    def rowTop(self, row: int) -> int:
        clamped = max(0, min(self.rowCount, row))
        if self.isUniform:
            return clamped * self._baseRowHeight
        return self._rowTops[clamped]

    def rowAtY(self, y: float) -> int:
        if y <= 0:
            return 0
        if self.isUniform:
            return min(self.rowCount - 1, int(y // self._baseRowHeight))
        index = bisect_right(self._rowTops, y) - 1
        return max(0, min(self.rowCount - 1, index))

    def rowPlan(self, row: int) -> SequenceRowPlan:
        basesPerRow = self.layout.basesPerRow
        rowStart = min(self.sequenceLength, max(0, row) * basesPerRow)
        rowEnd = min(self.sequenceLength, rowStart + basesPerRow)
        lanes = self._rowLaneCounts.get(row, 0)
        lanesH = (lanes * self.FEATURE_STEP + self._LANE_GAP) if lanes else 0
        restrictionTop = self._TOP_PAD + lanesH
        forwardY = restrictionTop + self._restrictionLaneCounts.get(row, 0) * self.RESTRICTION_STEP
        complementY = (forwardY + self.layout.strandHeight) if self.hasComplement else None
        translationTop = forwardY + self.layout.strandHeight * (2 if self.hasComplement else 1)
        return SequenceRowPlan(
            rowIndex=row,
            rowStart=rowStart,
            rowEnd=rowEnd,
            rowHeight=self.rowHeight(row),
            featureLaneCount=lanes,
            featuresTop=self._TOP_PAD,
            restrictionTop=restrictionTop,
            forwardY=forwardY,
            complementY=complementY,
            translationTop=translationTop,
            features=self._rowFeatures.get(row, ()),
            cdsTracks=self._translationTracks(row, rowStart, rowEnd),
            restrictionSpans=self._rowRestrictionSpans.get(row, ()),
            restrictionMarks=self._rowRestrictionMarks.get(row, ()),
        )


@dataclass(frozen=True, slots=True)
class OverviewLayout:
    """Map the stored sequence to a linear coordinate axis regardless of topology."""

    sequenceLength: int
    left: float
    width: float

    def __post_init__(self) -> None:
        if self.sequenceLength <= 0 or self.width <= 0:
            raise ValueError("Overview length and width must be positive.")

    def positionX(self, boundary: int) -> float:
        return self.left + self.width * max(0, min(self.sequenceLength, boundary)) / self.sequenceLength

    def boundaryAt(self, x: float) -> int:
        fraction = max(0.0, min(1.0, (x - self.left) / self.width))
        return min(self.sequenceLength, int(fraction * self.sequenceLength + 0.5))


@dataclass(frozen=True, slots=True)
class LinearFeatureSpan:
    """One visible feature part on a linear axis, packed in screen-coordinate order."""

    feature: SequenceFeature
    partIndex: int
    xStart: float
    xEnd: float
    strand: int | None
    lane: int


def buildLinearFeatureSpans(
    geometry: OverviewLayout,
    features: tuple[SequenceFeature, ...],
    options: LayerOptions,
    *, maxLanes: int | None = None, selectedId: str | None = None,
) -> tuple[LinearFeatureSpan, ...]:
    """Pack visible feature parts by coordinate, independent of annotation order.

    The one-lane overview uses exact projected endpoints so neighboring annotations
    can share a boundary. Expanded maps enlarge short hit targets to five pixels
    and separate their lanes by three pixels.
    """
    parts = []
    axisEnd = geometry.left + geometry.width
    minimumWidth = 0.0 if maxLanes == 1 else min(5.0, geometry.width)
    separation = 0.0 if maxLanes == 1 else 3.0
    for featureIndex, feature in enumerate(features):
        isPrimer = feature.type == "primer_bind"
        if not (options.showPrimers if isPrimer else options.showFeatures):
            continue
        for partIndex, part in enumerate(feature.parts):
            if not part.isSelectable or part.start is None or part.end is None:
                continue
            xStart = geometry.positionX(part.start)
            xEnd = min(axisEnd, max(xStart + minimumWidth, geometry.positionX(part.end)))
            xStart = max(geometry.left, min(xStart, xEnd - minimumWidth))
            strand = part.strand if part.strand is not None else feature.strand
            parts.append((xStart, xEnd, featureIndex, partIndex, strand))
    parts.sort(key=lambda item: (item[0], -item[1], item[2], item[3]))
    if maxLanes == 1 and selectedId is not None:
        reserved = [(x0, x1) for x0, x1, index, _, _ in parts if features[index].featureId == selectedId]
        parts = [part for part in parts if features[part[2]].featureId == selectedId
                 or not any(part[0] < end and start < part[1] for start, end in reserved)]
    laneEnds: list[float] = []
    spans = []
    for xStart, xEnd, featureIndex, partIndex, strand in parts:
        lane = next(
            (index for index, occupied in enumerate(laneEnds) if occupied + separation <= xStart),
            len(laneEnds),
        )
        if maxLanes is not None and lane >= maxLanes:
            continue
        if lane == len(laneEnds):
            laneEnds.append(xEnd)
        else:
            laneEnds[lane] = xEnd
        spans.append(LinearFeatureSpan(
            features[featureIndex], partIndex, xStart, xEnd, strand, lane,
        ))
    return tuple(spans)
