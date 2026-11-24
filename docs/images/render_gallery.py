"""Capture the README figures from the native app in a disposable project."""

import argparse
from collections.abc import Callable
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication, QSplitter, QWidget

from databases.enzymes.build_catalogue import buildDatabase
from strandfolio.application.workspace import WorkspaceDocument
from strandfolio.domain.features import SequenceFeature
from strandfolio.gui.app import configureWorkspaceApplication
from strandfolio.gui.panels.digest import DigestDialog
from strandfolio.gui.views.pcr_result import PcrResultDialog
from strandfolio.gui.window import MainWindow
from strandfolio.io.formats import readSequence
from strandfolio.operations.alignment import AlignmentSettings
from strandfolio.operations.conversion import reverseComplementBases
from strandfolio.operations.pcr import PcrRun
from strandfolio.rendering.maps import MapFigureSource
from strandfolio.storage.projects import ProjectRepository


DATABASES = Path(__file__).resolve().parents[2] / "databases"
MAPS = DATABASES / "plasmids/maps"


def waitFor(ready: Callable[[], bool], action: str) -> None:
    """Let the Qt event loop service workers and fail if the requested view never arrives."""
    if ready():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if ready() else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(15000)
    poll.start(20)
    loop.exec()
    if not ready():
        raise RuntimeError(f"Timed out waiting for {action}.")


def capture(widget: QWidget, path: Path) -> None:
    QApplication.processEvents()
    if not widget.isVisible() or not widget.grab().save(str(path)):
        raise OSError(f"Could not capture {path.name}.")
    print(path.name, flush=True)


def openMap(window: MainWindow, filename: str, name: str) -> WorkspaceDocument:
    controller = window.controller
    path = MAPS / filename
    controller.loadSequence(path)
    waitFor(lambda: controller.importResult is not None
            and controller.importResult.sourcePath == path, filename)
    workspace = controller.workspace
    target = workspace.targetFor(workspace.activeDocumentId)
    if not controller.saveDocument(target, name):
        raise RuntimeError(f"Could not save the imported {name} to the temporary project.")
    return workspace.activeDocument


