"""Versioned native JSON for the supported immutable sequence snapshot."""

from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import math
from typing import Callable, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from Bio.Seq import Seq
from Bio.SeqFeature import (
    AfterPosition,
    BeforePosition,
    BetweenPosition,
    CompoundLocation,
    ExactPosition,
    OneOfPosition,
    Position,
    Reference,
    SeqFeature,
    SimpleLocation,
    UncertainPosition,
    UnknownPosition,
    WithinPosition,
)
from Bio.SeqRecord import SeqRecord

from strandfolio.domain.features import canonicalFeature, canonicalMetadata
from strandfolio.domain.fragments import DuplexGeometry, FragmentEnd, FragmentSpan
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision


_SCHEMA = "vibegene.project.revision.v1"
_PRODUCT_SCHEMA = "vibegene.project.revision.v2"
_REFERENCE_FIELDS = ("authors", "consrtm", "title", "journal", "pubmed_id", "medline_id", "comment")
_INTEGER_POSITIONS = {
    positionType.__name__: positionType
    for positionType in (ExactPosition, BeforePosition, AfterPosition, UncertainPosition)
}
_BOUNDED_POSITIONS = {"BetweenPosition": BetweenPosition, "WithinPosition": WithinPosition}
_Item = TypeVar("_Item")


