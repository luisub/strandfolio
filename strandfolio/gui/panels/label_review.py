"""Review exact catalogue matches before making any stored annotation decisions."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from strandfolio.gui.panels.choice_rows import ChoiceRow, ChoiceRows
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController
    from strandfolio.operations.editing import LabelSuggestion


_STATE_TEXT = {
    "new": "New",
    "already_annotated": "Already annotated",
    "overlap": "Review overlap",
    "ambiguous": "Review reference",
    "dismissed": "Dismissed",
}


def _locationText(parts: tuple[tuple[int, int], ...], strand: int | None) -> str:
    intervals = ", ".join(f"{start + 1:,}–{end:,}" for start, end in parts)
    direction = {1: "+", -1: "−", 0: "unstranded", None: "strand unresolved"}[strand]
    return f"{intervals} ({direction})"


class LabelReviewDialog(QDialog):
    """Show exact matches, conflicts and explicit decisions for the active document."""

    def __init__(self, controller: "WorkspaceController", parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review labels")
        self.setMinimumSize(560, 385)
        self.resize(845, 470)
        self.controller = controller
        self.annotations = controller.annotations
        self._documentId = controller.workspace.activeDocumentId
        self._review = None
        self._suggestions: dict[str, LabelSuggestion] = {}
        self._selectedId: str | None = None

        layout = dialogLayout(self)
        self.status = QLabel(self)
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.wholePlasmids = QLabel(self)
        self.wholePlasmids.setWordWrap(True)
        self.wholePlasmids.setTextFormat(Qt.TextFormat.PlainText)
        self.wholePlasmids.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.wholePlasmids)

        self._panes = QSplitter(Qt.Orientation.Horizontal, self)
        self._panes.setChildrenCollapsible(False)
        self._panes.setHandleWidth(10)
        layout.addWidget(self._panes, 1)
        self.suggestionsList = ChoiceRows("Detected label suggestions", self, pageSize=8)
        self.details = QPlainTextEdit(self)
        self.details.setAccessibleName("Selected label match evidence")
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a match to inspect its evidence.")
        for title, control in (("Matches", self.suggestionsList), ("Match evidence", self.details)):
            pane = QWidget(self._panes)
            column = QVBoxLayout(pane)
            column.setContentsMargins(0, 0, 0, 0)
            heading = QLabel(title, pane)
            heading.setObjectName("sectionTitle")
            column.addWidget(heading)
            column.addWidget(control, 1)
            self._panes.addWidget(pane)
        self._panes.setSizes([380, 425])
        actions = QHBoxLayout()
        self.acceptButton = QPushButton("Accept", self)
        self.acceptAllButton = QPushButton("Accept all new", self)
        self.dismissButton = QPushButton("Dismiss", self)
        self.reconsiderButton = QPushButton("Reconsider", self)
        for button in (self.acceptButton, self.acceptAllButton, self.dismissButton, self.reconsiderButton):
            button.setAutoDefault(False)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        close.rejected.connect(self.reject)
        actions.addWidget(close)
        actions.addStretch(1)
        for button in (self.acceptAllButton, self.reconsiderButton, self.dismissButton, self.acceptButton):
            actions.addWidget(button)
        addDialogFooter(layout, actions, self.acceptButton)
        self.acceptButton.clicked.connect(self._acceptSelected)
        self.acceptAllButton.clicked.connect(lambda: self.annotations.accept(None))
        self.dismissButton.clicked.connect(self._dismissSelected)
        self.reconsiderButton.clicked.connect(self._acceptSelected)
        self.reconsiderButton.setToolTip("Accept this previously dismissed match into the draft.")
        self.suggestionsList.activated.connect(self._selectSuggestion)
        self.annotations.changed.connect(self._updateReview)
        self._updateReview()
        if self._selectedId in self.suggestionsList.rowButtons:
            self.suggestionsList.rowButtons[self._selectedId].setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        orientation = Qt.Orientation.Horizontal if event.size().width() >= 710 else Qt.Orientation.Vertical
        if self._panes.orientation() != orientation:
            self._panes.setOrientation(orientation)
            self._panes.setSizes([1, 1])

    def _selected(self) -> "LabelSuggestion | None":
        return self._suggestions.get(self._selectedId)

    def _selectSuggestion(self, suggestionId: str) -> None:
        self._selectedId = suggestionId
        self._updateSelected()

    def _updateReview(self) -> None:
        if self._documentId != self.controller.workspace.activeDocumentId:
            self.reject()
            return
        review = self.annotations.review
        self.status.setText(self.annotations.status)
        if review is not self._review:
            self._review = review
            suggestions = review.suggestions if review is not None else ()
            self._suggestions = {suggestion.suggestionId: suggestion for suggestion in suggestions}
            self.suggestionsList.setRows(tuple(
                ChoiceRow(suggestion.suggestionId, " · ".join((
                    _STATE_TEXT[suggestion.status], suggestion.label,
                    _locationText(suggestion.parts, suggestion.strand),
                )))
                for suggestion in suggestions
            ))
            if suggestions and self._selectedId not in self._suggestions:
                self._selectedId = suggestions[0].suggestionId
            self.suggestionsList.setSelected(self._selectedId)
        matches = review.wholePlasmids if review is not None else ()
        self.wholePlasmids.setVisible(bool(matches))
        if matches:
            names = ", ".join(dict.fromkeys(match.record.label for match in matches))
            title = "Whole-plasmid exact match" if len(matches) == 1 else "Whole-plasmid exact matches"
            self.wholePlasmids.setText(f"{title} · {names}")
        self.acceptAllButton.setEnabled(
            not self.annotations.busy
            and any(suggestion.status == "new" for suggestion in self._suggestions.values())
        )
        self._updateSelected()

    def _updateSelected(self) -> None:
        suggestion = self._selected()
        status = suggestion.status if suggestion is not None and not self.annotations.busy else None
        canDecide = status in ("new", "overlap", "ambiguous")
        self.acceptButton.setEnabled(canDecide)
        self.dismissButton.setEnabled(canDecide)
        self.reconsiderButton.setEnabled(status == "dismissed")
        if suggestion is None:
            self.details.clear()
            return
        self.details.setPlainText(self._matchDetails(suggestion))

    def _matchDetails(self, suggestion: "LabelSuggestion") -> str:
        evidence = suggestion.evidence
        lines = [f"Exact full-reference match · {suggestion.featureType}"]
        if suggestion.aliases:
            lines.append(f"Reviewed names · {', '.join(suggestion.aliases)}")
        relatedIds = set(suggestion.matchedFeatureIds + suggestion.overlappingFeatureIds)
        revision = self.controller.session.current
        related = {
            feature.featureId: feature for feature in revision.features
            if feature.featureId in relatedIds
        } if relatedIds and revision is not None else {}
        for featureIds, relation in (
            (suggestion.matchedFeatureIds, "Existing annotation"),
            (suggestion.overlappingFeatureIds, "Overlapping annotation"),
        ):
            for featureId in featureIds:
                feature = related.get(featureId)
                if feature is None:
                    continue
                if feature.parts and all(part.isSelectable for part in feature.parts):
                    parts = tuple((part.start, part.end) for part in feature.parts)
                    location = _locationText(parts, feature.strand)
                else:
                    location = ", ".join(part.locationText for part in feature.parts)
                lines.append(f"{relation} · {feature.label} ({feature.type}) · {location}")
        lines.extend((
            f"Reference {evidence.referenceId}",
            f"Source {evidence.referenceSourceId} · {evidence.referenceSourceRecord}",
            f"Catalogue release {evidence.catalogueRelease}",
            f"Reference SHA-256 {evidence.referenceSequenceSha256}",
        ))
        return "\n".join(lines)

    def _acceptSelected(self) -> None:
        selected = self._selected()
        if selected is not None and self._review is self.annotations.review:
            self.annotations.accept((selected.suggestionId,))

    def _dismissSelected(self) -> None:
        selected = self._selected()
        if selected is not None and self._review is self.annotations.review:
            self.annotations.dismiss((selected.suggestionId,))
