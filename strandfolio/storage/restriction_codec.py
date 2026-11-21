"""Versioned restriction results referencing separately encoded revision snapshots."""

from collections.abc import Callable
from datetime import datetime, timedelta
import json
from string import hexdigits
from typing import TypeVar

from strandfolio.domain.fragments import DNAFragment, FragmentEnd, FragmentFeatureMapping, FragmentSpan
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.restriction import (
    RecognitionSite, RestrictionCut, RestrictionDigest, RestrictionScan, RestrictionStatus,
    validateRestrictionEvidence,
)
from strandfolio.storage.catalogues import (
    CatalogueIssue, CatalogueRelease, CleavagePair, EnzymeRecord, EnzymeSupplier,
)
from strandfolio.storage.revision_codec import encodeCanonicalJson


_SCHEMA = "vibegene.project.restriction.v1"
_Item = TypeVar("_Item")


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("Expected text in the restriction result.")
    return value


def _identity(value: object) -> str:
    identifier = _text(value)
    if not identifier.strip() or "\x00" in identifier:
        raise ValueError("Expected a nonblank identity in the restriction result.")
    return identifier


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("Expected an integer in the restriction result.")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("Expected a boolean in the restriction result.")
    return value


def _array(values: object, decode: Callable[[object], _Item]) -> tuple[_Item, ...]:
    if type(values) is not list:
        raise ValueError("Expected an ordered array in the restriction result.")
    return tuple(decode(value) for value in values)


def _span(encoded: object) -> tuple[int, int]:
    match encoded:
        case [start, end]:
            return _integer(start), _integer(end)
    raise ValueError("Expected two span endpoints in the restriction result.")


def _status(value: object) -> RestrictionStatus:
    if value == "completed":
        return "completed"
    if value == "unsupported":
        return "unsupported"
    raise ValueError("Invalid restriction completion status.")


def _encodeRelease(release: CatalogueRelease) -> dict[str, object]:
    return {
        "source": release.source, "biopython_version": release.biopythonVersion,
        "source_release_description": release.sourceReleaseDescription,
        "source_dictionary_sha256": release.sourceDictionarySha256,
        "snapshot_sha256": release.snapshotSha256, "schema_version": release.schemaVersion,
        "created_utc": release.createdUtc,
    }


def _decodeRelease(value: dict[str, object]) -> CatalogueRelease:
    sourceDigest = _text(value["source_dictionary_sha256"])
    snapshotDigest = _text(value["snapshot_sha256"])
    for digest in (sourceDigest, snapshotDigest):
        if len(digest) != 64 or any(letter not in hexdigits for letter in digest):
            raise ValueError("Invalid restriction catalogue checksum.")
    return CatalogueRelease(
        source=_identity(value["source"]), biopythonVersion=_identity(value["biopython_version"]),
        sourceReleaseDescription=_text(value["source_release_description"]),
        sourceDictionarySha256=sourceDigest, snapshotSha256=snapshotDigest,
        schemaVersion=_integer(value["schema_version"]), createdUtc=_identity(value["created_utc"]),
    )


def _encodeEnzyme(enzyme: EnzymeRecord) -> dict[str, object]:
    return {
        "name": enzyme.name, "rebase_id": enzyme.rebaseId, "source_url": enzyme.sourceUrl,
        "recognition_site": enzyme.recognitionSite, "recognition_length": enzyme.recognitionLength,
        "cut_notation": enzyme.cutNotation, "cleavage_known": enzyme.cleavageKnown,
        "overhang_type": enzyme.overhangType, "overhang_length": enzyme.overhangLength,
        "overhang_pattern": enzyme.overhangPattern,
        "commercial_in_snapshot": enzyme.commercialInSnapshot,
        "cleavage_pairs": [
            [pair.pairNumber, pair.topOffset, pair.bottomOffset, pair.biopythonBottomFromEnd]
            for pair in enzyme.cleavagePairs
        ],
        "suppliers": [[supplier.code, supplier.name] for supplier in enzyme.suppliers],
        "issues": [[issue.code, issue.detail] for issue in enzyme.issues],
    }


