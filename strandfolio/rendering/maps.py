"""Vector overview, annotated linear map and circular plasmid map exports."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from html import escape
from math import asin, cos, degrees, hypot, pi, sin, sqrt, tau
from pathlib import Path
import re
from threading import local

from strandfolio.application.map_features import LANDMARK_TYPES, isWholeRecordSource, mapFeatures
from strandfolio.application.session import DocumentSession
from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.conversion import CdsTranslation, CodonPlacement, FrameTranslation
from strandfolio.operations.restriction import RecognitionSite, RestrictionCut
from strandfolio.rendering.circular import (
    CircularFeatureArc,
    CircularMapLayout,
    annularArrowPolygon,
    buildCircularMapLayout,
    circularBaseRadius,
)
from strandfolio.rendering import colors
from strandfolio.rendering.layout import (
    LayerOptions,
    LinearFeatureSpan,
    OverviewLayout,
    annotationColor,
    buildLinearFeatureSpans,
)


_INVALID_XML_CHARACTERS = re.compile(
    r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


class _FontState(local):
    def __init__(self) -> None:
        self.fonts = {}


_FONT_STATE = _FontState()


@lru_cache(maxsize=4096)
def _textWidth(text: str, pixels: int, bold: bool) -> float:
    # Use consistent sans-serif metrics for label fitting without a GUI.
    from matplotlib import get_data_path
    from matplotlib.ft2font import FT2Font

    key = (pixels, bold)
    font = _FONT_STATE.fonts.get(key)
    if font is None:
        filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        font = FT2Font(Path(get_data_path()) / "fonts/ttf" / filename)
        font.set_size(pixels, 72)
        _FONT_STATE.fonts[key] = font
    supported = "".join(character for character in text if font.get_char_index(ord(character)))
    font.set_text(supported, 0)
    advance, _ = font.get_width_height()
    # Reserve a full em for glyphs the reader draws in a fallback font.
    return advance / 64 + (len(text) - len(supported)) * pixels


def _fitSvgText(
    text: str, width: float, pixels: int, *, bold: bool = False, middle: bool = False,
) -> str:
    if _textWidth(text, pixels, bold) <= width:
        return text
    if _textWidth("…", pixels, bold) > width:
        return ""
    best = "…"
    lo, hi = 1, len(text) - 1
    while lo <= hi:
        keep = (lo + hi) // 2
        candidate = text[:keep] + "…"
        if middle:
            candidate = text[:(keep + 1) // 2] + "…" + text[len(text) - keep // 2:]
        if _textWidth(candidate, pixels, bold) <= width:
            best = candidate
            lo = keep + 1
        else:
            hi = keep - 1
    return best


def _fitSvgLines(text: str, width: float, pixels: int, *, bold: bool, maxLines: int) -> list[str]:
    """Wrap source names at spaces, hyphens and underscores before eliding."""
    lines = []
    remaining = text.strip()
    for _ in range(maxLines - 1):
        if _textWidth(remaining, pixels, bold) <= width:
            break
        split = 0
        for separator in re.finditer(r"[\s_-]+", remaining):
            if _textWidth(remaining[:separator.start()], pixels, bold) > width:
                break
            withSeparator = remaining[:separator.end()].rstrip()
            split = separator.end() if _textWidth(withSeparator, pixels, bold) <= width else separator.start()
        if not split:
            break
        lines.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip()
    lines.append(_fitSvgText(remaining, width, pixels, bold=bold, middle=bold))
    return lines


def _selectionSummaryLabel(
    selection: SequenceSelection | None, revision: SequenceRevision, unit: str,
) -> str:
    if selection is None or not len(selection):
        return "No selection"
    endpoints = selection.displayRange(len(revision), circular=revision.topology == "circular")
    if endpoints is None:
        return f"Selected {len(selection.segments)} segments · {len(selection):,} {unit}"
    first, last = endpoints
    wrap = " (wraps)" if first > last else ""
    return f"Selected {first:,}–{last:,}{wrap} · {len(selection):,} {unit}"


def overviewSvg(
    session: DocumentSession, *, width: int = 900, displayName: str | None = None,
) -> str:
    """Return an SVG coordinate overview supporting multi-segment selections.

    This string can be displayed in a notebook or written by its caller. Selection
    labels use one-based inclusive display coordinates; geometry uses boundaries.
    """
    revision = session.current
    if revision is None or width < 300:
        raise ValueError("Open a sequence and use a width of at least 300 pixels.")
    geometry = OverviewLayout(len(revision), 32, width - 64)
    unit = "aa" if revision.moleculeType == "protein" else "nt"
    subtitle = f"{revision.moleculeType} · {len(revision):,} {unit} · stored coordinate overview"
    name = displayName or revision.name
    shapes = _svgStart(width, 180, name)
    _mapTitle(shapes, name, 32, 34, geometry.width, pixels=18)
    shapes.extend([
        f'<text x="32" y="58" fill="{colors.MUTED}" font-family="sans-serif" font-size="12">{subtitle}</text>',
        f'<rect x="32" y="87" width="{width - 64}" height="16" rx="8" fill="{colors.LINE}"/>',
    ])
    selected = session.selection
    if selected is not None and len(selected):
        for segmentStart, segmentEnd in selected.segments:
            x = geometry.positionX(segmentStart)
            span = max(1.0, geometry.positionX(segmentEnd) - x)
            shapes.append(f'<rect x="{x:.3f}" y="87" width="{span:.3f}" height="16" fill="{colors.ACCENT}"/>')
        label = _selectionSummaryLabel(selected, revision, unit)
    else:
        label = "No selection"
    shapes.extend([
        f'<text x="32" y="126" fill="{colors.MUTED}" font-family="sans-serif" font-size="12">1</text>',
        f'<text x="{width - 32}" y="126" text-anchor="end" fill="{colors.MUTED}" '
        f'font-family="sans-serif" font-size="12">{len(revision):,}</text>',
        f'<text x="32" y="157" fill="{colors.ACCENT_INK}" font-family="sans-serif" font-size="12">{escape(label)}</text>',
        '</svg>',
    ])
    return "\n".join(shapes)



@dataclass(frozen=True, slots=True)
class MapScene:
    """SVG plus the exact title, feature and marker geometry used by the native canvas."""

    svg: str
    titleBounds: tuple[float, float, float, float]
    restrictionMarkers: tuple[tuple[float, float, RecognitionSite], ...]
    status: str
    circularLayout: CircularMapLayout | None = None
    linearGeometry: OverviewLayout | None = None
    linearHits: tuple[tuple[float, float, float, float, str], ...] = ()


@dataclass(frozen=True, slots=True)
class MapFigureSource:
    """Freeze a map's immutable record, selection, overlays and view settings for export."""

    revision: SequenceRevision
    displayName: str
    isCircular: bool
    selection: SequenceSelection | None = None
    options: LayerOptions = LayerOptions()
    visibleInterval: tuple[int, int] | None = None
    denseLabels: bool = False
    rotationDeg: float = 0.0
    zoom: float = 1.0
    panX: float = 0.0
    panY: float = 0.0
    viewportWidth: int = 760
    viewportHeight: int = 580
    restrictionSites: tuple[RecognitionSite, ...] = ()
    restrictionCuts: tuple[RestrictionCut, ...] = ()
    restrictionRecipe: tuple[str, ...] = ()
    cdsTranslations: tuple[CdsTranslation, ...] = ()
    frameTranslation: FrameTranslation | None = None
    mapFeatureTypes: frozenset[str] | None = LANDMARK_TYPES

    @property
    def selectedFeatureId(self) -> str | None:
        return self.selection.featureId if self.selection else None

    @property
    def visibleFeatures(self) -> tuple[SequenceFeature, ...]:
        return mapFeatures(self.revision, self.mapFeatureTypes, self.selectedFeatureId)

    @property
    def visibleCdsTranslations(self) -> tuple[CdsTranslation, ...]:
        types = self.mapFeatureTypes
        showEveryCds = types is None or (types != LANDMARK_TYPES and "CDS" in types)
        return tuple(t for t in self.cdsTranslations if t.isValid and t.revisionId == self.revision.revisionId
                     and (showEveryCds or t.featureId == self.selectedFeatureId))

    @property
    def defaultWidth(self) -> int:
        return 760 if self.isCircular else 900

    @property
    def description(self) -> str:
        layout = f"Circular map · {self.rotationDeg:g}° clockwise · {self.zoom:g}×" if self.isCircular else "Linear map"
        layers = []
        if self.options.showFeatures:
            if self.mapFeatureTypes is None or self.revision.moleculeType == "protein":
                layers.append("all feature types")
            elif self.mapFeatureTypes == LANDMARK_TYPES:
                layers.append("landmarks")
            else:
                layers.append("chosen feature types")
        if self.options.showPrimers:
            layers.append("primer features")
        if self.restrictionSites:
            layers.append("restriction recognition and supported cuts")
        if not self.isCircular and self.options.showTranslations:
            frame = self.frameTranslation
            if frame is not None and frame.revisionId == self.revision.revisionId:
                if frame.isValid:
                    layers.append("frame translation")
            elif self.visibleCdsTranslations:
                layers.append("translation tracks")
        if layers:
            layout += f" · {', '.join(layers)}"
        selection = _selectionSummaryLabel(self.selection, self.revision, self.unit)
        return f"{layout}\nFull record · {len(self.revision):,} {self.unit} · {selection}"

    @property
    def unit(self) -> str:
        return "aa" if self.revision.moleculeType == "protein" else "nt"

    def renderSvg(self, width: int, height: int | None = None) -> str:
        """Resize the map using the full title, and return a standalone SVG scene."""
        if width < 320 or (self.isCircular and height is not None and height < 280):
            raise ValueError("Use a width of at least 320 pixels and a circular height of at least 280.")
        return self.scene(width, height).svg

    def scene(
        self, width: int, height: int | None, *, native: bool = False,
        hoveredFeatureId: str | None = None, focusedSite: RecognitionSite | None = None,
        inspector: bool = False, embeddedLinear: bool = False,
    ) -> MapScene:
        """Project the map for a viewport, optionally omitting standalone linear map labels."""
        if self.isCircular:
            if self.revision.topology != "circular":
                raise ValueError("Circular plasmid map export requires circular topology.")
            if height is None:
                height = max(280, round(width * self.viewportHeight / self.viewportWidth))
            return _circularScene(self, width, height, hoveredFeatureId, focusedSite, inspector)
        return _linearScene(
            self, width, height, native, hoveredFeatureId, focusedSite, inspector, embeddedLinear,
        )


