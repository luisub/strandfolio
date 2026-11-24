"""Shared illustrative gel geometry and attributed figure/data exports."""

from collections import defaultdict
from dataclasses import dataclass
from html import escape
import json
from math import ceil, isfinite
import re
from textwrap import wrap
from typing import Literal

from strandfolio.operations.gel import (
    GEL_LEGEND,
    GEL_MODEL_VERSION,
    GelLane,
    GelMarker,
    GelProduct,
    GelSelection,
    GelSettings,
    GelState,
)
from strandfolio.operations.pcr import PcrRun
from strandfolio.storage.catalogues import LadderComponent


GelProductStatus = Literal["visible", "outside-display-range", "circular", "unknown-topology"]
GEL_PCR_LEGEND = (
    "PCR bands represent predicted products from exact primer matches. Modeled sizes in bp · no yield estimate."
)
_INVALID_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


@dataclass(frozen=True, slots=True)
class GelTick:
    sizeBp: int
    position: float


@dataclass(frozen=True, slots=True)
class GelBand:
    """One band per size, retaining every product or marker component at that size."""

    sizeBp: int
    position: float
    products: tuple[GelProduct, ...] = ()
    markerComponents: tuple[LadderComponent, ...] = ()
    outsideMarkerRange: bool = False

    @property
    def label(self) -> str:
        label = f"{self.sizeBp:,}"
        contributors = len(self.products) or len(self.markerComponents)
        return f"{label} ×{contributors}" if contributors > 1 else label


@dataclass(frozen=True, slots=True)
class GelProductPlacement:
    product: GelProduct
    position: float | None
    status: GelProductStatus
    outsideMarkerRange: bool


@dataclass(frozen=True, slots=True)
class GelMarkerLayout:
    marker: GelMarker
    bands: tuple[GelBand, ...]
    offScale: tuple[LadderComponent, ...]


@dataclass(frozen=True, slots=True)
class GelLaneLayout:
    lane: GelLane
    bands: tuple[GelBand, ...]
    placements: tuple[GelProductPlacement, ...]


@dataclass(frozen=True, slots=True)
class GelLayout:
    """All positions share the well-to-bottom normalized range [0, 1]."""

    marker: GelMarkerLayout
    lanes: tuple[GelLaneLayout, ...]
    ticks: tuple[GelTick, ...]


def gelBandIsSelected(selection: GelSelection | None, lane: GelLane, band: GelBand) -> bool:
    """Identify a highlighted band by its pinned contributor."""
    return selection is not None and selection.laneId == lane.laneId and any(
        product.outputReference == selection.outputReference for product in band.products
    )


def gelLaneIdentity(lane: GelLane) -> str:
    """Describe the exact pinned source revision and optional recorded run."""
    text = f"Revision {lane.source.revisionId}"
    if lane.run is not None:
        text += f" · Run {lane.run.operationId}"
    return text


def buildGelLayout(state: GelState) -> GelLayout:
    """Place linear products and DNA marker components using the same display settings."""
    marker = state.marker
    return GelLayout(
        _markerLayout(marker, state.settings),
        tuple(_laneLayout(lane, state.settings, marker.rangeBp) for lane in state.lanes),
        _sizeTicks(state.settings),
    )


def _sizeTicks(settings: GelSettings) -> tuple[GelTick, ...]:
    low, high = settings.minimumBp, settings.maximumBp
    firstDecade, lastDecade = len(str(low)) - 1, len(str(high)) - 1
    step = max(1, ceil((lastDecade - firstDecade) / 8))
    multiples = (1, 2, 5) if lastDecade - firstDecade <= 3 else (1,)
    candidates = (
        multiple * 10 ** decade
        for decade in range(firstDecade, lastDecade + 1, step) for multiple in multiples
    )
    # A boundary tick takes priority when a standard tick would be too close to it.
    ticks = [GelTick(high, 0.0)]
    for size in sorted(candidates, reverse=True):
        position = settings.position(size)
        if position is not None and ticks[-1].position + 0.05 <= position <= 0.95:
            ticks.append(GelTick(size, position))
    return (*ticks, GelTick(low, 1.0))


def _markerLayout(marker: GelMarker, settings: GelSettings) -> GelMarkerLayout:
    groups: dict[int, list[LadderComponent]] = defaultdict(list)
    offScale = []
    if marker.profile is not None:
        for component in marker.profile.components:
            if settings.position(component.size) is None:
                offScale.append(component)
            else:
                groups[component.size].append(component)
    bands = tuple(
        GelBand(size, settings.position(size), markerComponents=tuple(groups[size]))
        for size in sorted(groups, reverse=True)
    )
    return GelMarkerLayout(marker, bands, tuple(offScale))


