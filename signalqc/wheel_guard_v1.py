"""Keep wheel navigation from modifying form values, including in new dialogs."""
from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication, QAbstractScrollArea, QAbstractSpinBox, QComboBox, QDial,
    QSlider, QWidget,
)


class SettingsWheelGuard(QObject):
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        control = watched
        while control is not None:
            if isinstance(control, (QComboBox, QAbstractSpinBox, QSlider, QDial)):
                break
            # An open dropdown's list must remain scrollable. Its viewport is
            # not a setting editor even though the popup belongs to a combo.
            if isinstance(control, QAbstractScrollArea) or control.isWindow():
                return False
            control = control.parentWidget()
        if control is None:
            return False

        # Also block a focused editor. StrongFocus alone does not protect it.
        # Forward the original deltas to the surrounding page/table so that
        # scrolling across a form is continuous, including touchpad scrolling.
        area = control.parentWidget()
        while area is not None:
            if isinstance(area, QAbstractScrollArea):
                viewport = area.viewport()
                forwarded = QWheelEvent(
                    QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(),
                    event.inverted(), event.source(), event.pointingDevice(),
                )
                QApplication.sendEvent(viewport, forwarded)
                break
            area = area.parentWidget()
        event.accept()
        return True


def install_settings_wheel_guard(app):
    if not hasattr(app, "_signalqc_settings_wheel_guard"):
        guard = SettingsWheelGuard(app)
        app.installEventFilter(guard)
        app._signalqc_settings_wheel_guard = guard
