"""Commit and reopen PCR results, exact primer revisions and selected gel views."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import sqlite3
from typing import TYPE_CHECKING

from strandfolio.domain.provenance import OutputReference
from strandfolio.storage.project_primer_pairs import unownedPrimerCondition
from strandfolio.storage.revision_codec import renamedRevisionPayload

if TYPE_CHECKING:
    from strandfolio.domain.primers import PrimerRevision
    from strandfolio.domain.sequence import SequenceRevision
    from strandfolio.io.digestion import SavedDigestion
    from strandfolio.io.formats import SequenceImportResult
    from strandfolio.operations.gel import GelViewSettings
    from strandfolio.operations.pcr import PcrProduct, PcrRun
    from strandfolio.storage.project_edits import AnnotationEvidence
    from strandfolio.storage.projects import _ProjectConnection


PCR_SCHEMA = (
    """CREATE TABLE pcr_primer_revisions (
        revision_id TEXT PRIMARY KEY NOT NULL,
        payload TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    )""",
    """CREATE TABLE pcr_runs (
        operation_id TEXT PRIMARY KEY NOT NULL,
        template_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
        forward_primer_revision_id TEXT NOT NULL REFERENCES pcr_primer_revisions(revision_id),
        reverse_primer_revision_id TEXT NOT NULL REFERENCES pcr_primer_revisions(revision_id),
        payload TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    )""",
    """CREATE INDEX pcr_runs_by_input ON pcr_runs (template_revision_id)""",
    """CREATE INDEX pcr_runs_by_forward_primer ON pcr_runs (forward_primer_revision_id)""",
    """CREATE INDEX pcr_runs_by_reverse_primer ON pcr_runs (reverse_primer_revision_id)""",
    """CREATE TABLE pcr_outputs (
        operation_id TEXT NOT NULL REFERENCES pcr_runs(operation_id),
        output_key TEXT NOT NULL,
        revision_id TEXT UNIQUE NOT NULL REFERENCES revisions(revision_id),
        output_ordinal INTEGER NOT NULL CHECK (output_ordinal > 0),
        PRIMARY KEY (operation_id, output_key),
        UNIQUE (operation_id, output_ordinal)
    )""",
    """CREATE TABLE pcr_views (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES pcr_runs(operation_id),
        payload TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    )""",
)


def _checksum(payload: str) -> str:
    return sha256(payload.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class CertifiedPcrRun:
    """A PCR result whose evidence and completeness have been checked."""

    result: PcrRun

    def __init__(self, result: PcrRun, *, isCanceled: Callable[[], bool] | None = None) -> None:
        from strandfolio.operations.pcr import validatePcrEvidence

        validatePcrEvidence(result, checkCompleteness=True, isCanceled=isCanceled)
        object.__setattr__(self, "result", result)


def certifyPcrRun(
    result: PcrRun, *, isCanceled: Callable[[], bool] | None = None,
) -> CertifiedPcrRun:
    """Validate one result's prediction and completeness before publication."""
    return CertifiedPcrRun(result, isCanceled=isCanceled)


