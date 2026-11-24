"""Preview figures and export SVG or PNG at the chosen dimensions."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QRectF, QSaveFile, QSignalBlocker, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from strandfolio.gui.panels.dialog_layout import addDialogFooter, confirmAction, dialogLayout, fileDialogDirectory
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.io.formats import (
    checkExportDestination, checkExportDestinationVersion, exportDestinationVersion,
    publishStagedExport,
)

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController


_MAXIMUM_PNG_PIXELS = 24_000_000
_PNG_LIMIT_MESSAGE = "PNG supports up to 24 million pixels. Reduce the width or choose SVG."
_FILE_FILTERS = {
    ".svg": "SVG figure (*.svg)",
    ".png": "PNG figure (*.png)",
    ".json": "Pinned gel data (*.json)",
}


@dataclass(frozen=True, slots=True)
class FigureScope:
    """A pinned view, its identity and a renderer taking the output width in pixels."""

    label: str
    description: str
    renderSvg: Callable[[int], str]
    defaultWidth: int
    minimumWidth: int = 320


class _FigurePreviewArea(QScrollArea):
    """Fit the figure to the viewport width while keeping long figures scrollable."""

    def __init__(self, figure: QSvgWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._figure = figure
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(figure)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

    def fitFigure(self) -> None:
        size = self._figure.renderer().defaultSize()
        viewportSize = self.maximumViewportSize()
        if size.isEmpty() or viewportSize.isEmpty():
            return
        availableWidth = viewportSize.width()
        # Keep unusually tall vector scenes within scrollable widget coordinates.
        maximumScale = min(1.0, 100_000 / size.height())
        scale = min(maximumScale, availableWidth / size.width())
        if round(size.height() * scale) > viewportSize.height():
            # Reserve the scrollbar width from the unoccupied viewport. This keeps the
            # preview stable even when that small reduction makes the entire scene fit.
            availableWidth -= self.verticalScrollBar().sizeHint().width()
            availableWidth = min(availableWidth, self.viewport().width())
            scale = min(maximumScale, availableWidth / size.width())
        self._figure.setFixedSize(
            max(1, round(size.width() * scale)), max(1, round(size.height() * scale)),
        )

    def viewportEvent(self, event: QEvent) -> bool:
        handled = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize and self.widget() is not None:
            self.fitFigure()
        return handled


class FigureExportDialog(QDialog):
    """Render a captured view at the selected width and show its actual output bounds."""

    def __init__(
        self, parent: QWidget, title: str, scopes: tuple[FigureScope, ...],
        *, hasData: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("figureExportDialog")
        self.setWindowTitle(title)
        self.resize(830, min(660, max(480, parent.height() - 64)))
        self._scopes = scopes
        self._widths = [scope.defaultWidth for scope in scopes]
        self.svg = b""
        self._error = ""

        heading, _, self._sourceName = title.partition(" · ")
        layout = dialogLayout(self, heading)
        selectors = QHBoxLayout()
        selectors.setSpacing(12)
        self.scopeSelector = QComboBox(self)
        self.scopeSelector.addItems([scope.label for scope in scopes])
        self.scopeSelector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._addField(selectors, "&Scope", self.scopeSelector, 2)
        self.formatSelector = QComboBox(self)
        self.formatSelector.addItem("SVG", ".svg")
        self.formatSelector.addItem("PNG", ".png")
        if hasData:
            self.formatSelector.addItem("Pinned gel data (JSON)", ".json")
        self._addField(selectors, "&Format", self.formatSelector, 1)
        self.widthSpinBox = QSpinBox(self)
        self.widthSpinBox.setSuffix(" px")
        self.widthSpinBox.setSingleStep(100)
        self.widthSpinBox.setKeyboardTracking(False)
        self._addField(selectors, "&Width", self.widthSpinBox, 1)
        layout.addLayout(selectors)

        sourceRow = QHBoxLayout()
        self._scopeSummary = FittedLabel("", self)
        self._scopeSummary.setObjectName("muted")
        sourceRow.addWidget(self._scopeSummary, 1)
        self.detailsButton = QToolButton(self)
        self.detailsButton.setText("Source details")
        self.detailsButton.setCheckable(True)
        self.detailsButton.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.detailsButton.setArrowType(Qt.ArrowType.RightArrow)
        sourceRow.addWidget(self.detailsButton)
        layout.addLayout(sourceRow)
        self.sourceDetails = QPlainTextEdit(self)
        self.sourceDetails.setReadOnly(True)
        self.sourceDetails.setAccessibleName("Captured figure source details")
        self.sourceDetails.setMaximumHeight(105)
        self.sourceDetails.hide()
        layout.addWidget(self.sourceDetails)
        self.detailsButton.toggled.connect(self._showSourceDetails)
        previewHeader = QHBoxLayout()
        previewLabel = QLabel("Preview", self)
        previewLabel.setObjectName("sectionTitle")
        previewHeader.addWidget(previewLabel)
        self.dimensionsLabel = QLabel(self)
        self.dimensionsLabel.setTextFormat(Qt.TextFormat.PlainText)
        self.dimensionsLabel.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.dimensionsLabel.setWordWrap(True)
        previewHeader.addWidget(self.dimensionsLabel, 1)
        layout.addLayout(previewHeader)

        self.figurePreview = QSvgWidget(self)
        self.figurePreview.setAccessibleName("Figure preview")
        self.previewScrollArea = _FigurePreviewArea(self.figurePreview, self)
        layout.addWidget(self.previewScrollArea, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.destinationButton = buttons.addButton(
            "Choose destination…", QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.destinationButton.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        addDialogFooter(layout, buttons, self.destinationButton)

        self.scopeSelector.currentIndexChanged.connect(self._changeScope)
        self.widthSpinBox.valueChanged.connect(self._render)
        self.formatSelector.currentIndexChanged.connect(self._updateStatus)
        self._changeScope(0)

    def _addField(self, row: QHBoxLayout, name: str, field: QWidget, stretch: int) -> None:
        column = QVBoxLayout()
        column.setSpacing(4)
        label = QLabel(name, self)
        label.setBuddy(field)
        column.addWidget(label)
        column.addWidget(field)
        row.addLayout(column, stretch)

    def _showSourceDetails(self, expanded: bool) -> None:
        self.sourceDetails.setVisible(expanded)
        self.detailsButton.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _changeScope(self, index: int) -> None:
        scope = self._scopes[index]
        summary = scope.description.partition("\n")[0]
        self._scopeSummary.setFullText(f"{self._sourceName} · {summary}" if self._sourceName else summary)
        self.sourceDetails.setPlainText(scope.description)
        self.figurePreview.setAccessibleDescription(scope.description)
        with QSignalBlocker(self.widthSpinBox):
            self.widthSpinBox.setRange(scope.minimumWidth, _MAXIMUM_PNG_PIXELS)
            self.widthSpinBox.setValue(self._widths[index])
        self._render(self.widthSpinBox.value())

    def _render(self, width: int) -> None:
        index = self.scopeSelector.currentIndex()
        scope = self._scopes[index]
        self._widths[index] = width
        self.svg = b""
        self._error = ""
        try:
            svg = scope.renderSvg(width).encode("utf-8")
            self.figurePreview.load(svg)
            renderer = self.figurePreview.renderer()
            size = renderer.defaultSize()
            if not renderer.isValid() or size.isEmpty() or renderer.viewBoxF().isEmpty():
                raise ValueError("The figure has no displayable bounds.")
            self.svg = svg
            self.previewScrollArea.fitFigure()
        except (OSError, ValueError) as error:
            self._error = str(error)
        self.figurePreview.setVisible(bool(self.svg))
        self._updateStatus()

    def _updateStatus(self) -> None:
        suffix = self.formatSelector.currentData()
        size = self.figurePreview.renderer().defaultSize()
        self.widthSpinBox.setEnabled(suffix != ".json")
        tooLarge = suffix == ".png" and size.width() * size.height() > _MAXIMUM_PNG_PIXELS
        dimensions = f"{size.width():,} × {size.height():,} px"
        if suffix == ".json":
            status = "Includes the pinned lanes, marker and display settings shown here."
        elif self._error:
            status = self._error
        else:
            status = dimensions
            if tooLarge:
                status += f" · {_PNG_LIMIT_MESSAGE}"
        self.dimensionsLabel.setText(status)
        self.destinationButton.setEnabled(suffix == ".json" or (bool(self.svg) and not tooLarge))


def _pngPayload(renderer: QSvgRenderer) -> bytes:
    size = renderer.defaultSize()
    if size.isEmpty() or not renderer.isValid():
        raise ValueError("The figure has no displayable bounds.")
    if size.width() * size.height() > _MAXIMUM_PNG_PIXELS:
        raise ValueError(_PNG_LIMIT_MESSAGE)
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise OSError("There is not enough memory for the PNG.")
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(image.rect()))
    painter.end()
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise OSError("Could not encode the PNG.")
    return bytes(buffer.data())


def _writeExport(
    destination: Path, payload: bytes, approvedVersion: tuple[int, ...] | None,
    checkBeforeCommit: Callable[[], None],
) -> None:
    from strandfolio.storage.projects import _syncFile

    if approvedVersion is None:
        with TemporaryDirectory(dir=destination.parent, prefix=".strandfolio-figure-") as directory:
            staged = Path(directory) / "figure"
            with staged.open("wb") as handle:
                handle.write(payload)
                _syncFile(handle)
            checkBeforeCommit()
            publishStagedExport(staged, destination, approvedVersion)
        return

    # QSaveFile preserves the approved file's permissions and follows its symlink, if any.
    output = QSaveFile(str(destination))
    output.setDirectWriteFallback(False)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    try:
        if output.write(payload) != len(payload):
            raise OSError(output.errorString())
        checkBeforeCommit()
        if not output.commit():
            raise OSError(output.errorString())
    finally:
        if output.isOpen():
            output.cancelWriting()


def exportFigure(
    parent: QWidget, controller: "WorkspaceController", *, title: str, suggestedStem: str,
    scopes: tuple[FigureScope, ...], dataPayload: Callable[[], bytes] | None = None,
    protectedPaths: tuple[Path, ...] = (), beforePublish: Callable[[], None] | None = None,
) -> Path | None:
    """Preview a captured view and atomically publish the chosen format after validation."""
    dialog = FigureExportDialog(parent, title, scopes, hasData=dataPayload is not None)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        suffix = dialog.formatSelector.currentData()
        stem = Path(suggestedStem).name or "figure"
        suggested = fileDialogDirectory() / f"{stem}{suffix}"
        filename, _ = QFileDialog.getSaveFileName(
            parent, title, str(suggested), _FILE_FILTERS[suffix],
        )
        if not filename:
            return None
        destination = Path(filename)
        needsSuffix = not destination.suffix
        if needsSuffix:
            destination = destination.with_suffix(suffix)
        pickedVersion = exportDestinationVersion(destination)

        def checkDestination() -> None:
            if beforePublish is not None:
                beforePublish()
            controller.ensureFigureDestination(destination)
            if protectedPaths:
                checkExportDestination(destination, protectedPaths)
            checkExportDestinationVersion(destination, pickedVersion)

        checkDestination()
        if destination.suffix.lower() != suffix:
            raise ValueError(f"Use a {suffix} filename for the selected output format.")
        if suffix != ".json" and not dialog.svg:
            raise ValueError("The figure has no displayable bounds.")
        if needsSuffix and pickedVersion is not None:
            if not confirmAction(parent, "Replace existing file", f"Replace {destination.name}?", "Replace"):
                return None
        if suffix == ".png":
            payload = _pngPayload(dialog.figurePreview.renderer())
        elif suffix == ".json" and dataPayload is not None:
            payload = dataPayload()
        else:
            payload = dialog.svg
        _writeExport(destination, payload, pickedVersion, checkDestination)
        controller.message.emit(f"Exported {destination.name}.")
    except (OSError, ValueError) as error:
        controller.operationFailed.emit(f"Could not export figure. {error}")
        return None
    finally:
        dialog.deleteLater()
    return destination
