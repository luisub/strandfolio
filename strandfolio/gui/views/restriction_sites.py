"""Pointer markers and keyboard focus for the shared Sequence restriction overlay."""

from collections.abc import Callable, Collection, Iterable
from math import hypot

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from strandfolio.gui.controller import WorkspaceController
from strandfolio.gui.panels.fitted_label import FittedLabel
from strandfolio.operations.restriction import RecognitionSite, RestrictionCut
from strandfolio.rendering.colors import recognitionMarkerColor


_KEYBOARD_GUIDE = "F7 or Shift+F7 focuses a restriction site. Enter or Space adds its enzyme."


class MapSiteInspector(QFrame):
    """Browse the map's displayed recognition occurrences without changing a DNA selection."""

    siteChanged = Signal(object)

    def __init__(self, controller: WorkspaceController, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.analysis = controller.analysis
        self._sites: tuple[RecognitionSite, ...] = ()
        self._index = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("Recognition sites", self, objectName="sectionTitle"))
        self.position = QLabel(self, objectName="muted")
        self.position.setAccessibleName("Displayed recognition site position")
        top.addWidget(self.position, 1)
        self.previous = QPushButton("‹", self)
        self.next = QPushButton("›", self)
        for button, label, step in ((self.previous, "Previous site", -1), (self.next, "Next site", 1)):
            button.setFixedWidth(29)
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.clicked.connect(lambda _checked, delta=step: self._move(delta))
            top.addWidget(button)
        self.add = QPushButton("Add to Digest", self)
        self.add.clicked.connect(self._activate)
        top.addWidget(self.add)
        layout.addLayout(top)
        self.evidence = FittedLabel("", self)
        layout.addWidget(self.evidence)

    def setSites(self, sites: tuple[RecognitionSite, ...]) -> None:
        site = self._sites[self._index] if self._sites else None
        self._sites = sites
        self._index = sites.index(site) if site in sites else 0
        self._render()

    def inspect(self, site: RecognitionSite) -> None:
        if site in self._sites:
            self._index = self._sites.index(site)
            self._render()

    def _move(self, step: int) -> None:
        if self._sites:
            self._index = (self._index + step) % len(self._sites)
            self._render()
            self.siteChanged.emit(self._sites[self._index])

    def _activate(self) -> None:
        if self._sites:
            site = self._sites[self._index]
            self.siteChanged.emit(site)
            self.analysis.activateSite(site)

    def _render(self) -> None:
        count = len(self._sites)
        self.setVisible(bool(count))
        if not count:
            return
        site = self._sites[self._index]
        self.position.setText(f"{self._index + 1:,} of {count:,}")
        coords = " → ".join(f"{start + 1:,}–{end:,}" for start, end in site.sourceParts)
        strand = "forward" if site.orientation == 1 else "reverse"
        self.evidence.setFullText(
            f"{site.enzymeName}  ·  {coords}  ·  {site.motif}  ·  {strand}  ·  {site.certainty}"
        )
        self.previous.setEnabled(count > 1)
        self.next.setEnabled(count > 1)
        included = site.enzymeName in self.analysis.recipe
        self.add.setText("In digest" if included else "Add to Digest")
        self.add.setEnabled(not included)
        self.add.setToolTip(f"{site.enzymeName} is in the whole-molecule digest." if included
                            else f"Add {site.enzymeName} to the whole-molecule digest.")


def restrictionSiteColor(
    site: RecognitionSite, recipe: Collection[str], *, isFocused: bool = False,
) -> QColor:
    """Use the same recognition colors in interactive views and exported figures."""
    return QColor(recognitionMarkerColor(
        isFocused=isFocused, isInRecipe=site.enzymeName in recipe,
        isDefinite=site.certainty == "definite",
    ))


def nearestSite(
    markers: Iterable[tuple[QPointF, RecognitionSite]], point: QPointF,
    *, focused: RecognitionSite | None,
) -> RecognitionSite | None:
    """Find a marker within six pixels, preferring the focused site in a tie."""
    nearest = None
    distance = 6.0
    for center, site in markers:
        delta = center - point
        candidate = hypot(delta.x(), delta.y())
        if candidate < distance or (candidate == distance and nearest is not None and site == focused):
            nearest = site
            distance = candidate
    return nearest