def encodeCanonicalJson(value: object) -> str:
    """Encode deterministic ASCII JSON for recorded evidence and its checksums."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
    )


def revisionPayloadWithoutName(
    encoded: dict[str, object], *, independent: bool = False,
) -> dict[str, object]:
    """Compare native values across a rename or an explicit independent product copy."""
    ignored = {"name", "revision_id", "content_digest"}
    if independent:
        ignored.update(("record_id", "feature_ids"))
    return {name: value for name, value in encoded.items() if name not in ignored}


def renamedRevisionPayload(
    revision: SequenceRevision, *, independent: bool = False,
) -> dict[str, object]:
    """Encode the values compared across a rename or an independent copy."""
    return revisionPayloadWithoutName(json.loads(encodeRevision(revision)), independent=independent)


def matchesRecordedProduct(revision: SequenceRevision, output: SequenceRevision) -> bool:
    """Compare exact product evidence, allowing a rename or an independent adoption."""
    independent = revision.recordId != output.recordId
    return (renamedRevisionPayload(revision, independent=independent)
            == renamedRevisionPayload(output, independent=independent))


def _encodeOrdered(
    values: list[_Item] | tuple[_Item, ...], encode: Callable[[_Item], object],
) -> list:
    if type(values) not in (list, tuple):
        raise ValueError("Native revision collections must be lists or tuples.")
    return [type(values).__name__, [encode(value) for value in values]]


def _encodeReference(reference: Reference) -> dict[str, object]:
    return {
        "fields": {field: getattr(reference, field) for field in _REFERENCE_FIELDS},
        "ranges": _encodeOrdered(reference.location, lambda span: [int(span.start), int(span.end)]),
    }


def _encodeAnnotation(key: str, value: object) -> object:
    if key == "references":
        return _encodeOrdered(value, _encodeReference)
    return canonicalMetadata(value, native=True)


def sameRevisionSnapshot(first: SequenceRevision, second: SequenceRevision) -> bool:
    """Compare complete native content and provenance before reusing an immutable revision."""
    return first is second or (
        first.revisionId == second.revisionId and first.contentDigest == second.contentDigest
        and encodeRevision(first) == encodeRevision(second)
    )


def encodeRevision(revision: SequenceRevision) -> str:
    """Encode identities, typed content and source links as deterministic native JSON."""
    record = revision.toSeqRecord()
    if revision.moleculeDeclaration is None:
        del record.annotations["molecule_type"]
    document = {
        "schema": _SCHEMA,
        "record_id": revision.recordId,
        "revision_id": revision.revisionId,
        "content_digest": revision.contentDigest,
        "source_artifact_id": _artifactId(revision.sourceArtifactId),
        "source_ordinal": revision.sourceOrdinal,
        "source_path": str(revision.sourcePath) if revision.sourcePath is not None else None,
        "source_format": _optionalText(revision.sourceFormat),
        "molecule_type": revision.moleculeType,
        "molecule_declaration": revision.moleculeDeclaration,
        "strand_state": revision.strandState,
        "name": revision.name,
        "source_id": revision.sourceId,
        "description": revision.description,
        "sequence": revision.sequence,
        "annotations": [
            [key, _encodeAnnotation(key, value)] for key, value in record.annotations.items()
        ],
        "dbxrefs": _encodeOrdered(record.dbxrefs, str),
        "features": _encodeOrdered(
            record.features, lambda feature: canonicalFeature(feature, len(record), native=True),
        ),
        "feature_ids": [feature.featureId for feature in revision.features],
    }
    if revision.duplexGeometry is not None or revision.outputReference is not None:
        document["schema"] = _PRODUCT_SCHEMA
        for key, value in (("duplex_geometry", revision.duplexGeometry),
                           ("output_reference", revision.outputReference)):
            document[key] = None if value is None else asdict(value)
    return encodeCanonicalJson(document)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("Expected a string in the stored revision.")
    return value


def _optionalText(value: object) -> str | None:
    return None if value is None else _text(value)


def _artifactId(value: object) -> str | None:
    identifier = _optionalText(value)
    if identifier is not None and (not identifier.strip() or "\x00" in identifier):
        raise ValueError("Invalid source artifact identity in the stored revision.")
    return identifier


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Expected an integer in the stored revision.")
    return value


def _decodeOrdered(
    encoded: object, decode: Callable[[object], _Item],
) -> list[_Item] | tuple[_Item, ...]:
    match encoded:
        case ["list", list(values)]:
            return [decode(value) for value in values]
        case ["tuple", list(values)]:
            return tuple(decode(value) for value in values)
    raise ValueError("Expected a typed list or tuple in the stored revision.")


def _decodeEntries(entries: object, decode: Callable[[str, object], _Item]) -> dict[str, _Item]:
    if type(entries) is not list:
        raise ValueError("Expected dictionary entries in the stored revision.")
    result = {}
    for entry in entries:
        match entry:
            case [str(key), value] if key not in result:
                result[key] = decode(key, value)
            case _:
                raise ValueError("Invalid or duplicate dictionary key in the stored revision.")
    return result


def _decodeDatetime(value: str, fold: object, encodedZone: object) -> datetime:
    fold = _integer(fold)
    if fold not in (0, 1):
        raise ValueError("A stored datetime fold must be zero or one.")
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    match encodedZone:
        case None if offset is None:
            zone = None
        case ["fixed", str(name)] if offset is not None:
            zone = timezone(offset, name)
        case ["zoneinfo", str(key)] if offset is not None:
            try:
                zone = ZoneInfo(key)
            except ZoneInfoNotFoundError as error:
                raise ValueError(f"Stored datetime timezone {key!r} is unavailable.") from error
        case _:
            raise ValueError("Invalid timezone in the stored revision.")
    result = parsed.replace(tzinfo=zone, fold=fold)
    if result.isoformat() != value:
        raise ValueError("Stored datetime offset conflicts with its timezone.")
    return result


def _decodeMetadata(encoded: object) -> object:
    match encoded:
        case ["none"]:
            return None
        case ["str", str(value)] | ["bool", bool(value)]:
            return value
        case ["int", value]:
            return _integer(value)
        case ["float", float(value)] if math.isfinite(value):
            return value
        case ["date", str(value)]:
            return date.fromisoformat(value)
        case ["datetime", str(value), fold, zone]:
            return _decodeDatetime(value, fold, zone)
        case ["dict", entries]:
            return _decodeEntries(entries, lambda key, value: _decodeMetadata(value))
        case ["list" | "tuple", _]:
            return _decodeOrdered(encoded, _decodeMetadata)
    raise ValueError("Invalid typed metadata in the stored revision.")


def _decodeRange(encoded: object) -> SimpleLocation:
    match encoded:
        case [start, end]:
            return SimpleLocation(_integer(start), _integer(end))
    raise ValueError("Invalid publication range in the stored revision.")


def _decodeReference(encoded: dict[str, object]) -> Reference:
    reference = Reference()
    for field in _REFERENCE_FIELDS:
        setattr(reference, field, _text(encoded["fields"][field]))
    reference.location = _decodeOrdered(encoded["ranges"], _decodeRange)
    return reference


def _decodePosition(encoded: dict[str, object]) -> Position:
    positionType = _text(encoded["type"])
    if positionType == "UnknownPosition":
        return UnknownPosition()
    value = _integer(encoded["value"])
    if positionType == "OneOfPosition":
        choices = _decodeOrdered([encoded["choice_container"], encoded["choices"]], _decodePosition)
        return OneOfPosition(value, choices)
    if positionType in _BOUNDED_POSITIONS:
        left, right = _integer(encoded["left"]), _integer(encoded["right"])
        if value not in (left, right):
            raise ValueError("A fuzzy position's default must match one of its bounds.")
        return _BOUNDED_POSITIONS[positionType](value, left, right)
    if positionType in _INTEGER_POSITIONS:
        return _INTEGER_POSITIONS[positionType](value)
    raise ValueError(f"Unsupported stored feature position {positionType!r}.")


def _decodePart(encoded: dict[str, object]) -> SimpleLocation:
    strand = None if encoded["strand"] is None else _integer(encoded["strand"])
    return SimpleLocation(
        _decodePosition(encoded["start"]), _decodePosition(encoded["end"]),
        strand=strand, ref=_optionalText(encoded["ref"]), ref_db=_optionalText(encoded["ref_db"]),
    )


def _decodeLocation(encoded: dict[str, object] | None) -> SimpleLocation | CompoundLocation | None:
    if encoded is None:
        return None
    if encoded["type"] == "SimpleLocation" and encoded["operator"] is None:
        match encoded["parts"]:
            case [part]:
                return _decodePart(part)
    if encoded["type"] == "CompoundLocation":
        parts = _decodeOrdered([encoded["part_container"], encoded["parts"]], _decodePart)
        compound = CompoundLocation(parts, operator=_text(encoded["operator"]))
        compound.parts = parts
        return compound
    raise ValueError("Invalid feature location in the stored revision.")


def _decodeFeature(encoded: dict[str, object]) -> SeqFeature:
    qualifiers = _decodeMetadata(encoded["qualifiers"])
    if type(qualifiers) is not dict:
        raise ValueError("Stored feature qualifiers must be a dictionary.")
    return SeqFeature(
        _decodeLocation(encoded["location"]), type=_text(encoded["type"]),
        id=_text(encoded["source_id"]), qualifiers=qualifiers,
    )


def _decodeAnnotation(key: str, value: object) -> object:
    if key == "references":
        return _decodeOrdered(value, _decodeReference)
    return _decodeMetadata(value)


def _decodeRecord(encoded: dict[str, object]) -> SeqRecord:
    record = SeqRecord(
        Seq(_text(encoded["sequence"])), id=_text(encoded["source_id"]),
        name=_text(encoded["name"]), description=_text(encoded["description"]),
        annotations=_decodeEntries(encoded["annotations"], _decodeAnnotation),
    )
    record.dbxrefs = _decodeOrdered(encoded["dbxrefs"], _text)
    record.features = _decodeOrdered(encoded["features"], _decodeFeature)
    return record


def _uniqueObject(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON field in the stored revision.")
    return result


def _decodeGeometry(encoded: dict[str, object]) -> DuplexGeometry:
    return DuplexGeometry(
        forwardSpan=FragmentSpan(**encoded["forwardSpan"]),
        reverseSpan=FragmentSpan(**encoded["reverseSpan"]),
        leftEnd=None if encoded["leftEnd"] is None else FragmentEnd(**encoded["leftEnd"]),
        rightEnd=None if encoded["rightEnd"] is None else FragmentEnd(**encoded["rightEnd"]),
    )


def decodeRevision(encoded: str) -> SequenceRevision:
    """Decode a stored snapshot and validate its content and IDs.

    Raises:
        ValueError: The schema, typed payload, content digest or identities are invalid.
    """
    try:
        document = json.loads(_text(encoded), object_pairs_hook=_uniqueObject)
        if type(document) is not dict or document.get("schema") not in (_SCHEMA, _PRODUCT_SCHEMA):
            raise ValueError("Unsupported stored revision schema.")
        geometry = None
        reference = None
        if document["schema"] == _PRODUCT_SCHEMA:
            if document["duplex_geometry"] is not None:
                geometry = _decodeGeometry(document["duplex_geometry"])
            if document["output_reference"] is not None:
                reference = OutputReference(**document["output_reference"])
        revision = SequenceRevision.fromStoredRecord(
            _decodeRecord(document), moleculeType=document["molecule_type"],
            recordId=document["record_id"], revisionId=document["revision_id"],
            featureIds=tuple(document["feature_ids"]),
            sourceArtifactId=_artifactId(document["source_artifact_id"]),
            sourceOrdinal=document["source_ordinal"],
            sourcePath=_optionalText(document["source_path"]),
            sourceFormat=_optionalText(document["source_format"]),
            duplexGeometry=geometry, outputReference=reference,
        )
        if revision.contentDigest != document["content_digest"]:
            raise ValueError("Stored revision content digest does not match its payload.")
        # Verify that no fields were ignored, coerced or lost during hydration.
        if encodeCanonicalJson(document) != encodeRevision(revision):
            raise ValueError("Stored revision fields do not match the native representation.")
    except (KeyError, IndexError, TypeError, ValueError, RecursionError) as error:
        raise ValueError(f"Invalid stored revision: {error}") from error
    return revision
