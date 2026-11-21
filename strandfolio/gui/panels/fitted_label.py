"""A single-line scientific name that keeps both ends visible in narrow views."""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class FittedLabel(QLabel):
    """Fit a name to one line and retain its full text on hover and keyboard inspection."""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFullText(text)

    def setFullText(self, text: str) -> None:
        """Set the display name and expose its complete text to assistive technology."""
        self._fullText = text
        self.setAccessibleName(text)
        self.setToolTip(escape(text))
        self._fitText()

    def _fitText(self) -> None:
        self.setText(self.fontMetrics().elidedText(
            self._fullText, Qt.TextElideMode.ElideMiddle, self.contentsRect().width(),
        ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fitText()