def _decodeEnzyme(value: dict[str, object]) -> EnzymeRecord:
    return EnzymeRecord(
        name=_identity(value["name"]),
        rebaseId=None if value["rebase_id"] is None else _integer(value["rebase_id"]),
        sourceUrl=None if value["source_url"] is None else _text(value["source_url"]),
        recognitionSite=_text(value["recognition_site"]),
        recognitionLength=_integer(value["recognition_length"]),
        cutNotation=None if value["cut_notation"] is None else _text(value["cut_notation"]),
        cleavageKnown=_boolean(value["cleavage_known"]), overhangType=_text(value["overhang_type"]),
        overhangLength=None if value["overhang_length"] is None else _integer(value["overhang_length"]),
        overhangPattern=None if value["overhang_pattern"] is None else _text(value["overhang_pattern"]),
        commercialInSnapshot=_boolean(value["commercial_in_snapshot"]),
        cleavagePairs=_array(value["cleavage_pairs"], lambda row: CleavagePair(*_array(row, _integer))),
        suppliers=_array(value["suppliers"], lambda row: EnzymeSupplier(*_array(row, _text))),
        issues=_array(value["issues"], lambda row: CatalogueIssue(*_array(row, _text))),
    )


def _encodeSite(site: RecognitionSite) -> dict[str, object]:
    return {
        "enzyme": site.enzymeName, "motif": site.motif, "start": site.start, "end": site.end,
        "orientation": site.orientation, "certainty": site.certainty, "parts": site.sourceParts,
    }


def _decodeSite(value: dict[str, object]) -> RecognitionSite:
    orientation = _integer(value["orientation"])
    certainty = value["certainty"]
    if orientation not in (-1, 1) or certainty not in ("definite", "possible"):
        raise ValueError("Invalid restriction-site orientation or certainty.")
    return RecognitionSite(
        enzymeName=_identity(value["enzyme"]), motif=_identity(value["motif"]),
        start=_integer(value["start"]), end=_integer(value["end"]), orientation=orientation,
        certainty=certainty, sourceParts=_array(value["parts"], _span),
    )


def _decodeCut(value: dict[str, object], sites: tuple[RecognitionSite, ...]) -> RestrictionCut:
    indices = _array(value["site_indices"], _integer)
    if any(not 0 <= index < len(sites) for index in indices):
        raise ValueError("A restriction cut refers to an unknown site.")
    return RestrictionCut(
        topBoundary=_integer(value["top_boundary"]), bottomBoundary=_integer(value["bottom_boundary"]),
        bottomOffset=_integer(value["bottom_offset"]), sites=tuple(sites[index] for index in indices),
    )


def _revisionReference(revision: SequenceRevision) -> list[str]:
    return [revision.recordId, revision.revisionId, revision.contentDigest]


def _encodeFragment(fragment: DNAFragment) -> dict[str, object]:
    return {
        "operation_id": fragment.operationId, "output_key": fragment.outputKey,
        "source_revision_id": fragment.sourceRevisionId, "revision": _revisionReference(fragment.revision),
        "source_spans": [[span.start, span.end] for span in fragment.sourceSpans],
        "warnings": fragment.warnings,
        "feature_mappings": [
            [mapping.featureId, mapping.sourceFeatureId, mapping.partial]
            for mapping in fragment.featureMappings
        ],
    }


def _decodeFeatureMapping(value: object) -> FragmentFeatureMapping:
    match value:
        case [candidateId, sourceId, partial]:
            return FragmentFeatureMapping(_identity(candidateId), _identity(sourceId), _boolean(partial))
    raise ValueError("Invalid restriction-product feature mapping.")


def _decodeFragment(value: dict[str, object], key: str, revision: SequenceRevision) -> DNAFragment:
    if value["output_key"] != key or value["revision"] != _revisionReference(revision):
        raise ValueError("A restriction output differs from its linked revision.")
    return DNAFragment(
        operationId=_identity(value["operation_id"]), outputKey=_identity(key),
        sourceRevisionId=_identity(value["source_revision_id"]), revision=revision,
        sourceSpans=_array(value["source_spans"], lambda row: FragmentSpan(*_span(row))),
        warnings=_array(value["warnings"], _text),
        featureMappings=_array(value["feature_mappings"], _decodeFeatureMapping),
    )