def selectFeatureInTracks(window: MainWindow, feature: SequenceFeature) -> None:
    """Select a feature with one preceding sequence row visible for context."""
    window.textSizeSpinBox.setValue(14)
    window.mainSplitter.setSizes([844, 376])
    window.controller.selectWholeFeature(feature.featureId)
    QApplication.processEvents()
    tracks = window.sequenceView
    firstRow = max(0, feature.parts[0].start // tracks.layoutGeometry.basesPerRow - 1)
    tracks.verticalScrollBar().setValue(tracks.rowIndex.rowTop(firstRow))


def capturePuc19(window: MainWindow, output: Path) -> str:
    """Show the annotated pUC19 example and return its saved revision for the digest."""
    controller = window.controller
    digest = controller.analysis
    revision = openMap(window, "pUC19.gb", "pUC19").revision
    if revision.name != "pUC19" or len(revision) != 2686 or revision.topology != "circular":
        raise ValueError("The gallery requires the 2,686 bp circular pUC19 record.")
    mcs, = (feature for feature in revision.features if feature.label == "MCS")
    waitFor(lambda: digest.scan is not None, "the pUC19 map")
    digest.setOverlayNames(())
    selectFeatureInTracks(window, mcs)
    window.inspectorScroll.verticalScrollBar().setValue(0)
    capture(window, output / "sequence-workspace.png")
    return revision.revisionId


def captureReporter(
    window: MainWindow, output: Path,
) -> tuple[WorkspaceDocument, WorkspaceDocument, PcrRun]:
    """Follow the selected reporter CDS through PCR, the product preview and saved ancestry."""
    controller = window.controller
    pcr = controller.pcr
    reporter = openMap(window, "pIAKA1s271_11_L30_01.gb", "SB2CL GFP reporter")
    gfp = next(feature for feature in reporter.revision.features if feature.label == "GFPmut3_E0040")
    waitFor(lambda: controller.analysis.scan is not None, "the reporter map")
    controller.analysis.setOverlayNames(())
    selectFeatureInTracks(window, gfp)

    window.workflows.setCurrentWidget(window.pcrView)
    window.pcrView.textSize.setValue(14)
    selected = controller.session.selectedText
    pcr.setPrimerInputs(selected[:20], reverseComplementBases(selected[-20:], "DNA"))
    if not pcr.runPcr():
        raise RuntimeError(pcr.unavailableReason)
    waitFor(lambda: isinstance(QApplication.activeModalWidget(), PcrResultDialog), "the PCR result")
    dialog = QApplication.activeModalWidget()
    run = dialog.run
    product, = run.products
    if product.revision.sequence != selected or product.sourceSegments != ((2489, 3206),):
        raise ValueError("The simulated product does not span the GFP annotation.")
    capture(dialog, output / "pcr-product.png")
    dialog.accept()
    window.pcrView.showProduct(product, "forward")
    caret = controller.session.selection.caret
    controller.select(caret, caret)
    waitFor(window.pcrView.structureReview.temperatures.isVisible, "primer quality summary")
    capture(window, output / "pcr.png")

    if not pcr.adoptProduct(run.operationId, product.outputKey):
        raise RuntimeError("Could not reopen the simulated GFP product.")
    workspace = controller.workspace
    if not controller.saveDocument(workspace.targetFor(workspace.activeDocumentId), "GFP amplicon"):
        raise RuntimeError("Could not save the GFP product.")
    history = window.historyView
    window.workflows.setCurrentWidget(history)
    waitFor(lambda: history.ancestry.revisionList.rowCount >= 2, "the PCR ancestry diagram")
    capture(window, output / "history.png")

    return reporter, workspace.activeDocument, run


def captureAlignments(
    window: MainWindow, reporter: WorkspaceDocument, amplicon: WorkspaceDocument, output: Path,
) -> None:
    controller = window.controller
    gfp = next(feature for feature in reporter.revision.features if feature.label == "GFPmut3_E0040")
    dryad = openMap(window, "pBH4-His-SUMO-GFP-LPETGG.gb", "pBH4-His-SUMO-GFP-LPETGG")
    fusion = next(feature for feature in dryad.revision.features
                  if feature.type == "CDS" and feature.label == "Translation 847-1953")
    window.workflows.setCurrentWidget(window.alignmentView)
    alignment = controller.alignment
    alignment.setReference(reporter.documentId, gfp.featureId)
    for query in tuple(alignment.queries):
        alignment.setQuery(query.documentId, False)
    alignment.setQuery(dryad.documentId, True)
    alignment.setQueryRegion(dryad.documentId, fusion.featureId)
    alignment.setSettings(AlignmentSettings(mode="local"))
    if not alignment.runAlignment():
        raise RuntimeError("Could not start the GFP alignment.")
    waitFor(lambda: alignment.activeResult is not None, "the fluorescence reporter alignment")
    result = alignment.activeResult
    selectedFeatures = result.reference.selection.featureId, result.query.selection.featureId
    if selectedFeatures != (gfp.featureId, fusion.featureId):
        raise ValueError("The displayed comparison must use both deposited CDS annotations.")
    window.alignmentView.columnSelector.setValue(result.differences[0].start + 1)
    print(f"GFP alignment: {result.matches}/{result.pairedLength} matching paired bases")
    size = window.size()
    window.resize(1280, 940)
    try:
        capture(window, output / "alignment.png")
    finally:
        window.resize(size)

    alignment.setQuery(amplicon.documentId, True)
    view = window.alignmentView
    view.multipleButton.click()
    matrix = view.multipleDialog
    if matrix is None:
        raise RuntimeError("The three selected inputs could not be opened for alignment.")
    waitFor(lambda: matrix.alignmentResult is not None, "the shared-column alignment")
    shared = matrix.alignmentResult
    column = next(index for index, symbols in enumerate(zip(*shared.alignedRows, strict=True))
                  if index >= shared.columnCount // 2 and "-" not in symbols and len(set(symbols)) > 1)
    row = next(index for index, source in enumerate(shared.inputs)
               if source.revisionId == dryad.revision.revisionId)
    cell = matrix.table.model().index(row, column)
    matrix.table.setCurrentIndex(cell)
    matrix.table.scrollTo(cell, matrix.table.ScrollHint.PositionAtCenter)
    print(f"Kalign: {len(shared.inputs)} rows, {shared.columnCount} shared columns")
    capture(matrix, output / "shared-alignment.png")
    matrix.accept()


def captureDigestAndGel(window: MainWindow, puc19RevisionId: str, run: PcrRun, output: Path) -> None:
    digest = window.controller.analysis
    product, = run.products
    if not window.controller.openSavedRevision(puc19RevisionId):
        raise RuntimeError("Could not reopen the saved pUC19 template for digestion.")
    waitFor(lambda: digest.scan is not None, "the pUC19 restriction scan")
    digest.addEnzyme("MspI")
    if not digest.runDigest():
        raise RuntimeError(digest.digestUnavailableReason)
    waitFor(lambda: isinstance(QApplication.activeModalWidget(), DigestDialog), "the digest result")
    dialog = QApplication.activeModalWidget()
    digestion = dialog.digestion
    sizes = sorted(item.forwardLength for item in digestion.result.fragments)
    marker = next(profile for profile in digest.ladderProfiles if profile.ladderId == "puc19-mspi")
    if sizes != sorted(component.size for component in marker.components):
        raise ValueError(f"Unexpected pUC19 digest fragments: {sizes}")
    markers = dialog.preview.ladderSelector
    markers.setCurrentIndex(next(index for index in range(markers.count())
                                 if markers.itemData(index).ladderId == marker.ladderId))
    capture(dialog, output / "digest.png")
    dialog.accept()
    if not digest.pinDigestion(digestion, label="pUC19 · MspI"):
        raise RuntimeError("Could not load the digest lane.")
    if not digest.pinRecordedPcrProduct(run.operationId, product.outputKey, label="GFP amplicon"):
        raise RuntimeError("Could not load the PCR lane.")
    window.workflows.setCurrentWidget(window.gelView)
    markers = window.gelView.preview.ladderSelector
    markers.setCurrentIndex(next(index for index in range(markers.count())
                                 if markers.itemData(index).ladderId == "phix-haeiii"))
    window.gelView.preview.bandSizesButton.setChecked(True)
    window.gelView.findChild(QSplitter, "analysisPanels").setSizes([800, 380])
    capture(window, output / "gel.png")


def captureAssembly(window: MainWindow, output: Path) -> None:
    window.openDesignExample()
    waitFor(lambda: window.controller.session.current is not None
            and window.controller.session.current.name == "pReporter_demo", "the recorded design")
    assembly = window.assemblyView
    window.workflows.setCurrentWidget(assembly)
    assembly.exampleButton.click()
    waitFor(lambda: assembly.isShowingExample, "the bundled Assembly example")
    capture(window, output / "assembly.png")


def captureLambda(window: MainWindow, output: Path) -> None:
    """Capture the same selected coding sequence on the split and expanded landmark maps."""
    source = DATABASES / "references/lambda-landmarks.gb"
    controller = window.controller
    window.workflows.setCurrentIndex(0)
    window.resize(1280, 780)
    window.splitViewButton.click()
    controller.loadSequence(source)
    waitFor(lambda: controller.importResult is not None and controller.importResult.sourcePath == source,
            "λ gallery record")
    revision = controller.session.current
    if (sha256(revision.sequence.encode()).hexdigest() !=
            "36432a40f602258d19ae7c8152ddbc30390b559f2859c01d7047c77b048c71b3"
            or len(revision.features) != 69):
        raise ValueError("The λ gallery requires the 48,502 bp landmark edition of J02459.1.")
    target = controller.workspace.targetFor(controller.workspace.activeDocumentId)
    if not controller.saveDocument(target, "Bacteriophage λ"):
        raise RuntimeError("Could not save the λ gallery record.")
    waitFor(lambda: controller.analysis.scan is not None, "λ restriction scan")
    controller.analysis.setOverlayNames(())
    feature, = (f for f in controller.session.current.features if f.type == "CDS"
                and (f.parts[0].start, f.parts[0].end) == (7976, 8555))
    selectFeatureInTracks(window, feature)
    window.inspectorScroll.verticalScrollBar().setValue(window.features.y() - 6)
    capture(window, output / "lambda-split.png")
    window.mapViewButton.click()
    waitFor(lambda: window.plasmidMap.summary.text() == "68 mapped segments", "expanded λ landmarks")
    capture(window, output / "lambda-linear.png")


def captureHxb2Map(output: Path) -> None:
    """Export the annotated coordinate sheet using the desktop's linear figure renderer."""
    path = DATABASES / "references/HXB2-coordinate-map.gb"
    revision, = readSequence(path).records
    if len(revision) != 9719 or set(revision.sequence) != {"N"} or len(revision.features) != 9:
        raise ValueError("Expected the nine-gene, coordinate-only HXB2 example.")
    source = MapFigureSource(revision, "HIV-1 HXB2 · gene map (coordinates only)", False)
    (output / "hxb2-gene-map.svg").write_text(source.renderSvg(1200), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory for the gallery PNGs")
    parser.add_argument("--reference-maps-only", action="store_true", help="Rebuild the λ and HXB2 maps")
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_SCALE_FACTOR", "2")
    app = QApplication([])
    configureWorkspaceApplication(app)
    with TemporaryDirectory() as scratch:
        root = Path(scratch)
        catalogue = root / "enzymes.sqlite3"
        buildDatabase(catalogue)
        window = MainWindow()
        errors = []
        window.controller.importFailed.connect(errors.append)
        window.controller.operationFailed.connect(errors.append)
        window.controller.analysis.restrictionCataloguePath = catalogue
        window.controller.setProject(ProjectRepository.create(root / "gallery"))
        window.resize(1220, 770)
        window.show()
        try:
            with TemporaryDirectory(dir=output) as staging:
                destination = Path(staging)
                if not args.reference_maps_only:
                    puc19RevisionId = capturePuc19(window, destination)
                    if not window.controller.applyClose(window.controller.workspace.planClose(), {}):
                        raise RuntimeError("Could not close the pUC19 example.")
                    reporter, amplicon, run = captureReporter(window, destination)
                    captureAlignments(window, reporter, amplicon, destination)
                    captureDigestAndGel(window, puc19RevisionId, run, destination)
                    if not window.controller.applyClose(window.controller.workspace.planClose(), {}):
                        raise RuntimeError("Could not close the saved gallery records.")
                    captureAssembly(window, destination)
                    if not window.controller.applyClose(window.controller.workspace.planClose(), {}):
                        raise RuntimeError("Could not close the assembly gallery records.")
                captureLambda(window, destination)
                captureHxb2Map(destination)
                if errors:
                    raise RuntimeError("\n".join(errors))
                for path in destination.iterdir():
                    path.replace(output / path.name)
        finally:
            window.hide()
            window.controller.alignment.shutdown()
            window.controller.annotations.shutdown()
            window.historyView.shutdown()
            for pool in (*window.findChildren(QThreadPool), QThreadPool.globalInstance()):
                pool.waitForDone()


if __name__ == "__main__":
    main()
