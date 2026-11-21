"""Keep digestion results and source records available in the workspace."""

from collections.abc import Iterable
from dataclasses import replace
from itertools import chain
import json

from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.digestion import DigestionSourceArtifact, SavedDigestion
from strandfolio.io.formats import SequenceImportResult
from strandfolio.operations.gel import GelViewSettings
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.storage.project_edits import (
    AnnotationEvidence, AnnotationStep, annotationBase, annotationTrail, validateRecordOwnership,
)
from strandfolio.storage.restriction_codec import encodeRestrictionRun
from strandfolio.storage.revision_codec import (
    encodeRevision, matchesRecordedProduct, sameRevisionSnapshot,
)


class DigestionSession:
    """Keep accepted digestions by operation ID and support opening their source revisions."""

    def __init__(self) -> None:
        self._runs: dict[str, SavedDigestion] = {}

    @property
    def runs(self) -> tuple[RestrictionDigest, ...]:
        return tuple(value.result for value in self._runs.values())

    @property
    def retainedAnnotationSteps(self) -> tuple[AnnotationStep, ...]:
        """Collect the verified annotation links carried by retained portable digestions."""
        steps = {step.revision.revisionId: step
                 for bundle in self._runs.values() for step in bundle.annotationSteps}
        return tuple(steps.values())

    def get(self, operationId: str) -> SavedDigestion | None:
        digestion = self._runs.get(operationId)
        if digestion is None:
            return None
        # A subsequent portable import may supply a previously unresolved ancestor.
        while digestion.ancestryGaps:
            reference, = digestion.ancestryGaps
            parent = self._runs.get(reference.operationId)
            if parent is None:
                break
            digestion = replace(
                digestion,
                ancestorRuns=(*parent.ancestorRuns, parent.result, *digestion.ancestorRuns),
                sourceArtifacts=(*parent.sourceArtifacts, *digestion.sourceArtifacts),
                ancestryGaps=parent.ancestryGaps,
                intakeRevisions=(*parent.intakeRevisions, *digestion.intakeRevisions),
                annotationSteps=(*parent.annotationSteps, *digestion.annotationSteps),
            )
        return digestion

    def sourceBundle(self, revisionId: str) -> SavedDigestion | None:
        return next((
            value for value in self._runs.values()
            if any(item.revisionId == revisionId for item in self._snapshots(value))
        ), None)

    @staticmethod
    def _snapshots(digestion: SavedDigestion) -> Iterable[SequenceRevision]:
        yield from digestion.intakeRevisions
        for step in digestion.annotationSteps:
            yield step.parent
            yield step.revision
        for run in (*digestion.ancestorRuns, digestion.result):
            yield run.template
            yield from (fragment.revision for fragment in run.fragments)

    def snapshot(self, revisionId: str) -> SequenceRevision | None:
        bundle = self.sourceBundle(revisionId)
        if bundle is None:
            return None
        return next(item for item in self._snapshots(bundle) if item.revisionId == revisionId)

    def build(
        self, result: RestrictionDigest, view: GelViewSettings, capture: SequenceImportResult | None,
        annotationEvidence: AnnotationEvidence | None = None,
    ) -> SavedDigestion:
        """Include any earlier runs and the selected original record in the saved digestion."""
        template = result.template
        prior = self.sourceBundle(template.revisionId)
        indexedSteps = {
            step.revision.revisionId: step for step in prior.annotationSteps
        } if prior is not None else {}
        if annotationEvidence is not None:
            history = annotationEvidence.history
            indexedSteps.update((child.revisionId, AnnotationStep(parent, child))
                                for parent, child in zip(history, history[1:]))
            capture = annotationEvidence.capture or capture
        trail = annotationTrail(template, indexedSteps)
        base = trail[0].parent if trail else template
        reference = base.outputReference
        parent = self.get(reference.operationId) if reference is not None else None
        if parent is not None:
            return SavedDigestion(
                result, view, (*parent.ancestorRuns, parent.result),
                parent.sourceArtifacts, parent.ancestryGaps, parent.intakeRevisions,
                (*parent.annotationSteps, *trail),
            )
        sources = ()
        intake = ()
        if capture is not None:
            sources = (DigestionSourceArtifact(capture.source, capture.formatName),)
            original = capture.records[base.sourceOrdinal - 1]
            if original.revisionId != base.revisionId:
                intake = (original,)
        elif template.sourceArtifactId is not None:
            source = self.sourceBundle(template.revisionId)
            if source is not None:
                sources = tuple(
                    value for value in source.sourceArtifacts
                    if value.artifact.artifactId == template.sourceArtifactId
                )
                intake = tuple(
                    value for value in source.intakeRevisions
                    if value.sourceArtifactId == template.sourceArtifactId
                )
        return SavedDigestion(
            result, view, sourceArtifacts=sources,
            ancestryGaps=() if reference is None else (reference,), intakeRevisions=intake,
            annotationSteps=trail,
        )

    def checkCompatible(
        self, digestion: SavedDigestion, snapshots: Iterable[SequenceRevision] = (),
        captures: Iterable[SequenceImportResult] = (),
        annotationSteps: Iterable[AnnotationStep] = (),
    ) -> None:
        """Reject a conflicting portable import before mutating the session or project."""
        combined = {operationId: value.result for operationId, value in self._runs.items()}
        for run in (*digestion.ancestorRuns, digestion.result):
            cached = combined.get(run.operationId)
            if cached is not None and encodeRestrictionRun(cached) != encodeRestrictionRun(run):
                raise ValueError(f"Conflicting restriction operation ID {run.operationId!r}.")
            combined[run.operationId] = run
        steps = {step.revision.revisionId: step for step in annotationSteps}
        for value in (*self._runs.values(), digestion):
            for step in value.annotationSteps:
                previous = steps.setdefault(step.revision.revisionId, step)
                if (not sameRevisionSnapshot(previous.parent, step.parent)
                        or not sameRevisionSnapshot(previous.revision, step.revision)
                        or previous.isNameOnly != step.isNameOnly):
                    raise ValueError(f"Conflicting annotation history for {step.revision.revisionId!r}.")
        self._checkParents(combined, steps)
        bundled = (item for value in (*self._runs.values(), digestion)
                   for item in self._snapshots(value))
        edited = (item for step in steps.values() for item in (step.parent, step.revision))
        existing = {}
        for revision in chain(snapshots, bundled, edited):
            prior = existing.setdefault(revision.revisionId, revision)
            if not sameRevisionSnapshot(prior, revision):
                raise ValueError(f"Conflicting revision ID {revision.revisionId!r}.")
        validateRecordOwnership(existing.values(), steps)
        self._checkSnapshotOwners(existing.values(), steps)
        descriptors = {
            source.artifact.artifactId: source
            for value in self._runs.values()
            for source in value.sourceArtifacts
        }
        descriptors.update((capture.source.artifactId, DigestionSourceArtifact(
            capture.source, capture.formatName,
        )) for capture in captures)
        for source in digestion.sourceArtifacts:
            existing = descriptors.get(source.artifact.artifactId)
            if existing is not None and existing != source:
                raise ValueError(f"Conflicting source artifact ID {source.artifact.artifactId!r}.")

    @staticmethod
    def _checkSnapshotOwners(
        snapshots: Iterable[SequenceRevision], steps: dict[str, AnnotationStep],
    ) -> None:
        features, origins = {}, {}
        for revision in snapshots:
            base = annotationBase(revision, steps)
            encoded = json.loads(encodeRevision(base))
            if base.sourceArtifactId is not None:
                origin = (base.sourceArtifactId, base.sourceOrdinal)
                if origins.setdefault(origin, base.recordId) != base.recordId:
                    raise ValueError(f"Conflicting source record ID at ordinal {base.sourceOrdinal}.")
            for featureId, feature in zip(encoded["feature_ids"], encoded["features"][1], strict=True):
                owner = (base.recordId, feature)
                if features.setdefault(featureId, owner) != owner:
                    raise ValueError(f"Conflicting feature ID {featureId!r}.")

    @staticmethod
    def _checkParents(runs: dict[str, RestrictionDigest], steps: dict[str, AnnotationStep]) -> None:
        complete = set()
        for operationId in runs:
            path = set()
            while operationId in runs and operationId not in complete:
                if operationId in path:
                    raise ValueError(f"Circular restriction ancestry at {operationId!r}.")
                path.add(operationId)
                template = annotationBase(runs[operationId].template, steps)
                reference = template.outputReference
                if reference is None or reference.operationId not in runs:
                    break
                operationId = reference.operationId
                candidate = next((
                    fragment.revision for fragment in runs[operationId].fragments
                    if fragment.outputKey == reference.outputKey
                ), None)
                if candidate is None:
                    raise ValueError(f"Unknown generating restriction output {reference.outputKey!r}.")
                if not matchesRecordedProduct(template, candidate):
                    raise ValueError("The recorded source differs from its generating restriction output.")
            complete.update(path)

    def retain(self, digestion: SavedDigestion) -> None:
        """Record an accepted result and register any previously unknown ancestry."""
        indexSteps = {step.revision.revisionId: step for step in digestion.annotationSteps}
        for index, run in enumerate(digestion.ancestorRuns):
            if run.operationId not in self._runs:
                view = replace(digestion.view, selectedOutputKey=None)
                neededSteps = {
                    step.revision.revisionId: step
                    for ancestor in digestion.ancestorRuns[:index + 1]
                    for step in annotationTrail(ancestor.template, indexSteps)
                }
                self._runs[run.operationId] = replace(
                    digestion, result=run, view=view, ancestorRuns=digestion.ancestorRuns[:index],
                    annotationSteps=tuple(neededSteps.values()),
                )
        self._runs[digestion.result.operationId] = digestion
