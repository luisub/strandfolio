"""Read and atomically write portable digestion results with their source ancestry."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from strandfolio.domain.provenance import OutputReference, SourceArtifact
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.formats import (
    MAX_FILE_BYTES, SequenceFormat, _capture, _checkCaptureSize, _validateByteLimit,
    checkExportDestination, exportDestinationVersion, publishStagedExport,
)
from strandfolio.operations.gel import GEL_MODEL_VERSION, GelMarker, GelSettings, GelViewSettings
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.storage.catalogues import LadderComponent, LadderProfile, LadderRelease
from strandfolio.storage.project_edits import (
    AnnotationStep, annotationBase, validateAnnotationStep, validateRecordOwnership,
)
from strandfolio.storage.restriction_codec import decodeRestrictionRun, encodeRestrictionRun
from strandfolio.storage.revision_codec import (
    decodeRevision, encodeCanonicalJson, encodeRevision, matchesRecordedProduct,
    renamedRevisionPayload,
)


_FILE_KIND = "vibegene.digestion"
_SCHEMA_VERSION = 1
_ANNOTATION_SCHEMA_VERSION = 2
_PRODUCER_VERSION = "1"
_ORIGINAL_BYTES_UNAVAILABLE = "original-bytes-unavailable"


class DigestionError(ValueError):
    """An invalid portable digestion or a file operation that could not complete."""


@dataclass(frozen=True, slots=True)
class DigestionSourceArtifact:
    """Metadata for an original file whose bytes are omitted from the digestion file."""

    artifact: SourceArtifact
    formatName: SequenceFormat


@dataclass(frozen=True, slots=True)
class SavedDigestion:
    """One recorded run and its view, with the available source ancestry."""

    result: RestrictionDigest
    view: GelViewSettings
    ancestorRuns: tuple[RestrictionDigest, ...] = ()
    sourceArtifacts: tuple[DigestionSourceArtifact, ...] = ()
    ancestryGaps: tuple[OutputReference, ...] = ()
    intakeRevisions: tuple[SequenceRevision, ...] = ()
    annotationSteps: tuple[AnnotationStep, ...] = ()


def _uniqueObject(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise DigestionError("Duplicate JSON field in the saved digestion.")
    return value


def _array(value: object) -> list:
    if type(value) is not list:
        raise DigestionError("Expected an ordered JSON array in the saved digestion.")
    return value


def _encodeLadderRelease(release: LadderRelease) -> dict[str, object]:
    return {
        "name": release.name, "snapshot_sha256": release.snapshotSha256,
        "schema_version": release.schemaVersion, "biopython_version": release.biopythonVersion,
        "reviewed": release.reviewed,
    }


def _decodeLadderRelease(value: dict[str, object]) -> LadderRelease:
    return LadderRelease(
        name=value["name"], snapshotSha256=value["snapshot_sha256"],
        schemaVersion=value["schema_version"], biopythonVersion=value["biopython_version"],
        reviewed=value["reviewed"],
    )


def _viewDocument(view: GelViewSettings) -> dict[str, object]:
    if type(view) is not GelViewSettings:
        raise TypeError("Expected recorded gel view settings.")
    marker = view.marker
    profile = marker.profile
    return {
        "model": GEL_MODEL_VERSION, "unit": "bp",
        "minimum_bp": view.settings.minimumBp, "maximum_bp": view.settings.maximumBp,
        "selected_output_key": view.selectedOutputKey,
        "marker": {
            "ladder_id": marker.ladderId,
            "release": None if marker.release is None else _encodeLadderRelease(marker.release),
            "profile": None if profile is None else {
                "name": profile.name, "unit": profile.unit, "attributions": profile.attributions,
                "components": [
                    [value.preparation, value.ordinal, value.size, value.nominalSize, value.sourceId]
                    for value in profile.components
                ],
            },
        },
    }


def encodeDigestionView(view: GelViewSettings) -> str:
    """Encode the presentation settings separately from immutable run identity."""
    return encodeCanonicalJson(_viewDocument(view))


def _decodeViewDocument(value: dict[str, object]) -> GelViewSettings:
    if value["model"] != GEL_MODEL_VERSION or value["unit"] != "bp":
        raise DigestionError("Unsupported saved gel model or unit.")
    marker = value["marker"]
    release = _decodeLadderRelease(marker["release"]) if marker["release"] is not None else None
    definition = marker["profile"]
    profile = None
    if definition is not None:
        profile = LadderProfile(
            ladderId=marker["ladder_id"], name=definition["name"], unit=definition["unit"],
            release=release, attributions=tuple(_array(definition["attributions"])),
            components=tuple(LadderComponent(*_array(row)) for row in _array(definition["components"])),
        )
    result = GelViewSettings(
        settings=GelSettings(minimumBp=value["minimum_bp"], maximumBp=value["maximum_bp"]),
        marker=GelMarker(ladderId=marker["ladder_id"], profile=profile, release=release),
        selectedOutputKey=value["selected_output_key"],
    )
    if encodeCanonicalJson(value) != encodeDigestionView(result):
        raise DigestionError("Saved gel fields differ from their native representation.")
    return result


def decodeDigestionView(encoded: str) -> GelViewSettings:
    """Validate and restore a recorded gel view without resolving a live catalogue."""
    try:
        return _decodeViewDocument(json.loads(encoded, object_pairs_hook=_uniqueObject))
    except DigestionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise DigestionError(f"Invalid saved gel view: {error}") from error


def validateDigestionView(result: RestrictionDigest, view: GelViewSettings) -> None:
    """Check that the saved selection identifies a contributor of this exact run."""
    if type(view) is not GelViewSettings:
        raise DigestionError("Expected recorded gel view settings.")
    if view.selectedOutputKey is not None and not any(
        fragment.outputKey == view.selectedOutputKey for fragment in result.fragments
    ):
        raise DigestionError("The saved gel selection refers to an unknown restriction output.")


def _artifactDocument(source: DigestionSourceArtifact) -> dict[str, object]:
    from strandfolio.storage.projects import _FORMATS, _validateArtifact

    if type(source) is not DigestionSourceArtifact or source.formatName not in _FORMATS:
        raise DigestionError("Invalid source evidence for the saved digestion.")
    artifact = source.artifact
    _validateArtifact(artifact)
    return {
        "artifact_id": artifact.artifactId, "filename": artifact.filename, "sha256": artifact.sha256,
        "byte_count": artifact.byteCount, "format": source.formatName,
        "availability": _ORIGINAL_BYTES_UNAVAILABLE,
    }


def _decodeArtifact(value: dict[str, object]) -> DigestionSourceArtifact:
    source = DigestionSourceArtifact(
        SourceArtifact(value["artifact_id"], value["filename"], value["sha256"], value["byte_count"]),
        value["format"],
    )
    if encodeCanonicalJson(value) != encodeCanonicalJson(_artifactDocument(source)):
        raise DigestionError("Invalid source descriptor in the saved digestion.")
    return source


def _collectRevisions(
    digestion: SavedDigestion, steps: dict[str, AnnotationStep],
) -> dict[str, dict[str, object]]:
    revisions = {}
    featureOwners = {}
    runs = (*digestion.ancestorRuns, digestion.result)
    snapshots = (
        revision
        for run in runs
        for revision in (run.template, *(fragment.revision for fragment in run.fragments))
    )
    editSnapshots = (item for step in steps.values() for item in (step.parent, step.revision))
    allSnapshots = (*snapshots, *digestion.intakeRevisions, *editSnapshots)
    for revision in allSnapshots:
        encoded = json.loads(encodeRevision(revision))
        existing = revisions.setdefault(revision.revisionId, encoded)
        if encodeCanonicalJson(existing) != encodeCanonicalJson(encoded):
            raise DigestionError(f"Conflicting revision ID {revision.revisionId!r}.")
    validateRecordOwnership(allSnapshots, steps)
    featurePayloads = {
        revisionId: dict(zip(encoded["feature_ids"], encoded["features"][1], strict=True))
        for revisionId, encoded in revisions.items()
    }
    for revisionId, features in featurePayloads.items():
        for featureId in features:
            originId = revisionId
            while (step := steps.get(originId)) is not None:
                parentId = step.parent.revisionId
                if featureId not in featurePayloads[parentId]:
                    break
                originId = parentId
            owner = (
                revisions[originId]["record_id"],
                encodeCanonicalJson(featurePayloads[originId][featureId]),
            )
            if featureOwners.setdefault(featureId, owner) != owner:
                raise DigestionError(f"Conflicting feature ID {featureId!r}.")
    return revisions


def _verifiedAnnotationSteps(digestion: SavedDigestion) -> dict[str, AnnotationStep]:
    steps = {}
    for step in digestion.annotationSteps:
        validateAnnotationStep(step.parent, step.revision, isNameOnly=step.isNameOnly)
        if type(step.isNameOnly) is not bool or step.revision.revisionId in steps:
            raise DigestionError("Invalid or repeated annotation step in the saved digestion.")
        steps[step.revision.revisionId] = step
    used = set()
    for run in (*digestion.ancestorRuns, digestion.result):
        path = set()
        revisionId = run.template.revisionId
        while revisionId in steps and revisionId not in used:
            if revisionId in path:
                raise DigestionError("Circular annotation ancestry in the saved digestion.")
            path.add(revisionId)
            revisionId = steps[revisionId].parent.revisionId
        used.update(path)
    if used != steps.keys():
        raise DigestionError("The saved digestion contains unreferenced annotation steps.")
    return steps


def _validateAncestry(
    digestion: SavedDigestion, steps: dict[str, AnnotationStep],
) -> None:
    runs = (*digestion.ancestorRuns, digestion.result)
    runIds = {run.operationId for run in runs}
    if len(runIds) != len(runs):
        raise DigestionError("Duplicate operation ID in the saved digestion.")

    newRecordIds = {annotationBase(runs[0].template, steps).recordId}
    for ancestor, child in zip(digestion.ancestorRuns, runs[1:]):
        template = annotationBase(child.template, steps)
        reference = template.outputReference
        if reference is None or reference.operationId != ancestor.operationId:
            raise DigestionError("A saved ancestor does not generate the next input revision.")
        parent = next(
            (fragment.revision for fragment in ancestor.fragments
             if fragment.outputKey == reference.outputKey), None,
        )
        if parent is None or not matchesRecordedProduct(template, parent):
            raise DigestionError("A saved input revision does not match its generating output.")
        if template.recordId != parent.recordId:
            if template.recordId in newRecordIds:
                raise DigestionError(f"Conflicting record ID {template.recordId!r}.")
            newRecordIds.add(template.recordId)

    for run in runs:
        for fragment in run.fragments:
            if fragment.revision.recordId in newRecordIds:
                raise DigestionError(f"Conflicting record ID {fragment.revision.recordId!r}.")
            newRecordIds.add(fragment.revision.recordId)
    missing = annotationBase(runs[0].template, steps).outputReference
    if missing is not None and missing.operationId in runIds:
        raise DigestionError("Circular restriction ancestry in the saved digestion.")
    if digestion.ancestryGaps != (() if missing is None else (missing,)):
        raise DigestionError("Saved ancestry gaps do not match the unresolved source references.")


def _validateSourceEvidence(digestion: SavedDigestion, steps: dict[str, AnnotationStep]) -> None:
    sources = {source.artifact.artifactId: source for source in digestion.sourceArtifacts}
    if len(sources) != len(digestion.sourceArtifacts):
        raise DigestionError("Duplicate source artifact ID in the saved digestion.")
    templates = [annotationBase(run.template, steps) for run in (*digestion.ancestorRuns, digestion.result)]
    origin = {}
    for template in templates:
        artifactId = template.sourceArtifactId
        if artifactId is None:
            if any(value is not None for value in (
                template.sourceOrdinal, template.sourceFormat, template.sourcePath,
            )):
                raise DigestionError("Saved input has source metadata without an artifact.")
            continue
        source = sources.get(artifactId)
        if (source is None or template.sourceFormat != source.formatName
                or template.sourceOrdinal is None or template.outputReference is not None):
            raise DigestionError("Saved input does not match its declared source artifact.")
        origin[artifactId] = template
    if sources.keys() != origin.keys():
        raise DigestionError("The saved digestion contains unreferenced source descriptors.")
    intakeArtifacts = set()
    for intake in digestion.intakeRevisions:
        template = origin.get(intake.sourceArtifactId)
        if (template is None or intake.sourceArtifactId in intakeArtifacts
                or renamedRevisionPayload(intake) != renamedRevisionPayload(template)):
            raise DigestionError("Saved intake evidence does not match the selected source.")
        intakeArtifacts.add(intake.sourceArtifactId)


def _digestionPayload(digestion: SavedDigestion) -> dict[str, object]:
    if type(digestion) is not SavedDigestion:
        raise TypeError("Expected a saved digestion.")
    payload = {
        "app_version": _PRODUCER_VERSION, "sequence_unit": "bp",
        "operation_id": digestion.result.operationId,
        "runs": [json.loads(encodeRestrictionRun(run))
                 for run in (*digestion.ancestorRuns, digestion.result)],
        "revisions": _collectRevisions(digestion, _verifiedAnnotationSteps(digestion)),
        "intake_revisions": [revision.revisionId for revision in digestion.intakeRevisions],
        "source_artifacts": [_artifactDocument(source) for source in digestion.sourceArtifacts],
        "ancestry_gaps": [[ref.operationId, ref.outputKey] for ref in digestion.ancestryGaps],
        "gel_view": _viewDocument(digestion.view),
    }
    if digestion.annotationSteps:
        payload["annotation_steps"] = [
            [step.parent.revisionId, step.revision.revisionId, step.isNameOnly]
            for step in digestion.annotationSteps
        ]
    return payload


def _decodePayload(payload: dict[str, object], *, hasAnnotations: bool = False) -> SavedDigestion:
    expected = {
        "app_version", "sequence_unit", "operation_id", "runs", "revisions", "intake_revisions",
        "source_artifacts", "ancestry_gaps", "gel_view",
    }
    if hasAnnotations:
        expected.add("annotation_steps")
    if payload["sequence_unit"] != "bp" or set(payload) != expected:
        raise DigestionError("Invalid saved digestion payload fields or sequence unit.")
    version = payload["app_version"]
    if type(version) is not str or not version.strip() or "\x00" in version:
        raise DigestionError("Invalid digestion producer version.")
    revisions = {
        identifier: decodeRevision(encodeCanonicalJson(value))
        for identifier, value in payload["revisions"].items()
    }
    if any(identifier != revision.revisionId for identifier, revision in revisions.items()):
        raise DigestionError("A saved revision identity differs from its lookup key.")
    runs = [
        decodeRestrictionRun(
            encodeCanonicalJson(value), revisions[value["template"][1]],
            tuple((output["output_key"], revisions[output["revision"][1]])
                  for output in _array(value["outputs"])),
        )
        for value in _array(payload["runs"])
    ]
    if not runs or runs[-1].operationId != payload["operation_id"]:
        raise DigestionError("The saved digestion does not identify its primary run.")
    digestion = SavedDigestion(
        result=runs[-1], view=_decodeViewDocument(payload["gel_view"]), ancestorRuns=tuple(runs[:-1]),
        sourceArtifacts=tuple(_decodeArtifact(item) for item in _array(payload["source_artifacts"])),
        ancestryGaps=tuple(OutputReference(*_array(item)) for item in _array(payload["ancestry_gaps"])),
        intakeRevisions=tuple(revisions[item] for item in _array(payload["intake_revisions"])),
        annotationSteps=tuple(
            AnnotationStep(revisions[parent], revisions[child], isNameOnly)
            for parent, child, isNameOnly in _array(payload.get("annotation_steps", []))
        ),
    )
    steps = _verifiedAnnotationSteps(digestion)
    if _collectRevisions(digestion, steps).keys() != revisions.keys():
        raise DigestionError("The saved digestion contains unreferenced revisions.")
    validateDigestionView(digestion.result, digestion.view)
    _validateAncestry(digestion, steps)
    _validateSourceEvidence(digestion, steps)
    return digestion


def _validatePayload(payload: dict[str, object]) -> SavedDigestion:
    try:
        # The file uses JSON arrays for all nested native values, including immutable tuples.
        return _decodePayload(
            json.loads(encodeCanonicalJson(payload)), hasAnnotations="annotation_steps" in payload,
        )
    except DigestionError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError,
            RecursionError) as error:
        raise DigestionError(f"Invalid saved digestion payload: {error}") from error


def validateDigestion(digestion: SavedDigestion) -> SavedDigestion:
    """Check schema, identities, ancestry, physical products and gel references offline."""
    try:
        return _validatePayload(_digestionPayload(digestion))
    except DigestionError:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise DigestionError(f"Invalid saved digestion: {error}") from error


def readDigestion(source: str | Path, *, maxBytes: int = MAX_FILE_BYTES) -> SavedDigestion:
    """Read a regular UTF-8 digestion file up to maxBytes, including its ancestry."""
    try:
        value = json.loads(
            _capture(Path(source), maxBytes).decode("utf-8"), object_pairs_hook=_uniqueObject,
        )
        if (type(value) is not dict or value.get("file_kind") != _FILE_KIND
                or set(value) != {"file_kind", "schema_version", "payload", "payload_sha256"}):
            raise DigestionError("Invalid strandfolio digestion file signature.")
        schemaVersion = value["schema_version"]
        if type(schemaVersion) is not int or schemaVersion not in (
            _SCHEMA_VERSION, _ANNOTATION_SCHEMA_VERSION,
        ):
            raise DigestionError(f"Unsupported digestion schema version {value['schema_version']!r}.")
        payload = value["payload"]
        if sha256(encodeCanonicalJson(payload).encode("ascii")).hexdigest() != value["payload_sha256"]:
            raise DigestionError("Saved digestion payload checksum differs.")
        return _decodePayload(payload, hasAnnotations=schemaVersion == _ANNOTATION_SCHEMA_VERSION)
    except DigestionError:
        raise
    except (AttributeError, IndexError, KeyError, OSError, TypeError, ValueError, OverflowError,
            RecursionError) as error:
        raise DigestionError(f"Unable to read saved digestion: {error}") from error


def writeDigestion(
    digestion: SavedDigestion, destination: str | Path, *,
    confirm: Callable[[SavedDigestion], bool] | None = None,
    protectedPaths: Iterable[str | Path] = (), maxBytes: int = MAX_FILE_BYTES,
) -> Path | None:
    """Stage, validate and atomically publish a portable file on the destination filesystem.

    The complete encoded file must fit maxBytes. A confirmation callback sees exactly
    the staged digestion. Returning False cancels the write and yields None. A change
    to the destination during review aborts publication.
    """
    from strandfolio.storage.projects import _syncFile

    requested = Path(destination)
    target = requested.parent.resolve() / requested.name
    if target.suffix.lower() != ".vibedigest":
        raise DigestionError("A saved digestion requires the .vibedigest filename suffix.")
    try:
        _validateByteLimit(maxBytes)
        approvedVersion = exportDestinationVersion(target)
        payload = _digestionPayload(digestion)
        schemaVersion = _ANNOTATION_SCHEMA_VERSION if digestion.annotationSteps else _SCHEMA_VERSION
        document = {
            "file_kind": _FILE_KIND, "schema_version": schemaVersion, "payload": payload,
            "payload_sha256": sha256(encodeCanonicalJson(payload).encode("ascii")).hexdigest(),
        }
        originalPaths = {
            Path(revision["source_path"]) for revision in payload["revisions"].values()
            if revision["source_path"] is not None
        }
        protected = (*map(Path, protectedPaths), *originalPaths)
        checkExportDestination(target, protected)
        with TemporaryDirectory(dir=target.parent, prefix=".strandfolio-digestion-") as directory:
            stagedPath = Path(directory) / target.name
            with stagedPath.open("wb") as handle:
                # The canonical encoder emits only ASCII. Count the final newline as well.
                encoded = encodeCanonicalJson(document)
                _checkCaptureSize(len(encoded) + 1, maxBytes)
                handle.write(encoded.encode("ascii"))
                handle.write(b"\n")
                _syncFile(handle)
            del encoded
            staged = readDigestion(stagedPath, maxBytes=maxBytes)
            if confirm is not None and confirm(staged) is not True:
                return None
            checkExportDestination(target, protected)
            publishStagedExport(stagedPath, target, approvedVersion)
        return target
    except DigestionError:
        raise
    except (AttributeError, OSError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise DigestionError(f"Unable to save digestion: {error}") from error
