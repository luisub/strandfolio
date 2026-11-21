"""Presentation colors shared by the desktop canvases and their exported figures."""

from functools import lru_cache


CANVAS = "#ffffff"
SURFACE = "#f5f3fa"
INSPECTOR = "#faf9fe"
LINE = "#e2dcef"
INK = "#252135"
MUTED = "#625a75"
ACCENT = "#6236d3"
ACCENT_INK = "#4e29b4"
SELECTION = "#e9e0fc"
AMBER = "#8b5c24"
AMBER_SOFT = "#fbf3e6"
GREEN = "#367564"
RED = "#a03d53"

BASE_COLORS = {
    "A": "#086b48", "C": "#1458b8", "G": "#885200", "T": "#b72150", "U": "#b72150",
}


def recognitionMarkerColor(*, isFocused: bool, isInRecipe: bool, isDefinite: bool) -> str:
    """Return the shared display color for a recognition marker."""
    if isFocused:
        return INK
    if isInRecipe:
        return ACCENT
    return AMBER if isDefinite else MUTED


@lru_cache(maxsize=256)
def featureTextColor(fill: str) -> str:
    """Choose the higher-contrast black or white label for an opaque six-digit RGB fill."""
    channels = (int(fill[offset:offset + 2], 16) / 255 for offset in (1, 3, 5))
    linear = (
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    )
    luminance = sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    whiteContrast = 1.05 / (luminance + 0.05)
    blackContrast = (luminance + 0.05) / 0.05
    return "#ffffff" if whiteContrast > blackContrast else "#000000"