def encodeRestrictionRun(result: RestrictionDigest) -> str:
    """Encode the exact recorded values and identifiers without embedding revision bytes."""
    if not isinstance(result, RestrictionDigest):
        raise TypeError("Expected a restriction digest result.")
    siteIndices = {site: index for index, site in enumerate(result.sites)}
    cuts = []
    for cut in result.cuts:
        try:
            indices = [siteIndices[site] for site in cut.sites]
        except KeyError as error:
            raise ValueError("A restriction cut refers to an unknown site.") from error
        cuts.append({
            "top_boundary": cut.topBoundary, "bottom_boundary": cut.bottomBoundary,
            "bottom_offset": cut.bottomOffset, "site_indices": indices,
        })
    document = {
        "schema": _SCHEMA, "operation_id": result.operationId, "created_at": result.createdAt,
        "template": _revisionReference(result.template),
        "engine_version": result.engineVersion, "status": result.status,
        "digest_model": result.digestModel, "duplex_policy": result.duplexPolicy,
        "coordinate_convention": result.coordinateConvention,
        "scan_status": result.scan.status, "matching_policy": result.scan.matchingPolicy,
        "catalogue": _encodeRelease(result.catalogueRelease), "metadata": result.catalogueMetadata,
        "enzymes": [_encodeEnzyme(enzyme) for enzyme in result.enzymes],
        "sites": [_encodeSite(site) for site in result.sites], "cuts": cuts,
        "outputs": [_encodeFragment(fragment) for fragment in result.fragments],
        "scan_warnings": result.scan.warnings, "warnings": result.warnings,
    }
    return encodeCanonicalJson(document)


def decodeRestrictionRun(
    encoded: str, template: SequenceRevision, outputs: tuple[tuple[str, SequenceRevision], ...],
) -> RestrictionDigest:
    """Restore recorded results, checking their linked revisions without rerunning the digest."""
    try:
        value = json.loads(_text(encoded))
        if type(value) is not dict or value.get("schema") != _SCHEMA:
            raise ValueError("Unsupported restriction result schema.")
        if value["template"] != _revisionReference(template):
            raise ValueError("The restriction input differs from its linked revision.")
        metadata = _array(value["metadata"], lambda row: _array(row, _text))
        if any(len(entry) != 2 for entry in metadata) or len(dict(metadata)) != len(metadata):
            raise ValueError("Invalid or repeated restriction catalogue metadata key.")
        scan = RestrictionScan(
            template=template, catalogueRelease=_decodeRelease(value["catalogue"]),
            catalogueMetadata=metadata, enzymes=_array(value["enzymes"], _decodeEnzyme),
            sites=_array(value["sites"], _decodeSite), warnings=_array(value["scan_warnings"], _text),
            matchingPolicy=_identity(value["matching_policy"]), status=_status(value["scan_status"]),
        )
        fragments = tuple(
            _decodeFragment(item, key, revision)
            for item, (key, revision) in zip(value["outputs"], outputs, strict=True)
        )
        result = RestrictionDigest(
            operationId=_identity(value["operation_id"]), createdAt=_identity(value["created_at"]),
            scan=scan, status=_status(value["status"]),
            cuts=_array(value["cuts"], lambda row: _decodeCut(row, scan.sites)), fragments=fragments,
            warnings=_array(value["warnings"], _text), engineVersion=_identity(value["engine_version"]),
            digestModel=_identity(value["digest_model"]), duplexPolicy=_identity(value["duplex_policy"]),
            coordinateConvention=_identity(value["coordinate_convention"]),
        )
        _validateResult(result)
        if encodeRestrictionRun(result) != encoded:
            raise ValueError("Restriction result fields differ from the native representation.")
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as error:
        raise ValueError(f"Invalid restriction result: {error}") from error


