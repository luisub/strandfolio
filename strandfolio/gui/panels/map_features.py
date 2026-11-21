"""Map-only feature type controls for the Sequence Layers menu."""

from collections import Counter

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QMenu, QScrollArea, QVBoxLayout, QWidget,
)

from strandfolio.application.map_features import LANDMARK_TYPES, mapFeatureType
from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.dialog_layout import dialogLayout


class MapFeaturesMenu(QMenu):
    def __init__(self, controller: WorkspaceController, parent: QMenu) -> None:
        super().__init__("Map features", parent)
        self.controller = controller
        self.landmarks = self.addAction("Landmarks")
        self.allFeatures = self.addAction("All features")
        self.chooseTypes = self.addAction("Choose types…")
        group = QActionGroup(self)
        for action in (self.landmarks, self.allFeatures, self.chooseTypes):
            action.setCheckable(True)
            group.addAction(action)
        self.landmarks.triggered.connect(lambda: controller.setMapFeatureTypes(LANDMARK_TYPES))
        self.allFeatures.triggered.connect(lambda: controller.setMapFeatureTypes(None))
        self.chooseTypes.triggered.connect(self._chooseTypes)
        controller.documentChanged.connect(self._refresh)
        controller.mapFeaturesChanged.connect(self._refresh)
        controller.layersChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        document = self.controller.workspace.activeDocument
        self.setEnabled(document is not None and document.revision.moleculeType != "protein"
                        and self.controller.layerOptions.showFeatures)
        types = document.viewState.mapFeatureTypes if document else LANDMARK_TYPES
        action = self.chooseTypes
        if types is None or (document is not None and document.revision.moleculeType == "protein"):
            action = self.allFeatures
        elif types == LANDMARK_TYPES:
            action = self.landmarks
        action.setChecked(True)

    def _chooseTypes(self) -> None:
        workspace = self.controller.workspace
        document = workspace.activeDocument
        if document is None:
            return
        target = workspace.targetFor(document.documentId)
        types = document.viewState.mapFeatureTypes
        counts = Counter(mapFeatureType(f) for f in document.revision.features if f.type != "primer_bind")
        initial = frozenset(counts) if types is None else types.intersection(counts)
        dialog = QDialog(self)
        dialog.setObjectName("mapFeatureTypesDialog")
        dialog.setWindowTitle("Choose map feature types")
        layout = dialogLayout(dialog)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        choices = QWidget(scroll)
        column = QVBoxLayout(choices)
        checkboxes = {}
        for featureType, count in sorted(counts.items(), key=lambda entry: entry[0].casefold()):
            checkbox = QCheckBox(f"{featureType} ({count})", choices)
            checkbox.setChecked(featureType in initial)
            checkboxes[featureType] = checkbox
            column.addWidget(checkbox)
        column.addStretch()
        scroll.setWidget(choices)
        layout.addWidget(scroll)
        dialog.resize(320, 360)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and workspace.targetIsCurrent(target) and (
            workspace.activeDocumentId == target.documentId
        ):
            selected = frozenset(key for key, checkbox in checkboxes.items() if checkbox.isChecked())
            if selected != initial:
                self.controller.setMapFeatureTypes(selected)
        self._refresh()
        dialog.deleteLater()