def _svgStart(width: int, height: int, name: str, description: str = "",
              background: str = colors.CANVAS, *, fontFamily: str = "sans-serif") -> list[str]:
    shapes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{fontFamily}">',
        f"<title>{_xmlText(name)}</title>",
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
    ]
    if description:
        shapes.insert(2, f"<desc>{_xmlText(description)}</desc>")
    return shapes


def _startMapSvg(source: MapFigureSource, width: int, height: int, inspector: bool) -> list[str]:
    names = ", ".join(dict.fromkeys(site.enzymeName for site in source.restrictionSites))
    description = (
        f"{source.description}\n{width} × {height} px\nRecord {source.revision.recordId}\n"
        f"Revision {source.revision.revisionId}"
    )
    if names:
        description += f"\nRestriction overlay: {names}"
    background = colors.INSPECTOR if inspector else colors.CANVAS
    return _svgStart(width, height, source.displayName, description, background)


def _xmlText(value: str) -> str:
    return escape(_INVALID_XML_CHARACTERS.sub("\ufffd", value))


def _text(
    text: str, x: float, y: float, *, cssClass: str = "", fill: str = colors.MUTED,
    pixels: int = 11, anchor: str = "start", bold: bool = False,
) -> str:
    weight = 700 if bold else 400
    return (
        f'<text class="{cssClass}" x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
        f'fill="{fill}" font-size="{pixels}" '
        f'font-weight="{weight}">{_xmlText(text)}</text>'
    )


