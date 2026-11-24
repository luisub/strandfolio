"""Check project integrity and create a standalone backup."""

from __future__ import annotations

from contextlib import closing
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory

from strandfolio.domain.features import canonicalFeature
from strandfolio.storage.project_edits import validateAnnotationStep
from strandfolio.storage.project_parents import verifyDeclaredParents
from strandfolio.storage.project_runs import operationOutputOwners
from strandfolio.storage.projects import (
    ProjectError,
    ProjectRepository,
    _captureKey,
    _captureRequestKey,
    _insideProject,
    _json,
    _matchKey,
    _projectHeader,
    _syncDescriptor,
    _syncDirectory,
    _validateArtifact,
    _validateCapture,
)
from strandfolio.storage.revision_codec import decodeRevision, renamedRevisionPayload


def _artifactPath(root: Path, digest: str, byteCount: int) -> Path:
    if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise ProjectError(f"Invalid managed artifact digest: {digest!r}")
    path = root / "artifacts" / digest
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise ProjectError(f"Missing managed artifact or invalid file: {digest}")
    if path.stat().st_size != byteCount:
        raise ProjectError(f"Corrupt managed artifact (size): {digest}")
    return path


def _verifyRevisions(connection: sqlite3.Connection) -> None:
    outputOwners = operationOutputOwners(connection)
    hasAnnotations = _projectHeader(connection) >= 6
    steps = dict(connection.execute(
        "SELECT child_revision_id, parent_revision_id FROM annotation_steps",
    )) if hasAnnotations else {}
    verified = set()
    for revisionId in steps:
        seen = set()
        while revisionId in steps and revisionId not in verified:
            if revisionId in seen:
                raise ProjectError(f"Circular annotation ancestry at revision {revisionId!r}.")
            seen.add(revisionId)
            revisionId = steps[revisionId]
        verified.update(seen)
    annotationColumn = "rf.annotation_payload" if hasAnnotations else "NULL"
    recordRoots: dict[str, str | None] = {}
    for row in connection.execute("SELECT * FROM revisions"):
        revision = decodeRevision(row["payload"])
        identity = (revision.revisionId, revision.recordId, revision.sourceArtifactId,
                    revision.sourceOrdinal, revision.contentDigest)
        if identity != tuple(row[field] for field in (
            "revision_id", "record_id", "source_artifact_id", "source_ordinal", "content_digest"
        )):
            raise ProjectError(f"Inconsistent stored revision: {row['revision_id']}")
        step = ProjectRepository._loadAnnotationStep(
            connection, revision.revisionId,
        ) if revision.revisionId in steps else None
        parent = ProjectRepository._loadRevision(connection, step[0]) if step is not None else None
        if parent is not None:
            validateAnnotationStep(parent, revision, isNameOnly=bool(step[1]))
        elif revision.outputReference != outputOwners.get(revision.recordId):
            raise ProjectError(f"Broken operation output reference on revision: {revision.revisionId}")
        if hasAnnotations and (parent is None or parent.recordId != revision.recordId):
            # Unlinked root snapshots can coexist across a rename. An annotation
            # descendant has exactly one entry revision, with its recorded parent.
            owner = None if parent is None else revision.revisionId
            if recordRoots.setdefault(revision.recordId, owner) != owner:
                raise ProjectError(f"Conflicting record ID {revision.recordId!r}.")
        featureRows = connection.execute(
            f"SELECT rf.feature_ordinal, rf.feature_id, f.record_id, f.payload, {annotationColumn} "
            "FROM revision_features rf "
            "JOIN features f USING (feature_id) WHERE rf.revision_id = ? "
            "ORDER BY rf.feature_ordinal", (revision.revisionId,),
        )
        record = revision.toSeqRecord()
        inheritedIds = {item.featureId for item in parent.features} if parent is not None else set()
        expected = [(index, projection.featureId, _json(canonicalFeature(feature, len(record), native=True)))
                    for index, (projection, feature) in enumerate(
                        zip(revision.features, record.features, strict=True), start=1,
                    )]
        actual = []
        for index, featureId, ownerId, content, annotation in featureRows:
            if annotation is None and ownerId == revision.recordId:
                actual.append((index, featureId, content))
            elif annotation is not None and featureId in inheritedIds:
                actual.append((index, featureId, annotation))
            else:
                raise ProjectError(f"Inconsistent features on revision: {revision.revisionId}")
        if actual != expected:
            raise ProjectError(f"Inconsistent features on revision: {revision.revisionId}")
        nameRow = connection.execute(
            "SELECT name, name_key, record_id FROM named_records WHERE revision_id = ?",
            (revision.revisionId,),
        ).fetchone()
        if nameRow and tuple(nameRow) != (revision.name, revision.name.casefold(), revision.recordId):
            raise ProjectError(f"Inconsistent named revision: {revision.revisionId}")


