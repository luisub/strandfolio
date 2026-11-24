"""Trace recorded digestions and PCR runs into a sequence ancestry view."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from strandfolio.domain.coordinates import SelectionSegment
from strandfolio.domain.fragments import DNAFragment
from strandfolio.domain.primers import PrimerRevision
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.pcr import PcrProduct, PcrRun, PcrSourceRole
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.storage.project_edits import AnnotationStep, validateAnnotationStep
from strandfolio.storage.projects import ProjectError, ProjectRepository
from strandfolio.storage.revision_codec import matchesRecordedProduct, sameRevisionSnapshot


HistoryStatus = Literal["source", "recorded", "missing", "malformed", "cycle", "unsupported"]
HistoryOperation = Literal["digest", "pcr", "annotation", "name"]
HistoryRole = PcrSourceRole | Literal["parent"]
RecordedRun = RestrictionDigest | PcrRun


class HistoryCanceled(Exception):
    """Stop a history request before returning a partial view."""


@dataclass(frozen=True, slots=True)
class HistorySpan:
    """One ordered half-open mapping in the stored source and product axes."""

    sourceSpan: SelectionSegment
    productSpan: SelectionSegment
    orientation: Literal[1, -1]


@dataclass(frozen=True, slots=True)
class HistoryInput:
    """One source role and its ordered spans, including overlaps in PCR evidence."""

    role: HistoryRole
    sourceRevisionId: str
    sourceName: str
    spans: tuple[HistorySpan, ...]


@dataclass(frozen=True, slots=True)
class HistoryTrace:
    """A terminal contribution mapped onto the requested product's stored axis."""

    role: HistoryRole
    sourceRevisionId: str
    sourceName: str
    span: HistorySpan
    status: HistoryStatus


@dataclass(frozen=True, slots=True)
class HistoryNode:
    """A recorded revision and the operation that produced it or the reason tracing stopped."""

    revision: SequenceRevision
    reference: OutputReference | None
    status: HistoryStatus
    detail: str
    isPersisted: bool
    operation: HistoryOperation | None = None
    inputs: tuple[HistoryInput, ...] = ()
    candidateRevisionId: str | None = None
    engineVersion: str | None = None
    createdAt: str | None = None
    settings: tuple[tuple[str, str], ...] = ()
    canOpenSnapshot: bool = True


@dataclass(frozen=True, slots=True)
class RecordedHistory:
    """Sequence revisions in ancestor-first order. Primer uses are attached to their operations."""

    rootRevisionId: str
    nodes: tuple[HistoryNode, ...]

    def node(self, revisionId: str) -> HistoryNode:
        """Look up an exact sequence snapshot; raise KeyError for an absent revision."""
        for node in self.nodes:
            if node.revision.revisionId == revisionId:
                return node
        raise KeyError(revisionId)

    def trace(
        self, revisionId: str, productSpan: SelectionSegment,
        *, isCanceled: Callable[[], bool] | None = None,
    ) -> tuple[HistoryTrace, ...]:
        """Compose recorded intervals through earlier generations onto one product axis.

        ``productSpan`` is a nonempty zero-based half-open interval on the specified
        revision. Primer and template evidence can overlap. Gaps end at the last
        recorded snapshot and retain its evidence status. Cancellation raises
        HistoryCanceled without returning partial intervals.
        """
        _checkCancellation(isCanceled)
        start = self.node(revisionId)
        if type(productSpan) is not tuple or len(productSpan) != 2:
            raise ValueError("Trace interval needs two product boundaries.")
        lower, upper = productSpan
        if (type(lower) is not int or type(upper) is not int
                or not 0 <= lower < upper <= len(start.revision)):
            raise ValueError("Trace interval must be nonempty and within the selected revision.")
        byRevision = {node.revision.revisionId: node for node in self.nodes}
        pending = [(start, HistorySpan(productSpan, productSpan, 1))]
        terminal = []
        while pending:
            _checkCancellation(isCanceled)
            node, projection = pending.pop()
            if node.status != "recorded":
                terminal.append(HistoryTrace(
                    "template", node.revision.revisionId, node.revision.name, projection, node.status,
                ))
                continue
            for source in node.inputs:
                for mapping in source.spans:
                    span = _intersectMapping(projection, mapping)
                    if span is None:
                        continue
                    if source.role in ("template", "parent"):
                        pending.append((byRevision[source.sourceRevisionId], span))
                    else:
                        terminal.append(HistoryTrace(
                            source.role, source.sourceRevisionId, source.sourceName, span, "recorded",
                        ))
        traces = tuple(sorted(terminal, key=lambda source: source.span.productSpan))
        _checkCancellation(isCanceled)
        return traces


def _checkCancellation(isCanceled: Callable[[], bool] | None) -> None:
    if isCanceled is not None and isCanceled():
        raise HistoryCanceled()


