"""Paged annotation inspector with keyboard navigation to features and their parts."""

from bisect import bisect_right
from collections.abc import Iterator
from html import escape

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from strandfolio.domain.features import LABEL_EVIDENCE_QUALIFIER, FeaturePart, SequenceFeature
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.annotation_editor import AnnotationEditor
from strandfolio.gui.panels.dialog_layout import confirmAction
from strandfolio.gui.panels.label_review import LabelReviewDialog
from strandfolio.gui.panels.orf_review import OrfReviewDialog
from strandfolio.gui.panels.sequence_conversion import translationInterpretation
from strandfolio.rendering.layout import annotationColor


_DISCLOSURE_WIDTH = 20
_DEFAULT_STATUS = "Choose a feature. Expand a joined feature to select one segment."
FeatureLocation = tuple[str, int | None]


def _partCoordinates(part: FeaturePart) -> str:
    if part.isSelectable:
        return f"{part.start + 1:,}–{part.end:,}"
    return f"{part.locationText} · inspect only"


def _partLabel(part: FeaturePart) -> str:
    """Present one-based inclusive coordinates, strand and uncertain source text."""
    coordinates = _partCoordinates(part)
    direction = {1: "+", -1: "−", 0: "?", None: "?"}[part.strand]
    return f"{coordinates} ({direction})" if part.isSelectable else coordinates


def _swatchIcon(hexColor: str) -> QIcon:
    pixmap = QPixmap(7, 7)
    pixmap.fill(QColor(hexColor))
    return QIcon(pixmap)