def _productPlacement(
    product: GelProduct, settings: GelSettings, markerRange: tuple[int, int] | None,
) -> GelProductPlacement:
    if product.topology == "circular":
        return GelProductPlacement(product, None, "circular", False)
    if product.topology is None:
        return GelProductPlacement(product, None, "unknown-topology", False)
    position = settings.position(product.lengthBp)
    outsideMarker = markerRange is not None and not markerRange[0] <= product.lengthBp <= markerRange[1]
    return GelProductPlacement(
        product, position, "outside-display-range" if position is None else "visible", outsideMarker,
    )


def _laneLayout(
    lane: GelLane, settings: GelSettings, markerRange: tuple[int, int] | None,
) -> GelLaneLayout:
    placements = tuple(_productPlacement(product, settings, markerRange) for product in lane.products)
    groups: dict[int, list[GelProductPlacement]] = defaultdict(list)
    for placement in placements:
        if placement.position is not None:
            groups[placement.product.lengthBp].append(placement)
    bands = tuple(
        GelBand(
            size, groups[size][0].position,
            products=tuple(placement.product for placement in groups[size]),
            outsideMarkerRange=groups[size][0].outsideMarkerRange,
        )
        for size in sorted(groups, reverse=True)
    )
    return GelLaneLayout(lane, bands, placements)


def gelLabelPositions(
    positions: tuple[float, ...], height: float, minimumGap: float = 13.0,
) -> tuple[float, ...]:
    """Separate ordered label centers without moving bands; positions remain normalized."""
    if (not isfinite(height) or height <= 0 or not isfinite(minimumGap) or minimumGap < 0
            or any(not 0 <= position <= 1 for position in positions)
            or any(left > right for left, right in zip(positions, positions[1:]))):
        raise ValueError("Gel label placement requires a positive height and ordered positions.")
    if len(positions) < 2:
        return positions
    gap = min(minimumGap / height, 1 / (len(positions) - 1))
    centers = [positions[0]]
    for position in positions[1:]:
        centers.append(max(position, centers[-1] + gap))
    centers[-1] = min(1.0, centers[-1])
    for index in range(len(centers) - 2, -1, -1):
        centers[index] = max(0.0, min(centers[index], centers[index + 1] - gap))
    return tuple(centers)


def gelProductStatus(placement: GelProductPlacement) -> str:
    """Label the product's placement and report separately when it falls outside the marker range."""
    labels = {
        "visible": "",
        "outside-display-range": "Outside display range",
        "circular": "Circular · no modeled band",
        "unknown-topology": "Unknown topology · no modeled band",
    }
    labelsForProduct = [labels[placement.status]]
    if placement.outsideMarkerRange:
        labelsForProduct.append("Outside marker range")
    return " · ".join(filter(None, labelsForProduct))


def _markerData(layout: GelMarkerLayout) -> dict[str, object]:
    marker, profile = layout.marker, layout.marker.profile
    release = marker.release
    offScale = {(component.preparation, component.ordinal) for component in layout.offScale}
    return {
        "ladderId": marker.ladderId,
        "name": profile.name if profile else None,
        "unit": "bp",
        "available": profile is not None,
        "rangeBp": marker.rangeBp,
        "release": {
            "name": release.name,
            "snapshotSha256": release.snapshotSha256,
            "schemaVersion": release.schemaVersion,
            "biopythonVersion": release.biopythonVersion,
            "reviewed": release.reviewed,
        } if release else None,
        "attributions": profile.attributions if profile else (),
        "components": [
            {
                "preparation": component.preparation, "ordinal": component.ordinal,
                "sizeBp": component.size, "nominalBp": component.nominalSize,
                "sourceId": component.sourceId, "amountNg": None,
                "outsideDisplayRange": (component.preparation, component.ordinal) in offScale,
            }
            for component in (profile.components if profile else ())
        ],
    }


def _productData(placement: GelProductPlacement) -> dict[str, object]:
    product = placement.product
    reference = product.outputReference
    data = {
        "operationId": reference.operationId if reference else None,
        "outputKey": reference.outputKey if reference else None,
        "lengthBp": product.lengthBp,
        "topology": product.topology,
        "sourceSpans": [[span.start, span.end] for span in product.sourceSpans],
        "amountNg": product.amountNg,
        "position": placement.position,
        "status": placement.status,
        "outsideMarkerRange": placement.outsideMarkerRange,
    }
    pcr = product.pcrProduct
    if pcr is not None:
        data.update({
            "productRevisionId": pcr.revision.revisionId,
            "sourceOrientation": pcr.orientation,
            "sourceMappings": [
                {
                    "role": mapping.role, "sourceRevisionId": mapping.sourceRevisionId,
                    "sourceSpan": mapping.sourceSpan, "productSpan": mapping.productSpan,
                    "orientation": mapping.orientation,
                }
                for mapping in pcr.sourceMappings
            ],
        })
    return data


