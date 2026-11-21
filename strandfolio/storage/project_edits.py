"""Store annotation edits and link each edit to its previous revision."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import sqlite3

from strandfolio.domain.features import ANNOTATION_DECISIONS_KEY
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.formats import SequenceImportResult
from strandfolio.storage.revision_codec import (
    encodeRevision, renamedRevisionPayload, sameRevisionSnapshot,
)


ANNOTATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS annotation_steps (
    child_revision_id TEXT PRIMARY KEY NOT NULL REFERENCES revisions (revision_id),
    parent_revision_id TEXT NOT NULL REFERENCES revisions (revision_id),
    is_name_only INTEGER NOT NULL CHECK (is_name_only IN (0, 1)),
    CHECK (child_revision_id <> parent_revision_id)
)
"""


@dataclass(frozen=True, slots=True)
class AnnotationEvidence:
    """Ordered applied edit snapshots and an optional exact capture for their base."""

    history: tuple[SequenceRevision, ...]
    capture: SequenceImportResult | None = None
    independent: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationStep:
    """A stored metadata edit between exact immutable snapshots."""

    parent: SequenceRevision
    revision: SequenceRevision
    isNameOnly: bool = False


def annotationBase(
    revision: SequenceRevision, steps: dict[str, AnnotationStep],
) -> SequenceRevision:
    """Return the revision that precedes the annotation edits, or the input if there are no edits."""
    trail = annotationTrail(revision, steps)
    return trail[0].parent if trail else revision


def annotationTrail(
    revision: SequenceRevision, steps: dict[str, AnnotationStep],
) -> tuple[AnnotationStep, ...]:
    """Select the exact steps used by this revision in ancestor-first order."""
    trail = []
    seen = set()
    while (step := steps.get(revision.revisionId)) is not None:
        if revision.revisionId in seen:
            raise ValueError(f"Circular annotation ancestry at revision {revision.revisionId!r}.")
        seen.add(revision.revisionId)
        trail.append(step)
        revision = step.parent
    return tuple(reversed(trail))


def validateRecordOwnership(
    snapshots: Iterable[SequenceRevision], steps: Mapping[str, AnnotationStep],
) -> None:
    """Check record roots after verifying snapshot identities and annotation edges.

    An annotation descendant starts a new record at exactly one edge. Independent
    roots can include equivalent snapshots with different names, such as a recorded
    output and its renamed input snapshot.
    """
    roots: dict[str, SequenceRevision] = {}
    for revision in snapshots:
        step = steps.get(revision.revisionId)
        if step is not None and step.parent.recordId == revision.recordId:
            continue
        previous = roots.setdefault(revision.recordId, revision)
        if previous.revisionId == revision.revisionId:
            continue
        if (step is not None or previous.revisionId in steps
                or renamedRevisionPayload(previous) != renamedRevisionPayload(revision)):
            raise ValueError(f"Conflicting record ID {revision.recordId!r}.")


def validateAnnotationStep(
    parent: SequenceRevision, child: SequenceRevision, *, isNameOnly: bool,
) -> None:
    """Require an immutable name change or annotation-only change on the same bases."""
    from strandfolio.storage.projects import ProjectError

    previous = json.loads(encodeRevision(parent))
    current = json.loads(encodeRevision(child))
    for payload in (previous, current):
        del payload["revision_id"], payload["content_digest"]
        if isNameOnly:
            del payload["name"]
            continue
        del payload["record_id"], payload["features"], payload["feature_ids"], payload["schema"]
        payload.pop("output_reference", None)
        if payload.get("duplex_geometry") is None:
            payload.pop("duplex_geometry", None)
        payload["annotations"] = [
            entry for entry in payload["annotations"] if entry[0] != ANNOTATION_DECISIONS_KEY
        ]
    if (previous != current or parent.revisionId == child.revisionId
            or parent.contentDigest == child.contentDigest
            or (not isNameOnly and child.outputReference is not None)):
        raise ProjectError(f"Invalid annotation step to revision {child.revisionId!r}.")


