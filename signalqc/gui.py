from __future__ import annotations
import json
import copy
import os
from pathlib import Path
import sys
import tempfile
import traceback

from PySide6.QtCore import Qt,QThread,Signal,QTimer,QUrl,QAbstractTableModel,QSortFilterProxyModel
from PySide6.QtGui import QColor,QDesktopServices,QFont,QFontDatabase,QPixmap
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
    QLabel,QPushButton,QLineEdit,QListWidget,QStackedWidget,QScrollArea,QFrame,QGroupBox,QFormLayout,
    QComboBox,QCheckBox,QRadioButton,QButtonGroup,QSpinBox,QDoubleSpinBox,QFileDialog,QPlainTextEdit,QProgressBar,QTableView,QHeaderView,
    QSplitter,QTabWidget,QMessageBox,QDialog,QDialogButtonBox,QTableWidget,QTableWidgetItem,QTextBrowser)
import numpy as np
import pandas as pd
import pyqtgraph as pg

from .project import APP_ROOT,SOURCE_ROOT,Project,defaults,CHECKS,atomic_json
from .data import inspect_inputs,read_table,hdf5_records,read_waveform,walk_files
from .pipeline import Pipeline,load_engine
from .user_paths import user_state_dir, default_projects_dir, console_python
from .workflow_v2 import relevant_input_config, validate_workflow

from .theme_v1 import STYLE as BASE_STYLE, apply_theme, configure_accessibility
from .accessible_controls_v1 import TaskChoice, TASK_CHOICE_STYLE

STYLE = BASE_STYLE + TASK_CHOICE_STYLE
OVERVIEW_PAGE, TASK_PAGE, INPUTS_PAGE, OPTIONS_PAGE, RUN_PAGE, RESULTS_PAGE = range(6)

class FrequencySpinBox(QDoubleSpinBox):
    def textFromValue(self,value):
        text=self.locale().toString(value,'f',self.decimals())
        return text.rstrip('0').rstrip(self.locale().decimalPoint()) if self.locale().decimalPoint() in text else text

class Work(QThread):
    progress=Signal(object); result=Signal(object); error=Signal(str)
    def __init__(self,function): super().__init__(); self.function=function
    def run(self):
        try: self.result.emit(self.function(self.progress.emit))
        except Exception: self.error.emit(traceback.format_exc())

class FrameModel(QAbstractTableModel):
    def __init__(self,frame): super().__init__(); self.frame=frame
    def rowCount(self,parent=None): return len(self.frame)
    def columnCount(self,parent=None): return len(self.frame.columns)
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid(): return None
        value=self.frame.iloc[index.row(),index.column()]
        if role==Qt.DisplayRole:
            if isinstance(value,(dict,list)): return json.dumps(value,default=str)
            try:
                if pd.isna(value): return ''
            except Exception: pass
            return f'{value:.7g}' if isinstance(value,float) else str(value)
        if role==Qt.BackgroundRole:
            c=str(self.frame.columns[index.column()]).lower()
            if c in ('sc_status','status','outcome','pipeline_outcome','stage2_status'):
                if str(value) in ('fail','failed','rejected','error'): return QColor('#fbe4e2')
                if str(value) in ('pass','completed','accepted'): return QColor('#d9f1e8')
        return None
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role!=Qt.DisplayRole: return None
        return str(self.frame.columns[section]) if orientation==Qt.Horizontal else str(section+1)

class Paths(QWidget):
    changed=Signal()
    def __init__(self,label,filters='All files (*)'):
        super().__init__(); self.filters=filters
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.list=QListWidget(); self.list.setMinimumHeight(92); self.list.setMaximumHeight(125)
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.list)
        row=QHBoxLayout()
        for text,func in [('Add files',self.files),('Add folder',self.folder),('Remove',self.remove)]:
            b=QPushButton(text); b.clicked.connect(func); row.addWidget(b)
        row.addStretch(); layout.addLayout(row)
        self.setToolTip(label); self.setAccessibleName(label)
    def files(self):
        values,_=QFileDialog.getOpenFileNames(self,'Select files',str(Path.home()),self.filters)
        self.add(values)
    def folder(self):
        value=QFileDialog.getExistingDirectory(self,'Select folder',str(Path.home()))
        if value: self.add([value])
    def add(self,values):
        existing=set(self.values())
        for v in values:
            if v not in existing: self.list.addItem(v); existing.add(v)
        self.changed.emit()
    def remove(self):
        for item in self.list.selectedItems(): self.list.takeItem(self.list.row(item))
        self.changed.emit()
    def values(self): return [self.list.item(i).text() for i in range(self.list.count())]
    def set_values(self,values): self.list.clear(); self.list.addItems(values)

