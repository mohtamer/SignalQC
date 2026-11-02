"""Compact, accessible desktop controls; independent of processing methods."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QRadioButton, QSizePolicy, QStyle, QStyleOption,
    QVBoxLayout,
)


# Include in the application stylesheet, so an application's own theme can
# override it. The control never installs a higher-priority local stylesheet.
TASK_CHOICE_STYLE = """
QRadioButton#taskChoice {
    background: #ffffff;
    border: 2px solid #aebdcc;
    border-radius: 8px;
    padding: 0px;
    color: #172b42;
}
QRadioButton#taskChoice:hover { background: #f0f5fa; border-color: #577996; }
QRadioButton#taskChoice:checked { background: #e8f3fa; border-color: #22618b; }
QRadioButton#taskChoice[keyboardFocus="true"] {
    border: 3px solid #154e87;
}
QRadioButton#taskChoice:disabled { background: #eef1f4; border-color: #aeb7c1; }
QLabel#taskChoiceTitle {
    background: transparent; color: #172b42; font-size: 11pt; font-weight: 600;
    border: none; padding: 0px;
}
QLabel#taskChoiceDescription {
    background: transparent; color: #405269; font-size: 10pt;
    border: none; padding: 0px;
}
QLabel#taskChoiceStatus {
    background: transparent; color: #174f77; font-size: 10pt; font-weight: 600;
    border: none; padding: 0px;
}
QLabel#taskChoiceTitle:disabled, QLabel#taskChoiceDescription:disabled,
QLabel#taskChoiceStatus:disabled { color: #606c79; }
"""


class TaskChoice(QRadioButton):
    """A full-row radio choice with text, description and visible selection.

    Use ``TaskChoice(title, description, parent=None)`` in a vertical layout.
    The normal QRadioButton API and accessibility role are retained: a
    QButtonGroup can supply exclusivity, arrow keys move between choices and
    Space activates the focused choice. All child labels are mouse-transparent
    so a click anywhere inside the rectangle selects the choice.
    """

    def __init__(self, title: str, description: str, parent=None):
        # Keep the actual button text for the native accessibility interface,
        # even though paintEvent delegates visual text to the wrapping labels.
        super().__init__(title, parent)
        self.setObjectName("taskChoice")
        self.setProperty("keyboardFocus", False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Native radios reserve style-dependent margins around their indicator;
        # this control's painted rectangle is its complete layout geometry.
        self.setAttribute(Qt.WA_LayoutUsesWidgetRect, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(76)
        self.setAccessibleName(title)
        self.setAccessibleDescription(
            f"{description} Use arrow keys to choose a task, or press Space to select."
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(18)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(5)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("taskChoiceTitle")
        self.description_label = QLabel(description)
        self.description_label.setObjectName("taskChoiceDescription")
        for label in (self.title_label, self.description_label):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            label.setFocusPolicy(Qt.NoFocus)
            text.addWidget(label)
        row.addLayout(text, 1)
        self.status_label = QLabel("Select")
        self.status_label.setObjectName("taskChoiceStatus")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.status_label.setFocusPolicy(Qt.NoFocus)
        row.addWidget(self.status_label)
        self.toggled.connect(self._selection_changed)
        self._selection_changed(self.isChecked())

    def hitButton(self, position):
        return self.rect().contains(position)

    def _selection_changed(self, checked):
        # Text keeps selection understandable without colour or symbol fonts.
        self.status_label.setText("Selected" if checked else "Select")
        self.updateGeometry()
        QTimer.singleShot(0, self._fit_height)
        self.update()

    def heightForWidth(self, width):
        height = self.layout().totalHeightForWidth(max(1, width))
        return max(76, height)

    def hasHeightForWidth(self):
        return True

    def sizeHint(self):
        return QSize(640, self.heightForWidth(640))

    def minimumSizeHint(self):
        return QSize(280, 76)

    def _fit_height(self):
        if not self.layout():
            return
        # QRadioButton's fixed vertical policy otherwise caps a wrapped row at
        # sizeHint's preferred width. Recompute that cap for its actual width,
        # including after font/scale changes, without consuming spare height.
        needed = self.heightForWidth(self.width())
        if self.minimumHeight() != needed or self.maximumHeight() != needed:
            self.setFixedHeight(needed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.StyleChange, QEvent.FontChange,
                            QEvent.ApplicationFontChange):
            QTimer.singleShot(0, self._fit_height)

    def paintEvent(self, event):
        # Native radio paint would draw a second title and a tiny indicator.
        # PE_Widget paints the styled, full-size rectangle instead; selection
        # remains a true radio state and is also spelled out in status_label.
        option = QStyleOption()
        option.initFrom(self)
        if self.isChecked():
            option.state |= QStyle.State_On
        else:
            option.state |= QStyle.State_Off
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)

    def _refresh_focus(self, focused):
        self.setProperty("keyboardFocus", focused)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._refresh_focus(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._refresh_focus(False)
