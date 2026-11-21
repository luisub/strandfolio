"""Circular plasmid map geometry, zoom and feature label placement."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import accumulate
from math import atan2, ceil, cos, hypot, pi, radians, sin, sqrt, tau
import re

from strandfolio.application.map_features import LANDMARK_TYPES, isWholeRecordSource, mapFeatureType
from strandfolio.domain.coordinates import SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.rendering.layout import LayerOptions, annotationColor


_TYPE_PRIORITY = {
    "gene": 6,
    "CDS": 6,
    "promoter": 5,
    "enhancer": 4,
    "regulatory": 4,
    "rep_origin": 4,
    "polyA_signal": 3,
    "terminator": 3,
    "misc_feature": 2,
    "protein_bind": 1,
    "primer_bind": 0,
}

_SHORT_LABEL_TYPES = LANDMARK_TYPES | {"enhancer", "regulatory", "polyA_signal"}

_TICK_STEPS = (
    5, 10, 20, 25, 50, 100, 200, 250, 500,
    1_000, 2_000, 2_500, 5_000, 10_000, 20_000, 25_000, 50_000, 100_000,
)

_NUMBERED_SERIES_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,}?)[_-]?(\d+)$")
_COORDINATE_LABEL = re.compile(r"(?:translation|CDS|feature)\s+[\d,]+\s*[-–:]\s*[\d,]+", re.I)


@dataclass(frozen=True, slots=True)
class CircularTick:
    """One coordinate tick mark and optional numeric label on the plasmid ring."""

    boundary: int
    label: str
    innerX: float
    innerY: float
    outerX: float
    outerY: float
    labelX: float
    labelY: float
    showLabel: bool


@dataclass(frozen=True, slots=True)
class CircularPartArc:
    """One feature segment projected onto the map ring."""

    start: int
    end: int
    strand: int | None
    startRad: float
    sweepRad: float
    rInner: float
    rOuter: float
    hasArrowhead: bool


@dataclass(frozen=True, slots=True)
class CircularFeatureArc:
    """Visual arc geometry and hit-test metadata for one circular feature."""

    featureId: str
    label: str
    featureType: str
    strand: int | None
    color: str
    lane: int
    rInner: float
    rOuter: float
    parts: tuple[CircularPartArc, ...]
    isPrimer: bool
    isSelected: bool
    isHovered: bool
    partCount: int
    isBackbone: bool = False

    @property
    def longestPart(self) -> CircularPartArc:
        """Return the displayed interval with the most room for a feature name."""
        return max(self.parts, key=lambda part: part.end - part.start)


@dataclass(frozen=True, slots=True)
class CircularLabelCallout:
    """Collision-resolved external label with an elbowed leader line."""

    featureId: str
    text: str
    color: str
    side: str  # "left" or "right"
    anchorX: float
    anchorY: float
    elbowX: float
    elbowY: float
    labelX: float
    labelY: float
    width: float
    height: float
    isSelected: bool
    isHovered: bool


@dataclass(frozen=True, slots=True)
class CircularSelectionArc:
    """One selected interval rendered along the circular backbone."""

    start: int
    end: int
    startRad: float
    sweepRad: float


@dataclass(frozen=True, slots=True)
class CircularMapLayout:
    """Computed geometry shared by the circular map and SVG export."""

    sequenceLength: int
    width: float
    height: float
    cx: float
    cy: float
    backboneRadius: float
    baseBackboneRadius: float
    rotationDeg: float
    zoom: float
    ticks: tuple[CircularTick, ...]
    features: tuple[CircularFeatureArc, ...]
    callouts: tuple[CircularLabelCallout, ...]
    selectionArcs: tuple[CircularSelectionArc, ...]
    visibleArc: CircularSelectionArc | None
    totalVisibleFeatures: int
    hiddenLabelCount: int
    arcLabelIds: frozenset[str] = frozenset()

    @property
    def centerTextWidth(self) -> float:
        """Width inside the labeled ticks and the horizontal map bounds, in pixels."""
        nearestTick = min(
            (hypot(tick.labelX - self.cx, tick.labelY - self.cy)
             for tick in self.ticks if tick.showLabel),
            default=self.backboneRadius,
        )
        return max(0.0, min(
            self.backboneRadius * 1.65,
            (nearestTick - 14.0) * 1.85,
            2.0 * (min(self.cx, self.width - self.cx) - 6.0),
        ))

    def angleForBoundary(self, boundary: int) -> float:
        """Return clockwise screen angle in radians with boundary 0 at 12 o'clock."""
        clamped = max(0, min(self.sequenceLength, boundary))
        return -pi / 2.0 + radians(self.rotationDeg) + tau * (clamped / self.sequenceLength)

    def boundaryAtPoint(self, x: float, y: float) -> int:
        """Convert pointer coordinates back to the nearest interbase boundary [0, length)."""
        angle = atan2(y - self.cy, x - self.cx)
        origin = -pi / 2.0 + radians(self.rotationDeg)
        delta = (angle - origin) % tau
        boundary = int((delta / tau) * self.sequenceLength + 0.5)
        return 0 if boundary >= self.sequenceLength else boundary

    def featureGapArcs(self, feature: CircularFeatureArc) -> tuple[tuple[float, float], ...]:
        """Return clockwise (start, sweep) angles between ordered feature parts.

        Reverse-strand gaps run clockwise from the next part's upper boundary
        to the previous part's lower boundary. Unknown or omitted parts do not imply a
        known connector, and adjacent parts across the origin have no gap.
        """
        if feature.strand not in (1, -1) or len(feature.parts) != feature.partCount:
            return ()
        gaps: list[tuple[float, float]] = []
        for previous, current in zip(feature.parts, feature.parts[1:]):
            if feature.strand == -1:
                start, end = current.end, previous.start
            else:
                start, end = previous.end, current.start
            gapLength = (end - start) % self.sequenceLength
            if gapLength:
                gaps.append((self.angleForBoundary(start), tau * gapLength / self.sequenceLength))
        return tuple(gaps)

    def featureAtPoint(self, x: float, y: float, *, tolerance: float = 5.0) -> CircularFeatureArc | None:
        """Hit-test callout labels first, then annular feature arcs."""
        for callout in reversed(self.callouts):
            left = callout.labelX - callout.width if callout.side == "left" else callout.labelX
            right = left + callout.width
            top = callout.labelY - callout.height / 2.0
            bottom = callout.labelY + callout.height / 2.0
            if left - 4 <= x <= right + 4 and top - 2 <= y <= bottom + 2:
                for feature in self.features:
                    if feature.featureId == callout.featureId:
                        return feature

        dx = x - self.cx
        dy = y - self.cy
        radius = (dx * dx + dy * dy) ** 0.5
        angle = atan2(dy, dx)
        for feature in reversed(self.features):
            for part in feature.parts:
                if not (part.rInner - tolerance <= radius <= part.rOuter + tolerance):
                    continue
                rel = (angle - part.startRad) % tau
                pad = max(0.015, tolerance / max(20.0, part.rOuter))
                if rel <= part.sweepRad + pad or (tau - rel) <= pad:
                    return feature
        return None