def _validateResult(result: RestrictionDigest) -> None:
    if datetime.fromisoformat(result.createdAt).utcoffset() != timedelta(0):
        raise ValueError("A restriction run requires a UTC creation time.")
    metadata = dict(result.catalogueMetadata)
    release = _encodeRelease(result.catalogueRelease)
    for field in ("source", "biopython_version", "source_release_description",
                  "source_dictionary_sha256", "schema_version", "created_utc"):
        if metadata.get(field) != str(release[field]):
            raise ValueError(f"Restriction catalogue release conflicts with metadata field {field!r}.")

    validateRestrictionEvidence(result)
    if result.status == "unsupported":
        if result.fragments:
            raise ValueError("An unsupported restriction run cannot have completed outputs.")
        if not any(warning.strip() for warning in result.warnings):
            raise ValueError("An unsupported restriction run requires a diagnostic.")
        return
    _validateFragments(result)
    _validatePartition(result)


def _validateFragments(result: RestrictionDigest) -> None:
    inputSequence = result.template.sequence
    sourceFeatures = {item.featureId for item in result.template.features}
    keys = set()
    recordIds = {result.template.recordId}
    for fragment in result.fragments:
        if (fragment.operationId != result.operationId or fragment.outputKey in keys
                or fragment.sourceRevisionId != result.template.revisionId
                or fragment.revision.recordId in recordIds
                or fragment.revision.sourceArtifactId is not None):
            raise ValueError("Invalid restriction run, output key or revision reference on a product.")
        if (any(span.end > len(inputSequence) for span in fragment.sourceSpans)
                or "".join(inputSequence[span.start:span.end] for span in fragment.sourceSpans)
                != fragment.revision.sequence):
            raise ValueError("A restriction product's source spans differ from its reference sequence.")
        mappings = fragment.featureMappings
        if (tuple(mapping.featureId for mapping in mappings)
                != tuple(item.featureId for item in fragment.revision.features)
                or any(mapping.sourceFeatureId not in sourceFeatures for mapping in mappings)):
            raise ValueError("A restriction product refers to an unknown source or product feature.")
        keys.add(fragment.outputKey)
        recordIds.add(fragment.revision.recordId)


def _validatePartition(result: RestrictionDigest) -> None:
    template = result.template
    size = len(template)
    isCircular = template.topology == "circular"
    cuts = tuple((cut.topBoundary, cut.topBoundary + cut.bottomOffset) for cut in result.cuts)
    source = template.duplexGeometry
    if isCircular and cuts:
        firstForward, firstReverse = cuts[0]
        boundaries = (*cuts, (firstForward + size, firstReverse + size))
    else:
        start = (source.forwardSpan.start, source.reverseSpan.start) if source else (0, 0)
        end = (source.forwardSpan.end, source.reverseSpan.end) if source else (size, size)
        boundaries = (start, *cuts, end)
    if len(result.fragments) != len(boundaries) - 1:
        raise ValueError("Restriction output count differs from the paired cut partition.")

    intervals = zip(boundaries, boundaries[1:])
    for index, (fragment, (left, right)) in enumerate(zip(result.fragments, intervals, strict=True)):
        origin = min(left)
        expected = (left[0], right[0], left[1], right[1])
        actual = tuple(origin + boundary for span in (fragment.forwardSpan, fragment.reverseSpan)
                       for boundary in (span.start, span.end))
        topology = "linear" if cuts else template.topology
        if expected != actual or fragment.revision.topology != topology:
            raise ValueError("Restriction products do not follow paired cut boundaries in source order.")
        for span in fragment.sourceSpans:
            if span.start != (origin % size if isCircular else origin):
                raise ValueError("Restriction source spans are not contiguous in source order.")
            origin += len(span)
        if cuts:
            cutEnds = (
                end for end, isCut in (
                    (fragment.leftEnd, isCircular or index > 0),
                    (fragment.rightEnd, isCircular or index < len(result.fragments) - 1),
                ) if isCut
            )
            if any(end is None or end.kind == "unknown" or end.fivePrimeChemistry != "phosphate"
                   or end.threePrimeChemistry != "hydroxyl" for end in cutEnds):
                raise ValueError("A cleaved terminus lacks its recorded polarity or chemistry.")

    if not isCircular:
        leftEnd = source.leftEnd if source else FragmentEnd("unknown")
        rightEnd = source.rightEnd if source else FragmentEnd("unknown")
        if result.fragments[0].leftEnd != leftEnd or result.fragments[-1].rightEnd != rightEnd:
            raise ValueError("Restriction products do not retain the input termini.")
