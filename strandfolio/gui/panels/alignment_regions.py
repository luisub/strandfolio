"""Choose and identify the source region of an alignment input."""

from collections.abc import Iterable
from html import escape

from PySide6.QtCore import QEvent, QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QListView, QPlainTextEdit,
    QPushButton, QSizePolicy, QWidget,
)

from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.panels.dialog_layout import addDialogFooter, dialogLayout
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.operations.alignment import alignmentFeatures


_SEARCH_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def regionCaption(label: str | None, segments: Iterable[tuple[int, int]]) -> str:
    """Identify an annotated region by name and one-based source intervals."""
    if label is None:
        return "Whole record"
    return f"{label} · {regionIntervals(segments)}"


def regionIntervals(segments: Iterable[tuple[int, int]]) -> str:
    """Display ordered half-open intervals as one-based inclusive ranges."""
    return ", ".join(f"{start + 1:,}–{end:,}" for start, end in segments)


class AlignmentRegionButton(QPushButton):
    """Keep the chosen region identifiable in the narrow inspector."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._caption = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(76)

    def setRegion(self, role: str, caption: str, description: str) -> None:
        self._caption = caption
        self.setAccessibleName(f"Choose {role} region. {description}")
        self.setToolTip(f"<p>{escape(description)}</p>")
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        text = self.fontMetrics().elidedText(
            self._caption + "  ▾", Qt.TextElideMode.ElideMiddle, max(0, self.width() - 24),
        )
        self.setText(text.replace("&", "&&"))


class AlignmentRegionDialog(QDialog):
    """Search the exact eligible annotations of one captured source revision."""

    def __init__(
        self, role: str, name: str, revision: SequenceRevision, featureId: str | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("alignmentRegionChooser")
        self.setWindowTitle(f"Choose {role} region")
        self.resize(570, 480)
        self.featureId = featureId
        layout = dialogLayout(self)
        layout.addWidget(FittedLabel(name, self))
        self.wholeRecordButton = QPushButton(f"Use whole record · {len(revision):,} nt", self)
        self.wholeRecordButton.setAutoDefault(False)
        self.wholeRecordButton.clicked.connect(self._chooseWholeRecord)
        layout.addWidget(self.wholeRecordButton)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find a feature by name or type")
        self.search.setAccessibleName("Find a feature by name or type")
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)
        self.features = QListView(self)
        self.features.setAccessibleName("Eligible regions")
        self.features.setUniformItemSizes(True)
        self.features.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.features.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        layout.addWidget(self.features, 1)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected region details")
        self.details.setFixedHeight(68)
        layout.addWidget(self.details)
        footer = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.chooseButton = footer.addButton("Use region", QDialogButtonBox.ButtonRole.AcceptRole)
        self.chooseButton.setDefault(True)
        footer.accepted.connect(self._chooseFeature)
        footer.rejected.connect(self.reject)
        addDialogFooter(layout, footer, self.chooseButton)

        features = sorted(alignmentFeatures(revision), key=lambda feature: (
            min(part.start for part in feature.parts), feature.label.casefold(), feature.type.casefold(),
        ))
        self._model = QStandardItemModel(self)
        selected = None
        for feature in features:
            segments = tuple((part.start, part.end) for part in feature.parts)
            length = sum(end - start for start, end in segments)
            strand = "−" if feature.strand == -1 else "+"
            caption = f"{regionCaption(feature.label, segments)} · {feature.type} · {strand} · {length:,} nt"
            item = QStandardItem(caption)
            item.setEditable(False)
            item.setData(feature.featureId, Qt.ItemDataRole.UserRole)
            item.setData(f"{feature.label}\n{feature.type}", _SEARCH_ROLE)
            item.setToolTip(f"<p>{escape(caption)}</p>")
            self._model.appendRow(item)
            if feature.featureId == featureId:
                selected = item
        self._filterModel = QSortFilterProxyModel(self)
        self._filterModel.setSourceModel(self._model)
        self._filterModel.setFilterRole(_SEARCH_ROLE)
        self._filterModel.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.features.setModel(self._filterModel)
        self._hint = "Eligible regions include supported genes, CDSs, ORFs and replication origins."
        self.features.selectionModel().currentChanged.connect(self._updateDetails)
        self.features.activated.connect(self._chooseFeature)
        self.search.textChanged.connect(self._filter)
        self._filter("")
        if selected is not None:
            self.features.setCurrentIndex(self._filterModel.mapFromSource(selected.index()))
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        self._filterModel.setFilterFixedString(text.strip())
        first = self._filterModel.index(0, 0)
        self.features.setCurrentIndex(first)
        if first.isValid():
            message = self._hint
        else:
            message = ("No eligible features match your search." if self._model.rowCount()
                       else "No eligible features in this record.")
            message += " " + self._hint
        self.message.setText(message)
        self._updateDetails()

    def _updateDetails(self) -> None:
        index = self.features.currentIndex()
        self.details.setPlainText(index.data() if index.isValid() else "")
        self.chooseButton.setEnabled(index.isValid())

    def _chooseWholeRecord(self) -> None:
        self.featureId = None
        self.accept()

    def _chooseFeature(self) -> None:
        index = self.features.currentIndex()
        if index.isValid():
            self.featureId = index.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Up, Qt.Key.Key_Down,
        ) and self.features.currentIndex().isValid():
            self.features.setFocus()
            return True
        return super().eventFilter(watched, event)
