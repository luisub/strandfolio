"""Read-only Assembly illustration of the bundled, already annotated product."""

from html import escape

from PySide6.QtCore import QByteArray, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QResizeEvent
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSplitter,
    QStackedWidget, QVBoxLayout, QWidget,
)

from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.views.gel_preview import formatSourceIntervals
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.rendering import colors
from strandfolio.rendering.layout import annotationColor
from strandfolio.rendering.maps import MapFigureSource


# Published shares in the fixed pReporter_demo illustration.
_ILLUSTRATED_BACKBONE_BP = 3_600
_ILLUSTRATED_INSERT_BP = 900


class _IllustratedContributions(QWidget):
    """Draw the two amounts credited in the Assembly design plate."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setMinimumSize(160, 128)
        self.setAccessibleName("Illustrated contributions")
        self.setAccessibleDescription(
            "Assembly design plate: 3,600 bp backbone and 900 bp insert, totaling 4,500 bp."
        )

    def paintEvent(self, event) -> None:
        total = _ILLUSTRATED_BACKBONE_BP + _ILLUSTRATED_INSERT_BP
        # Center the map and its source key as one figure, including at compact heights.
        diameter = max(1, min(278, self.width() - 32, self.height() - 58))
        ring = QRectF((self.width() - diameter) / 2, (self.height() - diameter - 32) / 2,
                      diameter, diameter)
        weight = min(17, diameter * 0.063)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#6944ae"), weight))
            painter.drawEllipse(ring)
            insertPen = QPen(QColor("#277669"), weight)
            insertPen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(insertPen)
            painter.drawArc(ring, 18 * 16, -round(360 * 16 * _ILLUSTRATED_INSERT_BP / total))
            font = QFont(self.font())
            font.setPixelSize(min(27, round(diameter * 0.14)))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(colors.INK))
            center = ring.center().y()
            painter.drawText(QRectF(0, center - 31, self.width(), 36),
                             Qt.AlignmentFlag.AlignCenter, f"{total:,} bp")
            font.setPixelSize(13)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(colors.MUTED))
            painter.drawText(QRectF(0, center + 8, self.width(), 20),
                             Qt.AlignmentFlag.AlignCenter, "Circular product")
            metrics = painter.fontMetrics()
            entries = (("Backbone", "#6944ae"), ("Insert", "#277669"))
            keyWidth = sum(17 + metrics.horizontalAdvance(label) for label, _ in entries) + 22
            left = (self.width() - keyWidth) / 2
            top = ring.bottom() + weight / 2 + 10
            for label, color in entries:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(QRectF(left, top + 6, 11, 5), 2, 2)
                painter.setPen(QColor(colors.MUTED))
                painter.drawText(QRectF(left + 17, top, metrics.horizontalAdvance(label) + 1, 18),
                                 Qt.AlignmentFlag.AlignVCenter, label)
                left += 17 + metrics.horizontalAdvance(label) + 22


class _ProductMap(QSvgWidget):
    """Fit the same annotated map used by Sequence to the example viewport."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._source: MapFigureSource | None = None
        self.setMinimumSize(160, 128)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setAccessibleName("Recorded product map")

    def setProduct(self, product: SequenceRevision) -> None:
        self._source = MapFigureSource(product, product.name, product.topology == "circular", mapFeatureTypes=None)
        self.setAccessibleDescription(self._source.description)
        self._render()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._source is not None:
            scene = self._source.scene(self.width(), self.height(), native=True)
            self.load(QByteArray(scene.svg.encode("utf-8")))