def _mapTitle(
    shapes: list[str], name: str, x: float, y: float, width: float,
    *, centered: bool = False, pixels: int = 15, bold: bool = True,
) -> tuple[float, float, float, float]:
    name = " ".join(_INVALID_XML_CHARACTERS.sub("\ufffd", name).split())
    visible = _fitSvgText(name, width - 2, pixels, bold=bold, middle=True)
    shapes.append(_text(
        visible, x, y, cssClass="map-title", fill=colors.INK, pixels=pixels,
        anchor="middle" if centered else "start", bold=bold,
    ))
    textWidth = _textWidth(visible, pixels, bold)
    return (x - textWidth / 2 if centered else x, y - pixels, textWidth, pixels + 3)


def _svgArcPath(cx: float, cy: float, radius: float, startRad: float, sweepRad: float) -> str:
    x0 = cx + radius * cos(startRad)
    y0 = cy + radius * sin(startRad)
    if sweepRad >= tau - 1e-9:
        # A full turn needs two arcs because coincident SVG endpoints draw nothing.
        oppositeX, oppositeY = 2 * cx - x0, 2 * cy - y0
        return (
            f"M {x0:.3f} {y0:.3f} A {radius:.3f} {radius:.3f} 0 1 1 {oppositeX:.3f} {oppositeY:.3f} "
            f"A {radius:.3f} {radius:.3f} 0 1 1 {x0:.3f} {y0:.3f}"
        )
    x1 = cx + radius * cos(startRad + sweepRad)
    y1 = cy + radius * sin(startRad + sweepRad)
    return f"M {x0:.3f} {y0:.3f} A {radius:.3f} {radius:.3f} 0 {int(sweepRad > pi)} 1 {x1:.3f} {y1:.3f}"


def _circularPoint(layout: CircularMapLayout, boundary: int, radialOffset: float) -> tuple[float, float]:
    angle = layout.angleForBoundary(boundary)
    radius = layout.backboneRadius + radialOffset
    return layout.cx + radius * cos(angle), layout.cy + radius * sin(angle)


def _svgLine(x0: float, y0: float, x1: float, y1: float, color: str, width: float, **attributes) -> str:
    extras = "".join(f' {attribute.replace("_", "-")}="{value}"' for attribute, value in attributes.items())
    return (
        f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
        f'stroke="{color}" stroke-width="{width}"{extras}/>'
    )


def _svgPolygon(points: Sequence[tuple[float, float]], fill: str, stroke: str, width: float) -> str:
    coords = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polygon points="{coords}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'


def _restrictionShapes(
    source: MapFigureSource, shapes: list[str], *,
    width: int, height: int, focusedSite: RecognitionSite | None,
    circular: CircularMapLayout | None = None, linear: OverviewLayout | None = None,
) -> tuple[tuple[float, float, RecognitionSite], ...]:
    if not source.restrictionSites:
        return ()
    axisY = 57.0

    def cutPoint(boundary: int, offset: float) -> tuple[float, float]:
        if circular is not None:
            return _circularPoint(circular, boundary, offset)
        return linear.positionX(boundary), axisY - offset

    shapes.append('<g class="restriction-cuts">')
    shownSites = frozenset(source.restrictionSites)
    for cut in source.restrictionCuts:
        names = ", ".join(dict.fromkeys(site.enzymeName for site in cut.sites if site in shownSites))
        if not names:
            continue
        for boundary, direction, color, strand in (
            (cut.topBoundary, 1, colors.GREEN, "top"), (cut.bottomBoundary, -1, colors.RED, "bottom"),
        ):
            shapes.append(
                f'<g class="restriction-{strand}-cut" data-boundary="{boundary}">'
                f'<title>{_xmlText(names)} · {strand} cut at boundary {boundary:,}</title>'
                + _svgLine(*cutPoint(boundary, direction), *cutPoint(boundary, 7 * direction), color, 1.6)
                + "</g>"
            )
    shapes.append('</g><g class="restriction-recognition">')
    markers = []
    recipe = frozenset(source.restrictionRecipe)
    ranked = sorted(source.restrictionSites, key=lambda site: (site == focusedSite, site.enzymeName in recipe))
    for site in ranked:
        x, y = _circularPoint(circular, site.start, 1.0) if circular is not None else (
            linear.positionX(site.start), 73.0,
        )
        color = colors.recognitionMarkerColor(
            isFocused=site == focusedSite, isInRecipe=site.enzymeName in recipe,
            isDefinite=site.certainty == "definite",
        )
        intervals = " and ".join(f"{start + 1:,}–{end:,}" for start, end in site.sourceParts)
        shapes.append(
            f'<g class="recognition-{site.certainty}" data-start="{site.start}">'
            f'<title>{_xmlText(site.enzymeName)} · {site.certainty} recognition · {intervals}</title>'
        )
        for start, end in site.sourceParts:
            if circular is not None:
                startRad = circular.angleForBoundary(start)
                sweep = tau * (end - start) / circular.sequenceLength
                path = _svgArcPath(circular.cx, circular.cy, circular.backboneRadius - 4, startRad, sweep)
                shapes.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.4"/>')
            else:
                shapes.append(_svgLine(linear.positionX(start), y, linear.positionX(end), y, color, 1.6))
        if 0 <= x < width and 0 <= y < height:
            markers.append((x, y, site))
            radius = 2.25 if circular is not None and width < 460 and height < 280 else 4
            shapes.append(_svgPolygon(
                ((x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)),
                color if site.certainty == "definite" else colors.CANVAS, color, 1.3,
            ))
        shapes.append("</g>")
    shapes.append("</g>")
    return tuple(markers)


def _restrictionLegend(source: MapFigureSource, shapes: list[str], width: int, y: float) -> None:
    if not source.restrictionSites:
        return
    names = tuple(dict.fromkeys(site.enzymeName for site in source.restrictionSites))
    entries = [("Recognition", colors.AMBER)]
    if source.restrictionCuts:
        entries.extend((("Top cut", colors.GREEN), ("Bottom cut", colors.RED)))
    x = 16.0
    for label, color in entries:
        shapes.append(_svgLine(x, y - 4, x + 10, y - 4, color, 2))
        shapes.append(_text(label, x + 14, y, pixels=10))
        x += 22 + _textWidth(label, 10, False)
    available = width - x - 16
    if available >= 55:
        namesLabel = _fitSvgText(", ".join(names), available, 10)
        shapes.append(_text(namesLabel, x, y, pixels=10))