class ProjectEditMixin:
    """Read and write revision links for annotation edits."""

    def getAnnotationStep(self, revisionId: str) -> tuple[SequenceRevision, bool] | None:
        """Load the prior revision and report whether the change was a save under a name."""
        from strandfolio.storage.projects import _projectHeader

        with self._read() as connection:
            if _projectHeader(connection) < 6:
                return None
            step = self._loadAnnotationStep(connection, revisionId)
            return None if step is None else (self._loadRevision(connection, step[0]), bool(step[1]))

    def getAnnotationParent(self, revisionId: str) -> SequenceRevision | None:
        """Load the immediate predecessor if this snapshot has an annotation history."""
        step = self.getAnnotationStep(revisionId)
        return step[0] if step is not None else None

    def getAnnotationHistory(self, revisionId: str) -> tuple[SequenceRevision, ...]:
        """Load annotation ancestry and the requested revision in oldest-first order."""
        with self._read() as connection:
            revision = self._loadRevision(connection, revisionId)
            steps = self._loadAnnotationTrail(connection, revision)
            return (*[step.parent for step in steps], revision)

    def annotationStepsFor(self, revisions: Iterable[SequenceRevision]) -> tuple[AnnotationStep, ...]:
        """Load and check annotation history in a read transaction."""
        from strandfolio.storage.projects import ProjectError, _projectHeader

        with self._read() as connection:
            if _projectHeader(connection) < 6:
                return ()
            steps = {}
            for revision in revisions:
                if self._loadAnnotationStep(connection, revision.revisionId) is None:
                    continue
                stored = self._loadRevision(connection, revision.revisionId)
                if not sameRevisionSnapshot(stored, revision):
                    raise ProjectError(f"Conflicting revision ID {revision.revisionId!r}.")
                steps.update((step.revision.revisionId, step)
                             for step in self._loadAnnotationTrail(connection, stored))
            return tuple(steps.values())

    def _loadAnnotationTrail(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
    ) -> tuple[AnnotationStep, ...]:
        from strandfolio.storage.projects import ProjectError, _projectHeader

        if _projectHeader(connection) < 6:
            return ()
        steps = []
        seen = {revision.revisionId}
        while (step := self._loadAnnotationStep(connection, revision.revisionId)) is not None:
            parentId = step[0]
            if parentId in seen:
                raise ProjectError(f"Circular annotation ancestry at revision {parentId!r}.")
            seen.add(parentId)
            parent = self._loadRevision(connection, parentId)
            steps.append(AnnotationStep(parent, revision, bool(step[1])))
            revision = parent
        return tuple(reversed(steps))

    @staticmethod
    def _loadAnnotationStep(connection: sqlite3.Connection, revisionId: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT parent_revision_id, is_name_only FROM annotation_steps WHERE child_revision_id = ?",
            (revisionId,),
        ).fetchone()

    @staticmethod
    def _validateAnnotationEvidence(
        evidence: AnnotationEvidence, revision: SequenceRevision,
    ) -> None:
        from strandfolio.storage.projects import ProjectError, _validateCapture

        history = evidence.history
        if (len(history) < 2 or not sameRevisionSnapshot(history[-1], revision)
                or len({item.revisionId for item in history}) != len(history)):
            raise ProjectError("Annotation evidence does not match the requested revision.")
        if type(evidence.independent) is not bool or (evidence.independent and evidence.capture is None):
            raise ProjectError("An independent annotation intake requires its exact capture.")
        capture = evidence.capture
        if capture is not None:
            _validateCapture(capture)
            base = history[0]
            ordinal = base.sourceOrdinal
            if (ordinal is None or ordinal > len(capture.records)
                    or not sameRevisionSnapshot(capture.records[ordinal - 1], base)):
                raise ProjectError("Annotation evidence differs from its exact source capture.")
        for parent, child in zip(history, history[1:]):
            validateAnnotationStep(parent, child, isNameOnly=False)

    def _storeAnnotationEvidence(
        self, connection: sqlite3.Connection, evidence: AnnotationEvidence, requestId: str,
    ) -> None:
        parent = evidence.history[0]
        capture = evidence.capture
        if capture is not None:
            known = self._findAnnotationIntake(connection, capture)
            # Keep the capture ID when the draft refers to a separate intake of the same source.
            preserveOccurrence = known is not None and known.source.artifactId != capture.source.artifactId
            _, isNew = self._registerCapture(
                connection, capture, independent=evidence.independent or preserveOccurrence,
                preserveCaptureIdentity=True,
            )
            if isNew:
                self._preserveArtifact(capture.source, capture.captureBytes, requestId)
        if parent.outputReference is not None:
            self._storeOutputAdoptionCandidate(connection, parent)
        else:
            self._storeRevision(connection, parent)
        for revision in evidence.history[1:]:
            self._storeAnnotationStep(connection, parent, revision)
            parent = revision

    def _findAnnotationIntake(
        self, connection: sqlite3.Connection, capture: SequenceImportResult,
    ) -> SequenceImportResult | None:
        from strandfolio.storage.projects import _matchKey

        return self._findMatchingIntake(connection, _matchKey(capture)) if capture.sourceOrigin else None

    def _storeAnnotationStep(
        self, connection: sqlite3.Connection, parent: SequenceRevision, revision: SequenceRevision,
        *, isNameOnly: bool = False,
    ) -> None:
        from strandfolio.storage.projects import ProjectError

        existing = self._loadAnnotationStep(connection, revision.revisionId)
        if existing is None and connection.execute(
            "SELECT 1 FROM revisions WHERE revision_id = ?", (revision.revisionId,),
        ).fetchone() is not None:
            raise ProjectError(f"Revision {revision.revisionId!r} already has different ancestry.")
        if existing is not None and tuple(existing) != (parent.revisionId, int(isNameOnly)):
            raise ProjectError(f"Conflicting annotation ancestry for {revision.revisionId!r}.")
        self._storeRevision(connection, revision, annotationParent=parent)
        if existing is None:
            connection.execute(
                "INSERT INTO annotation_steps VALUES (?, ?, ?)",
                (revision.revisionId, parent.revisionId, isNameOnly),
            )

    def _annotationProductBase(
        self, connection: sqlite3.Connection, revision: SequenceRevision,
    ) -> SequenceRevision:
        """Resolve changes made before a product's first save within that product record."""
        from strandfolio.storage.projects import _projectHeader

        if _projectHeader(connection) < 6:
            return revision
        while (step := self._loadAnnotationStep(connection, revision.revisionId)) is not None:
            parent = self._loadRevision(connection, step[0])
            if parent.recordId != revision.recordId:
                break
            revision = parent
        return revision
