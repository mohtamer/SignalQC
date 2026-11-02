"""Read-only HDF5 inspection and explicit project-level schema mapping."""
from __future__ import annotations

import copy
import fnmatch
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QWidget, QDialogButtonBox, QFileDialog, QScrollArea,
)

FIELDS = [
    ('sampling_rate', 'Sampling rate (Hz)'), ('dt', 'Sample interval (s)'),
    ('start_time', 'Record start time (UTC)'), ('origin_time', 'Event origin time (UTC)'),
    ('network', 'Network'), ('station', 'Station'), ('location', 'Location code'),
    ('channel', 'Channel / component'), ('magnitude', 'Magnitude'),
    ('event_latitude', 'Event latitude (degrees)'), ('event_longitude', 'Event longitude (degrees)'),
    ('event_depth', 'Event depth (km)'), ('station_latitude', 'Station latitude (degrees)'),
    ('station_longitude', 'Station longitude (degrees)'), ('station_elevation', 'Station elevation (m)'),
    ('event_id', 'Event identifier'), ('trace_id', 'Original trace identifier'),
    ('station_group_id', 'Station grouping identifier (optional)'),
    ('recording_group_id', 'Recording grouping identifier (optional)'),
    ('station_group_id', 'Known station association (optional)'),
    ('recording_group_id', 'Known recording association (optional)'),
]
KINDS = [('Automatic', 'auto'), ('Attribute', 'attribute'), ('Dataset', 'dataset'),
         ('Constant', 'constant'), ('Path level', 'path_part')]


