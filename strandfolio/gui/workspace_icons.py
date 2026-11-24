"""Outline icons for workspace navigation and actions."""

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


# The paths use the same 24-unit coordinate system as the design's workspace icons.
_PATHS = {
    "sequence": "M5 5h14M5 10h14M5 15h9M5 20h14",
    "pcr": "M3 8h14l-3-3M21 16H7l3 3M17 8l-3 3M7 16l3-3",
    "alignment": "M4 5h16M4 10h10M4 15h16M4 20h10M18 8v4M18 18v4",
    "assembly": "M3 7h6v10H3zM15 7h6v10h-6zM9 12h6M12 9v6",
    "gel": "M4 3h16v18H4zM8 6v12M12 6v12M16 6v12M7 9h2M11 14h2M15 11h2",
    "history": "M6 7v10M6 12h8a4 4 0 0 0 4-4V7M8 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z"
               "M8 19a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM20 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z",
    "target": "M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M8 12h8M12 8v8",
    "layers": "M3 7l9-4 9 4-9 4-9-4ZM3 12l9 4 9-4M3 17l9 4 9-4",
    "digest": "M7 8l12 12M7 16L19 4M8 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
              "M8 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
    "copy": "M9 8h11v13H9zM15 8V3H4v13h5",
    "play": "M8 4l12 8-12 8V4Z",
    "plus": "M12 5v14M5 12h14",
    "circular": "M18 5a8 8 0 1 1-6-1M12 2v5h5",
    "linear": "M4 12h16M4 8v8M20 8v8",
    "record": "M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6",
    "open": "M3 18V6h6l2 2h8v3M3 18l3-7h15l-3 7z",
    "export": "M12 3v12M7 10l5 5 5-5M4 16v5h16v-5",
    "close": "M6 6l12 12M18 6 6 18",
    "info": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 11v6M12 7v1",
}


@lru_cache(maxsize=64)
def workspaceIcon(name: str, *, onAccent: bool = False) -> QIcon:
    """Return regular, selected and disabled icon variants at three display scales."""
    icon = QIcon()
    colors = {
        QIcon.Mode.Normal: "#ffffff" if onAccent else "#645471",
        QIcon.Mode.Selected: "#ffffff" if onAccent else "#6236d3",
        QIcon.Mode.Disabled: "#776389" if onAccent else "#83768c",
    }
    for mode, color in colors.items():
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
               f'stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
               f'<path d="{_PATHS[name]}"/></svg>')
        renderer = QSvgRenderer(svg.encode("ascii"))
        for logicalSize in (14, 16, 20):
            for scale in (1, 2, 3):
                pixels = logicalSize * scale
                pixmap = QPixmap(pixels, pixels)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                pixmap.setDevicePixelRatio(scale)
                icon.addPixmap(pixmap, mode)
                if mode is QIcon.Mode.Selected:
                    icon.addPixmap(pixmap, QIcon.Mode.Normal, QIcon.State.On)
    return icon
