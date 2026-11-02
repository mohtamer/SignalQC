"""Guided, read-only HDF5 inspection, field matching and record preview."""
from __future__ import annotations

import copy
import fnmatch
import json
import math
from pathlib import Path

from PySide6.QtCore import Qt,QThread,QTimer,Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QGridLayout,
    QWidget,QLabel,QPushButton,QComboBox,QLineEdit,QCheckBox,QGroupBox,QScrollArea,
    QTabWidget,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QDialogButtonBox,QSpinBox,QPlainTextEdit)

from .hdf5_mapping_dialog_v1 import FIELDS as LEGACY_FIELDS

FIELDS=list(dict(LEGACY_FIELDS).items())
KINDS=[('Automatic match','auto'),('Missing / unknown','missing'),('HDF5 attribute','attribute'),
       ('Scalar HDF5 dataset','dataset'),('Table column','table'),('Known constant','constant'),('Path level','path_part')]
UNITS={'acceleration':['auto','m/s^2','cm/s^2','g'],'raw':['auto','counts'],
       'velocity':['auto','m/s','cm/s'],'displacement':['auto','m','cm']}
FIELD_UNITS={'sampling_rate':['auto','Hz'],'dt':['auto','s','ms','us'],
             'event_depth':['auto','km','m'],'station_elevation':['auto','m','km']}
TIME_FIELDS={'start_time','origin_time'}
DATE_FORMATS=[('Year-month-day, 24-hour time','%Y-%m-%d %H:%M:%S'),
              ('Day/month/year, 24-hour time','%d/%m/%Y %H:%M:%S'),
              ('Month/day/year, 24-hour time','%m/%d/%Y %H:%M:%S'),
              ('Year/month/day, 24-hour time','%Y/%m/%d %H:%M:%S'),
              ('Year-month-day, fractional seconds','%Y-%m-%d %H:%M:%S.%f')]
RESERVED={'dataset_path':'Full waveform dataset path','record_path':'Record group path',
          'source_filename':'Source filename','component_index':'Component index (zero-based)'}


def short(value,limit=85):
    text=json.dumps(value,ensure_ascii=False,default=str) if isinstance(value,(dict,list)) else str(value)
    return text if len(text)<=limit else text[:limit-1]+'…'


def set_combo(combo,value):
    index=combo.findData(value)
    if index>=0:combo.setCurrentIndex(index)


def source_label(entry):
    spec=entry.get('source',entry)
    scope=spec.get('scope','nearest')
    label={'nearest':'nearest matching field','dataset':'waveform dataset','record':'record group','parent:0':'record group','root':'file root'}.get(scope,scope)
    if str(scope).startswith('parent:') and scope!='parent:0':label=scope.split(':')[1]+' level(s) above record'
    return f"{spec.get('value',entry.get('name',''))} · {label} · {short(entry.get('sample',''))}"


def displayed_rate(fields):
    """Display the reciprocal of a known interval without changing metadata."""
    try:
        rate=float(fields.get('sampling_rate',''))
        if math.isfinite(rate) and rate>0:return rate,False
    except (TypeError,ValueError):pass
    try:
        dt=float(fields.get('dt',''))
        if math.isfinite(dt) and dt>0:return 1/dt,True
    except (TypeError,ValueError):pass
    return '',False


def preview_native(path):
    """Reuse the standard input inventory without copying or reading samples."""
    from .data import hdf5_records
    from .hdf5_import_v2 import stage_readiness
    rows=hdf5_records(path);records=[];issues=[]
    for row in rows:
        acceleration=next((name for name in ('acceleration_waveform','acceleration','acc') if row['dataset_sizes'].get(name,0)>0),'')
        fields=copy.deepcopy(row)
        fields.setdefault('channel',row.get('component',''))
        fields.setdefault('station_latitude',row.get('latitude',''));fields.setdefault('station_longitude',row.get('longitude',''))
        errors=[] if acceleration else ['No nonempty calibrated acceleration dataset in this record.']
        records.append(dict(dataset=row['hdf5_path']+'/'+acceleration,component_index=None,valid=bool(acceleration),fields=fields,errors=errors,provenance={},stage_readiness=stage_readiness(fields)))
        issues.extend(row['hdf5_path']+': '+e for e in errors)
    if not rows:issues.append('No supported event/trace waveform groups detected. Select the mapped working-copy option for this layout.')
    return dict(traces=sum(r['valid'] for r in records),records=records,issues=issues,header_only=True,native=True)


class ReadTask(QThread):
    result=Signal(object)
    failed=Signal(str)

    def __init__(self,function,parent=None):
        super().__init__(parent);self.function=function

    def run(self):
        try:self.result.emit(self.function())
        except Exception as exc:self.failed.emit(str(exc))


