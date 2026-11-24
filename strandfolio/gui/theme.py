"""Native widget styling and shared rendering colors for the violet workspace."""

from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QPalette

from strandfolio.rendering.colors import (
    ACCENT, BASE_COLORS, CANVAS as PANEL, INK as TEXT, MUTED, SELECTION, SURFACE as BACKGROUND,
)
from strandfolio.rendering.layout import DEFAULT_FEATURE_COLOR, FEATURE_COLORS, featureColor


__all__ = [
    "ACCENT", "BACKGROUND", "BASE_COLORS", "DEFAULT_FEATURE_COLOR", "FEATURE_COLORS",
    "MUTED", "PANEL", "SELECTION", "STYLE", "TEXT", "featureColor",
    "ensureMapFonts", "workspaceMonoFont", "workspacePalette", "workspaceUiFont",
]


def workspaceUiFont() -> QFont:
    """Use the platform's actual interface family for native text and its weight variants."""
    systemFont = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    families = QFontDatabase.families()
    if systemFont.family() not in families and ".AppleSystemUIFont" in families:
        font = QFont(".AppleSystemUIFont")
    else:
        font = QFont(QFontInfo(systemFont).family())
    font.setPixelSize(14)
    return font


@lru_cache(maxsize=1)
def _monoFamily() -> str:
    # Some platform plugins map the generic system "monospace" to the proportional UI face.
    systemFont = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    installed = QFontDatabase.families()
    if systemFont.family() in installed and QFontDatabase.isFixedPitch(systemFont.family()):
        return QFontInfo(systemFont).family()
    preferred = [family for family in ("Menlo", "Consolas", "DejaVu Sans Mono") if family in installed]
    for family in (*preferred, *installed):
        if QFontDatabase.isFixedPitch(family):
            return family
    return systemFont.family()


def workspaceMonoFont(pixels: int) -> QFont:
    """Use measured fixed-pitch type for aligned sequence and scientific data."""
    font = QFont(_monoFamily())
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixels)
    return font


def ensureMapFonts() -> None:
    """Register the fonts used to measure and paint SVG map labels."""
    if "DejaVu Sans" not in QFontDatabase.families():
        from matplotlib import get_data_path

        faceDirectory = Path(get_data_path()) / "fonts/ttf"
        for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
            QFontDatabase.addApplicationFont(str(faceDirectory / name))


def workspacePalette(native: QPalette) -> QPalette:
    """Carry workspace colors into native menu, focus, checkbox and dialog drawing."""
    palette = QPalette(native)
    roles = QPalette.ColorRole
    for role, color in (
        (roles.Window, BACKGROUND), (roles.WindowText, TEXT), (roles.Base, PANEL),
        (roles.AlternateBase, "#faf9fe"), (roles.Text, TEXT), (roles.Button, "#faf7fd"),
        (roles.ButtonText, "#534360"), (roles.Highlight, ACCENT), (roles.HighlightedText, PANEL),
        (roles.Accent, ACCENT), (roles.Link, "#4e29b4"), (roles.PlaceholderText, "#766785"),
    ):
        palette.setColor(role, QColor(color))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#807789"))
    return palette


