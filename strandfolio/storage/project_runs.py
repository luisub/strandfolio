"""Save restriction runs and reopen their recorded inputs and outputs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import sqlite3
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from strandfolio.domain.provenance import OutputReference
from strandfolio.storage.revision_codec import matchesRecordedProduct, renamedRevisionPayload

if TYPE_CHECKING:
    from strandfolio.storage.project_edits import AnnotationEvidence
    from strandfolio.domain.fragments import DNAFragment
    from strandfolio.domain.sequence import SequenceRevision
    from strandfolio.io.digestion import SavedDigestion
    from strandfolio.io.formats import SequenceImportResult
    from strandfolio.operations.gel import GelViewSettings
    from strandfolio.operations.pcr import PcrRun
    from strandfolio.storage.project_pcr import CertifiedPcrRun
    from strandfolio.operations.restriction import RestrictionDigest


RESTRICTION_SCHEMA = (
    """CREATE TABLE restriction_runs (
        operation_id TEXT PRIMARY KEY NOT NULL,
        template_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
        payload TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    )""",
    """CREATE INDEX restriction_runs_by_input ON restriction_runs (template_revision_id)""",
    """CREATE TABLE restriction_outputs (
        operation_id TEXT NOT NULL REFERENCES restriction_runs(operation_id),
        output_key TEXT NOT NULL,
        revision_id TEXT UNIQUE NOT NULL REFERENCES revisions(revision_id),
        output_ordinal INTEGER NOT NULL CHECK (output_ordinal > 0),
        PRIMARY KEY (operation_id, output_key),
        UNIQUE (operation_id, output_ordinal)
    )""",
)


def operationOutputOwners(connection: sqlite3.Connection) -> dict[str, OutputReference]:
    """Resolve each product record to its single generating operation and output key."""
    from strandfolio.storage.projects import ProjectError, _projectHeader

    version = _projectHeader(connection)
    if version < 3:
        return {}
    owners = {}
    tables = ("restriction_outputs", "pcr_outputs") if version >= 5 else ("restriction_outputs",)
    for table in tables:
        for recordId, operationId, key in connection.execute(
            f"SELECT r.record_id, o.operation_id, o.output_key FROM {table} o "
            "JOIN revisions r USING (revision_id)",
        ):
            if recordId in owners:
                raise ProjectError(f"Multiple operation outputs claim record {recordId!r}.")
            owners[recordId] = OutputReference(operationId, key)
    if version >= 4:
        # Schema v4 named this ledger for restriction, its only operation type at the time.
        for recordId, operationId, key in connection.execute(
            "SELECT record_id, operation_id, output_key FROM restriction_origins",
        ):
            reference = OutputReference(operationId, key)
            if recordId in owners and owners[recordId] != reference:
                raise ProjectError(f"Conflicting output owners for record {recordId!r}.")
            owners[recordId] = reference
    return owners


def _verifyNewRestrictionRun(result: RestrictionDigest) -> None:
    from strandfolio.operations.restriction import rebuildRestrictionRun
    from strandfolio.storage.revision_codec import encodeRevision

    expected = rebuildRestrictionRun(result)
    if (not set(expected.scan.warnings).issubset(result.scan.warnings)
            or not set(expected.warnings).issubset(result.warnings)):
        raise ValueError("The restriction run omits model diagnostics.")
    for fragment, projected in zip(result.fragments, expected.fragments, strict=True):
        key = fragment.outputKey
        if fragment.featureMappings != projected.featureMappings:
            raise ValueError(f"Restriction product {key!r} has inconsistent feature mappings.")
        if (fragment.sourceSpans != projected.sourceSpans
                or encodeRevision(fragment.revision) != encodeRevision(projected.revision)):
            raise ValueError(f"Restriction product {key!r} differs from the modeled product.")
        if not set(projected.warnings).issubset(fragment.warnings):
            raise ValueError(f"Restriction product {key!r} omits model diagnostics.")


@dataclass(frozen=True, slots=True, init=False)
class CertifiedRestrictionRun:
    """A restriction result validated against the current engine."""

    result: RestrictionDigest

    def __init__(self, result: RestrictionDigest) -> None:
        _verifyNewRestrictionRun(result)
        object.__setattr__(self, "result", result)


def certifyRestrictionRun(result: RestrictionDigest) -> CertifiedRestrictionRun:
    """Check current-engine predictions before accepting a worker's new digest."""
    return CertifiedRestrictionRun(result)


