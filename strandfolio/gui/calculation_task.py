"""Run a revision-bound calculation with cooperative cancellation."""

from collections.abc import Callable
import logging
import sqlite3
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class _TaskSignals(QObject):
    """Deliver the worker's outcome to the Qt coordinator."""

    completed = Signal(int, object, object)


class CalculationTask(QRunnable):
    """Run background work without touching Qt view state."""

    def __init__(
        self, requestId: int, calculate: Callable[[Callable[[], bool]], object],
        *, failureContext: str = "calculation",
    ) -> None:
        super().__init__()
        self.requestId = requestId
        self._calculate = calculate
        self._failureContext = failureContext
        self._canceled = Event()
        self.signals = _TaskSignals()

    def cancel(self) -> None:
        """Ask the calculation to stop at its next cancellation check."""
        self._canceled.set()

    @Slot()
    def run(self) -> None:
        result = None
        error = None
        if not self._canceled.is_set():
            try:
                result = self._calculate(self._canceled.is_set)
            except (OSError, ValueError, KeyError, sqlite3.Error) as problem:
                error = str(problem)
            except Exception:
                summary = f"Unexpected {self._failureContext} failure"
                logging.exception(summary)
                error = f"{summary}. See the application log."
        self.signals.completed.emit(self.requestId, result, error)