def annularArrowPolygon(
    cx: float,
    cy: float,
    rInner: float,
    rOuter: float,
    startRad: float,
    sweepRad: float,
    strand: int | None,
    hasArrowhead: bool,
) -> tuple[tuple[float, float], ...]:
    """Sample a curved annular arc or directional wedge polygon in screen coordinates."""
    sweep = max(0.002, min(tau - 1e-4, sweepRad))
    midRadius = (rInner + rOuter) / 2.0
    thickness = max(2.0, rOuter - rInner)
    arrowRad = (
        min(sweep * 0.48, (thickness * 0.95) / max(15.0, midRadius))
        if hasArrowhead and strand in (1, -1)
        else 0.0
    )

    if arrowRad > 0.0 and strand == 1:
        bodyStart = startRad
        bodyEnd = startRad + sweep - arrowRad
        tipAngle = startRad + sweep
    elif arrowRad > 0.0 and strand == -1:
        bodyStart = startRad + arrowRad
        bodyEnd = startRad + sweep
        tipAngle = startRad
    else:
        bodyStart = startRad
        bodyEnd = startRad + sweep
        tipAngle = None

    steps = max(4, min(64, ceil(abs(bodyEnd - bodyStart) / radians(5.0))))
    outerPts: list[tuple[float, float]] = []
    innerPts: list[tuple[float, float]] = []
    for index in range(steps + 1):
        theta = bodyStart + (bodyEnd - bodyStart) * (index / steps)
        ct, st = cos(theta), sin(theta)
        outerPts.append((cx + rOuter * ct, cy + rOuter * st))
        innerPts.append((cx + rInner * ct, cy + rInner * st))

    if tipAngle is None:
        return tuple(outerPts + list(reversed(innerPts)))

    tipPt = (cx + midRadius * cos(tipAngle), cy + midRadius * sin(tipAngle))
    if strand == 1:
        flareOuter = (cx + (rOuter + 1.5) * cos(bodyEnd), cy + (rOuter + 1.5) * sin(bodyEnd))
        flareInner = (cx + (rInner - 1.5) * cos(bodyEnd), cy + (rInner - 1.5) * sin(bodyEnd))
        return tuple(outerPts + [flareOuter, tipPt, flareInner] + list(reversed(innerPts)))

    flareOuter = (cx + (rOuter + 1.5) * cos(bodyStart), cy + (rOuter + 1.5) * sin(bodyStart))
    flareInner = (cx + (rInner - 1.5) * cos(bodyStart), cy + (rInner - 1.5) * sin(bodyStart))
    return tuple([tipPt, flareOuter] + outerPts + list(reversed(innerPts)) + [flareInner])