class AssemblyDesignExample(QSplitter):
    """Display a fixed design record and open the exact saved copy's History."""

    historyRequested = Signal(str)
    sequenceRequested = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setChildrenCollapsible(False)
        canvas = QFrame(self, objectName="workflowCanvas")
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Illustrated source contributions", canvas, objectName="sectionTitle"))
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.backboneName = self._addRole(cards, "Backbone", "backboneCard", _ILLUSTRATED_BACKBONE_BP, canvas)
        self.insertName = self._addRole(cards, "Insert", "insertCard", _ILLUSTRATED_INSERT_BP, canvas)
        layout.addLayout(cards)
        layout.addSpacing(6)
        preview = QHBoxLayout()
        preview.addWidget(QLabel("Product preview", canvas, objectName="sectionTitle"), 1)
        self.annotationsButton = QPushButton("Show annotations", canvas, checkable=True)
        self.annotationsButton.setIcon(workspaceIcon("layers"))
        preview.addWidget(self.annotationsButton)
        layout.addLayout(preview)
        self.previewPages = QStackedWidget(canvas)
        self.contributionChart = _IllustratedContributions(self.previewPages)
        self.productMap = _ProductMap(self.previewPages)
        self.previewPages.addWidget(self.contributionChart)
        self.previewPages.addWidget(self.productMap)
        self.annotationsButton.toggled.connect(self.previewPages.setCurrentIndex)
        layout.addWidget(self.previewPages, 1)
        self.savedRow = QWidget(canvas)
        saved = QHBoxLayout(self.savedRow)
        saved.setContentsMargins(0, 7, 0, 0)
        copiesLabel = QLabel("Saved copies", self.savedRow, objectName="sectionTitle")
        saved.addWidget(copiesLabel)
        self.savedCopies = QComboBox(self.savedRow)
        self.savedCopies.setAccessibleName("Saved example copies, newest first")
        self.savedCopies.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.savedCopies.setMinimumContentsLength(9)
        self.savedCopies.setMaximumWidth(320)
        copiesLabel.setBuddy(self.savedCopies)
        saved.addWidget(self.savedCopies, 1)
        saved.addStretch()
        self.sequenceButton = QPushButton("Open in Sequence", self.savedRow)
        self.sequenceButton.clicked.connect(self._openSavedSequence)
        saved.addWidget(self.sequenceButton)
        self.historyButton = QPushButton("View in History", self.savedRow)
        self.historyButton.clicked.connect(self._openSavedHistory)
        saved.addWidget(self.historyButton)
        layout.addWidget(self.savedRow)
        self.savedRow.hide()

        inspector = QFrame(self, objectName="workflowInspector")
        inspector.setMinimumWidth(220)
        details = QVBoxLayout(inspector)
        details.setContentsMargins(20, 18, 20, 18)
        details.setSpacing(12)
        details.addWidget(QLabel("Recorded design example", inspector, objectName="sectionTitle"))
        self.productName = FittedLabel("", inspector)
        self.productName.setObjectName("documentTitle")
        details.addWidget(self.productName)
        form = QFormLayout()
        form.setHorizontalSpacing(20)
        self.length = QLabel(inspector)
        self.topology = QLabel(inspector)
        form.addRow("Length", self.length)
        form.addRow("Topology", self.topology)
        details.addLayout(form)
        divider = QFrame(inspector, objectName="sectionDivider")
        divider.setFixedHeight(1)
        details.addWidget(divider)
        self.annotationHeading = QLabel(inspector, objectName="sectionTitle")
        details.addWidget(self.annotationHeading)
        self.annotationNames = QLabel(inspector, textFormat=Qt.TextFormat.RichText, wordWrap=True)
        self.annotationNames.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.annotationNames)
        details.addStretch()
        details.addWidget(QLabel(
            "Contribution sizes are credited by the Assembly design plate. "
            "Annotations come from the bundled GenBank record.",
            inspector, objectName="muted", wordWrap=True,
        ))
        self.addWidget(canvas)
        self.addWidget(inspector)
        self.setStretchFactor(0, 1)
        self.setSizes([950, 324])

    def _addRole(self, row: QHBoxLayout, role: str, style: str, lengthBp: int,
                 parent: QWidget) -> FittedLabel:
        card = QFrame(parent, objectName=style)
        contents = QVBoxLayout(card)
        contents.setContentsMargins(16, 15, 16, 17)
        contents.addWidget(QLabel(role, card, objectName="sectionTitle"))
        name = FittedLabel("", card)
        name.setObjectName("documentTitle")
        contents.addWidget(name)
        contents.addWidget(QLabel(f"{lengthBp:,} bp", card, objectName="muted"))
        row.addWidget(card, 1)
        return name

    def setProduct(self, product: SequenceRevision, backboneLabel: str, insertLabel: str) -> None:
        """Use the supplied record's features and the two attributed source names."""
        self.backboneName.setFullText(backboneLabel)
        self.insertName.setFullText(insertLabel)
        self.productName.setFullText(product.name)
        self.length.setText(f"{len(product):,} bp")
        self.topology.setText((product.topology or "Unspecified").capitalize())
        self.annotationHeading.setText(f"Annotations ({len(product.features)})")
        rows = []
        descriptions = []
        for feature in product.features:
            if all(part.isSelectable for part in feature.parts):
                interval = formatSourceIntervals((part.start, part.end) for part in feature.parts)
            else:
                interval = " · ".join(part.locationText for part in feature.parts)
            rows.append(
                f'<tr><td><font color="{annotationColor(feature)}">▪</font> {escape(feature.label)}</td>'
                f'<td align="right">{escape(interval)}</td></tr>'
            )
            descriptions.append(f"{feature.label}, {interval}")
        self.annotationNames.setText('<table width="100%" cellspacing="0" cellpadding="3">'
                                     + "".join(rows) + "</table>")
        self.annotationNames.setAccessibleDescription("\n".join(descriptions))
        self.productMap.setProduct(product)
        self.annotationsButton.setChecked(False)

    def setSavedCopy(self, revision: SequenceRevision | None) -> None:
        """Select the new saved revision at the head of the session list, or clear on project change."""
        if revision is None:
            self.savedCopies.clear()
        else:
            index = self.savedCopies.findData(revision.revisionId)
            if index >= 0:
                self.savedCopies.removeItem(index)
            self.savedCopies.insertItem(0, revision.name, revision.revisionId)
            self.savedCopies.setCurrentIndex(0)
        self.savedRow.setVisible(revision is not None)

    def _openSavedHistory(self) -> None:
        revisionId = self.savedCopies.currentData()
        if revisionId is not None:
            self.historyRequested.emit(revisionId)

    def _openSavedSequence(self) -> None:
        revisionId = self.savedCopies.currentData()
        if revisionId is not None:
            self.sequenceRequested.emit(revisionId)