def _verifyAncestry(parents: dict[str, str]) -> None:
    from strandfolio.storage.projects import ProjectError

    known = set()
    for operationId in parents:
        ancestry = set()
        while operationId in parents and operationId not in known:
            if operationId in ancestry:
                raise ProjectError(f"Circular operation ancestry at operation {operationId!r}.")
            ancestry.add(operationId)
            operationId = parents[operationId]
        known.update(ancestry)


class ProjectRunMixin:
    """Provide the restriction-run operations used by ProjectRepository."""

    def registerRestrictionRun(
        self, result: RestrictionDigest, *, capture: SequenceImportResult | None = None,
        independent: bool = False, view: GelViewSettings | None = None,
        certification: CertifiedRestrictionRun | None = None,
        ancestors: tuple[PcrRun | SavedDigestion, ...] = (),
        ancestorCapture: SequenceImportResult | None = None, ancestorIndependent: bool = False,
        ancestorCertifications: tuple[CertifiedPcrRun, ...] = (),
        ancestorViews: tuple[tuple[str, GelViewSettings], ...] = (),
        annotationEvidence: AnnotationEvidence | None = None,
        ancestorAnnotations: tuple[AnnotationEvidence, ...] = (),
        timeout: float = 30.0,
    ) -> RestrictionDigest:
        """Retain a run and all its snapshots in one write, without naming products.

        First registration checks occurrences and products using captured inputs.
        A matching operation ID is idempotent. A source without a managed artifact
        can be stored directly. Supply an exact capture for a new file intake.
        Only an explicitly independent intake can keep new identities for an
        already known source.
        """
        from strandfolio.storage.projects import ProjectError, _checkedEncoding, _key, _validateCapture
        from strandfolio.storage.restriction_codec import decodeRestrictionRun, encodeRestrictionRun

        try:
            payload = encodeRestrictionRun(result)
            outputs = tuple((item.outputKey, item.revision) for item in result.fragments)
            decodeRestrictionRun(payload, result.template, outputs)
        except (ValueError, TypeError, AttributeError) as error:
            raise ProjectError(f"Invalid restriction run: {error}") from error
        if certification is not None and (
            type(certification) is not CertifiedRestrictionRun or certification.result is not result
        ):
            raise ProjectError("Restriction certification belongs to another result.")
        prepared = ()
        if ancestors or ancestorCapture is not None or ancestorIndependent or ancestorViews:
            prepared = self._prepareAnalysisRuns(
                ancestors, ancestorCapture, ancestorIndependent, terminalTemplate=result.template,
                annotations=ancestorAnnotations,
            )

        if type(independent) is not bool:
            raise TypeError("independent must be a boolean.")
        if independent and capture is None:
            raise ProjectError("An independent restriction intake requires its source capture.")
        if capture is not None:
            _validateCapture(capture)
            ordinal = result.template.sourceOrdinal
            if (type(ordinal) is not int or not 1 <= ordinal <= len(capture.records)
                    or _checkedEncoding(capture.records[ordinal - 1]) != _checkedEncoding(result.template)):
                raise ProjectError("The restriction input differs from its supplied source capture.")

        operationId = result.operationId
        if annotationEvidence is not None:
            self._validateAnnotationEvidence(annotationEvidence, result.template)
        with self._write(timeout=timeout) as connection:
            newAncestorCapture = self._storeAnalysisRuns(
                connection, prepared, ancestorCapture, ancestorIndependent,
                certifications=ancestorCertifications, views=ancestorViews,
                annotations=ancestorAnnotations,
            ) if prepared or ancestorViews else None
            newCapture = False
            if capture is not None:
                capture, newCapture = self._registerCapture(
                    connection, capture, independent=independent, preserveCaptureIdentity=True,
                )
                if capture.records[ordinal - 1].revisionId != result.template.revisionId:
                    raise ProjectError("The restriction input differs from the registered intake.")
            if annotationEvidence is not None:
                self._storeAnnotationEvidence(
                    connection, annotationEvidence, f"restriction-edit-{_key(operationId)}",
                )
            self._storeRevision(connection, result.template)
            self._verifyRunInput(connection, result.template)
            retained = self._storeRestrictionRun(
                connection, result, payload, certify=certification is None,
            )
            if view is not None:
                self._storeRestrictionView(connection, retained, view)
            if newCapture:
                stagingId = f"restriction-{_key(operationId)}"
                self._preserveArtifact(capture.source, capture.captureBytes, stagingId)
            if newAncestorCapture is not None:
                stagingId = f"restriction-ancestor-{_key(operationId)}"
                self._preserveArtifact(newAncestorCapture.source, newAncestorCapture.captureBytes, stagingId)
            return retained

    def _storeRestrictionRun(
        self, connection: sqlite3.Connection, result: RestrictionDigest, payload: str,
        *, certify: bool,
    ) -> RestrictionDigest:
        from strandfolio.storage.projects import ProjectError

        operationId = result.operationId
        if self._operationKind(connection, operationId) == "pcr":
            raise ProjectError(f"Conflicting operation ID {operationId!r}.")
        row = connection.execute(
            "SELECT payload FROM restriction_runs WHERE operation_id = ?", (operationId,),
        ).fetchone()
        if row is not None and row["payload"] != payload:
            raise ProjectError(f"Conflicting restriction operation ID {operationId!r}.")
        closesGap = False
        if row is None:
            closesGap = self._hasAncestryGap(connection, operationId)
            if certify:
                try:
                    _verifyNewRestrictionRun(result)
                except ValueError as error:
                    raise ProjectError(
                        f"Cannot certify a new run from restriction engine {result.engineVersion!r}. "
                        f"{error}"
                    ) from error
            connection.execute(
                "INSERT INTO restriction_runs VALUES (?, ?, ?, ?)",
                (operationId, result.template.revisionId, payload,
                 sha256(payload.encode("ascii")).hexdigest()),
            )
        for ordinal, fragment in enumerate(result.fragments, start=1):
            revision = fragment.revision
            if row is None:
                self._storeOutputCandidate(connection, revision)
                connection.execute(
                    "INSERT INTO restriction_outputs VALUES (?, ?, ?, ?)",
                    (operationId, fragment.outputKey, revision.revisionId, ordinal),
                )
                connection.execute(
                    "DELETE FROM restriction_origins WHERE record_id = ?", (revision.recordId,),
                )
            else:
                self._storeRevision(connection, revision, isOperationOutput=True)
        retained = self._loadRestrictionRun(connection, operationId)
        if closesGap:
            self._closeAncestryGap(connection, operationId)
        return retained

    def _storeOutputCandidate(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
    ) -> None:
        from strandfolio.storage.projects import ProjectError

        existing = self._firstRecordRevision(connection, revision.recordId)
        if existing is None:
            self._storeRevision(connection, revision, isOperationOutput=True)
            return
        origin = self._loadOutputOrigin(connection, revision.recordId)
        if (origin is None or not origin["was_gap"]
                or (origin["operation_id"], origin["output_key"]) != (
                    revision.outputReference.operationId, revision.outputReference.outputKey,
                )
                or renamedRevisionPayload(existing) != renamedRevisionPayload(revision)):
            raise ProjectError(f"Conflicting output record ID {revision.recordId!r}.")
        self._storeRevision(connection, revision, isPortableEvidence=True)

    def _loadRestrictionRun(
        self, connection: sqlite3.Connection, operationId: str,
    ) -> RestrictionDigest:
        from strandfolio.storage.projects import ProjectError, _projectHeader
        from strandfolio.storage.restriction_codec import decodeRestrictionRun

        if _projectHeader(connection) < 3:
            raise KeyError(operationId)
        row = connection.execute(
            "SELECT * FROM restriction_runs WHERE operation_id = ?", (operationId,),
        ).fetchone()
        if row is None:
            raise KeyError(operationId)
        try:
            payload = row["payload"]
            if sha256(payload.encode("ascii")).hexdigest() != row["payload_sha256"]:
                raise ValueError("Stored restriction payload checksum differs.")
            template = self._loadRevision(connection, row["template_revision_id"])
            links = connection.execute(
                "SELECT output_key, revision_id, output_ordinal FROM restriction_outputs "
                "WHERE operation_id = ? ORDER BY output_ordinal", (operationId,),
            )
            outputs = []
            for ordinal, link in enumerate(links, start=1):
                if link["output_ordinal"] != ordinal:
                    raise ValueError("Invalid restriction output order.")
                outputs.append((link["output_key"], self._loadRevision(connection, link["revision_id"])))
            result = decodeRestrictionRun(payload, template, tuple(outputs))
            if result.operationId != operationId:
                raise ValueError("Stored restriction operation identity differs.")
            return result
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ProjectError(f"Invalid stored restriction run {operationId!r}: {error}") from error

    def _verifyRestrictionRuns(self, connection: sqlite3.Connection) -> dict[str, str]:
        from strandfolio.storage.projects import ProjectError, _projectHeader

        if _projectHeader(connection) < 3:
            return {}
        named = dict(connection.execute("SELECT record_id, revision_id FROM named_records"))
        parents = {}
        for operationId, in connection.execute("SELECT operation_id FROM restriction_runs"):
            run = self._loadRestrictionRun(connection, operationId)
            reference = run.template.outputReference
            if reference is not None:
                parents[operationId] = reference.operationId
                self._verifyRunInput(connection, run.template)
            for fragment in run.fragments:
                namedId = named.get(fragment.revision.recordId)
                if namedId is None:
                    continue
                revision = self._loadRevision(connection, namedId)
                if (not run.cuts or revision.name.strip().casefold() == run.template.name.strip().casefold()
                        or not matchesRecordedProduct(
                            self._annotationProductBase(connection, revision), fragment.revision,
                        )):
                    raise ProjectError(f"Invalid named restriction product {namedId!r}.")
        if _projectHeader(connection) >= 4:
            self._verifyRestrictionViews(connection)
        return parents

    @staticmethod
    def _operationKind(connection: sqlite3.Connection, operationId: str) -> str | None:
        """Look up an operation ID across the tables for each analysis type."""
        from strandfolio.storage.projects import ProjectError, _projectHeader

        version = _projectHeader(connection)
        restriction = version >= 3 and connection.execute(
            "SELECT 1 FROM restriction_runs WHERE operation_id = ?", (operationId,),
        ).fetchone() is not None
        pcr = version >= 5 and connection.execute(
            "SELECT 1 FROM pcr_runs WHERE operation_id = ?", (operationId,),
        ).fetchone() is not None
        if restriction and pcr:
            raise ProjectError(f"Conflicting operation ID {operationId!r}.")
        if restriction:
            return "restriction"
        return "pcr" if pcr else None

    def _loadGeneratingOutput(
        self, connection: sqlite3.Connection, reference: OutputReference,
    ) -> tuple[SequenceRevision, SequenceRevision, bool]:
        """Find the candidate output and its input revision, and check whether it can be adopted."""
        from strandfolio.storage.projects import ProjectError

        operationId = reference.operationId
        kind = self._operationKind(connection, operationId)
        if kind == "restriction":
            run = self._loadRestrictionRun(connection, operationId)
            for fragment in run.fragments:
                if fragment.outputKey == reference.outputKey:
                    return fragment.revision, run.template, bool(run.cuts)
        elif kind == "pcr":
            run = self._loadPcrRun(connection, operationId)
            for product in run.products:
                if product.outputKey == reference.outputKey:
                    return product.revision, run.template, True
        else:
            raise KeyError(operationId)
        raise ProjectError(f"Missing {kind} output {reference.outputKey!r} for run {operationId!r}.")

    def _verifyRunInput(self, connection: sqlite3.Connection, template: SequenceRevision) -> None:
        from strandfolio.storage.projects import ProjectError, _projectHeader

        reference = template.outputReference
        if reference is None:
            return
        kind = self._operationKind(connection, reference.operationId)
        if kind is None:
            if _projectHeader(connection) >= 4:
                origin = self._loadOutputOrigin(connection, template.recordId)
                if (origin is not None and origin["was_gap"]
                        and (origin["operation_id"], origin["output_key"]) ==
                        (reference.operationId, reference.outputKey)):
                    return
            raise ProjectError("Register the generating restriction run or PCR run before using its output.")
        row = connection.execute(
            f"SELECT revision_id FROM {kind}_outputs WHERE operation_id = ? AND output_key = ?",
            (reference.operationId, reference.outputKey),
        ).fetchone()
        if row is None or not matchesRecordedProduct(template, self._loadRevision(connection, row[0])):
            raise ProjectError(f"Invalid generating output for source {template.revisionId!r}.")

    def _loadOutputOrigin(
        self, connection: sqlite3.Connection, recordId: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM restriction_origins WHERE record_id = ?", (recordId,),
        ).fetchone()

    def _verifyOutputOrigin(
        self, connection: sqlite3.Connection, row: sqlite3.Row, named: dict[str, str],
    ) -> None:
        from strandfolio.storage.projects import ProjectError

        recordId = row["record_id"]
        revision = self._firstRecordRevision(connection, recordId)
        reference = OutputReference(row["operation_id"], row["output_key"])
        if revision is None or revision.outputReference != reference:
            raise ProjectError(f"Invalid output provenance for record {recordId!r}.")
        try:
            candidate, template, canAdopt = self._loadGeneratingOutput(connection, reference)
        except KeyError as error:
            if row["was_gap"] and recordId not in named:
                return
            raise ProjectError(f"Missing generating run for record {recordId!r}.") from error
        saved = self._loadRevision(connection, named[recordId]) if recordId in named else None
        if (not matchesRecordedProduct(revision, candidate)
                or (saved is not None and (not canAdopt or not matchesRecordedProduct(
                    self._annotationProductBase(connection, saved), revision,
                )
                    or saved.name.strip().casefold() == template.name.strip().casefold()))):
            raise ProjectError(f"Invalid output provenance for record {recordId!r}.")

    @staticmethod
    def _hasAncestryGap(connection: sqlite3.Connection, operationId: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM restriction_origins WHERE operation_id = ? AND was_gap = 1 LIMIT 1",
            (operationId,),
        ).fetchone() is not None

    def _closeAncestryGap(self, connection: sqlite3.Connection, operationId: str) -> None:
        self._verifyStoredRuns(connection)
        connection.execute(
            "UPDATE restriction_origins SET was_gap = 0 WHERE operation_id = ?", (operationId,),
        )

    def _verifyStoredRuns(self, connection: sqlite3.Connection) -> None:
        from strandfolio.storage.projects import ProjectError, _projectHeader

        version = _projectHeader(connection)
        if version >= 5:
            overlap = connection.execute(
                "SELECT operation_id FROM restriction_runs JOIN pcr_runs USING (operation_id) LIMIT 1",
            ).fetchone()
            if overlap is not None:
                raise ProjectError(f"Conflicting operation ID {overlap[0]!r}.")
        _verifyAncestry(self._verifyRestrictionRuns(connection) | self._verifyPcrRuns(connection))
        if version >= 4:
            named = dict(connection.execute("SELECT record_id, revision_id FROM named_records"))
            for row in connection.execute("SELECT * FROM restriction_origins"):
                self._verifyOutputOrigin(connection, row, named)

    def listRestrictionRuns(self, revisionId: str | None = None) -> tuple[RestrictionDigest, ...]:
        """List completed runs, optionally for one exact input, newest registration first."""
        from strandfolio.storage.projects import _projectHeader

        with self._read() as connection:
            if _projectHeader(connection) < 3:
                return ()
            byRevision = "AND template_revision_id = ?" if revisionId is not None else ""
            rows = connection.execute(
                "SELECT operation_id FROM restriction_runs "
                f"WHERE json_extract(payload, '$.status') = 'completed' {byRevision} ORDER BY rowid DESC",
                (revisionId,) if revisionId is not None else (),
            )
            return tuple(self._loadRestrictionRun(connection, row[0]) for row in rows)

    def getRestrictionRun(self, operationId: str) -> RestrictionDigest:
        """Reopen an exact recorded result without originals, catalogue lookup or engine work."""
        with self._read() as connection:
            return self._loadRestrictionRun(connection, operationId)

    def getRecordedRun(self, operationId: str) -> RestrictionDigest | PcrRun:
        """Load a retained analysis from its global operation ID in one read transaction."""
        with self._read() as connection:
            kind = self._operationKind(connection, operationId)
            if kind == "restriction":
                return self._loadRestrictionRun(connection, operationId)
            if kind == "pcr":
                return self._loadPcrRun(connection, operationId)
            raise KeyError(operationId)

    def getRestrictionOutput(self, operationId: str, outputKey: str) -> DNAFragment:
        """Return a product of this recorded operation by its stable output key."""
        for fragment in self.getRestrictionRun(operationId).fragments:
            if fragment.outputKey == outputKey:
                return fragment
        raise KeyError(OutputReference(operationId, outputKey))

    def getRestrictionOutputReference(self, revisionId: str) -> OutputReference | None:
        """Return the generating reference for a candidate or a named snapshot of it."""
        with self._read() as connection:
            try:
                reference = self._loadRevision(connection, revisionId).outputReference
            except KeyError:
                return None
            if reference is not None and self._operationKind(connection, reference.operationId) == "pcr":
                return None
            return reference

    def _checkOutputAdoption(
        self, connection: sqlite3.Connection, revision: SequenceRevision, nameKey: str,
    ) -> None:
        from strandfolio.storage.projects import ProjectError

        reference = revision.outputReference
        if reference is None:
            return
        _, template, canAdopt = self._loadGeneratingOutput(connection, reference)
        if not canAdopt:
            raise ProjectError("An uncut restriction product cannot be named as a descendant.")
        if template.name.strip().casefold() == nameKey:
            raise ProjectError("A product name must differ from its template name.")

    def saveRestrictionOutput(
        self, operationId: str, outputKey: str, name: str, *, requestId: str,
        independent: bool = False,
    ) -> SequenceRevision:
        """Name a candidate or explicitly save an independent copy with its generating reference."""
        if type(independent) is not bool:
            raise TypeError("independent must be a boolean.")
        fragment = self.getRestrictionOutput(operationId, outputKey)
        revision = fragment.revision
        if independent:
            self._validateRequestId(requestId)
            revision = _independentProduct(revision, requestId)
        return self.saveRevision(revision, name, requestId=requestId)

    def adoptRestrictionOutput(self, operationId: str, outputKey: str) -> SequenceRevision:
        """Resolve adoption to the named record, or to the same immutable candidate."""
        from strandfolio.storage.projects import ProjectError

        run = self.getRestrictionRun(operationId)
        if not run.cuts:
            raise ProjectError("An uncut restriction product cannot be adopted as a descendant.")
        for fragment in run.fragments:
            if fragment.outputKey == outputKey:
                candidate = fragment.revision
                return self.getSavedRevision(candidate.recordId) or candidate
        raise KeyError(OutputReference(operationId, outputKey))

    def _storeOutputAdoptionCandidate(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
    ) -> None:
        from strandfolio.storage.projects import ProjectError

        reference = revision.outputReference
        try:
            product, _, _ = self._loadGeneratingOutput(connection, reference)
        except KeyError as error:
            raise ProjectError("Register the generating restriction run or PCR run before using its output.") from error
        if not matchesRecordedProduct(revision, product):
            raise ProjectError("Adoption differs from its recorded output.")
        if self._firstRecordRevision(connection, revision.recordId) is None:
            self._storeRevision(connection, revision, isOperationOutput=True)
            connection.execute(
                "INSERT INTO restriction_origins VALUES (?, ?, ?, 0)",
                (revision.recordId, reference.operationId, reference.outputKey),
            )
        else:
            self._storeRevision(connection, revision)


def _independentProduct(
    revision: SequenceRevision, requestId: str, *, kind: str = "restriction",
) -> SequenceRevision:
    from strandfolio.domain.sequence import SequenceRevision
    from strandfolio.storage.projects import _json

    reference = revision.outputReference
    # The save request always describes the same candidate across retries.
    namespace = uuid5(NAMESPACE_URL, _json((f"vibeGene {kind} copy", reference.operationId,
                                           reference.outputKey, requestId)))
    record = revision.toSeqRecord()
    if revision.moleculeDeclaration is None:
        del record.annotations["molecule_type"]
    return SequenceRevision.fromStoredRecord(
        record, moleculeType=revision.moleculeType, recordId=str(uuid5(namespace, "record")),
        revisionId=str(uuid5(namespace, "revision")),
        featureIds=tuple(str(uuid5(namespace, item.featureId)) for item in revision.features),
        duplexGeometry=revision.duplexGeometry, outputReference=reference,
    )
