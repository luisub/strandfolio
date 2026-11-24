"""Write the evidence displayed for one active revision as a read-only JSON report."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from strandfolio.domain.sequence import SequenceRevision
from strandfolio.io.formats import (
    checkExportDestination, exportDestinationVersion, publishStagedExport,
)
from strandfolio.rendering.history import HistoryNode, RecordedHistory
from strandfolio.storage.revision_codec import sameRevisionSnapshot

if TYPE_CHECKING:
    from strandfolio.storage.projects import ProjectRepository


def _describeRevision(revision: SequenceRevision) -> dict:
    return {
        "revision_id": revision.revisionId,
        "record_id": revision.recordId,
        "name": revision.name,
        "molecule_type": revision.moleculeType,
        "length": len(revision),
        "content_sha256": revision.contentDigest,
        "source_artifact_id": revision.sourceArtifactId,
        "source_ordinal": revision.sourceOrdinal,
        "source_format": revision.sourceFormat,
    }


def _reportNode(node: HistoryNode) -> dict:
    reference = node.reference
    return {
        **_describeRevision(node.revision),
        "project_snapshot": node.isPersisted,
        "snapshot_available": node.canOpenSnapshot,
        "evidence_status": node.status,
        "evidence_detail": node.detail,
        "operation": node.operation,
        "operation_id": reference.operationId if reference is not None else None,
        "output_key": reference.outputKey if reference is not None else None,
        "recorded_output_revision_id": node.candidateRevisionId,
        "recorded_at": node.createdAt,
        "engine_version": node.engineVersion,
        "displayed_settings": dict(node.settings),
        "inputs": [
            {
                "role": item.role,
                "source_revision_id": item.sourceRevisionId,
                "source_name": item.sourceName,
                "spans": [
                    {"source": span.sourceSpan, "product": span.productSpan,
                     "orientation": span.orientation}
                    for span in item.spans
                ],
            }
            for item in node.inputs
        ],
    }


def writeHistoryReport(
    history: RecordedHistory, destination: str | Path, *,
    project: ProjectRepository | None = None,
    protectedPaths: tuple[Path, ...] = (), ensureCurrent: Callable[[], None] | None = None,
) -> Path:
    """Atomically export ordered revision identities, interval evidence and gaps.

    Sequence letters and machine-local source paths are omitted. The report uses
    zero-based half-open coordinates on the recorded source and product axes.
    User-reported links are listed separately from automatic evidence. A source
    without a recorded operation keeps that status. The optional workspace
    check guards the active revision before publication. Original source paths
    and managed project files are protected from replacement.
    """
    from strandfolio.storage.projects import _syncDescriptor, _syncDirectory

    requested = Path(destination)
    target = requested.parent.resolve() / requested.name
    if target.suffix.lower() != ".json":
        raise ValueError("Use a .json filename for the provenance report.")
    approvedVersion = exportDestinationVersion(target)
    automatic = {node.revision.revisionId: node.revision for node in history.nodes}
    originals = {revision.sourcePath for revision in automatic.values() if revision.sourcePath}
    available = (node.revision.revisionId for node in history.nodes if node.canOpenSnapshot)
    declared = project.declaredParentGraph(available) if project is not None else {}
    reported = []
    if project is not None:
        revisionIds = set(declared)
        for parents in declared.values():
            revisionIds.update(parent.revisionId for parent in parents)
        for revisionId in sorted(revisionIds):
            stored = project.getRevision(revisionId)
            if revisionId not in automatic:
                reported.append(_describeRevision(stored))
                if stored.sourcePath is not None:
                    originals.add(stored.sourcePath)
            elif not sameRevisionSnapshot(stored, automatic[revisionId]):
                raise ValueError(f"Reported parentage conflicts with revision {revisionId!r}.")
    protected = (*originals, *protectedPaths)

    def checkTarget() -> None:
        if ensureCurrent is not None:
            ensureCurrent()
        checkExportDestination(target, protected)
        if project is not None:
            project.checkFigureDestination(target)

    checkTarget()
    document = {
        "file_kind": "vibegene.provenance-report",
        "schema_version": 1,
        "root_revision_id": history.rootRevisionId,
        "coordinate_system": "zero-based half-open",
        "evidence_gaps": sum(node.status not in ("recorded", "source") for node in history.nodes),
        "revisions": [_reportNode(node) for node in history.nodes],
        "additional_reported_revisions": reported,
        "user_reported_parentage": {
            childId: [
                {"parent_revision_id": parent.revisionId, "source_span": parent.sourceSpan,
                 "product_span": parent.productSpan, "orientation": parent.orientation}
                for parent in parents
            ]
            for childId, parents in declared.items()
        },
    }
    with TemporaryDirectory(dir=target.parent, prefix=".strandfolio-history-") as directory:
        staged = Path(directory) / "report.json"
        with staged.open("w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            _syncDescriptor(handle.fileno())
        checkTarget()
        publishStagedExport(staged, target, approvedVersion)
    if getattr(os, "O_DIRECTORY", None) is not None:
        try:
            _syncDirectory(target.parent)
        except OSError as error:
            raise OSError(f"The report was written. The destination folder sync failed. {error}") from error
    return target