class RestrictionSitePicker:
    """Provide a shared focus and activation path for painted recognition markers."""

    def __init__(
        self, widget: QWidget, controller: WorkspaceController, repaint: Callable[[], None],
    ) -> None:
        self.widget = widget
        self.controller = controller
        self.repaint = repaint
        self.focused: RecognitionSite | None = None
        self.description = widget.accessibleDescription()
        self._cutBySite: dict[RecognitionSite, RestrictionCut] = {}
        controller.analysis.scanChanged.connect(self.sync)
        controller.analysis.recipeChanged.connect(repaint)

    def sync(self) -> None:
        """Apply the current overlay projection after a scan or filter change."""
        analysis = self.controller.analysis
        if self.focused not in analysis.displayedSites:
            self.focused = None
        self._cutBySite = {site: cut for cut in analysis.displayedCuts for site in cut.sites}
        self._describeFocus()
        self.repaint()

    def color(self, site: RecognitionSite) -> QColor:
        """Use distinct focus, recipe, definite and possible recognition colors."""
        return restrictionSiteColor(
            site, self.controller.analysis.recipe,
            isFocused=site == self.focused and self.widget.hasFocus(),
        )

    def tooltip(self, site: RecognitionSite) -> str:
        """Describe motif recognition and any predicted cleavage using separate coordinates."""
        parts = " and ".join(f"{start + 1:,}–{end:,}" for start, end in site.sourceParts)
        orientation = "+" if site.orientation == 1 else "−"
        text = (
            f"{site.enzymeName} · {site.motif} ({orientation}) · {parts} · "
            f"{site.certainty} recognition"
        )
        cut = self._cutBySite.get(site)
        if cut is None:
            return f"{text}\nNo predicted cleavage for this occurrence."
        offset = cut.bottomOffset
        overhang = "Blunt" if offset == 0 else f"{abs(offset)} nt {'5′' if offset > 0 else '3′'} overhang"
        return (
            f"{text}\nTop cut {cut.topBoundary:,} · Bottom cut {cut.bottomBoundary:,} "
            f"(0-based interbase boundaries) · {overhang}"
        )

    def setDescription(self, description: str) -> None:
        self.description = description
        self._describeFocus()

    def _describeFocus(self) -> None:
        parts = [self.description]
        if self.controller.analysis.displayedSites:
            parts.append(_KEYBOARD_GUIDE)
        if self.focused is not None:
            parts.append(self.tooltip(self.focused))
        self.widget.setAccessibleDescription(" ".join(part for part in parts if part))

    def reset(self) -> None:
        if self.focused is not None:
            self.focused = None
            self._describeFocus()
            self.repaint()

    def activate(self, site: RecognitionSite) -> None:
        self.focused = site
        self.widget.setFocus(Qt.FocusReason.MouseFocusReason)
        self._describeFocus()
        self.controller.analysis.activateSite(site)
        self.repaint()

    def handleKey(
        self, event: QKeyEvent, *, referenceBase: int,
        reveal: Callable[[int, int], None] | None = None,
        visibleSites: tuple[RecognitionSite, ...] | None = None,
    ) -> bool:
        """Focus a marker relative to the viewport, or activate an already-focused marker."""
        sites = self.controller.analysis.displayedSites if visibleSites is None else visibleSites
        key = event.key()
        if key == Qt.Key.Key_F7 and sites:
            reverse = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self.focused in sites:
                step = -1 if reverse else 1
                self.focused = sites[(sites.index(self.focused) + step) % len(sites)]
            elif reverse:
                self.focused = next(
                    (site for site in reversed(sites) if site.start <= referenceBase), sites[-1],
                )
            else:
                self.focused = next((site for site in sites if site.start >= referenceBase), sites[0])
            self._describeFocus()
            self.controller.message.emit(self.tooltip(self.focused).splitlines()[0])
            if reveal is not None:
                reveal(*self.focused.sourceParts[0])
            self.repaint()
        elif self.focused in sites and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.controller.analysis.activateSite(self.focused)
        elif self.focused is not None and key == Qt.Key.Key_Escape:
            self.reset()
        else:
            return False
        event.accept()
        return True
