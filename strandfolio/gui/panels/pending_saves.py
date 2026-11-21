"""Choose a specific retained analysis for a failed project save."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QMenu, QPushButton, QWidget

from strandfolio.operations.pcr import PcrRun
from strandfolio.operations.restriction import RestrictionDigest


class SaveNotice(QLabel):
    """Keep a project recovery outcome on the workflow that requested the save."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setObjectName("notice")
        self.hide()

    def showMessage(self, text: str = "") -> None:
        self.setText(text)
        self.setVisible(bool(text))


class PendingSaveButton(QPushButton):
    """Retry one local run directly or show a source-labeled list of pending saves."""

    retryRequested = Signal(object)

    def __init__(self, label: str, parent: QWidget, *, chooseSource: bool = False) -> None:
        super().__init__(label, parent)
        self._label = label
        self._chooseSource = chooseSource
        self._runs: tuple[PcrRun | RestrictionDigest, ...] = ()
        self._pressedRun: PcrRun | RestrictionDigest | None = None
        self._choices = QMenu(self)
        self.pressed.connect(self._rememberSingle)
        self.clicked.connect(self._retrySingle)
        self.hide()

    def setRuns(self, runs: tuple[PcrRun | RestrictionDigest, ...]) -> None:
        """Bind each action to its captured operation, including equally named sources."""
        if self._runs == runs:
            return
        self._pressedRun = None
        self._runs = runs
        self._choices.close()
        self._choices.clear()
        descriptions = []
        for run in runs:
            kind = "PCR" if isinstance(run, PcrRun) else "Digest"
            description = (f"{kind} · {run.template.name} · revision {run.template.revisionId[:8]}"
                           f" · run {run.operationId[:8]}")
            action = self._choices.addAction(description.replace("&", "&&"))
            action.setData(run.operationId)
            action.triggered.connect(lambda checked=False, selected=run: self.retryRequested.emit(selected))
            descriptions.append(description)
        choose = bool(runs) and (self._chooseSource or len(runs) > 1)
        self.setMenu(self._choices if choose else None)
        self.setText(self._label + ("…" if choose else ""))
        self.setToolTip("\n".join(descriptions))
        self.setVisible(bool(runs))

    def _rememberSingle(self) -> None:
        self._pressedRun = self._runs[0] if self.menu() is None and len(self._runs) == 1 else None

    def _retrySingle(self) -> None:
        run, self._pressedRun = self._pressedRun, None
        if run is not None:
            self.retryRequested.emit(run)