def _selectionRectangles(
    source: MapFigureSource, geometry: OverviewLayout, backboneY: int,
) -> list[str]:
    selection = source.selection
    if selection is None or not len(selection):
        return []
    shapes = []
    axisEnd = geometry.left + geometry.width
    for start, end in selection.segments:
        x = min(axisEnd - 2, geometry.positionX(start))
        width = max(2, geometry.positionX(end) - x)
        shapes.append(
            f'<rect class="map-selection" data-start="{start}" data-end="{end}" x="{x:.3f}" '
            f'y="{backboneY - 1}" width="{width:.3f}" height="12" rx="2" fill="{colors.ACCENT}"/>'
        )
    return shapes


def _arcLabelWidth(label: str) -> float:
    # Joined scripts and combining marks need shaping as a whole in a horizontal callout.
    if len(label) > 80 or not label.isascii() or not label.isprintable():
        return float("inf")
    return sum(_textWidth(char, 12, True) for char in label)


def _arcLabel(layout: CircularMapLayout, feature: CircularFeatureArc) -> str:
    # Qt's SVG renderer skips textPath. Tangent glyphs also work in browsers and PNG exports.
    part = feature.longestPart
    radius = (part.rInner + part.rOuter) / 2
    mid = part.startRad + part.sweepRad / 2
    direction = 1 if sin(mid) <= 0 else -1
    angle = mid - direction * _arcLabelWidth(feature.label) / (2 * radius)
    glyphs = []
    for char in feature.label:
        advance = direction * _textWidth(char, 12, True) / radius
        theta = angle + advance / 2
        x, y = layout.cx + radius * cos(theta), layout.cy + radius * sin(theta)
        glyphs.append(f'<text transform="translate({x:.2f} {y:.2f}) rotate({degrees(theta) + 90 * direction:.2f})" '
                      f'y="4" text-anchor="middle">{_xmlText(char)}</text>')
        angle += advance
    return (f'<g class="map-arc-label" aria-label="{_xmlText(feature.label)}" '
            f'font-size="12" font-weight="700" '
            f'fill="{colors.featureTextColor(feature.color)}">{"".join(glyphs)}</g>')


def _circularFeatures(layout: CircularMapLayout, shapes: list[str]) -> None:
    for feature in layout.features:
        active = feature.isSelected or feature.isHovered
        if feature.isBackbone and not active:
            continue
        stroke = colors.ACCENT_INK if active else colors.INK
        weight = 1.8 if active else 0.85
        intervals = ", ".join(f"{p.start + 1:,}–{p.end:,}" for p in feature.parts)
        shapes.append(f'<g class="map-feature" stroke-linejoin="round" stroke-opacity="{1 if active else 0.55}">'
                      f'<title>{_xmlText(feature.label)}</title><desc>{intervals}</desc>')
        radius = (feature.rInner + feature.rOuter) / 2
        for start, sweep in layout.featureGapArcs(feature):
            path = _svgArcPath(layout.cx, layout.cy, radius, start, sweep)
            shapes.append(
                f'<path d="{path}" fill="none" stroke="{feature.color}" stroke-width="1.2" '
                'stroke-dasharray="3,2" opacity="0.7"/>'
            )
        for part in feature.parts:
            points = annularArrowPolygon(
                layout.cx, layout.cy, part.rInner, part.rOuter,
                part.startRad, part.sweepRad, part.strand, part.hasArrowhead,
            )
            shapes.append(_svgPolygon(points, feature.color, stroke, weight))
        if feature.featureId in layout.arcLabelIds:
            shapes.append(_arcLabel(layout, feature))
        shapes.append("</g>")


