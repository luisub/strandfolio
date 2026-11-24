"""Inspect and export a multiple alignment of open sequence revisions."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QSignalBlocker, QThreadPool, Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from strandfolio.application.alignment_session import AlignmentSelection
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.calculation_task import CalculationTask
from strandfolio.gui.panels.dialog_layout import addDialogFooter, confirmAction, dialogLayout, fileDialogDirectory
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.gui.theme import workspaceMonoFont
from strandfolio.gui.views.alignment_matrix import ColumnOverview, MatrixModel, MatrixTable, SourceHeader
from strandfolio.gui.workspace_icons import workspaceIcon
from strandfolio.io.formats import (
    checkExportDestination, checkExportDestinationVersion, exportDestinationVersion,
    publishStagedExport,
)
from strandfolio.operations.alignment import AlignmentCanceled, alignmentInput
from strandfolio.rendering.colors import ACCENT_INK, AMBER, AMBER_SOFT, SELECTION

if TYPE_CHECKING:
    from strandfolio.gui.controller import WorkspaceController
    from strandfolio.operations.multiple_alignment import MultipleAlignmentResult


def _calculate(
    sources: tuple[tuple[AlignmentSelection, SequenceRevision], ...], canceled: Callable[[], bool],
) -> MultipleAlignmentResult | None:
    from strandfolio.operations.multiple_alignment import alignMultiple

    inputs = []
    for selection, revision in sources:
        if canceled():
            return None
        inputs.append(alignmentInput(
            revision, selection.featureId, selection.reverse, checkProteinMatrix=False,
        ))
    captured = tuple(inputs)
    try:
        result = alignMultiple(captured, isCanceled=canceled)
    except AlignmentCanceled:
        return None
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    if result.inputs != captured:
        raise ValueError("The returned matrix does not match the selected sequence revisions.")
    return result


class MultipleAlignmentDialog(QDialog):
    """Own one nonmodal calculation and its source bindings until the window closes."""

    def __init__(self, controller: WorkspaceController, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("multipleAlignmentDialog")
        self.setWindowTitle("Multiple alignment")
        self.controller = controller
        self.alignmentResult: MultipleAlignmentResult | None = None
        self._closed = False
        workspace = controller.workspace
        roles = (controller.alignment.reference, *controller.alignment.queries)
        if roles[0] is None or not 3 <= len(roles) <= 9:
            raise ValueError("Choose a reference and two to eight queries.")
        self.resize(820, 160)
        docs = tuple(workspace.getDocument(role.documentId) for role in roles)
        names = workspace.documentDisplayNames()
        self._names = tuple(" · ".join(filter(None, names[doc.documentId])) for doc in docs)
        self._protected = tuple(doc.revision.sourcePath for doc in docs if doc.revision.sourcePath)
        self._request = workspace.tasks.begin(
            "multiple-alignment", "matrix", settings=roles, description="Align all selected sequences together",
            inputs=tuple(workspace.taskInput("row", doc.documentId) for doc in docs),
        )
        self.statusLabel = QLabel(f"Aligning {len(docs)} sequences with Kalign…", self)
        self.statusLabel.setObjectName("muted")
        self.statusLabel.setTextFormat(Qt.TextFormat.PlainText)
        self.statusLabel.setWordWrap(True)
        layout = dialogLayout(self, icon=workspaceIcon("alignment"), subtitle=self.statusLabel)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)
        self.setStyleSheet("""
            QLabel#dialogWorkflowIcon { background: #eee8fa; border: 1px solid #e3d9f4; border-radius: 10px; }
            QFrame#matrixBody { background: white; border: 1px solid #ded7ea; border-radius: 8px; }
            QFrame#matrixNavigation { background: #f8f6fc; border: 1px solid #e5dfef; border-radius: 7px; }
            QTableView#sharedColumns { background: white; alternate-background-color: #faf9fd;
                border: 0; border-top: 1px solid #e5dfef; selection-background-color: #6236d3;
                selection-color: white; }
            QTableView#sharedColumns QHeaderView::section, QTableCornerButton::section {
                background: #f4f1f9; color: #665876; border: 0; border-bottom: 1px solid #ded7ea;
                padding: 0; font-size: 11px; }
            QSpinBox#matrixColumn::up-button, QSpinBox#matrixColumn::down-button { width: 0; height: 0; }
            QProgressBar { border: 0; border-radius: 2px; background: #eee8fa; }
            QProgressBar::chunk { border-radius: 2px; background: #7550cb; }
        """)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Calculating multiple alignment")
        layout.addWidget(self.progress)
        self.circularNote = QLabel(
            "Whole circular records use their stored origins and selected orientations. "
            "Choose homologous regions to assess correspondence.", self,
        )
        self.circularNote.setObjectName("muted")
        self.circularNote.setWordWrap(True)
        self.circularNote.hide()
        layout.addWidget(self.circularNote)
        self.matrixBody = self._buildMatrixBody()
        self.navigation = self._buildNavigation()
        layout.addWidget(self.matrixBody, 1)
        layout.addWidget(self.navigation)
        self.matrixBody.hide()
        self.navigation.hide()

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.exportFeedback = FittedLabel("", self)
        actions.addWidget(self.exportFeedback, 1)
        self.closeButton = QPushButton("Cancel", self)
        self.closeButton.setAutoDefault(False)
        self.closeButton.clicked.connect(self.reject)
        self.exportButton = QPushButton("Export aligned FASTA…", self)
        self.exportButton.setIcon(workspaceIcon("export", onAccent=True))
        self.exportButton.setAutoDefault(False)
        self.exportButton.clicked.connect(self._export)
        actions.addWidget(self.closeButton)
        actions.addWidget(self.exportButton)
        addDialogFooter(layout, actions, self.exportButton)
        self.exportButton.setEnabled(False)
        self.exportButton.hide()
        self._pool = QThreadPool(self)
        sources = tuple((role, doc.revision) for role, doc in zip(roles, docs, strict=True))
        self._worker = CalculationTask(self._request.requestId, lambda canceled: _calculate(sources, canceled))
        self._worker.signals.completed.connect(self._completed)
        controller.alignment.changed.connect(self._checkSources)
        controller.alignment.shuttingDown.connect(self.reject)
        self._pool.start(self._worker)

    def _buildMatrixBody(self) -> QFrame:
        body = QFrame(self)
        body.setObjectName("matrixBody")
        column = QVBoxLayout(body)
        column.setSpacing(0)
        column.setContentsMargins(1, 1, 1, 3)
        legend = QHBoxLayout()
        legend.setContentsMargins(12, 8, 12, 8)
        legend.setSpacing(12)
        heading = QLabel("Aligned sequences", body)
        heading.setObjectName("sectionTitle")
        legend.addWidget(heading, 1)
        for label, fill, ink in (
            ("Different from reference", AMBER_SOFT, AMBER), ("Gap", SELECTION, ACCENT_INK),
        ):
            swatch = QLabel(body)
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(f"background: {fill}; border: 1px solid {ink}; border-radius: 3px;")
            key = QHBoxLayout()
            key.setSpacing(5)
            key.addWidget(swatch)
            caption = QLabel(label, body)
            caption.setObjectName("muted")
            key.addWidget(caption)
            legend.addLayout(key)
        column.addLayout(legend)
        self.table = MatrixTable(body)
        self.table.setObjectName("sharedColumns")
        self.table.setAccessibleName("Shared alignment columns")
        self.table.setFont(workspaceMonoFont(14))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalHeader(SourceHeader(self.table))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setMinimumSectionSize(18)
        self.table.horizontalHeader().setFixedHeight(25)
        singleRow = self.table.verticalHeader().defaultSectionSize()
        self.table.setMinimumHeight(28 + singleRow + self.table.horizontalScrollBar().sizeHint().height())
        self.table.setCornerButtonEnabled(False)
        column.addWidget(self.table, 1)
        overview = QHBoxLayout()
        overview.setContentsMargins(12, 3, 12, 2)
        overview.setSpacing(9)
        start = QLabel("1", body)
        start.setObjectName("muted")
        self.lastColumn = QLabel(body)
        self.lastColumn.setObjectName("muted")
        self.overview = ColumnOverview(body)
        self.overview.valueChanged.connect(self._goToColumn)
        overview.addWidget(start)
        overview.addWidget(self.overview, 1)
        overview.addWidget(self.lastColumn)
        column.addLayout(overview)
        return body

    def _buildNavigation(self) -> QFrame:
        panel = QFrame(self)
        panel.setObjectName("matrixNavigation")
        row = QHBoxLayout(panel)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        source = QVBoxLayout()
        source.setSpacing(3)
        self.rowLabel = FittedLabel("", panel)
        self.rowLabel.setObjectName("sectionTitle")
        source.addWidget(self.rowLabel)
        self.cellLabel = QLabel(panel)
        self.cellLabel.setObjectName("muted")
        self.cellLabel.setTextFormat(Qt.TextFormat.PlainText)
        self.cellLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source.addWidget(self.cellLabel)
        row.addLayout(source, 1)
        columnLabel = QLabel("Go to column", panel)
        self.columnSelector = QSpinBox(panel)
        self.columnSelector.setObjectName("matrixColumn")
        self.columnSelector.setAccessibleName("Go to alignment column")
        self.columnSelector.setMinimumWidth(65)
        self.columnSelector.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.columnSelector.setGroupSeparatorShown(True)
        self.columnSelector.setKeyboardTracking(False)
        self.columnSelector.valueChanged.connect(self._goToColumn)
        columnLabel.setBuddy(self.columnSelector)
        row.addWidget(columnLabel)
        row.addWidget(self.columnSelector)
        return panel

    def _current(self) -> bool:
        workspace = self.controller.workspace
        state = self.controller.alignment
        available = {doc.documentId for doc in state.eligibleDocuments}
        return (
            not self._closed and (state.reference, *state.queries) == self._request.settings
            and workspace.taskContext(False) == self._request.context
            and all(item.documentId in available and workspace.taskInput(item.role, item.documentId) == item
                    for item in self._request.inputs)
        )

    @Slot()
    def _checkSources(self) -> None:
        if not self._closed and not self._current():
            self.controller.message.emit("Multiple alignment closed because its selected inputs changed.")
            self.reject()

    @Slot(int, object, object)
    def _completed(self, requestId: int, result: MultipleAlignmentResult | None, error: str | None) -> None:
        if self._closed or requestId != self._request.requestId:
            return
        if not self._current() or not self.controller.workspace.tasks.finish(self._request):
            self.reject()
            return
        expandFromProgress = self.height() <= 200
        self.progress.hide()
        self.closeButton.setText("Close")
        if result is None or error:
            self.statusLabel.setText(error or "Multiple alignment canceled.")
            return
        self.alignmentResult = result
        self.statusLabel.setText(
            f"{result.inputs[0].moleculeType} · {len(result.inputs)} sequences · "
            f"{result.columnCount:,} shared columns · {result.engineVersion}"
        )
        self.circularNote.setVisible(any(
            source.topology == "circular" and source.selection.featureId is None for source in result.inputs
        ))
        model = MatrixModel(result, self._names, self.table)
        header = self.table.horizontalHeader()
        header.setDefaultSectionSize(max(23, header.fontMetrics().horizontalAdvance(str(result.columnCount)) + 5))
        self.table.setModel(model)
        self.table.selectionModel().currentChanged.connect(self._showCell)
        with QSignalBlocker(self.columnSelector):
            self.columnSelector.setRange(1, result.columnCount)
        with QSignalBlocker(self.overview):
            self.overview.setResult(result)
        self.lastColumn.setText(f"{result.columnCount:,}")
        self.matrixBody.show()
        self.navigation.show()
        self.exportButton.show()
        if expandFromProgress:
            noteHeight = self.circularNote.heightForWidth(self.width() - 44) if self.circularNote.isVisible() else 0
            rowHeight = self.table.verticalHeader().defaultSectionSize()
            self.resize(self.width(), min(650, 330 + rowHeight * len(result.inputs) + noteHeight))
        self.table.setCurrentIndex(model.index(0, 0))
        self.exportButton.setEnabled(True)
        self.table.setFocus()

    def _showCell(self, current: QModelIndex, previous: QModelIndex) -> None:
        if current.isValid():
            model = self.table.model()
            self.rowLabel.setFullText(model.rowIdentity(current.row()))
            self.cellLabel.setText(model.cellDetail(current.row(), current.column()))
            self.table.setAccessibleDescription(self.cellLabel.text())
            with QSignalBlocker(self.columnSelector):
                self.columnSelector.setValue(current.column() + 1)
            with QSignalBlocker(self.overview):
                self.overview.setValue(current.column() + 1)
            if previous.column() != current.column():
                self.table.viewport().update()

    def _goToColumn(self, column: int) -> None:
        current = self.table.currentIndex()
        if current.isValid():
            target = current.siblingAtColumn(column - 1)
            self.table.setCurrentIndex(target)
            self.table.scrollTo(target, QAbstractItemView.ScrollHint.PositionAtCenter)

    def done(self, code: int) -> None:
        if not self._closed:
            self._closed = True
            self._worker.cancel()
            self.controller.workspace.tasks.cancel(self._request.owner, self._request.slot)
            self._pool.waitForDone()
        super().done(code)

    def _export(self) -> None:
        result = self.alignmentResult
        if result is None or not self._current():
            return
        self.exportFeedback.hide()
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export aligned FASTA", str(fileDialogDirectory() / "alignment.fasta"),
            "Aligned FASTA (*.fasta *.fa *.fas *.fna *.faa *.frn)",
        )
        if not filename or self._closed:
            return
        try:
            target = Path(filename)
            appendSuffix = not target.suffix
            if appendSuffix:
                target = target.with_suffix(".fasta")
            if target.suffix.lower() not in (".fasta", ".fa", ".fas", ".fna", ".faa", ".frn"):
                raise ValueError("Choose a FASTA filename.")
            version = exportDestinationVersion(target)

            def checkDestination() -> None:
                if not self._current():
                    raise ValueError("The selected alignment inputs changed.")
                self.controller.ensureFigureDestination(target)
                checkExportDestination(target, self._protected)

            checkDestination()
            checkExportDestinationVersion(target, version)
            if appendSuffix and version is not None and not confirmAction(
                self, "Replace existing file", f"Replace {target.name}?", "Replace",
            ):
                return
            with TemporaryDirectory(dir=target.parent, prefix=".strandfolio-alignment-") as directory:
                staged = Path(directory) / "alignment.fasta"
                with staged.open("w", encoding="utf-8") as handle:
                    handle.write(result.fastaText())
                    handle.flush()
                    os.fsync(handle.fileno())
                checkDestination()
                publishStagedExport(staged, target, version)
            message = f"Exported {target.name}."
            self.controller.message.emit(message)
        except (OSError, ValueError) as error:
            message = f"Could not export aligned FASTA. {error}"
            self.controller.operationFailed.emit(message)
        if not self._closed:
            self.exportFeedback.setFullText(message)
            self.exportFeedback.show()