class _FeatureNameButton(QPushButton):
    """Expose a full name to accessibility while fitting the visual label."""

    def __init__(self, label: str, accessibleName: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._label = label
        self.setAccessibleName(accessibleName)
        self.setToolTip(escape(accessibleName))
        self.setCheckable(True)
        self.setIconSize(QSize(7, 7))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(48)
        self.setObjectName("featureName")
        self._fitLabel()

    def _fitLabel(self) -> None:
        iconWidth = self.iconSize().width() + 4 if not self.icon().isNull() else 0
        text = self.fontMetrics().elidedText(
            self._label, Qt.TextElideMode.ElideMiddle, max(0, self.width() - iconWidth - 12),
        )
        self.setText(text.replace("&", "&&"))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fitLabel()


class _FeatureLocationLabel(QLabel):
    """Reserve name space when the sidebar narrows and keep full coordinates accessible."""

    def __init__(self, coordinates: str, parent: QWidget) -> None:
        super().__init__(coordinates, parent)
        self._coordinates = coordinates
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(escape(coordinates))
        self.setAccessibleName(coordinates)
        self.setObjectName("featureCoordinates")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        text = self.fontMetrics().elidedText(
            self._coordinates, Qt.TextElideMode.ElideMiddle,
            max(0, self.contentsRect().width() - max(0, self.indent())),
        )
        self.setText(text)


class FeatureRow(QWidget):
    """One rendered feature or part with stable annotation identity."""

    def __init__(
        self, feature: SequenceFeature, partIndex: int | None, icon: QIcon,
        *, isExpanded: bool, parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.featureId = feature.featureId
        self.partIndex = partIndex
        self.setObjectName("featureRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QGridLayout(self)
        layout.setContentsMargins(6 if partIndex is None else 20, 2, 6, 2)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(0)
        layout.setColumnStretch(0, 1)
        part = None
        if partIndex is not None:
            part = feature.parts[partIndex]
        elif len(feature.parts) == 1:
            part = feature.parts[0]
        if part is None:
            interval = f"{len(feature.parts)} segments" if feature.parts else "No recorded location"
        else:
            interval = _partCoordinates(part)
        strands = {entry.strand for entry in feature.parts} if part is None else {part.strand}
        if strands == {1, -1}:
            direction, strandName = "±", "Mixed strands"
        elif strands == {1}:
            direction, strandName = "+", "Forward strand"
        elif strands == {-1}:
            direction, strandName = "−", "Reverse strand"
        else:
            direction, strandName = "?", "Unspecified strand"
        label = feature.label if partIndex is None else f"Segment {partIndex + 1}"
        accessibleName = (f"{label}, {feature.type}" if partIndex is None
                          else f"{feature.label}, {label}")
        self.selectButton = _FeatureNameButton(label, accessibleName, self)
        self.selectButton.setAccessibleDescription(f"{interval}. {strandName}.")
        if partIndex is None:
            self.selectButton.setIcon(icon)
        layout.addWidget(self.selectButton, 0, 0)
        self.location = _FeatureLocationLabel(interval, self)
        self.location.setIndent(14 if partIndex is None else 4)
        layout.addWidget(self.location, 1, 0)
        self.direction = QLabel(direction, self)
        self.direction.setObjectName("featureStrand")
        self.direction.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.direction.setAccessibleName(strandName)
        self.direction.setToolTip(strandName)
        self.direction.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.direction, 1, 1)
        self.disclosureButton: QPushButton | None = None
        if partIndex is None and len(feature.parts) > 1:
            disclosure = QPushButton("▾" if isExpanded else "▸", self)
            disclosure.setObjectName("featureDisclosure")
            disclosure.setFixedWidth(_DISCLOSURE_WIDTH)
            disclosure.setCheckable(True)
            disclosure.setChecked(isExpanded)
            action = "Collapse" if isExpanded else "Expand"
            disclosure.setAccessibleName(f"{action} {feature.label} segments")
            layout.addWidget(disclosure, 0, 1)
            self.disclosureButton = disclosure

    def setSelected(self, selected: bool) -> None:
        with QSignalBlocker(self.selectButton):
            self.selectButton.setChecked(selected)
        if self.property("selected") != selected:
            self.setProperty("selected", selected)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.selectButton.click()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    @property
    def annotationLocation(self) -> FeatureLocation:
        return self.featureId, self.partIndex


class FeaturePanel(QWidget):
    """Show one page of features for the current revision."""

    ROWS_PER_PAGE = 24

    def __init__(self, controller: WorkspaceController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._swatches: dict[str, QIcon] = {}
        self._expandedFeatureIds: set[str] = set()
        self._filteredFeatures: tuple[SequenceFeature, ...] = ()
        self._featureOffsets: dict[str, int] = {}
        self._rowStarts: tuple[int, ...] = ()
        self._rowCount = 0
        self._page = 0
        self._documentId: str | None = None
        self._inspectedLocation: FeatureLocation | None = None
        self.featureRows: tuple[FeatureRow, ...] = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        topRow = QHBoxLayout()
        topRow.setContentsMargins(0, 0, 0, 0)
        topRow.setSpacing(6)
        self.heading = QToolButton(self)
        self.heading.setObjectName("featureSectionToggle")
        self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.heading.setArrowType(Qt.ArrowType.DownArrow)
        self.heading.setCheckable(True)
        self.heading.setChecked(True)
        self.heading.setToolTip("Show or hide the annotated feature list.")
        topRow.addWidget(self.heading, 1)
        self.annotateButton = QPushButton("Annotate", self)
        self.annotateButton.setToolTip("Create an annotation from the selected range.")
        self.annotateButton.clicked.connect(lambda: self._openAnnotationEditor(None))
        topRow.addWidget(self.annotateButton)
        self.annotationActionsButton = QToolButton(self)
        self.annotationActionsButton.setObjectName("annotationOptions")
        self.annotationActionsButton.setText("⋯")
        self.annotationActionsButton.setAccessibleName("Feature options")
        self.annotationActionsButton.setToolTip("Filter, detect or edit annotations")
        self.annotationActionsButton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.annotationActionsButton)
        self.filterAction = menu.addAction("Find features…")
        self.filterAction.setCheckable(True)
        self.keyFeaturesAction = menu.addAction("Key features only")
        self.keyFeaturesAction.setCheckable(True)
        self.keyFeaturesAction.setToolTip("Hide primer-binding annotations in this list.")
        menu.addSeparator()
        self.detectLabelsAction = menu.addAction("Detect labels", self._toggleDetection)
        self.editLabelAction = menu.addAction("Edit label…", self._editSelected)
        self.removeAnnotationAction = menu.addAction("Remove annotation…", self._removeSelected)
        self.annotationActionsButton.setMenu(menu)
        topRow.addWidget(self.annotationActionsButton)
        layout.addLayout(topRow)
        self.orfButton = QPushButton("Find ORFs…", self)
        self.orfButton.clicked.connect(self._openOrfReview)
        layout.addWidget(self.orfButton, alignment=Qt.AlignmentFlag.AlignLeft)
        self._listBody = QWidget(self)
        bodyLayout = QVBoxLayout(self._listBody)
        bodyLayout.setContentsMargins(0, 0, 0, 0)
        bodyLayout.setSpacing(5)
        layout.addWidget(self._listBody, 1)
        self.heading.toggled.connect(self._setListExpanded)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Find feature or type…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Filter annotations by name or type")
        self.search.hide()
        self.filterAction.toggled.connect(self._setFilterVisible)
        bodyLayout.addWidget(self.search)
        self.reviewLabelsButton = QPushButton("Review labels…", self)
        self.reviewLabelsButton.clicked.connect(self._openLabelReview)
        bodyLayout.addWidget(self.reviewLabelsButton)

        self.scrollArea = QScrollArea(self)
        self.scrollArea.setAccessibleName("Stored annotation rows")
        self.scrollArea.setMinimumHeight(90)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._rowContent = QWidget(self.scrollArea)
        self._rowLayout = QVBoxLayout(self._rowContent)
        self._rowLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._rowLayout.setContentsMargins(0, 0, 0, 0)
        self._rowLayout.setSpacing(0)
        self._emptyLabel = QLabel("No features match this filter.", self._rowContent)
        self._emptyLabel.setObjectName("muted")
        self._rowLayout.addWidget(self._emptyLabel)
        self.scrollArea.setWidget(self._rowContent)
        bodyLayout.addWidget(self.scrollArea, 1)

        self._paging = QWidget(self)
        paging = QHBoxLayout(self._paging)
        paging.setContentsMargins(0, 0, 0, 0)
        self.previousPageButton = QPushButton("‹", self._paging)
        self.previousPageButton.setAccessibleName("Previous feature page")
        self.nextPageButton = QPushButton("›", self._paging)
        self.nextPageButton.setAccessibleName("Next feature page")
        self._pageStatus = QLabel(self._paging)
        self._pageStatus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        paging.addWidget(self.previousPageButton)
        paging.addWidget(self._pageStatus, 1)
        paging.addWidget(self.nextPageButton)
        bodyLayout.addWidget(self._paging)

        self.status = QLabel(_DEFAULT_STATUS, self)
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        bodyLayout.addWidget(self.status)
        self.qualifiers = QPlainTextEdit(self)
        self.qualifiers.setReadOnly(True)
        self.qualifiers.setMaximumHeight(68)
        self.qualifiers.setPlaceholderText("Selected qualifiers")
        self.qualifiers.setAccessibleName("Selected annotation qualifiers")
        bodyLayout.addWidget(self.qualifiers)

        self.search.textChanged.connect(self._onFilterChanged)
        self.keyFeaturesAction.toggled.connect(self._onFilterChanged)
        self.previousPageButton.clicked.connect(lambda: self._changePage(-1))
        self.nextPageButton.clicked.connect(lambda: self._changePage(1))
        controller.documentAboutToChange.connect(self._saveViewState)
        controller.documentChanged.connect(self._onDocumentChanged)
        controller.selectionChanged.connect(self._onSelectionChanged)
        controller.geneticCodeChanged.connect(self._onGeneticCodeChanged)
        controller.annotations.changed.connect(self._updateAnnotationControls)
        controller.orfs.changed.connect(self._updateAnnotationControls)
        self._onDocumentChanged()

    def _setListExpanded(self, expanded: bool) -> None:
        self.heading.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._listBody.setVisible(expanded)

    def _setFilterVisible(self, visible: bool) -> None:
        self.search.setVisible(visible)
        if visible:
            self.heading.setChecked(True)
            self.search.setFocus()
        else:
            self.search.clear()

    def _selectedFeature(self) -> SequenceFeature | None:
        revision = self.controller.session.current
        if revision is None or self._inspectedLocation is None:
            return None
        return revision.getFeature(self._inspectedLocation[0])

    def _updateAnnotationControls(self) -> None:
        annotations = self.controller.annotations
        revision = self.controller.session.current
        selection = self.controller.session.selection
        self.annotateButton.setEnabled(selection is not None and bool(len(selection)))
        hasFeature = self._inspectedLocation is not None and revision is not None
        self.editLabelAction.setEnabled(hasFeature)
        self.removeAnnotationAction.setEnabled(hasFeature)
        self.detectLabelsAction.setEnabled(revision is not None and revision.moleculeType == "DNA")
        self.detectLabelsAction.setText("Cancel scan" if annotations.busy else "Detect labels")
        scanStatus = annotations.status or "Scan this DNA record for exact matches."
        self.detectLabelsAction.setToolTip(escape(scanStatus))
        self.reviewLabelsButton.setVisible(annotations.review is not None)
        orfs = self.controller.orfs
        self.orfButton.setVisible(revision is not None and revision.moleculeType == "DNA")
        count = len(orfs.review.candidates) if orfs.review is not None else 0
        self.orfButton.setText(f"Review ORFs ({count:,})…" if count else "Find ORFs…")
        self.orfButton.setToolTip(orfs.status or "Find and review candidate coding regions.")

    def _openAnnotationEditor(self, feature: SequenceFeature | None) -> None:
        annotations = self.controller.annotations
        target = annotations.target()
        revision = self.controller.session.current
        selection = self.controller.session.selection
        if target is None or revision is None or selection is None:
            return
        editor = AnnotationEditor(
            revision, selection, feature,
            lambda command, showError: annotations.apply(target, command, onError=showError), self,
        )
        editor.exec()
        editor.deleteLater()

    def _editSelected(self) -> None:
        feature = self._selectedFeature()
        if feature is not None:
            self._openAnnotationEditor(feature)

    def _removeSelected(self) -> None:
        feature = self._selectedFeature()
        annotations = self.controller.annotations
        target = annotations.target()
        if feature is None or target is None:
            return
        location = ", ".join(_partLabel(part) for part in feature.parts) or "No recorded location"
        longLocation = len(location) > 160
        if confirmAction(
            self, "Remove annotation", f"Remove {feature.label} ({feature.type}) from the working draft?",
            "Remove annotation", detail=f"{len(feature.parts)} ordered segments" if longLocation else location,
            expandedDetail=location if longLocation else "",
        ):
            annotations.remove(target, feature.featureId)

    def _toggleDetection(self) -> None:
        annotations = self.controller.annotations
        if annotations.busy:
            annotations.cancel()
        else:
            annotations.detect()

    def _openLabelReview(self) -> None:
        dialog = LabelReviewDialog(self.controller, self)
        dialog.exec()
        dialog.deleteLater()

    def _openOrfReview(self) -> None:
        dialog = OrfReviewDialog(self.controller, self)
        dialog.exec()
        dialog.deleteLater()

    def _iconForFeature(self, feature: SequenceFeature) -> QIcon:
        color = annotationColor(feature)
        if color not in self._swatches:
            self._swatches[color] = _swatchIcon(color)
        return self._swatches[color]

    def _saveViewState(self) -> None:
        document = self.controller.workspace.activeDocument
        if document is None:
            return
        state = document.viewState
        state.featureSearch = self.search.text()
        state.featureKeyOnly = self.keyFeaturesAction.isChecked()
        state.featurePage = self._page
        state.featureScroll = self.scrollArea.verticalScrollBar().value()
        state.expandedFeatureIds = self._expandedFeatureIds.copy()

    def _onDocumentChanged(self) -> None:
        document = self.controller.workspace.activeDocument
        state = document.viewState if document is not None else None
        documentId = self.controller.workspace.activeDocumentId
        sameDocument = documentId == self._documentId
        previousLocation = self._inspectedLocation
        selection = self.controller.session.selection
        revision = self.controller.session.current
        inspected = None
        if revision is not None and sameDocument and previousLocation is not None:
            previousId = previousLocation[0]
            if selection is None or previousId != selection.featureId:
                inspected = next((
                    feature for feature in revision.features
                    if feature.featureId == previousId
                    and (not feature.parts or any(not part.isSelectable for part in feature.parts))
                ), None)
        self._documentId = documentId
        with QSignalBlocker(self.search), QSignalBlocker(self.keyFeaturesAction):
            self.search.setText(state.featureSearch if state is not None else "")
            self.keyFeaturesAction.setChecked(state.featureKeyOnly if state is not None else False)
        with QSignalBlocker(self.filterAction):
            self.filterAction.setChecked(bool(self.search.text()))
        self.search.setVisible(bool(self.search.text()))
        self._expandedFeatureIds = state.expandedFeatureIds.copy() if state is not None else set()
        self._inspectedLocation = None
        self._refilter()
        count = len(revision.features) if revision is not None else 0
        self.heading.setText(f"All features ({count:,})")
        self._emptyLabel.setText(
            "No features match this filter." if count else "Select a range to annotate it."
        )
        self._showPage(state.featurePage if state is not None else 0)
        if state is not None:
            self.scrollArea.verticalScrollBar().setValue(state.featureScroll)
        self._refreshSelection(
            reveal=sameDocument and selection is not None and selection.featureId is not None,
            previousLocation=previousLocation,
        )
        if inspected is not None:
            self._inspectedLocation = previousLocation
            self._updateCheckedRows()
            self._showFeatureDetails(inspected, previousLocation[1])
        self.setVisible(revision is not None)
        self._updateAnnotationControls()

    def _refilter(self) -> None:
        revision = self.controller.session.current
        features = revision.features if revision is not None else ()
        search = self.search.text().casefold().strip()
        keyOnly = self.keyFeaturesAction.isChecked()
        self._filteredFeatures = tuple(
            feature for feature in features
            if (not keyOnly or feature.type.casefold() != "primer_bind")
            and search in f"{feature.label} {feature.type}".casefold()
        )
        self._indexRows()

    def _indexRows(self) -> None:
        rowStarts = []
        offset = 0
        for feature in self._filteredFeatures:
            rowStarts.append(offset)
            offset += 1
            if len(feature.parts) > 1 and feature.featureId in self._expandedFeatureIds:
                offset += len(feature.parts)
        self._rowCount = offset
        self._rowStarts = tuple(rowStarts)
        self._featureOffsets = {
            feature.featureId: offset
            for feature, offset in zip(self._filteredFeatures, rowStarts, strict=True)
        }

    def _pageFeatures(self) -> Iterator[tuple[SequenceFeature, int | None]]:
        if self._rowCount == 0:
            return
        start = self._page * self.ROWS_PER_PAGE
        stop = min(self._rowCount, start + self.ROWS_PER_PAGE)
        firstFeature = bisect_right(self._rowStarts, start) - 1
        for index in range(firstFeature, len(self._filteredFeatures)):
            feature = self._filteredFeatures[index]
            root = self._rowStarts[index]
            if root >= stop:
                break
            if root >= start:
                yield feature, None
            if len(feature.parts) > 1 and feature.featureId in self._expandedFeatureIds:
                firstPart = max(0, start - root - 1)
                lastPart = min(len(feature.parts), stop - root - 1)
                for partIndex in range(firstPart, lastPart):
                    yield feature, partIndex

    def _onFilterChanged(self) -> None:
        self._refilter()
        self._showPage(0)

    def _showPage(self, page: int) -> None:
        lastPage = max(0, (self._rowCount - 1) // self.ROWS_PER_PAGE)
        self._page = min(max(0, page), lastPage)
        for row in self.featureRows:
            self._rowLayout.removeWidget(row)
            row.hide()
            row.deleteLater()
        rows = []
        for feature, partIndex in self._pageFeatures():
            row = FeatureRow(
                feature, partIndex, self._iconForFeature(feature),
                isExpanded=feature.featureId in self._expandedFeatureIds, parent=self._rowContent,
            )
            row.selectButton.toggled.connect(
                lambda _checked, row=row: self._activateRow(row),
            )
            row.selectButton.installEventFilter(self)
            if row.disclosureButton is not None:
                row.disclosureButton.toggled.connect(
                    lambda expanded, row=row: self._setExpanded(row, expanded),
                )
                row.disclosureButton.installEventFilter(self)
            self._rowLayout.addWidget(row)
            row.show()
            rows.append(row)
        self.featureRows = tuple(rows)
        self._emptyLabel.setVisible(not rows)
        self._updateCheckedRows()
        self._updateTabOrder()
        self._paging.setVisible(lastPage > 0)
        self.previousPageButton.setEnabled(self._page > 0)
        self.nextPageButton.setEnabled(self._page < lastPage)
        first = self._page * self.ROWS_PER_PAGE + 1
        self._pageStatus.setText(f"{first:,}–{first + len(rows) - 1:,} of {self._rowCount:,}")
        self._rowLayout.activate()
        self._rowContent.setMinimumHeight(self._rowLayout.minimumSize().height())
        self.scrollArea.verticalScrollBar().setValue(0)

    def _updateTabOrder(self) -> None:
        previous = self.search
        for row in self.featureRows:
            if row.disclosureButton is not None:
                QWidget.setTabOrder(previous, row.disclosureButton)
                previous = row.disclosureButton
            QWidget.setTabOrder(previous, row.selectButton)
            previous = row.selectButton
        QWidget.setTabOrder(previous, self.previousPageButton)
        QWidget.setTabOrder(self.previousPageButton, self.nextPageButton)
        QWidget.setTabOrder(self.nextPageButton, self.qualifiers)

    def _updateCheckedRows(self) -> None:
        for row in self.featureRows:
            row.setSelected(row.annotationLocation == self._inspectedLocation)

    def _changePage(self, direction: int) -> None:
        self._showPage(self._page + direction)
        if self.featureRows:
            self._focusRow(self.featureRows[0])

    def _rowOffset(self, key: FeatureLocation) -> int | None:
        featureId, partIndex = key
        root = self._featureOffsets.get(featureId)
        if root is None or partIndex is None:
            return root
        return root + 1 + partIndex

    def _rowForLocation(self, key: FeatureLocation) -> FeatureRow | None:
        return next((row for row in self.featureRows if row.annotationLocation == key), None)

    def _focusRow(self, row: FeatureRow, *, disclosure: bool = False) -> None:
        button = row.disclosureButton if disclosure else None
        button = button if button is not None else row.selectButton
        button.setFocus(Qt.FocusReason.OtherFocusReason)
        self.scrollArea.ensureWidgetVisible(row, 0, 4)

    def _focusLocation(self, key: FeatureLocation, *, disclosure: bool = False) -> None:
        offset = self._rowOffset(key)
        if offset is None:
            return
        page = offset // self.ROWS_PER_PAGE
        if page != self._page:
            self._showPage(page)
        row = self._rowForLocation(key)
        if row is not None:
            self._focusRow(row, disclosure=disclosure)

    def _setExpanded(self, row: FeatureRow, expanded: bool) -> None:
        if row not in self.featureRows or row.disclosureButton is None:
            return
        featureId = row.featureId
        hadDisclosureFocus = row.disclosureButton.hasFocus()
        if expanded:
            self._expandedFeatureIds.add(featureId)
        else:
            self._expandedFeatureIds.discard(featureId)
        scroll = self.scrollArea.verticalScrollBar().value()
        self._indexRows()
        self._showPage(self._page)
        self.scrollArea.verticalScrollBar().setValue(scroll)
        self._focusLocation((featureId, None), disclosure=hadDisclosureFocus)

    def _activateRow(self, row: FeatureRow) -> None:
        revision = self.controller.session.current
        if revision is None or row not in self.featureRows:
            return
        feature = revision.getFeature(row.featureId)
        featureId, partIndex = row.annotationLocation
        if len(feature.parts) == 1:
            partIndex = 0
        self._inspectedLocation = row.annotationLocation
        if partIndex is None:
            if len(feature.parts) > 1 and featureId not in self._expandedFeatureIds:
                self._setExpanded(row, True)
            if feature.parts and all(part.isSelectable for part in feature.parts):
                self.controller.selectWholeFeature(featureId)
        elif feature.parts[partIndex].isSelectable:
            self.controller.selectFeature(featureId, partIndex)
        self._updateCheckedRows()
        self._showFeatureDetails(feature, partIndex)
        self._updateAnnotationControls()

    def _moveFocus(self, row: FeatureRow, offset: int) -> None:
        current = self._page * self.ROWS_PER_PAGE + self.featureRows.index(row)
        destination = min(self._rowCount - 1, max(0, current + offset))
        page, index = divmod(destination, self.ROWS_PER_PAGE)
        if page != self._page:
            self._showPage(page)
        self._focusRow(self.featureRows[index])

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.KeyPress or not isinstance(watched.parent(), FeatureRow):
            return super().eventFilter(watched, event)
        row = watched.parent()
        if row not in self.featureRows:
            return super().eventFilter(watched, event)
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            watched.click()
        elif key == Qt.Key.Key_Up:
            self._moveFocus(row, -1)
        elif key == Qt.Key.Key_Down:
            self._moveFocus(row, 1)
        elif key == Qt.Key.Key_Home:
            self._focusRow(self.featureRows[0])
        elif key == Qt.Key.Key_End:
            self._focusRow(self.featureRows[-1])
        elif key == Qt.Key.Key_Right and row.disclosureButton is not None:
            if row.featureId not in self._expandedFeatureIds:
                self._setExpanded(row, True)
            else:
                self._focusLocation((row.featureId, 0))
        elif key == Qt.Key.Key_Left and row.partIndex is not None:
            self._focusLocation((row.featureId, None))
        elif key == Qt.Key.Key_Left and row.featureId in self._expandedFeatureIds:
            self._setExpanded(row, False)
        else:
            return super().eventFilter(watched, event)
        return True

    def _showFeatureQualifiers(self, feature: SequenceFeature) -> None:
        detailLines = [f"Type: {feature.type}", f"Location operator: {feature.operator or 'single'}"]
        cdsTranslation = self.controller.cdsForFeature(feature.featureId)
        if cdsTranslation is not None:
            matchNote = (
                "matches imported"
                if cdsTranslation.matchesImported is True
                else ("differs from imported" if cdsTranslation.matchesImported is False else "computed")
            )
            if cdsTranslation.isValid:
                detailLines.append(
                    f"Computed CDS: {cdsTranslation.lengthAa} aa ({matchNote})"
                )
            else:
                detailLines.append("CDS translation unavailable")
            detailLines.append(translationInterpretation(cdsTranslation))
            if cdsTranslation.warnings:
                detailLines.append("CDS notes: " + "; ".join(cdsTranslation.warnings))
        evidence = feature.labelEvidence
        if evidence is not None:
            detailLines.extend((
                f"Exact catalogue reference · {evidence.referenceId}",
                f"Catalogue release · {evidence.catalogueRelease}",
                f"Reference source · {evidence.referenceSourceRecord}",
            ))
        detailLines.extend(
            f"{key}: {'; '.join(entries)}" for key, entries in feature.qualifiers
            if key != LABEL_EVIDENCE_QUALIFIER or evidence is None
        )
        self.qualifiers.setPlainText("\n".join(detailLines))

    def _showFeatureDetails(self, feature: SequenceFeature, partIndex: int | None) -> None:
        self.status.show()
        self.qualifiers.show()
        self._showFeatureQualifiers(feature)
        if partIndex is None:
            if not feature.parts:
                self.status.setText("This feature has no recorded location.")
            elif all(part.isSelectable for part in feature.parts):
                self.status.setText(f"{feature.type} · {len(feature.parts)} segments selected (gaps excluded).")
            else:
                self.status.setText("Inspecting this feature. Choose an exact segment to select bases.")
            return
        part = feature.parts[partIndex]
        if not part.isSelectable:
            self.status.setText("Inspecting an uncertain location. Sequence selection has not changed.")
            return
        self.status.setText(f"{feature.type} · segment {partIndex + 1} of {len(feature.parts)}")

    def _onGeneticCodeChanged(self) -> None:
        feature = self._selectedFeature()
        if feature is not None and feature.type == "CDS":
            self._showFeatureQualifiers(feature)

    def _onSelectionChanged(self) -> None:
        self._refreshSelection(reveal=True)
        self._updateAnnotationControls()

    def _refreshSelection(
        self, *, reveal: bool, previousLocation: FeatureLocation | None = None,
    ) -> None:
        selected = self.controller.session.selection
        hasFeature = selected is not None and selected.featureId is not None
        self.status.setVisible(hasFeature)
        self.qualifiers.setVisible(hasFeature)
        if not hasFeature:
            self._inspectedLocation = None
            self.status.setText(_DEFAULT_STATUS if not reveal else
                                "Manual sequence selection; choose a feature to navigate.")
            self.qualifiers.clear()
            self._updateCheckedRows()
            return

        feature = self.controller.session.current.getFeature(selected.featureId)
        partIndex = None if selected.wholeFeature else selected.partIndex
        rowPart = None if len(feature.parts) == 1 else partIndex
        self._inspectedLocation = feature.featureId, rowPart
        if reveal and self._inspectedLocation != previousLocation:
            self._revealSelectedRow(feature, rowPart)
        self._updateCheckedRows()
        self._showFeatureDetails(feature, partIndex)

    def _revealSelectedRow(self, feature: SequenceFeature, partIndex: int | None) -> None:
        featureId = feature.featureId
        if featureId not in self._featureOffsets:
            return
        shouldExpand = partIndex is not None and featureId not in self._expandedFeatureIds
        if shouldExpand:
            self._expandedFeatureIds.add(featureId)
            self._indexRows()
        key = featureId, partIndex
        page = self._rowOffset(key) // self.ROWS_PER_PAGE
        if shouldExpand or page != self._page:
            self._showPage(page)
        row = self._rowForLocation(key)
        if row is not None:
            self.scrollArea.ensureWidgetVisible(row, 0, 4)