def _intersectMapping(projection: HistorySpan, mapping: HistorySpan) -> HistorySpan | None:
    """Compose two mappings over the interval where their shared coordinates overlap."""
    start = max(projection.sourceSpan[0], mapping.productSpan[0])
    end = min(projection.sourceSpan[1], mapping.productSpan[1])
    if start >= end:
        return None
    sourceSpan = _correspondingSpan(
        start, end, mapping.productSpan[0], mapping.sourceSpan, mapping.orientation,
    )
    productSpan = _correspondingSpan(
        start, end, projection.sourceSpan[0], projection.productSpan, projection.orientation,
    )
    orientation = 1 if projection.orientation == mapping.orientation else -1
    return HistorySpan(sourceSpan, productSpan, orientation)


def _correspondingSpan(
    start: int, end: int, origin: int, corresponding: SelectionSegment, orientation: Literal[1, -1],
) -> SelectionSegment:
    lower, upper = corresponding
    if orientation == 1:
        return lower + start - origin, lower + end - origin
    return upper - (end - origin), upper - (start - origin)


def _digestInput(run: RestrictionDigest, product: DNAFragment) -> tuple[HistoryInput, ...]:
    if product.sourceRevisionId != run.template.revisionId:
        raise ValueError("The fragment source differs from its run's template revision.")
    spans = []
    offset = 0
    for span in product.sourceSpans:
        if span.end > len(run.template):
            raise ValueError("A fragment span crosses its stored source bounds.")
        end = offset + len(span)
        spans.append(HistorySpan((span.start, span.end), (offset, end), 1))
        offset = end
    return (HistoryInput("template", run.template.revisionId, run.template.name, tuple(spans)),)


def _pcrInputs(run: PcrRun, product: PcrProduct) -> tuple[HistoryInput, ...]:
    sources: tuple[tuple[PcrSourceRole, SequenceRevision | PrimerRevision, int], ...] = (
        ("template", run.template, len(run.template)),
        ("forwardPrimer", run.forwardPrimer, len(run.forwardPrimer.sequence)),
        ("reversePrimer", run.reversePrimer, len(run.reversePrimer.sequence)),
    )
    inputs = []
    for role, source, size in sources:
        spans = []
        for mapping in product.sourceMappings:
            if mapping.role != role:
                continue
            if (mapping.sourceRevisionId != source.revisionId or mapping.sourceSpan[1] > size
                    or mapping.productSpan[1] > product.sizeBp):
                raise ValueError("A PCR mapping differs from its recorded source or product.")
            spans.append(HistorySpan(mapping.sourceSpan, mapping.productSpan, mapping.orientation))
        if not spans:
            raise ValueError(f"The PCR product has no mapping for the {role} role.")
        inputs.append(HistoryInput(role, source.revisionId, source.name, tuple(spans)))
    covered = 0
    for start, end in sorted(span.productSpan for source in inputs for span in source.spans):
        if start > covered:
            raise ValueError("The PCR mappings leave an unaccounted product interval.")
        covered = max(covered, end)
    if covered != product.sizeBp:
        raise ValueError("The PCR mappings do not reach the end of the product.")
    return tuple(inputs)


def _describeRun(run: RecordedRun) -> tuple[tuple[str, str], ...]:
    if isinstance(run, RestrictionDigest):
        return (
            ("Enzymes", " + ".join(enzyme.name for enzyme in run.enzymes)),
            ("Catalogue", f"Biopython {run.catalogueRelease.biopythonVersion}"),
            ("Matching policy", run.matchingPolicy),
        )
    return (
        ("Binding mode", run.settings.bindingMode),
        ("Product size range", f"{run.settings.minProductBp:,}–{run.settings.maxProductBp:,} bp"),
    )


def _projectNode(
    node: HistoryNode, run: RecordedRun, isRunPersisted: bool,
) -> tuple[HistoryNode, SequenceRevision | None]:
    reference = node.reference
    assert reference is not None
    isDigest = isinstance(run, RestrictionDigest)
    node = replace(
        node, operation="digest" if isDigest else "pcr", engineVersion=run.engineVersion,
        createdAt=run.createdAt, settings=_describeRun(run),
    )
    if run.status == "unsupported":
        detail = "The recorded operation was unsupported and has no verified product."
        return replace(node, status="unsupported", detail=detail), None
    if run.status != "completed":
        detail = f"The recorded operation has status {run.status!r} and has no completed product."
        return replace(node, status="malformed", detail=detail), None
    products = run.fragments if isDigest else run.products
    outputs = [product for product in products if product.outputKey == reference.outputKey]
    if len(outputs) != 1:
        detail = f"The operation does not resolve to one output with key {reference.outputKey!r}."
        return replace(node, status="malformed", detail=detail), None
    product = outputs[0]
    try:
        if product.operationId != reference.operationId or not matchesRecordedProduct(
            node.revision, product.revision,
        ):
            raise ValueError("The revision differs from the recorded output snapshot.")
        if isinstance(run, RestrictionDigest):
            assert isinstance(product, DNAFragment)
            inputs = _digestInput(run, product)
        else:
            assert isinstance(product, PcrProduct)
            inputs = _pcrInputs(run, product)
    except (TypeError, ValueError) as error:
        return replace(node, status="malformed", detail=str(error)), None
    detail = "Stored project evidence." if isRunPersisted else "Session evidence."
    return replace(
        node, status="recorded", detail=detail, inputs=inputs,
        candidateRevisionId=product.revision.revisionId,
    ), run.template


