"""Connect the workspace session to the Qt interface."""

from collections.abc import Callable, Sequence
from pathlib import Path
import sqlite3
from uuid import uuid4

import Bio
from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication

from strandfolio.application.session import DocumentSession
from strandfolio.application.tasks import TaskRequest
from strandfolio.application.workspace import ClosePlan, DocumentTarget, Workspace
from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import MoleculeType, SequenceRevision
from strandfolio.gui.alignment_controller import AlignmentCoordinator
from strandfolio.gui.annotation_controller import AnnotationCoordinator
from strandfolio.gui.digest_controller import DEFAULT_RESTRICTION_CATALOGUE_PATH, DigestCoordinator
from strandfolio.gui.import_task import SequenceImportTask
from strandfolio.gui.pcr_controller import PcrCoordinator
from strandfolio.gui.pcr_task import ProjectTasks
from strandfolio.io.formats import (
    PlainTextSource, SequenceFormat, SequenceImportResult, TextNormalization, checkExportDestination,
)
from strandfolio.operations.conversion import (
    CdsTranslation,
    ConversionPreview,
    CopyFormat,
    DEFAULT_GENETIC_CODE,
    FrameTranslation,
    GeneticCodePreference,
    previewConversion,
)
from strandfolio.rendering.layout import LayerOptions
from strandfolio.storage.catalogues import DEFAULT_LADDER_CATALOGUE_PATH
from strandfolio.storage.feature_catalogue import DEFAULT_FEATURE_CATALOGUE_PATH
from strandfolio.storage.projects import ProjectRepository, RecordPreferenceConflict


_OPERATION_ERRORS = (OSError, ValueError, KeyError, sqlite3.Error)