class Inspection(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            from .hdf5_import_v1 import inspect_hdf5
            self.result.emit(inspect_hdf5(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))


class Hdf5MappingDialog(QDialog):
    def __init__(self, files=None, mapping=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Inspect HDF5 layout')
        self.resize(1040, 690)
        self.setMinimumSize(850, 580)
        self._original = copy.deepcopy(mapping or {})
        self._mapping = copy.deepcopy(self._original)
        self.worker = None
        self.inventory = None
        self._closing_after_read = False
        layout = QVBoxLayout(self)
        title = QLabel('HDF5 layout and field mapping')
        title.setStyleSheet('font-size:17pt;font-weight:600;')
        layout.addWidget(title)
        note = QLabel('Inspect the stored datasets and specify how they represent your records. Applying a mapping saves project settings only. Processing creates a compatible working copy; the selected input files are preserved.')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.file_picker = QComboBox()
        self.file_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.file_picker.setMinimumContentsLength(28)
        for path in dict.fromkeys(map(str, files or [])):
            self.file_picker.addItem(path, path)
        row.addWidget(self.file_picker, 1)
        self.browse_button = QPushButton('Choose file')
        self.browse_button.clicked.connect(self.browse)
        row.addWidget(self.browse_button)
        self.inspect_button = QPushButton('Inspect file')
        self.inspect_button.clicked.connect(self.inspect_selected)
        row.addWidget(self.inspect_button)
        layout.addLayout(row)
        self.enabled = QCheckBox('Use this layout mapping for the selected HDF5 inputs')
        self.enabled.setChecked(bool(mapping and mapping.get('enabled', False)))
        self.enabled.toggled.connect(self.update_apply)
        layout.addWidget(self.enabled)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        datasets_page = QWidget()
        d = QVBoxLayout(datasets_page)
        self.dataset_table = QTableWidget(0, 4)
        self.dataset_table.setHorizontalHeaderLabels(['Dataset path', 'Shape', 'Data type', 'Units'])
        self.dataset_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dataset_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.dataset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3): self.dataset_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.dataset_table.setMinimumHeight(130)
        self.dataset_table.setMaximumHeight(190)
        d.addWidget(self.dataset_table, 1)
        self.suggest_button = QPushButton('Use suggested dataset fields')
        self.suggest_button.setEnabled(False)
        self.suggest_button.clicked.connect(self.use_suggestions)
        d.addWidget(self.suggest_button, alignment=Qt.AlignLeft)
        form = QFormLayout()
        self.dataset_fields = {}
        self.unit_fields = {}
        labels = {'acceleration':'Acceleration (required)', 'raw':'Raw counts (optional)',
                  'velocity':'Velocity (optional)', 'displacement':'Displacement (optional)'}
        for key, label in labels.items():
            field = QComboBox()
            field.setEditable(True)
            field.setInsertPolicy(QComboBox.NoInsert)
            field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            field.setMinimumContentsLength(20)
            field.lineEdit().setPlaceholderText('Dataset path or **/dataset_name')
            field.setEditText(str((mapping or {}).get('datasets', {}).get(key, '')))
            field.currentTextChanged.connect(self.update_apply)
            self.dataset_fields[key] = field
            wrap = QWidget()
            row = QHBoxLayout(wrap)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(field, 1)
            unit = QComboBox()
            unit.addItems({'acceleration':['auto', 'm/s^2', 'cm/s^2', 'g'], 'raw':['auto', 'counts'],
                           'velocity':['auto', 'm/s', 'cm/s'], 'displacement':['auto', 'm', 'cm']}[key])
            unit.setCurrentText((mapping or {}).get(key + '_units', 'auto'))
            unit.setMinimumWidth(100)
            unit.setToolTip(label + ' units')
            self.unit_fields[key] = unit
            row.addWidget(unit)
            form.addRow(label, wrap)
        self.units = self.unit_fields['acceleration']
        d.addLayout(form)
        syntax = QLabel('Use **/acc to match a dataset named acc in every record group, or select an exact dataset path. Choose the physical units explicitly when the file does not identify them. A name match alone does not establish units. Only numeric one-dimensional traces or two-dimensional component matrices are supported.')
        syntax.setWordWrap(True)
        d.addWidget(syntax)
        dataset_scroll = QScrollArea()
        dataset_scroll.setWidgetResizable(True)
        dataset_scroll.setWidget(datasets_page)
        self.tabs.addTab(dataset_scroll, 'Datasets')

        metadata_page = QWidget()
        m = QVBoxLayout(metadata_page)
        text = QLabel('Automatic matching uses common field names. For custom layouts, select an attribute or scalar dataset, provide a known constant, or use a zero-based level of the record-group path. Time values must be ISO timestamps in UTC; numeric epoch units are ambiguous. Leave unknown physical metadata automatic; do not invent coordinates or magnitude.')
        text.setWordWrap(True)
        m.addWidget(text)
        self.metadata_table = QTableWidget(len(FIELDS), 3)
        self.metadata_table.setHorizontalHeaderLabels(['Meaning', 'Source', 'Attribute, dataset, value or path level'])
        self.metadata_table.verticalHeader().hide()
        self.metadata_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.metadata_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.metadata_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.metadata_fields = {}
        for row_number, (key, label) in enumerate(FIELDS):
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.metadata_table.setItem(row_number, 0, item)
            kind = QComboBox()
            for name, value in KINDS: kind.addItem(name, value)
            prior = (mapping or {}).get('metadata', {}).get(key, {})
            kind.setCurrentIndex(max(0, kind.findData(prior.get('kind', 'auto'))))
            value = QComboBox()
            value.setEditable(True)
            value.setInsertPolicy(QComboBox.NoInsert)
            value.setEditText(str(prior.get('value', '')))
            self.metadata_table.setCellWidget(row_number, 1, kind)
            self.metadata_table.setCellWidget(row_number, 2, value)
            self.metadata_fields[key] = (kind, value)
            kind.currentIndexChanged.connect(lambda _, k=key: self.metadata_source_changed(k))
            value.currentTextChanged.connect(self.update_apply)
            self.metadata_table.setRowHeight(row_number, 39)
        m.addWidget(self.metadata_table, 1)
        self.tabs.addTab(metadata_page, 'Metadata')

        matrix_page = QWidget()
        f = QFormLayout(matrix_page)
        self.sample_axis = QComboBox()
        self.sample_axis.addItem('Select sample dimension', None)
        self.sample_axis.addItem('Rows contain samples (axis 0)', 0)
        self.sample_axis.addItem('Columns contain samples (axis 1)', 1)
        self.sample_axis.setCurrentIndex(max(0, self.sample_axis.findData((mapping or {}).get('sample_axis'))))
        self.sample_axis.currentIndexChanged.connect(self.update_apply)
        f.addRow('Sample direction in a 2D dataset', self.sample_axis)
        self.components = QLineEdit(', '.join((mapping or {}).get('components', [])))
        self.components.setPlaceholderText('For example: HNE, HNN, HNZ')
        f.addRow('Component names in stored order', self.components)
        matrix_note = QLabel('These settings apply to datasets containing several waveform components in one matrix. The other axis represents components. Select the sample dimension explicitly; no orientation is assumed. Enter labels that match the stored order when known. One-dimensional traces do not require these settings.')
        matrix_note.setWordWrap(True)
        f.addRow(matrix_note)
        self.tabs.addTab(matrix_page, 'Multi-component arrays')
        self.status = QLabel('Select a file and choose Inspect file to review its layout.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.Apply)
        self.apply_button.clicked.connect(self.apply_mapping)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for key in self.metadata_fields: self.metadata_source_changed(key)
        self.update_apply()
        self.file_picker.currentIndexChanged.connect(self.file_selection_changed)

    def file_selection_changed(self, *args):
        self.inventory = None
        self.dataset_table.setRowCount(0)
        self.suggest_button.setEnabled(False)
        self.status.setText('Choose Inspect file to read the selected file layout.')

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select HDF5 file', self.file_picker.currentText(), 'HDF5 (*.h5 *.hdf5)')
        if path:
            existing = self.file_picker.findData(path)
            if existing < 0:
                self.file_picker.addItem(path, path)
                existing = self.file_picker.count() - 1
            self.file_picker.setCurrentIndex(existing)
            self.inspect_selected()

    def inspect_selected(self):
        if self.busy(): return
        path = self.file_picker.currentData() or self.file_picker.currentText()
        if not path:
            self.status.setText('Choose an HDF5 file to inspect.')
            return
        self.inventory = None
        self.status.setText('Reading dataset headers and available metadata…')
        self.inspect_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.file_picker.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.suggest_button.setEnabled(False)
        self.worker = Inspection(path, self)
        self.worker.result.connect(self.inspected)
        self.worker.failed.connect(self.inspection_failed)
        self.worker.finished.connect(self.inspection_finished)
        self.worker.start()

    def inspected(self, inventory):
        self.inventory = inventory
        rows = inventory.get('datasets', [])
        self.dataset_table.setRowCount(len(rows))
        paths = [''] + [str(row['path']) for row in rows]
        patterns = sorted({'**/' + path.rsplit('/', 1)[-1] for path in paths if path})
        for index, row in enumerate(rows):
            values = [row.get('path', ''), ' × '.join(map(str, row.get('shape', []))) or 'scalar', row.get('dtype', ''), row.get('units', '')]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(row.get('path', '')) + '\nAttributes: ' + str(row.get('attributes', {})))
                self.dataset_table.setItem(index, column, item)
        for field in self.dataset_fields.values():
            text = field.currentText()
            field.blockSignals(True)
            field.clear()
            field.addItems(paths + patterns)
            field.setEditText(text)
            field.blockSignals(False)
        self.suggest_button.setEnabled(bool(inventory.get('suggestions')))
        for key in self.metadata_fields: self.metadata_source_changed(key)
        ambiguities = list(inventory.get('ambiguities') or inventory.get('issues') or [])
        if inventory.get('truncated'): ambiguities.append('The inspection list is limited to the first 5,000 datasets. Select a precise pattern and review the processing scan before running.')
        self.status.setText(f'{len(rows)} datasets detected. Review names, shapes and units before applying a mapping.' + ('\n' + '; '.join(map(str, ambiguities)) if ambiguities else ''))
        self.update_apply()

    def inspection_failed(self, message):
        self.inventory = None
        self.dataset_table.setRowCount(0)
        self.status.setText('Cannot inspect this file: ' + message)

    def inspection_finished(self):
        self.inspect_button.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.file_picker.setEnabled(True)
        if self._closing_after_read:
            super().reject()
        else:
            self.update_apply()

    def use_suggestions(self):
        suggestions = (self.inventory or {}).get('suggestions', {})
        for key, field in self.dataset_fields.items():
            suggestion = suggestions.get(key)
            # Multiple candidates require the user's explicit selection.
            if isinstance(suggestion, str): field.setEditText(suggestion)
        self.enabled.setChecked(True)
        self.status.setText('Suggested fields have been placed in the form. Review the mapping and units; ambiguous candidates require an explicit selection.')
        self.update_apply()

    def metadata_source_changed(self, key):
        kind, value = self.metadata_fields[key]
        selected = kind.currentData()
        text = value.currentText()
        value.blockSignals(True)
        value.clear()
        if selected == 'attribute':
            value.addItems([str(name) for name in (self.inventory or {}).get('attributes', [])])
        elif selected == 'dataset':
            value.addItems([str(row['path']) for row in (self.inventory or {}).get('datasets', [])])
        value.setEditText(text)
        value.setEnabled(selected != 'auto')
        placeholders = {'auto': 'Automatic matching', 'attribute': 'For example: sampling_rate',
                        'dataset': 'Exact scalar dataset path', 'constant': 'Known value',
                        'path_part': '0 = first group, 1 = next group'}
        value.lineEdit().setPlaceholderText(placeholders[selected])
        value.blockSignals(False)
        self.update_apply()

    def validation_errors(self):
        if not self.enabled.isChecked(): return []
        errors = []
        if not self.dataset_fields['acceleration'].currentText().strip():
            errors.append('Select the acceleration dataset or path pattern.')
        pattern = self.dataset_fields['acceleration'].currentText().strip().lstrip('/')
        matches_matrix = any(len(row.get('shape', [])) == 2 and
                             (fnmatch.fnmatchcase(str(row.get('path','')).lstrip('/'), pattern) or
                              pattern.startswith('**/') and fnmatch.fnmatchcase(str(row.get('path','')).lstrip('/'), pattern[3:]))
                             for row in (self.inventory or {}).get('datasets', []))
        if matches_matrix and self.sample_axis.currentData() is None:
            errors.append('Select the sample dimension in Multi-component arrays for the selected two-dimensional acceleration dataset.')
        for key, (kind, value) in self.metadata_fields.items():
            source, text = kind.currentData(), value.currentText().strip()
            if source != 'auto' and not text:
                errors.append(f'Provide the selected source value for {dict(FIELDS)[key]}.')
            if source == 'path_part' and (not text.isdigit()):
                errors.append(f'Use a zero-based nonnegative path level for {dict(FIELDS)[key]}.')
        return errors

    def update_apply(self, *args):
        if not hasattr(self, 'apply_button'): return
        self.apply_button.setEnabled(not self.busy() and not self.validation_errors())
        errors = self.validation_errors()
        self.apply_button.setToolTip('\n'.join(errors) if errors else 'Save this layout mapping in the project settings.')
        if errors and self.enabled.isChecked(): self.status.setText('Required before applying: ' + ' '.join(errors))

    def apply_mapping(self):
        if self.busy(): return
        errors = self.validation_errors()
        if errors:
            self.status.setText('\n'.join(errors))
            return
        metadata = {}
        for key, (kind, value) in self.metadata_fields.items():
            source, text = kind.currentData(), value.currentText().strip()
            metadata[key] = {'kind': source, 'value': int(text) if source == 'path_part' else text}
        self._mapping = dict(enabled=self.enabled.isChecked(),
                             datasets={key: field.currentText().strip() for key, field in self.dataset_fields.items()},
                             metadata=metadata, **{key+'_units':widget.currentText() for key,widget in self.unit_fields.items()},
                             components=[value.strip() for value in self.components.text().split(',') if value.strip()])
        if self.sample_axis.currentData() is not None:
            self._mapping['sample_axis'] = self.sample_axis.currentData()
        self.accept()

    def mapping(self):
        return copy.deepcopy(self._mapping)

    def reject(self):
        if self.busy():
            self._closing_after_read = True
            self.status.setText('The inspection is finishing. This dialog will close when the file has been released.')
            return
        super().reject()

    def closeEvent(self, event):
        if self.busy():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
