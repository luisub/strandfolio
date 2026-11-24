"""Read-only linear source map for a recorded digestion."""

from dataclasses import replace

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from strandfolio.domain.coordinates import SequenceSelection
from strandfolio.domain.fragments import FragmentSpan
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.gui.theme import ensureMapFonts
from strandfolio.rendering.maps import MapFigureSource


class DigestSourceMap(QWidget):
    """Draw recorded fragment spans without changing the workspace selection."""

    def __init__(self, revision: SequenceRevision, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Linear map of the recorded digest source")
        self.setMinimumWidth(100)
        self.setFixedHeight(88)
        self._source = MapFigureSource(revision, revision.name, isCircular=False)
        self._renderer = QSvgRenderer(self)
        self._renderedKey: tuple[int, int, SequenceSelection | None] | None = None
        ensureMapFonts()

    def setSourceSpans(self, spans: tuple[FragmentSpan, ...]) -> None:
        """Highlight the supplied ordered reference spans, or clear the highlight."""
        selection = None
        if spans:
            selection = SequenceSelection(
                self._source.revision.revisionId, spans[0].start, spans[-1].end,
                segments=tuple((span.start, span.end) for span in spans),
            )
        if self._source.selection != selection:
            self._source = replace(self._source, selection=selection)
            self.update()

    def paintEvent(self, event) -> None:
        width, height = self.width(), self.height()
        if width < self.minimumWidth() or height == 0:
            return
        key = (width, height, self._source.selection)
        if key != self._renderedKey:
            scene = self._source.scene(width, height, native=True, embeddedLinear=True)
            self._renderer.load(QByteArray(scene.svg.encode("utf-8")))
            self._renderedKey = key
        with QPainter(self) as painter:
            self._renderer.render(painter, QRectF(self.rect()))
