"""Track background requests and decide which results the workspace can use."""

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskInput:
    """A revision used in one input role, optionally tied to an open tab."""

    role: str
    documentId: str | None
    recordId: str
    revisionId: str
    sessionVersion: int | None = None


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Generation counters for the project and, optionally, the active document."""

    projectVersion: int
    activeVersion: int | None = None
    activeDocumentId: str | None = None
    activeSessionVersion: int | None = None


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """A background request with the inputs, settings, and workspace state used to validate it."""

    requestId: int
    owner: str
    slot: str
    inputs: tuple[TaskInput, ...]
    settings: tuple[Hashable, ...]
    versions: tuple[Hashable, ...]
    bindActive: bool
    context: TaskContext
    description: str
    affectsClose: bool = True


class TaskRegistry:
    """Track the latest request for each owner and slot, and check whether its inputs still match."""

    def __init__(
        self,
        taskContext: Callable[[bool], TaskContext],
        inputIsCurrent: Callable[[TaskInput], bool],
    ) -> None:
        self._taskContext = taskContext
        self._inputIsCurrent = inputIsCurrent
        self._latest: dict[tuple[str, str], TaskRequest] = {}
        self._requestId = 0

    def begin(
        self,
        owner: str,
        slot: str,
        *,
        inputs: Iterable[TaskInput] = (),
        settings: Iterable[Hashable] = (),
        versions: Iterable[Hashable] = (),
        bindActive: bool = False,
        description: str | None = None,
        affectsClose: bool = True,
    ) -> TaskRequest:
        """Replace the latest request, preserving order and repeated input roles."""
        inputTuple, settingsTuple, versionTuple = tuple(inputs), tuple(settings), tuple(versions)
        hash((inputTuple, settingsTuple, versionTuple))
        self._requestId += 1
        request = TaskRequest(
            self._requestId, owner, slot, inputTuple, settingsTuple, versionTuple,
            bindActive, self._taskContext(bindActive), description or slot, affectsClose,
        )
        self._latest[owner, slot] = request
        return request

    def updateContext(
        self,
        owner: str,
        slot: str,
        *,
        inputs: Iterable[TaskInput] = (),
        settings: Iterable[Hashable] = (),
        versions: Iterable[Hashable] = (),
    ) -> bool:
        """Invalidate work when its owner changes any bound role, setting or version."""
        request = self._latest.get((owner, slot))
        if request is None or (request.inputs, request.settings, request.versions) == (
            tuple(inputs), tuple(settings), tuple(versions),
        ):
            return False
        return self.cancel(owner, slot)

    def isCurrent(self, request: TaskRequest) -> bool:
        """Check the request object, its workspace context, and each input revision."""
        return (
            self._latest.get((request.owner, request.slot)) is request
            and self._taskContext(request.bindActive) == request.context
            and all(self._inputIsCurrent(item) for item in request.inputs)
        )

    def finish(self, request: TaskRequest) -> bool:
        """Remove the latest request for this slot and report whether its result is still valid."""
        if self._latest.get((request.owner, request.slot)) is not request:
            return False
        isCurrent = self.isCurrent(request)
        self.cancel(request.owner, request.slot)
        return isCurrent

    def cancel(self, owner: str, slot: str) -> bool:
        """Invalidate a pending request even if its worker cannot stop immediately."""
        return self._latest.pop((owner, slot), None) is not None

    def pendingFor(self, documents: frozenset[str] | None = None) -> tuple[TaskRequest, ...]:
        """Return current work affected by closure of the given live documents."""
        return tuple(
            request for request in self._latest.values()
            if request.affectsClose and self.isCurrent(request) and (
                documents is None
                or request.context.activeDocumentId in documents
                or any(item.documentId in documents for item in request.inputs)
            )
        )