class Hdf5MappingDialog(QDialog):
    def __init__(self,files=None,mapping=None,parent=None,*,mappings=None,stages=None):
        super().__init__(parent)
        self.setWindowTitle('Review HDF5 data mapping');self.resize(1140,740);self.setMinimumSize(980,650)
        self._original=copy.deepcopy(mapping or {});self._mapping=copy.deepcopy(self._original)
        self._file_mappings={str(Path(k).resolve()):copy.deepcopy(v) for k,v in (mappings or {}).items()}
        self.stages=list(stages or [])
        self.inventory=None;self.table_inventory=None;self.worker=None
        self._loading=True;self._closing=False;self._revision=0;self._preview_revision=None
        self._preview_results=[];self._metadata_options={};self._active_field=None;self._pending_table=False
        outer=QVBoxLayout(self)
        title=QLabel('Review HDF5 data mapping');title.setStyleSheet('font-size:18pt;font-weight:600;');outer.addWidget(title)
        note=QLabel('Inspect a sample, match its fields, then preview the records. Applying saves settings only; processing uses a working copy and preserves the source.');note.setWordWrap(True);outer.addWidget(note)
        row=QHBoxLayout();self.file_picker=QComboBox();self.file_picker.setMinimumContentsLength(24)
        self.file_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for path in dict.fromkeys(str(Path(p).resolve()) for p in files or []):self.file_picker.addItem(path,path)
        self._current_file=self.selected_file()
        row.addWidget(self.file_picker,1);self.browse_button=QPushButton('Choose HDF5 file');self.browse_button.clicked.connect(self.browse);row.addWidget(self.browse_button)
        self.inspect_button=QPushButton('Inspect again');self.inspect_button.clicked.connect(self.inspect_selected);row.addWidget(self.inspect_button);outer.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('Apply to'));self.scope_picker=QComboBox();self.scope_picker.addItem('This file only','file');self.scope_picker.addItem('All selected files (verify each)','all');row.addWidget(self.scope_picker,1)
        row.addWidget(QLabel('Input layout'));self.layout_mode=QComboBox();self.layout_mode.addItem('Match fields and prepare a compatible working copy','mapped');self.layout_mode.addItem('Use existing SignalQC layout','native');self.layout_mode.currentIndexChanged.connect(self.layout_changed);row.addWidget(self.layout_mode,2);outer.addLayout(row)
        self.tabs=QTabWidget();outer.addWidget(self.tabs,1)
        self.build_waveforms();self.build_metadata();self.build_preview()
        self.status=QLabel('Select an HDF5 file. Its layout will be inspected automatically.');self.status.setWordWrap(True);outer.addWidget(self.status)
        bottom=QHBoxLayout();self.back_button=QPushButton('Back');self.back_button.clicked.connect(lambda:self.tabs.setCurrentIndex(0 if self.layout_mode.currentData()=='native' else max(0,self.tabs.currentIndex()-1)));bottom.addWidget(self.back_button)
        self.next_button=QPushButton('Continue to metadata');self.next_button.clicked.connect(self.next_step);bottom.addWidget(self.next_button);bottom.addStretch()
        self.buttons=QDialogButtonBox(QDialogButtonBox.Apply|QDialogButtonBox.Cancel);self.apply_button=self.buttons.button(QDialogButtonBox.Apply);self.apply_button.setText('Apply reviewed mapping');self.apply_button.clicked.connect(self.apply_mapping);self.buttons.rejected.connect(self.reject);bottom.addWidget(self.buttons);outer.addLayout(bottom)
        self.file_picker.currentIndexChanged.connect(self.file_changed);self.scope_picker.currentIndexChanged.connect(self.changed);self.tabs.currentChanged.connect(self.navigation_changed)
        self.load_mapping(self._original);self._loading=False;self.navigation_changed();self.changed()
        QTimer.singleShot(0,self.inspect_selected)

    def scroll_page(self):
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(12,12,12,12);layout.setSpacing(10)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(body)
        return scroll,layout

    def build_waveforms(self):
        page,layout=self.scroll_page();note=QLabel('1. Select the waveform datasets, their stored units and the sample direction for component matrices.');note.setWordWrap(True);layout.addWidget(note)
        self.dataset_table=QTableWidget(0,4);self.dataset_table.setHorizontalHeaderLabels(['Stored dataset','Shape','Type','Units']);self.dataset_table.setEditTriggers(QTableWidget.NoEditTriggers);self.dataset_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.dataset_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        for col in (1,2,3):self.dataset_table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        self.dataset_table.setMinimumHeight(140);self.dataset_table.setMaximumHeight(190);layout.addWidget(self.dataset_table)
        self.suggestion_note=QLabel();self.suggestion_note.setWordWrap(True);layout.addWidget(self.suggestion_note)
        self.suggest_button=QPushButton('Use common-name suggestions');self.suggest_button.clicked.connect(self.use_suggestions);layout.addWidget(self.suggest_button,alignment=Qt.AlignLeft)
        form=QFormLayout();self.dataset_fields={};self.unit_fields={}
        for key,label in [('acceleration','Acceleration (required)'),('raw','Raw counts (optional)'),('velocity','Velocity (optional)'),('displacement','Displacement (optional)')]:
            wrap=QWidget();row=QHBoxLayout(wrap);row.setContentsMargins(0,0,0,0)
            combo=QComboBox();combo.setEditable(True);combo.setInsertPolicy(QComboBox.NoInsert);combo.setMinimumContentsLength(20);combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);combo.lineEdit().setPlaceholderText('Select a dataset or repeated-name pattern');combo.currentTextChanged.connect(self.changed)
            self.dataset_fields[key]=combo;row.addWidget(combo,1)
            units=QComboBox();units.addItems(UNITS[key]);units.setMinimumWidth(110);units.currentTextChanged.connect(self.changed);self.unit_fields[key]=units;row.addWidget(units);form.addRow(label,wrap)
        self.units=self.unit_fields['acceleration'];layout.addLayout(form)
        form=QFormLayout();self.sample_axis=QComboBox();self.sample_axis.addItem('Select for a 2D matrix',None);self.sample_axis.addItem('Rows are samples (axis 0)',0);self.sample_axis.addItem('Columns are samples (axis 1)',1);self.sample_axis.currentIndexChanged.connect(self.changed);form.addRow('Sample direction',self.sample_axis)
        self.components=QLineEdit();self.components.setPlaceholderText('Known stored order, for example HNE, HNN, HNZ');self.components.textChanged.connect(self.changed);form.addRow('Component labels',self.components);layout.addLayout(form)
        note=QLabel('A pattern such as **/acc matches that dataset name in each record group. A name match does not establish units. Matrix orientation must be selected explicitly. Leave unknown component labels blank.');note.setWordWrap(True);layout.addWidget(note);layout.addStretch();self.tabs.addTab(page,'1  Waveforms')

    def build_metadata(self):
        page,layout=self.scroll_page()
        self.metadata_help=QLabel('2. Match metadata using the available fields and sample values. Sampling rate or interval and record start form the waveform time axis. Event and coordinate requirements depend on the selected stages. Missing network or station names can remain unknown.');self.metadata_help.setWordWrap(True);layout.addWidget(self.metadata_help)
        self.use_table=QCheckBox('Use an Excel or CSV metadata table');self.use_table.toggled.connect(self.table_toggled);layout.addWidget(self.use_table)
        self.table_panel=QGroupBox('Metadata table and exact record matching');table_layout=QVBoxLayout(self.table_panel)
        row=QHBoxLayout();self.table_path=QLineEdit();self.table_path.setPlaceholderText('Optional Excel, CSV or TSV file');self.table_path.textChanged.connect(self.table_path_changed);row.addWidget(self.table_path,1)
        self.table_browse=QPushButton('Choose table');self.table_browse.clicked.connect(self.browse_table);row.addWidget(self.table_browse);table_layout.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('Sheet'));self.table_sheet=QComboBox();self.table_sheet.currentIndexChanged.connect(self.sheet_changed);row.addWidget(self.table_sheet,1);self.table_inspect=QPushButton('Read table columns');self.table_inspect.clicked.connect(self.inspect_table);row.addWidget(self.table_inspect);table_layout.addLayout(row)
        row=QHBoxLayout();row.addWidget(QLabel('Column header row'));self.table_header=QSpinBox();self.table_header.setRange(1,100000);self.table_header.setValue(1);self.table_header.valueChanged.connect(self.table_options_changed);row.addWidget(self.table_header)
        row.addWidget(QLabel('CSV delimiter'));self.table_delimiter=QComboBox()
        for label,value in [('Detect automatically',None),('Comma',','),('Tab','\t'),('Semicolon',';')]:self.table_delimiter.addItem(label,value)
        self.table_delimiter.currentIndexChanged.connect(self.table_options_changed);row.addWidget(self.table_delimiter,1);table_layout.addLayout(row)
        self.table_note=QLabel('Match rows using one or more exact identifiers. No row-order or approximate matching is used.');self.table_note.setWordWrap(True);table_layout.addWidget(self.table_note)
        self.join_table=QTableWidget(0,3);self.join_table.setHorizontalHeaderLabels(['Table column','Match against','HDF5 source or field']);self.join_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.join_table.verticalHeader().hide();self.join_table.setMinimumHeight(95);self.join_table.setMaximumHeight(145);table_layout.addWidget(self.join_table)
        row=QHBoxLayout();self.add_join_button=QPushButton('Add match key');self.add_join_button.clicked.connect(lambda:self.add_join());row.addWidget(self.add_join_button);remove=QPushButton('Remove selected key');remove.clicked.connect(self.remove_join);row.addWidget(remove);row.addStretch();table_layout.addLayout(row);layout.addWidget(self.table_panel)
        self.metadata_table=QTableWidget(len(FIELDS),4);self.metadata_table.setHorizontalHeaderLabels(['Meaning','Source type','Match this source','Example value']);self.metadata_table.verticalHeader().hide();self.metadata_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.metadata_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents);self.metadata_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents);self.metadata_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);self.metadata_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        self.metadata_table.setMinimumHeight(270);self.metadata_table.setMaximumHeight(390);self.metadata_fields={}
        for row,(key,label) in enumerate(FIELDS):
            item=QTableWidgetItem(label);item.setFlags(item.flags()&~Qt.ItemIsEditable);self.metadata_table.setItem(row,0,item)
            kind=QComboBox();[kind.addItem(label,value) for label,value in KINDS]
            value=QComboBox();value.setEditable(True);value.setInsertPolicy(QComboBox.NoInsert);value.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);value.setMinimumContentsLength(15)
            self.metadata_fields[key]=(kind,value);self.metadata_table.setCellWidget(row,1,kind);self.metadata_table.setCellWidget(row,2,value);self.metadata_table.setItem(row,3,QTableWidgetItem(''));self.metadata_table.setRowHeight(row,39)
            kind.currentIndexChanged.connect(lambda _,k=key:self.metadata_kind_changed(k));value.currentIndexChanged.connect(lambda _,k=key:self.source_changed(k));value.editTextChanged.connect(lambda _,k=key:self.source_changed(k))
        layout.addWidget(self.metadata_table)
        self.options_box=QGroupBox('Selected field conversion');options=QFormLayout(self.options_box)
        self.field_label=QLabel('Select a metadata row to review its units or time format.');self.field_label.setWordWrap(True);options.addRow(self.field_label)
        self.field_unit=QComboBox();self.field_unit.currentIndexChanged.connect(self.field_options_changed);options.addRow('Input units',self.field_unit)
        self.time_format=QComboBox();[self.time_format.addItem(label,value) for label,value in [('ISO date/time','iso'),('Numeric Unix epoch','epoch'),('Excel date serial','excel'),('Custom date/time format','strptime')]];self.time_format.currentIndexChanged.connect(self.field_options_changed);options.addRow('Time representation',self.time_format)
        self.time_unit=QComboBox();self.time_unit.addItems(['s','ms','us','ns']);self.time_unit.currentTextChanged.connect(self.field_options_changed);options.addRow('Epoch unit',self.time_unit)
        self.time_zone=QComboBox();self.time_zone.setEditable(True);self.time_zone.addItems(['UTC']);self.time_zone.currentTextChanged.connect(self.field_options_changed);options.addRow('Time zone (IANA)',self.time_zone)
        self.datetime_format=QComboBox();self.datetime_format.setEditable(True);self.datetime_format.setInsertPolicy(QComboBox.NoInsert)
        for label,value in DATE_FORMATS:self.datetime_format.addItem(label,value)
        self.datetime_format.currentTextChanged.connect(self.field_options_changed);options.addRow('Date/time order',self.datetime_format)
        self.excel_system=QComboBox();self.excel_system.addItems(['1900','1904']);self.excel_system.currentTextChanged.connect(self.field_options_changed);options.addRow('Excel date system',self.excel_system)
        self.field_options_form=options;layout.addWidget(self.options_box);self.metadata_table.currentCellChanged.connect(self.metadata_row_changed);layout.addStretch();self.tabs.addTab(page,'2  Metadata and table')

    def build_preview(self):
        page=QWidget();layout=QVBoxLayout(page);self.preview_button=QPushButton('Preview mapped records');self.preview_button.clicked.connect(self.preview);layout.addWidget(self.preview_button,alignment=Qt.AlignLeft)
        self.preview_summary=QLabel('3. Preview is required before applying. Every selected target file is checked when applying to all files.');self.preview_summary.setWordWrap(True);layout.addWidget(self.preview_summary)
        self.file_summary=QTableWidget(0,4);self.file_summary.setHorizontalHeaderLabels(['File','Valid records','Blocked records','Review notes']);self.file_summary.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch);self.file_summary.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        for col in (1,2):self.file_summary.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        self.file_summary.setFixedHeight(70);layout.addWidget(self.file_summary)
        self.preview_table=QTableWidget(0,9);self.preview_table.setHorizontalHeaderLabels(['Source / component','State','Sample rate (Hz)','Record start','Event','Station','Channel','Magnitude','Notes']);self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers);self.preview_table.setSelectionBehavior(QTableWidget.SelectRows);self.preview_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        for col,width in enumerate([180,68,115,180,110,75,70,85,95]):
            if col:self.preview_table.setColumnWidth(col,width)
        self.preview_table.setMinimumHeight(135)
        layout.addWidget(self.preview_table,1);self.preview_detail=QPlainTextEdit();self.preview_detail.setReadOnly(True);self.preview_detail.setPlaceholderText('Select a preview row to see matched source fields and warnings.');self.preview_detail.setFixedHeight(100);layout.addWidget(self.preview_detail);self.preview_table.currentCellChanged.connect(self.preview_row_changed);self.tabs.addTab(page,'3  Preview')

    def busy(self):return self.worker is not None and self.worker.isRunning()
    def selected_file(self):return str(self.file_picker.currentData() or '')
    def scope(self):return self.scope_picker.currentData()
    def target_files(self):return [str(self.file_picker.itemData(i)) for i in range(self.file_picker.count())] if self.scope()=='all' else ([self.selected_file()] if self.selected_file() else [])

    def changed(self,*args):
        if self._loading:return
        self._revision+=1;self._preview_revision=None
        self.preview_summary.setText('Mapping changed. Preview the current selection before applying.');self.update_buttons()

    def layout_changed(self,*args):
        if not hasattr(self,'dataset_fields'):return
        mapped=self.layout_mode.currentData()=='mapped'
        for widget in [*self.dataset_fields.values(),*self.unit_fields.values(),self.sample_axis,self.components,self.suggest_button]:widget.setEnabled(mapped)
        self.tabs.setTabEnabled(1,mapped)
        if not mapped and self.tabs.currentIndex()==1:self.tabs.setCurrentIndex(0)
        self.navigation_changed();self.changed()

    def launch(self,function,handler,message):
        if self.busy():return
        self.status.setText(message);self.tabs.setEnabled(False);self.file_picker.setEnabled(False);self.scope_picker.setEnabled(False);self.layout_mode.setEnabled(False);self.browse_button.setEnabled(False);self.inspect_button.setEnabled(False)
        self.worker=ReadTask(function,self);self.worker.result.connect(handler);self.worker.failed.connect(self.read_failed);self.worker.finished.connect(self.read_finished);self.worker.start();self.update_buttons()

    def read_failed(self,message):self.status.setText('Could not read the selected data: '+message);self._preview_revision=None
    def read_finished(self):
        self.tabs.setEnabled(True);self.file_picker.setEnabled(True);self.scope_picker.setEnabled(True);self.layout_mode.setEnabled(True);self.browse_button.setEnabled(True);self.inspect_button.setEnabled(True)
        if self._closing:super().reject()
        else:
            self.update_buttons()
            if self._pending_table:
                self._pending_table=False;QTimer.singleShot(0,self.inspect_table)

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self,'Select HDF5 file',self.selected_file(),'HDF5 (*.h5 *.hdf5)')
        if path:
            path=str(Path(path).resolve());index=self.file_picker.findData(path)
            if index<0:self.file_picker.addItem(path,path);index=self.file_picker.count()-1
            self.file_picker.setCurrentIndex(index)
            if self.inventory is None:self.inspect_selected()

    def file_changed(self,*args):
        if self._loading:return
        if self._current_file:self._file_mappings[self._current_file]=self.draft_mapping()
        self._current_file=self.selected_file()
        self.inventory=None;self.table_inventory=None;self.dataset_table.setRowCount(0);self.load_mapping(self._file_mappings.get(self.selected_file(),{}));self.changed();self.inspect_selected()

    def inspect_selected(self):
        if self.busy() or not self.selected_file():return
        path=self.selected_file()
        def inspect():
            from .hdf5_import_v2 import inspect_hdf5
            return inspect_hdf5(path)
        self.launch(inspect,self.inspected,'Inspecting dataset headers and available metadata…')

    def inspected(self,inventory):
        self.inventory=inventory;self._loading=True
        try:
            datasets=inventory.get('datasets',[]);self.dataset_table.setRowCount(len(datasets))
            for row,item in enumerate(datasets):
                for col,value in enumerate([item.get('path',''),' × '.join(map(str,item.get('shape',[]))) or 'scalar',item.get('dtype',''),item.get('units','')]):
                    cell=QTableWidgetItem(str(value));cell.setToolTip(str(item.get('path','')));self.dataset_table.setItem(row,col,cell)
            choices=['']+sorted({'**/'+str(item['path']).rsplit('/',1)[-1] for item in datasets if len(item.get('shape',[])) in (1,2)})+[str(item['path']) for item in datasets if len(item.get('shape',[])) in (1,2)]
            for widget in self.dataset_fields.values():
                text=widget.currentText();widget.clear();widget.addItems(choices);widget.setEditText(text)
            for key in self.metadata_fields:self.populate_source(key,self.metadata_spec(key))
            notes=inventory.get('ambiguities') or inventory.get('issues') or []
            self.suggestion_note.setText('Common-name suggestions are shown for review. Verify their units and metadata sources.'+(' '+short(notes,600) if notes else '')+(' Inspection is limited; the full selection will be checked in preview.' if inventory.get('truncated') else ''))
            self.use_suggestions(only_empty=True)
        finally:self._loading=False
        for key in self.metadata_fields:self.update_source_example(key)
        for row in range(self.join_table.rowCount()):
            kind,source=[self.join_table.cellWidget(row,c) for c in (1,2)]
            self.fill_source(source,kind.currentData(),self.combo_spec(kind.currentData(),source))
        self._pending_table=self.layout_mode.currentData()=='mapped' and self.use_table.isChecked() and bool(self.table_path.text())
        self.status.setText(f'{len(datasets)} datasets inspected. Review the matches, then continue to metadata.');self.changed()

    def use_suggestions(self,only_empty=False):
        inventory=self.inventory or {}
        for key,value in inventory.get('suggestions',{}).items():
            if key in self.dataset_fields and isinstance(value,str) and (not only_empty or not self.dataset_fields[key].currentText()):self.dataset_fields[key].setEditText(value)
        for key,candidates in inventory.get('metadata_suggestions',{}).items():
            if key not in self.metadata_fields:continue
            if isinstance(candidates,dict):candidates=[candidates]
            if len(candidates or [])==1 and (not only_empty or self.metadata_fields[key][0].currentData()=='auto'):
                spec=copy.deepcopy(candidates[0].get('source',candidates[0]));set_combo(self.metadata_fields[key][0],spec.get('kind','auto'));self.populate_source(key,spec)
        self.changed()

    def sources(self,kind):
        result=[]
        for entry in (self.inventory or {}).get('sources',[]):
            spec=entry.get('source',entry)
            if spec.get('kind',entry.get('type'))==kind:result.append(entry)
        return result

    def fill_source(self,combo,kind,prior=None,join=False):
        prior=prior or {};combo.blockSignals(True);combo.clear();combo.addItem('Select a source',None)
        if kind in ('attribute','dataset'):
            for entry in self.sources(kind):
                spec=copy.deepcopy(entry.get('source',entry));combo.addItem(source_label(entry),spec);combo.setItemData(combo.count()-1,str(entry.get('owner_path','')),Qt.ToolTipRole)
        elif kind=='table':
            for column in (self.table_inventory or {}).get('columns',[]):
                sample=next((row.get(column) for row in self.table_inventory.get('samples',[]) if row.get(column) is not None),'')
                combo.addItem(f'{column} · {short(sample)}',{'kind':'table','value':str(column)})
        elif kind=='path_part':
            sample=next((str(row.get('record_path','')) for row in (self.inventory or {}).get('datasets',[]) if row.get('record_path')),'')
            for index,value in enumerate(sample.strip('/').split('/') if sample else []):combo.addItem(f'{index} · {value}',{'kind':'path_part','value':index})
        elif kind=='field':
            for key,label in FIELDS:combo.addItem(label,{'kind':'field','value':key})
        elif kind=='reserved':
            for key,label in RESERVED.items():combo.addItem(label,{'kind':'reserved','value':key})
        matched=False
        for i in range(combo.count()):
            candidate=combo.itemData(i)
            if candidate and all(candidate.get(k)==prior.get(k) for k in ('kind','value','scope')):combo.setCurrentIndex(i);matched=True;break
        if not matched and prior.get('value') not in ('',None):combo.addItem(str(prior['value']),copy.deepcopy(prior));combo.setCurrentIndex(combo.count()-1)
        editable=kind in ('attribute','dataset','constant','path_part')
        combo.setEditable(editable);combo.setEnabled(kind not in ('auto','missing'))
        if editable:
            combo.setInsertPolicy(QComboBox.NoInsert)
            if kind=='constant':combo.setEditText(str(prior.get('value','')))
            combo.lineEdit().setPlaceholderText('Known value' if kind=='constant' else 'Select a field or enter an exact name')
        combo.blockSignals(False)

    def populate_source(self,key,prior=None):self.fill_source(self.metadata_fields[key][1],self.metadata_fields[key][0].currentData(),prior);self.update_source_example(key)
    def metadata_kind_changed(self,key):
        if self._loading:return
        self.metadata_table.setCurrentCell([k for k,_ in FIELDS].index(key),0)
        self.populate_source(key,{'kind':self.metadata_fields[key][0].currentData(),'value':''});self.changed()
    def source_changed(self,key):
        if not self._loading:self.metadata_table.setCurrentCell([k for k,_ in FIELDS].index(key),0)
        self.update_source_example(key);self.changed()

    def combo_spec(self,kind,combo):
        if kind in ('auto','missing'):return {'kind':kind,'value':''}
        data=combo.currentData()
        if data and (not combo.isEditable() or combo.currentText()==combo.itemText(combo.currentIndex())):
            result=copy.deepcopy(data);result['kind']=kind;return result
        value=combo.currentText().strip()
        if combo.currentIndex()==0 and value=='Select a source':value=''
        if kind=='path_part' and value.isdigit():value=int(value)
        return {'kind':kind,'value':value}

    def metadata_spec(self,key):
        kind,source=self.metadata_fields[key];result=self.combo_spec(kind.currentData(),source);result.update(self._metadata_options.get(key,{}));return result
    def update_source_example(self,key):
        if self._loading or not hasattr(self,'metadata_table'):return
        spec=self.metadata_spec(key);sample='Automatic — verify in preview' if spec['kind']=='auto' else 'Unknown' if spec['kind']=='missing' else spec.get('value','')
        for entry in self.sources(spec['kind']):
            candidate=entry.get('source',entry)
            if all(candidate.get(k)==spec.get(k) for k in ('kind','value','scope')):sample=entry.get('sample','');break
        row=[k for k,_ in FIELDS].index(key);self.metadata_table.item(row,3).setText(short(sample));self.metadata_table.item(row,3).setToolTip(str(sample))

    def metadata_row_changed(self,row,*args):
        if row<0:return
        self._active_field=FIELDS[row][0];previous=self._loading;self._loading=True
        try:
            key=self._active_field;options=self._metadata_options.get(key,{})
            self.field_label.setText(FIELDS[row][1]+' — input interpretation; preview shows normalized values.')
            self.field_unit.clear();self.field_unit.addItems(FIELD_UNITS.get(key,['auto']));self.field_unit.setCurrentText(options.get('unit','auto'))
            set_combo(self.time_format,options.get('format','iso'));self.time_unit.setCurrentText(options.get('time_unit','s'));self.time_zone.setCurrentText(options.get('timezone','UTC'))
            date_format=options.get('datetime_format',DATE_FORMATS[0][1]);date_index=self.datetime_format.findData(date_format)
            if date_index>=0:self.datetime_format.setCurrentIndex(date_index)
            else:self.datetime_format.setEditText(date_format)
            self.excel_system.setCurrentText(options.get('excel_date_system',(self.table_inventory or {}).get('excel_date_system','1900')))
            self.show_field_options()
        finally:self._loading=previous

    def show_field_options(self):
        key=self._active_field;form=self.field_options_form;timed=key in TIME_FIELDS;fmt=self.time_format.currentData()
        for widget,visible in [(self.field_unit,key in FIELD_UNITS),(self.time_format,timed),(self.time_unit,timed and fmt=='epoch'),(self.time_zone,timed),(self.datetime_format,timed and fmt=='strptime'),(self.excel_system,timed and fmt=='excel')]:
            widget.setVisible(visible);form.labelForField(widget).setVisible(visible)

    def field_options_changed(self,*args):
        if self._loading or not self._active_field:return
        key=self._active_field;options={}
        if key in FIELD_UNITS and self.field_unit.currentText()!='auto':options['unit']=self.field_unit.currentText()
        if key in TIME_FIELDS:
            fmt=self.time_format.currentData();options.update(format=fmt,timezone=self.time_zone.currentText().strip() or 'UTC')
            if fmt=='epoch':options['time_unit']=self.time_unit.currentText()
            elif fmt=='excel':options.update(time_unit='days',excel_date_system=self.excel_system.currentText())
            elif fmt=='strptime':options['datetime_format']=self.datetime_format.currentData() if self.datetime_format.currentText()==self.datetime_format.itemText(self.datetime_format.currentIndex()) else self.datetime_format.currentText()
        self._metadata_options[key]=options;self.show_field_options();self.changed()

    def table_toggled(self,*args):self.table_panel.setVisible(self.use_table.isChecked());self.changed()
    def table_path_changed(self,*args):
        self.table_inventory=None
        if not self._loading:
            self.table_sheet.blockSignals(True);self.table_sheet.clear();self.table_sheet.blockSignals(False)
        self.changed()
    def table_options_changed(self,*args):
        if not self._loading:
            self.table_inventory=None;self.changed();self.table_note.setText('Table settings changed. Read the columns again before previewing.')
    def browse_table(self):
        path,_=QFileDialog.getOpenFileName(self,'Select metadata table',self.table_path.text(),'Metadata tables (*.xlsx *.xlsm *.csv *.tsv)')
        if path:self.table_path.setText(path);self.inspect_table()
    def sheet_changed(self,*args):
        if not self._loading:self.changed();self.inspect_table()

    def inspect_table(self):
        if not self.table_path.text().strip() or self.busy():return
        path=self.table_path.text().strip();sheet=self.table_sheet.currentData();header=self.table_header.value();delimiter=self.table_delimiter.currentData()
        def inspect():
            from .hdf5_import_v2 import inspect_metadata_table
            return inspect_metadata_table(path,sheet=sheet,header_row=header,delimiter=delimiter)
        self.launch(inspect,self.table_inspected,'Reading metadata table columns and sample rows…')

    def table_inspected(self,inventory):
        self.table_inventory=inventory;self._loading=True
        try:
            self.table_sheet.clear();self.table_sheet.addItem('Select a worksheet' if inventory.get('sheets') else 'Not applicable (CSV / TSV)',None);self.table_sheet.setEnabled(bool(inventory.get('sheets')))
            for sheet in inventory.get('sheets',[]):self.table_sheet.addItem(str(sheet),sheet)
            set_combo(self.table_sheet,inventory.get('selected_sheet'))
            for key in TIME_FIELDS:
                spec=self.metadata_spec(key)
                if spec.get('kind')=='table' and spec.get('format')=='excel':self._metadata_options[key]['excel_date_system']=inventory.get('excel_date_system','1900')
            for key in self.metadata_fields:
                if self.metadata_fields[key][0].currentData()=='table':self.populate_source(key,self.metadata_spec(key))
            for row in range(self.join_table.rowCount()):
                combo=self.join_table.cellWidget(row,0);old=combo.currentData();combo.clear();combo.addItem('Select column','');[combo.addItem(str(x),str(x)) for x in inventory.get('columns',[])];set_combo(combo,old)
        finally:self._loading=False
        if self._active_field:self.metadata_row_changed([k for k,_ in FIELDS].index(self._active_field))
        self.table_note.setText(f"{inventory.get('row_count',0)} rows; {len(inventory.get('columns',[]))} columns. Select exact matching keys. "+short('; '.join(map(str,inventory.get('warnings',[]))),400));self.status.setText('Table columns loaded. Match the table to waveform records, then choose Table column for the metadata it supplies.');self.changed()

    def add_join(self,spec=None):
        spec=spec if isinstance(spec,dict) else {};row=self.join_table.rowCount();self.join_table.insertRow(row)
        column=QComboBox();column.addItem('Select column','');[column.addItem(str(x),str(x)) for x in (self.table_inventory or {}).get('columns',[])]
        if spec.get('column') and column.findData(spec['column'])<0:column.addItem(spec['column'],spec['column'])
        set_combo(column,spec.get('column',''));kind=QComboBox()
        for label,value in [('HDF5 attribute','attribute'),('Scalar HDF5 dataset','dataset'),('Mapped field','field'),('Path level','path_part'),('Source information','reserved')]:kind.addItem(label,value)
        set_combo(kind,spec.get('source',{}).get('kind','attribute'));source=QComboBox();source.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);source.setMinimumContentsLength(12)
        self.join_table.setCellWidget(row,0,column);self.join_table.setCellWidget(row,1,kind);self.join_table.setCellWidget(row,2,source);self.join_table.setRowHeight(row,38)
        self.fill_source(source,kind.currentData(),spec.get('source'))
        column.currentIndexChanged.connect(self.changed);kind.currentIndexChanged.connect(lambda:self.join_kind_changed(kind,source));source.currentTextChanged.connect(self.changed);self.changed()
    def join_kind_changed(self,kind,source):self.fill_source(source,kind.currentData());self.changed()
    def remove_join(self):
        row=self.join_table.currentRow()
        if row>=0:self.join_table.removeRow(row);self.changed()

    def draft_mapping(self):
        if self.layout_mode.currentData()=='native':return {'schema':'signalqc.hdf5_mapping.v2','enabled':False}
        mapping={'schema':'signalqc.hdf5_mapping.v2','enabled':True,'datasets':{k:v.currentText().strip() for k,v in self.dataset_fields.items()},
                 'metadata':{key:self.metadata_spec(key) for key in self.metadata_fields},'components':[x.strip() for x in self.components.text().split(',') if x.strip()],
                 **{key+'_units':widget.currentText() for key,widget in self.unit_fields.items()}}
        if self.sample_axis.currentData() is not None:mapping['sample_axis']=self.sample_axis.currentData()
        if self.use_table.isChecked():
            joins=[]
            for row in range(self.join_table.rowCount()):
                column,kind,source=[self.join_table.cellWidget(row,c) for c in range(3)];joins.append({'column':column.currentData() or '', 'source':self.combo_spec(kind.currentData(),source)})
            mapping['metadata_table']={'path':self.table_path.text().strip(),'sheet':self.table_sheet.currentData(),'join':joins,'header_row':self.table_header.value(),'delimiter':self.table_delimiter.currentData()}
        return mapping

    def load_mapping(self,mapping):
        previous=self._loading;self._loading=True
        try:
            set_combo(self.layout_mode,'native' if mapping.get('enabled') is False else 'mapped')
            self._metadata_options={key:{k:v for k,v in mapping.get('metadata',{}).get(key,{}).items() if k not in ('kind','value','scope')} for key in self.metadata_fields}
            for key,widget in self.dataset_fields.items():widget.setEditText(mapping.get('datasets',{}).get(key,''))
            for key,widget in self.unit_fields.items():widget.setCurrentText(mapping.get(key+'_units','auto'))
            set_combo(self.sample_axis,mapping.get('sample_axis'));self.components.setText(', '.join(mapping.get('components',[])))
            for key,(kind,source) in self.metadata_fields.items():
                spec=mapping.get('metadata',{}).get(key,{'kind':'auto','value':''});set_combo(kind,spec.get('kind','auto'));self.fill_source(source,kind.currentData(),spec)
            table=mapping.get('metadata_table',{});self.use_table.setChecked(bool(table));self.table_panel.setVisible(bool(table));self.table_path.setText(str(table.get('path','')));self.table_sheet.clear()
            if table.get('sheet') is not None:self.table_sheet.addItem(str(table['sheet']),table['sheet'])
            self.table_header.setValue(table.get('header_row',1));set_combo(self.table_delimiter,table.get('delimiter'))
            self.join_table.setRowCount(0)
            for spec in table.get('join',[]):self.add_join(spec)
            self.metadata_table.setCurrentCell(0,0);self.metadata_row_changed(0)
        finally:self._loading=previous
        self.layout_changed()

    def validation_errors(self):
        errors=[];mapping=self.draft_mapping();pattern=mapping.get('datasets',{}).get('acceleration','').lstrip('/')
        if not self.selected_file():errors.append('Choose an HDF5 file.')
        if self.inventory is None:errors.append('Inspect the selected HDF5 file.')
        if not mapping.get('enabled'):return errors
        if not pattern:errors.append('Select the acceleration dataset.')
        if any(len(row.get('shape',[]))==2 and (fnmatch.fnmatchcase(str(row.get('path','')).lstrip('/'),pattern) or pattern.startswith('**/') and fnmatch.fnmatchcase(str(row.get('path','')).lstrip('/'),pattern[3:])) for row in (self.inventory or {}).get('datasets',[])) and 'sample_axis' not in mapping:errors.append('Select the sample direction for the two-dimensional waveform.')
        for key,spec in mapping['metadata'].items():
            if spec['kind'] not in ('auto','missing') and spec.get('value') in ('',None):errors.append('Select a source for '+dict(FIELDS)[key]+'.')
            if spec['kind']=='path_part' and (not isinstance(spec['value'],int) or spec['value']<0):errors.append('Path levels must be zero-based nonnegative integers.')
            if spec['kind']=='table' and not self.use_table.isChecked():errors.append('Enable and match a metadata table for table-sourced fields.')
            if spec.get('format')=='strptime' and not spec.get('datetime_format'):errors.append('Enter the custom timestamp format.')
        if self.use_table.isChecked():
            table=mapping['metadata_table']
            if not table['path'] or self.table_inventory is None:errors.append('Read the selected metadata table.')
            if self.table_inventory and self.table_inventory.get('sheets') and self.table_sheet.currentData() is None:errors.append('Select a worksheet and read its columns.')
            if not table['join']:errors.append('Add at least one exact table match key.')
            for join in table['join']:
                if not join['column'] or join['source'].get('value') in ('',None):errors.append('Complete every table match key.')
        return list(dict.fromkeys(errors))

    def next_step(self):
        if self.tabs.currentIndex()<2:self.tabs.setCurrentIndex(2 if self.layout_mode.currentData()=='native' else self.tabs.currentIndex()+1)
        if self.tabs.currentIndex()==2:self.preview()
    def navigation_changed(self,*args):
        if not hasattr(self,'next_button'):return
        index=self.tabs.currentIndex();self.back_button.setEnabled(index>0 and not self.busy());self.next_button.setVisible(index<2);self.next_button.setText('Continue to metadata' if index==0 and self.layout_mode.currentData()=='mapped' else 'Preview records');self.update_buttons()
    def update_buttons(self):
        if not hasattr(self,'apply_button'):return
        errors=self.validation_errors();self.preview_button.setEnabled(not self.busy() and not errors);self.preview_button.setToolTip('\n'.join(errors))
        ready=self._preview_revision==self._revision and bool(self._preview_results) and all(item.get('traces',0)>0 for item in self._preview_results)
        self.apply_button.setEnabled(not self.busy() and not errors and ready);self.next_button.setEnabled(not self.busy());self.back_button.setEnabled(not self.busy() and self.tabs.currentIndex()>0)
        self.apply_button.setToolTip('\n'.join(errors) if errors else 'Run a current preview of every target file before applying.' if not ready else 'Save the reviewed mapping without changing source files.')

    def preview(self):
        errors=self.validation_errors()
        if errors:self.status.setText('Required for preview: '+' '.join(errors));return
        mapping=self.draft_mapping();paths=self.target_files();revision=self._revision
        def read():
            from .hdf5_import_v2 import preview_mapping
            results=[]
            for path in paths:
                if QThread.currentThread().isInterruptionRequested():break
                try:result=preview_mapping(path,mapping) if mapping.get('enabled') else preview_native(path)
                except Exception as exc:result={'traces':0,'records':[],'issues':[str(exc)]}
                result['source_file']=path;results.append(result)
            return {'revision':revision,'results':results}
        self.launch(read,self.previewed,f'Checking mapped records in {len(paths)} file(s)…')

    def previewed(self,payload):
        if payload['revision']!=self._revision:return
        self._preview_results=payload['results'];self._preview_revision=self._revision;self.file_summary.setRowCount(len(self._preview_results));self._preview_rows=[]
        total_valid=total_blocked=0
        for index,result in enumerate(self._preview_results):
            records=result.get('records',[]);valid=int(result.get('traces',sum(bool(r.get('valid')) for r in records)));blocked=sum(not r.get('valid',False) for r in records);total_valid+=valid;total_blocked+=blocked
            for col,value in enumerate([Path(result['source_file']).name,valid,blocked,'; '.join(map(str,result.get('issues',[]))) or 'No header issues']):
                cell=QTableWidgetItem(short(value,220));cell.setToolTip(str(value));self.file_summary.setItem(index,col,cell)
            for record in records[:100]:self._preview_rows.append(dict(record,_source_file=result['source_file']))
        self.file_summary.setFixedHeight(min(112,40+30*len(self._preview_results)))
        self.preview_table.setRowCount(len(self._preview_rows))
        for row,record in enumerate(self._preview_rows):
            fields=record.get('fields',{});notes=list(record.get('errors',[]));readiness=record.get('stage_readiness',{})
            for stage in self.stages:
                state=readiness.get(stage)
                if isinstance(state,dict) and state.get('missing'):notes.append(stage+': missing '+', '.join(state['missing']))
            shown_start=str(fields.get('start_time','')).replace('.000000000Z','Z').replace('.000000Z','Z')
            rate,from_interval=displayed_rate(fields)
            values=[record.get('dataset','')+(f" [{record['component_index']}]" if record.get('component_index') is not None else ''),'valid' if record.get('valid') else 'blocked',format(rate,'.12g') if rate!='' else '',shown_start,fields.get('event_id',''),fields.get('station',''),fields.get('channel',''),fields.get('magnitude',''),'; '.join(map(str,notes))]
            for col,value in enumerate(values):item=QTableWidgetItem(short(value,110));item.setToolTip(str(value));self.preview_table.setItem(row,col,item)
            if from_interval:self.preview_table.item(row,2).setToolTip(f'{rate:g} Hz, displayed as 1 / the mapped sample interval ({fields.get("dt")} s).')
        self.preview_summary.setText(f'{total_valid} valid record headers; {total_blocked} blocked record headers across {len(self._preview_results)} file(s). First 100 per file shown. Waveform samples and check-specific requirements are validated during processing.')
        self.status.setText('Preview complete. Review the values and warnings before applying.' if all(r.get('traces',0)>0 for r in self._preview_results) else 'At least one target file has no valid mapped records. Correct its mapping or apply to this file only.')
        if self._preview_rows:self.preview_table.setCurrentCell(0,0)
        self.update_buttons()

    def preview_row_changed(self,row,*args):
        if row<0 or row>=len(getattr(self,'_preview_rows',[])):return
        record=self._preview_rows[row];fields=record.get('fields',{});provenance=record.get('provenance',{})
        details=[Path(record['_source_file']).name+' · '+str(record.get('dataset',''))]
        rate,from_interval=displayed_rate(fields)
        if from_interval:details.append(f'Displayed sample rate: {rate:g} Hz (1 / the mapped sample interval, {fields.get("dt")} s). The original mapped fields are retained.')
        labels=dict(FIELDS)
        for key in dict.fromkeys([key for key,_ in FIELDS]+list(fields)):
            if key not in fields:continue
            source=provenance.get(key,{})
            value=fields[key];shown='(blank)' if value is None or isinstance(value,str) and not value else short(value,2000)
            details.append('\n'+labels.get(key,key)+': '+shown)
            if source:
                details.append('Source: '+source.get('kind','auto')+(' · '+str(source['value']) if source.get('value') else '')+(' · '+str(source['scope']) if source.get('scope') else ''))
                if source.get('sheet') is not None or source.get('row') is not None:details.append('Worksheet: '+str(source.get('sheet',''))+'; row: '+str(source.get('row','')))
                if source.get('join_key') is not None:details.append('Exact match key: '+short(source['join_key'],2000))
                if 'raw_value' in source:details.append('Stored value: '+short(source['raw_value'],2000))
                if source.get('conversion'):details.append('Conversion: '+', '.join(f'{name} = {value}' for name,value in source['conversion'].items()))
        if record.get('errors'):details.append('; '.join(map(str,record['errors'])))
        for stage in self.stages:
            state=record.get('stage_readiness',{}).get(stage,{})
            if state.get('missing'):details.append(stage+': missing '+', '.join(state['missing']))
            if state.get('note'):details.append(stage+': '+state['note'])
        self.preview_detail.setPlainText('\n'.join(details))

    def apply_mapping(self):
        if not self.apply_button.isEnabled():return
        self._mapping=self.draft_mapping();self.accept()
    def mapping(self):return copy.deepcopy(self._mapping)
    def reject(self):
        if self.busy():self._closing=True;self.worker.requestInterruption();self.status.setText('Finishing the current file read before closing. Source files are unchanged.');return
        super().reject()
    def closeEvent(self,event):
        if self.busy():self.reject();event.ignore()
        else:super().closeEvent(event)