def _circularCallouts(
    layout: CircularMapLayout, leaders: list[str], shapes: list[str], *, showIntervals: bool,
) -> CircularMapLayout:
    byId = {feature.featureId: feature for feature in layout.features}
    fittedCallouts = []
    for callout in layout.callouts:
        left = callout.side == "left"
        nameHeight = callout.height - 13 if showIntervals else callout.height
        maxLines = max(1, int(nameHeight // 13))
        textX = callout.labelX
        if not showIntervals and maxLines > 1:
            textX += 6 if left else -6
        available = (textX if left else layout.width - textX) - 3
        active = callout.isSelected or callout.isHovered
        feature = byId[callout.featureId]
        detail = ""
        if showIntervals and callout.text == feature.label:
            if feature.partCount == 1:
                part = feature.parts[0]
                direction = {1: " →", -1: " ←"}.get(part.strand, "")
                detail = f"{part.start + 1:,}–{part.end:,}{direction}"
            else:
                detail = f"{len(feature.parts)} mapped intervals"
        pixels = 12 if showIntervals else 11
        text = " ".join(callout.text.split())
        if not showIntervals and maxLines > 1 and text.casefold().endswith(" region"):
            text = text[:-7]
        lines = _fitSvgLines(text, available, pixels, bold=active, maxLines=maxLines)
        if not any(lines):
            continue
        detail = _fitSvgText(detail, available, 10) if detail else ""
        labelWidth = max(_textWidth(line, pixels, active) for line in lines)
        if detail:
            labelWidth = max(labelWidth, _textWidth(detail, 10, False))
        height = 13 * len(lines) + (18 if detail else 2)
        hitY = callout.labelY - (len(lines) - 1) * 6.5 if detail else callout.labelY
        anchor = "end" if left else "start"
        direction = -1 if left else 1
        endX = textX - 5 * direction
        elbowX, elbowY = callout.elbowX, callout.elbowY
        fittedCallouts.append(replace(callout, text="\n".join(lines), labelX=textX, labelY=hitY,
                                      width=labelWidth, height=height))
        leader = colors.ACCENT_INK if active else "#9199a3"
        stub = min(7.0, max(0.0, (endX - elbowX) * direction))
        leaders.append(
            f'<polyline points="{callout.anchorX:.2f},{callout.anchorY:.2f} '
            f'{elbowX:.2f},{elbowY:.2f} {endX - stub * direction:.2f},{callout.labelY:.2f} '
            f'{endX:.2f},{callout.labelY:.2f}" '
            f'fill="none" stroke="{leader}" stroke-width="0.85" stroke-linejoin="round"/>'
        )
        if layout.zoom > 1.05:
            x = textX - (labelWidth if left else 0)
            shapes.append(
                f'<rect x="{x - 3:.2f}" y="{callout.labelY - 7:.2f}" width="{labelWidth + 6:.2f}" '
                f'height="15" rx="4" fill="{colors.CANVAS}" fill-opacity="0.88"/>'
            )
        fill = colors.ACCENT_INK if active else colors.INK
        if detail:
            baseline = callout.labelY - 5 - (len(lines) - 1) * 13
        else:
            baseline = callout.labelY + 4 - (len(lines) - 1) * 6.5
        for index, line in enumerate(lines):
            shapes.append(_text(line, textX, baseline + 13 * index, cssClass="map-callout",
                                fill=fill, pixels=pixels, anchor=anchor, bold=active))
        if detail:
            shapes.append(_text(detail, textX, callout.labelY + 13,
                                cssClass="map-callout-interval", pixels=10, anchor=anchor))
    hiddenCount = layout.hiddenLabelCount + len(layout.callouts) - len(fittedCallouts)
    return replace(layout, callouts=tuple(fittedCallouts), hiddenLabelCount=hiddenCount)


def _circularScene(
    source: MapFigureSource, width: int, height: int,
    hoveredFeatureId: str | None, focusedSite: RecognitionSite | None, inspector: bool,
) -> MapScene:
    scale = circularBaseRadius(width, height) / circularBaseRadius(source.viewportWidth, source.viewportHeight)
    miniature = width < 460 and height < 280
    showIntervals = width >= 540 and height >= 360 and not source.denseLabels and source.zoom <= 1.05
    footer = 52 if source.restrictionSites else 32
    features = source.visibleFeatures
    layout = buildCircularMapLayout(
        len(source.revision), features, width=width, height=height,
        options=source.options, selection=source.selection, visibleInterval=source.visibleInterval,
        hoveredFeatureId=hoveredFeatureId, denseLabels=source.denseLabels, rotationDeg=source.rotationDeg,
        zoom=source.zoom, panX=source.panX * scale, panY=source.panY * scale,
        maxLabels=8 if miniature else 22, labelHeader=20 if miniature else 50,
        labelFooter=20 if miniature else footer, showCalloutIntervals=showIntervals,
        arcLabelWidth=_arcLabelWidth if showIntervals and width >= 720 else None,
    )
    shapes = _startMapSvg(source, width, height, inspector)
    shapes.append(
        f'<circle class="map-backbone" cx="{layout.cx:.3f}" cy="{layout.cy:.3f}" '
        f'r="{layout.backboneRadius:.3f}" fill="none" stroke="#59636f" stroke-width="2"/>'
    )
    if layout.visibleArc is not None:
        arc = layout.visibleArc
        path = _svgArcPath(layout.cx, layout.cy, layout.backboneRadius, arc.startRad, arc.sweepRad)
        shapes.append(f'<path class="map-viewport" d="{path}" fill="none" stroke="{colors.ACCENT}" stroke-width="2"/>')
    for arc in layout.selectionArcs:
        path = _svgArcPath(layout.cx, layout.cy, layout.backboneRadius, arc.startRad, arc.sweepRad)
        shapes.append(
            f'<path class="map-selection" data-start="{arc.start}" data-end="{arc.end}" d="{path}" '
            f'fill="none" stroke="{colors.ACCENT}" stroke-width="6.5" stroke-linecap="round"/>'
        )
    if not miniature:
        origin = layout.ticks[0]
        rulerRadius = hypot(origin.outerX - layout.cx, origin.outerY - layout.cy)
        shapes.append(f'<circle class="map-ruler" cx="{layout.cx:.2f}" cy="{layout.cy:.2f}" '
                      f'r="{rulerRadius:.2f}" fill="none" stroke="#d4dadd" stroke-width="0.8"/>')
    for tick in layout.ticks:
        shapes.append(_svgLine(tick.innerX, tick.innerY, tick.outerX, tick.outerY, "#8e99a1", 0.9))
        if tick.showLabel:
            shapes.append(_text(tick.label, tick.labelX, tick.labelY + 3, cssClass="map-tick",
                                pixels=10, anchor="middle"))
    labels: list[str] = []
    layout = _circularCallouts(layout, shapes, labels, showIntervals=showIntervals)
    _circularFeatures(layout, shapes)
    shapes.extend(labels)
    markers = _restrictionShapes(source, shapes, width=width, height=height, circular=layout, focusedSite=focusedSite)

    titleBounds = _circularTitle(source, layout, shapes, miniature=miniature)
    if not miniature:
        _restrictionLegend(source, shapes, width, height - 31)
    selection = _selectionSummaryLabel(source.selection, source.revision, source.unit)
    if not miniature and source.selection is not None and len(source.selection):
        shapes.append(_text(selection, 16, height - 12, cssClass="selection-summary", fill=colors.ACCENT_INK))
    status = f"{layout.totalVisibleFeatures} mapped features"
    if layout.hiddenLabelCount:
        status = f"{len(layout.callouts) + len(layout.arcLabelIds)} key labels · +{layout.hiddenLabelCount} on zoom"
        hidden = f"+{layout.hiddenLabelCount} crowded labels"
        if not miniature and _textWidth(selection, 11, False) + _textWidth(hidden, 10, False) + 40 < width:
            shapes.append(_text(hidden, width - 16, height - 12, pixels=10, anchor="end"))
    shapes.append("</svg>")
    return MapScene("\n".join(shapes), titleBounds, markers,
                    _mapFilterStatus(source, features, status), circularLayout=layout)


def _mapFilterStatus(source: MapFigureSource, features: tuple[SequenceFeature, ...], status: str) -> str:
    filtered = len(source.revision.features) - len(features)
    if source.options.showFeatures and filtered:
        return f"{status} · {filtered} hidden by type"
    return status


def _compactTitleSpace(layout: CircularMapLayout, halfHeight: float) -> tuple[float, float]:
    """Fit a text block between the actual left and right tracks at the equator."""
    sides = []
    for direction in (pi, 0.0):
        radius = layout.backboneRadius
        for arc in layout.features:
            for part in arc.parts:
                margin = asin(min(1.0, halfHeight / part.rInner))
                if (direction + margin - part.startRad) % tau <= part.sweepRad + 2 * margin:
                    radius = min(radius, part.rInner)
        extent = sqrt(max(0, max(0.0, radius - 4) ** 2 - halfHeight ** 2))
        for tick in layout.ticks:
            if tick.showLabel and abs(tick.labelY - layout.cy) < halfHeight + 7:
                if (tick.labelX < layout.cx) == (direction == pi):
                    extent = min(extent, abs(tick.labelX - layout.cx) - _textWidth(tick.label, 10, False) / 2 - 4)
        sides.append(max(0.0, extent))
    left, right = sides
    return layout.cx + (right - left) / 2, left + right


def _circularTitle(
    source: MapFigureSource, layout: CircularMapLayout, shapes: list[str], *, miniature: bool,
) -> tuple[float, float, float, float]:
    centerWidth = layout.centerTextWidth
    compactTitle = miniature and layout.zoom <= 1.05
    name = " ".join(source.displayName.split())
    centeredPixels = 11 if miniature else 22
    titleX = layout.cx
    bold = True
    if compactTitle:
        titleX, centerWidth = _compactTitleSpace(layout, 12)
        if _textWidth(name, centeredPixels, True) > centerWidth - 2:
            bold = False
            if _textWidth(name, centeredPixels, False) > centerWidth - 2 and _textWidth(name, 10, False) <= centerWidth - 2:
                centeredPixels = 10
        if _textWidth(name, centeredPixels, bold) > centerWidth - 2:
            titleX, centerWidth = _compactTitleSpace(layout, 23)
    elif layout.zoom <= 1.05 and _textWidth(name, centeredPixels, True) > centerWidth - 2:
        centeredPixels = 14
    hasRoomInsideRing = centerWidth >= 36 and 26 <= layout.cy <= layout.height - 36
    isCentered = hasRoomInsideRing and (
        compactTitle or _textWidth(name, centeredPixels, True) <= centerWidth - 2
    )
    if isCentered:
        lines = _fitSvgLines(name, centerWidth - 2, centeredPixels, bold=bold,
                             maxLines=3 if compactTitle else 1)
        top = layout.cy - 4 - (len(lines) - 1) * 7
        lineBounds = [_mapTitle(shapes, line, titleX, top + 13 * index, centerWidth,
                                centered=True, pixels=centeredPixels, bold=bold) for index, line in enumerate(lines)]
        left, _, textWidth, _ = max(lineBounds, key=lambda bounds: bounds[2])
        bounds = (left, lineBounds[0][1], textWidth, (len(lines) - 1) * 13 + centeredPixels + 3)
        subtitleY = top + 13 * (len(lines) - 1) + 16
    else:
        if layout.zoom > 1.05:
            titleWidth = min(layout.width - 32, _textWidth(name, 15, True))
            shapes.append(f'<rect x="9" y="5" width="{titleWidth + 16:.2f}" height="25" rx="4" '
                          f'fill="{colors.CANVAS}" fill-opacity="0.94"/>')
        bounds = _mapTitle(shapes, name, 16, 24, layout.width - 32)
        subtitleY = layout.cy + 12
    shortSubtitle = f"{len(source.revision):,} {source.unit}"
    subtitle = f"{shortSubtitle} · Circular"
    if compactTitle or (hasRoomInsideRing and _textWidth(subtitle, 10, False) > centerWidth):
        subtitle = shortSubtitle
    if hasRoomInsideRing:
        shapes.append(_text(subtitle, layout.cx, subtitleY, pixels=10 if miniature else 12, anchor="middle"))
    else:
        shapes.append(_text(subtitle, 16, 40, pixels=10))
    return bounds


def _linearArrow(
    x0: float, x1: float, y: float, height: float, strand: int | None,
) -> tuple[tuple[float, float], ...]:
    tip = min(6.0, (x1 - x0) * 0.4)
    if strand == 1:
        return ((x0, y), (x1 - tip, y), (x1, y + height / 2), (x1 - tip, y + height), (x0, y + height))
    if strand == -1:
        return ((x0 + tip, y), (x1, y), (x1, y + height), (x0 + tip, y + height), (x0, y + height / 2))
    return ((x0, y), (x1, y), (x1, y + height), (x0, y + height))


@dataclass(frozen=True, slots=True)
class _TranslationTrack:
    label: str
    strand: int
    intervals: tuple[SelectionSegment, ...]
    codons: Sequence[CodonPlacement]


def _translationTracks(source: MapFigureSource) -> tuple[_TranslationTrack, ...]:
    if not source.options.showTranslations:
        return ()
    revisionId = source.revision.revisionId
    frame = source.frameTranslation
    if frame is not None and frame.revisionId == revisionId:
        if not frame.isValid:
            return ()
        first, last = frame.codons[0], frame.codons[-1]
        start, end = sorted((first.positions[0], last.positions[-1]))
        label = f"Frame {frame.frame:+d} · code {frame.translTable}"
        return (_TranslationTrack(label, frame.strand, ((start, end + 1),), frame.codons),)
    tracks = []
    for translation in source.visibleCdsTranslations:
        intervalStart = previous = translation.codons[0].positions[0]
        intervals: list[SelectionSegment] = []
        for codon in translation.codons:
            for position in codon.positions:
                if position != previous and position != previous + translation.strand:
                    intervals.append((min(intervalStart, previous), max(intervalStart, previous) + 1))
                    intervalStart = position
                previous = position
        intervals.append((min(intervalStart, previous), max(intervalStart, previous) + 1))
        label = f"{translation.label} · CDS · code {translation.translTable}"
        tracks.append(_TranslationTrack(label, translation.strand, tuple(intervals), translation.codons))
    return tuple(tracks)


def _drawTranslationTrack(
    shapes: list[str], track: _TranslationTrack, geometry: OverviewLayout, y: float,
) -> None:
    label = _fitSvgText(track.label, geometry.width, 10)
    shapes.append(_text(label, geometry.left, y, cssClass="map-translation-title", pixels=10))
    showsCodons = 3 * geometry.width / geometry.sequenceLength >= 13
    shapes.append(f'<g class="map-translation" data-strand="{track.strand}">')
    for start, end in track.intervals:
        x0, x1 = geometry.positionX(start), geometry.positionX(end)
        if showsCodons:
            shapes.append(_svgLine(x0, y + 16, x1, y + 16, "#826da4", 1.2))
        else:
            shapes.append(_svgPolygon(_linearArrow(x0, x1, y + 4, 6, track.strand), "#d4caef", "none", 0))
    if showsCodons:
        for codon in track.codons:
            x = geometry.positionX(codon.centerPosition) + geometry.width / (2 * geometry.sequenceLength)
            shapes.append(_text(codon.aminoAcid, x, y + 13, cssClass="map-codon",
                                pixels=9, anchor="middle", fill=colors.ACCENT_INK))
    shapes.append("</g>")


def _linearScene(
    source: MapFigureSource, width: int, height: int | None, native: bool,
    hoveredFeatureId: str | None, focusedSite: RecognitionSite | None, inspector: bool,
    embedded: bool,
) -> MapScene:
    revision = source.revision
    margin = 22.0
    geometry = OverviewLayout(len(revision), margin, width - 2 * margin)
    features = source.visibleFeatures
    laneFeatures = []
    backboneTitle = ""
    for feature in features:
        if isWholeRecordSource(feature, len(revision)):
            if source.options.showFeatures and not backboneTitle:
                backboneTitle = f"<title>{_xmlText(feature.label)}</title>"
        else:
            laneFeatures.append(feature)
    spans = buildLinearFeatureSpans(geometry, tuple(laneFeatures), source.options)
    laneCount = max((span.lane + 1 for span in spans), default=0)
    translations = _translationTracks(source)
    compact = embedded or (inspector and height is not None and height < 240)
    lanePitch = 17 if compact else 20
    backboneY, tickY, tracksTop = (8, 34, 41) if embedded else (52, 78, 86)
    if source.restrictionSites and not embedded:
        tickY, tracksTop = (86, 98) if compact else (91, 108)
    translationTop = tracksTop + laneCount * lanePitch + 9
    footerHeight = 6 if embedded else (47 if source.restrictionSites else 29)
    contentHeight = translationTop + len(translations) * 25 + footerHeight
    if height is None:
        height = max(180, contentHeight)
    elif not native and height < contentHeight:
        raise ValueError(f"Use a height of at least {contentHeight} pixels for these map tracks.")
    tracksBottom = height - footerHeight
    if embedded:
        shapes = _svgStart(
            width, height, source.displayName, background=colors.INSPECTOR, fontFamily="DejaVu Sans",
        )
        titleBounds = (0.0, 0.0, 0.0, 0.0)
    else:
        shapes = _startMapSvg(source, width, height, inspector)
        titleBounds = _mapTitle(shapes, source.displayName, margin, 24, geometry.width)
        topology = revision.topology or "topology unknown"
        if revision.topology == "circular":
            topology = "circular · stored coordinates"
        subtitle = f"{revision.moleculeType} · {len(revision):,} {source.unit} · {topology}"
        shapes.append(_text(subtitle, margin, 42, pixels=10))
    shapes.append(f'<rect class="map-backbone" x="{margin}" y="{backboneY}" width="{geometry.width}" '
                  f'height="10" rx="5" fill="{colors.LINE}">{backboneTitle}</rect>')
    shapes.extend(_selectionRectangles(source, geometry, backboneY))
    if not embedded and source.visibleInterval is not None and source.visibleInterval[0] < source.visibleInterval[1]:
        x0, x1 = (geometry.positionX(boundary) for boundary in source.visibleInterval)
        shapes.append(
            f'<rect class="map-viewport" x="{x0:.2f}" y="48" width="{x1 - x0:.2f}" height="18" '
            f'rx="3" fill="none" stroke="{colors.ACCENT}" stroke-width="1.2"/>'
        )
    markers = () if embedded else _restrictionShapes(
        source, shapes, width=width, height=height, linear=geometry, focusedSite=focusedSite,
    )
    _linearScale(shapes, geometry, tickY)
    linearHits = _linearFeatures(
        shapes, source, spans, geometry, tracksTop, tracksBottom, lanePitch, hoveredFeatureId,
    )
    visibleTranslations = 0
    for index, track in enumerate(translations):
        y = translationTop + index * 25
        if y + 17 <= tracksBottom:
            _drawTranslationTrack(shapes, track, geometry, y)
            visibleTranslations += 1
    if not embedded:
        _linearSelectionLabel(shapes, source, geometry, height)
        _restrictionLegend(source, shapes, width, height - 31)
    hiddenCount = len(spans) - len(linearHits) + len(translations) - visibleTranslations
    status = f"{len(linearHits)} mapped segments"
    if hiddenCount:
        status += f" · {hiddenCount} below canvas"
        if compact and not embedded and not source.selection:
            shapes.append(_text(f"{hiddenCount} below canvas", geometry.left + geometry.width,
                                height - 12, anchor="end", pixels=10))
    shapes.append("</svg>")
    return MapScene("\n".join(shapes), titleBounds, markers, _mapFilterStatus(source, features, status),
                    linearGeometry=geometry, linearHits=linearHits)


def _linearSelectionLabel(
    shapes: list[str], source: MapFigureSource,
    geometry: OverviewLayout, height: int,
) -> None:
    summary = _selectionSummaryLabel(source.selection, source.revision, source.unit)
    shapes.append(_text(summary, geometry.left, height - 12, cssClass="selection-summary", fill=colors.ACCENT_INK))
    if source.selection is None or source.selection.featureId is None:
        return
    selectedFeature = next((f for f in source.revision.features if f.featureId == source.selectedFeatureId), None)
    if selectedFeature is not None:
        remaining = geometry.width - _textWidth(summary, 11, False) - 12
        label = _fitSvgText(" ".join(selectedFeature.label.split()), remaining, 11, bold=True)
        if label:
            shapes.append(_text(label, geometry.left + geometry.width, height - 12,
                                cssClass="selected-feature-label", anchor="end", fill=colors.INK, bold=True))


def _linearScale(shapes: list[str], geometry: OverviewLayout, y: float) -> None:
    length = geometry.sequenceLength
    power = 10 ** max(0, len(str(max(1, length // 5))) - 1)
    step = next(step for step in (power, power * 2, power * 5, power * 10) if length / step <= 6)
    lastX = geometry.positionX(length)
    shapes.append(_text("1", geometry.left, y, cssClass="map-tick", pixels=10))
    occupied = geometry.left + _textWidth("1", 10, False)
    endLabel = f"{length:,}"
    lastLeft = lastX - _textWidth(endLabel, 10, False)
    for position in range(step, length, step):
        x = geometry.positionX(position - 1)
        label = f"{position:,}"
        halfWidth = _textWidth(label, 10, False) / 2
        if occupied + 6 <= x - halfWidth and x + halfWidth <= lastLeft - 6:
            shapes.append(_text(label, x, y, cssClass="map-tick", pixels=10, anchor="middle"))
            occupied = x + halfWidth
    if length > 1:
        shapes.append(_text(endLabel, lastX, y, cssClass="map-tick", pixels=10, anchor="end"))


def _linearFeatureLabel(shapes: list[str], span: LinearFeatureSpan, y: float, right: float, fill: str) -> float:
    """Fit the label inside its arrow or in the gap after it. Return the clickable right edge."""
    x0, x1 = span.xStart, span.xEnd
    name = " ".join(span.feature.label.split())
    inside = x1 - x0 - 7
    label = name
    for candidate in dict.fromkeys((name, name.partition(" (")[0])):
        textWidth = _textWidth(candidate, 10, True)
        if textWidth <= inside:
            label = candidate
            break
        if textWidth <= right - x1 - 9:
            shapes.append(_text(candidate, x1 + 4, y + 10.5, fill=colors.INK, pixels=10, bold=True))
            return x1 + 4 + textWidth
    else:
        label = _fitSvgText(name, inside, 10, bold=True) if x1 - x0 >= 26 else ""
    if label:
        shapes.append(_text(label, (x0 + x1) / 2, y + 10.5, anchor="middle",
                            fill=colors.featureTextColor(fill), pixels=10, bold=True))
    return x1


def _linearFeatures(
    shapes: list[str], source: MapFigureSource, spans: tuple[LinearFeatureSpan, ...],
    geometry: OverviewLayout, tracksTop: float, tracksBottom: float,
    pitch: float, hoveredFeatureId: str | None,
) -> tuple[tuple[float, float, float, float, str], ...]:
    hits = []
    selectionId = source.selection.featureId if source.selection is not None else None
    lanes: dict[int, list[LinearFeatureSpan]] = {}
    for span in spans:
        lanes.setdefault(span.lane, []).append(span)
    capacity = max(0, int((tracksBottom - tracksTop - 14) // pitch) + 1)
    visible = sorted(lanes)
    if capacity < len(lanes):
        selectedLanes = {s.lane for s in spans if s.feature.featureId == selectionId}
        visible.sort(key=lambda lane: lane not in selectedLanes)
        visible = sorted(visible[:capacity])
    for row, lane in enumerate(visible):
        y = tracksTop + row * pitch
        neighbors = lanes[lane]
        for index, span in enumerate(neighbors):
            feature = span.feature
            x0, x1 = span.xStart, span.xEnd
            selected = feature.featureId == selectionId
            strand = span.strand if span.partIndex == len(feature.parts) - 1 else None
            hovered = feature.featureId == hoveredFeatureId
            stroke = colors.INK if selected else (colors.ACCENT if hovered else colors.CANVAS)
            fill = annotationColor(feature)
            shapes.append(f'<g class="map-feature"><title>{_xmlText(feature.label)}</title>')
            shapes.append(_svgPolygon(_linearArrow(x0, x1, y, 14, strand), fill, stroke, 1.2 if selected else 0.75))
            right = neighbors[index + 1].xStart if index + 1 < len(neighbors) else geometry.left + geometry.width
            hitRight = _linearFeatureLabel(shapes, span, y, right, fill)
            hits.append((x0, y, hitRight - x0, 14, feature.featureId))
            shapes.append("</g>")
    return tuple(hits)


def plasmidMapSvg(
    session: DocumentSession, *, width: int = 760, height: int = 580,
    options: LayerOptions = LayerOptions(), denseLabels: bool = False, rotationDeg: float = 0.0,
    zoom: float = 1.0, panX: float = 0.0, panY: float = 0.0, displayName: str | None = None,
    mapFeatureTypes: frozenset[str] | None = LANDMARK_TYPES,
) -> str:
    """Return the same circular map SVG used by the native canvas and figure preview."""
    revision = session.current
    if revision is None:
        raise ValueError("Open a sequence before exporting a map.")
    source = MapFigureSource(
        revision, displayName or revision.name, True, session.selection, options,
        denseLabels=denseLabels, rotationDeg=rotationDeg, zoom=zoom, panX=panX, panY=panY,
        viewportWidth=width, viewportHeight=height, mapFeatureTypes=mapFeatureTypes,
    )
    return source.renderSvg(width, height)


def linearMapSvg(
    session: DocumentSession, *, width: int = 900, options: LayerOptions = LayerOptions(),
    displayName: str | None = None, mapFeatureTypes: frozenset[str] | None = LANDMARK_TYPES,
) -> str:
    """Return the annotated map of the full stored coordinate axis."""
    revision = session.current
    if revision is None:
        raise ValueError("Open a sequence before exporting a map.")
    source = MapFigureSource(revision, displayName or revision.name, False, session.selection, options,
                             mapFeatureTypes=mapFeatureTypes)
    return source.renderSvg(width)
