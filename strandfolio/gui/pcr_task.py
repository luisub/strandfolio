"""Queue independent project reads and ordered analysis writes."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QThreadPool, Slot

from strandfolio.gui.calculation_task import CalculationTask


class ProjectTasks(QObject):
    """Keep SQLite reads and ordered writes away from the Qt event loop."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._readPool = QThreadPool(self)
        self._readPool.setMaxThreadCount(1)
        self._writePool = QThreadPool(self)
        self._writePool.setMaxThreadCount(1)
        self._requestId = 0
        self._work: dict[int, tuple[CalculationTask, Callable[[object, str | None], None]]] = {}

    def submit(
        self, calculate: Callable[[], object], publish: Callable[[object, str | None], None],
        *, write: bool = False,
    ) -> None:
        self._requestId += 1
        worker = CalculationTask(self._requestId, lambda _isCanceled: calculate())
        worker.signals.completed.connect(self._completed)
        self._work[self._requestId] = worker, publish
        pool = self._writePool if write else self._readPool
        pool.start(worker)

    @Slot(int, object, object)
    def _completed(self, requestId: int, value: object, error: str | None) -> None:
        _, publish = self._work.pop(requestId)
        publish(value, error)