def _laneData(layout: GelLaneLayout, number: int) -> dict[str, object]:
    lane = layout.lane
    data = {
        "number": number,
        "laneId": lane.laneId,
        "label": lane.label,
        "treatment": lane.treatment,
        "operationId": lane.run.operationId if lane.run else None,
        "source": {
            "recordId": lane.source.recordId, "revisionId": lane.source.revisionId,
            "contentDigest": lane.source.contentDigest, "name": lane.source.name,
            "lengthNt": len(lane.source), "topology": lane.source.topology,
        },
        "products": [_productData(placement) for placement in layout.placements],
    }
    if isinstance(lane.run, PcrRun):
        run = lane.run
        data["pcrModel"] = {
            "searchStatus": run.status,
            "searchScope": "whole-template",
            "bindingMode": run.settings.bindingMode,
            "engineVersion": run.engineVersion,
            "minProductBp": run.settings.minProductBp,
            "maxProductBp": run.settings.maxProductBp,
            "runProductCount": len(run.products),
            "warnings": list(run.warnings),
            "bandPositionRepresents": "modeled-product-size-bp",
            "brightnessEstimatesYield": False,
        }
    return data


def _gelData(state: GelState, layout: GelLayout) -> dict[str, object]:
    selection = state.selection
    reference = selection.outputReference if selection is not None else None
    legend = GEL_LEGEND
    if any(isinstance(lane.run, PcrRun) for lane in state.lanes):
        legend += f" {GEL_PCR_LEGEND}"
    return {
        "model": GEL_MODEL_VERSION,
        "legend": legend,
        "sourceSpanConvention": "zero-based half-open intervals in traversal order",
        "settings": {"minimumBp": state.settings.minimumBp, "maximumBp": state.settings.maximumBp},
        "marker": _markerData(layout.marker),
        "lanes": [_laneData(lane, number) for number, lane in enumerate(layout.lanes, 1)],
        "selection": {
            "laneId": selection.laneId,
            "operationId": reference.operationId if reference is not None else None,
            "outputKey": reference.outputKey if reference is not None else None,
        } if selection is not None else None,
    }


def gelData(state: GelState) -> dict[str, object]:
    """Return JSON-serializable gel settings, selection, source IDs and lane inventories."""
    return _gelData(state, buildGelLayout(state))


def _text(value: str, x: float, y: float, *, size: int = 12, anchor: str = "start") -> str:
    safe = escape(_INVALID_XML.sub("\ufffd", value))
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}">{safe}</text>'
    )


def _groupedSizeLabels(sizes: tuple[int, ...]) -> str:
    counts: dict[int, int] = defaultdict(int)
    for size in sizes:
        counts[size] += 1
    return ", ".join(f"{size:,} ×{count}" if count > 1 else f"{size:,}"
                     for size, count in sorted(counts.items(), reverse=True))


def _figureInventory(layout: GelLayout, selection: GelSelection | None) -> tuple[str, ...]:
    marker = layout.marker.marker
    if marker.profile is None:
        lines = [f"M · Missing ladder definition · {marker.ladderId}"]
    else:
        low, high = marker.rangeBp
        lines = [f"M · {marker.profile.name} · {marker.ladderId} · {low:,}–{high:,} bp"]
        if layout.marker.offScale:
            sizes = _groupedSizeLabels(tuple(component.size for component in layout.marker.offScale))
            lines.append(f"Marker components outside display range (bp) · {sizes}")
        nominal = tuple(dict.fromkeys(
            f"{component.size:,} → {component.nominalSize:,}"
            for component in marker.profile.components if component.nominalSize is not None
        ))
        if nominal:
            lines.append(f"Exact → nominal marker labels (bp) · {', '.join(nominal)}")
        lines.extend(marker.profile.attributions)
    if marker.release is not None:
        lines.insert(1, f"Ladder release {marker.release.name} · SHA-256 {marker.release.snapshotSha256}")
    for number, laneLayout in enumerate(layout.lanes, 1):
        lane = laneLayout.lane
        sizes = _groupedSizeLabels(tuple(product.lengthBp for product in lane.products))
        summary = f"{number} · {lane.label} · {lane.source.name} · {lane.treatment}"
        if sizes:
            inventory = f"{sizes} bp"
        elif isinstance(lane.run, PcrRun):
            settings = lane.run.settings
            inventory = f"0 exact-match products in the {settings.minProductBp:,}–{settings.maxProductBp:,} bp range"
        else:
            inventory = "No products"
        lines.append(f"{summary} · {inventory}")
        lines.append(f"    {gelLaneIdentity(lane)}")
        if selection is not None and selection.laneId == lane.laneId:
            reference = selection.outputReference
            lines.append(f"    Selected output {reference.outputKey}" if reference
                         else "    Selected lane")
        for placement in laneLayout.placements:
            status = gelProductStatus(placement)
            product = placement.product
            if status or product.pcrProduct is not None:
                reference = product.outputReference
                key = f"{reference.outputKey} · " if reference else ""
                item = f"    {key}{product.lengthBp:,} bp"
                lines.append(f"{item} · {status}" if status else item)
    lines.append(GEL_LEGEND)
    if any(isinstance(lane.lane.run, PcrRun) for lane in layout.lanes):
        lines.append(GEL_PCR_LEGEND)
    return tuple(lines)


