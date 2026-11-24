"""Link portable restriction evidence and saved gel views to native projects."""

from __future__ import annotations

from hashlib import sha256
import sqlite3
from typing import TYPE_CHECKING

from strandfolio.domain.provenance import OutputReference
from strandfolio.storage.project_edits import annotationTrail
from strandfolio.storage.revision_codec import matchesRecordedProduct, renamedRevisionPayload

if TYPE_CHECKING:
    from strandfolio.domain.sequence import SequenceRevision
    from strandfolio.io.digestion import DigestionSourceArtifact, SavedDigestion
    from strandfolio.operations.gel import GelViewSettings
    from strandfolio.operations.restriction import RestrictionDigest
    from strandfolio.storage.project_edits import AnnotationEvidence
    from strandfolio.storage.projects import _ProjectConnection


DIGESTION_SCHEMA = (
    """CREATE TABLE restriction_views (
        operation_id TEXT PRIMARY KEY NOT NULL REFERENCES restriction_runs(operation_id),
        payload TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64)
    )""",
    """CREATE TABLE portable_sources (
        artifact_id TEXT PRIMARY KEY NOT NULL REFERENCES artifacts(artifact_id)
    )""",
    """CREATE TABLE portable_intake_revisions (
        artifact_id TEXT NOT NULL REFERENCES portable_sources(artifact_id),
        source_ordinal INTEGER NOT NULL CHECK (source_ordinal > 0),
        revision_id TEXT NOT NULL UNIQUE,
        PRIMARY KEY (artifact_id, source_ordinal),
        FOREIGN KEY (artifact_id, source_ordinal, revision_id)
            REFERENCES revisions (source_artifact_id, source_ordinal, revision_id)
    )""",
    """CREATE TABLE restriction_origins (
        record_id TEXT PRIMARY KEY NOT NULL REFERENCES records(record_id),
        operation_id TEXT NOT NULL,
        output_key TEXT NOT NULL,
        was_gap INTEGER NOT NULL CHECK (was_gap IN (0, 1))
    )""",
)


