"""Portable SQLite projects with immutable revisions and managed source bytes."""

from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import BinaryIO, TYPE_CHECKING, cast

from strandfolio.domain.features import canonicalFeature
from strandfolio.domain.provenance import SourceArtifact
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.formats import SequenceFormat, SequenceImportResult
from strandfolio.operations.conversion import DEFAULT_GENETIC_CODE, GeneticCodePreference
from strandfolio.storage.project_digestions import DIGESTION_SCHEMA, ProjectDigestionMixin
from strandfolio.storage.project_edits import ANNOTATION_SCHEMA, AnnotationEvidence, ProjectEditMixin
from strandfolio.storage.project_exports import EXPORT_SCHEMA, ProjectExportMixin
from strandfolio.storage.project_parents import DECLARED_PARENT_SCHEMA, ProjectParentMixin
from strandfolio.storage.project_pcr import PCR_SCHEMA, CertifiedPcrRun, ProjectPcrMixin
from strandfolio.storage.project_primer_pairs import SAVED_PAIR_SCHEMA, ProjectPrimerPairMixin
from strandfolio.storage.project_runs import RESTRICTION_SCHEMA, ProjectRunMixin
from strandfolio.storage.revision_codec import decodeRevision, encodeRevision, renamedRevisionPayload

if TYPE_CHECKING:
    from strandfolio.io.digestion import SavedDigestion
    from strandfolio.operations.gel import GelViewSettings
    from strandfolio.operations.pcr import PcrRun


DATABASE_FILENAME = "project.sqlite3"
APPLICATION_ID = 0x5647454E  # VGEN
SCHEMA_VERSION = 9
_READABLE_SCHEMA_VERSIONS = tuple(range(1, SCHEMA_VERSION + 1))
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FORMATS = ("fasta", "genbank", "snapgene", "text")
_PREFERRED_NAME_COLUMN = (
    "preferred_name TEXT CHECK (preferred_name IS NULL OR length(preferred_name) > 0)"
)
_TRANSLATION_PREFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS record_translation_preferences (
    record_id TEXT PRIMARY KEY NOT NULL REFERENCES records,
    table_id INTEGER NOT NULL CHECK (typeof(table_id) = 'integer' AND table_id > 0),
    override_annotated INTEGER NOT NULL CHECK (override_annotated IN (0, 1))
)
"""
_SCHEMA = f"""
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    format TEXT NOT NULL CHECK (format IN ('fasta', 'genbank', 'snapgene', 'text')),
    kind TEXT NOT NULL CHECK (kind IN ('original', 'export'))
);
CREATE TABLE records (record_id TEXT PRIMARY KEY NOT NULL, {_PREFERRED_NAME_COLUMN});
CREATE TABLE revisions (
    revision_id TEXT PRIMARY KEY NOT NULL,
    record_id TEXT NOT NULL REFERENCES records,
    source_artifact_id TEXT REFERENCES artifacts,
    source_ordinal INTEGER,
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    payload TEXT NOT NULL,
    UNIQUE (record_id, revision_id),
    UNIQUE (source_artifact_id, source_ordinal, revision_id),
    CHECK ((source_artifact_id IS NULL AND source_ordinal IS NULL) OR
           (source_artifact_id IS NOT NULL AND source_ordinal IS NOT NULL AND source_ordinal > 0))
);
CREATE TABLE features (
    feature_id TEXT PRIMARY KEY NOT NULL,
    record_id TEXT NOT NULL REFERENCES records,
    payload TEXT NOT NULL
);
CREATE TABLE revision_features (
    revision_id TEXT NOT NULL REFERENCES revisions,
    feature_ordinal INTEGER NOT NULL CHECK (feature_ordinal > 0),
    feature_id TEXT NOT NULL REFERENCES features,
    annotation_payload TEXT,
    PRIMARY KEY (revision_id, feature_ordinal),
    UNIQUE (revision_id, feature_id)
);
CREATE TABLE named_records (
    record_id TEXT PRIMARY KEY NOT NULL REFERENCES records,
    revision_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    FOREIGN KEY (record_id, revision_id) REFERENCES revisions (record_id, revision_id)
);
CREATE TABLE intakes (
    artifact_id TEXT PRIMARY KEY NOT NULL REFERENCES artifacts,
    source_origin TEXT,
    source_path TEXT,
    interpretation TEXT NOT NULL,
    warnings TEXT NOT NULL,
    biopython_version TEXT NOT NULL,
    match_key TEXT NOT NULL,
    capture_key TEXT NOT NULL,
    is_independent INTEGER NOT NULL CHECK (is_independent IN (0, 1))
);
CREATE UNIQUE INDEX ordinary_intake ON intakes (match_key)
    WHERE source_origin IS NOT NULL AND is_independent = 0;
CREATE INDEX known_intake ON intakes (match_key, is_independent)
    WHERE source_origin IS NOT NULL;
