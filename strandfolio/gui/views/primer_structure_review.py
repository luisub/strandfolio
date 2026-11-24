"""Read-only structure predictions tied to the currently displayed full primer sequences."""

from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from strandfolio.domain.primers import PrimerEntry
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.operations.primer_structures import (
    DUPLEX_MODELS, STRUCTURE_MODEL, PrimerMeltingEstimate, PrimerStructureResult, assessPrimerStructures,
    estimatePrimerMelting,
)


class PrimerStructureReview(QWidget):
    findingsReady = Signal(object, str)

    def __init__(self, parent: QWidget, *, live: bool = False) -> None:
        super().__init__(parent)
        # Closing a result must never join a running calculation on the GUI thread.
        # The launcher drains the shared pool after the event loop exits.
        self._pool = QThreadPool.globalInstance()
        self._inputs: tuple[str | None, tuple[PrimerEntry, PrimerEntry]] | None = None
        self._request = 0
        self._worker: CalculationTask | None = None
        self._runEvidence = ""
        self._live = live
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if live:
            heading = QLabel("Primer quality", self)
            heading.setObjectName("analysisPanelHeading")
            layout.addWidget(heading)
            self.temperatures = QLabel(self)
            self.summary = QLabel(self)
            self.temperatures.setAccessibleName("Estimated perfect-complement melting temperatures")
            self.temperatures.setToolTip("Estimated melting temperature of the annealing sequence with its perfect complement.")
            self.summary.setAccessibleName("Primer quality findings")
            for label in (self.temperatures, self.summary):
                label.setWordWrap(True)
                label.setTextFormat(Qt.TextFormat.PlainText)
                layout.addWidget(label)
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(300)
            self._timer.timeout.connect(self.startCheck)
            self.check = QPushButton("Show checks", self)
            self.check.setCheckable(True)
            self.check.setAccessibleDescription("Show individual hairpin and dimer predictions.")
            self.check.toggled.connect(self._toggleChecks)
            layout.addWidget(self.check)
            self.checkRows = QLabel(self)
            self.checkRows.setWordWrap(True)
            self.checkRows.setTextFormat(Qt.TextFormat.PlainText)
            self.checkRows.setAccessibleName("Individual primer structure checks")
            self.checkRows.hide()
            layout.addWidget(self.checkRows)
            self.calculationDetails = QPushButton("Calculation details", self)
            self.calculationDetails.setCheckable(True)
            self.calculationDetails.setAccessibleDescription("Show the full inputs, methods and calculation conditions.")
            self.calculationDetails.hide()
            layout.addWidget(self.calculationDetails)
        self.report = QPlainTextEdit(self)
        self.report.setReadOnly(True)
        self.report.setTabChangesFocus(True)
        if live:
            self.report.setFixedHeight(165)
        self.report.setAccessibleName("Primer structure predictions and model conditions")
        self.report.hide()
        layout.addWidget(self.report)
        if live:
            self.calculationDetails.toggled.connect(self.report.setVisible)

    def setInputs(self, contextId: str | None, entries: tuple[PrimerEntry, PrimerEntry], *, runEvidence: str = "") -> None:
        identity = (contextId, entries)
        if identity == self._inputs and self._runEvidence == runEvidence:
            return
        self._request += 1
        self._releaseWorker()
        self._inputs = identity
        self._runEvidence = runEvidence
        self.report.clear()
        self.report.hide()
        if self._live:
            self._timer.stop()
            self.checkRows.clear()
            self.check.setChecked(False)
            hasInput = bool(runEvidence) or any(entry.annealing or entry.tail for entry in entries)
            self.check.setEnabled(hasInput)
            self.temperatures.clear()
            self.temperatures.hide()
            self._setSummary("Checking…" if hasInput else "")
            self.setVisible(hasInput)
            if hasInput:
                self._timer.start()

    def _toggleChecks(self, expanded: bool) -> None:
        self.check.setText("Hide checks" if expanded else "Show checks")
        self.checkRows.setVisible(expanded)
        self.calculationDetails.setVisible(expanded)
        if not expanded:
            self.calculationDetails.setChecked(False)
        if expanded and self._timer.isActive():
            self.startCheck()

    def startCheck(self) -> None:
        if self._inputs is None or self._worker is not None:
            return
        _, entries = self._inputs
        live = self._live
        if live:
            if not self.check.isEnabled():
                return
            self._timer.stop()
            self.checkRows.setText("Checking…")
            self.calculationDetails.setEnabled(False)
        else:
            if not self._runEvidence and all(entry.problem for entry in entries):
                return
            self.report.setPlainText("Checking primers…")
            self.report.show()
        self._request += 1

        def calculate(canceled):
            structures = assessPrimerStructures(*entries)
            estimates = tuple(estimatePrimerMelting(entry) for entry in entries) if live and not canceled() else ()
            return structures, estimates
        self._worker = CalculationTask(
            self._request, calculate, failureContext="primer quality check",
        )
        self.destroyed.connect(self._worker.cancel)
        self._worker.signals.completed.connect(self._finished)
        self._pool.start(self._worker)

    def _releaseWorker(self) -> None:
        if self._worker is not None:
            self.destroyed.disconnect(self._worker.cancel)
            self._worker.cancel()
            self._worker = None

    @Slot(int, object, object)
    def _finished(self, request: int, outcome: tuple | None, error: str | None) -> None:
        if request != self._request or self._inputs is None:
            return
        self._releaseWorker()
        if self._live:
            self.calculationDetails.setEnabled(True)
        if error or outcome is None:
            message = error or "Structure checks unavailable."
            self.report.setPlainText(f"{self._runEvidence}\n\n{message}".strip())
            if self._live:
                self._setSummary("Primer checks unavailable.", needsReview=True)
                self.checkRows.setText(message)
            self.findingsReady.emit((), message)
            return
        results, estimates = outcome
        checks = results
        lines = []
        if self._live:
            supplied = [entry.role for entry in self._inputs[1] if entry.annealing or entry.tail]
            if len(supplied) == 1:
                checks = tuple(result for result in results if result.name.startswith(supplied[0] + " "))
            self._showQuality(checks, estimates)
            lines.append("Perfect-complement Tm · annealing portions")
            for entry, estimate in zip(self._inputs[1], estimates, strict=True):
                if entry.role not in supplied:
                    continue
                value = (f"{estimate.tmC:.1f} °C · {estimate.model}" if estimate.tmC is not None
                         else f"Unavailable · {estimate.reason}")
                lines.append(f"{entry.role} · {value}")
            lines.extend(("", DUPLEX_MODELS))
        if not self._live or self._runEvidence:
            for result in checks:
                if result.predicted is None:
                    finding = f"Unassessed · {result.reason}"
                elif result.predicted:
                    finding = f"Predicted · Tm {result.tmC:.1f} °C"
                else:
                    finding = "None predicted"
                lines.append(f"{result.name} · {finding}")
        lines.extend(("", "Full primers · 5′→3′"))
        for entry in self._inputs[1]:
            if not entry.problem:
                lines.append(f"{entry.role} · {len(entry.sequence)} nt · {entry.sequence}\n{entry.composition}")
        lines.extend(("", "Structure model", STRUCTURE_MODEL, "",
                      "Unassessed · 3′ extendability, primer–template Tm, mismatched binding and off-template targets.\n"
                      "Assay performance requires experimental evidence."))
        if self._runEvidence:
            lines.extend(("", self._runEvidence))
        self.report.setPlainText("\n".join(lines))
        self.findingsReady.emit(results, "")

    def _showQuality(
        self, results: tuple[PrimerStructureResult, ...], estimates: tuple[PrimerMeltingEstimate, ...],
    ) -> None:
        supplied = [(entry, estimate) for entry, estimate in zip(self._inputs[1], estimates, strict=True)
                    if entry.annealing or entry.tail]
        temperatures = []
        tmReasons = []
        for entry, estimate in supplied:
            if estimate.tmC is None:
                value = "unavailable"
                tmReasons.append(estimate.reason)
            else:
                approximate = "≈" if estimate.approximate else ""
                value = f"{approximate}{estimate.tmC:.1f} °C"
            temperatures.append(f"{entry.role} {value}")
        temperatureText = "Tm · " + " · ".join(temperatures) if temperatures else ""
        forward, reverse = estimates
        temperatureHelp = "Estimated Tm for the annealing sequence with its perfect complement."
        if forward.tmC is not None and reverse.tmC is not None:
            if forward.model == reverse.model:
                temperatureText += f"\nΔTm {abs(forward.tmC - reverse.tmC):.1f} °C"
            else:
                temperatureHelp += " ΔTm requires matching models."
        self.temperatures.setToolTip(temperatureHelp)
        self.temperatures.setAccessibleDescription(temperatureHelp)
        self.temperatures.setText(temperatureText)
        self.temperatures.setVisible(bool(temperatures))

        findings = []
        rows = []
        reasons = []
        for result in results:
            if result.predicted:
                kind = "pair dimer" if result.name == "Forward–reverse dimer" else result.name.split(" ", 1)[1]
                if kind not in findings:
                    findings.append(kind)
                value = f"Tm {result.tmC:.1f} °C"
            elif result.predicted is False:
                value = "None predicted"
            else:
                value = "Unavailable"
                reasons.append(result.reason)
            rows.append(f"{result.name} · {value}")
        if findings:
            summary = f"Possible {', '.join(findings)}"
            if reasons:
                summary += " · Some checks unavailable"
        elif reasons:
            summary = ("Some checks unavailable" if any(result.predicted is False for result in results)
                       else "Structure checks unavailable")
        else:
            summary = "No structures predicted"
        explanations = list(dict.fromkeys(reasons + tmReasons))
        if explanations:
            rows.extend(("", *explanations))
        self.checkRows.setText("\n".join(rows))
        self._setSummary(summary, needsReview=bool(findings or explanations))

    def _setSummary(self, message: str, *, needsReview: bool = False) -> None:
        name = "notice" if needsReview else ""
        if self.summary.objectName() != name:
            self.summary.setObjectName(name)
            self.summary.style().unpolish(self.summary)
            self.summary.style().polish(self.summary)
            self.summary.updateGeometry()
        self.summary.setText(message)