def _bandShapes(
    bands: tuple[GelBand, ...], x: float, top: float, height: float,
    lane: GelLane | None, selection: GelSelection | None,
) -> list[str]:
    shapes = []
    positions = gelLabelPositions(tuple(band.position for band in bands), height)
    for band, position in zip(bands, positions):
        y, labelY = top + band.position * height, top + position * height
        isSelected = lane is not None and gelBandIsSelected(selection, lane, band)
        color = "#b8a5ff" if isSelected else "#e2e9fa"
        shapes.append(f'<g fill="{color}">')
        shapes.append(f'<rect x="{x - 19:.2f}" y="{y - 2:.2f}" width="38" height="4" rx="2"/>')
        shapes.append(
            f'<line x1="{x + 21:.2f}" y1="{y:.2f}" x2="{x + 30:.2f}" y2="{labelY:.2f}" '
            'stroke="#7885a0"/>'
        )
        shapes.append(_text(band.label, x + 33, labelY + 4, size=11))
        shapes.append("</g>")
    return shapes


def gelFigureWidth(state: GelState) -> int:
    """Return the default figure width for the captured lane count."""
    return max(650, 84 + 126 * (len(state.lanes) + 1))


def gelSvg(state: GelState, *, width: int | None = None) -> str:
    """Return an attributed SVG, scaled to the requested width, with pinned lane metadata."""
    viewWidth = gelFigureWidth(state)
    if width is None:
        width = viewWidth
    if width < 1:
        raise ValueError("Choose a positive gel figure width.")
    layout = buildGelLayout(state)
    bandGroups = (layout.marker.bands, *(lane.bands for lane in layout.lanes))
    plotHeight = max(250, 15 * (max(map(len, bandGroups)) - 1))
    top = 83
    lines = tuple(
        part for line in _figureInventory(layout, state.selection)
        for part in wrap(line, (viewWidth - 48) // 12)
    )
    viewHeight = top + plotHeight + 59 + 17 * len(lines)
    height = max(1, round(viewHeight * width / viewWidth))
    data = escape(json.dumps(_gelData(state, layout), separators=(",", ":")))
    shapes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {viewWidth} {viewHeight}">',
        '<title>Illustrative DNA gel</title>',
        f"<metadata>{data}</metadata>",
        f'<rect width="{viewWidth}" height="{viewHeight}" fill="#181e28"/>',
        '<g fill="#e2e9fa" font-family="DejaVu Sans, sans-serif">',
        _text("Illustrative DNA gel", 24, 25, size=16),
        _text("bp", 58, 62, anchor="end"),
    ]
    for tick in layout.ticks:
        y = top + tick.position * plotHeight
        shapes.append(
            f'<line x1="65" y1="{y:.2f}" x2="{viewWidth - 24}" y2="{y:.2f}" stroke="#363f52"/>'
        )
        shapes.append(_text(f"{tick.sizeBp:,}", 58, y + 4, size=11, anchor="end"))
    for index, group in enumerate(bandGroups):
        x = 109 + 126 * index
        shapes.append(_text(str(index) if index else "M", x, 52, anchor="middle"))
        shapes.append(f'<rect x="{x - 19}" y="60" width="38" height="7" rx="2" fill="#65718e"/>')
        lane = state.lanes[index - 1] if index else None
        shapes.extend(_bandShapes(group, x, top, plotHeight, lane, state.selection))
    for index, line in enumerate(lines):
        shapes.append(_text(line, 24, top + plotHeight + 32 + 17 * index))
    shapes.append("</g></svg>")
    return "\n".join(shapes)
