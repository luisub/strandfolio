"""Import sequence files and clipboard text on a worker thread."""

import logging
from pathlib import Path
import sqlite3
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from strandfolio.domain.sequence import MoleculeType
from strandfolio.io.formats import (
    PlainTextSource, SequenceFormat, SequenceImportResult, TEXT_EXTENSIONS,
    capturePastedText, captureTextFile, readSequence,
)
from strandfolio.storage.projects import ProjectRepository


# The shared reader bounds file bytes and record count before returning revisions.
MAX_TOTAL_LETTERS = 2_000_000


class ImportSignals(QObject):
    """Deliver worker outcomes; slots on the controller execute on the GUI thread."""

    succeeded = Signal(int, object, bool)
    textTypeNeeded = Signal(int, object)
    normalizationNeeded = Signal(int, object, object)
    failed = Signal(int, str)


class SequenceImportTask(QRunnable):
    """Skip canceled reads and let the controller reject signals from stale requests."""

    def __init__(
        self, requestId: int, source: Path | PlainTextSource | str,
        moleculeType: MoleculeType | None = None,
        formatName: SequenceFormat | None = None, *, lookupProject: ProjectRepository | None = None,
    ) -> None:
        super().__init__()
        self.requestId = requestId
        self._source = source
        self.formatName = formatName
        self.moleculeType = moleculeType
        self._lookupProject = lookupProject
        self.signals = ImportSignals()
        self._canceled = Event()

    def cancel(self) -> None:
        """Skip queued reads and release a canceled capture when an active read returns."""
        self._canceled.set()

    def _readSource(self) -> PlainTextSource | SequenceImportResult:
        source = self._source
        if isinstance(source, str):
            return capturePastedText(source)
        if isinstance(source, PlainTextSource):
            return source
        if self.formatName == "text" or (
            self.formatName is None and source.suffix.lower() in TEXT_EXTENSIONS
        ):
            return captureTextFile(
                source, allowStructured=self.formatName is None, moleculeType=self.moleculeType,
            )
        return readSequence(source, moleculeType=self.moleculeType, formatName=self.formatName)

    @Slot()
    def run(self) -> None:
        if self._canceled.is_set():
            self.signals.failed.emit(self.requestId, "Import canceled.")
            return
        try:
            preview = None
            source = self._readSource()
            if isinstance(source, PlainTextSource):
                if self.moleculeType is None:
                    self.signals.textTypeNeeded.emit(self.requestId, source)
                    return
                result, preview = source.preview(self.moleculeType, maxLetters=MAX_TOTAL_LETTERS)
            else:
                result = source
            if sum(map(len, result.records)) > MAX_TOTAL_LETTERS:
                raise ValueError(
                    f"The viewer supports up to {MAX_TOTAL_LETTERS:,} total sequence letters."
                )
            knownBatch = (
                not self._canceled.is_set() and len(result.records) > 1
                and self._lookupProject is not None
                and self._lookupProject.findMatchingIntake(result) is not None
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            self.signals.failed.emit(self.requestId, str(error))
        except Exception:
            logging.exception("Unexpected sequence import failure")
            self.signals.failed.emit(self.requestId, "Unexpected import failure; see the application log.")
        else:
            if self._canceled.is_set():
                self.signals.failed.emit(self.requestId, "Import canceled.")
            elif preview is not None:
                self.signals.normalizationNeeded.emit(self.requestId, result, preview)
            else:
                self.signals.succeeded.emit(self.requestId, result, knownBatch)
