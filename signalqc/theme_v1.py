"""Consistent local light theme and mouse/keyboard navigation for desktop UI."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QComboBox, QListWidget, QScrollArea,
    QTableView, QPlainTextEdit, QTextBrowser,
)

ASSETS = (Path(__file__).resolve().parents[1] / "assets" / "ui_v1").as_posix()

STYLE = r'''
QWidget {font-family:"Segoe UI";font-size:11pt;color:#1c2e42;}
QMainWindow,QDialog,QWidget#pageBody,QStackedWidget {background:#f3f6fa;}
QLabel {background:transparent;}
QLabel#title {font-size:22pt;font-weight:600;color:#162c46;}
QLabel#subtitle {color:#485b70;}
QLabel#eyebrow {font-size:9pt;font-weight:700;color:#37665f;}
QWidget#sidebar {background:#142e49;}
QWidget#sidebar QLabel {color:#edf6ff;}
QListWidget#nav {background:#142e49;border:2px solid transparent;color:#dce8f2;font-size:10pt;}
QListWidget#nav:focus {border-color:#9bc7ef;}
QListWidget#nav::item {padding:9px 8px;margin:3px 2px;border-radius:5px;}
QListWidget#nav::item:hover {background:#224767;}
QListWidget#nav::item:selected {background:#2b597e;color:white;border-left:3px solid #aad6ff;}
QGroupBox {background:white;border:1px solid #cbd5e1;border-radius:8px;margin-top:13px;padding:17px 14px 12px;font-weight:600;}
QGroupBox::title {subcontrol-origin:margin;left:13px;top:2px;background:white;padding:0 6px;color:#243c54;}
QPushButton {background:white;color:#243c54;border:2px solid #c5d0dd;border-radius:5px;padding:7px 12px;min-height:20px;font-weight:600;}
QPushButton:hover {background:#eaf2fb;border-color:#7895b7;}
QPushButton:pressed {background:#dbe9f8;border-color:#175fa3;}
QPushButton:focus {border-color:#175fa3;}
QPushButton:disabled {color:#667589;background:#e9eef4;border-color:#d1dae5;}
QPushButton#primary {background:#175fa3;color:white;border:2px solid #175fa3;padding:9px 17px;}
QPushButton#primary:hover {background:#104d87;border-color:#104d87;}
QPushButton#primary:focus {border-color:#112c48;background:#104d87;}
QPushButton#primary:disabled {background:#d8e2ed;color:#56677c;border-color:#c5d0dd;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QPlainTextEdit {
    background:white;color:#1c2e42;border:2px solid #c5d0dd;border-radius:4px;
    padding:5px;selection-background-color:#175fa3;selection-color:white;
}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus,QPlainTextEdit:focus {border-color:#175fa3;}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled {background:#edf1f6;color:#667589;}
QComboBox {padding-right:29px;min-height:20px;combobox-popup:0;}
QComboBox::drop-down {subcontrol-origin:padding;subcontrol-position:top right;width:27px;background:#edf3fa;border-left:1px solid #b9c8da;border-top-right-radius:3px;border-bottom-right-radius:3px;}
QComboBox::drop-down:hover {background:#dbe9f8;}
QComboBox::down-arrow {image:url("@ASSETS@/down.svg");width:12px;height:12px;}
QComboBox QAbstractItemView {background:white;color:#1c2e42;border:2px solid #7895b7;selection-background-color:#175fa3;selection-color:white;outline:1px solid #175fa3;}
QComboBox QAbstractItemView::item {min-height:29px;padding:3px 7px;}
QSpinBox,QDoubleSpinBox {padding-right:26px;min-height:20px;}
QSpinBox::up-button,QDoubleSpinBox::up-button {subcontrol-origin:border;subcontrol-position:top right;width:24px;background:#edf3fa;border-left:1px solid #b9c8da;border-bottom:1px solid #b9c8da;}
QSpinBox::down-button,QDoubleSpinBox::down-button {subcontrol-origin:border;subcontrol-position:bottom right;width:24px;background:#edf3fa;border-left:1px solid #b9c8da;}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow {image:url("@ASSETS@/up.svg");width:9px;height:9px;}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow {image:url("@ASSETS@/down.svg");width:9px;height:9px;}
QListWidget,QTableView,QTextBrowser {background:white;color:#1c2e42;border:2px solid #d0d9e4;border-radius:4px;selection-background-color:#dceafb;selection-color:#142e49;}
QListWidget:focus,QTableView:focus,QTextBrowser:focus {border-color:#175fa3;}
QListWidget::item {padding:5px;}
QTableView {gridline-color:#dce3ec;alternate-background-color:#f0f4f9;}
QHeaderView {background:#e8eef6;}
QHeaderView::section {background:#e8eef6;color:#243c54;border:none;border-right:1px solid #cfdae6;border-bottom:1px solid #c2cfdd;padding:8px 6px;font-weight:600;}
QTableCornerButton::section {background:#e8eef6;border:none;}
QCheckBox,QRadioButton {spacing:9px;padding:4px;border:2px solid transparent;border-radius:4px;}
QCheckBox:hover,QRadioButton:hover {background:#eaf2fb;}
QCheckBox:focus,QRadioButton:focus {border-color:#175fa3;background:#edf4fc;}
QCheckBox:disabled,QRadioButton:disabled {color:#667589;}
QCheckBox::indicator,QRadioButton::indicator {width:20px;height:20px;border:1px solid #657a93;background:white;}
QCheckBox::indicator {border-radius:3px;}
QRadioButton::indicator {border-radius:10px;}
QCheckBox::indicator:checked {background:#175fa3;border-color:#175fa3;image:url("@ASSETS@/check.svg");}
QRadioButton::indicator:checked {border-color:#175fa3;image:url("@ASSETS@/radio.svg");}
QCheckBox::indicator:disabled,QRadioButton::indicator:disabled {background:#d5dfea;border-color:#8594a6;}
QCheckBox::indicator:checked:disabled {background:#657a93;image:url("@ASSETS@/check.svg");}
QProgressBar {background:#e1e8f1;color:#162c46;border:1px solid #bac8d9;border-radius:4px;text-align:center;min-height:20px;}
QProgressBar::chunk {background:#82b3dd;border-radius:3px;}
QTabWidget::pane {border:1px solid #c5d0dd;background:#f3f6fa;}
QTabBar::tab {background:#e7eef7;color:#304964;padding:9px 15px;border:2px solid transparent;border-bottom:3px solid transparent;}
QTabBar::tab:hover {background:#dce8f6;}
QTabBar::tab:selected {background:white;color:#174f83;font-weight:600;border-bottom-color:#175fa3;}
QTabBar::tab:focus {border-color:#175fa3;}
QScrollArea {border:none;background:#f3f6fa;}
QScrollBar:vertical {background:#e3eaf3;width:20px;margin:20px 0;border-left:1px solid #b3c2d4;}
QScrollBar:horizontal {background:#e3eaf3;height:20px;margin:0 20px;border-top:1px solid #b3c2d4;}
QScrollBar::handle:vertical {background:#758da8;min-height:34px;border:3px solid #e3eaf3;border-radius:5px;}
QScrollBar::handle:horizontal {background:#758da8;min-width:34px;border:3px solid #e3eaf3;border-radius:5px;}
QScrollBar::handle:hover,QScrollBar::handle:pressed {background:#315f8c;}
QScrollBar::sub-line:vertical {subcontrol-origin:margin;subcontrol-position:top;height:20px;background:#dce6f2;border:1px solid #afc0d5;}
QScrollBar::add-line:vertical {subcontrol-origin:margin;subcontrol-position:bottom;height:20px;background:#dce6f2;border:1px solid #afc0d5;}
QScrollBar::sub-line:horizontal {subcontrol-origin:margin;subcontrol-position:left;width:20px;background:#dce6f2;border:1px solid #afc0d5;}
QScrollBar::add-line:horizontal {subcontrol-origin:margin;subcontrol-position:right;width:20px;background:#dce6f2;border:1px solid #afc0d5;}
QScrollBar::sub-line:hover,QScrollBar::add-line:hover {background:#bacee7;}
QScrollBar::up-arrow {image:url("@ASSETS@/up.svg");width:10px;height:10px;}
QScrollBar::down-arrow {image:url("@ASSETS@/down.svg");width:10px;height:10px;}
QScrollBar::left-arrow {image:url("@ASSETS@/left.svg");width:10px;height:10px;}
QScrollBar::right-arrow {image:url("@ASSETS@/right.svg");width:10px;height:10px;}
QScrollBar::add-page,QScrollBar::sub-page {background:#e3eaf3;}
QScrollBar:focus {border:2px solid #175fa3;}
QSplitter::handle {background:#c8d5e5;width:7px;height:7px;}
QSplitter::handle:hover {background:#7798bd;}
QStatusBar {background:#e7eef7;color:#304964;}
QToolTip {background:#ffffff;color:#162c46;border:1px solid #7d95b0;padding:6px;}
QLabel#stat {font-size:25pt;font-weight:700;color:#175fa3;}
'''.replace("@ASSETS@", ASSETS)


def apply_theme(app):
    """Keep all widget surfaces readable even under the Windows dark theme."""
    if app is None:
        return
    from .wheel_guard_v1 import install_settings_wheel_guard
    install_settings_wheel_guard(app)
    if not QFontDatabase.families():
        fonts = Path("C:/Windows/Fonts")
        for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    if app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    colors = {
        QPalette.Window: "#f3f6fa", QPalette.WindowText: "#1c2e42",
        QPalette.Base: "#ffffff", QPalette.AlternateBase: "#f0f4f9",
        QPalette.ToolTipBase: "#ffffff", QPalette.ToolTipText: "#162c46",
        QPalette.Text: "#1c2e42", QPalette.Button: "#ffffff",
        QPalette.ButtonText: "#243c54", QPalette.BrightText: "#ffffff",
        QPalette.Highlight: "#175fa3", QPalette.HighlightedText: "#ffffff",
        QPalette.Link: "#175fa3", QPalette.LinkVisited: "#633c91",
        QPalette.PlaceholderText: "#596b80", QPalette.Light: "#ffffff",
        QPalette.Midlight: "#e6edf5", QPalette.Mid: "#a9b9cc",
        QPalette.Dark: "#657a93", QPalette.Shadow: "#304964",
    }
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        for role, color in colors.items():
            palette.setColor(group, role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#667589"))
    app.setPalette(palette)
    app.setFont(QFont("Segoe UI", 11))


def configure_accessibility(root):
    """Expose standard scrollbar controls without changing selected values."""
    for area in root.findChildren(QAbstractScrollArea):
        if isinstance(area, (QScrollArea, QListWidget, QTableView, QPlainTextEdit, QTextBrowser)):
            if area.objectName() != "nav":
                area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            area.verticalScrollBar().setAccessibleName("Scroll down or up")
            area.horizontalScrollBar().setAccessibleName("Scroll right or left")
            area.verticalScrollBar().setSingleStep(30)
        if isinstance(area, QAbstractItemView):
            area.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            area.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    for combo in root.findChildren(QComboBox):
        combo.setMaxVisibleItems(12)
        combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        combo.view().setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        combo.view().verticalScrollBar().setAccessibleName("Scroll available choices")