class ProjectDigestionMixin:
    """Store the run data needed to reopen the digestion in another project."""

    def _loadRestrictionView(
        self, connection: sqlite3.Connection, run: RestrictionDigest,
    ) -> GelViewSettings:
        from strandfolio.io.digestion import decodeDigestionView, validateDigestionView
        from strandfolio.operations.gel import GelViewSettings
        from strandfolio.storage.projects import ProjectError, _projectHeader

        if _projectHeader(connection) < 4:
            return GelViewSettings()
        row = connection.execute(
            "SELECT payload, payload_sha256 FROM restriction_views WHERE operation_id = ?",
            (run.operationId,),
        ).fetchone()
        if row is None:
            return GelViewSettings()
        try:
            payload = row["payload"]
            if sha256(payload.encode("ascii")).hexdigest() != row["payload_sha256"]:
                raise ValueError("Stored gel view checksum differs.")
            view = decodeDigestionView(payload)
            validateDigestionView(run, view)
            return view
        except (ValueError, AttributeError) as error:
            raise ProjectError(f"Invalid gel view for run {run.operationId!r}: {error}") from error

    @staticmethod
    def _storeRestrictionView(
        connection: sqlite3.Connection, run: RestrictionDigest, view: GelViewSettings,
        *, updateExisting: bool = True,
    ) -> None:
        from strandfolio.io.digestion import (
            decodeDigestionView, encodeDigestionView, validateDigestionView,
        )
        from strandfolio.storage.projects import ProjectError

        try:
            payload = encodeDigestionView(view)
            validateDigestionView(run, decodeDigestionView(payload))
        except (ValueError, TypeError, AttributeError) as error:
            raise ProjectError(f"Invalid gel view for run {run.operationId!r}: {error}") from error
        connection.execute(
            "INSERT INTO restriction_views VALUES (?, ?, ?) ON CONFLICT (operation_id) "
            "DO UPDATE SET payload = excluded.payload, payload_sha256 = excluded.payload_sha256 "
            "WHERE ? AND (restriction_views.payload <> excluded.payload "
            "OR restriction_views.payload_sha256 <> excluded.payload_sha256)",
            (run.operationId, payload, sha256(payload.encode("ascii")).hexdigest(), updateExisting),
        )

    def _verifyRestrictionViews(self, connection: sqlite3.Connection) -> None:
        for operationId, in connection.execute("SELECT operation_id FROM restriction_views"):
            self._loadRestrictionView(connection, self._loadRestrictionRun(connection, operationId))

    def saveRestrictionView(
        self, operationId: str, view: GelViewSettings, *, timeout: float = 30.0,
    ) -> None:
        """Replace a run's presentation settings without changing the run itself."""
        with self._write(timeout=timeout) as connection:
            run = self._loadRestrictionRun(connection, operationId)
            self._storeRestrictionView(connection, run, view)

    def getDigestion(self, operationId: str) -> SavedDigestion:
        """Resolve the stored run, exact source ancestry and view without engine or catalogue access."""
        with self._read() as connection:
            return self._loadDigestion(connection, operationId)

    def _loadDigestion(self, connection: sqlite3.Connection, operationId: str) -> SavedDigestion:
        from strandfolio.io.digestion import SavedDigestion

        result = self._loadRestrictionRun(connection, operationId)
        ancestors, gaps = self._restrictionAncestry(connection, result)
        sources, intakes, annotationSteps = {}, {}, {}
        for run in (*ancestors, result):
            trail = self._loadAnnotationTrail(connection, run.template)
            annotationSteps.update((step.revision.revisionId, step) for step in trail)
            base = trail[0].parent if trail else run.template
            if base.sourceArtifactId is not None:
                source, intake = self._loadDigestionSource(connection, base)
                sources[source.artifact.artifactId] = source
                if intake is not None and intake.revisionId != base.revisionId:
                    intakes[intake.revisionId] = intake
        return SavedDigestion(
            result, self._loadRestrictionView(connection, result), ancestors, tuple(sources.values()),
            gaps, tuple(intakes.values()), tuple(annotationSteps.values()),
        )

    def _restrictionAncestry(
        self, connection: sqlite3.Connection, result: RestrictionDigest,
    ) -> tuple[tuple[RestrictionDigest, ...], tuple[OutputReference, ...]]:
        from strandfolio.storage.projects import ProjectError, _projectHeader

        run = result
        visited = {run.operationId}
        ancestors = []
        while True:
            trail = self._loadAnnotationTrail(connection, run.template)
            base = trail[0].parent if trail else run.template
            reference = base.outputReference
            if reference is None:
                return tuple(reversed(ancestors)), ()
            if reference.operationId in visited:
                raise ProjectError(f"Circular restriction ancestry at run {reference.operationId!r}.")
            visited.add(reference.operationId)
            if self._operationKind(connection, reference.operationId) == "pcr":
                # A portable digestion can carry the exact PCR-derived snapshot and its
                # reference, while the full PCR run remains in the native project.
                return tuple(reversed(ancestors)), (reference,)
            try:
                run = self._loadRestrictionRun(connection, reference.operationId)
            except KeyError as error:
                if _projectHeader(connection) >= 4:
                    origin = self._loadOutputOrigin(connection, base.recordId)
                    if origin is not None and origin["was_gap"]:
                        return tuple(reversed(ancestors)), (reference,)
                raise ProjectError(f"Missing generating run {reference.operationId!r}.") from error
            ancestors.append(run)

    def _loadDigestionSource(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
    ) -> tuple[DigestionSourceArtifact, SequenceRevision | None]:
        from strandfolio.io.digestion import DigestionSourceArtifact
        from strandfolio.storage.projects import _projectHeader

        artifactId = revision.sourceArtifactId
        source = self._loadArtifact(connection, artifactId)
        formatName = connection.execute(
            "SELECT format FROM artifacts WHERE artifact_id = ?", (artifactId,),
        ).fetchone()[0]
        fields = (artifactId, revision.sourceOrdinal)
        match = connection.execute(
            "SELECT revision_id FROM intake_revisions WHERE artifact_id = ? AND source_ordinal = ?",
            fields,
        ).fetchone()
        if match is None and _projectHeader(connection) >= 4:
            match = connection.execute(
                "SELECT revision_id FROM portable_intake_revisions "
                "WHERE artifact_id = ? AND source_ordinal = ?", fields,
            ).fetchone()
        intake = self._loadRevision(connection, match[0]) if match is not None else None
        return DigestionSourceArtifact(source, formatName), intake

    def registerDigestion(self, digestion: SavedDigestion, *, timeout: float = 30.0) -> SavedDigestion:
        """Import a digestion in one transaction and update the main run's saved view.

        Reopening reuses exact revision and operation identities even when the supplied
        presentation settings differ. Offline replay uses recorded outcomes.
        """
        digestion = self._checkedDigestion(digestion)
        with self._write(timeout=timeout) as connection:
            self._storeDigestion(connection, digestion, updateView=True)
            return self._loadDigestion(connection, digestion.result.operationId)

    @staticmethod
    def _checkedDigestion(digestion: SavedDigestion) -> SavedDigestion:
        from strandfolio.io.digestion import validateDigestion
        from strandfolio.storage.projects import ProjectError

        try:
            return validateDigestion(digestion)
        except ValueError as error:
            raise ProjectError(str(error)) from error

    def _checkedAdoptionDigestion(
        self, digestion: SavedDigestion, revision: SequenceRevision, revisionPayload: str,
    ) -> SavedDigestion:
        from strandfolio.storage.projects import ProjectError
        from strandfolio.storage.revision_codec import encodeRevision

        digestion = self._checkedDigestion(digestion)
        reference = revision.outputReference
        if (reference is None or reference.operationId != digestion.result.operationId
                or not any(fragment.outputKey == reference.outputKey
                           and encodeRevision(fragment.revision) == revisionPayload
                           for fragment in digestion.result.fragments)):
            raise ProjectError("The candidate is not an exact output of the supplied digestion.")
        return digestion

    def _storeDigestion(
        self, connection: sqlite3.Connection, digestion: SavedDigestion, *, updateView: bool,
    ) -> None:
        from strandfolio.storage.restriction_codec import encodeRestrictionRun

        for source in digestion.sourceArtifacts:
            self._storePortableSource(connection, source)
        for revision in digestion.intakeRevisions:
            self._storePortableIntake(connection, revision)
        steps = {step.revision.revisionId: step for step in digestion.annotationSteps}
        for run in (*digestion.ancestorRuns, digestion.result):
            trail = annotationTrail(run.template, steps)
            base = trail[0].parent if trail else run.template
            self._storePortableRevision(connection, base, digestion.ancestryGaps)
            for step in trail:
                self._storeAnnotationStep(
                    connection, step.parent, step.revision, isNameOnly=step.isNameOnly,
                )
            self._storeRestrictionRun(connection, run, encodeRestrictionRun(run), certify=False)
        self._storeRestrictionView(
            connection, digestion.result, digestion.view, updateExisting=updateView,
        )
        self._verifyStoredRuns(connection)

    def _storeDigestionAnnotations(
        self, connection: _ProjectConnection, digestion: SavedDigestion,
        annotations: tuple[AnnotationEvidence, ...],
    ) -> None:
        """Store annotation edits and available source captures for the digestion templates."""
        from strandfolio.storage.projects import _key

        templates = {run.template.revisionId for run in (*digestion.ancestorRuns, digestion.result)}
        for evidence in annotations:
            if evidence.history[-1].revisionId in templates:
                self._validateAnnotationEvidence(evidence, evidence.history[-1])
                self._storeAnnotationEvidence(
                    connection, evidence, f"restriction-edit-{_key(digestion.result.operationId)}",
                )

    def _storePortableSource(
        self, connection: sqlite3.Connection, source: DigestionSourceArtifact,
    ) -> None:
        self._recordArtifact(connection, source.artifact, source.formatName, "original")
        artifactId = source.artifact.artifactId
        intake = connection.execute("SELECT 1 FROM intakes WHERE artifact_id = ?", (artifactId,))
        if intake.fetchone() is None:
            connection.execute("INSERT OR IGNORE INTO portable_sources VALUES (?)", (artifactId,))

    def _storePortableIntake(self, connection: sqlite3.Connection, revision: SequenceRevision) -> None:
        from strandfolio.storage.projects import ProjectError

        self._storePortableRevision(connection, revision, ())
        fields = (revision.sourceArtifactId, revision.sourceOrdinal)
        existing = connection.execute(
            "SELECT revision_id FROM intake_revisions WHERE artifact_id = ? AND source_ordinal = ? "
            "UNION ALL SELECT revision_id FROM portable_intake_revisions "
            "WHERE artifact_id = ? AND source_ordinal = ?", (*fields, *fields),
        ).fetchone()
        if existing is not None and existing[0] != revision.revisionId:
            raise ProjectError(f"Conflicting intake revision at source ordinal {revision.sourceOrdinal}.")
        if existing is None:
            connection.execute(
                "INSERT INTO portable_intake_revisions VALUES (?, ?, ?)",
                (*fields, revision.revisionId),
            )

    def _storePortableRevision(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
        gaps: tuple[OutputReference, ...],
    ) -> None:
        from strandfolio.storage.projects import ProjectError

        existing = self._firstRecordRevision(connection, revision.recordId)
        if (existing is not None
                and renamedRevisionPayload(existing) != renamedRevisionPayload(revision)):
            raise ProjectError(f"Conflicting record ID {revision.recordId!r} in portable evidence.")
        if revision.sourceArtifactId is not None:
            self._checkPortableSourceRevision(connection, revision)
        reference = revision.outputReference
        self._storeRevision(connection, revision, isPortableEvidence=True)
        if reference is not None:
            candidate = self._portableGeneratingProduct(connection, reference, gaps)
            if candidate is not None and not matchesRecordedProduct(revision, candidate):
                raise ProjectError("Portable source revision differs from its generating output.")
            if candidate is None or candidate.recordId != revision.recordId:
                connection.execute(
                    "INSERT OR IGNORE INTO restriction_origins VALUES (?, ?, ?, ?)",
                    (revision.recordId, reference.operationId, reference.outputKey, candidate is None),
                )

    def _portableGeneratingProduct(
        self, connection: sqlite3.Connection, reference: OutputReference,
        gaps: tuple[OutputReference, ...],
    ) -> SequenceRevision | None:
        from strandfolio.storage.projects import ProjectError

        try:
            revision, _, _ = self._loadGeneratingOutput(connection, reference)
        except KeyError as error:
            if reference in gaps:
                return None
            raise ProjectError(f"Missing generating run {reference.operationId!r}.") from error
        return revision

    @staticmethod
    def _checkPortableSourceRevision(connection: sqlite3.Connection, revision: SequenceRevision) -> None:
        from strandfolio.storage.projects import ProjectError

        fields = (revision.sourceArtifactId, revision.sourceOrdinal)
        records = {row[0] for row in connection.execute(
            "SELECT record_id FROM revisions WHERE source_artifact_id = ? AND source_ordinal = ? "
            "AND revision_id NOT IN (SELECT child_revision_id FROM annotation_steps)", fields,
        )}
        if records and records != {revision.recordId}:
            raise ProjectError(f"Conflicting portable source record ID at ordinal {revision.sourceOrdinal}.")
        if connection.execute("SELECT 1 FROM intakes WHERE artifact_id = ?", (fields[0],)).fetchone():
            if connection.execute(
                "SELECT 1 FROM intake_revisions WHERE artifact_id = ? AND source_ordinal = ?", fields,
            ).fetchone() is None:
                raise ProjectError(f"Portable source ordinal {revision.sourceOrdinal} is outside the intake.")