class Window(QMainWindow):
    def __init__(self,project=None,last_project=None):
        apply_theme(QApplication.instance())
        super().__init__(); self.setWindowTitle('SignalQC · Automated Signal Processing & Quality Control')
        self.resize(1280,800); self.setMinimumSize(1040,650)
        self.setStyleSheet(STYLE)
        self.project=None; self.worker=None; self.pipeline=None; self.preflight=None; self.config=defaults()
        self.example_ready=False; self.run_after_scan=False; self.example_folder=None
        self.example_window=None
        self.mapping={}; self.result_records=[]; self.display_records=[]; self.current_table=None
        shell=QWidget(); self.setCentralWidget(shell); row=QHBoxLayout(shell); row.setContentsMargins(0,0,0,0); row.setSpacing(0)
        sidebar=QWidget(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(212); left=QVBoxLayout(sidebar); left.setContentsMargins(8,24,8,16)
        brand=QLabel('  SignalQC'); brand.setStyleSheet('font-size:23pt;font-weight:700;color:white'); left.addWidget(brand)
        tagline=QLabel('    Signal processing and QC'); tagline.setStyleSheet('font-size:9pt;color:#aec4d6'); left.addWidget(tagline); left.addSpacing(24)
        self.nav=QListWidget(); self.nav.setObjectName('nav'); self.nav.setAccessibleName('Application sections'); self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.nav.addItems(['Overview','1   Select task','2   Input data','3   Processing options','4   Review and run','Results','Reader','User guide','Runtime settings']); left.addWidget(self.nav)
        self.project_label=QLabel('No project open'); self.project_label.setWordWrap(True); self.project_label.setMaximumWidth(190); self.project_label.setStyleSheet('font-size:10pt;color:#c5d9e7;padding:10px;'); left.addWidget(self.project_label)
        self.open_button=QPushButton('Open project'); self.open_button.clicked.connect(self.open_project); left.addWidget(self.open_button)
        self.new_button=QPushButton('New project'); self.new_button.clicked.connect(self.new_project); left.addWidget(self.new_button)
        version=QLabel('  Public edition'); version.setStyleSheet('font-size:8pt;color:#89b5c1;padding-top:12px;'); left.addWidget(version)
        credit=QLabel('Developed by Mohamed T. Hussein & Tadahiro Kishida'); credit.setWordWrap(True)
        credit.setStyleSheet('font-size:9pt;color:#d9e6ef;padding:6px 5px;'); left.addWidget(credit)
        row.addWidget(sidebar); self.pages=QStackedWidget(); row.addWidget(self.pages,1)
        self.build_overview(last_project); self.build_task(); self.build_inputs(); self.build_workflow(); self.build_activity(); self.build_results(); self.build_reader(); self.build_help(); self.build_runtime()
        configure_accessibility(self)
        self.nav.currentRowChanged.connect(self.navigate)
        if project: self.load_project(project)
        else: self.sample_inputs()
        self.nav.setCurrentRow(OVERVIEW_PAGE)
        self.overview_run.setFocus()

    def page(self,title,subtitle):
        body=QWidget(); body.setObjectName('pageBody'); layout=QVBoxLayout(body); layout.setContentsMargins(28,22,28,22); layout.setSpacing(12)
        eyebrow=QLabel('SIGNALQC'); eyebrow.setObjectName('eyebrow'); layout.addWidget(eyebrow)
        h=QLabel(title); h.setObjectName('title'); layout.addWidget(h)
        p=QLabel(subtitle); p.setObjectName('subtitle'); p.setWordWrap(True); layout.addWidget(p)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setAccessibleName(title+' page'); scroll.setWidget(body); self.pages.addWidget(scroll)
        return layout

    def file_field(self,form,label,key,filter='All files (*)'):
        wrap=QWidget(); line=QHBoxLayout(wrap); line.setContentsMargins(0,0,0,0)
        edit=QLineEdit(); edit.setPlaceholderText('Select a file…'); setattr(self,key,edit); line.addWidget(edit,1)
        b=QPushButton('Browse'); b.clicked.connect(lambda: self.pick_file(edit,filter)); line.addWidget(b); form.addRow(label,wrap)
        edit._browse_button=b
        edit.textChanged.connect(self.invalidate)
        return edit

    def pick_file(self,edit,filter):
        value,_=QFileDialog.getOpenFileName(self,'Select file',str(Path.home()),filter)
        if value: edit.setText(value)

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        if index==RUN_PAGE: self.update_review()

    def next_step(self,page,index,label):
        row=QHBoxLayout(); row.addStretch()
        button=QPushButton(label); button.setObjectName('primary')
        button.clicked.connect(lambda:self.nav.setCurrentRow(index)); row.addWidget(button)
        page.addLayout(row)
        return button

    def build_overview(self,last_project):
        page=self.page('Overview','Try SignalQC with the included recordings, or open a project to process your own data.')
        box=QGroupBox('Included example'); content=QVBoxLayout(box)
        description=QLabel('Process 18 waveform components from three stations through screening, filtering and sanity checks. The inputs and settings are included. Results are saved in a new project folder.')
        description.setWordWrap(True); content.addWidget(description)
        self.overview_run=QPushButton('Run example'); self.overview_run.setObjectName('primary')
        self.overview_run.clicked.connect(self.run_overview_example); content.addWidget(self.overview_run,alignment=Qt.AlignLeft)
        page.addWidget(box)
        own=QPushButton('Use my own data')
        own.clicked.connect(lambda: self.use_own_data() if self.example_ready else self.nav.setCurrentRow(TASK_PAGE))
        page.addWidget(own,alignment=Qt.AlignLeft)
        if last_project:
            reopen=QPushButton('Reopen last project'); reopen.setToolTip(str(last_project))
            reopen.clicked.connect(lambda: self.reopen_from_overview(last_project))
            page.addWidget(reopen,alignment=Qt.AlignLeft)
        page.addStretch()

    def reopen_from_overview(self,folder):
        if self.busy(): return
        if not (Path(folder)/'project.json').is_file():
            self.error('The previous project is no longer at this location. Use Open project to locate it.')
            return
        self.load_project(folder)

    def run_overview_example(self):
        if self.busy(): return
        if self.project and self.project.folder==self.example_folder:
            self.sample_inputs()
        if self.example_ready:
            self.start_run()
            return
        # Keep the user's current project and any unsaved inputs in their own window.
        if self.example_window is None or not self.example_window.isVisible():
            self.example_window=Window()
            if not self.example_window.example_ready: return
        self.example_window.showNormal(); self.example_window.raise_(); self.example_window.activateWindow()
        self.example_window.run_overview_example()

    def build_task(self):
        page=self.page('Select a task','Choose the starting point for this project. The following steps show only the inputs and options required for your selection.')
        self.task_buttons={}; self.task_cards={}; self.task_group=QButtonGroup(self)
        tasks=[('process','Process existing HDF5 files','Run accept/reject, filtering and sanity checks on prepared waveforms.'),
               ('prepare','Create HDF5 files','Convert waveform recordings to HDF5, with optional instrument response removal.'),
               ('prepare_process','Create HDF5 files and process','Prepare recordings, then run the selected processing stages.')]
        cards=QVBoxLayout(); cards.setSpacing(9)
        for key,title,description in tasks:
            button=TaskChoice(title,description)
            self.task_group.addButton(button); self.task_buttons[key]=button; self.task_cards[key]=button
            button.toggled.connect(lambda checked,k=key:self.select_task(k) if checked else None)
            cards.addWidget(button)
        page.addLayout(cards)
        self.stage_panel=QGroupBox('Select processing stages'); stages=QVBoxLayout(self.stage_panel); self.stage_boxes={}
        self.stage_boxes['ingestion']=QCheckBox(); self.stage_boxes['ingestion'].hide()
        for key,title,description in [
            ('acceptreject','Stage 1   Accept / reject','Evaluate waveform quality using the original acceptance models and scores.'),
            ('filtering','Stage 2   Filtering','Select corner frequencies, trim and baseline-correct; save acceleration, velocity and displacement.'),
            ('sanity','Stage 3   Sanity checks','Evaluate the selected checks and record individual outcomes.')]:
            cb=QCheckBox(title); cb.setChecked(True); cb.setStyleSheet('font-weight:600;'); self.stage_boxes[key]=cb; stages.addWidget(cb)
            text=QLabel(description); text.setWordWrap(True); text.setStyleSheet('color:#485b70;margin-left:27px;'); stages.addWidget(text)
            cb.stateChanged.connect(self.stage_selection_changed)
        self.stage_hint=QLabel(); self.stage_hint.setWordWrap(True); self.stage_hint.setStyleSheet('color:#40576e;padding-top:8px;'); stages.addWidget(self.stage_hint); page.addWidget(self.stage_panel)
        saved=QLabel('To continue a previous run, open its saved project. To inspect existing results without processing, select Results in the sidebar.')
        saved.setWordWrap(True); saved.setStyleSheet('color:#485b70;'); page.addWidget(saved)
        page.addStretch(); self.next_step(page,INPUTS_PAGE,'Continue to input data')

    def task_key(self):
        return next((key for key,button in self.task_buttons.items() if button.isChecked()),'process')

    def select_task(self,key):
        if not hasattr(self,'stage_boxes'): return
        self.stage_boxes['ingestion'].setChecked(key in ('prepare','prepare_process'))
        self.stage_panel.setVisible(key!='prepare')
        self.invalidate(); self.update_context()

    def stage_selection_changed(self,*args):
        self.invalidate(); self.update_context()

    def build_inputs(self):
        page=self.page('Input data','Select the source files and any metadata needed for the chosen task. Source recordings are preserved.')
        row=QHBoxLayout(); self.name=QLineEdit('Untitled project'); self.name.setPlaceholderText('Project name'); self.name.setAccessibleName('Project name'); row.addWidget(self.name,1)
        self.name.textChanged.connect(self.invalidate)
        save=QPushButton('Save project'); save.clicked.connect(self.save); row.addWidget(save); page.addLayout(row)
        self.input_context=QLabel(); self.input_context.setWordWrap(True); self.input_context.setStyleSheet('color:#40576e;'); page.addWidget(self.input_context)
        self.layout_options=QCheckBox('My HDF5 files use a different structure (review data mapping)')
        self.layout_options.toggled.connect(self.stage_selection_changed)
        page.addWidget(self.layout_options)
        self.input_boxes={}
        for title,key,filters in [('Raw waveform files','raw_paths','Waveforms (*.mseed *.seed *.miniseed *.output *.sac *.segy *.sgy);;All files (*)'),
                                  ('Existing HDF5 files','hdf5_paths','HDF5 (*.h5 *.hdf5);;All files (*)'),
                                  ('Instrument response files','response_paths','Responses (*.resp *.dataless *.xml *.stationxml);;All files (*)')]:
            box=QGroupBox(title); lay=QVBoxLayout(box); picker=Paths(title,filters); setattr(self,key,picker); picker.changed.connect(self.invalidate); lay.addWidget(picker); page.addWidget(box); self.input_boxes[key]=box
        mapping_row=QHBoxLayout()
        self.hdf5_mapping_button=QPushButton('Review data mapping'); self.hdf5_mapping_button.clicked.connect(self.edit_hdf5_mapping); mapping_row.addWidget(self.hdf5_mapping_button)
        self.hdf5_mapping_status=QLabel('Standard SignalQC layout'); self.hdf5_mapping_status.setWordWrap(True); self.hdf5_mapping_status.setStyleSheet('color:#485b70;'); mapping_row.addWidget(self.hdf5_mapping_status,1)
        self.input_boxes['hdf5_paths'].layout().addLayout(mapping_row)
        self.hdf5_paths.changed.connect(self.update_context)
        self.include_existing=QCheckBox('Also process existing HDF5 files in this run'); self.include_existing.stateChanged.connect(self.stage_selection_changed); page.insertWidget(4,self.include_existing)
        self.catalog_box=QGroupBox('Event and station metadata'); form=QFormLayout(self.catalog_box)
        self.file_field(form,'Event catalog','event_catalog','Tables (*.xlsx *.csv *.tsv *.xls);;All files (*)')
        self.file_field(form,'Station catalog','station_catalog','Tables (*.xlsx *.csv *.tsv *.xls);;All files (*)')
        self.catalog_form=form
        self.mapping_buttons={}
        for title,kind in [('Map event columns','event'),('Map station columns','station')]:
            b=QPushButton(title); b.clicked.connect(lambda _,k=kind:self.map_columns(k)); form.addRow('',b); self.mapping_buttons[kind]=b
        self.catalog_note=QLabel('A station catalog is optional when coordinates and responses are supplied by StationXML or dataless SEED.'); self.catalog_note.setWordWrap(True); form.addRow(self.catalog_note); page.addWidget(self.catalog_box)
        self.previous_box=QGroupBox('Processing metadata'); ef=QFormLayout(self.previous_box); self.previous_form=ef
        self.file_field(ef,'Stage 1 results','stage1_metadata','Tables (*.csv *.xlsx);;All files (*)')
        self.file_field(ef,'Stage 2 results','filtering_metadata','Tables (*.csv *.xlsx);;All files (*)')
        note=QLabel('Select the matching consolidated results workbook or a compatible table from the earlier stage. Record identifiers must match the selected HDF5 files.'); note.setWordWrap(True); ef.addRow(note); page.addWidget(self.previous_box)
        self.sample_button=QPushButton('Use included sample inputs'); self.sample_button.clicked.connect(self.sample_inputs)
        page.addWidget(self.sample_button,alignment=Qt.AlignLeft)
        page.addStretch(); self.inputs_next=self.next_step(page,OPTIONS_PAGE,'Continue to processing options')
        self.inputs_next.clicked.disconnect();self.inputs_next.clicked.connect(self.continue_from_inputs)

    def continue_from_inputs(self):
        if not self.layout_options.isChecked():
            self.nav.setCurrentRow(OPTIONS_PAGE); return
        from .hdf5_profiles_v1 import mapping_files,mapping_for_source
        if self.task_key()=='process' or self.task_key()=='prepare_process' and self.include_existing.isChecked():
            files=mapping_files(self.hdf5_paths.values(),self.project.folder if self.project else None)
            if files and any(not mapping_for_source(self.config,path) for path in files):
                if not self.edit_hdf5_mapping():return
        self.nav.setCurrentRow(OPTIONS_PAGE)

    def edit_hdf5_mapping(self):
        if self.busy(): return False
        from .hdf5_mapping_dialog_v4 import Hdf5MappingDialog
        from .hdf5_profiles_v1 import mapping_files,mapping_for_source,set_source_mapping
        files=mapping_files(self.hdf5_paths.values(),self.project.folder if self.project else None)
        if not files and self.preflight: files=self.preflight.get('hdf5_files',[])
        mappings={str(path):mapping_for_source(self.config,path) for path in files}
        # Start with an unreviewed file when others already have explicit mappings.
        files=sorted(files,key=lambda path:bool(mappings[str(path)]))
        dialog=Hdf5MappingDialog(files,mappings.get(str(files[0]),{}) if files else {},self,mappings=mappings,stages=self.collect()['stages'])
        if dialog.exec():
            targets=dialog.target_files();self.config=set_source_mapping(self.config,targets,dialog.mapping())
            additional=[path for path in targets if path not in files]
            if additional:self.hdf5_paths.add(additional)
            self.invalidate(); self.update_context()
            return True
        return False

    def build_workflow(self):
        page=self.page('Processing options','Choose the required calculation settings, output preferences and processing resources.')
        self.generation_box=QGroupBox('HDF5 preparation'); form=QFormLayout(self.generation_box)
        self.grouping=QComboBox(); self.grouping.addItems(['monthly','yearly','daily','single']); form.addRow('Group output files by',self.grouping)
        self.remove_response=QCheckBox('Remove instrument response to obtain acceleration'); self.remove_response.setChecked(True); form.addRow(self.remove_response)
        self.input_units=QComboBox(); self.input_units.addItems(['counts','m/s^2','cm/s^2','g']); form.addRow('Input amplitude units',self.input_units)
        self.scan_all=QCheckBox('Attempt decoding files with unrecognized extensions'); form.addRow(self.scan_all)
        note=QLabel('All recordings within each input file are examined. Response removal requires a valid response for each trace and recording epoch.'); note.setWordWrap(True); form.addRow(note); page.addWidget(self.generation_box)
        self.build_method_controls(page)
        self.processing_box=QGroupBox('Processing and output'); form=QFormLayout(self.processing_box)
        self.suffix=QLineEdit('processed_2'); form.addRow('Processed HDF5 suffix',self.suffix)
        self.figures=QCheckBox('Save diagnostic figures for each trace'); self.figures.setChecked(True); form.addRow(self.figures)
        self.output_note=QLabel('One consolidated Excel workbook per HDF5 output contains the available results from all selected stages, including acceptance scores and corner frequencies from each method.'); self.output_note.setWordWrap(True); form.addRow(self.output_note); page.addWidget(self.processing_box)
        self.sanity_box=QGroupBox('Sanity checks'); s=QGridLayout(self.sanity_box); self.checks={}
        for i,(key,name) in enumerate(CHECKS.items()):
            cb=QCheckBox(f'{key}.  {name}'+(' (default: off)' if key=='5' else '')); cb.setChecked(key!='5'); self.checks[key]=cb; s.addWidget(cb,i//2,i%2)
        bitsrow=QHBoxLayout(); bitsrow.addWidget(QLabel('Fallback ADC resolution')); self.bits=QSpinBox(); self.bits.setRange(1,64); self.bits.setValue(24); self.bits.setSuffix(' bits'); bitsrow.addWidget(self.bits); bitsrow.addStretch(); s.addLayout(bitsrow,4,0,1,2)
        note=QLabel('Disabled, unavailable and failed checks are reported separately. Check 5 is off by default; enable it when required by your processing protocol.'); note.setWordWrap(True); note.setStyleSheet('color:#485b70'); s.addWidget(note,5,0,1,2); page.addWidget(self.sanity_box)
        execution=QGroupBox('Execution'); form=QFormLayout(execution)
        self.workers=QSpinBox(); self.workers.setRange(1,min(32,os.cpu_count() or 1)); self.workers.setMaximumWidth(150); self.workers.setValue(min(2,os.cpu_count() or 1)); form.addRow('Parallel waveform workers',self.workers)
        self.resume=QCheckBox('Resume matching completed work and saved checkpoints'); self.resume.setChecked(True)
        note=QLabel('Workers process waveforms or component groups in parallel, including waveforms in the same HDF5 file. Accept/reject prepares event information first, then processes independent three-component groups in parallel. Start with 2–4 and increase gradually while the computer remains responsive. More workers use more memory; HDF5 updates are coordinated to protect the files.'); note.setWordWrap(True); note.setStyleSheet('color:#485b70'); form.addRow(note); form.addRow(self.resume); page.addWidget(execution)
        for widget in [self.grouping,self.input_units]: widget.currentTextChanged.connect(self.invalidate)
        self.input_units.currentTextChanged.connect(lambda value:self.remove_response.setChecked(False) if value!='counts' else None)
        for widget in [self.remove_response,self.figures,self.resume,self.scan_all,*self.checks.values()]: widget.stateChanged.connect(self.stage_selection_changed)
        for widget in [self.bits,self.workers]: widget.valueChanged.connect(self.invalidate)
        self.suffix.textChanged.connect(self.invalidate)
        page.addStretch(); self.next_step(page,RUN_PAGE,'Continue to review')

    def select_workbook_sheet(self,name):
        path=getattr(self,'selected_workbook',None)
        if path and name:
            try:self.set_table(pd.read_excel(path,sheet_name=name,keep_default_na=False))
            except Exception as exc:self.error(str(exc))

    def build_method_controls(self,page):
        from .method_settings_v1 import (ACCEPTREJECT_METHODS,CORNER_METHODS,
            DEFAULT_ACCEPTREJECT_METHODS,DEFAULT_CORNER_METHODS,
            DEFAULT_ACCEPTREJECT_REQUIRED_VOTES,DEFAULT_FILTERING_HP_FALLBACK_HZ,
            DEFAULT_FILTERING_LP_MIN_HZ)
        self.acceptance_methods_box=QGroupBox('Accept / reject methods'); form=QVBoxLayout(self.acceptance_methods_box)
        note=QLabel('Default: all eight methods, five-vote setting (original evaluation rules).'); note.setWordWrap(True); form.addWidget(note)
        grid=QGridLayout(); self.acceptance_method_boxes={}
        for i,(key,label) in enumerate(ACCEPTREJECT_METHODS.items()):
            cb=QCheckBox(f'{key}.  {label}'); cb.setChecked(key in DEFAULT_ACCEPTREJECT_METHODS)
            self.acceptance_method_boxes[key]=cb; grid.addWidget(cb,i//2,i%2)
            cb.toggled.connect(self.methods_changed)
        form.addLayout(grid)
        row=QHBoxLayout(); row.addWidget(QLabel('Accepting votes required'))
        self.required_votes=QSpinBox(); self.required_votes.setRange(1,len(DEFAULT_ACCEPTREJECT_METHODS)); self.required_votes.setValue(DEFAULT_ACCEPTREJECT_REQUIRED_VOTES); self.required_votes.setMaximumWidth(120)
        row.addWidget(self.required_votes); self.enabled_vote_count=QLabel(); row.addWidget(self.enabled_vote_count); row.addStretch(); form.addLayout(row)
        self.vote_adjustment=QLabel(); self.vote_adjustment.setWordWrap(True); self.vote_adjustment.setStyleSheet('color:#9a5b00'); form.addWidget(self.vote_adjustment)
        note=QLabel('The vote requirement applies to each component; existing duration, resolution, trigger, coherence and three-component checks still apply. Shared STA/LTA arrival preparation runs even when its acceptance vote is disabled. Processing may stop once the decision is determined; unevaluated scores are reported separately.'); note.setWordWrap(True); note.setStyleSheet('color:#485b70'); form.addWidget(note)
        self.restore_acceptance_button=QPushButton('Restore acceptance defaults'); self.restore_acceptance_button.clicked.connect(self.restore_acceptance_defaults); form.addWidget(self.restore_acceptance_button,alignment=Qt.AlignLeft)
        self.required_votes.valueChanged.connect(self.invalidate); page.addWidget(self.acceptance_methods_box)
        self.filter_methods_box=QGroupBox('Filtering methods and fallback corners'); form=QVBoxLayout(self.filter_methods_box)
        note=QLabel('Default: all five corner-frequency methods enabled.'); note.setWordWrap(True); form.addWidget(note)
        grid=QGridLayout(); self.filter_method_boxes={}
        for i,(key,label) in enumerate(CORNER_METHODS.items()):
            cb=QCheckBox(str(label)); cb.setChecked(key in DEFAULT_CORNER_METHODS)
            self.filter_method_boxes[key]=cb; grid.addWidget(cb,i//2,i%2); cb.toggled.connect(self.methods_changed)
        form.addLayout(grid)
        self.filter_method_status=QLabel(); self.filter_method_status.setWordWrap(True); self.filter_method_status.setStyleSheet('color:#485b70'); form.addWidget(self.filter_method_status)
        values=QFormLayout(); self.hp_fallback=FrequencySpinBox(); self.lp_minimum=FrequencySpinBox()
        for widget,value in [(self.hp_fallback,DEFAULT_FILTERING_HP_FALLBACK_HZ),(self.lp_minimum,DEFAULT_FILTERING_LP_MIN_HZ)]:
            widget.setDecimals(9); widget.setRange(0.000000001,1000000); widget.setSingleStep(.1); widget.setMaximumWidth(190); widget.setValue(value); widget.valueChanged.connect(self.invalidate)
        values.addRow('High-pass fallback (Hz)',self.hp_fallback); values.addRow('Low-pass minimum / fallback (Hz)',self.lp_minimum); form.addLayout(values)
        note=QLabel('The high-pass fallback is used only when no valid high-pass corner is picked. The low-pass value is the minimum for a valid combined corner and the fallback when none is picked. Defaults: 0.1 Hz and 8 Hz.'); note.setWordWrap(True); note.setStyleSheet('color:#485b70'); form.addWidget(note)
        self.restore_filtering_button=QPushButton('Restore filtering defaults'); self.restore_filtering_button.clicked.connect(self.restore_filtering_defaults); form.addWidget(self.restore_filtering_button,alignment=Qt.AlignLeft)
        page.addWidget(self.filter_methods_box); self.refresh_method_controls()

    def refresh_method_controls(self,show_adjustment=False):
        if not hasattr(self,'filter_method_status'): return
        count=sum(b.isChecked() for b in self.acceptance_method_boxes.values())
        before=self.required_votes.value(); self.required_votes.setMaximum(max(1,count)); self.required_votes.setEnabled(count>0)
        self.enabled_vote_count.setText(f'out of {count} enabled methods')
        adjustment='Select at least one acceptance method.' if count==0 else (
            f'Required votes adjusted to {self.required_votes.value()} because {count} methods are enabled.' if show_adjustment and before>count else '')
        self.vote_adjustment.setText(adjustment); self.vote_adjustment.setVisible(bool(adjustment))
        enabled=[k for k,b in self.filter_method_boxes.items() if b.isChecked()]
        message='Select at least one corner-frequency method.' if not enabled else (
            f'{len(enabled)} method'+('s' if len(enabled)!=1 else '')+' enabled. '+('Guo runs in Python; no MATLAB license is required.' if 'guo' in enabled else 'The selected corner-frequency methods use the included R and Python runtimes.'))
        if enabled==['hk']:message+=' Hussein/Kishida alone does not supply mean SNR; filtering can run, but the existing SNR quality check will be unavailable.'
        self.filter_method_status.setText(message)

    def methods_changed(self,*args):
        if getattr(self,'_applying_config',False) or getattr(self,'_changing_methods',False): return
        self.refresh_method_controls(show_adjustment=True); self.invalidate(); self.update_context()

    def restore_acceptance_defaults(self):
        from .method_settings_v1 import DEFAULT_ACCEPTREJECT_METHODS,DEFAULT_ACCEPTREJECT_REQUIRED_VOTES
        self._changing_methods=True
        try:
            for key,cb in self.acceptance_method_boxes.items():cb.setChecked(key in DEFAULT_ACCEPTREJECT_METHODS)
            self.required_votes.setMaximum(len(DEFAULT_ACCEPTREJECT_METHODS)); self.required_votes.setValue(DEFAULT_ACCEPTREJECT_REQUIRED_VOTES)
        finally:self._changing_methods=False
        self.methods_changed()

    def restore_filtering_defaults(self):
        from .method_settings_v1 import DEFAULT_CORNER_METHODS,DEFAULT_FILTERING_HP_FALLBACK_HZ,DEFAULT_FILTERING_LP_MIN_HZ
        self._changing_methods=True
        try:
            for key,cb in self.filter_method_boxes.items():cb.setChecked(key in DEFAULT_CORNER_METHODS)
            self.hp_fallback.setValue(DEFAULT_FILTERING_HP_FALLBACK_HZ); self.lp_minimum.setValue(DEFAULT_FILTERING_LP_MIN_HZ)
        finally:self._changing_methods=False
        self.methods_changed()

    def form_row_visible(self,form,edit,visible):
        field=edit.parentWidget()
        label=form.labelForField(field)
        field.setVisible(visible)
        if label: label.setVisible(visible)

    def update_context(self):
        if not hasattr(self,'runtime_info') or getattr(self,'_applying_config',False): return
        task=self.task_key(); preparation=task in ('prepare','prepare_process'); processing=task!='prepare'
        selected={k for k,b in self.stage_boxes.items() if b.isChecked() and k!='ingestion'} if processing else set()
        self.stage_panel.setVisible(processing)
        self.sample_button.setVisible((APP_ROOT/'test'/'sample_settings.json').is_file())
        self.input_boxes['raw_paths'].setVisible(preparation)
        self.input_boxes['response_paths'].setVisible(preparation and self.remove_response.isChecked())
        self.include_existing.setVisible(task=='prepare_process')
        self.input_boxes['hdf5_paths'].setVisible(task=='process' or task=='prepare_process' and self.include_existing.isChecked())
        self.generation_box.setVisible(preparation); self.processing_box.setVisible(processing)
        self.sanity_box.setVisible('sanity' in selected)
        self.acceptance_methods_box.setVisible('acceptreject' in selected)
        self.filter_methods_box.setVisible('filtering' in selected)
        self.figures.setVisible(bool(selected & {'acceptreject','filtering'}))
        self.catalog_box.setVisible(preparation or 'sanity' in selected and self.checks['6'].isChecked())
        self.form_row_visible(self.catalog_form,self.event_catalog,preparation)
        self.mapping_buttons['event'].setVisible(preparation)
        self.catalog_box.setTitle('Event and station metadata' if preparation else 'Station metadata (optional)')
        self.catalog_note.setText('A station catalog is optional when coordinates and responses are supplied by StationXML or dataless SEED.' if preparation else 'For the clipping check, a station catalog can supply ADC resolution by network, station, channel and epoch. Otherwise the configured fallback is used.')
        need_stage1='filtering' in selected and 'acceptreject' not in selected
        need_stage2='sanity' in selected and 'filtering' not in selected
        self.previous_box.setVisible(need_stage1 or need_stage2)
        self.form_row_visible(self.previous_form,self.stage1_metadata,need_stage1)
        self.form_row_visible(self.previous_form,self.filtering_metadata,need_stage2)
        hints=[]
        if need_stage1: hints.append('Stage 2 requires matching Stage 1 results.')
        if need_stage2: hints.append('Stage 3 requires matching Stage 2 results.')
        if processing and not selected: hints.append('Select at least one processing stage.')
        self.stage_hint.setText(' '.join(hints) or 'Stages run in the displayed order. Completed work can be resumed from its saved project.')
        self.input_context.setText('Select raw recordings and the metadata needed to create HDF5 files.' if preparation else 'Select HDF5 files containing calibrated acceleration.')
        if hasattr(self,'stats_boxes'):
            for key,box in self.stats_boxes.items():
                box.setVisible(preparation if key in ('raw_files','raw_trace_count','response_files') else task=='process' or self.include_existing.isChecked())
        saved=sum(bool(mapping.get('enabled')) for mapping in self.config.get('hdf5_mappings',{}).values() if isinstance(mapping,dict))
        native=sum(mapping.get('enabled') is False for mapping in self.config.get('hdf5_mappings',{}).values() if isinstance(mapping,dict))
        legacy=self.config.get('hdf5_mapping',{}).get('enabled')
        self.hdf5_mapping_status.setText(f'Mappings saved for {saved} file(s); {native} file(s) use the existing layout. Review new files individually or verify all selected files together.' if saved or native else 'Saved layout mapping available. Review it to inspect the matched values.' if legacy else 'Review dataset names, units and metadata before processing. An Excel or CSV metadata table can be matched here.')
        self.layout_options.setVisible(task=='process' or task=='prepare_process' and self.include_existing.isChecked())
        self.hdf5_mapping_button.setVisible(self.layout_options.isChecked())
        self.hdf5_mapping_status.setVisible(self.layout_options.isChecked())
        self.update_review()

    def update_review(self):
        if not hasattr(self,'run_summary'): return
        cfg=self.collect()
        names={'ingestion':'HDF5 preparation','acceptreject':'Stage 1: accept / reject','filtering':'Stage 2: filtering','sanity':'Stage 3: sanity checks'}
        sequence=' → '.join(names[k] for k in cfg['stages']) or 'No processing stages selected'
        output=str(self.project.folder) if self.project else 'Choose a project folder when saving or starting the run.'
        if self.example_ready:
            output='A new sample project folder will be created automatically.'
        text=f'{sequence}\nProject folder: {output}\nWorkers: {cfg["workers"]}   |   Resume: {"enabled" if cfg["resume"] else "disabled"}'
        if 'acceptreject' in cfg['stages']:
            legacy=cfg['acceptreject_methods']==list('12345678') and cfg['acceptreject_required_votes']==5
            text+='\nAcceptance: '+('Original voting rules; five-vote setting, eight methods.' if legacy else f'{cfg["acceptreject_required_votes"]} of {len(cfg["acceptreject_methods"])} enabled methods must accept.')
        if 'filtering' in cfg['stages']:text+=f'\nFiltering: {len(cfg["filtering_methods"])} methods; high-pass fallback {cfg["filtering_hp_fallback_hz"]:g} Hz; low-pass minimum/fallback {cfg["filtering_lp_min_hz"]:g} Hz.'
        if 'sanity' in cfg['stages']: text+='   |   Sanity checks: '+(', '.join(cfg['enabled_checks']) or 'none')
        if 'ingestion' in cfg['stages']:text+='\nEvent matching: file name first, then trace start −2 min to +1 h; full waveform saved for every matched event.'
        if self.preflight:
            errors=self.preflight.get('blocking_issues',[])
            text+='\n'+(f'{len(errors)} required input issue(s) must be resolved before running.' if errors else 'Input scan complete. Review the inventory and any notices below before running.')
        elif self.example_ready: text+='\nRun example checks the bundled inputs and starts all three stages.'
        else: text+='\nScan the selected inputs before running. Changes to settings require a new scan.'
        if self.example_ready: text='Included example ready · 11 May 2013 · 18 components from 3 stations\n'+text
        self.run_button.setText('Run example' if self.example_ready else 'Run selected task')
        self.custom_button.setVisible(self.example_ready)
        self.scan_button.setVisible(not self.example_ready)
        self.run_summary.setText(text)

    def build_activity(self):
        page=self.page('Review and run','Confirm the selected workflow, inspect the input inventory and start processing.')
        self.run_summary=QLabel(); self.run_summary.setWordWrap(True); self.run_summary.setStyleSheet('background:white;border:1px solid #dde5ee;border-radius:8px;padding:16px;line-height:1.4;'); page.addWidget(self.run_summary)
        row=QHBoxLayout(); self.scan_button=QPushButton('Scan selected inputs'); self.scan_button.clicked.connect(self.scan); row.addWidget(self.scan_button)
        self.run_button=QPushButton('Run selected task'); self.run_button.setObjectName('primary'); self.run_button.setEnabled(False); self.run_button.clicked.connect(self.start_run); row.addWidget(self.run_button)
        self.custom_button=QPushButton('Use my own data'); self.custom_button.clicked.connect(self.use_own_data); row.addWidget(self.custom_button)
        self.pause_button=QPushButton('Pause safely'); self.pause_button.setEnabled(False); self.pause_button.clicked.connect(self.pause); row.addWidget(self.pause_button)
        openout=QPushButton('Open project folder'); openout.clicked.connect(lambda:self.open_path(self.project.folder) if self.project else None); row.addWidget(openout); row.addStretch(); page.addLayout(row)
        stats=QHBoxLayout(); self.stats={}; self.stats_boxes={}
        for key,label in [('raw_files','Raw files'),('raw_trace_count','Raw traces'),('response_files','Response files'),('hdf5_files','HDF5 files'),('trace_count','HDF5 traces')]:
            box=QGroupBox(label); lay=QVBoxLayout(box); value=QLabel('—'); value.setObjectName('stat'); lay.addWidget(value); stats.addWidget(box); self.stats[key]=value; self.stats_boxes[key]=box
        page.addLayout(stats)
        self.inventory_text=QPlainTextEdit(); self.inventory_text.setReadOnly(True); self.inventory_text.setPlaceholderText('The scan lists detected files, traces, networks and input issues.'); self.inventory_text.setMinimumHeight(105); self.inventory_text.setMaximumHeight(120); page.addWidget(self.inventory_text)
        from .progress_v1 import build_progress_panel
        self.progress_panel=build_progress_panel(self); page.addWidget(self.progress_panel)
        self.progress=self.progress_panel.stage_bar
        self.activity_label=QLabel('Ready'); self.activity_label.setObjectName('subtitle'); self.activity_label.setWordWrap(True); page.addWidget(self.activity_label)
        self.activity_tabs=QTabWidget()
        self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(110); self.activity_tabs.addTab(self.log,'Activity log')
        self.log.setMaximumBlockCount(2000)
        self.jobs_table=QTableView(); self.jobs_table.setMinimumHeight(110); self.activity_tabs.addTab(self.jobs_table,'Saved job history'); page.addWidget(self.activity_tabs,1)

    def build_results(self):
        page=self.page('Results','Open consolidated results, stored waveforms and diagnostic figures from current or previous projects.')
        row=QHBoxLayout(); refresh=QPushButton('Refresh project results'); refresh.clicked.connect(lambda:self.refresh_results()); row.addWidget(refresh)
        browse=QPushButton('Browse existing results folder'); browse.clicked.connect(self.browse_results); row.addWidget(browse)
        self.results_search=QLineEdit(); self.results_search.setAccessibleName('Filter result files and traces'); self.results_search.setPlaceholderText('Filter files or traces by event, station, network…'); self.results_search.textChanged.connect(self.filter_results); row.addWidget(self.results_search,1); page.addLayout(row)
        self.show_intermediate=QCheckBox('Show intermediate files and logs'); self.show_intermediate.toggled.connect(self.filter_results); page.addWidget(self.show_intermediate)
        sample_actions=QHBoxLayout()
        compare=QPushButton('Compare sample results');compare.clicked.connect(self.compare_sample_results);sample_actions.addWidget(compare)
        reference=QPushButton('View sample reference results');reference.clicked.connect(lambda:self.refresh_results(APP_ROOT/'test/reference_results'));sample_actions.addWidget(reference)
        sample_actions.addStretch();page.addLayout(sample_actions)
        split=QSplitter(); self.result_files=QListWidget(); self.result_files.setAccessibleName('Saved result files'); self.result_files.setMinimumWidth(230); self.result_files.setSpacing(3); self.result_files.currentRowChanged.connect(self.select_result); split.addWidget(self.result_files)
        right=QWidget(); rr=QVBoxLayout(right); rr.setContentsMargins(0,0,0,0); self.result_title=QLabel('Select a saved result'); self.result_title.setStyleSheet('font-size:14pt;font-weight:600;'); rr.addWidget(self.result_title)
        self.result_tabs=QTabWidget(); rr.addWidget(self.result_tabs,1)
        tablepage=QWidget(); tl=QVBoxLayout(tablepage); self.table=QTableView(); self.table.setAccessibleName('Processing results table'); self.table.setSortingEnabled(True); self.table.setSelectionBehavior(QTableView.SelectRows); tl.addWidget(self.table)
        actions=QHBoxLayout()
        export=QPushButton('Export visible table'); export.clicked.connect(self.export_table); actions.addWidget(export)
        wave=QPushButton('View selected trace'); wave.clicked.connect(self.view_table_trace); actions.addWidget(wave)
        figure=QPushButton('Open trace figure'); figure.clicked.connect(self.view_table_figure); actions.addWidget(figure); actions.addStretch(); tl.addLayout(actions)
        self.table.doubleClicked.connect(lambda _:self.view_table_trace()); self.result_tabs.addTab(tablepage,'Table')
        self.sheet_picker=QComboBox(); self.sheet_picker.setAccessibleName('Workbook sheet'); self.sheet_picker.hide(); self.sheet_picker.currentTextChanged.connect(self.select_workbook_sheet); tl.insertWidget(0,self.sheet_picker)
        wavepage=QWidget(); wl=QVBoxLayout(wavepage); self.trace_picker=QComboBox(); self.trace_picker.setAccessibleName('Waveform trace to display'); self.trace_picker.setMinimumContentsLength(30); self.trace_picker.currentIndexChanged.connect(self.plot_trace); wl.addWidget(self.trace_picker)
        pg.setConfigOptions(antialias=True,background='w',foreground='#38516a')
        self.waveplots=[]
        for label in ['Acceleration','Velocity','Displacement']:
            plot=pg.PlotWidget(); plot.setLabel('left',label); plot.getAxis('left').enableAutoSIPrefix(False); plot.showGrid(x=True,y=True,alpha=.14); plot.setMinimumHeight(125); plot.setMaximumHeight(170); wl.addWidget(plot,1); self.waveplots.append(plot)
        self.waveplots[1].setXLink(self.waveplots[0]); self.waveplots[2].setXLink(self.waveplots[0]); self.waveplots[-1].setLabel('bottom','Time from stored record start',units='s')
        self.wave_info=QLabel(); self.wave_info.setWordWrap(True); wl.addWidget(self.wave_info)
        imagebtn=QPushButton('Export waveform figure'); imagebtn.clicked.connect(self.export_waveform); wl.addWidget(imagebtn,alignment=Qt.AlignLeft); self.result_tabs.addTab(wavepage,'Waveforms')
        self.image_label=QLabel('Select a diagnostic figure.'); self.image_label.setAlignment(Qt.AlignCenter); self.image_label.setMinimumSize(500,400)
        image_scroll=QScrollArea(); image_scroll.setWidgetResizable(True); image_scroll.setWidget(self.image_label); self.result_tabs.addTab(image_scroll,'Figures')
        self.text_result=QPlainTextEdit(); self.text_result.setReadOnly(True); self.result_tabs.addTab(self.text_result,'Details')
        summarypage=QWidget(); sl=QVBoxLayout(summarypage); self.summary_label=QLabel('Open a decision or filtering table to see its outcome summary.'); self.summary_label.setWordWrap(True); self.summary_label.setMaximumHeight(45); sl.addWidget(self.summary_label)
        self.outcome_plot=pg.PlotWidget(); self.outcome_plot.setMinimumHeight(170); self.outcome_plot.setMaximumHeight(250); self.outcome_plot.setLabel('left','Trace count'); self.outcome_plot.showGrid(y=True,alpha=.15); sl.addWidget(self.outcome_plot,1)
        self.corner_plot=pg.PlotWidget(); self.corner_plot.setMinimumHeight(170); self.corner_plot.setMaximumHeight(250); self.corner_plot.setLabel('left','Low-pass corner',units='Hz'); self.corner_plot.setLabel('bottom','High-pass corner',units='Hz'); self.corner_plot.showGrid(x=True,y=True,alpha=.15); sl.addWidget(self.corner_plot,1); self.result_tabs.addTab(summarypage,'Summary')
        for index,description in enumerate(['Processing results table','Stored acceleration, velocity and displacement','Diagnostic figures saved by processing','Result details and processing logs','Acceptance outcomes and filtering corners']):
            self.result_tabs.setTabToolTip(index,description)
        split.addWidget(right); split.setStretchFactor(1,1); split.setSizes([270,850]); page.addWidget(split,1)

    def build_reader(self):
        from .reader_panel_v1 import ReaderPanel
        page=self.page('Reader','Browse existing unprocessed or processed HDF5 files and plot their stored waveforms.')
        self.reader=ReaderPanel(self)
        page.addWidget(self.reader,1)

    def build_help(self):
        page=self.page('User guide','Input preparation, scientific methods, result interpretation, and recovery.')
        self.help=QTextBrowser(); self.help.setOpenExternalLinks(True); self.help.setMinimumHeight(440)
        path=APP_ROOT/'docs'/'help.html'
        if path.exists(): self.help.setHtml(path.read_text(encoding='utf-8'))
        page.addWidget(self.help,1)

    def build_runtime(self):
        page=self.page('Runtime and compatibility','The app runs the supplied R and Python methods locally. Guo runs in Python; MATLAB is not required.')
        box=QGroupBox('Installed runtimes'); f=QFormLayout(box)
        self.file_field(f,'Python executable','python_executable','Executables (*.exe);;All files (*)')
        self.file_field(f,'Rscript executable','rscript_executable','Executables (*.exe);;All files (*)')
        from .runtime_env_v1 import external_settings
        if external_settings():
            for key in ('python_executable','rscript_executable'):
                field=getattr(self,key);field.setReadOnly(True);field._browse_button.setEnabled(False)
            note=QLabel('This session uses the installed-runtime setup. To change Python or R, reopen that launcher.');note.setWordWrap(True);page.addWidget(note)
        page.addWidget(box)
        b=QPushButton('Check runtime availability'); b.clicked.connect(self.check_runtime); page.addWidget(b,alignment=Qt.AlignLeft)
        self.runtime_info=QPlainTextEdit(); self.runtime_info.setReadOnly(True); self.runtime_info.setMinimumHeight(320); page.addWidget(self.runtime_info)
        note=QLabel('USGS filtering is intentionally inactive. The application versions of the main scripts call the original algorithms; changes to metadata, storage, error handling and configuration are documented in the app folder.'); note.setWordWrap(True); page.addWidget(note); page.addStretch()
        self.apply_config(defaults())
        self.update_context()

    def collect(self):
        cfg=copy.deepcopy(self.config)
        task=self.task_key()
        selected=[k for k,b in self.stage_boxes.items() if b.isChecked() and k!='ingestion']
        stages=(['ingestion'] if task in ('prepare','prepare_process') else [])+(selected if task!='prepare' else [])
        cfg.update(name=self.name.text().strip() or 'Untitled project',column_mappings=copy.deepcopy(self.mapping),
                   grouping=self.grouping.currentText(),remove_response=self.remove_response.isChecked(),input_units=self.input_units.currentText(),
                   processed_suffix=self.suffix.text().strip(),workers=self.workers.value(),save_figures=self.figures.isChecked(),
                   resume=self.resume.isChecked(),scan_all_files=self.scan_all.isChecked(),default_bits=self.bits.value(),
                   enabled_checks=[k for k,b in self.checks.items() if b.isChecked()],stages=stages,interface_task=task)
        cfg.update(acceptreject_methods=[k for k,b in self.acceptance_method_boxes.items() if b.isChecked()],
                   acceptreject_required_votes=self.required_votes.value(),
                   filtering_methods=[k for k,b in self.filter_method_boxes.items() if b.isChecked()],
                   filtering_hp_fallback_hz=self.hp_fallback.value(),filtering_lp_min_hz=self.lp_minimum.value())
        saved={}
        for key in ['raw_paths','response_paths','hdf5_paths']: saved[key]=getattr(self,key).values()
        for key in ['event_catalog','station_catalog','stage1_metadata','filtering_metadata']:
            saved[key]=getattr(self,key).text().strip()
        cfg.update(saved)
        saved['include_existing']=self.include_existing.isChecked(); saved['processing_stages']=selected
        cfg['interface_saved_inputs']=saved
        for key in ['python_executable','rscript_executable']:
            cfg[key]=getattr(self,key).text().strip()
        if cfg['python_executable']: cfg['python_executable']=console_python(cfg['python_executable'])
        if task=='prepare_process' and not self.include_existing.isChecked(): cfg['hdf5_paths']=[]
        if not self.remove_response.isChecked(): cfg['response_paths']=[]
        from .ncm_data_v1 import bind_config
        cfg['review_custom_hdf5_layout']=self.layout_options.isChecked()
        cfg=bind_config(cfg)
        from .runtime_env_v1 import runtime_config
        return relevant_input_config(runtime_config(cfg))

    def apply_config(self,cfg):
        from .ncm_data_v1 import bind_config
        from .runtime_env_v1 import runtime_config
        cfg=runtime_config(bind_config(cfg))
        from .method_settings_v1 import (DEFAULT_ACCEPTREJECT_METHODS,DEFAULT_CORNER_METHODS,
            DEFAULT_ACCEPTREJECT_REQUIRED_VOTES,DEFAULT_FILTERING_HP_FALLBACK_HZ,DEFAULT_FILTERING_LP_MIN_HZ)
        self._applying_config=True
        try:
            self.layout_options.setChecked(cfg.get('review_custom_hdf5_layout',False))
            self.config=copy.deepcopy(cfg); self.mapping=copy.deepcopy(cfg.get('column_mappings',{}))
            saved=cfg.get('interface_saved_inputs',{})
            self.name.setText(cfg.get('name','Untitled project'))
            for key in ['raw_paths','response_paths','hdf5_paths']:
                getattr(self,key).set_values(cfg.get(key) or saved.get(key,[]))
            for key in ['event_catalog','station_catalog','stage1_metadata','filtering_metadata']:
                getattr(self,key).setText(cfg.get(key) or saved.get(key,''))
            for key in ['python_executable','rscript_executable']:
                getattr(self,key).setText(cfg.get(key,''))
            for key,widget in [('grouping',self.grouping),('input_units',self.input_units)]: widget.setCurrentText(cfg.get(key,''))
            self.suffix.setText(cfg.get('processed_suffix','processed_2')); self.workers.setValue(cfg.get('workers',1)); self.bits.setValue(cfg.get('default_bits',24))
            for key,widget in [('remove_response',self.remove_response),('save_figures',self.figures),('resume',self.resume),('scan_all_files',self.scan_all)]: widget.setChecked(cfg.get(key,key!='scan_all_files'))
            for k,b in self.checks.items(): b.setChecked(k in cfg.get('enabled_checks',[]))
            for k,b in self.acceptance_method_boxes.items():b.setChecked(k in cfg.get('acceptreject_methods',DEFAULT_ACCEPTREJECT_METHODS))
            for k,b in self.filter_method_boxes.items():b.setChecked(k in cfg.get('filtering_methods',DEFAULT_CORNER_METHODS))
            self.required_votes.setMaximum(max(1,sum(b.isChecked() for b in self.acceptance_method_boxes.values())))
            self.required_votes.setValue(cfg.get('acceptreject_required_votes',DEFAULT_ACCEPTREJECT_REQUIRED_VOTES))
            self.hp_fallback.setValue(cfg.get('filtering_hp_fallback_hz',DEFAULT_FILTERING_HP_FALLBACK_HZ)); self.lp_minimum.setValue(cfg.get('filtering_lp_min_hz',DEFAULT_FILTERING_LP_MIN_HZ))
            self.refresh_method_controls()
            stages=cfg.get('stages',[])
            task='prepare_process' if 'ingestion' in stages and len(stages)>1 else 'prepare' if stages==['ingestion'] else 'process'
            self.task_buttons[task].setChecked(True)
            retained=saved.get('processing_stages',['acceptreject','filtering','sanity']) if task=='prepare' else stages
            for k,b in self.stage_boxes.items(): b.setChecked(k in (stages if k=='ingestion' else retained))
            self.include_existing.setChecked(saved.get('include_existing',bool(cfg.get('hdf5_paths')) and task=='prepare_process'))
        finally: self._applying_config=False
        self.invalidate(); self.update_context()

    def busy(self): return self.worker is not None and self.worker.isRunning()
    def can_run(self):
        return bool(self.preflight) and not self.preflight.get('blocking_issues')
    def invalidate(self,*args):
        self.preflight=None
        if self.example_ready:
            self.example_ready=False
            self.project_label.setText('No project open')
        if hasattr(self,'run_button'): self.run_button.setEnabled(False)
        if hasattr(self,'runtime_info') and not getattr(self,'_applying_config',False): self.update_review()
    def new_project(self):
        if self.busy(): return
        folder=QFileDialog.getExistingDirectory(self,'Choose a project folder (a new project stores outputs here)',str(default_projects_dir()))
        if folder:
            config=self.collect(); self.load_project(folder)
            if self.project and not self.project.file.exists(): self.apply_config(config); self.save()
    def open_project(self):
        if self.busy(): return
        name,_=QFileDialog.getOpenFileName(self,'Open SignalQC project',str(self.project.folder if self.project else default_projects_dir()),'Project (project.json)')
        if name: self.load_project(Path(name).parent)
    def load_project(self,folder):
        try:
            self.project=Project(folder); self.apply_config(self.project.config)
            self.project_label.setText(self.project.config['name']+'\n'+str(self.project.folder))
            self.nav.setCurrentRow(TASK_PAGE)
            self.refresh_jobs(); self.refresh_results(); self.statusBar().showMessage(
                'This project included intensity measures, which are unavailable in the paper edition. Review the remaining stages.'
                if self.project.retired_stage_removed else 'Project opened: '+str(self.project.folder))
        except Exception as e: self.error(str(e))
    def use_own_data(self):
        if self.busy(): return
        self.apply_config(defaults())
        self.nav.setCurrentRow(TASK_PAGE)
        self.statusBar().showMessage('Choose a task for your own recordings.')
    def save(self):
        if not self.project:
            self.new_project()
            if not self.project: return False
        self.project.save(self.collect()); self.config=self.project.config
        self.project_label.setText(self.config['name']+'\n'+str(self.project.folder)); return True
    def sample_inputs(self):
        if self.busy():return
        from .sample_v1 import sample_config
        try:
            cfg=sample_config()
            self.project=None
            self.apply_config(cfg)
            self.example_ready=True
            self.project_label.setText('Included example ready')
            self.nav.setCurrentRow(RUN_PAGE); self.update_review()
            self.inventory_text.setPlainText('Waveforms, event catalogue, station metadata and processing settings are loaded.')
            self.run_button.setEnabled(True); self.run_button.setFocus()
            self.statusBar().showMessage('Press Run example to process the included recordings.')
        except Exception as exc:self.error(str(exc))

    def launch(self,function,on_result):
        if self.busy(): return
        self.worker=Work(function); self.worker.progress.connect(self.on_progress); self.worker.result.connect(on_result); self.worker.error.connect(self.error)
        self.worker.finished.connect(self.finish_worker); self.scan_button.setEnabled(False); self.open_button.setEnabled(False); self.new_button.setEnabled(False); self.overview_run.setEnabled(False); self.worker.start()
    def finish_worker(self):
        self.scan_button.setEnabled(True); self.open_button.setEnabled(True); self.new_button.setEnabled(True); self.pause_button.setEnabled(False)
        self.custom_button.setEnabled(True)
        self.overview_run.setEnabled(True)
        self.run_button.setEnabled(self.can_run() or self.example_ready)
        self.pipeline=None
        self.refresh_jobs()
        if self.run_after_scan:
            self.run_after_scan=False
            if self.example_ready and self.can_run():
                QTimer.singleShot(0,lambda: self.start_run() if self.example_ready and self.can_run() else None)
    def scan(self):
        if self.busy(): return
        cfg=self.collect(); self.preflight=None; self.run_button.setEnabled(False); self.inventory_text.setPlainText('Reading file inventory and waveform headers…')
        self.nav.setCurrentRow(RUN_PAGE)
        def inspect(progress):
            result=inspect_inputs(relevant_input_config(cfg),self.project.folder if self.project else None,progress)
            if cfg.get('event_catalog') or cfg.get('station_catalog'):
                try: result['catalogs']=load_engine('ingestion','hdf5_writer_v10_ncm.py').discover(cfg)
                except Exception as e: result['issues'].append('Catalog: '+str(e))
            return result
        self.launch(inspect,lambda result:self.scanned(result,cfg))
    def scanned(self,result,scan_config=None):
        if scan_config is not None and scan_config!=self.collect():
            self.preflight=None; self.inventory_text.setPlainText('Inputs or settings changed during the scan. Scan again to review the current selection.'); return
        result=copy.deepcopy(result)
        result['blocking_issues']=validate_workflow(self.collect(),result)
        self.preflight=result
        for k,widget in self.stats.items(): widget.setText(str(result.get('counts',{}).get(k,result.get(k,0))))
        text=f'Networks: {", ".join(result.get("networks",[])) or "None detected"}\n'
        if 'ingestion' in self.collect()['stages']: text+=f'Waveform formats: {result.get("formats",{})}\n'
        if result.get('event_count'): text+=f'HDF5 events: {result["event_count"]}\n'
        if 'catalogs' in result:
            c=result['catalogs']; text+=f'Catalog events: {c.get("event_count",0)}  |  Station rows: {c.get("station_row_count",0)}\n'
            result.setdefault('issues',[]).extend(c.get('issues',[]))
        if 'association_preview' in result:
            a=result['association_preview']
            text+=f'Matching preview: {a["filename"]} trace(s) by file name; {a["time"]} by time; {a["multiple"]} with multiple events; {a["unmatched"]} unmatched.\n'
        if result['blocking_issues']: text+='\nRequired before running:\n'+'\n'.join(str(x) for x in result['blocking_issues'])
        notices=result.get('issues',[])
        if notices: text+='\n\nInput notices:\n'+'\n'.join(str(x) for x in notices)
        elif not result['blocking_issues']: text+='\nNo input-read issues detected.'
        if 'ingestion' in self.collect()['stages']: text+='\nResponse epochs and exact event associations are verified during HDF5 preparation.'
        self.inventory_text.setPlainText(text); self.update_review()
        self.run_button.setEnabled(self.can_run() and not self.busy())
        self.statusBar().showMessage('Input scan complete. Review the inventory before running.' if self.can_run() else 'Resolve the required input issues and scan again.')
        if self.project: atomic_json(self.project.folder/'input_scan.json',result)
    def start_run(self):
        if self.busy(): return
        if self.example_ready and not self.can_run():
            self.run_after_scan=True; self.custom_button.setEnabled(False)
            self.scan(); return
        if not self.can_run(): self.error('Scan the selected inputs and resolve the required input issues before starting.'); return
        issues=validate_workflow(self.collect(),self.preflight)
        if issues: self.error('\n'.join(issues)); self.invalidate(); return
        from .ncm_data_v1 import reference_fingerprint
        if self.preflight.get('ncm_reference_fingerprint')!=reference_fingerprint(self.collect()):
            self.invalidate();self.error('Reference data changed after the scan. Scan again to review the current catalogue, stations and responses.');return
        if 'hdf5_review_fingerprint' in self.preflight:
            from .hdf5_profiles_v1 import review_hdf5_files,hdf5_review_fingerprint
            cfg=self.collect();files=review_hdf5_files(cfg,self.project.folder if self.project else None)
            if hdf5_review_fingerprint(cfg,files)!=self.preflight['hdf5_review_fingerprint']:
                self.invalidate();self.error('HDF5 files or mapped metadata changed after the scan. Scan again to review current values.');return
        if self.example_ready:
            try:
                folder=tempfile.mkdtemp(prefix='SignalQC-example-',dir=default_projects_dir())
                self.project=Project(folder)
                self.save()
                atomic_json(self.project.folder/'input_scan.json',self.preflight)
            except OSError as exc:
                self.project=None; self.error('Could not create the sample project: '+str(exc)); return
            self.example_folder=self.project.folder
            self.example_ready=False; self.update_review()
        elif not self.save(): return
        cfg=self.collect()
        self.pipeline=Pipeline(self.project); runner=self.pipeline
        self.progress_panel.begin(cfg['stages'])
        self.run_button.setEnabled(False); self.pause_button.setEnabled(True); self.nav.setCurrentRow(RUN_PAGE)
        QTimer.singleShot(0,lambda:self.pages.widget(RUN_PAGE).ensureWidgetVisible(self.progress_panel,0,15))
        self.launch(lambda emit: self._run_pipeline(runner,emit),self.completed)
    def _run_pipeline(self,runner,emit): runner.progress=emit; return runner.run()
    def pause(self):
        if self.pipeline: self.pipeline.pause(); self.pause_button.setEnabled(False)
    def completed(self,result):
        self.log.appendPlainText('Saved run result: '+result.get('status','finished')); self.progress_panel.finish(result.get('status','finished'))
        self.activity_label.setText(result.get('status','finished').replace('_',' ').title()); self.refresh_results(); self.run_button.setEnabled(self.can_run() and not self.busy())
        if self.project.folder==self.example_folder and result.get('status')=='completed':
            self.nav.setCurrentRow(RESULTS_PAGE)
            self.statusBar().showMessage('Example complete. Choose Compare sample results to check this run against the reference.')
    def on_progress(self,event):
        message=event.get('message',str(event)) if isinstance(event,dict) else str(event)
        if message.startswith('SIGNALQC_EVENT '):
            try:
                detail=json.loads(message.split(' ',1)[1]); event=dict(event,**detail)
                message=detail.get('message',detail.get('event','Processing'))
                if detail.get('trace_id'): message+=' · '+detail['trace_id']
            except (ValueError,TypeError): pass
        self.progress_panel.update_event(event)
        self.log.appendPlainText(message[:4000])
        if message.startswith('[1]'):
            return
        self.statusBar().showMessage(message[:220]); self.activity_label.setText(message[:500])
    def error(self,message):
        if self.pipeline and self.progress_panel.model.running: self.progress_panel.finish('failed')
        self.log.appendPlainText(message); self.statusBar().showMessage('An issue needs attention. Details are saved in activity.'); self.activity_label.setText('Issue needs attention')
        QMessageBox.warning(self,'SignalQC · Action needed',message[-2500:]); self.run_button.setEnabled(self.can_run() and not self.busy())
    def refresh_jobs(self):
        if not self.project: return
        rows=self.project.jobs()
        frame=pd.DataFrame(rows)
        if len(frame): frame=frame[[c for c in ['stage','input','status','started','finished','message'] if c in frame]]
        self.jobs_model=FrameModel(frame); self.jobs_table.setModel(self.jobs_model); self.jobs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def map_columns(self,kind):
        file=getattr(self,kind+'_catalog').text()
        if not file: self.error('Select the '+kind+' catalog first.'); return
        try: columns=list(read_table(file).columns)
        except Exception as e: self.error(str(e)); return
        fields=['event_id','origin_time','year','month','day','hour','minute','second','latitude','longitude','depth','magnitude','magnitude_type','source_file'] if kind=='event' else ['station','network','location','channel','latitude','longitude','elevation','start_date','end_date','bits']
        dialog=QDialog(self); dialog.setWindowTitle('Map '+kind+' metadata columns'); dialog.resize(650,650); lay=QVBoxLayout(dialog)
        desc=QLabel('Choose the source column for each field. Automatic matching remains available for unchanged fields. Review the units below; depth defaults to km and elevation to m.'); desc.setWordWrap(True); lay.addWidget(desc)
        settings=QFormLayout()
        unit=QComboBox(); unit.addItems(['Automatic','km','m']); unit_key='event_depth_unit' if kind=='event' else 'station_elevation_unit'; unit.setCurrentText(self.config.get(unit_key,'Automatic'))
        settings.addRow('Depth units' if kind=='event' else 'Elevation units',unit)
        sheet=QLineEdit(str(self.config.get(kind+'s_sheet') or '')); sheet.setPlaceholderText('First sheet (default), sheet name or zero-based index'); settings.addRow('Excel sheet',sheet)
        delimiter=QLineEdit(self.config.get(kind+'s_delimiter','')); delimiter.setPlaceholderText('Automatic detection'); settings.addRow('Text delimiter',delimiter); lay.addLayout(settings)
        table=QTableWidget(len(fields),2); table.setHorizontalHeaderLabels(['Field','Your column']); combos={}
        table.setAccessibleName('Catalog field mapping'); table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i,field in enumerate(fields):
            table.setItem(i,0,QTableWidgetItem(field)); combo=QComboBox(); combo.addItem('Automatic / not supplied'); combo.addItems(list(map(str,columns)))
            prior=self.mapping.get(kind,self.mapping.get(kind+'s',{}))
            if field in prior: combo.setCurrentText(prior[field])
            combo.setAccessibleName(field.replace('_',' ')+' source column')
            combos[field]=combo; table.setCellWidget(i,1,combo); table.setRowHeight(i,42)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); lay.addWidget(table)
        reload_columns=QPushButton('Load columns from selected sheet / delimiter'); lay.addWidget(reload_columns)
        def reload():
            try:
                sn=sheet.text().strip(); sn=int(sn) if sn.isdigit() else sn or 0
                frame=pd.read_excel(file,sheet_name=sn,nrows=2) if Path(file).suffix.lower() in ('.xlsx','.xls') else pd.read_csv(file,sep=delimiter.text() or None,engine='python',nrows=2)
                for combo in combos.values():
                    previous=combo.currentText(); combo.clear(); combo.addItem('Automatic / not supplied'); combo.addItems(list(map(str,frame.columns)))
                    if previous in frame.columns: combo.setCurrentText(previous)
            except Exception as e: QMessageBox.warning(dialog,'Metadata columns',str(e))
        reload_columns.clicked.connect(reload)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); lay.addWidget(buttons)
        configure_accessibility(dialog)
        if dialog.exec():
            self.mapping[kind]={k:c.currentText() for k,c in combos.items() if c.currentIndex()>0}
            self.config[unit_key]=None if unit.currentIndex()==0 else unit.currentText()
            if sheet.text().strip(): self.config[kind+'s_sheet']=int(sheet.text()) if sheet.text().isdigit() else sheet.text().strip()
            else: self.config.pop(kind+'s_sheet',None)
            if delimiter.text(): self.config[kind+'s_delimiter']=delimiter.text()
            else: self.config.pop(kind+'s_delimiter',None)
            self.invalidate()

    def refresh_results(self,folder=None):
        if folder is None and not self.project: return
        folder=Path(folder or self.project.folder)
        suffixes={'.h5','.hdf5','.csv','.xlsx','.json','.log'}
        self.result_paths=sorted([p for p in walk_files([folder]) if p.suffix.lower() in suffixes and not any(x in p.parts for x in ('.venv','runtime','engines','__pycache__'))],key=lambda p:(0 if p.name.endswith('_results.xlsx') else 1,p.suffix,p.name))
        self.filter_results()
    def browse_results(self):
        folder=QFileDialog.getExistingDirectory(self,'Browse existing processed results',str(Path.home()))
        if folder: self.refresh_results(folder)
    def filter_results(self):
        query=self.results_search.text().lower()
        paths=[p for p in getattr(self,'result_paths',[]) if not p.name.lower().endswith('_ims_output.xlsx') and 'intensity' not in p.parts and p.suffix.lower() not in ('.png','.jpg','.jpeg','.svg','.pdf')]
        has_consolidated=any(p.name.endswith('_results.xlsx') for p in paths)
        if not self.show_intermediate.isChecked():
            paths=[p for p in paths if p.suffix.lower() in ('.h5','.hdf5') or p.suffix.lower() in ('.csv','.xlsx') and (not has_consolidated or p.name.endswith('_results.xlsx'))]
        self.display_paths=[p for p in paths if query in str(p).lower()]
        self.result_files.clear()
        for p in self.display_paths:
            names={'results':'Consolidated results','ingestion':'Prepared HDF5','acceptreject':'Accept / reject','filtering':'Filtering','sanity':'Sanity checks'}
            stage=next((names[x] for x in p.parts if x in names),p.parent.name)
            self.result_files.addItem(p.name+'\n'+stage)
            self.result_files.item(self.result_files.count()-1).setToolTip(str(p))
        if getattr(self,'table_proxy',None): self.table_proxy.setFilterFixedString(query)
        if self.result_records:
            self.display_records=[r for r in self.result_records if query in json.dumps(r,default=str).lower()]
            self.trace_picker.blockSignals(True); self.trace_picker.clear()
            for r in self.display_records: self.trace_picker.addItem(self.trace_label(r))
            self.trace_picker.blockSignals(False)
            if self.display_records: self.plot_trace(0)
    def set_table(self,frame):
        self.current_table=frame; self.table_model=FrameModel(frame); self.table_proxy=QSortFilterProxyModel(); self.table_proxy.setSourceModel(self.table_model); self.table_proxy.setFilterKeyColumn(-1); self.table_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive); self.table.setModel(self.table_proxy)
        self.update_outcome_summary(frame)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive); self.table.resizeColumnsToContents()
        for c in range(len(frame.columns)):
            self.table.setColumnWidth(c,min(340,max(90,self.table.columnWidth(c))))
    def select_result(self,index):
        if index<0 or index>=len(self.display_paths): return
        path=self.display_paths[index]; self.result_title.setText(path.name); self.result_title.setToolTip(str(path))
        self.selected_workbook=None; self.sheet_picker.hide()
        try:
            if path.suffix.lower() in ('.csv','.xlsx'):
                self.selected_workbook=path if path.suffix.lower()=='.xlsx' else None
                self.sheet_picker.blockSignals(True); self.sheet_picker.clear()
                if self.selected_workbook:
                    with pd.ExcelFile(path) as book:self.sheet_picker.addItems(book.sheet_names)
                self.sheet_picker.setVisible(bool(self.selected_workbook)); self.sheet_picker.blockSignals(False)
                frame=read_table(path); self.set_table(frame); self.result_tabs.setCurrentIndex(0)
                self.text_result.setPlainText(f'{len(frame)} rows · {len(frame.columns)} columns\n{path}')
            elif path.suffix.lower() in ('.h5','.hdf5'):
                self.result_records=hdf5_records(path); self.display_records=list(self.result_records); self.set_table(pd.DataFrame(self.result_records)); self.trace_picker.blockSignals(True); self.trace_picker.clear()
                for r in self.result_records: self.trace_picker.addItem(self.trace_label(r))
                self.trace_picker.blockSignals(False); self.plot_trace(0); self.result_tabs.setCurrentIndex(1)
            elif path.suffix.lower() in ('.png','.jpg'):
                pix=QPixmap(str(path)); self.image_label.setPixmap(pix.scaled(1100,900,Qt.KeepAspectRatio,Qt.SmoothTransformation)); self.result_tabs.setCurrentIndex(2)
            else: self.text_result.setPlainText(path.read_text(encoding='utf-8',errors='replace')[:1000000]); self.result_tabs.setCurrentIndex(3)
        except Exception as e: self.error(f'Cannot open {path.name}: {e}')
    def trace_label(self,r): return f'{r.get("event_id","")} · {r.get("network","")}.{r.get("station","")}.{r.get("channel","")} · {r["trace_id"]}'
    def plot_trace(self,index):
        for plot in self.waveplots: plot.clear()
        if index<0 or index>=len(self.display_records): return
        r=self.display_records[index]; self.wave_data=[]
        options=[['acceleration_waveform','acceleration','acc','raw_waveform'],['velocity_waveform','velocity','vel'],['displacement_waveform','displacement','disp']]
        colors=['#147d70','#417bd0','#ac6dba']; descriptions=[]
        for i,candidates in enumerate(options):
            dataset=next((d for d in candidates if d in r['datasets'] and r.get('dataset_sizes',{}).get(d,1)>0),None)
            if not dataset: descriptions.append(['acceleration','velocity','displacement'][i]+': not stored'); continue
            try:
                t,y,attrs=read_waveform(r,dataset)
                units=attrs.get('units','counts' if dataset=='raw_waveform' else ['m/s²','m/s','m'][i])
                curve=self.waveplots[i].plot(t,y,pen=pg.mkPen(colors[i],width=1))
                curve.setDownsampling(auto=True,method='peak')
                curve.setClipToView(True)
                self.waveplots[i].setLabel('left',dataset.replace('_waveform','').title(),units=str(units)); self.waveplots[i].enableAutoRange()
                self.wave_data.append((i,t,y,str(units))); descriptions.append(f'{dataset}: {len(y):,} samples')
            except Exception as e: descriptions.append(str(e))
        self.wave_info.setText(' · '.join(descriptions)+'\nDrag to pan. Scroll to zoom. Right-click a plot for axis options. Display downsampling preserves visible peaks; saved data remain unchanged.')
    def export_table(self):
        if self.current_table is None: return
        file,_=QFileDialog.getSaveFileName(self,'Export table','results.csv','CSV (*.csv)')
        if file:
            rows=[self.table_proxy.mapToSource(self.table_proxy.index(i,0)).row() for i in range(self.table_proxy.rowCount())]
            self.current_table.iloc[rows].to_csv(file,index=False)
    def selected_row(self):
        index=self.table.currentIndex()
        if not index.isValid() or self.current_table is None: return None
        return self.current_table.iloc[self.table_proxy.mapToSource(index).row()]
    def view_table_trace(self):
        row=self.selected_row()
        if row is None: return
        file=next((self.result_link(row[k]) for k in ['processed_hdf5','source_file','filename','file'] if k in row and pd.notna(row[k]) and Path(str(row[k])).suffix.lower() in ('.h5','.hdf5') and self.result_link(row[k])),None)
        if not file: self.error('This row has no accessible HDF5 path. Select its HDF5 file in the results list.'); return
        key=next((str(row[k]) for k in ['trace.id','trace_id'] if k in row and pd.notna(row[k])),None)
        try:
            records=hdf5_records(file); chosen=[r for r in records if r['trace_id']==key and str(r['event_id'])==str(row.get('event_id',r['event_id']))]
            if not chosen: self.error('The exact stored trace key was not found.'); return
            self.result_records=records; self.display_records=chosen; self.trace_picker.blockSignals(True); self.trace_picker.clear(); self.trace_picker.addItems([self.trace_label(r) for r in chosen]); self.trace_picker.blockSignals(False); self.plot_trace(0); self.result_tabs.setCurrentIndex(1)
        except Exception as e: self.error(str(e))
    def view_table_figure(self):
        row=self.selected_row()
        if row is None: return
        file=next((self.result_link(row[k]) for k in ['figure_avd','figure_path','figure_fas'] if k in row and pd.notna(row[k]) and self.result_link(row[k])),None)
        if file:
            self.image_label.setPixmap(QPixmap(file).scaled(1100,900,Qt.KeepAspectRatio,Qt.SmoothTransformation)); self.result_tabs.setCurrentIndex(2)
        else: self.error('No saved diagnostic figure is linked to this row.')
    def result_link(self,value):
        path=Path(str(value))
        if not path.is_absolute() and getattr(self,'selected_workbook',None):path=self.selected_workbook.parent/path
        return str(path.resolve()) if path.is_file() else None

    def compare_sample_results(self):
        if self.busy():return
        folder=self.example_folder if self.project and self.project.folder==self.example_folder else QFileDialog.getExistingDirectory(self,'Choose your completed sample project folder',str(self.project.folder if self.project else default_projects_dir()))
        if not folder:return
        from .sample_compare_v1 import compare_sample,format_report
        def display(result):
            dialog=QDialog(self);dialog.setWindowTitle('Sample result comparison');dialog.resize(780,500)
            layout=QVBoxLayout(dialog);body=QPlainTextEdit();body.setReadOnly(True);body.setPlainText(format_report(result));layout.addWidget(body)
            buttons=QDialogButtonBox(QDialogButtonBox.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()
        self.launch(lambda progress:compare_sample(folder),display)
    def update_outcome_summary(self,frame):
        self.outcome_plot.clear(); self.corner_plot.clear()
        column=next((c for c in ['pipeline_outcome','sc_status','stage2_status','rejectreason3','status','acrjfinal3','acrjfinal'] if c in frame),None)
        if column:
            counts=frame[column].fillna('not evaluated').astype(str).value_counts()
            counts.index=[{'True':'accepted','False':'rejected','1':'accepted','0':'rejected'}.get(x,x) for x in counts.index]
            x=np.arange(len(counts)); colors=[QColor('#25a389') if c in ('accepted','completed','pass') else QColor('#c76c66') if c in ('fail','failed','rejected','error') else QColor('#d0a247') for c in counts.index]
            self.outcome_plot.addItem(pg.BarGraphItem(x=x,height=counts.to_numpy(),width=.55,brushes=colors)); self.outcome_plot.getAxis('bottom').setTicks([list(zip(x,counts.index))])
            self.outcome_plot.setYRange(0,max(counts.max()*1.15,1),padding=0)
            self.summary_label.setText(f'{len(frame):,} traces  ·  '+ '  ·  '.join(f'{label}: {count}' for label,count in counts.items()))
        else: self.summary_label.setText(f'{len(frame):,} rows. This table has no recognized processing decision column.')
        if 'fcHP' in frame and 'fcLP' in frame:
            hp=pd.to_numeric(frame.fcHP,errors='coerce'); lp=pd.to_numeric(frame.fcLP,errors='coerce'); valid=(hp>0)&(lp>hp)&np.isfinite(hp)&np.isfinite(lp)
            self.corner_plot.plot(hp[valid].to_numpy(),lp[valid].to_numpy(),pen=None,symbol='o',symbolSize=8,symbolBrush='#147d70',symbolPen=None)
    def export_waveform(self):
        if not getattr(self,'wave_data',[]): return
        file,_=QFileDialog.getSaveFileName(self,'Export waveform figure','waveforms.png','PNG (*.png);;PDF (*.pdf)')
        if not file: return
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True)
        for i,t,y,unit in self.wave_data: axes[i].plot(t,y,lw=.6); axes[i].set_ylabel(['Acceleration','Velocity','Displacement'][i]+' ('+unit+')'); axes[i].grid(alpha=.15)
        axes[-1].set_xlabel('Time (s)'); fig.suptitle(self.trace_picker.currentText(),fontsize=9); fig.tight_layout(); fig.savefig(file,dpi=180); plt.close(fig)
    def open_path(self,path): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def check_runtime(self):
        cfg=self.collect()
        def work(progress):
            import subprocess
            results=[]
            for key in ('python_executable','rscript_executable'):
                results.append(f'{key}: '+('found' if Path(cfg.get(key,'')).is_file() else 'MISSING')+'\n'+cfg.get(key,''))
            py=cfg['python_executable']
            if Path(py).is_file():
                code='import importlib.util,json; print(json.dumps({n:bool(importlib.util.find_spec(n)) for n in ["numpy","scipy","pandas","h5py","obspy","PySide6","pyqtgraph","openpyxl"]},indent=2))'
                r=subprocess.run([py,'-c',code],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,timeout=30); results.append(r.stdout+r.stderr)
            rpath=cfg['rscript_executable']
            if Path(rpath).is_file():
                env=os.environ.copy(); env.pop('R_HOME',None)
                env['R_LIBS_USER']=str(APP_ROOT/'runtime'/'R-library')
                from .runtime_env_v1 import runtime_environment
                env=runtime_environment(env)
                packages=['reticulate','openxlsx','jsonlite','zoo','RCTC']
                code='for(p in c('+','.join(json.dumps(p) for p in packages)+')) cat(p,requireNamespace(p,quietly=TRUE),"\\n")'
                r=subprocess.run([rpath,'-e',code],env=env,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,timeout=45); results.append(r.stdout+r.stderr)
            return '\n\n'.join(results)
        self.launch(work,self.runtime_info.setPlainText)
    def closeEvent(self,event):
        if self.reader.busy():
            self.statusBar().showMessage('The Reader is finishing a file read. Close again when it has finished.'); event.ignore(); return
        if self.busy():
            if self.pipeline: self.pause()
            self.statusBar().showMessage('Waiting for the active operation to reach a safe checkpoint. Close again after it finishes.'); event.ignore(); return
        if self.project: self.save()
        event.accept()

def main():
    app=QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('SignalQC'); app.setOrganizationName('SignalQC')
    project=sys.argv[1] if len(sys.argv)>1 else None
    preferences=user_state_dir()/'preferences.json'
    previous=preferences if preferences.exists() else APP_ROOT/'preferences.json'
    last_project=None
    if not project and previous.exists():
        try:
            last=json.loads(previous.read_text()).get('last_project','')
            if last and Path(last,'project.json').is_file(): last_project=last
        except (ValueError,OSError): pass
    window=Window(project,last_project); window.show()
    ready_file=os.environ.pop('SIGNALQC_STARTUP_READY_FILE',None)
    if ready_file:
        # Acknowledge only after Qt has entered the event loop and shown the window.
        def acknowledge_startup():
            try: Path(ready_file).write_text(str(os.getpid()),encoding='utf-8')
            except OSError: pass
        QTimer.singleShot(0,acknowledge_startup)
    def remember():
        if window.project and window.project.folder!=window.example_folder:
            try: atomic_json(preferences,{'last_project':str(window.project.folder)})
            except OSError: pass
    app.aboutToQuit.connect(remember)
    return app.exec()
