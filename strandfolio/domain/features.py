"""Immutable feature projections and deterministic annotation fingerprints."""

import base64
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from Bio.SeqFeature import (
    AfterPosition,
    BeforePosition,
    BetweenPosition,
    CompoundLocation,
    ExactPosition,
    OneOfPosition,
    Reference,
    SeqFeature,
    SimpleLocation,
    UncertainPosition,
    UnknownPosition,
    WithinPosition,
)

from strandfolio.domain.coordinates import SelectionSegment


FEATURE_LABEL_QUALIFIERS = ("label", "gene", "product", "bound_moiety")
FEATURE_COLOR_QUALIFIER = "vibegene_color"
LABEL_EVIDENCE_QUALIFIER = "vibegene_match"
ANNOTATION_DECISIONS_KEY = "vibegene_label_decisions"
ANNOTATION_CURATION_QUALIFIER = "vibegene_curation"
_FEATURE_COLOR = re.compile(r"#[A-Fa-f0-9]{6}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class LabelEvidence:
    """An exact full-reference DNA match, with parts in feature traversal order."""

    catalogueRelease: str
    referenceId: str
    referenceSourceId: str
    referenceSourceRecord: str
    referenceSequenceSha256: str
    parts: tuple[SelectionSegment, ...]
    strand: int | None

    def __post_init__(self) -> None:
        identifiers = (self.referenceId, self.referenceSourceId, self.referenceSourceRecord)
        hashes = (self.catalogueRelease, self.referenceSequenceSha256)
        if (
            any(type(value) is not str or not value.strip() for value in identifiers)
            or any(type(value) is not str or not _SHA256.fullmatch(value) for value in hashes)
            or type(self.parts) is not tuple
            or not self.parts
            or any(
                type(part) is not tuple or len(part) != 2
                or any(type(boundary) is not int for boundary in part)
                or not 0 <= part[0] < part[1]
                for part in self.parts
            )
            or (
                self.strand is not None
                and (type(self.strand) is not int or self.strand not in (1, -1))
            )
        ):
            raise ValueError("Invalid exact catalogue match evidence.")

    def qualifierValues(self) -> tuple[str, ...]:
        """Serialize evidence into short textual qualifier values for native and GenBank storage."""
        payload = (
            self.catalogueRelease, self.referenceId, self.referenceSourceId,
            self.referenceSourceRecord, self.referenceSequenceSha256, self.parts, self.strand,
        )
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        encoded = base64.urlsafe_b64encode(serialized).decode("ascii")
        # Each value stays on one GenBank feature qualifier line.
        return ("1", *(encoded[start:start + 38] for start in range(0, len(encoded), 38)))


def _projectLabelEvidence(values: tuple[str, ...]) -> LabelEvidence | None:
    if len(values) < 2 or values[0] != "1":
        return None
    try:
        encoded = base64.b64decode("".join(values[1:]), altchars=b"-_", validate=True)
        release, reference, source, sourceRecord, sequenceHash, parts, strand = json.loads(encoded)
        return LabelEvidence(
            release, reference, source, sourceRecord, sequenceHash,
            tuple(tuple(part) for part in parts), strand,
        )
    except (TypeError, ValueError):
        # An imported record can contain an unrelated or malformed qualifier of this name.
        return None


def isFeatureColor(value: object) -> bool:
    """Return whether a color is an explicit six-digit RGB label color."""
    return bool(type(value) is str and _FEATURE_COLOR.fullmatch(value))


@dataclass(frozen=True, slots=True)
class FeaturePart:
    """One part of an annotation location, which may have uncertain bounds.

    Coordinates are zero-based, half-open, and remain in the source's part order.
    Only nonempty intervals with exact local coordinates are selectable.
    """

    start: int | None
    end: int | None
    strand: int | None
    locationText: str
    isSelectable: bool


@dataclass(frozen=True, slots=True)
class SequenceFeature:
    """Immutable feature data for display.

    featureId is an opaque identity retained when naming the record; sourceId
    retains the imported identifier, including missing or duplicate source IDs.
    Qualifiers here are display strings. toSeqRecord() preserves original types.
    """

    featureId: str
    type: str
    label: str
    parts: tuple[FeaturePart, ...]
    strand: int | None
    operator: str | None
    qualifiers: tuple[tuple[str, tuple[str, ...]], ...]
    sourceId: str
    labelSource: str | None = None
    color: str | None = None
    labelEvidence: LabelEvidence | None = None


@dataclass(frozen=True, slots=True)
class ReferenceRange:
    """A zero-based, half-open range on the referenced record."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class PublicationReference:
    """An imported citation with ordered ranges, empty when unspecified."""

    authors: str
    consortium: str
    title: str
    journal: str
    pubmedId: str
    medlineId: str
    comment: str
    ranges: tuple[ReferenceRange, ...]


def projectReference(reference: object, sequenceLength: int) -> PublicationReference:
    """Validate and isolate a Biopython publication reference."""
    if type(reference) is not Reference:
        raise ValueError("Record references must be Biopython Reference values.")
    citation = (
        reference.authors, reference.consrtm, reference.title, reference.journal,
        reference.pubmed_id, reference.medline_id, reference.comment,
    )
    if any(type(value) is not str for value in citation):
        raise ValueError("Publication reference fields must be strings.")
    if type(reference.location) not in (list, tuple):
        raise ValueError("Publication reference ranges must be an ordered list or tuple.")
    ranges = []
    for location in reference.location:
        if (
            type(location) is not SimpleLocation
            or type(location.start) is not ExactPosition
            or type(location.end) is not ExactPosition
            or location.strand is not None
            or location.ref is not None
            or location.ref_db is not None
        ):
            raise ValueError("Publication reference ranges must be exact, unstranded and local.")
        start, end = int(location.start), int(location.end)
        if not 0 <= start < end <= sequenceLength:
            raise ValueError("A publication reference range is empty or outside the sequence.")
        ranges.append(ReferenceRange(start, end))
    return PublicationReference(*citation, ranges=tuple(ranges))


def _nativeDatetime(value: datetime) -> list:
    zone = value.tzinfo
    if zone is None:
        encodedZone = None
    elif type(zone) is timezone:
        encodedZone = ["fixed", value.tzname()]
    elif type(zone) is ZoneInfo and zone.key is not None:
        try:
            standard = value.replace(tzinfo=ZoneInfo(zone.key))
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                f"Native revision datetime timezone {zone.key!r} is unavailable."
            ) from error
        if (standard.utcoffset(), standard.tzname()) != (value.utcoffset(), value.tzname()):
            raise ValueError(f"Native datetime timezone {zone.key!r} conflicts with IANA data.")
        encodedZone = ["zoneinfo", zone.key]
    else:
        raise ValueError("Native revision datetimes require a fixed-offset or named IANA timezone.")
    return ["datetime", value.isoformat(), value.fold, encodedZone]


def canonicalMetadata(
    value: object, _activeContainers: set[int] | None = None, *, native: bool = False,
) -> object:
    """Encode supported metadata deterministically, preserving value/container types.

    Unsupported objects, nonfinite numbers and cyclic containers are rejected.
    Fingerprints sort dictionary keys and use ISO datetimes. Native snapshots
    preserve dictionary order and include the datetime timezone and fold.
    """
    if value is None:
        return ["none"]
    if type(value) in (str, bool, int):
        return [type(value).__name__, value]
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Metadata numbers must be finite.")
        return ["float", value]
    if type(value) in (datetime, date):
        if type(value) is datetime and native:
            return _nativeDatetime(value)
        return [type(value).__name__, value.isoformat()]
    if type(value) not in (dict, list, tuple):
        raise ValueError(f"Unsupported metadata type: {type(value).__name__}.")
    activeContainers = set() if _activeContainers is None else _activeContainers
    if id(value) in activeContainers:
        raise ValueError("Cyclic metadata cannot be preserved in a content fingerprint.")
    activeContainers.add(id(value))
    try:
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise ValueError("Annotation and qualifier keys must be strings.")
            keys = value if native else sorted(value)
            entries = [
                [key, canonicalMetadata(value[key], activeContainers, native=native)]
                for key in keys
            ]
        else:
            entries = [
                canonicalMetadata(item, activeContainers, native=native)
                for item in value
            ]
        return [type(value).__name__, entries]
    finally:
        activeContainers.remove(id(value))


def _canonicalPosition(position: object, *, native: bool) -> dict[str, object]:
    """Encode Biopython positions with their type and any uncertainty."""
    if type(position) is UnknownPosition:
        return {"type": "UnknownPosition"}
    allowed = (ExactPosition, BeforePosition, AfterPosition, WithinPosition,
               BetweenPosition, OneOfPosition, UncertainPosition)
    if type(position) not in allowed:
        raise ValueError(f"Unsupported feature position: {type(position).__name__}.")
    result = {"type": type(position).__name__, "value": int(position)}
    if type(position) in (WithinPosition, BetweenPosition):
        left, right = position._left, position._right
        if native and (
            type(left) is not int or type(right) is not int or left > right
            or int(position) not in (left, right)
        ):
            raise ValueError("Native fuzzy positions require ordered integer bounds and a default.")
        result.update(left=left, right=right)
    elif type(position) is OneOfPosition:
        choices = position.position_choices
        if native:
            if type(choices) not in (list, tuple) or int(position) not in choices:
                raise ValueError("Native fuzzy positions require ordered choices and a default.")
            result["choice_container"] = type(choices).__name__
        result["choices"] = [_canonicalPosition(choice, native=native) for choice in choices]
    return result


def _canonicalPart(part: SimpleLocation, sequenceLength: int, *, native: bool) -> dict[str, object]:
    if type(part) is not SimpleLocation:
        raise ValueError("Only Biopython SimpleLocation parts are supported.")
    if part.ref is not None and not isinstance(part.ref, str):
        raise ValueError("Remote feature references must be strings.")
    if part.ref_db is not None and not isinstance(part.ref_db, str):
        raise ValueError("Remote feature reference databases must be strings.")
    strand = part.strand
    if native:
        if strand is not None and (type(strand) is not int or strand not in (-1, 0, 1)):
            raise ValueError("Native feature strand must be -1, 0, 1 or unspecified.")
        if isinstance(part.start, int) and isinstance(part.end, int) and part.start > part.end:
            raise ValueError("A native feature end cannot precede its start.")
    local = part.ref is None and part.ref_db is None
    exact = type(part.start) is ExactPosition and type(part.end) is ExactPosition
    if local and exact and not 0 <= int(part.start) <= int(part.end) <= sequenceLength:
        raise ValueError("An exact local feature location lies outside the sequence.")
    return {
        "start": _canonicalPosition(part.start, native=native),
        "end": _canonicalPosition(part.end, native=native),
        "strand": strand, "ref": part.ref, "ref_db": part.ref_db,
    }


def canonicalFeature(
    feature: SeqFeature, sequenceLength: int, *, native: bool = False,
) -> dict[str, object]:
    """Validate and encode a feature's preserved biological content, excluding internal IDs."""
    if type(feature) is not SeqFeature:
        raise ValueError("Only standard Biopython SeqFeature values are supported.")
    if not isinstance(feature.type, str) or not isinstance(feature.id, str):
        raise ValueError("Feature type and imported ID must be strings.")
    if not isinstance(feature.qualifiers, dict):
        raise ValueError("Feature qualifiers must be a dictionary.")
    location = feature.location
    if location is None:
        encodedLocation = None
    elif type(location) in (SimpleLocation, CompoundLocation):
        if type(location) is CompoundLocation and not isinstance(location.operator, str):
            raise ValueError("Compound-location operators must be strings.")
        encodedLocation = {
            "type": type(location).__name__,
            "operator": location.operator if type(location) is CompoundLocation else None,
            "parts": [
                _canonicalPart(part, sequenceLength, native=native) for part in location.parts
            ],
        }
        if native and type(location) is CompoundLocation:
            if type(location.parts) not in (list, tuple) or len(location.parts) < 2:
                raise ValueError("Native compound locations require at least two ordered parts.")
            encodedLocation["part_container"] = type(location.parts).__name__
    else:
        raise ValueError("Unsupported Biopython feature location type.")
    return {
        "type": feature.type, "source_id": feature.id, "location": encodedLocation,
        "qualifiers": canonicalMetadata(feature.qualifiers, native=native),
    }


def _projectPart(part: SimpleLocation) -> FeaturePart:
    start = None if type(part.start) is UnknownPosition else int(part.start)
    end = None if type(part.end) is UnknownPosition else int(part.end)
    isSelectable = (
        part.ref is None and part.ref_db is None
        and type(part.start) is ExactPosition and type(part.end) is ExactPosition
        and start < end
    )
    return FeaturePart(start, end, part.strand, str(part), isSelectable)


def projectFeature(feature: SeqFeature, featureId: str) -> SequenceFeature:
    """Project an already validated feature without exposing mutable payload values."""
    qualifiers = []
    for key, value in sorted(feature.qualifiers.items()):
        values = value if isinstance(value, (list, tuple)) else (value,)
        qualifiers.append((key, tuple(str(item) for item in values)))
    displayValues = dict(qualifiers)
    label = ""
    labelSource = None
    for key in FEATURE_LABEL_QUALIFIERS:
        values = displayValues.get(key, ())
        if values and values[0].strip():
            label = values[0]
            labelSource = key
            break
    if not label:
        label = feature.id if feature.id not in ("", "<unknown id>") else feature.type or "Feature"
    location = feature.location
    parts = tuple(_projectPart(part) for part in location.parts) if location is not None else ()
    strand = location.strand if location is not None else None
    color = next(iter(displayValues.get(FEATURE_COLOR_QUALIFIER, ())), None)
    evidence = _projectLabelEvidence(displayValues.get(LABEL_EVIDENCE_QUALIFIER, ()))
    if evidence is not None and (
        strand != evidence.strand or any(not part.isSelectable for part in parts)
        or tuple((part.start, part.end) for part in parts) != evidence.parts
        or any(part.strand != strand for part in parts)
    ):
        evidence = None
    return SequenceFeature(
        featureId=featureId, type=feature.type, label=label,
        parts=parts, strand=strand,
        operator=location.operator if type(location) is CompoundLocation else None,
        qualifiers=tuple(qualifiers), sourceId=feature.id, labelSource=labelSource,
        color=color if isFeatureColor(color) else None, labelEvidence=evidence,
    )