def _chooseTickStep(sequenceLength: int, zoom: float) -> int:
    targetTicks = min(48, max(8, int(10 * zoom)))
    for step in _TICK_STEPS:
        if sequenceLength / step <= targetTicks:
            return step
    return max(100_000, sequenceLength // targetTicks)


def _relaxVerticalPositions(targets: list[float], minY: float, maxY: float, pitch: float) -> list[float]:
    """Push overlapping callouts apart deterministically while staying inside vertical bounds."""
    if not targets:
        return []
    # Pool neighboring labels whose ideal positions would violate the minimum pitch.
    # Each pool stays centered on its anchors, including dense clusters near a pole.
    pools: list[tuple[float, int]] = []
    for index, y in enumerate(targets):
        mean, count = y - index * pitch, 1
        while pools and pools[-1][0] > mean:
            previous, size = pools.pop()
            mean = (previous * size + mean * count) / (size + count)
            count += size
        pools.append((mean, count))
    lastStart = max(minY, maxY - (len(targets) - 1) * pitch)
    positions = [min(lastStart, max(minY, mean)) for mean, size in pools for _ in range(size)]
    return [start + index * pitch for index, start in enumerate(positions)]


def _intervalsOverlap(a: tuple[float, float], b: tuple[float, float], pad: float) -> bool:
    """Return whether two modular [start, sweep] arcs on a circle overlap within pad."""
    s1, w1 = a[0] % tau, a[1]
    s2, w2 = b[0] % tau, b[1]
    if w1 >= tau - 1e-4 or w2 >= tau - 1e-4:
        return True
    if (s2 - s1) % tau < w1 + pad:
        return True
    if (s1 - s2) % tau < w2 + pad:
        return True
    return False


def _packCircularLanes(featureArcSets: list[list[tuple[float, float]]]) -> list[int]:
    """Assign concentric track indices using each feature's actual angular part intervals."""
    if not featureArcSets:
        return []
    totalSweeps = [sum(w for _, w in arcs) for arcs in featureArcSets]
    ordered = sorted(
        range(len(featureArcSets)),
        key=lambda index: (-totalSweeps[index], featureArcSets[index][0][0]),
    )
    lanes: list[list[tuple[float, float]]] = []
    assigned = [0] * len(featureArcSets)
    pad = radians(0.7)
    for originalIndex in ordered:
        arcs = featureArcSets[originalIndex]
        chosen = -1
        for laneIndex, occupied in enumerate(lanes):
            if not any(_intervalsOverlap(a, b, pad) for a in arcs for b in occupied):
                occupied.extend(arcs)
                chosen = laneIndex
                break
        if chosen < 0:
            chosen = len(lanes)
            lanes.append(list(arcs))
        assigned[originalIndex] = chosen
    return assigned


def _normalizeArc(start: int, end: int, sequenceLength: int, originRad: float) -> tuple[float, float]:
    startRad = originRad + tau * (start / sequenceLength)
    sweepRad = tau * ((end - start) / sequenceLength)
    return startRad, sweepRad


def _truncateLabel(label: str, limit: int = 22) -> str:
    return label if len(label) <= limit else label[:limit - 1] + "…"


def _selectOverviewCandidates(
    rawCandidates: list[dict],
    *,
    sequenceLength: int,
    zoom: float,
    denseLabels: bool,
) -> tuple[list[dict], int]:
    """Group repeated motifs and prioritize landmark annotations at overview zoom."""
    if denseLabels or zoom >= 1.8:
        return rawCandidates, 0

    # Shared names across feature types do not establish a repeated biological motif.
    exactGroups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seriesGroups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in rawCandidates:
        if item["is_selected"] or item["is_hovered"]:
            continue
        if item["is_primer"]:
            continue
        exactGroups[(item["feature_type"], item["raw_label"].casefold().strip())].append(item)
        match = _NUMBERED_SERIES_RE.match(item["raw_label"].strip())
        if match:
            seriesGroups[(item["feature_type"], match.group(1).casefold())].append(item)

    groupedIds: set[str] = set()
    kept: list[dict] = []
    collapsedHidden = 0

    for item in rawCandidates:
        if item["is_selected"] or item["is_hovered"]:
            kept.append(item)

    # Collapse exact repeat clusters (>= 3 occurrences) into one representative callout.
    for items in exactGroups.values():
        if len(items) < 3:
            continue
        itemsSorted = sorted(items, key=lambda x: x["theta"])
        representative = dict(itemsSorted[len(itemsSorted) // 2])
        stem = _truncateLabel(representative["raw_label"], 18)
        representative["text"] = f"{stem} (×{len(items)})"
        representative["width"] = max(32.0, len(representative["text"]) * 6.4 + 8.0)
        representative["priority"] = (
            0,
            max(item["priority"][1] for item in items),
            max(item["total_bp"] for item in items) + len(items) * 20,
            representative["priority"][3],
        )
        kept.append(representative)
        groupedIds.update(x["feature_id"] for x in items)
        collapsedHidden += len(items) - 1

    # Collapse numbered repeat arrays (>= 4 occurrences, not already exact-grouped).
    for (featureType, _seriesStem), items in seriesGroups.items():
        remaining = [x for x in items if x["feature_id"] not in groupedIds]
        if len(remaining) < 4:
            continue
        itemsSorted = sorted(remaining, key=lambda x: x["theta"])
        representative = dict(itemsSorted[len(itemsSorted) // 2])
        stemDisplay = _NUMBERED_SERIES_RE.match(representative["raw_label"].strip()).group(1)
        representative["text"] = f"{_truncateLabel(stemDisplay, 18)} (×{len(remaining)})"
        representative["width"] = max(32.0, len(representative["text"]) * 6.4 + 8.0)
        representative["priority"] = (
            0,
            _TYPE_PRIORITY.get(featureType, 1),
            max(item["total_bp"] for item in remaining) + len(remaining) * 20,
            representative["priority"][3],
        )
        kept.append(representative)
        groupedIds.update(x["feature_id"] for x in remaining)
        collapsedHidden += len(remaining) - 1

    # Skip short fragment labels in the overview of larger plasmids.
    minFragmentLength = 15 if sequenceLength >= 1000 else 1
    for item in rawCandidates:
        if item["is_selected"] or item["is_hovered"] or item["feature_id"] in groupedIds:
            continue
        if item["is_primer"]:
            collapsedHidden += 1
            continue
        if item["total_bp"] < minFragmentLength and item["feature_type"] not in _SHORT_LABEL_TYPES:
            collapsedHidden += 1
            continue
        if item["total_bp"] < 12 and sequenceLength >= 1000 and item["feature_type"] == "CDS":
            # Partial CDS fragments (< 12 bp) stay clickable on ring without crowding overview labels.
            collapsedHidden += 1
            continue
        kept.append(item)

    return kept, collapsedHidden


def _relaxSideCallouts(
    candidates: list[dict],
    side: str,
    cx: float,
    cy: float,
    elbowRadius: float,
    labelX: float,
    minY: float,
    maxY: float,
    pitch: float,
    labelHeight: float,
    zoomed: bool,
    compact: bool,
    columnPitch: float | None,
) -> list[CircularLabelCallout]:
    if not candidates:
        return []

    def idealY(item: dict) -> float:
        return item["anchor_y"] if zoomed else cy + elbowRadius * sin(item["theta"])

    ordered = sorted(candidates, key=idealY)
    targets = [idealY(item) for item in ordered]
    if columnPitch is not None:
        spacing = min(columnPitch, (maxY - minY) / max(1, len(ordered) - 1))
        # A lone label follows its feature. A crowded column uses the available height.
        count = len(ordered)
        center = cy + (sum(targets) / count - cy) / count
        targets = [center + (index - (count - 1) / 2) * spacing for index in range(count)]
    ys = _relaxVerticalPositions(targets, minY, maxY, pitch)
    result: list[CircularLabelCallout] = []
    for item, y in zip(ordered, ys):
        if zoomed:
            elbowX = (item["anchor_x"] + labelX) * 0.5
            elbowY = (item["anchor_y"] + y) * 0.5
        else:
            elbowX = cx + elbowRadius * cos(item["theta"])
            elbowY = cy + elbowRadius * sin(item["theta"])
        textX = labelX
        if compact or (not zoomed and len(ordered) < 5):
            # Sparse labels follow the rim. Crowded sides share a common text edge.
            dy = max(0.0, abs(y - cy) - labelHeight / 2)
            halfChord = sqrt(max(0.0, elbowRadius ** 2 - dy ** 2))
            offset = max(halfChord, abs(elbowX - cx)) + (4 if compact else 14)
            textX = min(cx + offset, labelX) if side == "right" else max(cx - offset, labelX)
        result.append(
            CircularLabelCallout(
                featureId=item["feature_id"],
                text=item["text"],
                color=item["color"],
                side=side,
                anchorX=item["anchor_x"],
                anchorY=item["anchor_y"],
                elbowX=elbowX,
                elbowY=elbowY,
                labelX=textX,
                labelY=y,
                width=item["width"],
                height=labelHeight,
                isSelected=item["is_selected"],
                isHovered=item["is_hovered"],
            )
        )
    return result


def _laneEdges(budget: float, count: int, maxPitch: float) -> list[float]:
    """Give the longest features a readable first lane, then share the remaining space."""
    if not count:
        return [0.0]
    average = budget / count
    first = min(maxPitch, average * 2.5, budget - (count - 1) * min(4.0, average))
    rest = min(maxPitch, (budget - first) / max(1, count - 1))
    return list(accumulate([first] + [rest] * (count - 1), initial=0.0))


def circularBaseRadius(width: float, height: float) -> float:
    """Return the outer track budget before applying the viewport's zoom and pan."""
    sideMargin = max(84.0, min(145.0, width * 0.22))
    verticalMargin = max(24.0, min(48.0, height * 0.08))
    if width < 460 and height < 280:
        sideMargin = min(sideMargin, width * 0.235)
        verticalMargin = 13.0
    return max(52.0, min(width * 0.5 - sideMargin, height * 0.5 - verticalMargin))


def buildCircularMapLayout(
    sequenceLength: int,
    features: tuple[SequenceFeature, ...],
    *,
    width: float,
    height: float,
    options: LayerOptions = LayerOptions(),
    selection: SequenceSelection | None = None,
    visibleInterval: tuple[int, int] | None = None,
    hoveredFeatureId: str | None = None,
    denseLabels: bool = False,
    rotationDeg: float = 0.0,
    zoom: float = 1.0,
    panX: float = 0.0,
    panY: float = 0.0,
    maxLabels: int = 22,
    labelHeader: float = 20.0,
    labelFooter: float = 20.0,
    showCalloutIntervals: bool = False,
    arcLabelWidth: Callable[[str], float] | None = None,
) -> CircularMapLayout:
    """Compute concentric feature arcs, ticks, selection arcs, zoom, and relevant labels."""
    if sequenceLength <= 0:
        raise ValueError("Sequence length must be positive for circular layout.")
    zoom = max(1.0, min(8.0, float(zoom)))
    baseMaxRadius = circularBaseRadius(width, height)
    baseBackboneRadius = max(40.0, baseMaxRadius * 0.77)

    maxRingRadius = baseMaxRadius * zoom
    backboneRadius = baseBackboneRadius * zoom
    if zoom <= 1.0 + 1e-4:
        panX = 0.0
        panY = 0.0
    else:
        maxPan = backboneRadius + 40.0
        panX = max(-maxPan, min(maxPan, panX))
        panY = max(-maxPan, min(maxPan, panY))

    cx = width / 2.0 + panX
    cy = height / 2.0 + panY
    originRad = -pi / 2.0 + radians(rotationDeg)

    selectedFeatureId = selection.featureId if selection is not None else None
    outerEntries: list[tuple[int, SequenceFeature, list[tuple[float, float]]]] = []
    innerEntries: list[tuple[int, SequenceFeature, list[tuple[float, float]]]] = []
    primerEntries: list[tuple[int, SequenceFeature]] = []
    sourceEntries: list[tuple[int, SequenceFeature]] = []
    for index, feature in enumerate(features):
        exactParts = [
            part for part in feature.parts
            if part.isSelectable and part.start is not None and part.end is not None
        ]
        if not exactParts:
            continue
        if feature.type == "primer_bind":
            if options.showPrimers:
                primerEntries.append((index, feature))
            continue
        if not options.showFeatures:
            continue
        if isWholeRecordSource(feature, sequenceLength):
            sourceEntries.append((index, feature))
            continue
        partArcsNorm = [
            (
                originRad + tau * (part.start / sequenceLength),
                max(radians(2.0 / zoom), tau * ((part.end - part.start) / sequenceLength)),
            )
            for part in exactParts
        ]
        if feature.strand == -1:
            innerEntries.append((index, feature, partArcsNorm))
        else:
            outerEntries.append((index, feature, partArcsNorm))

    outerLanes = _packCircularLanes([item[2] for item in outerEntries])
    innerLanes = _packCircularLanes([item[2] for item in innerEntries])
    maxOuterLane = max(outerLanes, default=-1)
    maxInnerLane = max(innerLanes, default=-1)

    outerTrackCount = maxOuterLane + 1 + int(
        any(feature.strand != -1 for _, feature in primerEntries)
    )
    innerTrackCount = maxInnerLane + 1 + int(
        any(feature.strand == -1 for _, feature in primerEntries)
    )
    # Dense overlaps must fit the radial budget rather than invert inner rings or
    # push outer tracks beyond the map. Individual labels remain available on zoom.
    trackGap = min(7.0, backboneRadius * 0.04)
    maxPitch = min(20.0, backboneRadius * 0.13)
    outerEdges = _laneEdges(maxRingRadius - backboneRadius - trackGap, outerTrackCount, maxPitch)
    innerEdges = _laneEdges(backboneRadius - trackGap - 18.0, innerTrackCount, maxPitch)
    tickRingRadius = max(18.0, backboneRadius - trackGap - innerEdges[-1] - 2.0)
    step = _chooseTickStep(sequenceLength, zoom)
    tickStep = step // 5 if tickRingRadius >= 80 and step % 5 == 0 else step
    ticks: list[CircularTick] = []
    minTickGapRad = radians(16.0 / zoom)
    for boundary in range(0, sequenceLength, tickStep):
        angle = originRad + tau * (boundary / sequenceLength)
        ct, st = cos(angle), sin(angle)
        major = boundary % step == 0
        tickLength = 5.0 if major else 2.5
        # The inspector map can be only 140 pixels tall. Keep tick marks, but
        # reserve its small center for the record size until coordinates fit.
        showLabel = major and tickRingRadius >= 62 and (
            boundary == 0 or (tau - tau * (boundary / sequenceLength)) >= minTickGapRad
        )
        label = "1" if boundary == 0 else f"{boundary:,}"
        labelRadius = max(18.0, tickRingRadius - 10 - len(label) * 3)
        ticks.append(
            CircularTick(
                boundary=boundary,
                label=label,
                innerX=cx + (tickRingRadius - tickLength) * ct,
                innerY=cy + (tickRingRadius - tickLength) * st,
                outerX=cx + tickRingRadius * ct,
                outerY=cy + tickRingRadius * st,
                labelX=cx + labelRadius * ct,
                labelY=cy + labelRadius * st,
                showLabel=showLabel,
            )
        )

    arcs: list[CircularFeatureArc] = []
    rawCandidates: list[dict] = []

    def _buildArc(
        featureIndex: int,
        feature: SequenceFeature,
        lane: int,
        innerRadius: float,
        outerRadius: float,
        isPrimer: bool,
        isBackbone: bool = False,
    ) -> None:
        minSweep = radians((1.2 if isPrimer else 2.0) / zoom)
        partArcs: list[CircularPartArc] = []
        for partIndex, part in enumerate(feature.parts):
            if not part.isSelectable or part.start is None or part.end is None:
                continue
            partStart, partEnd = part.start, part.end
            rawStartRad, rawSweepRad = _normalizeArc(partStart, partEnd, sequenceLength, originRad)
            visibleSweepRad = max(rawSweepRad, minSweep)
            visibleStartRad = rawStartRad - (visibleSweepRad - rawSweepRad) * 0.5
            is3pTip = partIndex == len(feature.parts) - 1
            partArcs.append(
                CircularPartArc(
                    start=partStart,
                    end=partEnd,
                    strand=part.strand if part.strand is not None else feature.strand,
                    startRad=visibleStartRad,
                    sweepRad=visibleSweepRad,
                    rInner=innerRadius,
                    rOuter=outerRadius,
                    hasArrowhead=is3pTip and feature.strand in (1, -1) and not isBackbone,
                )
            )

        isSelected = feature.featureId == selectedFeatureId
        isHovered = feature.featureId == hoveredFeatureId
        color = annotationColor(feature)
        arc = CircularFeatureArc(
            featureId=feature.featureId,
            label=feature.label,
            featureType=feature.type,
            strand=feature.strand,
            color=color,
            lane=lane,
            rInner=innerRadius,
            rOuter=outerRadius,
            parts=tuple(partArcs),
            isPrimer=isPrimer,
            isSelected=isSelected,
            isHovered=isHovered,
            partCount=len(feature.parts),
            isBackbone=isBackbone,
        )
        arcs.append(arc)

        if isBackbone and not (denseLabels or isSelected or isHovered):
            return
        longest = arc.longestPart
        longestMidpointRad = longest.startRad + longest.sweepRad / 2
        anchorX = cx + outerRadius * cos(longestMidpointRad)
        anchorY = cy + outerRadius * sin(longestMidpointRad)
        if zoom > 1.05 and not (isSelected or isHovered):
            if not (-24.0 <= anchorX <= width + 24.0 and -24.0 <= anchorY <= height + 24.0):
                return
        if isPrimer and zoom < 2.2 and not (denseLabels or isSelected or isHovered):
            return

        totalLength = sum(part.end - part.start for part in partArcs)
        text = feature.label
        estimatedWidth = max(28.0, len(text) * 6.4 + 8.0)
        rawCandidates.append(
            {
                "feature_id": feature.featureId,
                "raw_label": feature.label,
                "feature_type": feature.type,
                "text": text,
                "color": color,
                "theta": longestMidpointRad,
                "anchor_x": anchorX,
                "anchor_y": anchorY,
                "width": estimatedWidth,
                "total_bp": totalLength,
                "is_primer": isPrimer,
                "priority": (
                    2 if isSelected else int(isHovered),
                    _TYPE_PRIORITY.get(mapFeatureType(feature), 1),
                    totalLength,
                    -featureIndex,
                ),
                "is_selected": isSelected,
                "is_hovered": isHovered,
            }
        )

    for index, feature in sourceEntries:
        _buildArc(index, feature, 0, backboneRadius - 1, backboneRadius + 1, False, isBackbone=True)

    for entries, lanes, edges, direction in (
        (outerEntries, outerLanes, outerEdges, 1), (innerEntries, innerLanes, innerEdges, -1),
    ):
        for (index, feature, _), lane in zip(entries, lanes):
            pitch = edges[lane + 1] - edges[lane]
            near = backboneRadius + direction * (trackGap + edges[lane])
            far = near + direction * (pitch - min(2.0, pitch * 0.15))
            _buildArc(index, feature, lane, min(near, far), max(near, far), False)

    for index, feature in primerEntries:
        reverse = feature.strand == -1
        edges = innerEdges if reverse else outerEdges
        direction = -1 if reverse else 1
        near = backboneRadius + direction * (trackGap + edges[-2])
        far = near + direction * min(4.0, edges[-1] - edges[-2])
        _buildArc(index, feature, len(edges) - 2, min(near, far), max(near, far), True)

    candidates, prefilteredHidden = _selectOverviewCandidates(
        rawCandidates, sequenceLength=sequenceLength, zoom=zoom, denseLabels=denseLabels,
    )

    arcLabelIds: set[str] = set()
    if arcLabelWidth is not None:
        byId = {arc.featureId: arc for arc in arcs}
        for item in candidates:
            arc = byId[item["feature_id"]]
            if (arc.isBackbone or arc.isPrimer or arc.isSelected or arc.isHovered
                    or arc.label != item["text"] or arc.rOuter - arc.rInner < 14):
                continue
            part = arc.longestPart
            available = part.sweepRad * (arc.rInner + arc.rOuter) / 2 - (arc.rOuter - arc.rInner) * 2 - 8
            if arcLabelWidth(arc.label) <= available:
                arcLabelIds.add(arc.featureId)
        candidates = [item for item in candidates if item["feature_id"] not in arcLabelIds]

    compact = width < 460 and height < 280 and zoom <= 1.05 and not denseLabels
    if showCalloutIntervals:
        labelHeight = 43.0 if width < 720 else 30.0
        columnPitch = None
    else:
        labelHeight = 39.0 if compact else 15.0
        columnPitch = 44.0 if compact else None
    labelPitch = labelHeight + (1.0 if denseLabels else 5.0)
    minY = labelHeader
    maxY = max(minY + 20.0, height - labelFooter)
    capacityPerSide = int((maxY - minY) // labelPitch) + 1
    zoomed = zoom > 1.05
    totalCap = (
        min(len(candidates), capacityPerSide * 2)
        if denseLabels
        else min(capacityPerSide * 2, max(maxLabels, int(maxLabels * zoom)))
    )
    sideCap = min(capacityPerSide, max(3, totalCap // 2))

    def labelPriority(item: dict) -> tuple:
        active, *importance = item["priority"]
        named = item["raw_label"].casefold() != item["feature_type"].casefold()
        descriptive = named and not _COORDINATE_LABEL.fullmatch(item["raw_label"])
        return (active, bool(compact and descriptive), *importance)

    sortedCandidates = sorted(candidates, key=labelPriority, reverse=True)
    leftBucket: list[dict] = []
    rightBucket: list[dict] = []
    cappedHidden = 0
    for item in sortedCandidates:
        isLeft = (item["anchor_x"] < width / 2.0) if zoomed else (cos(item["theta"]) < 0.0)
        sideList = leftBucket if isLeft else rightBucket
        mustKeep = item["is_selected"] or item["is_hovered"]
        hasCapacity = len(sideList) < sideCap and len(leftBucket) + len(rightBucket) < totalCap
        canFitDense = denseLabels and len(sideList) < capacityPerSide
        if canFitDense or mustKeep or hasCapacity:
            sideList.append(item)
        else:
            cappedHidden += 1

    trackRadius = max((arc.rOuter for arc in arcs), default=backboneRadius)
    elbowRadius = max(trackRadius, backboneRadius) + (3.0 if compact else 10.0)
    if zoomed:
        leftLabelX = min(132.0, width * 0.28)
        rightLabelX = max(width - 132.0, width * 0.72)
    else:
        stub = 3.0 if compact else 26.0
        leftLabelX = max(8.0, cx - elbowRadius - stub)
        rightLabelX = min(width - 8.0, cx + elbowRadius + stub)

    callouts = tuple(
        _relaxSideCallouts(
            leftBucket, "left", cx, cy, elbowRadius, leftLabelX,
            minY, maxY, labelPitch, labelHeight, zoomed, compact, columnPitch,
        )
        + _relaxSideCallouts(
            rightBucket, "right", cx, cy, elbowRadius, rightLabelX,
            minY, maxY, labelPitch, labelHeight, zoomed, compact, columnPitch,
        )
    )

    selArcs: list[CircularSelectionArc] = []
    if selection is not None and len(selection):
        for segmentStart, segmentEnd in selection.segments:
            if segmentStart < segmentEnd:
                startRad, sweepRad = _normalizeArc(segmentStart, segmentEnd, sequenceLength, originRad)
                selArcs.append(CircularSelectionArc(segmentStart, segmentEnd, startRad, sweepRad))

    visibleArc: CircularSelectionArc | None = None
    if visibleInterval is not None and visibleInterval[0] < visibleInterval[1]:
        visibleStart, visibleEnd = visibleInterval
        visibleStartRad, visibleSweepRad = _normalizeArc(visibleStart, visibleEnd, sequenceLength, originRad)
        visibleArc = CircularSelectionArc(visibleStart, visibleEnd, visibleStartRad, visibleSweepRad)

    return CircularMapLayout(
        sequenceLength=sequenceLength,
        width=width,
        height=height,
        cx=cx,
        cy=cy,
        backboneRadius=backboneRadius,
        baseBackboneRadius=baseBackboneRadius,
        rotationDeg=rotationDeg,
        zoom=zoom,
        ticks=tuple(ticks),
        features=tuple(arcs),
        callouts=callouts,
        arcLabelIds=frozenset(arcLabelIds),
        selectionArcs=tuple(selArcs),
        visibleArc=visibleArc,
        totalVisibleFeatures=len(arcs),
        hiddenLabelCount=prefilteredHidden + cappedHidden,
    )
