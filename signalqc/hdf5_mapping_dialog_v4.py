"""Accessible navigation layered on the preserved, reviewed v3 field mapper.

This module changes presentation and navigation only. Inspection, suggestions,
mapping serialization, preview and confirmation remain in the previous versions.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QLabel, QListView,
    QPushButton, QScrollArea, QWidget,
)

from .hdf5_mapping_dialog_v2 import FIELDS
from .hdf5_mapping_dialog_v3 import Hdf5MappingDialog as MappingDialogV3


class Hdf5MappingDialog(MappingDialogV3):
    def __init__(self, *args, **kwargs):
        from .theme_v1 import STYLE, apply_theme
        apply_theme(QApplication.instance())
        super().__init__(*args, **kwargs)
        self.setStyleSheet(STYLE)
        self.layout().itemAt(1).widget().setText(
            'Match the waveform and metadata fields, then preview and confirm. Source files remain unchanged.')
        self.resize(1180, 780)
        self.setAccessibleName('Review HDF5 data mapping')
        self.setAccessibleDescription(
            'Three steps: select waveforms, review metadata, then preview and confirm. '
            'Use the visible scrollbars or page buttons to reach content below.')
        self._add_page_navigation()
        self._configure_accessible_controls()
        self.tabs.currentChanged.connect(self.update_page_navigation)
        self.update_page_navigation()

    def scroll_page(self):
        scroll, layout = super().scroll_page()
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.verticalScrollBar().setAccessibleName('Page scroll position')
        scroll.verticalScrollBar().setSingleStep(48)
        return scroll, layout

    def build_metadata(self):
        super().build_metadata()
        self.metadata_help.setText(
            'Review each suggested source and example. Select unmatched fields manually; '
            'optional fields may remain unknown.')
        self.metadata_help.setToolTip(
            'Sampling rate OR sample interval is sufficient. Select a field to review its '
            'units or time format. Saved and manual choices are retained when suggestions refresh.')
        table = self.metadata_table
        for col, width in [(0, 180), (1, 155), (3, 175), (4, 185)]:
            table.setColumnWidth(col, width)
        table.horizontalHeader().setMinimumSectionSize(140)
        table.setMinimumHeight(275)
        table.setMaximumHeight(460)
        for row in range(table.rowCount()):
            table.setRowHeight(row, 43)
        for kind, _ in self.metadata_fields.values():
            index = kind.findData('attribute')
            if index >= 0:
                kind.setItemText(index, 'Attribute')
            index = kind.findData('missing')
            if index >= 0:
                kind.setItemText(index, 'Unknown')
            index = kind.findData('dataset')
            if index >= 0:
                kind.setItemText(index, 'HDF5 dataset')

        self.field_navigation = QWidget()
        row = QHBoxLayout(self.field_navigation)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel('Jump to field')
        row.addWidget(label)
        self.field_picker = QComboBox()
        for key, title in FIELDS:
            self.field_picker.addItem(title, key)
        self.field_picker.setMinimumContentsLength(18)
        self.field_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.field_picker.setAccessibleName('Jump to metadata field')
        label.setBuddy(self.field_picker)
        row.addWidget(self.field_picker, 1)
        self.previous_field_button = QPushButton('Previous field')
        self.next_field_button = QPushButton('Next field')
        self.previous_field_button.setAutoDefault(False)
        self.next_field_button.setAutoDefault(False)
        self.previous_field_button.setAccessibleName('Review previous metadata field')
        self.next_field_button.setAccessibleName('Review next metadata field')
        self.previous_field_button.clicked.connect(lambda: self.move_field(-1))
        self.next_field_button.clicked.connect(lambda: self.move_field(1))
        self.field_picker.activated.connect(self.select_field)
        row.addWidget(self.previous_field_button)
        row.addWidget(self.next_field_button)
        self.field_position = QLabel()
        self.field_position.setMinimumWidth(62)
        row.addWidget(self.field_position)
        page_layout = self.tabs.widget(1).widget().layout()
        page_layout.removeWidget(self.metadata_suggest_button)
        page_layout.removeWidget(self.use_table)
        self.metadata_actions = QWidget()
        actions = QHBoxLayout(self.metadata_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.use_table)
        actions.addStretch()
        actions.addWidget(self.metadata_suggest_button)
        page_layout.insertWidget(1, self.metadata_actions)
        page_layout.insertWidget(page_layout.indexOf(table), self.field_navigation)
        table.currentCellChanged.connect(self.update_field_navigation)
        table.setCurrentCell(0, 0)
        self.update_field_navigation(0)

    def build_preview(self):
        super().build_preview()
        page = self.tabs.widget(2)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        page.verticalScrollBar().setAccessibleName('Preview page scroll position')
        page.verticalScrollBar().setSingleStep(48)
        self.preview_detail.setAccessibleName('Selected record details and warnings')

    def select_field(self, row):
        if not 0 <= row < self.metadata_table.rowCount():
            return
        self.metadata_table.setCurrentCell(row, 0)
        self.metadata_table.scrollToItem(
            self.metadata_table.item(row, 0), QAbstractItemView.PositionAtCenter)
        page = self.tabs.widget(1)
        # Keep the navigation bar and selected table row reachable together.
        page.ensureWidgetVisible(self.field_navigation, 0, 12)

    def move_field(self, direction):
        current = max(0, self.metadata_table.currentRow())
        self.select_field(max(0, min(len(FIELDS) - 1, current + direction)))

    def update_field_navigation(self, row, *args):
        if not hasattr(self, 'field_picker'):
            return
        row = max(0, row)
        self.field_picker.blockSignals(True)
        self.field_picker.setCurrentIndex(row)
        self.field_picker.blockSignals(False)
        self.field_position.setText(f'{row + 1} of {len(FIELDS)}')
        self.previous_field_button.setEnabled(row > 0)
        self.next_field_button.setEnabled(row < len(FIELDS) - 1)

    def _add_page_navigation(self):
        self.page_navigation = QWidget()
        row = QHBoxLayout(self.page_navigation)
        row.setContentsMargins(0, 0, 0, 0)
        self.page_navigation_hint = QLabel('Use the scrollbar or page buttons to see more.')
        self.page_navigation_hint.setWordWrap(True)
        row.addWidget(self.page_navigation_hint, 1)
        self.page_up_button = QPushButton('Page up')
        self.page_down_button = QPushButton('Page down')
        for button, direction in [(self.page_up_button, -1), (self.page_down_button, 1)]:
            button.setAutoDefault(False)
            button.setAutoRepeat(True)
            button.setAccessibleName('Scroll this page up' if direction < 0 else 'Scroll this page down')
            button.clicked.connect(lambda _, step=direction: self.scroll_current_page(step))
            row.addWidget(button)
        self.layout().insertWidget(self.layout().indexOf(self.status), self.page_navigation)
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, QScrollArea):
                page.verticalScrollBar().rangeChanged.connect(self.update_page_navigation)
                page.verticalScrollBar().valueChanged.connect(self.update_page_navigation)

    def scroll_current_page(self, direction):
        page = self.tabs.currentWidget()
        if isinstance(page, QScrollArea):
            bar = page.verticalScrollBar()
            bar.setValue(bar.value() + direction * max(48, bar.pageStep() - 36))

    def update_page_navigation(self, *args):
        if not hasattr(self, 'page_navigation'):
            return
        page = self.tabs.currentWidget()
        bar = page.verticalScrollBar() if isinstance(page, QScrollArea) else None
        self.page_up_button.setEnabled(bool(bar and bar.value() > bar.minimum()))
        self.page_down_button.setEnabled(bool(bar and bar.value() < bar.maximum()))
        self.page_navigation_hint.setText(
            'Scroll this page to see more.'
            if bar and bar.maximum() > 0 else 'All page content is visible.')

    def _configure_combo(self, combo):
        self._prepare_line_edit(combo)
        if combo.property('accessible_mapping_popup'):
            return
        combo.setProperty('accessible_mapping_popup', True)
        # The non-native popup respects maxVisibleItems and displays a scrollbar
        # that can be dragged or clicked, rather than Qt's hover-only scrollers.
        combo.setStyleSheet(combo.styleSheet() + '\nQComboBox { combobox-popup: 0; }')
        view = QListView(combo)
        view.setUniformItemSizes(True)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        view.setTextElideMode(Qt.ElideRight)
        view.setAccessibleName((combo.accessibleName() or 'Available choices') + ' list')
        view.verticalScrollBar().setAccessibleName('Scroll available choices')
        combo.setView(view)
        combo.setMaxVisibleItems(10)
        combo.setFocusPolicy(Qt.StrongFocus)
        combo.setAccessibleDescription(
            'Open the list and use its scrollbar, arrow keys, Page Up, Page Down, '
            'Home or End. Press Enter to select a choice.')

    def _prepare_line_edit(self, combo):
        if not combo.property('mapping_show_source_start'):
            combo.setProperty('mapping_show_source_start', True)
            combo.installEventFilter(self)
        edit = combo.lineEdit()
        if edit is None:
            return
        if not edit.property('mapping_show_source_start'):
            edit.setProperty('mapping_show_source_start', True)
            edit.installEventFilter(self)
        if not edit.hasFocus():
            edit.setCursorPosition(0)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.FocusOut and watched.property('mapping_show_source_start'):
            edit = watched.lineEdit() if isinstance(watched, QComboBox) else watched
            if edit is not None:
                edit.setCursorPosition(0)
        return super().eventFilter(watched, event)

    def fill_source(self, combo, kind, prior=None, join=False):
        super().fill_source(combo, kind, prior, join)
        self._prepare_line_edit(combo)

    def update_statuses(self):
        super().update_statuses()
        if not hasattr(self, 'metadata_fields'):
            return
        for combo in [*(source for _, source in self.metadata_fields.values()), *self.dataset_fields.values()]:
            self._prepare_line_edit(combo)
            text = combo.currentText()
            if text and text != 'Select a source':
                combo.setToolTip('Selected source: ' + text + '\n' + combo.toolTip())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'metadata_fields'):
            for combo in [*(source for _, source in self.metadata_fields.values()), *self.dataset_fields.values()]:
                self._prepare_line_edit(combo)

    def _configure_accessible_controls(self):
        names = {
            'file_picker': 'HDF5 file to inspect', 'scope_picker': 'Files receiving this mapping',
            'layout_mode': 'HDF5 input layout', 'sample_axis': 'Sample direction in a matrix',
            'field_unit': 'Selected metadata field input units',
        }
        for attribute, label in names.items():
            getattr(self, attribute).setAccessibleName(label)
        for key, title in FIELDS:
            kind, source = self.metadata_fields[key]
            kind.setAccessibleName(title + ': source type')
            source.setAccessibleName(title + ': source field')
        for key, combo in self.dataset_fields.items():
            combo.setAccessibleName(key.capitalize() + ' waveform dataset')
            self.unit_fields[key].setAccessibleName(key.capitalize() + ' stored units')
        for combo in self.findChildren(QComboBox):
            self._configure_combo(combo)
        for table, name in [
            (self.dataset_table, 'Available HDF5 datasets'),
            (self.metadata_table, 'Metadata field mappings'),
            (self.join_table, 'Exact table matching keys'),
            (self.file_summary, 'Preview summary by file'),
            (self.preview_table, 'Mapped record preview'),
        ]:
            table.setAccessibleName(name)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
            table.setAlternatingRowColors(True)
            table.setSelectionMode(QAbstractItemView.SingleSelection)
            table.verticalScrollBar().setAccessibleName('Scroll ' + name.lower())

    def add_join(self, spec=None):
        super().add_join(spec)
        for column in range(self.join_table.columnCount()):
            combo = self.join_table.cellWidget(self.join_table.rowCount() - 1, column)
            if isinstance(combo, QComboBox):
                self._configure_combo(combo)