STYLE = """
QWidget { color: #252135; font-size: 13px; }
QMainWindow, QDialog, QMenu, QMenuBar, QTabWidget::pane, QAbstractItemView {
    background: #ffffff; color: #252135;
}
QMainWindow, QStatusBar { background: #f5f3fa; }
QWidget#workspaceChrome { background: #ffffff; }
QWidget#startPage { background: #faf9fe; }
QFrame#startPanel { background: #ffffff; border: 1px solid #e2dcef; border-radius: 10px; }
QLabel#startTitle { color: #2e2444; font-size: 24px; font-weight: 600; }
QLabel#startSection { color: #625a75; font-size: 12px; font-weight: 600; }
QLabel#exampleRecordIcon { background: #f1ebfc; border-radius: 8px; }
QFrame#workspaceHeader { background: #f0edf8; border-bottom: 1px solid #e2dcef; }
QFrame#workspaceHeader QScrollArea { background: transparent; border: none; }
QFrame#workspaceHeader QScrollArea .QWidget { background: transparent; }
QFrame#brandDivider, QFrame#sectionDivider { background: #e2dcef; border: none; }
QWidget#sequenceTools, QWidget#workflowTools { background: transparent; }
QFrame#sequenceInspector, QWidget#workflowInspector,
QFrame#sequenceInspector QScrollArea .QWidget,
QWidget#workflowInspector QScrollArea .QWidget { background: #faf9fe; }
QWidget#sequenceWorkspace, QWidget#workflowCanvas { background: #ffffff; }
QFrame#card { background: #faf9fe; border: 1px solid #e2dcef; border-radius: 6px; }
QFrame#sequenceMapCard { background: #ffffff; }
QFrame#sequenceInspector QFrame#sequenceMapCard { background: #faf9fe; border-bottom: 1px solid #e2dcef; }
QSlider#overviewPosition::groove:horizontal { height: 3px; background: #e2dcef; }
QSlider#overviewPosition::handle:horizontal { width: 10px; margin: -4px 0;
    background: #ffffff; border: 1px solid #6236d3; border-radius: 5px; }
QSlider#overviewPosition::handle:horizontal:focus { background: #eee8fc; border: 2px solid #5126b8; }
QSlider#overviewPosition::handle:horizontal:disabled { border-color: #c7bbd9; }
QFrame#backboneCard, QFrame#insertCard { background: #ffffff; border: 1px solid #e2dcef;
    border-top: 3px solid #6944ae; border-radius: 6px; }
QFrame#insertCard { border-top-color: #277669; }
QFrame#backboneCard QLabel#sectionTitle { color: #6944ae; }
QFrame#insertCard QLabel#sectionTitle { color: #277669; }
QWidget#assemblyOrder QScrollArea > QWidget > QWidget { background: #ffffff; }
QWidget#assemblyCandidates QScrollArea > QWidget > QWidget { background: #faf9fe; }
QLabel { background: transparent; }
QLabel#brand { color: #2e2444; font-size: 17px; font-weight: 650; }
QLabel#muted { color: #625a75; font-size: 12px; }
QLabel#sectionTitle { color: #30263e; font-size: 13px; font-weight: 600; }
QLabel#alignmentAppliedSettings, QLabel#alignmentMapping,
QLabel#featureCoordinates, QLabel#featureStrand { color: #71617d; font-size: 11px; }
QWidget#featureRow { background: transparent; border-radius: 5px; }
QWidget#featureRow[selected="true"] { background: #eee8fc; }
QPlainTextEdit#historyEvidence { background: #faf9fe; color: #544761; border: 1px solid #e8dfef;
    border-radius: 6px; padding: 7px; font-size: 12px; }
QLabel#documentTitle { color: #2e2444; font-size: 18px; font-weight: 600; }
QLabel#selectionCount, QLabel#selectionPill { color: #4e29b4; font-size: 14px; font-weight: 600; }
QLabel#selectionStatus { color: #5a466f; font-size: 11px; font-weight: 500; }
QLabel#notice { background: #fbf3e6; color: #694517; border: 1px solid #ebd9b6;
    border-radius: 5px; padding: 6px; }
QTabWidget::pane { border: 1px solid #e2dcef; }
QTabWidget#workflows::pane { border: none; border-top: 1px solid #e2dcef; }
QTabBar::tab { background: transparent; color: #625a75; border: 1px solid transparent;
    border-radius: 5px; padding: 4px 8px; margin: 1px; font-size: 12px; }
QTabBar::tab:selected { background: #eee8fc; color: #4e29b4; font-weight: 600; }
QTabBar::tab:!selected:hover { background: #f0edf8; color: #252135; }
QTabBar#workflowNavigation::tab { color: #695a77; background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 0 5px; margin: 7px 1px; min-height: 28px; font-size: 13px; }
QTabBar#workflowNavigation::tab:first { margin-left: 7px; }
QTabBar#workflowNavigation::tab:selected { color: #4e29b4; background: #eee8fc;
    border: 1px solid #dfd2f4; font-weight: 600; }
QTabBar#workflowNavigation::tab:!selected:hover { background: #eee8f6; color: #4e29b4; }
QPushButton, QToolButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #ffffff,stop:1 #faf7fd);
    border: 1px solid #dfd6e8; border-radius: 7px; padding: 5px 10px; color: #4b405a;
}
QWidget#sequenceTools QPushButton, QWidget#workflowTools QPushButton {
    min-height: 22px; font-size: 13px; font-weight: 500; padding: 4px 10px;
}
QFrame#sequenceTextSize { background: #faf9fe; border: 1px solid #dfd3e9; border-radius: 6px; }
QFrame#sequenceTextSize QSpinBox { background: #ffffff; border: 0;
    border-left: 1px solid #e2dcef; border-right: 1px solid #e2dcef;
    border-radius: 0; padding: 0 3px; font-size: 12px; min-height: 22px; }
QFrame#sequenceTextSize QSpinBox:focus { background: #eee8fc; }
QFrame#sequenceTextSize QSpinBox::up-button, QFrame#sequenceTextSize QSpinBox::down-button {
    width: 0; height: 0; }
QFrame#sequenceTextSize QSpinBox::up-arrow, QFrame#sequenceTextSize QSpinBox::down-arrow,
QSpinBox#matrixColumn::up-arrow, QSpinBox#matrixColumn::down-arrow {
    image: none; width: 0; height: 0; }
QWidget#sequenceTools QFrame#sequenceTextSize QPushButton,
QWidget#workflowTools QFrame#sequenceTextSize QPushButton { border: 1px solid transparent;
    background: transparent; padding: 0 4px; min-width: 15px; min-height: 22px; font-size: 15px; }
QWidget#sequenceTools QFrame#sequenceTextSize QPushButton:hover,
QWidget#workflowTools QFrame#sequenceTextSize QPushButton:hover { background: #eee8fc; }
QWidget#sequenceTools QFrame#sequenceTextSize QPushButton:focus,
QWidget#workflowTools QFrame#sequenceTextSize QPushButton:focus { border-color: #6236d3; }
QWidget#recordControls QPushButton { background: transparent; border: 1px solid transparent;
    padding: 2px 6px; font-size: 11px; }
QWidget#recordControls QPushButton:checked { background: #eee8fc; color: #4e29b4; }
QWidget#recordControls QPushButton:hover { background: #e9e0f5; }
QWidget#recordControls QPushButton:focus { border-color: #6236d3; }
QPushButton:hover, QToolButton:hover { background: #f2eaf9; border-color: #b79ad1; color: #252135; }
QPushButton:pressed, QToolButton:pressed { background: #e9e0fc; }
QPushButton:checked, QToolButton:checked { background: #eee8fc; color: #4e29b4; border-color: #bda5e0; }
QPushButton:disabled, QToolButton:disabled { color: #807789; background: #f6f4f8; border-color: #e8e2ed; }
QPushButton#primary, QPushButton#alignmentRun {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #7547df,stop:1 #6236d3);
    color: #ffffff; border-color: #5830c4; font-weight: 600;
}
QPushButton#primary:hover, QPushButton#alignmentRun:hover { background: #5126b8; }
QPushButton#primary:disabled, QPushButton#alignmentRun:disabled {
    background: #e7dff5; color: #6b588b; border-color: #d9cde8; }
QFrame#workspaceHeader QPushButton { color: #625475; font-size: 12px; min-height: 19px; }
QPushButton#headerAction { background: transparent; border-color: transparent; padding: 4px 7px; }
QPushButton#headerAction:hover { background: #e7e1f2; color: #372849; }
QPushButton#headerAction:focus { border-color: #6236d3; }
QPushButton#headerInfo { border: 1px solid transparent; background: transparent; padding: 2px; }
QPushButton#headerInfo:hover { background: #e9e0f5; }
QFrame#documentTab { border: 1px solid transparent; background: transparent;
    border-top-left-radius: 7px; border-top-right-radius: 7px; }
QFrame#documentTab[active="true"] { background: #ffffff; border-color: #ded7ed; border-bottom-color: #ffffff; }
QPushButton#documentName, QToolButton#documentClose { background: transparent; border: 1px solid transparent;
    padding: 2px 3px; }
QPushButton#documentName { color: #625475; font-size: 12px; font-weight: 500; text-align: left; }
QPushButton#headerInfo:focus, QToolButton#documentClose:focus, QPushButton#documentName:focus {
    border: 1px solid #6236d3; background: #eee8fc; }
QPushButton#documentName:checked { color: #4e29b4; }
QLabel#documentLength { color: #6c607a; font-size: 11px; padding: 0 2px; }
QPushButton#documentName:hover, QToolButton#documentClose:hover { background: #f3edf9; }
QPushButton#featureName, QPushButton#featureDisclosure {
    background: transparent; border-color: transparent; text-align: left; padding: 3px 4px;
}
QPushButton#featureDisclosure { padding: 2px; }
QPushButton#featureName:hover, QPushButton#featureDisclosure:hover { background: #f0edf8; }
QPushButton#featureName:checked, QPushButton#featureDisclosure:checked {
    background: #eee8fc; color: #4e29b4;
}
QPushButton#featureName:focus, QPushButton#featureDisclosure:focus { border-color: #6236d3; }
QToolButton#featureSectionToggle { background: transparent; border: 1px solid transparent;
    color: #403049; padding: 3px 5px; font-size: 13px; font-weight: 600; text-align: left; }
QToolButton#featureSectionToggle:hover, QToolButton#featureSectionToggle:focus { background: #eee8fc; }
QToolButton#annotationOptions { background: transparent; border: 1px solid transparent; padding: 2px; }
QToolButton#annotationOptions::menu-indicator { image: none; }
QToolButton#annotationOptions:hover { background: #eee8fc; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus { border-color: #6236d3; }
QCheckBox, QRadioButton { background: transparent; color: #463b52; spacing: 7px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #b9acc9;
    border-radius: 4px; background: #ffffff; }
QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover {
    background: #f5f0ff; border-color: #997ac2; }
QCheckBox::indicator:checked { image: url("@icons@/control-check.svg");
    border-color: #6031cf; background: #6236d3; }
QCheckBox::indicator:checked:hover { background: #5126b8; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border-color: #46218c; }
QCheckBox::indicator:unchecked:disabled, QRadioButton::indicator:unchecked:disabled {
    background: #f1eef5; border-color: #d6d0df; }
QCheckBox::indicator:checked:disabled { background: #a49ab7; border-color: #a49ab7; }
QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid #b9acc9;
    border-radius: 8px; background: #ffffff; }
QRadioButton::indicator:checked { border-color: #6236d3;
    background: qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,
        stop:0 #6236d3,stop:0.53 #6236d3,stop:0.63 #ffffff,stop:1 #ffffff); }
QCheckBox:disabled, QRadioButton:disabled { color: #83798d; }
QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {
    background: #ffffff; color: #252135; border: 1px solid #dfd3e9; border-radius: 5px;
    padding: 3px; selection-background-color: #e9e0fc; selection-color: #252135;
    placeholder-text-color: #766785;
}
QWidget#workflowInspector QLineEdit, QWidget#workflowInspector QSpinBox,
QWidget#workflowInspector QDoubleSpinBox { min-height: 21px; padding: 3px 6px; }
QComboBox, QFrame#sequenceInspector QComboBox, QWidget#workflowInspector QComboBox,
QDialog[workspaceDialog="true"] QComboBox {
    min-height: 22px; padding: 4px 24px 4px 8px; border-radius: 7px; }
QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: right;
    width: 24px; border: none; background: transparent; }
QComboBox::down-arrow { image: url("@icons@/control-chevron.svg"); width: 12px; height: 12px; }
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border; width: 19px; border: none; background: transparent; }
QSpinBox::up-button { subcontrol-position: top right; }
QSpinBox::down-button { subcontrol-position: bottom right; }
QSpinBox::up-arrow { image: url("@icons@/control-chevron-up.svg"); width: 11px; height: 11px; }
QSpinBox::down-arrow { image: url("@icons@/control-chevron.svg"); width: 11px; height: 11px; }
QFrame#alignmentScoring QDoubleSpinBox { padding: 3px 55px 3px 7px; border-radius: 6px; }
QFrame#alignmentScoring QDoubleSpinBox::up-button, QFrame#alignmentScoring QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: center right; height: 28px; width: 24px;
    border: none; border-left: 1px solid #e5dfed; background: #f7f5fa; }
QFrame#alignmentScoring QDoubleSpinBox::up-button { right: 1px;
    border-top-right-radius: 5px; border-bottom-right-radius: 5px; }
QFrame#alignmentScoring QDoubleSpinBox::down-button { right: 26px; }
QFrame#alignmentScoring QDoubleSpinBox::up-button:hover, QFrame#alignmentScoring QDoubleSpinBox::down-button:hover {
    background: #eae3f6; }
QFrame#alignmentScoring QDoubleSpinBox::up-button:pressed, QFrame#alignmentScoring QDoubleSpinBox::down-button:pressed {
    background: #e0d5f2; }
QFrame#alignmentScoring QDoubleSpinBox::up-arrow {
    image: url("@icons@/control-plus.svg"); width: 14px; height: 14px; }
QFrame#alignmentScoring QDoubleSpinBox::down-arrow {
    image: url("@icons@/control-minus.svg"); width: 14px; height: 14px; }
QFrame#alignmentScoring QDoubleSpinBox::up-arrow:off, QFrame#alignmentScoring QDoubleSpinBox::up-arrow:disabled {
    image: url("@icons@/control-plus-muted.svg"); }
QFrame#alignmentScoring QDoubleSpinBox::down-arrow:off, QFrame#alignmentScoring QDoubleSpinBox::down-arrow:disabled {
    image: url("@icons@/control-minus-muted.svg"); }
QComboBox:hover:enabled, QLineEdit:hover:enabled { border-color: #baabce; }
QComboBox:focus, QLineEdit:focus { border-color: #6236d3; }
QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled, QComboBox:disabled {
    background: #f6f4f8; color: #807789; }
QAbstractItemView { selection-background-color: #e9e0fc; selection-color: #252135; }
QMenuBar::item:selected, QMenu::item:selected { background: #eee8fc; color: #4e29b4; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #f5f3fa; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #b0a4bf; border-radius: 3px; min-height: 22px; }
QScrollBar:horizontal { background: #f5f3fa; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: #b0a4bf; border-radius: 3px; min-width: 22px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: #e2dcef; width: 1px; height: 1px; }
QStatusBar::item { border: none; }
QStatusBar { min-height: 28px; color: #625a75; border-top: 1px solid #e2dcef; font-size: 11px; }
QToolTip { background: #ffffff; color: #252135; border: 1px solid #bdaacb; padding: 4px; }
QWidget#alignmentWorkspace, QWidget#gelWorkspace, QWidget#historyWorkspace,
QSplitter#analysisPanels::handle { background: #f5f3fa; }
QSplitter#analysisPanels > QWidget#workflowCanvas, QScrollArea#analysisInspector {
    border: 1px solid #e2dcef; border-radius: 10px; }
QSplitter#analysisPanels > QWidget#workflowCanvas { background: #ffffff; }
QScrollArea#analysisInspector { background: #faf9fe; padding: 2px; }
QScrollArea#analysisInspector > QWidget { background: transparent; }
QLabel#analysisPanelHeading { color: #574d69; font-size: 13px; font-weight: 600;
    border-bottom: 1px solid #e6e1f1; padding-bottom: 8px; }
QWidget#historyCanvasHeader { border-bottom: 1px solid #e6e1f1; }
QAbstractScrollArea#alignmentTracks, QListView#alignmentDifferences,
QListWidget#alignmentResults { border: 1px solid transparent; background: transparent; }
QAbstractScrollArea#alignmentTracks:focus, QListView#alignmentDifferences:focus,
QListWidget#alignmentResults:focus { border-color: #6236d3; }

QLabel#dialogHeading { font-size: 21px; font-weight: 600; color: #2e2444; padding-bottom: 3px; }
QFrame#dialogFooter { border: none; border-top: 1px solid #e2dcef; }
QDialog[workspaceDialog="true"] QPushButton { min-height: 21px; padding: 4px 10px; }
QFrame#dialogFooter QPushButton { min-height: 22px; padding: 4px 12px; }
QDialog[workspaceDialog="true"] QLineEdit {
    min-height: 23px; padding: 2px 6px; }
QDialog[workspaceDialog="true"] QSpinBox, QDialog[workspaceDialog="true"] QDoubleSpinBox {
    min-height: 23px; padding: 2px 4px; }
QDialog[workspaceDialog="true"] QLineEdit:read-only { background: #faf9fe; }
QDialog[workspaceDialog="true"] QPlainTextEdit, QDialog[workspaceDialog="true"] QTextEdit {
    padding: 7px; border-radius: 6px; }
QFrame#dialogFooter QPushButton[dialogPrimary="true"] {
    background: #6236d3; color: #ffffff; border: 1px solid #5830c4; font-weight: 600; }
QFrame#dialogFooter QPushButton[dialogPrimary="true"]:hover { background: #5126b8; }
QFrame#dialogFooter QPushButton[dialogPrimary="true"]:focus { border: 2px solid #382074; padding: 3px 11px; }
QFrame#dialogFooter QPushButton[dialogPrimary="true"]:disabled {
    background: #e7dff5; color: #6b588b; border: 1px solid #d9cde8; }
QFrame#dialogCard { background: #faf9fe; border: 1px solid #e2dcef; border-radius: 8px; }
QLabel#dialogSectionTitle { font-size: 13px; font-weight: 600; color: #403049; }
QListWidget#sequenceCopyFormats {
    background: #faf9fe; border: 1px solid #e2dcef; border-radius: 7px; padding: 4px; }
QListWidget#sequenceCopyFormats::item { padding: 6px; border: 1px solid transparent; border-radius: 4px; }
QListWidget#sequenceCopyFormats::item:selected {
    background: #eee8fc; color: #4e29b4; border: 1px solid #d6c9ec; }
QMenu { background: #ffffff; border: 1px solid #dfd5eb; padding: 5px; }
QMenu::item { background: transparent; padding: 5px 18px 5px 23px; border-radius: 4px; }
QMenu::item:selected { background: #eee8fc; color: #4e29b4; }
QMenu::item:disabled { color: #807789; }
QMenu::separator { height: 1px; margin: 4px 5px; background: #e2dcef; }
QMenu#layersMenu QCheckBox { min-height: 24px; padding: 2px 9px; border-radius: 4px; }
QMenu#layersMenu QCheckBox:hover, QMenu#layersMenu QCheckBox:focus { background: #eee8fc; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #dfd5eb; padding: 3px;
    selection-background-color: #eee8fc; selection-color: #4e29b4; }
QAbstractItemView#alignmentChoices { padding: 4px; outline: none; }
QAbstractItemView#alignmentChoices::item {
    min-height: 23px; padding: 3px 9px; border: 1px solid transparent; border-radius: 5px; }
QAbstractItemView#alignmentChoices::item:selected {
    background: #eee8fc; color: #4e29b4; border-color: #dacdf1; }
QMessageBox[workspaceNotice="true"] QLabel#qt_msgbox_label {
    font-size: 15px; font-weight: 600; color: #2e2444; }
QMessageBox[workspaceNotice="true"] QLabel#qt_msgbox_informativelabel { color: #625a75; }
QMessageBox[workspaceNotice="true"] QPushButton { min-height: 21px; padding: 4px 12px; }
QMessageBox[workspaceNotice="true"] QPushButton[destructive="true"] {
    background: #fbf0f3; border-color: #dfbac4; color: #883047; }
""".replace("@icons@", (Path(__file__).resolve().parents[1] / "data" / "icons").as_posix().replace('"', '\\"'))
