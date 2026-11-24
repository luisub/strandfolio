"""Read-only review of the order and recorded sources in a circular Assembly plan."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from strandfolio.application.assembly_session import AssemblyOccurrence, AssemblySession
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows


_END_NAMES = {
    "unknown": "unknown", "blunt": "blunt",
    "5-prime-overhang": "5′ overhang", "3-prime-overhang": "3′ overhang",
}


def recordedEndTypes(revision: SequenceRevision) -> str:
    """Describe left and right ends in the input record's own orientation."""
    geometry = revision.duplexGeometry
    if geometry is None:
        return "Source end types unrecorded"
    left = _END_NAMES[geometry.leftEnd.kind] if geometry.leftEnd is not None else "unrecorded"
    right = _END_NAMES[geometry.rightEnd.kind] if geometry.rightEnd is not None else "unrecorded"
    return f"Ends in input orientation · left {left} · right {right}"


class AssemblyJunctionReview(QWidget):
    """Inspect neighboring inputs, with selection keyed to the two slot IDs."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._roles: dict[str, tuple[str, AssemblyOccurrence]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        self.pairs = ChoiceRows("Planned junctions in circular order", self)
        layout.addWidget(self.pairs, 1)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setTabChangesFocus(True)
        self.details.setAccessibleName("Sources and orientations for the selected junction")
        layout.addWidget(self.details, 2)
        self.pairingStatus = QLabel("Pairing between these inputs has not been assessed.", self)
        self.pairingStatus.setTextFormat(Qt.TextFormat.PlainText)
        self.pairingStatus.setWordWrap(True)
        layout.addWidget(self.pairingStatus)
        self.pairs.activated.connect(self._showPair)

    def refresh(
        self, session: AssemblySession, roles: tuple[tuple[str, AssemblyOccurrence], ...],
    ) -> None:
        """Keep the same ordered pair selected while it remains in the plan."""
        self._roles = {occurrence.occurrenceId: (name, occurrence) for name, occurrence in roles}
        rows = []
        if any(occurrence.source is not None for _, occurrence in roles):
            for number, (first, second) in enumerate(session.plannedJunctions, start=1):
                key = first.occurrenceId, second.occurrenceId
                firstName, secondName = self._roles[key[0]][0], self._roles[key[1]][0]
                closing = " · closes circle" if second.occurrenceId == session.backbone.occurrenceId else ""
                rows.append(ChoiceRow(key, f"J{number} · {firstName} → {secondName}{closing}"))
        self.pairs.setRows(tuple(rows))
        selected = next(iter(self.pairs.selectedKeys), rows[0].key if rows else None)
        self.pairs.setSelected(selected)
        self._showPair(selected)

    def _showPair(self, key: tuple[str, str] | None) -> None:
        sections = []
        for occurrenceId in key or ():
            role, occurrence = self._roles[occurrenceId]
            source = occurrence.source
            if source is None:
                sections.append(f"{role}\nNo source selected")
                continue
            description = (
                f"{role} · {source.label}\n"
                f"Plan orientation · {occurrence.orientation} relative to the input record\n"
                f"{recordedEndTypes(source.revision)}"
            )
            geometry = source.revision.duplexGeometry
            if geometry is not None:
                forward, reverse = geometry.forwardSpan, geometry.reverseSpan
                description += (
                    f"\nStrands on input record (1-based inclusive) · "
                    f"forward {forward.start + 1:,}–{forward.end:,}, "
                    f"reverse {reverse.start + 1:,}–{reverse.end:,}"
                )
            description += f"\nInput revision {source.revision.revisionId}"
            if source.outputReference is not None:
                reference = source.outputReference
                description += f"\nPreparation {reference.operationId} · output {reference.outputKey}"
            sections.append(description)
        self.details.setPlainText("\n\n".join(sections))
        self.pairingStatus.setVisible(key is not None)
