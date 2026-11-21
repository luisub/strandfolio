"""Record file exports in SQLite and recover interrupted deliveries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import TYPE_CHECKING
from uuid import uuid4

import Bio

from strandfolio.domain.provenance import SourceArtifact
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.formats import (
    MAX_FILE_BYTES,
    ExportDestinationChangedError,
    ExportFormat,
    SequenceExportResult,
    checkExportDestination,
    fileStatVersion,
    _exportDestination,
    _exportFormat,
    _stageSequenceExport,
    _StagedSequenceExport,
)

if TYPE_CHECKING:
    from strandfolio.storage.project_edits import AnnotationEvidence


EXPORT_SCHEMA = """
CREATE TABLE export_requests (
    request_id TEXT PRIMARY KEY NOT NULL REFERENCES requests(request_id),
    source_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
    destination_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    report TEXT NOT NULL,
    producer TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    witness_name TEXT,
    witness_device TEXT,
    witness_inode TEXT,
    witness_directory_inode TEXT,
    artifact_id TEXT UNIQUE REFERENCES artifacts(artifact_id),
    created INTEGER CHECK (created IN (0, 1)),
    CHECK ((artifact_id IS NULL) = (created IS NULL)),
    CHECK ((witness_name IS NULL) = (witness_device IS NULL)),
    CHECK ((witness_name IS NULL) = (witness_inode IS NULL)),
    CHECK ((witness_name IS NULL) = (witness_directory_inode IS NULL))
);
"""
_WITNESS_PREFIX = ".vibegene-publication-"
_PRODUCER = "strandfolio/Biopython"


class ExportPendingError(ValueError):
    """A confirmed export still needs a matching verified delivery."""


class ExportIntentMissingError(ValueError):
    """Raised when the export request has no committed intent."""


@dataclass(frozen=True, slots=True)
class ExportDelivery:
    """Describe one verified delivery and its immutable managed copy.

    created means evidence shows this request installed the emitted file, including
    replacement of a prior file. False means the request verified matching bytes
    at the destination and has no evidence that it installed them.
    """

    requestId: str
    artifactId: str
    sourceRevisionId: str
    destinationPath: Path
    sha256: str
    byteCount: int
    created: bool
    formatName: ExportFormat
    emittedName: str | None
    warnings: tuple[str, ...]
    serializationDetails: tuple[str, ...]
    producer: str
    producerVersion: str


@dataclass(frozen=True, slots=True)
class _ObservedTarget:
    info: os.stat_result
    raw: bytes | None


@dataclass(frozen=True, slots=True)
class _PublicationWitness:
    directory: Path
    device: str
    inode: str
    directoryInode: str

    def matches(self, info: os.stat_result) -> bool:
        return stat.S_ISREG(info.st_mode) and self.device == str(info.st_dev) and (
            self.inode == str(info.st_ino)
        )

    @contextmanager
    def openDirectory(self) -> Iterator[int]:
        if self.directory.parent.resolve() != self.directory.parent:
            raise OSError("Publication witness parent changed.")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.directory, flags)
        try:
            info = os.fstat(descriptor)
            if (str(info.st_dev), str(info.st_ino)) != (self.device, self.directoryInode):
                raise OSError("Publication witness directory changed.")
            yield descriptor
        finally:
            os.close(descriptor)


def _observeTarget(path: Path, byteCount: int, *, sync: bool = False) -> _ObservedTarget | None:
    from strandfolio.storage.projects import _syncDescriptor

    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ExportPendingError(f"Export destination is not a regular file: {path}")
    if info.st_size != byteCount:
        return _ObservedTarget(info, None)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as source:
        if fileStatVersion(os.fstat(source.fileno())) != fileStatVersion(info):
            raise ExportPendingError(f"Export destination changed during verification: {path}")
        raw = source.read(byteCount + 1)
        if sync:
            _syncDescriptor(source.fileno())
        if fileStatVersion(path.lstat()) != fileStatVersion(info) or len(raw) != byteCount:
            raise ExportPendingError(f"Export destination changed during verification: {path}")
    return _ObservedTarget(info, raw)


def _publicationWitness(row: sqlite3.Row) -> _PublicationWitness | None:
    name = row["witness_name"]
    if name is None:
        return None
    token = name.removeprefix(_WITNESS_PREFIX)
    if (name == token or len(token) != 32
            or any(letter not in "0123456789abcdef" for letter in token)):
        raise ValueError("Invalid export publication witness directory.")
    return _PublicationWitness(
        Path(row["destination_path"]).parent / name, row["witness_device"],
        row["witness_inode"], row["witness_directory_inode"],
    )


def _newWitness(staged: _StagedSequenceExport) -> _PublicationWitness:
    from strandfolio.storage.projects import _syncDescriptor, _syncDirectory

    # The retained hard link keeps this inode from being reused after a delivery
    # is removed. Store the enclosing directory's identity to protect cleanup.
    directory = staged.report.destinationPath.parent / (_WITNESS_PREFIX + uuid4().hex)
    stageInfo = staged.path.lstat()
    with staged.path.open("rb") as handle:
        _syncDescriptor(handle.fileno())
    directory.mkdir(mode=0o700)
    witness = _PublicationWitness(
        directory, str(stageInfo.st_dev), str(stageInfo.st_ino), str(directory.lstat().st_ino),
    )
    try:
        with witness.openDirectory() as descriptor:
            os.link(staged.path, "payload", dst_dir_fd=descriptor, follow_symlinks=False)
            _syncDescriptor(descriptor)
        _syncDirectory(directory.parent)
    except BaseException:
        _removeWitness(witness)
        raise
    return witness


def _observedPublication(witness: _PublicationWitness | None, info: os.stat_result) -> bool:
    if witness is None or not witness.matches(info):
        return False
    try:
        with witness.openDirectory() as descriptor:
            return witness.matches(os.stat("payload", dir_fd=descriptor, follow_symlinks=False))
    except (OSError, RuntimeError):
        return False


def _removeWitness(witness: _PublicationWitness | None) -> None:
    if witness is None:
        return
    try:
        with witness.openDirectory() as descriptor:
            try:
                evidence = os.stat("payload", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not witness.matches(evidence):
                    return
                os.unlink("payload", dir_fd=descriptor)
            if os.path.samestat(os.fstat(descriptor), witness.directory.lstat()):
                witness.directory.rmdir()
    except (OSError, RuntimeError):
        # The completed delivery stays authoritative if advisory evidence was moved
        # or its private directory is no longer writable.
        return


def _exportRow(connection: sqlite3.Connection, requestId: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM export_requests WHERE request_id = ?", (requestId,),
    ).fetchone()
    if row is None:
        raise ExportIntentMissingError(f"Unknown export request: {requestId}")
    return row


def _delivery(row: sqlite3.Row) -> ExportDelivery:
    report = _readReport(row)
    return ExportDelivery(
        row["request_id"], row["artifact_id"], row["source_revision_id"],
        report.destinationPath, row["sha256"], row["byte_count"], bool(row["created"]),
        report.formatName, report.emittedName, report.warnings, report.serializationDetails,
        row["producer"], row["producer_version"],
    )


def _readReport(row: sqlite3.Row) -> SequenceExportResult:
    report = json.loads(row["report"])
    destination = Path(row["destination_path"])
    if (
        not destination.is_absolute() or "\x00" in row["destination_path"]
        or type(report) is not dict
        or set(report) != {"format", "emitted_name", "warnings", "serialization_details"}
    ):
        raise ValueError("Invalid export report fields or destination path.")
    formatName = _exportFormat(destination, report["format"])
    emittedName = report["emitted_name"]
    messages = report["warnings"], report["serialization_details"]
    if (
        report["format"] != formatName
        or formatName == "text" and emittedName is not None
        or formatName != "text" and (type(emittedName) is not str or not emittedName.strip())
        or any(type(items) is not list or any(type(item) is not str for item in items)
               for items in messages)
    ):
        raise ValueError("Invalid export report format, emitted name or fidelity messages.")
    warnings, details = messages
    return SequenceExportResult(destination, formatName, emittedName, tuple(warnings), tuple(details))


def verifyExportRows(connection: sqlite3.Connection) -> None:
    """Check typed export intents and receipts without consulting advisory paths."""
    orphan = connection.execute(
        "SELECT request_id FROM requests WHERE kind = 'export' AND NOT EXISTS "
        "(SELECT 1 FROM export_requests WHERE export_requests.request_id = requests.request_id)",
    ).fetchone()
    if orphan is not None:
        raise ValueError(f"Missing export intent for request {orphan[0]!r}.")
    for row in connection.execute(
        "SELECT e.*, q.kind AS request_kind, q.payload AS request_payload, q.result_id, "
        "a.kind AS artifact_kind, a.filename, a.format, a.sha256 AS artifact_sha256, "
        "a.byte_count AS artifact_byte_count FROM export_requests e "
        "JOIN requests q ON q.request_id = e.request_id "
        "LEFT JOIN artifacts a ON a.artifact_id = e.artifact_id",
    ):
        try:
            report = _readReport(row)
            payload = json.loads(row["request_payload"])
            witness = _publicationWitness(row)
            if (
                row["request_kind"] != "export" or row["result_id"] != row["request_id"]
                or type(payload) is not list or len(payload) != 4
                or payload[:3] != [row["source_revision_id"], row["destination_path"],
                                   report.formatName]
                or payload[3] is not None and (
                    report.formatName != "fasta" or report.emittedName != payload[3]
                )
                or not re.fullmatch("[a-f0-9]{64}", row["sha256"])
                or type(row["byte_count"]) is not int
                or not 1 <= row["byte_count"] <= MAX_FILE_BYTES
                or row["producer"] not in (_PRODUCER, "vibeGene/Biopython")
                or not row["producer_version"].strip()
                or "\x00" in row["producer_version"]
                or witness is not None and any(not re.fullmatch("[0-9]+", field) for field in (
                    witness.device, witness.inode, witness.directoryInode,
                ))
                or row["created"] == 1 and witness is None
            ):
                raise ValueError("Invalid export request payload, producer or evidence.")
            if row["artifact_id"] is not None and (
                row["artifact_kind"], row["filename"], row["format"], row["artifact_sha256"],
                row["artifact_byte_count"],
            ) != ("export", report.destinationPath.name, report.formatName, row["sha256"],
                  row["byte_count"]):
                raise ValueError("Export receipt conflicts with its managed artifact.")
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid export request {row['request_id']!r}: {error}") from error


class ProjectExportMixin:
    """Stage, verify, and recover sequence exports in a project."""

    def exportSequence(
        self,
        revision: SequenceRevision,
        path: str | Path,
        *,
        requestId: str,
        formatName: ExportFormat | None = None,
        header: str | None = None,
        confirm: Callable[[SequenceExportResult], bool] | None = None,
        protectedPaths: Iterable[str | Path] = (),
        annotationEvidence: AnnotationEvidence | None = None,
    ) -> ExportDelivery | None:
        """Confirm and deliver one verified exchange file, with its exact source link.

        An existing request with matching inputs only retries registration. It
        never writes to the destination or requests confirmation again. Declining
        confirmation on a new request returns None without registering it.
        """
        from strandfolio.storage.projects import _checkedEncoding

        self._validateRequestId(requestId)
        _checkedEncoding(revision)
        if annotationEvidence is not None:
            self._validateAnnotationEvidence(annotationEvidence, revision)
        destination = _exportDestination(path)
        selectedFormat = _exportFormat(destination, formatName)
        payload = json.dumps([revision.revisionId, str(destination), selectedFormat, header])
        with self._read() as connection:
            exists = self._checkRequest(connection, requestId, "export", payload) is not None
            if not exists:
                self._protectExportDestination(connection, destination)
        if exists:
            with self._write() as connection:
                if annotationEvidence is not None:
                    self._storeAnnotationEvidence(connection, annotationEvidence, requestId)
                self._storeRevision(connection, revision)
            return self.retryExport(requestId)

        with _stageSequenceExport(
            revision, destination, formatName=selectedFormat, header=header, confirm=confirm,
            protectedPaths=protectedPaths,
        ) as staged:
            if staged is None:
                return None
            witness = None
            try:
                with self._write() as connection:
                    exists = self._checkRequest(connection, requestId, "export", payload) is not None
                    if annotationEvidence is not None:
                        self._storeAnnotationEvidence(connection, annotationEvidence, requestId)
                    self._storeRevision(connection, revision)
                    if not exists:
                        self._protectExportDestination(connection, destination)
                        previous = _observeTarget(destination, len(staged.raw))
                        version = fileStatVersion(previous.info) if previous is not None else None
                        if version != staged.destinationVersion:
                            raise ExportDestinationChangedError(destination)
                        if previous is None or previous.raw != staged.raw:
                            witness = _newWitness(staged)
                        self._recordExportIntent(
                            connection, requestId, payload, revision.revisionId, staged, witness,
                        )
            except BaseException:
                if witness is not None:
                    self._discardUncommittedWitness(requestId, witness)
                raise
            if exists:
                return self.retryExport(requestId)
            with self._write() as connection:
                row = _exportRow(connection, requestId)
                if row["artifact_id"] is None:
                    self._publishExport(connection, row, staged, previous)
                    row = self._completeExport(connection, row)
        _removeWitness(_publicationWitness(row))
        return _delivery(row)

    def retryExport(self, requestId: str) -> ExportDelivery:
        """Register a pending delivery by verifying the current target, without writing to it.

        Raise ExportPendingError if the published bytes are missing or different.
        A completed request returns its stored receipt independently of the old path.
        """
        self._validateRequestId(requestId)
        with self._write() as connection:
            row = _exportRow(connection, requestId)
            if row["artifact_id"] is None:
                row = self._completeExport(connection, row)
        _removeWitness(_publicationWitness(row))
        return _delivery(row)

    def getExportDelivery(self, requestId: str) -> ExportDelivery | None:
        """Return the committed receipt, or None when no delivery is recorded."""
        self._validateRequestId(requestId)
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM export_requests WHERE request_id = ? AND artifact_id IS NOT NULL",
                (requestId,),
            ).fetchone()
            return _delivery(row) if row is not None else None

    def hasExportIntent(self, requestId: str) -> bool:
        """Report whether the export request has a committed intent or receipt."""
        self._validateRequestId(requestId)
        with self._read() as connection:
            return connection.execute(
                "SELECT 1 FROM export_requests WHERE request_id = ?", (requestId,),
            ).fetchone() is not None

    def checkFigureDestination(self, path: Path) -> None:
        """Reject a figure destination inside the project or aliasing a protected file."""
        with self._read() as connection:
            self._protectExportDestination(connection, path.parent.resolve() / path.name)

    def _protectExportDestination(self, connection: sqlite3.Connection, destination: Path) -> None:
        from strandfolio.storage.projects import DATABASE_FILENAME, _insideProject

        if destination.parent.resolve() != destination.parent:
            raise ExportPendingError(f"Export destination directory changed: {destination.parent}")
        if _insideProject(self.path, destination):
            raise ValueError("Export destination is inside the managed project.")
        sources = tuple(
            Path(path) for row in connection.execute("SELECT source_origin, source_path FROM intakes")
            for path in row if path is not None
        )
        managedFiles = tuple(self.path / (DATABASE_FILENAME + suffix)
                             for suffix in ("", "-wal", "-shm", "-journal")) + tuple(
            self.path / "artifacts" / digest for (digest,) in connection.execute(
                "SELECT DISTINCT sha256 FROM artifacts",
            )
        )
        checkExportDestination(destination, sources + managedFiles)

    def _recordExportIntent(
        self, connection: sqlite3.Connection, requestId: str, payload: str,
        revisionId: str, staged: _StagedSequenceExport, witness: _PublicationWitness | None,
    ) -> None:
        report = staged.report
        reportedValues = json.dumps({
            "format": report.formatName, "emitted_name": report.emittedName,
            "warnings": report.warnings, "serialization_details": report.serializationDetails,
        })
        self._recordRequest(connection, requestId, "export", payload, requestId)
        connection.execute(
            "INSERT INTO export_requests (request_id, source_revision_id, destination_path, sha256, "
            "byte_count, report, producer, producer_version, witness_name, witness_device, "
            "witness_inode, witness_directory_inode) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (requestId, revisionId, str(report.destinationPath), sha256(staged.raw).hexdigest(),
             len(staged.raw), reportedValues, _PRODUCER, Bio.__version__) + (
                (witness.directory.name, witness.device, witness.inode, witness.directoryInode)
                if witness is not None else (None, None, None, None)
             ),
        )

    def _discardUncommittedWitness(self, requestId: str, witness: _PublicationWitness) -> None:
        try:
            with self._read() as connection:
                row = connection.execute(
                    "SELECT witness_name FROM export_requests WHERE request_id = ?", (requestId,),
                ).fetchone()
                if row is None or row["witness_name"] != witness.directory.name:
                    _removeWitness(witness)
        except (OSError, sqlite3.Error):
            # A failed commit can be ambiguous. Retain the witness if we cannot
            # verify whether the request committed.
            return

    def _publishExport(
        self, connection: sqlite3.Connection, row: sqlite3.Row, staged: _StagedSequenceExport,
        previous: _ObservedTarget | None,
    ) -> None:
        from strandfolio.storage.projects import _syncDirectory

        destination = staged.report.destinationPath
        self._protectExportDestination(connection, destination)
        checkExportDestination(destination, staged.protectedPaths)
        current = _observeTarget(destination, len(staged.raw))
        if current is not None and current.raw == staged.raw:
            return
        witness = _publicationWitness(row)
        oldSignature = fileStatVersion(previous.info) if previous is not None else None
        currentSignature = fileStatVersion(current.info) if current is not None else None
        if oldSignature != currentSignature or witness is None:
            raise ExportPendingError(f"Destination changed before publication: {destination}")
        prepared = _observeTarget(staged.path, len(staged.raw))
        if (prepared is None or prepared.raw != staged.raw
                or not _observedPublication(witness, prepared.info)):
            raise ExportPendingError(f"Prepared export bytes could not be verified: {destination}")
        if previous is None:
            try:
                os.link(staged.path, destination)
            except FileExistsError:
                # Registration can still succeed when an independent writer supplied
                # the requested bytes during the race.
                return
        else:
            checkExportDestination(destination, staged.protectedPaths)
            if fileStatVersion(destination.lstat()) != fileStatVersion(previous.info):
                raise ExportPendingError(f"Destination changed before publication: {destination}")
            os.replace(staged.path, destination)
        _syncDirectory(destination.parent)

    def _completeExport(self, connection: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
        from strandfolio.storage.projects import _syncDirectory

        report = _readReport(row)
        destination = report.destinationPath
        try:
            if destination.parent.resolve() != destination.parent:
                raise ExportPendingError(f"Export destination directory changed: {destination.parent}")
            observed = _observeTarget(destination, row["byte_count"], sync=True)
        except (OSError, RuntimeError) as error:
            raise ExportPendingError(f"Could not verify export destination: {destination}") from error
        if observed is None:
            raise ExportPendingError(f"Export destination is missing: {destination}")
        raw = observed.raw
        if raw is None or sha256(raw).hexdigest() != row["sha256"]:
            raise ExportPendingError(f"Export destination has different bytes: {destination}")
        created = _observedPublication(_publicationWitness(row), observed.info)
        _syncDirectory(destination.parent)
        artifact = SourceArtifact.fromCapture(destination.name, raw)
        self._storeArtifact(
            connection, artifact, raw, report.formatName, "export", row["request_id"],
        )
        connection.execute(
            "UPDATE export_requests SET artifact_id = ?, created = ? WHERE request_id = ?",
            (artifact.artifactId, int(created), row["request_id"]),
        )
        return _exportRow(connection, row["request_id"])
