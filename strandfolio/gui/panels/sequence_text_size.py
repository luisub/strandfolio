"""Desktop text-size control shared by the Sequence and PCR toolbars."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSpinBox, QWidget

from strandfolio.gui.views.sequence_view import SequenceView


def sequenceTextSizeControl(
    parent: QWidget, *, viewName: str = "sequence",
) -> tuple[QFrame, QSpinBox]:
    """Wrap the editable size in accessible decrement and increment buttons."""
    control = QFrame(parent)
    control.setObjectName("sequenceTextSize")
    layout = QHBoxLayout(control)
    layout.setContentsMargins(1, 0, 1, 0)
    layout.setSpacing(0)
    size = QSpinBox(control)
    size.setRange(SequenceView.MIN_TEXT_SIZE_PX, SequenceView.MAX_TEXT_SIZE_PX)
    size.setValue(SequenceView.DEFAULT_TEXT_SIZE_PX)
    size.setSuffix(" px")
    size.setAlignment(Qt.AlignmentFlag.AlignCenter)
    size.setAccessibleName(f"{viewName[:1].upper()}{viewName[1:]} text size")
    size.ensurePolished()
    size.setFixedWidth(size.fontMetrics().horizontalAdvance(f"{size.maximum()}{size.suffix()}") + 15)
    smaller = QPushButton("−", control)
    larger = QPushButton("+", control)
    for button, direction, step in (
        (smaller, "Decrease", size.stepDown),
        (larger, "Increase", size.stepUp),
    ):
        name = f"{direction} {viewName} text size"
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.clicked.connect(step)

    def updateButtons(value: int) -> None:
        smaller.setEnabled(value > size.minimum())
        larger.setEnabled(value < size.maximum())

    size.valueChanged.connect(updateButtons)
    updateButtons(size.value())
    layout.addWidget(smaller)
    layout.addWidget(size)
    layout.addWidget(larger)
    QWidget.setTabOrder(smaller, size)
    QWidget.setTabOrder(size, larger)
    return control, size