CREATE TABLE intake_revisions (
    artifact_id TEXT NOT NULL REFERENCES intakes,
    source_ordinal INTEGER NOT NULL CHECK (source_ordinal > 0),
    revision_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY (artifact_id, source_ordinal),
    FOREIGN KEY (artifact_id, source_ordinal, revision_id)
        REFERENCES revisions (source_artifact_id, source_ordinal, revision_id)
);
CREATE TABLE requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('intake', 'save', 'export')),
    payload TEXT NOT NULL,
    result_id TEXT NOT NULL
);
"""


class ProjectError(ValueError):
    """Report an invalid project, immutable identity conflict or invalid mutation."""


class RecordPreferenceConflict(ProjectError):
    """Another project writer changed the preference shown when editing began."""


@dataclass(frozen=True, slots=True)
class ReopenableRevision:
    """Describe one stored snapshot without hydrating its sequence or source bytes."""

    revisionId: str
    recordId: str
    name: str
    originalName: str
    preferredName: str | None
    sourceFilename: str | None
    sourceOrdinal: int | None
    isNamed: bool


class _SavePreference(Enum):
    KEEP = "keep"


def _syncDescriptor(descriptor: int) -> None:
    if sys.platform == "darwin":
        import fcntl

        fcntl.fcntl(descriptor, fcntl.F_FULLFSYNC)
    else:
        os.fsync(descriptor)


def _syncDirectory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _syncDescriptor(descriptor)
    finally:
        os.close(descriptor)


def _syncFile(handle: BinaryIO) -> None:
    handle.flush()
    _syncDescriptor(handle.fileno())


def _insideProject(root: Path, candidate: Path) -> bool:
    """Check real directory ancestry, even when the target itself does not exist."""
    resolved = candidate.resolve()
    if resolved.is_relative_to(root.resolve()):
        return True
    rootInfo = root.stat()
    for ancestor in (resolved, *resolved.parents):
        try:
            if os.path.samestat(rootInfo, ancestor.stat()):
                return True
        except FileNotFoundError:
            continue
    return False


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _key(value: object) -> str:
    return sha256(_json(value).encode("ascii")).hexdigest()


def _pathValue(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _isIdentity(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and "\x00" not in value


def _normalizePreferredName(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("A preferred name must be text or None.")
    if "\x00" in value:
        raise ProjectError("A preferred name cannot contain a null character.")
    return value.strip() or None


def _validateArtifact(artifact: SourceArtifact) -> None:
    if (
        not isinstance(artifact, SourceArtifact)
        or not _isIdentity(artifact.artifactId)
        or not _isIdentity(artifact.filename)
        or "/" in artifact.filename
        or artifact.filename in (".", "..")
        or type(artifact.sha256) is not str
        or not _SHA256.fullmatch(artifact.sha256)
        or type(artifact.byteCount) is not int
        or artifact.byteCount <= 0
    ):
        raise ProjectError("Invalid artifact descriptor.")


def _verifyBytes(artifact: SourceArtifact, raw: bytes) -> None:
    try:
        artifact.verifyCapture(raw)
    except ValueError as error:
        raise ProjectError(str(error)) from error


def _readArtifact(root: Path, artifact: SourceArtifact) -> bytes:
    _validateArtifact(artifact)
    directory = root / "artifacts"
    if directory.is_symlink():
        raise ProjectError(f"Managed artifact directory is a symbolic link: {directory}.")
    return _readArtifactFile(directory / artifact.sha256, artifact)


def _readArtifactFile(path: Path, artifact: SourceArtifact) -> bytes:
    if path.is_symlink():
        raise ProjectError(f"Managed artifact {artifact.artifactId} must be a regular file.")
    try:
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open("rb") as handle:
            raw = handle.read(artifact.byteCount + 1)
    except OSError as error:
        raise ProjectError(
            f"Missing or unreadable managed artifact {artifact.artifactId} ({artifact.sha256})."
        ) from error
    _verifyBytes(artifact, raw)
    return raw


def _matchKey(capture: SequenceImportResult) -> str:
    snapshots = []
    for revision in capture.records:
        content = json.loads(encodeRevision(revision))
        for field in ("source_artifact_id", "source_path", "record_id", "revision_id", "feature_ids"):
            del content[field]
        snapshots.append(_key(content))
    return _key((
        _pathValue(capture.sourceOrigin), capture.source.sha256, capture.formatName,
        capture.interpretation, snapshots,
    ))


def _captureKey(capture: SequenceImportResult) -> str:
    return _key((
        _pathValue(capture.sourceOrigin),
        capture.source.sha256, capture.formatName, capture.interpretation, capture.biopythonVersion,
        capture.source.artifactId, capture.source.filename,
        _pathValue(capture.sourcePath), capture.warnings,
        [sha256(encodeRevision(revision).encode("utf-8")).hexdigest()
         for revision in capture.records],
    ))


def _captureRequestKey(capture: SequenceImportResult) -> str:
    # A parser upgrade or new warning can leave the interpreted content unchanged.
    # The original occurrence still retains its exact version and warnings.
    return _key((_matchKey(capture), capture.source.filename))


def _checkedEncoding(revision: SequenceRevision) -> str:
    if not isinstance(revision, SequenceRevision):
        raise TypeError("Expected a sequence revision.")
    if not _isIdentity(revision.recordId) or not _isIdentity(revision.revisionId):
        raise ProjectError("Invalid record or revision ID.")
    try:
        payload = encodeRevision(revision)
        hydrated = decodeRevision(payload)
    except ValueError as error:
        raise ProjectError(str(error)) from error
    if hydrated.features != revision.features:
        raise ProjectError(f"Inconsistent feature projections on revision {revision.revisionId!r}.")
    return payload


def _validateCapture(capture: SequenceImportResult) -> None:
    if not isinstance(capture, SequenceImportResult):
        raise TypeError("Expected a sequence import capture.")
    _validateArtifact(capture.source)
    _verifyBytes(capture.source, capture.captureBytes)
    if (
        capture.formatName not in _FORMATS
        or not _isIdentity(capture.biopythonVersion)
        or capture.interpretation not in (
            "automatic", "declared", "chosen:DNA", "chosen:RNA", "chosen:protein",
        )
        or type(capture.warnings) is not tuple
        or any(type(warning) is not str for warning in capture.warnings)
        or type(capture.records) is not tuple
        or not capture.records
        or (capture.sourceOrigin is None) != (capture.sourcePath is None)
        or any(path is not None and (not isinstance(path, Path) or not path.is_absolute())
               for path in (capture.sourceOrigin, capture.sourcePath))
    ):
        raise ProjectError("Invalid import capture metadata.")
    if capture.sourceOrigin is not None and capture.sourceOrigin.name != capture.source.filename:
        raise ProjectError("Captured filename differs from the declared source origin.")
    if (
        capture.formatName in ("genbank", "snapgene") and capture.interpretation != "declared"
        or capture.formatName == "fasta" and capture.interpretation == "declared"
        or capture.formatName == "text" and not capture.interpretation.startswith("chosen:")
        or capture.sourceOrigin is None and capture.formatName != "text"
        or capture.formatName in ("snapgene", "text") and len(capture.records) != 1
    ):
        raise ProjectError("Import interpretation and format are inconsistent.")
    recordIds, revisionIds, featureIds = set(), set(), set()
    for ordinal, revision in enumerate(capture.records, start=1):
        if (
            not isinstance(revision, SequenceRevision)
            or revision.sourceArtifactId != capture.source.artifactId
            or revision.outputReference is not None
            or type(revision.sourceOrdinal) is not int
            or revision.sourceOrdinal != ordinal
            or revision.sourceFormat != capture.formatName
            or revision.sourcePath != capture.sourcePath
            or capture.interpretation.startswith("chosen:")
            and capture.interpretation != f"chosen:{revision.moleculeType}"
            or not _isIdentity(revision.recordId)
            or not _isIdentity(revision.revisionId)
            or revision.recordId in recordIds
            or revision.revisionId in revisionIds
        ):
            raise ProjectError(f"Inconsistent imported record at ordinal {ordinal}.")
        _checkedEncoding(revision)
        recordIds.add(revision.recordId)
        revisionIds.add(revision.revisionId)
        for feature in revision.features:
            if not _isIdentity(feature.featureId) or feature.featureId in featureIds:
                raise ProjectError(f"Conflicting imported feature ID {feature.featureId!r}.")
            featureIds.add(feature.featureId)


def _freshCapture(capture: SequenceImportResult) -> SequenceImportResult:
    source = SourceArtifact.fromCapture(capture.source.filename, capture.captureBytes)
    records = []
    for revision in capture.records:
        record = revision.toSeqRecord()
        if revision.moleculeDeclaration is None:
            del record.annotations["molecule_type"]
        records.append(SequenceRevision.fromSeqRecord(
            record, moleculeType=revision.moleculeType, sourceArtifactId=source.artifactId,
            sourceOrdinal=revision.sourceOrdinal, sourcePath=revision.sourcePath,
            sourceFormat=revision.sourceFormat,
        ))
    return replace(capture, source=source, records=tuple(records))


def _projectHeader(connection: sqlite3.Connection) -> int:
    if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise ProjectError("Invalid strandfolio project signature.")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in _READABLE_SCHEMA_VERSIONS:
        raise ProjectError(f"Unsupported strandfolio project schema version {version}.")
    return version


class ProjectRepository(
    ProjectExportMixin, ProjectRunMixin, ProjectDigestionMixin, ProjectPcrMixin, ProjectEditMixin,
    ProjectParentMixin, ProjectPrimerPairMixin,
):
    """Read and commit sequence snapshots, source bytes and recorded analyses."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, path: str | Path) -> "ProjectRepository":
        """Create a project in a new or empty directory without replacing any file."""
        root = Path(path).resolve()
        root.mkdir(exist_ok=True)
        if any(root.iterdir()):
            raise FileExistsError(f"Project directory is not empty: {root}")
        with NamedTemporaryFile(dir=root, prefix=".new-project-", delete=False) as handle:
            stagedPath = Path(handle.name)
        try:
            with closing(sqlite3.connect(stagedPath)) as connection:
                runSchema = ";".join(
                    (*RESTRICTION_SCHEMA, *DIGESTION_SCHEMA, *PCR_SCHEMA, ANNOTATION_SCHEMA,
                     _TRANSLATION_PREFERENCES_SCHEMA, DECLARED_PARENT_SCHEMA, *SAVED_PAIR_SCHEMA),
                )
                connection.executescript(
                    f"PRAGMA synchronous=FULL; PRAGMA fullfsync=ON; BEGIN IMMEDIATE;"
                    f"PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version={SCHEMA_VERSION};"
                    f"{_SCHEMA}{EXPORT_SCHEMA}{runSchema};COMMIT;"
                )
            with stagedPath.open("rb") as handle:
                _syncDescriptor(handle.fileno())
            os.link(stagedPath, root / DATABASE_FILENAME)
            _syncDirectory(root)
            _syncDirectory(root.parent)
        finally:
            stagedPath.unlink()
        return cls.open(root)

    @classmethod
    def open(cls, path: str | Path) -> "ProjectRepository":
        """Open and validate a complete project without touching external originals."""
        repository = cls(Path(path).resolve())
        databasePath = repository.path / DATABASE_FILENAME
        if databasePath.is_symlink() or not databasePath.is_file():
            raise ProjectError(f"No regular {DATABASE_FILENAME} at {repository.path}.")
        try:
            repository._verifyOpen()
        except sqlite3.DatabaseError as error:
            if getattr(error, "sqlite_errorcode", None) != sqlite3.SQLITE_READONLY_ROLLBACK:
                raise ProjectError(f"Invalid strandfolio project database: {error}") from error
            repository._recoverJournal(databasePath)
            try:
                repository._verifyOpen()
            except sqlite3.DatabaseError as recoveredError:
                raise ProjectError(f"Invalid strandfolio project database: {recoveredError}") from recoveredError
        return repository

    def _verifyOpen(self) -> None:
        from strandfolio.storage.project_backup import verifyProject

        with self._read() as connection:
            try:
                verifyProject(connection, self.path)
                version = _projectHeader(connection)
                if version >= 2:
                    for recordId, preferredName in connection.execute(
                        "SELECT record_id, preferred_name FROM records WHERE preferred_name IS NOT NULL"
                    ):
                        if preferredName != _normalizePreferredName(preferredName):
                            raise ProjectError(f"Invalid preferred name on record {recordId!r}.")
                if version >= 7:
                    for tableId, override in connection.execute(
                        "SELECT table_id, override_annotated FROM record_translation_preferences",
                    ):
                        GeneticCodePreference(tableId, bool(override))
            except ProjectError:
                raise
            except (TypeError, ValueError) as error:
                raise ProjectError(f"Invalid project data: {error}") from error

    def _recoverJournal(self, databasePath: Path) -> None:
        """Permit SQLite to roll back an interrupted write after checking the disk header."""
        with databasePath.open("rb") as handle:
            header = handle.read(100)
        if (
            len(header) != 100
            or header[:16] != b"SQLite format 3\x00"
            or int.from_bytes(header[68:72], "big") != APPLICATION_ID
        ):
            raise ProjectError("Invalid strandfolio project signature.")
        version = int.from_bytes(header[60:64], "big")
        if version not in _READABLE_SCHEMA_VERSIONS:
            raise ProjectError(f"Unsupported strandfolio project schema version {version}.")
        with closing(self._connect("rw")) as connection:
            _projectHeader(connection)

    def _connect(self, mode: str, *, timeout: float = 30.0) -> sqlite3.Connection:
        uri = f"{(self.path / DATABASE_FILENAME).as_uri()}?mode={mode}"
        connection = sqlite3.connect(uri, uri=True, timeout=timeout)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect("ro")) as connection:
            connection.execute("BEGIN")
            _projectHeader(connection)
            yield connection

    @contextmanager
    def _write(self, *, timeout: float = 30.0) -> Iterator[sqlite3.Connection]:
        with closing(self._connect("rw", timeout=timeout)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA fullfsync=ON")
            connection.execute("BEGIN IMMEDIATE")
            version = _projectHeader(connection)
            if version == 1:
                connection.execute(f"ALTER TABLE records ADD COLUMN {_PREFERRED_NAME_COLUMN}")
                if connection.execute("PRAGMA integrity_check('records')").fetchone()[0] != "ok":
                    raise ProjectError("Could not validate the upgraded project records.")
            if version < 3:
                for statement in RESTRICTION_SCHEMA:
                    connection.execute(statement)
            if version < 4:
                for statement in DIGESTION_SCHEMA:
                    connection.execute(statement)
            if version < 5:
                for statement in PCR_SCHEMA:
                    connection.execute(statement)
            if version < 6:
                featureColumns = {row["name"] for row in connection.execute(
                    "PRAGMA table_info(revision_features)",
                )}
                if "annotation_payload" not in featureColumns:
                    connection.execute("ALTER TABLE revision_features ADD COLUMN annotation_payload TEXT")
                connection.execute(ANNOTATION_SCHEMA)
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ProjectError("Could not validate the upgraded project references.")
            if version < 7:
                connection.execute(_TRANSLATION_PREFERENCES_SCHEMA)
            if version < 8:
                connection.execute(DECLARED_PARENT_SCHEMA)
            if version < 9:
                for statement in SAVED_PAIR_SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            yield connection
            self._commit(connection)

    def _commit(self, connection: sqlite3.Connection) -> None:
        connection.commit()

    @staticmethod
    def _validateRequestId(requestId: str) -> None:
        if type(requestId) is not str or not _REQUEST_ID.fullmatch(requestId):
            raise ProjectError("Request ID must be a safe, nonempty project staging name.")

    def _checkRequest(
        self, connection: sqlite3.Connection, requestId: str, kind: str, payload: str,
    ) -> str | None:
        row = connection.execute(
            "SELECT kind, payload, result_id FROM requests WHERE request_id = ?", (requestId,),
        ).fetchone()
        if row is None:
            return None
        if (row["kind"], row["payload"]) != (kind, payload):
            raise ProjectError(f"Request ID {requestId!r} already has a different payload.")
        return row["result_id"]

    def _recordRequest(
        self, connection: sqlite3.Connection, requestId: str, kind: str, payload: str, resultId: str,
    ) -> None:
        connection.execute(
            "INSERT INTO requests (request_id, kind, payload, result_id) VALUES (?, ?, ?, ?)",
            (requestId, kind, payload, resultId),
        )

    def _dataDirectory(self, parent: Path, name: str) -> tuple[Path, bool]:
        target = parent / name
        try:
            target.mkdir()
            created = True
        except FileExistsError:
            created = False
        if target.is_symlink() or not target.is_dir():
            raise ProjectError(f"Project data path is not a regular directory: {target}")
        _syncDirectory(parent)
        return target, created

    def _promoteArtifact(self, stagedPath: Path, artifactPath: Path) -> None:
        os.link(stagedPath, artifactPath)
        _syncDirectory(artifactPath.parent)

    def _storeArtifact(
        self, connection: sqlite3.Connection, artifact: SourceArtifact, raw: bytes,
        formatName: str, kind: str, requestId: str,
    ) -> None:
        _validateArtifact(artifact)
        _verifyBytes(artifact, raw)
        self._recordArtifact(connection, artifact, formatName, kind)
        self._preserveArtifact(artifact, raw, requestId)

    def _recordArtifact(
        self, connection: sqlite3.Connection, artifact: SourceArtifact, formatName: str, kind: str,
    ) -> None:
        row = connection.execute(
            "SELECT filename, sha256, byte_count, format, kind FROM artifacts WHERE artifact_id = ?",
            (artifact.artifactId,),
        ).fetchone()
        data = (artifact.filename, artifact.sha256, artifact.byteCount, formatName, kind)
        if row is None:
            connection.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)", (artifact.artifactId, *data),
            )
        elif tuple(row) != data:
            raise ProjectError(f"Conflicting artifact ID {artifact.artifactId!r}.")

    def _preserveArtifact(self, artifact: SourceArtifact, raw: bytes, requestId: str) -> None:
        artifactDirectory, _ = self._dataDirectory(self.path, "artifacts")
        artifactPath = artifactDirectory / artifact.sha256
        if artifactPath.exists() or artifactPath.is_symlink():
            _readArtifact(self.path, artifact)
            with artifactPath.open("rb") as handle:
                _syncDescriptor(handle.fileno())
            _syncDirectory(artifactDirectory)
            self._clearStagedArtifact(artifact, requestId)
        else:
            self._stageArtifact(artifact, raw, requestId, artifactPath)

    def _stageArtifact(
        self, artifact: SourceArtifact, raw: bytes, requestId: str, artifactPath: Path,
    ) -> None:
        self._validateRequestId(requestId)
        staging, _ = self._dataDirectory(self.path, "staging")
        requestDirectory, created = self._dataDirectory(staging, requestId)
        stagedPath = requestDirectory / f"{artifact.sha256}.verified"
        if stagedPath.exists() or stagedPath.is_symlink():
            _readArtifactFile(stagedPath, artifact)
        else:
            with NamedTemporaryFile(dir=requestDirectory, prefix="writing-") as handle:
                handle.write(raw)
                _syncFile(handle)
                _readArtifactFile(Path(handle.name), artifact)
                os.link(handle.name, stagedPath)
            _syncDirectory(requestDirectory)
        self._promoteArtifact(stagedPath, artifactPath)
        self._removeStagedPath(stagedPath, removeParent=created)

    @staticmethod
    def _removeStagedPath(stagedPath: Path, *, removeParent: bool) -> None:
        stagedPath.unlink()
        requestDirectory = stagedPath.parent
        if removeParent and not any(requestDirectory.iterdir()):
            requestDirectory.rmdir()
            _syncDirectory(requestDirectory.parent)
        else:
            _syncDirectory(requestDirectory)

    def _clearStagedArtifact(self, artifact: SourceArtifact, requestId: str) -> None:
        """Remove verified staging files left by an interrupted promotion."""
        self._validateRequestId(requestId)
        root = self.path / "staging"
        request = root / requestId
        stagedPath = request / f"{artifact.sha256}.verified"
        if root.is_symlink() or request.is_symlink() or not stagedPath.is_file():
            return
        try:
            _readArtifactFile(stagedPath, artifact)
        except ProjectError:
            return
        self._removeStagedPath(stagedPath, removeParent=False)

    def _storeRevision(
        self, connection: sqlite3.Connection, revision: SequenceRevision, *,
        isIntake: bool = False, isNamed: bool = False, isOperationOutput: bool = False,
        isPortableEvidence: bool = False, annotationParent: SequenceRevision | None = None,
    ) -> None:
        payload = _checkedEncoding(revision)
        data = (
            revision.recordId, revision.sourceArtifactId, revision.sourceOrdinal,
            revision.contentDigest, payload,
        )
        row = connection.execute(
            "SELECT record_id, source_artifact_id, source_ordinal, content_digest, payload "
            "FROM revisions WHERE revision_id = ?", (revision.revisionId,),
        ).fetchone()
        if row is not None:
            if tuple(row) != data:
                raise ProjectError(f"Conflicting revision ID {revision.revisionId!r}.")
            return
        if revision.outputReference is not None and not (isOperationOutput or isNamed or isPortableEvidence):
            raise ProjectError("Register the generating restriction run or PCR run before using its output.")

        exists = connection.execute(
            "SELECT 1 FROM records WHERE record_id = ?", (revision.recordId,),
        ).fetchone() is not None
        sameAnnotationRecord = (
            annotationParent is not None and annotationParent.recordId == revision.recordId
        )
        if not isPortableEvidence and exists != (isNamed or sameAnnotationRecord):
            raise ProjectError(f"Conflicting record ID {revision.recordId!r}.")
        # A record first introduced by an annotation edge cannot gain an unlinked root.
        if exists and annotationParent is None and connection.execute(
            "SELECT 1 FROM revisions r JOIN annotation_steps a ON a.child_revision_id = r.revision_id "
            "JOIN revisions p ON p.revision_id = a.parent_revision_id "
            "WHERE r.record_id = ? AND p.record_id <> r.record_id LIMIT 1", (revision.recordId,),
        ).fetchone() is not None:
            raise ProjectError(f"Conflicting record ID {revision.recordId!r}.")
        if revision.sourceArtifactId is not None:
            if (not (isIntake or isPortableEvidence or annotationParent is not None)
                    and (not isNamed or not self._hasSourceRecord(connection, revision))):
                raise ProjectError("Revision must link to its exact registered intake.")
        elif any(value is not None for value in (
            revision.sourceOrdinal, revision.sourceFormat, revision.sourcePath,
        )):
            raise ProjectError("Revision has source metadata without an artifact.")

        if not exists:
            connection.execute("INSERT INTO records (record_id) VALUES (?)", (revision.recordId,))
        connection.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)", (revision.revisionId, *data))
        self._storeFeatures(connection, revision, annotationParent)

    @staticmethod
    def _hasSourceRecord(connection: sqlite3.Connection, revision: SequenceRevision) -> bool:
        return connection.execute(
            "SELECT 1 FROM revisions r WHERE r.source_artifact_id = ? AND r.source_ordinal = ? "
            "AND r.record_id = ? AND (EXISTS (SELECT 1 FROM intake_revisions i "
            "WHERE i.revision_id = r.revision_id) OR EXISTS (SELECT 1 FROM portable_sources p "
            "WHERE p.artifact_id = r.source_artifact_id))",
            (revision.sourceArtifactId, revision.sourceOrdinal, revision.recordId),
        ).fetchone() is not None

    @staticmethod
    def _firstRecordRevision(connection: sqlite3.Connection, recordId: str) -> SequenceRevision | None:
        row = connection.execute(
            "SELECT payload FROM revisions WHERE record_id = ? ORDER BY rowid LIMIT 1", (recordId,),
        ).fetchone()
        return decodeRevision(row[0]) if row is not None else None

    def _storeFeatures(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
        annotationParent: SequenceRevision | None,
    ) -> None:
        record = revision.toSeqRecord()
        inheritedIds = {item.featureId for item in annotationParent.features} if annotationParent else set()
        featureIds = set()
        for ordinal, (projection, feature) in enumerate(
            zip(revision.features, record.features, strict=True), start=1,
        ):
            if not _isIdentity(projection.featureId) or projection.featureId in featureIds:
                raise ProjectError(f"Invalid or repeated feature ID {projection.featureId!r}.")
            featureIds.add(projection.featureId)
            featureData = (revision.recordId, _json(canonicalFeature(feature, len(revision), native=True)))
            row = connection.execute(
                "SELECT record_id, payload FROM features WHERE feature_id = ?", (projection.featureId,),
            ).fetchone()
            annotationPayload = None
            if row is None:
                connection.execute(
                    "INSERT INTO features VALUES (?, ?, ?)", (projection.featureId, *featureData),
                )
            elif tuple(row) != featureData:
                if projection.featureId not in inheritedIds:
                    raise ProjectError(f"Conflicting feature ID {projection.featureId!r}.")
                annotationPayload = featureData[1]
            elif projection.featureId not in inheritedIds and annotationParent is not None:
                raise ProjectError(f"Conflicting feature ID {projection.featureId!r}.")
            connection.execute(
                "INSERT INTO revision_features VALUES (?, ?, ?, ?)",
                (revision.revisionId, ordinal, projection.featureId, annotationPayload),
            )

    def _registerCapture(
        self, connection: sqlite3.Connection, capture: SequenceImportResult,
        *, independent: bool, preserveCaptureIdentity: bool = False,
    ) -> tuple[SequenceImportResult, bool]:
        key = _captureKey(capture)
        artifactId = capture.source.artifactId
        existing = self._checkCaptureIdentities(connection, capture, key)
        wasPortable = self._hasPortableSource(connection, artifactId)
        if existing and (
            preserveCaptureIdentity or (capture.sourceOrigin is None and not independent)
        ):
            return self._loadIntake(connection, artifactId), False
        if (existing or wasPortable) and independent and not preserveCaptureIdentity:
            capture = _freshCapture(capture)
            artifactId = capture.source.artifactId
            key = _captureKey(capture)
            wasPortable = False
            self._checkCaptureIdentities(connection, capture, key)

        matchKey = _matchKey(capture)
        if not independent and capture.sourceOrigin is not None:
            known = self._findMatchingIntake(connection, matchKey)
            if known is not None:
                if not wasPortable:
                    return known, False
                # This occurrence already has immutable references in portable run history.
                independent = True

        self._recordArtifact(connection, capture.source, capture.formatName, "original")
        connection.execute(
            "INSERT INTO intakes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (artifactId, _pathValue(capture.sourceOrigin), _pathValue(capture.sourcePath),
             capture.interpretation, _json(capture.warnings), capture.biopythonVersion,
             matchKey, key, independent),
        )
        for revision in capture.records:
            self._storeRevision(connection, revision, isIntake=True, isPortableEvidence=wasPortable)
            connection.execute(
                "INSERT INTO intake_revisions VALUES (?, ?, ?)",
                (artifactId, revision.sourceOrdinal, revision.revisionId),
            )
        if wasPortable:
            connection.execute("DELETE FROM portable_intake_revisions WHERE artifact_id = ?", (artifactId,))
            connection.execute("DELETE FROM portable_sources WHERE artifact_id = ?", (artifactId,))
        return capture, True

    def _findMatchingIntake(
        self, connection: sqlite3.Connection, matchKey: str,
    ) -> SequenceImportResult | None:
        known = connection.execute(
            "SELECT artifact_id FROM intakes WHERE match_key = ? AND source_origin IS NOT NULL "
            "ORDER BY is_independent, rowid LIMIT 1", (matchKey,),
        ).fetchone()
        return self._loadIntake(connection, known["artifact_id"]) if known is not None else None

    def findMatchingIntake(self, capture: SequenceImportResult) -> SequenceImportResult | None:
        """Read a matching file intake without recording a request or writing artifacts."""
        _validateCapture(capture)
        if capture.sourceOrigin is None:
            return None
        with self._read() as connection:
            self._checkCaptureIdentities(connection, capture, _captureKey(capture))
            return self._findMatchingIntake(connection, _matchKey(capture))

    def _checkCaptureIdentities(
        self, connection: sqlite3.Connection, capture: SequenceImportResult, key: str,
    ) -> bool:
        artifactId = capture.source.artifactId
        row = connection.execute(
            "SELECT capture_key FROM intakes WHERE artifact_id = ?", (artifactId,),
        ).fetchone()
        if row is not None:
            if row["capture_key"] != key:
                raise ProjectError(f"Conflicting intake artifact ID {artifactId!r}.")
            return True
        if self._hasPortableSource(connection, artifactId):
            claimed, exact = self._checkPortableCapture(connection, capture)
            identities = []
        else:
            claimed, exact = set(), set()
            identities = [("artifacts", "artifact_id", artifactId)]
        for revision in capture.records:
            if revision.recordId not in claimed:
                identities.append(("records", "record_id", revision.recordId))
                identities.extend(("features", "feature_id", item.featureId) for item in revision.features)
            if revision.revisionId not in exact:
                identities.append(("revisions", "revision_id", revision.revisionId))
        for table, column, identity in identities:
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?", (identity,),
            ).fetchone() is not None:
                raise ProjectError(f"Conflicting immutable ID {identity!r} in {table}.")
        return False

    def _checkPortableCapture(
        self, connection: sqlite3.Connection, capture: SequenceImportResult,
    ) -> tuple[set[str], set[str]]:
        artifactId = capture.source.artifactId
        formatName = connection.execute(
            "SELECT format FROM artifacts WHERE artifact_id = ?", (artifactId,),
        ).fetchone()[0]
        if self._loadArtifact(connection, artifactId) != capture.source or formatName != capture.formatName:
            raise ProjectError(f"Conflicting portable source descriptor for artifact {artifactId!r}.")
        excludeAnnotations = (
            " AND r.revision_id NOT IN (SELECT child_revision_id FROM annotation_steps)"
            if _projectHeader(connection) >= 6 else ""
        )
        rows = connection.execute(
            "SELECT r.source_ordinal, r.payload, p.revision_id IS NOT NULL AS is_intake "
            "FROM revisions r LEFT JOIN portable_intake_revisions p USING (revision_id) "
            "WHERE r.source_artifact_id = ?" + excludeAnnotations, (artifactId,),
        )
        claimed, exact = set(), set()
        for ordinal, payload, isIntake in rows:
            if not 1 <= ordinal <= len(capture.records):
                raise ProjectError(f"Capture is missing portable source ordinal {ordinal}.")
            incoming = capture.records[ordinal - 1]
            known = decodeRevision(payload)
            if known.revisionId == incoming.revisionId and payload == encodeRevision(incoming):
                exact.add(incoming.revisionId)
            elif (isIntake or known.revisionId == incoming.revisionId
                    or renamedRevisionPayload(known) != renamedRevisionPayload(incoming)):
                raise ProjectError(f"Capture conflicts with portable source revision {known.revisionId!r}.")
            claimed.add(incoming.recordId)
        return claimed, exact

    def acceptImport(
        self, capture: SequenceImportResult, *, requestId: str, independent: bool = False,
        intakeGeneticCodePreferences: Mapping[int, GeneticCodePreference] | None = None,
    ) -> SequenceImportResult:
        """Commit captured originals and ordered intake snapshots together."""
        self._validateRequestId(requestId)
        _validateCapture(capture)
        if type(independent) is not bool:
            raise TypeError("independent must be a boolean.")
        preferences = self._checkIntakeGeneticCodePreferences(capture, intakeGeneticCodePreferences)
        request = {"capture": _captureRequestKey(capture), "independent": independent}
        if preferences:
            request["intake_genetic_code_preferences"] = self._encodeIntakePreferences(preferences)
        payload = _json(request)
        with self._write() as connection:
            previousId = self._checkRequest(connection, requestId, "intake", payload)
            if previousId is not None:
                self._checkCaptureIdentities(connection, capture, _captureKey(capture))
                return self._loadIntake(connection, previousId)
            accepted, isNew = self._registerCapture(connection, capture, independent=independent)
            self._storeIntakeGeneticCodePreferences(connection, accepted, preferences)
            self._recordRequest(connection, requestId, "intake", payload, accepted.source.artifactId)
            if isNew:
                self._preserveArtifact(accepted.source, accepted.captureBytes, requestId)
            return accepted

    def saveRevision(
        self, revision: SequenceRevision, name: str, *, requestId: str,
        capture: SequenceImportResult | None = None, independent: bool = False,
        preferredName: str | None | _SavePreference = _SavePreference.KEEP,
        geneticCodePreference: GeneticCodePreference | None = None,
        intakeGeneticCodePreferences: Mapping[int, GeneticCodePreference] | None = None,
        digestion: "SavedDigestion | None" = None,
        analysisRuns: "tuple[PcrRun | SavedDigestion, ...]" = (),
        analysisCapture: SequenceImportResult | None = None, analysisIndependent: bool = False,
        analysisCertifications: tuple[CertifiedPcrRun, ...] = (),
        analysisViews: "tuple[tuple[str, GelViewSettings], ...]" = (),
        annotationEvidence: AnnotationEvidence | None = None,
        analysisAnnotations: tuple[AnnotationEvidence, ...] = (),
    ) -> SequenceRevision:
        """Commit a named revision, its supplied evidence and optional display preference.

        An independent capture keeps its own intake identities. Omitting the preference
        preserves it, while passing None or blank text clears it. A digestion supplied
        with one of its exact output candidates retains its evidence in the same write.
        Supply additional staged analyses in dependency order, with an exact source
        capture when their intake still needs to be made durable. Staged PCR views
        initialize preview settings alongside their runs on the first save.
        Reusing a matching request returns its committed revision after checking evidence.
        Annotation evidence stores the applied snapshots and their immutable parent links.
        Supplied genetic code preferences are committed with the named record and
        its accepted source capture. Intake settings bind to one-based source ordinals.
        """
        self._validateRequestId(requestId)
        if type(name) is not str or not name.strip():
            raise ProjectError("A saved revision requires a nonblank name.")
        if type(independent) is not bool:
            raise TypeError("independent must be a boolean.")
        if independent and capture is None:
            raise ProjectError("An independent save requires its source capture.")
        updatesPreferredName = preferredName is not _SavePreference.KEEP
        normalizedPreference = _normalizePreferredName(preferredName) if updatesPreferredName else None
        if geneticCodePreference is not None:
            self._checkGeneticCodePreference(geneticCodePreference)
        name = name.strip()
        nameKey = name.casefold()
        encoded = _checkedEncoding(revision)
        base = revision
        if annotationEvidence is not None:
            self._validateAnnotationEvidence(annotationEvidence, revision)
            if capture is not None or independent:
                raise ProjectError("Pass an edited intake through its annotation evidence.")
            base = annotationEvidence.history[0]
            if base.recordId != revision.recordId and base.name.casefold() == nameKey:
                raise ProjectError("A descendant name must differ from its parent's name.")
        if digestion is not None:
            digestion = self._checkedAdoptionDigestion(digestion, base, _checkedEncoding(base))
        if analysisRuns or analysisCapture is not None or analysisIndependent or analysisViews:
            if base.outputReference is None:
                raise ProjectError("Staged analyses require a generating output reference.")
            analyses = self._prepareAnalysisRuns(
                analysisRuns, analysisCapture, analysisIndependent, trailing=digestion,
                output=base.outputReference, annotations=analysisAnnotations,
            )
        else:
            analyses = ()
        preferenceCapture = capture or (annotationEvidence.capture if annotationEvidence else None)
        if preferenceCapture is None and analyses:
            preferenceCapture = analysisCapture
        intakePreferences = self._checkIntakeGeneticCodePreferences(
            preferenceCapture, intakeGeneticCodePreferences,
        )
        revisionKey: str | int = sha256(encoded.encode("utf-8")).hexdigest()
        captureKey = None
        if capture is not None:
            _validateCapture(capture)
            ordinal = revision.sourceOrdinal
            if (type(ordinal) is not int or not 1 <= ordinal <= len(capture.records)
                    or encodeRevision(capture.records[ordinal - 1]) != encoded):
                raise ProjectError("The candidate is not an exact snapshot from the supplied capture.")
            captureKey = _captureRequestKey(capture)
            revisionKey = ordinal
        request = {"revision": revisionKey, "name": name, "capture": captureKey}
        if independent:
            request["independent"] = True
        if updatesPreferredName:
            request["preferred_name"] = normalizedPreference
        if geneticCodePreference is not None:
            request["genetic_code_preference"] = (
                geneticCodePreference.tableId, geneticCodePreference.overrideAnnotated,
            )
        if intakePreferences:
            request["intake_genetic_code_preferences"] = self._encodeIntakePreferences(intakePreferences)
        if annotationEvidence is not None:
            request["annotation_revisions"] = [item.revisionId for item in annotationEvidence.history]
        payload = _json(request)
        with self._write() as connection:
            previousId = self._checkRequest(connection, requestId, "save", payload)
            if previousId is not None:
                named = self._loadRevision(connection, previousId)
                if (capture is not None
                        and self._checkCaptureIdentities(connection, capture, _captureKey(capture))
                        and capture.source.artifactId != named.sourceArtifactId):
                    raise ProjectError(f"Request ID {requestId!r} already names a different intake.")
                # Validate replay evidence through ordinary registration. All captures must
                # already be committed so the savepoint cannot cause managed file writes.
                evidenceCaptures = (analysisCapture, *(item.capture for item in analysisAnnotations))
                if annotationEvidence is not None:
                    evidenceCaptures += (annotationEvidence.capture,)
                for evidenceCapture in evidenceCaptures:
                    if evidenceCapture is not None and not self._checkCaptureIdentities(
                        connection, evidenceCapture, _captureKey(evidenceCapture),
                    ):
                        raise ProjectError(f"Save request {requestId!r} has an uncommitted evidence capture.")
                connection.execute("SAVEPOINT save_replay")
            newAnalysisCapture = self._storeAnalysisRuns(
                connection, analyses, analysisCapture, analysisIndependent,
                certifications=analysisCertifications, views=analysisViews,
                annotations=analysisAnnotations,
            ) if analyses or analysisViews else None
            if digestion is not None and not analyses:
                self._storeDigestion(connection, digestion, updateView=False)
                self._storeDigestionAnnotations(connection, digestion, analysisAnnotations)
            if annotationEvidence is not None:
                self._storeAnnotationEvidence(connection, annotationEvidence, requestId)
            if previousId is not None:
                connection.execute("ROLLBACK TO save_replay")
                connection.execute("RELEASE save_replay")
                return named
            newCapture = None
            if capture is not None:
                accepted, isNew = self._registerCapture(
                    connection, capture, independent=independent, preserveCaptureIdentity=True,
                )
                revision = accepted.records[ordinal - 1]
                if isNew:
                    newCapture = accepted
                preferenceCapture = accepted
            if annotationEvidence is not None:
                if base.recordId == revision.recordId:
                    self._checkOutputAdoption(connection, base, nameKey)
            elif revision.outputReference is None:
                self._storeRevision(connection, revision)
            else:
                self._storeOutputAdoptionCandidate(connection, revision)
            named = self._nameRoot(connection, revision, name, nameKey)
            self._storeIntakeGeneticCodePreferences(connection, preferenceCapture, intakePreferences)
            if updatesPreferredName:
                self._storePreferredName(connection, named.recordId, normalizedPreference)
            if geneticCodePreference is not None:
                self._storeGeneticCodePreference(connection, named.recordId, geneticCodePreference)
            self._recordRequest(connection, requestId, "save", payload, named.revisionId)
            if newCapture is not None:
                self._preserveArtifact(newCapture.source, newCapture.captureBytes, requestId)
            if newAnalysisCapture is not None:
                self._preserveArtifact(newAnalysisCapture.source, newAnalysisCapture.captureBytes, requestId)
            return named

    def _nameRoot(
        self, connection: sqlite3.Connection, revision: SequenceRevision, name: str, nameKey: str,
    ) -> SequenceRevision:
        self._checkOutputAdoption(connection, self._annotationProductBase(connection, revision), nameKey)
        existing = connection.execute(
            "SELECT record_id, revision_id, name FROM named_records WHERE name_key = ?", (nameKey,),
        ).fetchone()
        if existing is not None:
            if (revision.outputReference is not None
                    and (existing["record_id"], existing["name"]) == (revision.recordId, name)):
                return self._loadRevision(connection, existing["revision_id"])
            if tuple(existing) != (revision.recordId, revision.revisionId, name):
                raise ProjectError(f"Saved name {name!r} is already in use.")
            return revision
        if connection.execute(
            "SELECT 1 FROM named_records WHERE record_id = ?", (revision.recordId,),
        ).fetchone() is not None:
            raise ProjectError(f"Record ID {revision.recordId!r} already has a named revision.")
        named = revision.withName(name)
        if named.revisionId != revision.revisionId:
            if self._loadAnnotationStep(connection, revision.revisionId) is not None:
                self._storeAnnotationStep(connection, revision, named, isNameOnly=True)
            else:
                self._storeRevision(connection, named, isNamed=True)
        connection.execute(
            "INSERT INTO named_records VALUES (?, ?, ?, ?)",
            (named.recordId, named.revisionId, name, nameKey),
        )
        return named

    @staticmethod
    def _loadRevision(connection: sqlite3.Connection, revisionId: str) -> SequenceRevision:
        row = connection.execute(
            "SELECT payload FROM revisions WHERE revision_id = ?", (revisionId,),
        ).fetchone()
        if row is None:
            raise KeyError(revisionId)
        revision = decodeRevision(row["payload"])
        if revision.revisionId != revisionId:
            raise ProjectError(f"Stored snapshot does not match requested revision ID {revisionId!r}.")
        return revision

    def getRevision(self, revisionId: str) -> SequenceRevision:
        """Return the immutable snapshot with this revision ID."""
        with self._read() as connection:
            return self._loadRevision(connection, revisionId)

    def getNamedRevision(self, name: str) -> SequenceRevision:
        """Return the saved snapshot by its trimmed, case-insensitive name."""
        with self._read() as connection:
            row = connection.execute(
                "SELECT revision_id FROM named_records WHERE name_key = ?", (name.strip().casefold(),),
            ).fetchone()
            if row is None:
                raise KeyError(name)
            return self._loadRevision(connection, row["revision_id"])

    def getSavedRevision(self, recordId: str) -> SequenceRevision | None:
        """Return the named snapshot for a record ID if the record has been saved."""
        with self._read() as connection:
            row = connection.execute(
                "SELECT revision_id FROM named_records WHERE record_id = ?", (recordId,),
            ).fetchone()
            return self._loadRevision(connection, row["revision_id"]) if row is not None else None

    def getOriginalName(self, recordId: str) -> str | None:
        """Return the name on the first stored snapshot, before any named save."""
        with self._read() as connection:
            revision = self._firstRecordRevision(connection, recordId)
            return revision.name if revision is not None else None

    def listSavedNames(self) -> tuple[str, ...]:
        """Return the committed construct names in case-insensitive order."""
        with self._read() as connection:
            return tuple(row[0] for row in connection.execute(
                "SELECT name FROM named_records ORDER BY name_key",
            ))

    def namedRevisionNames(self) -> dict[str, str]:
        """List exact named revision IDs and their saved labels in case-insensitive name order."""
        with self._read() as connection:
            return dict(connection.execute(
                "SELECT revision_id, name FROM named_records ORDER BY name_key",
            ))

    def listReopenableRevisions(self) -> tuple[ReopenableRevision, ...]:
        """List named and exact intake snapshots once each, with display metadata."""
        with self._read() as connection:
            preference = "NULL" if _projectHeader(connection) == 1 else "records.preferred_name"
            rows = connection.execute(f"""
                SELECT r.revision_id, r.record_id,
                    COALESCE(n.name, json_extract(r.payload, '$.name')) AS name,
                    json_extract(original.payload, CASE a.format WHEN 'fasta' THEN '$.description'
                                                                  ELSE '$.name' END) AS original_name,
                    {preference} AS preferred_name, a.filename, r.source_ordinal,
                    n.revision_id IS NOT NULL AS is_named
                FROM revisions AS r
                JOIN records ON records.record_id = r.record_id
                JOIN revisions AS original ON original.rowid = (
                    SELECT MIN(rowid) FROM revisions WHERE record_id = r.record_id
                )
                LEFT JOIN named_records AS n ON n.revision_id = r.revision_id
                LEFT JOIN intake_revisions AS i ON i.revision_id = r.revision_id
                LEFT JOIN intakes ON intakes.artifact_id = i.artifact_id
                LEFT JOIN artifacts AS a ON a.artifact_id = r.source_artifact_id
                WHERE n.revision_id IS NOT NULL OR i.revision_id IS NOT NULL
                ORDER BY is_named DESC, n.name_key, intakes.rowid, i.source_ordinal
            """)
            entries = []
            for row in rows:
                if not isinstance(row["name"], str) or not isinstance(row["original_name"], str):
                    raise ProjectError(f"Invalid names for stored revision {row['revision_id']!r}.")
                entries.append(ReopenableRevision(
                    row["revision_id"], row["record_id"], row["name"], row["original_name"],
                    row["preferred_name"], row["filename"], row["source_ordinal"],
                    bool(row["is_named"]),
                ))
            return tuple(entries)

    def getPreferredName(self, recordId: str) -> str | None:
        """Return the optional display name associated with a stable record ID."""
        with self._read() as connection:
            if _projectHeader(connection) == 1:
                return None
            row = connection.execute(
                "SELECT preferred_name FROM records WHERE record_id = ?", (recordId,),
            ).fetchone()
            return row["preferred_name"] if row is not None else None

    def setPreferredName(
        self, recordId: str, preferredName: str | None, *, expectedName: str | None = None,
    ) -> None:
        """Set or clear the label. An expected name (empty for unset) guards an interactive edit."""
        preferredName = _normalizePreferredName(preferredName)
        with self._write() as connection:
            if expectedName is not None:
                row = connection.execute(
                    "SELECT preferred_name FROM records WHERE record_id = ?", (recordId,),
                ).fetchone()
                if row is None:
                    raise KeyError(recordId)
                if row[0] != _normalizePreferredName(expectedName):
                    current = repr(row[0]) if row[0] is not None else "unset"
                    raise RecordPreferenceConflict(
                        f"The preferred name changed in another window. It is now {current}. "
                        "Reopen the name editor to change it."
                    )
            self._storePreferredName(connection, recordId, preferredName)

    def clearPreferredName(self, recordId: str) -> None:
        """Clear the record's display preference."""
        self.setPreferredName(recordId, None)

    def getGeneticCodePreference(self, recordId: str) -> GeneticCodePreference:
        """Read the record's preview settings, defaulting to annotated codes."""
        with self._read() as connection:
            if _projectHeader(connection) < 7:
                return DEFAULT_GENETIC_CODE
            return self._readGeneticCodePreference(connection, recordId)

    @staticmethod
    def _readGeneticCodePreference(connection: sqlite3.Connection, recordId: str) -> GeneticCodePreference:
        row = connection.execute(
            "SELECT table_id, override_annotated FROM record_translation_preferences "
            "WHERE record_id = ?", (recordId,),
        ).fetchone()
        return GeneticCodePreference(row[0], bool(row[1])) if row else DEFAULT_GENETIC_CODE

    def setGeneticCodePreference(
        self, recordId: str, preference: GeneticCodePreference, *, expected: GeneticCodePreference | None = None,
    ) -> None:
        """Save the preview settings, comparing the previous selection in the same transaction."""
        self._checkGeneticCodePreference(preference)
        with self._write() as connection:
            if expected is not None and self._readGeneticCodePreference(connection, recordId) != expected:
                raise RecordPreferenceConflict(
                    "The genetic code changed in another window. Review the updated selection before editing it."
                )
            self._storeGeneticCodePreference(connection, recordId, preference)

    def setGeneticCodePreferences(self, preferences: Mapping[str, GeneticCodePreference]) -> None:
        """Apply all supplied preferences together to existing record IDs."""
        preferences = dict(preferences)
        for preference in preferences.values():
            self._checkGeneticCodePreference(preference)
        with self._write() as connection:
            for recordId, preference in preferences.items():
                self._storeGeneticCodePreference(connection, recordId, preference)

    @staticmethod
    def _checkGeneticCodePreference(preference: GeneticCodePreference) -> None:
        if not isinstance(preference, GeneticCodePreference):
            raise ProjectError("Expected a genetic code preference.")
        if preference.tableId is not None and preference.tableId > (1 << 63) - 1:
            raise ProjectError("The genetic code ID is too large to store.")

    @classmethod
    def _checkIntakeGeneticCodePreferences(
        cls, capture: SequenceImportResult | None,
        preferences: Mapping[int, GeneticCodePreference] | None,
    ) -> dict[int, GeneticCodePreference]:
        checked = dict(preferences or {})
        for ordinal, preference in checked.items():
            if capture is None or type(ordinal) is not int or not 1 <= ordinal <= len(capture.records):
                raise ProjectError("A genetic code preference does not identify a captured record.")
            cls._checkGeneticCodePreference(preference)
        return checked

    @staticmethod
    def _encodeIntakePreferences(preferences: Mapping[int, GeneticCodePreference]) -> list[tuple]:
        return [
            (ordinal, preference.tableId, preference.overrideAnnotated)
            for ordinal, preference in sorted(preferences.items())
        ]

    @classmethod
    def _storeIntakeGeneticCodePreferences(
        cls, connection: sqlite3.Connection, capture: SequenceImportResult | None,
        preferences: Mapping[int, GeneticCodePreference],
    ) -> None:
        if capture is not None:
            for ordinal, preference in preferences.items():
                cls._storeGeneticCodePreference(connection, capture.records[ordinal - 1].recordId, preference)

    @staticmethod
    def _storeGeneticCodePreference(
        connection: sqlite3.Connection, recordId: str, preference: GeneticCodePreference,
    ) -> None:
        if connection.execute("SELECT 1 FROM records WHERE record_id = ?", (recordId,)).fetchone() is None:
            raise KeyError(recordId)
        if preference.tableId is None:
            connection.execute(
                "DELETE FROM record_translation_preferences WHERE record_id = ?", (recordId,),
            )
        else:
            connection.execute(
                "INSERT INTO record_translation_preferences (record_id, table_id, override_annotated) "
                "VALUES (?, ?, ?) ON CONFLICT (record_id) DO UPDATE SET "
                "table_id = excluded.table_id, override_annotated = excluded.override_annotated",
                (recordId, preference.tableId, preference.overrideAnnotated),
            )

    @staticmethod
    def _storePreferredName(
        connection: sqlite3.Connection, recordId: str, preferredName: str | None,
    ) -> None:
        updated = connection.execute(
            "UPDATE records SET preferred_name = ? WHERE record_id = ?", (preferredName, recordId),
        )
        if not updated.rowcount:
            raise KeyError(recordId)

    @staticmethod
    def _loadArtifact(connection: sqlite3.Connection, artifactId: str) -> SourceArtifact:
        row = connection.execute(
            "SELECT filename, sha256, byte_count FROM artifacts WHERE artifact_id = ?", (artifactId,),
        ).fetchone()
        if row is None:
            raise KeyError(artifactId)
        return SourceArtifact(artifactId, row["filename"], row["sha256"], row["byte_count"])

    def getArtifact(self, artifactId: str) -> SourceArtifact:
        """Return a verified capture descriptor or the descriptor from portable evidence."""
        with self._read() as connection:
            return self._loadArtifact(connection, artifactId)

    def getArtifactBytes(self, artifactId: str) -> bytes:
        """Read and verify exact managed bytes without opening an external path."""
        with self._read() as connection:
            artifact = self._loadArtifact(connection, artifactId)
            if not self._canReadArtifact(connection, artifact):
                raise ProjectError(f"Original bytes unavailable for artifact {artifactId!r}.")
            return _readArtifact(self.path, artifact)

    def retrieveOriginal(self, artifactId: str, folder: str | Path) -> Path:
        """Publish verified capture bytes with their original filename to an outside folder."""
        with self._read() as connection:
            artifact = self._loadArtifact(connection, artifactId)
            kind, isPaste = connection.execute(
                "SELECT a.kind, a.format = 'text' AND i.artifact_id IS NOT NULL "
                "AND i.source_origin IS NULL AND i.source_path IS NULL "
                "FROM artifacts AS a LEFT JOIN intakes AS i USING (artifact_id) "
                "WHERE a.artifact_id = ?", (artifactId,),
            ).fetchone()
            if kind != "original" or not self._canReadArtifact(connection, artifact):
                raise ProjectError("The registered original is unavailable.")
            raw = _readArtifact(self.path, artifact)
        name = Path(artifact.filename)
        if name.name != artifact.filename or name.is_reserved() or (
            os.name == "nt" and (":" in name.name or name.name.endswith((" ", ".")))
        ):
            raise ProjectError("The original filename is unsafe on this filesystem.")
        # Pasted text has a registered display label but never had a file extension.
        if isPaste and name.suffix.lower() != ".txt":
            name = name.with_name(name.name + ".txt")
        directory = Path(folder).resolve(strict=True)
        destination = directory / name
        if _insideProject(self.path, directory):
            raise ProjectError("Choose a destination outside the managed project.")

        identity = directory.stat()
        with TemporaryDirectory(dir=directory, prefix=".strandfolio-original-") as stage:
            stagedPath = Path(stage) / "capture"
            with stagedPath.open("xb") as handle:
                handle.write(raw)
                _syncFile(handle)
            _readArtifactFile(stagedPath, artifact)
            if not os.path.samestat(directory.stat(), identity) or _insideProject(self.path, directory):
                raise ProjectError("The destination folder changed. Choose the folder again.")
            try:
                os.link(stagedPath, destination)
            except FileExistsError as error:
                raise FileExistsError(
                    f"{destination.name} already exists. Choose a different folder."
                ) from error
        _syncDirectory(directory)
        return destination

    def hasOriginalBytes(self, artifactId: str) -> bool:
        """Report whether the project owns and can verify the captured bytes of an artifact."""
        with self._read() as connection:
            artifact = self._loadArtifact(connection, artifactId)
            if not self._canReadArtifact(connection, artifact):
                return False
            _readArtifact(self.path, artifact)
            return True

    def _canReadArtifact(self, connection: sqlite3.Connection, artifact: SourceArtifact) -> bool:
        if not self._hasPortableSource(connection, artifact.artifactId):
            return True
        return connection.execute(
            "SELECT 1 FROM artifacts a JOIN intakes i USING (artifact_id) "
            "WHERE a.sha256 = ? AND a.byte_count = ? LIMIT 1", (artifact.sha256, artifact.byteCount),
        ).fetchone() is not None

    @staticmethod
    def _hasPortableSource(connection: sqlite3.Connection, artifactId: str) -> bool:
        return _projectHeader(connection) >= 4 and connection.execute(
            "SELECT 1 FROM portable_sources WHERE artifact_id = ?", (artifactId,),
        ).fetchone() is not None

    def _loadIntake(self, connection: sqlite3.Connection, artifactId: str) -> SequenceImportResult:
        row = connection.execute(
            "SELECT i.*, a.format FROM intakes AS i JOIN artifacts AS a USING (artifact_id) "
            "WHERE i.artifact_id = ?", (artifactId,),
        ).fetchone()
        if row is None:
            raise KeyError(artifactId)
        records = tuple(decodeRevision(item[0]) for item in connection.execute(
            "SELECT r.payload FROM intake_revisions AS i JOIN revisions AS r USING (revision_id) "
            "WHERE i.artifact_id = ? ORDER BY i.source_ordinal", (artifactId,),
        ))
        source = self._loadArtifact(connection, artifactId)
        return SequenceImportResult(
            records=records, source=source,
            sourcePath=Path(row["source_path"]) if row["source_path"] is not None else None,
            warnings=tuple(json.loads(row["warnings"])), biopythonVersion=row["biopython_version"],
            formatName=cast(SequenceFormat, row["format"]), captureBytes=_readArtifact(self.path, source),
            sourceOrigin=Path(row["source_origin"]) if row["source_origin"] is not None else None,
            interpretation=row["interpretation"],
        )

    def getIntake(self, artifactId: str) -> SequenceImportResult:
        """Return the source capture metadata and exact ordered intake snapshots."""
        with self._read() as connection:
            return self._loadIntake(connection, artifactId)

    def listIntakes(self) -> tuple[SourceArtifact, ...]:
        """Return registered intake artifact descriptors in registration order."""
        with self._read() as connection:
            return tuple(SourceArtifact(*row) for row in connection.execute(
                "SELECT i.artifact_id, a.filename, a.sha256, a.byte_count "
                "FROM intakes AS i JOIN artifacts AS a USING (artifact_id) ORDER BY i.rowid",
            ))

    def backup(self, destination: str | Path) -> "ProjectRepository":
        """Create and verify a complete, independent project at the destination."""
        from strandfolio.storage.project_backup import backupProject

        return backupProject(self, destination)
