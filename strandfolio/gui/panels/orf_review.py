"""Review and choose candidate coding regions before adding annotations."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget,
)

from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.operations.orfs import OrfCandidate, OrfOptions, OrfReview

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController


def _candidateRow(candidate: OrfCandidate) -> ChoiceRow:
    coordinates = ", ".join(f"{start + 1:,}–{end:,}" for start, end in candidate.parts)
    frame = f"{candidate.frame:+d}".replace("-", "−")
    state = "CDS candidate" if candidate.hasStop else "Partial · no terminal stop"
    return ChoiceRow(candidate, f"{candidate.lengthAa:,} aa · Frame {frame} · {coordinates}\n{state}")


class OrfReviewDialog(QDialog):
    """Expose scan options and apply only checked candidates to the captured record."""

    def __init__(self, controller: "WorkspaceController", parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review ORFs")
        self.setMinimumSize(460, 395)
        self.resize(615, 525)
        self._controller = controller
        self._target = controller.annotations.target()
        self._orfs = controller.orfs
        self._shownReview: OrfReview | None = None
        self._isAdding = False

        layout = dialogLayout(self)
        settings = QHBoxLayout()
        minimumLabel = QLabel("Minimum length", self)
        self.minimumLength = QSpinBox(self)
        self.minimumLength.setAccessibleName("Minimum ORF length in amino acids")
        self.minimumLength.setRange(1, max(30, len(controller.session.sequence) // 3))
        self.minimumLength.setSuffix(" aa")
        self.minimumLength.setValue(self._orfs.options.minimumAa)
        minimumLabel.setBuddy(self.minimumLength)
        self.code = QLabel(self)
        self.code.setObjectName("muted")
        self.code.setWordWrap(True)
        self.scanButton = QPushButton("Scan", self)
        self.scanButton.setAutoDefault(False)
        for widget in (minimumLabel, self.minimumLength, self.code):
            settings.addWidget(widget)
        settings.addStretch(1)
        settings.addWidget(self.scanButton)
        layout.addLayout(settings)
        self.includeNoStop = QCheckBox("Include ORFs without a terminal stop", self)
        self.includeNoStop.setChecked(self._orfs.options.includeNoStop)
        revision = controller.session.current
        self.includeNoStop.setVisible(revision is None or revision.topology != "circular")
        layout.addWidget(self.includeNoStop)
        self.status = QLabel(self)
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.candidates = ChoiceRows("Candidate ORFs to annotate", self, multiple=True, pageSize=10)
        layout.addWidget(self.candidates, 1)
        hint = QLabel(
            "Checked complete ORFs become CDS annotations. "
            "Partial ORFs are saved as misc_feature annotations.", self,
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)

        footer = QHBoxLayout()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        close.rejected.connect(self.reject)
        self.dismissButton = QPushButton("Dismiss selected", self)
        self.addButton = QPushButton("Add selected", self)
        footer.addWidget(close)
        footer.addStretch(1)
        for button in (self.dismissButton, self.addButton):
            button.setAutoDefault(False)
            footer.addWidget(button)
        addDialogFooter(layout, footer, self.addButton)

        self.scanButton.clicked.connect(self._scanOrCancel)
        self.minimumLength.valueChanged.connect(self._updateCommands)
        self.includeNoStop.toggled.connect(self._updateCommands)
        self.candidates.activated.connect(self._updateCommands)
        self.dismissButton.clicked.connect(self._dismissSelected)
        self.addButton.clicked.connect(self._addSelected)
        self._orfs.changed.connect(self._updateReview)
        if self._orfs.review is None and not self._orfs.busy:
            self._orfs.scan(self._options())
        self._updateReview()

    def _options(self) -> OrfOptions:
        return OrfOptions(self.minimumLength.value(), self.includeNoStop.isChecked())

    def _scanOrCancel(self) -> None:
        if self._orfs.busy:
            self._orfs.cancel()
        else:
            self._orfs.scan(self._options())

    @Slot()
    def _updateReview(self) -> None:
        if self._isAdding:
            return
        if self._target != self._controller.annotations.target():
            self.reject()
            return
        self.code.setText(f"ATG start · Code {self._orfs.tableId}")
        review = self._orfs.review
        if review is not self._shownReview:
            self._shownReview = review
            rows = tuple(map(_candidateRow, review.candidates)) if review is not None else ()
            self.candidates.setRows(rows)
        self._updateCommands()

    @Slot()
    def _updateCommands(self) -> None:
        review = self._shownReview
        busy = self._orfs.busy
        self.scanButton.setText("Cancel scan" if busy else "Scan")
        settingsChanged = review is not None and review.options != self._options()
        canCurate = bool(review and not busy and not settingsChanged)
        count = len(self.candidates.selectedKeys)
        self.candidates.setEnabled(canCurate)
        self.addButton.setEnabled(canCurate and count > 0)
        self.dismissButton.setEnabled(canCurate and count > 0)
        if settingsChanged:
            status = "Scan to apply the new settings."
        elif review is not None and review.candidates:
            total = len(review.candidates)
            status = f"{total:,} {'candidate' if total == 1 else 'candidates'} · {count:,} selected"
        elif review is not None:
            status = "No candidates to review."
        else:
            status = self._orfs.status
        self.status.setText(status)

    def _dismissSelected(self) -> None:
        if self._shownReview is not None:
            self._orfs.dismiss(self._shownReview, self.candidates.selectedKeys)

    def _addSelected(self) -> None:
        if self._shownReview is None:
            return
        self._isAdding = True
        try:
            added = self._orfs.add(
                self._shownReview, self.candidates.selectedKeys, onError=self.status.setText,
            )
        finally:
            self._isAdding = False
        if added:
            self.accept()