def _verifyIntakes(connection: sqlite3.Connection, root: Path) -> dict[str, str]:
    repository = ProjectRepository(root)
    requestKeys = {}
    for row in connection.execute("SELECT * FROM intakes"):
        artifactId = row["artifact_id"]
        try:
            intake = repository._loadIntake(connection, artifactId)
            _validateCapture(intake)
            if (_json(intake.warnings), _matchKey(intake), _captureKey(intake)) != (
                row["warnings"], row["match_key"], row["capture_key"]
            ):
                raise ValueError("Stored capture identity or interpretation differs.")
            requestKeys[artifactId] = _captureRequestKey(intake)
        except (ValueError, TypeError, KeyError) as error:
            raise ProjectError(f"Invalid intake {artifactId}: {error}") from error
    return requestKeys


def _verifyRequests(connection: sqlite3.Connection, intakeKeys: dict[str, str]) -> None:
    for row in connection.execute("SELECT * FROM requests WHERE kind <> 'export'"):
        requestId = row["request_id"]
        ProjectRepository._validateRequestId(requestId)
        payload = json.loads(row["payload"])
        resultId = row["result_id"]
        if row["kind"] == "intake":
            if (not isinstance(payload, dict) or type(payload.get("independent")) is not bool
                    or resultId not in intakeKeys or payload.get("capture") != intakeKeys[resultId]):
                raise ProjectError(f"Inconsistent intake request: {requestId}")
            continue
        saved = connection.execute(
            "SELECT n.name, n.record_id, r.source_artifact_id, r.source_ordinal "
            "FROM named_records n JOIN revisions r USING (revision_id) WHERE n.revision_id = ?",
            (resultId,),
        ).fetchone()
        if saved is None or not isinstance(payload, dict) or saved["name"] != payload.get("name"):
            raise ProjectError(f"Inconsistent named save request: {requestId}")
        if payload["capture"] is None:
            snapshots = connection.execute(
                "SELECT payload FROM revisions WHERE record_id = ?", (saved["record_id"],)
            )
            matches = any(sha256(item[0].encode()).hexdigest() == payload["revision"]
                          for item in snapshots)
            annotationIds = payload.get("annotation_revisions")
            if annotationIds is not None:
                matches = matches and _verifyAnnotationRequest(connection, annotationIds, resultId)
        else:
            matches = (intakeKeys.get(saved["source_artifact_id"]) == payload["capture"]
                       and type(payload["revision"]) is int
                       and payload["revision"] == saved["source_ordinal"])
        if not matches:
            raise ProjectError(f"Save request points to a different input: {requestId}")


def _verifyAnnotationRequest(
    connection: sqlite3.Connection, revisionIds: object, savedId: str,
) -> bool:
    if (_projectHeader(connection) < 6 or type(revisionIds) is not list or len(revisionIds) < 2
            or any(type(revisionId) is not str for revisionId in revisionIds)):
        return False
    readStep = ProjectRepository._loadAnnotationStep
    if revisionIds[-1] != savedId:
        step = readStep(connection, savedId)
        if step is None or tuple(step) != (revisionIds[-1], 1):
            return False
    for parent, child in zip(revisionIds, revisionIds[1:]):
        step = readStep(connection, child)
        if step is None or tuple(step) != (parent, 0):
            return False
    return True


def _verifyPortableSources(connection: sqlite3.Connection) -> None:
    excludeAnnotations = (
        " AND r.revision_id NOT IN (SELECT child_revision_id FROM annotation_steps)"
        if _projectHeader(connection) >= 6 else ""
    )
    for row in connection.execute(
        "SELECT a.*, i.artifact_id AS has_intake FROM portable_sources p JOIN artifacts a USING (artifact_id) "
        "LEFT JOIN intakes i USING (artifact_id)"
    ):
        artifactId = row["artifact_id"]
        _validateArtifact(ProjectRepository._loadArtifact(connection, artifactId))
        if row["kind"] != "original" or row["has_intake"] is not None:
            raise ProjectError(f"Conflicting portable source ownership for artifact {artifactId!r}.")
        snapshots = connection.execute(
            "SELECT r.payload FROM revisions r WHERE source_artifact_id = ?" + excludeAnnotations,
            (artifactId,),
        )
        recordPayloads = {}
        for (payload,) in snapshots:
            revision = decodeRevision(payload)
            record = renamedRevisionPayload(revision)
            if (revision.sourceFormat != row["format"] or revision.outputReference is not None
                    or recordPayloads.setdefault(revision.sourceOrdinal, record) != record):
                raise ProjectError(f"Conflicting portable source snapshot {revision.revisionId!r}.")
        if not recordPayloads:
            raise ProjectError(f"Missing portable source snapshots for artifact {artifactId!r}.")


