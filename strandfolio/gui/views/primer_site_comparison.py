"""Inspect an entered oligo against the template interval picked for that primer."""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QVBoxLayout, QWidget,
)

from strandfolio.domain.primers import PrimerStrand
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.operations.conversion import complementBases
from strandfolio.rendering.colors import AMBER_SOFT, SELECTION


def _displayBases(bases: str, limit: int) -> str:
    """Keep both ends in view; the entry field retains the full sequence."""
    if len(bases) > limit:
        edge = limit // 2
        bases = bases[:edge] + "…" + bases[-edge:]
    return "".join(base if base in "ACGT…" else "?" for base in bases)


class PrimerSiteComparison(QFrame):
    pickRequested = Signal(int)
    revealRequested = Signal(int, int)

    def __init__(self, controller: WorkspaceController, parent: QWidget) -> None:
        super().__init__(parent)
        self.controller = controller
        self._endContext = None
        self._documentKey = None
        self._dismissed = False
        self._picking = False
        self.setAccessibleName("Primer and selected template site")
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        header = QHBoxLayout()
        self.role = QComboBox(self)
        self.role.setAccessibleName("Primer to compare")
        self.role.addItem("Forward primer", 1)
        self.role.addItem("Reverse primer", -1)
        self.siteLabel = QLabel(self)
        self.siteLabel.setWordWrap(True)
        endLabel = QLabel("5′ end", self)
        self.endInput = QLineEdit(self)
        self.endInput.setFont(workspaceMonoFont(13))
        self.endInput.setMaxLength(2_147_483_647)
        self.endInput.setMaximumWidth(130)
        self.endInput.setPlaceholderText("Optional")
        self.endInput.setToolTip("Unpaired bases at the start of this primer (5′→3′).")
        self.endInput.textChanged.connect(self._editEnd)
        endLabel.setBuddy(self.endInput)
        header.addWidget(self.role)
        header.addWidget(self.siteLabel, 1)
        header.addWidget(endLabel)
        header.addWidget(self.endInput)
        layout.addLayout(header)
        self.duplex = QPlainTextEdit(self)
        self.duplex.setReadOnly(True)
        self.duplex.setTabChangesFocus(True)
        self.duplex.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.duplex.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.duplex.setFrameStyle(QFrame.Shape.NoFrame)
        self.duplex.setFont(workspaceMonoFont(13))
        self.duplex.document().setDocumentMargin(1)
        self.duplex.ensurePolished()
        margins = self.duplex.contentsMargins()
        self.duplex.setFixedHeight(3 * (self.duplex.fontMetrics().height() + 1) + margins.top() + margins.bottom()
                                   + self.duplex.horizontalScrollBar().sizeHint().height() + 4)
        self.duplex.setAccessibleName("Primer 5′ to 3′ above its template strand 3′ to 5′")
        key = "| match · × mismatch · ~ unpaired 5′ end"
        self.duplex.setToolTip(key)
        self.duplex.setAccessibleDescription(key)
        layout.addWidget(self.duplex)
        self.tailNote = QLabel(self)
        self.tailNote.setWordWrap(True)
        self.tailNote.setObjectName("muted")
        layout.addWidget(self.tailNote)
        self.role.activated.connect(self._chooseRole)
        controller.pcr.primerSitePicked.connect(self.setRole)
        self.refresh()

    def setRole(self, strand: PrimerStrand) -> None:
        if any(site.strand == strand for site in self.controller.pcr.primerComparisonSites):
            self._dismissed = False
            self.role.setCurrentIndex(self.role.findData(strand))
            self.refresh()
        else:
            self.dismiss()

    def _chooseRole(self, index: int) -> None:
        strand = self.role.itemData(index)
        site = next((s for s in self.controller.pcr.primerComparisonSites if s.strand == strand), None)
        if site is not None:
            self.setRole(strand)
            self.revealRequested.emit(*site.sourceSegments[-1])
        else:
            if self._endContext is not None:
                with QSignalBlocker(self.role):
                    self.role.setCurrentIndex(self.role.findData(self._endContext[1].strand))
            self.pickRequested.emit(strand)

    def dismiss(self) -> None:
        self._dismissed = True
        self.hide()

    def setPicking(self, picking: bool) -> None:
        self._picking = picking
        self._updateVisibility()

    def _updateVisibility(self) -> None:
        self.setVisible(self._endContext is not None and not self._picking and not self._dismissed)

    def _editEnd(self, text: str) -> None:
        if self._endContext is None:
            return
        pcr = self.controller.pcr
        texts = list(pcr.primerTexts)
        texts[2 if self._endContext[1].strand == 1 else 3] = text
        pcr.setPrimerInputs(*texts)

    def refresh(self) -> None:
        revision = self.controller.session.current
        documentKey = (self.controller.workspace.activeDocumentId, revision.revisionId if revision else None)
        if self._documentKey != documentKey:
            self._documentKey = documentKey
            self._dismissed = False
            self._endContext = None
        sites = {site.strand: site for site in self.controller.pcr.primerComparisonSites}
        if not sites:
            self._endContext = None
            self.hide()
            with QSignalBlocker(self.endInput):
                self.endInput.clear()
            self.duplex.clear()
            self.siteLabel.clear()
            self.tailNote.clear()
            return
        for index, role in enumerate(("Forward", "Reverse")):
            label = f"{role} primer" if self.role.itemData(index) in sites else f"Select {role.lower()} primer…"
            self.role.setItemText(index, label)
        strand = self.role.currentData()
        if strand not in sites:
            if self._endContext is not None and self._endContext[1].strand not in sites:
                self._dismissed = True
            strand = next(iter(sites))
            self.role.setCurrentIndex(self.role.findData(strand))
        site = sites[strand]
        entry = self.controller.pcr.primerEntries[0 if strand == 1 else 1]
        first, last = site.sourceSegments[0], site.sourceSegments[-1]
        start, end = (first[0] + 1, last[1]) if strand == 1 else (first[1], last[0] + 1)
        location = f"Site {start:,}→{end:,}"
        if len(site.sourceSegments) > 1:
            location += " (across origin)"
        rawEnd = self.controller.pcr.primerTexts[2 if strand == 1 else 3]
        context = (self.controller.workspace.activeDocumentId, site)
        if context != self._endContext or rawEnd != self.endInput.text():
            # Reset field history on role/site changes, even if their ends happen to match.
            # During a live edit the raw text already matches, preserving the caret.
            with QSignalBlocker(self.endInput):
                self.endInput.setText(rawEnd)
            self._endContext = context
        self.endInput.setAccessibleName(f"{entry.role} primer 5′ end at selected site")

        # The stored site is already oriented like the selected primer. Its physical
        # partner below therefore needs a complement, with no additional reversal.
        partner = complementBases(site.annealingSequence, "DNA")
        tail = _displayBases(entry.tail, 24)
        annealing = _displayBases(entry.annealing, 80)
        marks = "".join("~" if base != "…" else " " for base in tail)
        if entry.problem:
            location += " · Check primer entry"
        elif len(entry.annealing) != len(partner):
            location += f" · Lengths differ ({len(entry.annealing):,} vs {len(partner)} nt)"
        else:
            paired = [a == b for a, b in zip(entry.annealing, site.annealingSequence, strict=True)]
            mismatches = paired.count(False)
            location += (" · Exact match" if not mismatches else
                         f" · {mismatches} mismatch" + ("es" if mismatches != 1 else ""))
            marks += "".join("|" if match else "×" for match in paired)
        self.siteLabel.setText(location)
        self.duplex.setPlainText("\n".join((
            f"Primer   5′ {tail}{annealing} 3′",
            "            " + marks,
            f"Template 3′ {' ' * len(tail)}{partner} 5′",
        )))
        highlights = []
        if tail:
            highlights.append((0, 0, len(tail), SELECTION))
        for column, mark in enumerate(marks):
            if mark == "×":
                highlights.extend((row, column, 1, AMBER_SOFT) for row in (0, 1, 2))
        selections = []
        for row, column, length, color in highlights:
            item = QTextEdit.ExtraSelection()
            item.cursor = QTextCursor(self.duplex.document().findBlockByNumber(row))
            start = item.cursor.position() + len("Primer   5′ ") + column
            item.cursor.setPosition(start)
            item.cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            item.format.setBackground(QColor(color))
            selections.append(item)
        self.duplex.setExtraSelections(selections)
        self.tailNote.setText(f"~ {len(entry.tail):,} nt unpaired" if tail else "")
        self.tailNote.setVisible(bool(tail))
        self._updateVisibility()