def _inspectNode(
    revision: SequenceRevision, project: ProjectRepository | None,
    sessionRuns: dict[str, RecordedRun], editSteps: dict[str, AnnotationStep],
    checkpoint: Callable[[], None],
) -> tuple[HistoryNode, SequenceRevision | None]:
    reference = revision.outputReference
    node = HistoryNode(revision, reference, "source", "No generating operation was recorded.", False)
    if project is not None:
        checkpoint()
        try:
            stored = project.getRevision(revision.revisionId)
            if not sameRevisionSnapshot(stored, revision):
                raise ValueError("The revision conflicts with its exact stored snapshot.")
        except KeyError:
            pass
        except (TypeError, ValueError) as error:
            return replace(
                node, status="malformed", detail=str(error), canOpenSnapshot=False,
            ), None
        else:
            node = replace(node, revision=stored, isPersisted=True)
    step = None
    if node.isPersisted and project is not None:
        try:
            step = project.getAnnotationStep(revision.revisionId)
        except (ValueError, KeyError) as error:
            return replace(node, status="malformed", detail=str(error)), None
    if step is None and revision.revisionId in editSteps:
        retained = editSteps[revision.revisionId]
        if not sameRevisionSnapshot(retained.revision, revision):
            detail = "The annotation step conflicts with the exact revision."
            return replace(node, status="malformed", detail=detail), None
        step = retained.parent, retained.isNameOnly
    if step is not None:
        parent, nameOnly = step
        try:
            validateAnnotationStep(parent, revision, isNameOnly=nameOnly)
        except ValueError as error:
            return replace(node, status="malformed", detail=str(error)), None
        axis = (0, len(revision))
        source = HistoryInput("parent", parent.revisionId, parent.name,
                              (HistorySpan(axis, axis, 1),) if len(revision) else ())
        return replace(
            node, reference=None, status="recorded", operation="name" if nameOnly else "annotation",
            inputs=(source,), detail="The DNA or residue sequence is unchanged.",
        ), parent
    if reference is None:
        return node, None
    run = None
    if project is not None:
        checkpoint()
        try:
            run = project.getRecordedRun(reference.operationId)
        except KeyError:
            pass
        except ProjectError as error:
            return replace(node, status="malformed", detail=str(error)), None
    isRunPersisted = run is not None
    if run is None:
        run = sessionRuns.get(reference.operationId)
    if run is None:
        detail = "The generating operation is unavailable in the project or session."
        return replace(node, status="missing", detail=detail), None
    checkpoint()
    return _projectNode(node, run, isRunPersisted)


def resolveHistory(
    revision: SequenceRevision, project: ProjectRepository | None,
    *, runs: tuple[RecordedRun, ...] = (), annotationSteps: tuple[AnnotationStep, ...] = (),
    isCanceled: Callable[[], bool] | None = None,
) -> RecordedHistory:
    """Follow applied annotation edits and exact digest and PCR references without running or saving an operation.

    Stored runs take precedence over retained session values. Missing or invalid
    evidence ends its branch, and a repeated revision ends a cyclic chain.
    Cancellation raises HistoryCanceled without returning a partial view.
    """
    def checkpoint() -> None:
        _checkCancellation(isCanceled)

    rootRevisionId = revision.revisionId
    sessionRuns = {run.operationId: run for run in runs}
    editSteps = {step.revision.revisionId: step for step in annotationSteps}
    nodes = []
    visited = set()
    current: SequenceRevision | None = revision
    while current is not None:
        checkpoint()
        visited.add(current.revisionId)
        node, current = _inspectNode(current, project, sessionRuns, editSteps, checkpoint)
        if current is not None and current.revisionId in visited:
            detail = f"Ancestry returns to revision {current.revisionId!r}."
            nodes.append(replace(node, status="cycle", detail=detail, inputs=()))
            break
        nodes.append(node)
    checkpoint()
    return RecordedHistory(rootRevisionId, tuple(reversed(nodes)))