class WorkspaceController(QObject):
    """Notify views of changes to the workspace and apply user actions to the active document."""

    documentAboutToChange = Signal()
    documentChanged = Signal()
    nameChanged = Signal()
    closeRequested = Signal(str)
    editNameRequested = Signal(str)
    batchReviewRequested = Signal(int, object)
    textTypeRequested = Signal(int, object)
    normalizationReviewRequested = Signal(int, object, object)
    operationFailed = Signal(str)
    selectionChanged = Signal()
    collectionChanged = Signal()
    projectChanged = Signal()
    layersChanged = Signal()
    mapFeaturesChanged = Signal()
    geneticCodeChanged = Signal()
    translationChanged = Signal()
    loadingChanged = Signal(bool)
    message = Signal(str)
    importFailed = Signal(str)
    sequenceOpened = Signal()

    def __init__(
        self, parent: QObject | None = None, *,
        restrictionCataloguePath: str | Path = DEFAULT_RESTRICTION_CATALOGUE_PATH,
        ladderCataloguePath: str | Path = DEFAULT_LADDER_CATALOGUE_PATH,
        featureCataloguePath: str | Path = DEFAULT_FEATURE_CATALOGUE_PATH,
    ) -> None:
        super().__init__(parent)
        self.workspace = Workspace()
        self._emptySession = DocumentSession()
        self.activationGeneration = 0
        self._importRequest: TaskRequest | None = None
        self._afterIntake: Callable[[DocumentTarget], None] | None = None
        self._importCommitId = ""
        self._independentImport = False
        self._review: SequenceImportResult | None = None
        self._textSource: PlainTextSource | None = None
        self.layerOptions = LayerOptions()
        self.cdsTranslations: tuple[CdsTranslation, ...] = ()
        self._cdsByFeature: dict[str, CdsTranslation] = {}
        self.frameTranslation: FrameTranslation | None = None
        self._copyBinding: tuple[ConversionPreview, DocumentTarget, SequenceSelection | None] | None = None
        self.selectionChanged.connect(self._clearCopyPreview)
        self._requestId = 0
        self._workers: dict[int, SequenceImportTask] = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self.projectTasks = ProjectTasks(self)
        self.analysis = DigestCoordinator(self, restrictionCataloguePath, ladderCataloguePath)
        self.pcr = PcrCoordinator(self)
        self.alignment = AlignmentCoordinator(self)
        self.annotations = AnnotationCoordinator(self, featureCataloguePath)

    @property
    def session(self) -> DocumentSession:
        doc = self.workspace.activeDocument
        return doc.session if doc is not None else self._emptySession

    @property
    def importResult(self) -> SequenceImportResult | None:
        doc = self.workspace.activeDocument
        return doc.capture if doc is not None else None

    @property
    def displayName(self) -> str:
        doc = self.workspace.activeDocument
        return self.workspace.displayName(doc) if doc is not None else ""

    @property
    def originalName(self) -> str:
        doc = self.workspace.activeDocument
        return doc.originalName if doc is not None else ""

    def cdsForFeature(self, featureId: str) -> CdsTranslation | None:
        """Return the cached CDS translation result, including unavailable results and warnings."""
        return self._cdsByFeature.get(featureId)

    @property
    def geneticCodePreference(self) -> GeneticCodePreference:
        document = self.workspace.activeDocument
        return self.workspace.geneticCodePreference(document) if document else DEFAULT_GENETIC_CODE

    def setGeneticCodePreference(self, tableId: int | None, overrideAnnotated: bool = False) -> bool:
        """Save the selected genetic code for this record and refresh its translation views."""
        document = self.workspace.activeDocument
        if document is None:
            return False
        try:
            changed = self.workspace.setGeneticCodePreference(
                self.workspace.targetFor(document.documentId),
                GeneticCodePreference(tableId, overrideAnnotated),
            )
        except RecordPreferenceConflict as error:
            self.refreshRecordPreferences(document.documentId)
            self.operationFailed.emit(str(error))
            return False
        except _OPERATION_ERRORS as error:
            self.operationFailed.emit(str(error))
            return False
        if changed:
            self._refreshDerivedViews()
            self.geneticCodeChanged.emit()
            self.translationChanged.emit()
        return changed

    @property
    def translationFrame(self) -> int | None:
        document = self.workspace.activeDocument
        return document.viewState.translationFrame if document is not None else None

    def setTranslationFrame(self, frame: int | None) -> bool:
        """Display annotated translations or one signed free frame for the active document."""
        document = self.workspace.activeDocument
        if document is None or document.revision.moleculeType == "protein":
            return False
        if frame is not None and (type(frame) is not int or frame not in (-3, -2, -1, 1, 2, 3)):
            raise ValueError("Choose a signed translation frame from ±1 to ±3.")
        if frame == document.viewState.translationFrame:
            return False
        document.viewState.translationFrame = frame
        self._refreshDerivedViews()
        self.translationChanged.emit()
        return True

    def setLayerOptions(self, options: LayerOptions) -> None:
        """Update view-only display layers without modifying record data."""
        if options != self.layerOptions:
            self.layerOptions = options
            self.layersChanged.emit()

    def setMapFeatureTypes(self, types: frozenset[str] | None) -> None:
        """Set the active document's map filter. None shows every feature type."""
        document = self.workspace.activeDocument
        if document is not None and document.viewState.mapFeatureTypes != types:
            document.viewState.mapFeatureTypes = types
            self.mapFeaturesChanged.emit()

    def loadSequence(
        self, path: str | Path, moleculeType: MoleculeType | None = None,
        *, formatName: SequenceFormat | None = None, independent: bool = False,
        afterIntake: Callable[[DocumentTarget], None] | None = None,
    ) -> None:
        """Read one bounded capture and retain existing tabs until its review succeeds."""
        requestId = self._beginImport(
            (str(path), moleculeType, formatName, independent), f"Opening {Path(path).name}",
            independent=independent, afterIntake=afterIntake,
        )
        worker = SequenceImportTask(
            requestId, Path(path), moleculeType, formatName,
            lookupProject=None if independent else self.workspace.project,
        )
        self._startImportWorker(worker)

    def loadPastedText(
        self, pastedText: str, moleculeType: MoleculeType, *,
        afterIntake: Callable[[DocumentTarget], None] | None = None,
    ) -> None:
        """Parse the supplied clipboard snapshot through the same review as file intake."""
        requestId = self._beginImport(
            ("clipboard", moleculeType), "Opening pasted sequence", afterIntake=afterIntake,
        )
        self._startImportWorker(SequenceImportTask(requestId, pastedText, moleculeType, "text"))

    def _beginImport(
        self, settings: tuple, description: str, *, independent: bool = False,
        afterIntake: Callable[[DocumentTarget], None] | None = None,
    ) -> int:
        self._endImport()
        self.analysis.cancelOpenDigestion()
        self._requestId += 1
        requestId = self._requestId
        self._importRequest = self.workspace.tasks.begin(
            "workspace", "import", settings=settings,
            versions=(("Biopython", Bio.__version__),), bindActive=True, description=description,
        )
        self._importCommitId = str(uuid4())
        self._independentImport = independent
        self._afterIntake = afterIntake
        self.loadingChanged.emit(True)
        self.message.emit("Opening sequence…")
        return requestId

    def _startImportWorker(self, worker: SequenceImportTask) -> None:
        self._workers[worker.requestId] = worker
        worker.signals.succeeded.connect(self._onImported)
        worker.signals.textTypeNeeded.connect(self._onTextTypeNeeded)
        worker.signals.normalizationNeeded.connect(self._onNormalizationNeeded)
        worker.signals.failed.connect(self._onImportFailed)
        self._pool.start(worker)

    def _endImport(self) -> None:
        if self._importRequest is None:
            return
        self.workspace.tasks.finish(self._importRequest)
        worker = self._workers.get(self._requestId)
        if worker is not None:
            worker.cancel()
        self._review = None
        self._textSource = None
        self._importRequest = None
        self._afterIntake = None
        self.loadingChanged.emit(False)

    def cancelImport(self) -> None:
        """Withdraw the active request, including any unconfirmed batch."""
        self._endImport()
        self.analysis.cancelOpenDigestion()
        self.message.emit("Import canceled.")

    def _canPublish(self, requestId: int) -> bool:
        return (requestId == self._requestId and self._importRequest is not None
                and self.workspace.tasks.isCurrent(self._importRequest))

    @Slot(int, object)
    def _onTextTypeNeeded(self, requestId: int, source: PlainTextSource) -> None:
        if self._workers.pop(requestId, None) is not None and self._canPublish(requestId):
            self._textSource = source
            self.textTypeRequested.emit(requestId, source)

    def chooseTextType(
        self, requestId: int, source: PlainTextSource, moleculeType: MoleculeType | None,
    ) -> None:
        """Resume validation of the captured file only while its type dialog is current."""
        if self._textSource is not source or not self._canPublish(requestId):
            return
        self._textSource = None
        if moleculeType is None:
            self.cancelImport()
            return
        self._startImportWorker(SequenceImportTask(requestId, source, moleculeType, "text"))

    @Slot(int, object, object)
    def _onNormalizationNeeded(
        self, requestId: int, result: SequenceImportResult, preview: TextNormalization,
    ) -> None:
        if self._workers.pop(requestId, None) is not None and self._canPublish(requestId):
            self._review = result
            self.normalizationReviewRequested.emit(requestId, result, preview)

    @Slot(int, object, bool)
    def _onImported(self, requestId: int, result: SequenceImportResult, knownBatch: bool) -> None:
        if self._workers.pop(requestId, None) is None or not self._canPublish(requestId):
            return
        needsReview = len(result.records) > 1 and (
            self._independentImport or not (knownBatch or self.workspace.hasOpenIntake(result))
        )
        self._review = result
        if needsReview:
            self.batchReviewRequested.emit(requestId, result)
        else:
            self.finishReview(requestId, result, True)

    def finishReview(self, requestId: int, result: SequenceImportResult, accepted: bool) -> None:
        """Commit the same captured result that the user approved, while the request is still current."""
        if self._review is not result or not self._canPublish(requestId):
            return
        if not accepted:
            self.cancelImport()
            return

        openedTarget = None

        def commitReviewedCapture() -> None:
            nonlocal openedTarget
            # No modal, signal or event processing between the final relevance
            # check and the repository's synchronous intake transaction.
            if self._review is not result or not self._canPublish(requestId):
                raise ValueError("The reviewed import is no longer current.")
            self.workspace.tasks.finish(self._importRequest)
            self._review = None
            self._importRequest = None
            documents = self.workspace.openImport(
                result, requestId=self._importCommitId, independent=self._independentImport,
            )
            openedTarget = self.workspace.targetFor(documents[0].documentId)

        afterIntake, self._afterIntake = self._afterIntake, None
        opened = self._changeWorkspace(commitReviewedCapture, onError=self.importFailed.emit)
        if self._requestId == requestId:
            self.loadingChanged.emit(False)
        if opened and self._requestId == requestId:
            message = (f"Opened {len(result.records)} record(s). Original file unchanged."
                       if result.sourcePath is not None else "Opened pasted sequence.")
            self.message.emit(message)
            if afterIntake is None:
                self.sequenceOpened.emit()
            else:
                assert openedTarget is not None
                afterIntake(openedTarget)

    @Slot(int, str)
    def _onImportFailed(self, requestId: int, message: str) -> None:
        if self._workers.pop(requestId, None) is not None and self._canPublish(requestId):
            self._endImport()
            self.importFailed.emit(message)

    def _refreshDerivedViews(self) -> None:
        self._clearCopyPreview()
        preference = self.geneticCodePreference
        self.cdsTranslations = self.session.cdsTranslationsFor(preference)
        self._cdsByFeature = {item.featureId: item for item in self.cdsTranslations}
        frame = self.translationFrame
        revision = self.session.current
        if frame is None and not self.cdsTranslations and revision is not None:
            if revision.moleculeType != "protein":
                frame = 1
        self.frameTranslation = (
            self.session.frameTranslationFor(frame, preference) if frame is not None else None
        )

    def _activeKey(self) -> tuple[ProjectRepository | None, str | None, int | None]:
        doc = self.workspace.activeDocument
        if doc is None:
            return self.workspace.project, None, None
        return self.workspace.project, doc.documentId, doc.session.revisionVersion

    def _tryOperation(self, action: Callable[[], object]) -> bool:
        """Report a desktop action failure through the common workspace signal."""
        try:
            action()
        except _OPERATION_ERRORS as error:
            self.operationFailed.emit(str(error))
            return False
        return True

    def _changeWorkspace(
        self, operation: Callable[[], object], *, onError: Callable[[str], None] | None = None,
    ) -> bool:
        """Capture the outgoing viewport and notify views of the actual committed state.

        A failed group save can still have committed an earlier tab. Reflect that
        change before displaying the failure, leaving every unclosed tab usable.
        """
        before = self._activeKey()
        self.documentAboutToChange.emit()
        errorMessage = None
        try:
            operation()
        except _OPERATION_ERRORS as error:
            errorMessage = str(error)
        activeChanged = before != self._activeKey()
        if activeChanged:
            self.activationGeneration += 1
            document = self.workspace.activeDocument
            if document is not None:
                try:
                    self.workspace.refreshRecordPreferences(document)
                except _OPERATION_ERRORS as error:
                    self.operationFailed.emit(f"Could not refresh record preferences. {error}")
            self._refreshDerivedViews()
        if self._importRequest is not None and not self.workspace.tasks.isCurrent(self._importRequest):
            self._endImport()
        self.collectionChanged.emit()
        if activeChanged:
            self.documentChanged.emit()
        if errorMessage is not None:
            (onError or self.operationFailed.emit)(errorMessage)
            return False
        return True

    def selectDocument(self, documentId: str) -> None:
        if self.workspace.activeDocumentId != documentId:
            self._changeWorkspace(lambda: self.workspace.activate(documentId))

    def closeDocument(self) -> None:
        """Run the close review for the requested tab."""
        doc = self.workspace.activeDocument
        if doc is not None:
            self.closeRequested.emit(doc.documentId)

    def applyClose(
        self, plan: ClosePlan, saves: dict[str, str], project: ProjectRepository | None = None,
        *, beforeRemove: Callable[[], None] | None = None,
    ) -> bool:
        return self._changeWorkspace(lambda: self.workspace.closeDocuments(
            plan, saves=saves, project=project, beforeRemove=beforeRemove,
        ))

    def saveDocument(
        self, target: DocumentTarget, name: str, project: ProjectRepository | None = None,
    ) -> bool:
        return self._changeWorkspace(lambda: self.workspace.saveDocument(
            target, name, project=project,
        ))

    def openSavedCopy(
        self, project: ProjectRepository, capture: SequenceImportResult, saved: SequenceRevision,
    ) -> bool:
        """Register the tab for a completed named save and keep its originating workflow visible."""
        return self._changeWorkspace(lambda: self.workspace.openSavedCopy(project, capture, saved))

    def cancelProductAdoption(self) -> bool:
        """Discard the active product draft and restore its open source when available."""
        document = self.workspace.activeDocument
        if document is None or not document.isDraft or document.adoptedTemplate is None:
            return False
        template = document.adoptedTemplate
        source = next((
            item.documentId for item in self.workspace.documents
            if item.revision.revisionId == template.revisionId
        ), None)
        plan = self.workspace.planClose((document.documentId,))

        def cancel() -> None:
            self.workspace.closeDocuments(plan)
            if source is not None:
                self.workspace.activate(source)

        return self._changeWorkspace(cancel)

    def refreshRecordPreferences(self, documentId: str | None = None) -> None:
        """Reflect external preference edits in labels and translation controls."""
        documentId = documentId or self.workspace.activeDocumentId
        if documentId is None:
            return
        try:
            document = self.workspace.getDocument(documentId)
            nameChanged, codeChanged = self.workspace.refreshRecordPreferences(document)
        except _OPERATION_ERRORS as error:
            self.operationFailed.emit(f"Could not refresh record preferences. {error}")
            return
        if nameChanged:
            self.nameChanged.emit()
        if codeChanged:
            self._refreshDerivedViews()
            self.geneticCodeChanged.emit()
            self.translationChanged.emit()

    def setPreferredName(self, target: DocumentTarget, name: str, *, expectedName: str | None = None) -> bool:
        try:
            updated = self.workspace.setPreferredName(target, name, expectedName=expectedName)
        except RecordPreferenceConflict as error:
            self.refreshRecordPreferences(target.documentId)
            self.operationFailed.emit(str(error))
            return False
        except _OPERATION_ERRORS as error:
            self.operationFailed.emit(str(error))
            return False
        if updated:
            self.nameChanged.emit()
        return updated

    def openSavedRevision(self, revisionId: str) -> bool:
        """Open a project revision by stable identity, even after its source moves."""
        opened = self._changeWorkspace(lambda: self.workspace.openSavedRevision(revisionId))
        if opened:
            self.sequenceOpened.emit()
        return opened

    def openExactRevision(
        self, revisionId: str, retained: SequenceRevision | None, *,
        afterOpen: Callable[[DocumentTarget], None] | None = None,
    ) -> bool:
        """Open the specified snapshot and notify the tool that requested it."""
        openedTarget = None

        def openSnapshot() -> None:
            nonlocal openedTarget
            document = self.workspace.openExactRevision(revisionId, retained=retained)
            openedTarget = self.workspace.targetFor(document.documentId)

        opened = self._changeWorkspace(openSnapshot)
        if opened:
            if afterOpen is None:
                self.sequenceOpened.emit()
            else:
                assert openedTarget is not None
                afterOpen(openedTarget)
        return opened

    def setProject(self, project: ProjectRepository | None) -> bool:
        """Start a fresh workspace after all open documents have passed close review."""
        if self.workspace.documents:
            self.operationFailed.emit("Close the open documents before changing projects.")
            return False
        self._endImport()
        self.workspace.setProject(project)
        self.pcr.reset()
        self.analysis.reset()
        self.activationGeneration += 1
        self._refreshDerivedViews()
        self.projectChanged.emit()
        self.collectionChanged.emit()
        self.documentChanged.emit()
        return True

    def checkFigureTarget(self, target: DocumentTarget) -> None:
        """Reject a sequence figure if its source changed while the preview or picker was open."""
        if not self.workspace.targetIsCurrent(target):
            raise ValueError("The active sequence changed. Open the figure preview again.")

    def openSourcePaths(self) -> tuple[Path, ...]:
        """Return paths of original captures that an export must preserve."""
        return tuple(
            path for doc in self.workspace.documents if doc.capture is not None
            for path in (doc.capture.sourcePath, doc.capture.sourceOrigin) if path is not None
        )

    def ensureFigureDestination(self, destination: Path) -> None:
        """Protect every open original and all managed project files from figure export."""
        checkExportDestination(destination, self.openSourcePaths())
        if self.workspace.project is not None:
            self.workspace.project.checkFigureDestination(destination)

    @Slot(int, int)
    def select(self, anchor: int, caret: int) -> None:
        if self.session.current is not None and self.session.select(anchor, caret):
            self.selectionChanged.emit()

    def selectDisplayRange(self, first: int, last: int) -> None:
        """Apply one-based inclusive coordinates while preserving an unchanged multipart selection."""
        revision, selected = self.session.current, self.session.selection
        if revision is None or not (1 <= first <= len(revision) and 1 <= last <= len(revision)):
            return
        circular = revision.topology == "circular"
        if (selected is not None and selected.isCompound
                and selected.displayRange(len(revision), circular=circular) == (first, last)):
            return
        if first <= last:
            self.select(first - 1, last)
        elif circular:
            self.selectSegments(((first - 1, len(revision)), (0, last)))

    @Slot(int, int)
    def selectCircular(self, anchor: int, caret: int) -> None:
        """Select a contiguous or origin-wrapping interval on circular records."""
        if self.session.current is None:
            return
        try:
            changed = self.session.selectCircular(anchor, caret)
        except ValueError as error:
            self.message.emit(str(error))
            return
        if changed:
            self.selectionChanged.emit()

    def selectSegments(
        self,
        segments: Sequence[SelectionSegment],
        *,
        anchor: int | None = None,
        caret: int | None = None,
    ) -> None:
        """Select explicit ordered intervals without filling gaps."""
        if self.session.current is None:
            return
        try:
            changed = self.session.selectSegments(segments, anchor=anchor, caret=caret)
        except ValueError as error:
            self.message.emit(str(error))
            return
        if changed:
            self.selectionChanged.emit()

    @Slot(str)
    def selectWholeFeature(self, featureId: str) -> None:
        """Select all exact ordered segments of a feature without including gaps."""
        try:
            changed = self.session.selectWholeFeature(featureId)
        except (KeyError, ValueError) as error:
            self.message.emit(str(error))
            return
        if changed:
            self.selectionChanged.emit()
        feature = self.session.current.getFeature(featureId)
        selected = self.session.selection
        unit = "aa" if self.session.current.moleculeType == "protein" else "nt"
        if len(feature.parts) > 1 and selected is not None:
            self.message.emit(
                f"{feature.label}: {len(feature.parts)} segments ({len(selected):,} {unit}, gaps excluded)."
            )
        else:
            self.message.emit(
                f"{feature.label}: selected. Copy uses stored sequence orientation."
            )

    @Slot(str, int)
    def selectFeature(self, featureId: str, partIndex: int | None = 0) -> None:
        """Navigate to one explicitly identified feature segment or whole feature."""
        if partIndex is None:
            self.selectWholeFeature(featureId)
            return
        try:
            changed = self.session.selectFeature(featureId, partIndex)
        except (KeyError, ValueError) as error:
            self.message.emit(str(error))
            return
        if changed:
            self.selectionChanged.emit()
        feature = self.session.current.getFeature(featureId)
        self.message.emit(
            f"{feature.label}: segment {partIndex + 1} of {len(feature.parts)} selected. "
            "Copy uses stored sequence orientation."
        )

    @property
    def copyTranslationFeatures(self) -> tuple[SequenceFeature, ...]:
        """List CDS interpretations overlapping the current copy selection."""
        revision = self.session.current
        selection = self.session.selection
        if revision is None or selection is None or not len(selection):
            return ()
        return tuple(
            feature for feature in revision.features
            if feature.featureId in self._cdsByFeature and (
                feature.featureId == selection.featureId or any(
                    part.isSelectable and selection.clippedTo(part.start, part.end)
                    for part in feature.parts
                )
            )
        )

    def copyPreview(
        self, copyFormat: CopyFormat, *, featureId: str | None = None, frame: int | None = None,
    ) -> ConversionPreview:
        """Preview the ordered selection using the same mapped translation as the display."""
        document = self.workspace.activeDocument
        if document is None:
            raise ValueError("Open a sequence before previewing a copy.")
        selection = document.session.selection
        translation = None
        if (copyFormat == "protein" and document.revision.moleculeType != "protein"
                and selection is not None and len(selection)):
            translation = self._copyTranslation(selection, featureId, frame)
        result = previewConversion(document.revision, selection, copyFormat, translation=translation)
        self._copyBinding = (result, self.workspace.targetFor(document.documentId), selection)
        return result

    def _copyTranslation(
        self, selection: SequenceSelection, featureId: str | None, frame: int | None,
    ) -> CdsTranslation | FrameTranslation:
        if featureId is None and frame is None:
            frame = self.translationFrame
            if frame is None:
                linked = self._cdsByFeature.get(selection.featureId)
                if linked is not None:
                    return linked
                features = self.copyTranslationFeatures
                if features:
                    return self._cdsByFeature[features[0].featureId]
        if featureId is not None:
            translation = self.cdsForFeature(featureId)
            if translation is None or frame is not None:
                raise ValueError("Choose one CDS or free frame from this sequence.")
            return translation
        frame = 1 if frame is None else frame
        if self.frameTranslation is not None and self.frameTranslation.frame == frame:
            return self.frameTranslation
        return self.session.frameTranslationFor(frame, self.geneticCodePreference)

    def _clearCopyPreview(self) -> None:
        self._copyBinding = None

    def copyConversion(self, result: ConversionPreview) -> bool:
        """Copy the displayed text after confirming its document and selection are current."""
        binding = self._copyBinding
        if (binding is None or binding[0] is not result or not self.workspace.targetIsCurrent(binding[1])
                or binding[2] != self.session.selection):
            self.message.emit("The copy preview has changed. Preview the current selection again.")
            return False
        if not result.isAvailable:
            if result.warnings:
                self.message.emit(result.warnings[0])
            return False
        try:
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(result.text)
                if clipboard.text() == result.text:
                    self.message.emit(f"Copied {len(result.text):,} letters · {result.orientation}.")
                    return True
        except RuntimeError:
            pass
        self.message.emit("Clipboard unavailable. Copy from the selectable preview.")
        return False

    def copySelection(self) -> bool:
        """Copy exact selected letters, retaining case and stored orientation."""
        return self._copyByFormat("stored")

    def copyReverseComplement(self) -> bool:
        """Copy selected nucleic-acid segments as a 5′→3′ reverse complement."""
        return self._copyByFormat("reverse")

    def _copyByFormat(self, copyFormat: CopyFormat) -> bool:
        selection = self.session.selection
        if selection is None or not len(selection):
            return False
        return self.copyConversion(self.copyPreview(copyFormat))