def verifyProject(connection: sqlite3.Connection, root: Path) -> None:
    """Validate SQLite references, stored revisions, and managed files in a read transaction."""
    from strandfolio.storage.project_exports import verifyExportRows

    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ProjectError("Damaged project database.")
    invalidLink = connection.execute("PRAGMA foreign_key_check").fetchone()
    if invalidLink is not None:
        raise ProjectError(f"Broken project reference in {invalidLink[0]}.")
    try:
        _verifyRevisions(connection)
        intakeKeys = _verifyIntakes(connection, root)
        _verifyRequests(connection, intakeKeys)
        verifyExportRows(connection)
        ProjectRepository(root)._verifyStoredRuns(connection)
        if _projectHeader(connection) >= 4:
            _verifyPortableSources(connection)
        if _projectHeader(connection) >= 8:
            verifyDeclaredParents(connection)
    except ProjectError:
        raise
    except (ValueError, TypeError, KeyError) as error:
        raise ProjectError(f"Invalid stored project metadata: {error}") from error
    portableJoin = ""
    ownsOriginal = "i.artifact_id IS NOT NULL"
    if _projectHeader(connection) >= 4:
        portableJoin = "LEFT JOIN portable_sources p USING (artifact_id) "
        ownsOriginal += " OR p.artifact_id IS NOT NULL"
    orphan = connection.execute(
        "SELECT a.artifact_id FROM artifacts a LEFT JOIN intakes i USING (artifact_id) "
        f"LEFT JOIN export_requests e USING (artifact_id) {portableJoin} WHERE "
        f"(a.kind = 'original') <> ({ownsOriginal}) OR "
        "(a.kind = 'export') <> (e.artifact_id IS NOT NULL) LIMIT 1"
    ).fetchone()
    if orphan:
        raise ProjectError(f"Missing project owner for artifact: {orphan[0]}")
    for digest, byteCount in connection.execute(
        "SELECT DISTINCT sha256, byte_count FROM artifacts WHERE kind = 'export'"
    ):
        path = _artifactPath(root, digest, byteCount)
        with path.open("rb") as payload:
            if file_digest(payload, "sha256").hexdigest() != digest:
                raise ProjectError(f"Corrupt managed artifact (SHA-256): {digest}")


def _manifest(connection: sqlite3.Connection) -> sqlite3.Cursor:
    filterClause = ""
    if _projectHeader(connection) >= 4:
        filterClause = "WHERE artifact_id NOT IN (SELECT artifact_id FROM portable_sources)"
    return connection.execute(f"SELECT DISTINCT sha256, byte_count FROM artifacts {filterClause}")


def backupProject(repository: ProjectRepository, destination: str | Path) -> ProjectRepository:
    """Copy one SQLite snapshot and its immutable files to a new independent directory.

    Validate the complete staged copy before atomically publishing the directory.
    """
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Backup destination already exists: {target}")
    if _insideProject(repository.path, target):
        raise ProjectError("Choose a backup destination outside the source project.")
    with TemporaryDirectory(prefix=".strandfolio-backup-", dir=target.parent) as directory:
        stage = Path(directory) / "project"
        stage.mkdir()
        database = stage / "project.sqlite3"
        with repository._read() as source, closing(sqlite3.connect(database)) as snapshot:
            source.backup(snapshot)
            # Fold committed WAL pages into the standalone backup before closing it.
            snapshot.execute("PRAGMA journal_mode=DELETE")
            for digest, byteCount in _manifest(snapshot):
                original = _artifactPath(repository.path, digest, byteCount)
                artifactDirectory = stage / "artifacts"
                artifactDirectory.mkdir(exist_ok=True)
                with original.open("rb") as reader, (artifactDirectory / digest).open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    _syncDescriptor(writer.fileno())
        type(repository).open(stage)
        with database.open("rb") as saved:
            _syncDescriptor(saved.fileno())
        artifactDirectory = stage / "artifacts"
        if artifactDirectory.exists():
            _syncDirectory(artifactDirectory)
        _syncDirectory(stage)
        # rename(2) may replace an empty directory. Reserve the name exclusively
        # so a preexisting empty destination is preserved as well.
        target.mkdir()
        reservation = target.stat()
        try:
            stage.replace(target)
        except OSError:
            if target.is_dir() and os.path.samestat(target.stat(), reservation):
                target.rmdir()
            raise
        _syncDirectory(target.parent)
    return type(repository).open(target)