class ProjectPcrMixin:
    """Store PCR results in a project and reopen them without rerunning PCR."""

    def registerPcrRun(
        self, result: PcrRun, *, capture: SequenceImportResult | None = None,
        independent: bool = False, ancestors: tuple[PcrRun | SavedDigestion, ...] = (),
        view: GelViewSettings | None = None, certification: CertifiedPcrRun | None = None,
        ancestorCertifications: tuple[CertifiedPcrRun, ...] = (),
        ancestorViews: tuple[tuple[str, GelViewSettings], ...] = (), timeout: float = 30.0,
        annotationEvidence: AnnotationEvidence | None = None,
        ancestorAnnotations: tuple[AnnotationEvidence, ...] = (),
    ) -> PcrRun:
        """Save the PCR result, inputs, and any staged ancestry in one transaction."""
        from strandfolio.storage.projects import ProjectError, _key

        if certification is not None and (
            type(certification) is not CertifiedPcrRun or certification.result is not result
        ):
            raise ProjectError("PCR certification belongs to another result.")
        annotations = (*ancestorAnnotations, annotationEvidence) if annotationEvidence else ancestorAnnotations
        analyses = self._prepareAnalysisRuns(
            ancestors, capture, independent, trailing=result, annotations=annotations,
        )
        if annotationEvidence is not None:
            self._validateAnnotationEvidence(annotationEvidence, result.template)
        with self._write(timeout=timeout) as connection:
            newCapture = self._storeAnalysisRuns(
                connection, analyses[:-1], capture, independent, certifications=ancestorCertifications,
                views=ancestorViews,
                annotations=ancestorAnnotations,
            )
            if annotationEvidence is not None:
                self._storeAnnotationEvidence(
                    connection, annotationEvidence, f"pcr-edit-{_key(result.operationId)}",
                )
            retained = self._storePcrRun(connection, result, certify=certification is None)
            if view is not None:
                self._storePcrView(connection, retained.operationId, view)
            if newCapture is not None:
                self._preserveArtifact(newCapture.source, newCapture.captureBytes,
                                       f"pcr-{_key(result.operationId)}")
            return retained

    def _prepareAnalysisRuns(
        self, runs: tuple[PcrRun | SavedDigestion, ...], capture: SequenceImportResult | None,
        independent: bool, *, trailing: PcrRun | SavedDigestion | None = None,
        output: OutputReference | None = None, terminalTemplate: SequenceRevision | None = None,
        annotations: tuple[AnnotationEvidence, ...] = (),
    ) -> tuple[PcrRun | SavedDigestion, ...]:
        """Validate staged evidence and its relationship to the final operation or adopted output."""
        from strandfolio.io.digestion import SavedDigestion
        from strandfolio.operations.pcr import PcrRun
        from strandfolio.storage.project_edits import annotationBase
        from strandfolio.storage.projects import ProjectError, _checkedEncoding, _validateCapture

        if type(independent) is not bool:
            raise TypeError("independent must be a boolean.")
        if independent and capture is None:
            raise ProjectError("An independent analysis intake requires its source capture.")
        analyses = []
        indexed = {}
        snapshots = []
        bases = {}
        for evidence in annotations:
            self._validateAnnotationEvidence(evidence, evidence.history[-1])
            bases[evidence.history[-1].revisionId] = evidence.history[0]
        if terminalTemplate is not None:
            terminalTemplate = bases.get(terminalTemplate.revisionId, terminalTemplate)
        staged = (*runs, trailing) if trailing is not None else runs
        for value in staged:
            if type(value) is PcrRun:
                indexed[value.operationId] = bases.get(value.template.revisionId, value.template)
                snapshots.append(value.template)
            elif type(value) is SavedDigestion:
                value = self._checkedDigestion(value)
                steps = {step.revision.revisionId: step for step in value.annotationSteps}
                for digest in (*value.ancestorRuns, value.result):
                    indexed[digest.operationId] = annotationBase(digest.template, steps)
                    snapshots.append(digest.template)
                snapshots.extend(value.intakeRevisions)
            else:
                raise ProjectError("Expected a staged PCR run or saved digestion.")
            analyses.append(value)
        if trailing is not None:
            entryPoint = trailing.result.operationId if type(trailing) is SavedDigestion else trailing.operationId
        else:
            reference = terminalTemplate.outputReference if terminalTemplate is not None else output
            entryPoint = reference.operationId if reference is not None else None
        remaining = set(indexed)
        while entryPoint in remaining:
            remaining.remove(entryPoint)
            reference = indexed[entryPoint].outputReference
            entryPoint = reference.operationId if reference is not None else None
        if remaining or entryPoint in indexed:
            raise ProjectError("Staged analyses must supply the selected output's ancestry.")
        if output is not None and trailing is None and output.operationId not in indexed:
            raise ProjectError("Staged analyses do not include the selected generating run.")
        if capture is not None:
            _validateCapture(capture)
            if not any(
                revision.sourceArtifactId == capture.source.artifactId
                and type(revision.sourceOrdinal) is int and 1 <= revision.sourceOrdinal <= len(capture.records)
                and _checkedEncoding(revision) == _checkedEncoding(capture.records[revision.sourceOrdinal - 1])
                for revision in snapshots
            ):
                raise ProjectError("No analysis input matches the supplied source capture.")
        return tuple(analyses)

    def _storeAnalysisRuns(
        self, connection: _ProjectConnection, analyses: tuple[PcrRun | SavedDigestion, ...],
        capture: SequenceImportResult | None, independent: bool, *,
        certifications: tuple[CertifiedPcrRun, ...] = (),
        views: tuple[tuple[str, GelViewSettings], ...] = (),
        annotations: tuple[AnnotationEvidence, ...] = (),
    ) -> SequenceImportResult | None:
        from strandfolio.io.digestion import SavedDigestion
        from strandfolio.operations.pcr import PcrRun
        from strandfolio.storage.projects import ProjectError, _key

        if any(type(proof) is not CertifiedPcrRun for proof in certifications):
            raise ProjectError("Invalid PCR certification.")
        certified = {id(proof.result) for proof in certifications}
        stagedIds = {analysis.operationId for analysis in analyses if type(analysis) is PcrRun}
        stagedViews = dict(views)
        if len(stagedViews) != len(views) or not stagedViews.keys() <= stagedIds:
            raise ProjectError("Staged PCR views require unique IDs of staged PCR runs.")
        newCapture = None
        if capture is not None:
            registered, isNew = self._registerCapture(
                connection, capture, independent=independent, preserveCaptureIdentity=True,
            )
            if registered.source.artifactId != capture.source.artifactId:
                raise ProjectError("The analysis input differs from the registered intake.")
            if isNew:
                newCapture = registered
        for analysis in analyses:
            if type(analysis) is SavedDigestion:
                self._storeDigestion(connection, analysis, updateView=False)
                self._storeDigestionAnnotations(connection, analysis, annotations)
            else:
                for evidence in annotations:
                    if evidence.history[-1].revisionId == analysis.template.revisionId:
                        self._storeAnnotationEvidence(
                            connection, evidence, f"pcr-edit-{_key(analysis.operationId)}",
                        )
                self._storePcrRun(connection, analysis, certify=id(analysis) not in certified)
                if analysis.operationId in stagedViews:
                    self._storePcrView(
                        connection, analysis.operationId, stagedViews[analysis.operationId],
                        updateExisting=False,
                    )
        return newCapture

    def _storePcrRun(self, connection: sqlite3.Connection, run: PcrRun, *, certify: bool) -> PcrRun:
        from strandfolio.storage.pcr_codec import encodePcrRun
        from strandfolio.storage.projects import ProjectError, _isIdentity

        try:
            if not _isIdentity(run.operationId):
                raise ValueError("A PCR run requires a valid operation ID.")
            payload = encodePcrRun(run)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ProjectError(f"Invalid PCR run: {error}") from error
        operationId = run.operationId
        kind = self._operationKind(connection, operationId)
        if kind == "restriction":
            raise ProjectError(f"Conflicting operation ID {operationId!r}.")
        if kind == "pcr":
            existing = connection.execute(
                "SELECT payload FROM pcr_runs WHERE operation_id = ?", (operationId,),
            ).fetchone()[0]
            if existing != payload:
                raise ProjectError(f"Conflicting PCR operation ID {operationId!r}.")
            return self._loadPcrRun(connection, operationId)
        self._storeRevision(connection, run.template)
        self._verifyRunInput(connection, run.template)
        for primer in (run.forwardPrimer, run.reversePrimer):
            self._storePrimerRevision(connection, primer)
        connection.execute(
            "INSERT INTO pcr_runs VALUES (?, ?, ?, ?, ?, ?)",
            (operationId, run.template.revisionId, run.forwardPrimer.revisionId,
             run.reversePrimer.revisionId, payload, _checksum(payload)),
        )
        closesGap = self._hasAncestryGap(connection, operationId)
        for ordinal, product in enumerate(run.products, start=1):
            revision = product.revision
            self._storeOutputCandidate(connection, revision)
            connection.execute(
                "INSERT INTO pcr_outputs VALUES (?, ?, ?, ?)",
                (operationId, product.outputKey, revision.revisionId, ordinal),
            )
            connection.execute("DELETE FROM restriction_origins WHERE record_id = ?", (revision.recordId,))
        retained = self._loadPcrRun(connection, operationId, checkCompleteness=certify)
        if closesGap:
            self._closeAncestryGap(connection, operationId)
        return retained

    @staticmethod
    def _storePrimerRevision(connection: sqlite3.Connection, primer: PrimerRevision) -> None:
        from strandfolio.storage.pcr_codec import decodePrimerRevision, encodePrimerRevision
        from strandfolio.storage.projects import ProjectError

        try:
            payload = encodePrimerRevision(primer)
            decodePrimerRevision(payload)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ProjectError(f"Invalid primer revision: {error}") from error
        fields = (payload, _checksum(payload))
        row = connection.execute(
            "SELECT payload, payload_sha256 FROM pcr_primer_revisions WHERE revision_id = ?",
            (primer.revisionId,),
        ).fetchone()
        if row is None:
            connection.execute("INSERT INTO pcr_primer_revisions VALUES (?, ?, ?)", (primer.revisionId, *fields))
        elif tuple(row) != fields:
            raise ProjectError(f"Conflicting primer revision ID {primer.revisionId!r}.")

    @staticmethod
    def _loadPrimerRevision(connection: sqlite3.Connection, revisionId: str) -> PrimerRevision:
        from strandfolio.storage.pcr_codec import decodePrimerRevision

        row = connection.execute(
            "SELECT payload, payload_sha256 FROM pcr_primer_revisions WHERE revision_id = ?",
            (revisionId,),
        ).fetchone()
        if row is None or _checksum(row["payload"]) != row["payload_sha256"]:
            raise ValueError(f"Missing or damaged primer revision {revisionId!r}.")
        result = decodePrimerRevision(row["payload"])
        if result.revisionId != revisionId:
            raise ValueError("Stored primer identity differs.")
        return result

    def _loadPcrRun(
        self, connection: sqlite3.Connection, operationId: str, *, checkCompleteness: bool = False,
    ) -> PcrRun:
        from strandfolio.storage.pcr_codec import decodePcrRun
        from strandfolio.storage.projects import ProjectError, _projectHeader

        if _projectHeader(connection) < 5:
            raise KeyError(operationId)
        row = connection.execute("SELECT * FROM pcr_runs WHERE operation_id = ?", (operationId,)).fetchone()
        if row is None:
            raise KeyError(operationId)
        try:
            payload = row["payload"]
            if _checksum(payload) != row["payload_sha256"]:
                raise ValueError("Stored PCR payload checksum differs.")
            template = self._loadRevision(connection, row["template_revision_id"])
            forward = self._loadPrimerRevision(connection, row["forward_primer_revision_id"])
            reverse = self._loadPrimerRevision(connection, row["reverse_primer_revision_id"])
            links = connection.execute(
                "SELECT output_key, revision_id, output_ordinal FROM pcr_outputs "
                "WHERE operation_id = ? ORDER BY output_ordinal", (operationId,),
            )
            outputs = []
            for ordinal, link in enumerate(links, start=1):
                if link["output_ordinal"] != ordinal:
                    raise ValueError("Invalid PCR output order.")
                outputs.append((link["output_key"], self._loadRevision(connection, link["revision_id"])))
            run = decodePcrRun(
                payload, template, forward, reverse, tuple(outputs), checkCompleteness=checkCompleteness,
            )
            if run.operationId != operationId:
                raise ValueError("Stored PCR operation identity differs.")
            return run
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
            raise ProjectError(f"Invalid stored PCR run {operationId!r}: {error}") from error

    def _verifyPcrRuns(self, connection: sqlite3.Connection) -> dict[str, str]:
        """Verify stored PCR metadata, named products and cross-operation input links."""
        from strandfolio.storage.projects import ProjectError, _projectHeader

        version = _projectHeader(connection)
        if version < 5:
            return {}
        if version >= 9:
            self._listPrimerPairs(connection)
        orphan = connection.execute(
            "SELECT revision_id FROM pcr_primer_revisions WHERE "
            + unownedPrimerCondition(includeSavedPairs=version >= 9) + " LIMIT 1",
        ).fetchone()
        if orphan is not None:
            raise ProjectError(f"Missing owner for primer revision {orphan[0]!r}.")
        named = dict(connection.execute("SELECT record_id, revision_id FROM named_records"))
        parents = {}
        for operationId, in connection.execute("SELECT operation_id FROM pcr_runs"):
            run = self._loadPcrRun(connection, operationId)
            reference = run.template.outputReference
            if reference is not None:
                parents[operationId] = reference.operationId
                self._verifyRunInput(connection, run.template)
            for product in run.products:
                namedId = named.get(product.revision.recordId)
                if namedId is None:
                    continue
                revision = self._loadRevision(connection, namedId)
                base = self._annotationProductBase(connection, revision)
                if (revision.name.strip().casefold() == run.template.name.strip().casefold()
                        or renamedRevisionPayload(base) != renamedRevisionPayload(product.revision)):
                    raise ProjectError(f"Invalid named PCR product {namedId!r}.")
            self._loadPcrView(connection, operationId)
        return parents

    def listPcrRuns(self, revisionId: str | None = None) -> tuple[PcrRun, ...]:
        """List retained results and their recorded statuses, newest registration first."""
        with self._read() as connection:
            return tuple(
                self._loadPcrRun(connection, operationId)
                for operationId in self._listPcrRunIds(connection, revisionId)
            )

    def listPcrRunIds(self, revisionId: str | None = None) -> tuple[str, ...]:
        """List stored run IDs for history without loading their results."""
        with self._read() as connection:
            return self._listPcrRunIds(connection, revisionId)

    @staticmethod
    def _listPcrRunIds(connection: sqlite3.Connection, revisionId: str | None) -> tuple[str, ...]:
        from strandfolio.storage.projects import _projectHeader

        if _projectHeader(connection) < 5:
            return ()
        where = "WHERE template_revision_id = ?" if revisionId is not None else ""
        return tuple(row[0] for row in connection.execute(
            f"SELECT operation_id FROM pcr_runs {where} ORDER BY rowid DESC",
            (revisionId,) if revisionId is not None else (),
        ))

    def getPcrRun(self, operationId: str) -> PcrRun:
        """Reopen recorded values without invoking the search engine."""
        with self._read() as connection:
            return self._loadPcrRun(connection, operationId)

    def getPcrOutput(self, operationId: str, outputKey: str) -> PcrProduct:
        """Find a completed PCR product by its output key."""
        for product in self.getPcrRun(operationId).products:
            if product.outputKey == outputKey:
                return product
        raise KeyError(OutputReference(operationId, outputKey))

    def adoptPcrOutput(self, operationId: str, outputKey: str) -> SequenceRevision:
        """Return the named revision of an adopted product, or its original candidate."""
        candidate = self.getPcrOutput(operationId, outputKey).revision
        return self.getSavedRevision(candidate.recordId) or candidate

    def savePcrOutput(
        self, operationId: str, outputKey: str, name: str, *, requestId: str,
        independent: bool = False,
    ) -> SequenceRevision:
        """Name the exact candidate or explicitly save a separately identified copy."""
        from strandfolio.storage.project_runs import _independentProduct

        if type(independent) is not bool:
            raise TypeError("independent must be a boolean.")
        revision = self.getPcrOutput(operationId, outputKey).revision
        if independent:
            self._validateRequestId(requestId)
            revision = _independentProduct(revision, requestId, kind="pcr")
        return self.saveRevision(revision, name, requestId=requestId)

    def getPcrView(self, operationId: str) -> GelViewSettings | None:
        """Read the optional saved preview settings for an exact retained run."""
        with self._read() as connection:
            if self._operationKind(connection, operationId) != "pcr":
                raise KeyError(operationId)
            return self._loadPcrView(connection, operationId)

    def updatePcrView(
        self, operationId: str, view: GelViewSettings, *, timeout: float = 30.0,
    ) -> None:
        """Persist marker, display bounds and contributor selection separately from the run."""
        with self._write(timeout=timeout) as connection:
            if self._operationKind(connection, operationId) != "pcr":
                raise KeyError(operationId)
            self._storePcrView(connection, operationId, view)

    @staticmethod
    def _loadPcrView(connection: sqlite3.Connection, operationId: str) -> GelViewSettings | None:
        from strandfolio.io.digestion import decodeDigestionView
        from strandfolio.storage.projects import ProjectError

        row = connection.execute(
            "SELECT payload, payload_sha256 FROM pcr_views WHERE operation_id = ?", (operationId,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = row["payload"]
            if _checksum(payload) != row["payload_sha256"]:
                raise ValueError("Stored PCR gel view checksum differs.")
            view = decodeDigestionView(payload)
            _checkPcrView(connection, operationId, view)
            return view
        except (ValueError, AttributeError) as error:
            raise ProjectError(f"Invalid PCR gel view for run {operationId!r}: {error}") from error

    @staticmethod
    def _storePcrView(
        connection: sqlite3.Connection, operationId: str, view: GelViewSettings,
        *, updateExisting: bool = True,
    ) -> None:
        from strandfolio.io.digestion import decodeDigestionView, encodeDigestionView
        from strandfolio.storage.projects import ProjectError

        try:
            payload = encodeDigestionView(view)
            _checkPcrView(connection, operationId, decodeDigestionView(payload))
        except (ValueError, TypeError, AttributeError) as error:
            raise ProjectError(f"Invalid PCR gel view: {error}") from error
        connection.execute(
            "INSERT INTO pcr_views VALUES (?, ?, ?) ON CONFLICT (operation_id) "
            "DO UPDATE SET payload = excluded.payload, payload_sha256 = excluded.payload_sha256 "
            "WHERE ? AND (pcr_views.payload <> excluded.payload "
            "OR pcr_views.payload_sha256 <> excluded.payload_sha256)",
            (operationId, payload, _checksum(payload), updateExisting),
        )


def _checkPcrView(connection: sqlite3.Connection, operationId: str, view: GelViewSettings) -> None:
    key = view.selectedOutputKey
    if key is not None and connection.execute(
        "SELECT 1 FROM pcr_outputs WHERE operation_id = ? AND output_key = ?", (operationId, key),
    ).fetchone() is None:
        raise ValueError("The saved gel selection refers to an unknown PCR output.")
