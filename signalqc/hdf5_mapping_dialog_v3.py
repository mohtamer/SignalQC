"""Reviewable field suggestions layered on the preserved v2 mapping workflow."""
from __future__ import annotations

import copy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox,QHeaderView,QLabel,QPushButton,QTableWidgetItem,QScrollArea,QLayout
from .hdf5_mapping_dialog_v2 import Hdf5MappingDialog as MappingDialogV2,FIELDS,set_combo,short

STATUS_LABELS={'suggested':'Suggested — review','ambiguous':'Multiple matches — select',
               'unmatched':'Select manually','manual':'Manual selection','saved':'Saved mapping',
               'missing':'Missing / unknown'}


class Hdf5MappingDialog(MappingDialogV2):
    def __init__(self,*args,**kwargs):
        self._metadata_origins={};self._dataset_origins={};self._unit_origins={}
        self._suggestions={'metadata':{},'datasets':{}};self._file_review_states={};self._refreshing=False;self._literal_selections={}
        super().__init__(*args,**kwargs)

    def build_waveforms(self):
        super().build_waveforms();self.dataset_status={}
        self.suggest_button.setText('Refresh untouched suggestions')
        for key,combo in self.dataset_fields.items():
            label=QLabel('Select manually');label.setFixedWidth(163);label.setWordWrap(True)
            combo.parentWidget().layout().addWidget(label);self.dataset_status[key]=label
            combo.currentTextChanged.connect(lambda _,field=key:self.dataset_edited(field))
            combo.activated.connect(lambda index,field=key:self.dataset_path_selected(field,index))
            self.unit_fields[key].currentIndexChanged.connect(lambda _,field=key:self.unit_edited(field))

    def build_metadata(self):
        super().build_metadata()
        self.metadata_help.setText('2. Review suggested matches and their example values. Select sources manually when names are ambiguous or unmatched. Optional fields may remain unknown. Saved and manual choices are retained when suggestions are refreshed.')
        self.metadata_table.setColumnCount(5)
        self.metadata_table.setHorizontalHeaderLabels(['Meaning','Source type','Match this source','Example value','Review status'])
        header=self.metadata_table.horizontalHeader()
        for col,width in [(0,215),(1,147),(3,125),(4,170)]:header.setSectionResizeMode(col,QHeaderView.Fixed);self.metadata_table.setColumnWidth(col,width)
        header.setSectionResizeMode(2,QHeaderView.Stretch)
        for row,(key,_) in enumerate(FIELDS):
            self.metadata_table.item(row,0).setToolTip(dict(FIELDS)[key])
            item=QTableWidgetItem('Select manually');item.setFlags(item.flags()&~Qt.ItemIsEditable);self.metadata_table.setItem(row,4,item)
            kind=self.metadata_fields[key][0];index=kind.findData('auto')
            if index>=0:kind.removeItem(index)
        page_layout=self.tabs.widget(1).widget().layout()
        self.metadata_suggest_button=QPushButton('Refresh untouched suggestions');self.metadata_suggest_button.clicked.connect(self.use_suggestions)
        page_layout.insertWidget(1,self.metadata_suggest_button,alignment=Qt.AlignLeft)

    def build_preview(self):
        super().build_preview()
        self.preview_button.setText('Preview records')
        self.review_confirmation=QCheckBox('I reviewed the mappings, preview values and fields left missing or unknown.')
        self.review_confirmation.setEnabled(False);self.review_confirmation.toggled.connect(self.update_buttons)
        page=self.tabs.widget(2);page.layout().addWidget(self.review_confirmation);page.layout().setSizeConstraint(QLayout.SetMinimumSize)
        title=self.tabs.tabText(2);self.tabs.removeTab(2)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);self.tabs.addTab(scroll,title)

    def load_mapping(self,mapping):
        self._literal_selections={}
        prepared=copy.deepcopy(mapping)
        metadata=prepared.setdefault('metadata',{})
        self._metadata_origins={key:'saved' if key in metadata else 'untouched' for key,_ in FIELDS}
        self._dataset_origins={key:'saved' if key in mapping.get('datasets',{}) else 'untouched' for key in self.dataset_fields}
        self._unit_origins={key:'saved' if key+'_units' in mapping else 'untouched' for key in self.unit_fields}
        self._suggestions={'metadata':{},'datasets':{}}
        previous=self._loading;self._loading=True
        try:
            for key,(kind,_) in self.metadata_fields.items():
                saved_auto=metadata.get(key,{}).get('kind')=='auto'
                if saved_auto and kind.findData('auto')<0:kind.addItem('Saved automatic match','auto')
                if not saved_auto and kind.findData('auto')>=0:kind.removeItem(kind.findData('auto'))
                metadata.setdefault(key,{'kind':'missing','value':''})
        finally:self._loading=previous
        super().load_mapping(prepared);self.update_statuses()

    def file_changed(self,*args):
        if self._loading:return
        previous=self._current_file
        if previous:self._file_review_states[previous]=copy.deepcopy((self._metadata_origins,self._dataset_origins,self._unit_origins,self._suggestions))
        super().file_changed(*args)
        if self._current_file in self._file_review_states:
            self._metadata_origins,self._dataset_origins,self._unit_origins,self._suggestions=copy.deepcopy(self._file_review_states[self._current_file])
        self.update_statuses()

    def changed(self,*args):
        if not self._loading and hasattr(self,'review_confirmation'):
            self.review_confirmation.blockSignals(True);self.review_confirmation.setChecked(False);self.review_confirmation.blockSignals(False)
        super().changed(*args)

    def update_buttons(self):
        super().update_buttons()
        if not hasattr(self,'review_confirmation') or not hasattr(self,'apply_button'):return
        ready=self.apply_button.isEnabled()
        self.review_confirmation.setEnabled(ready)
        self.apply_button.setEnabled(ready and self.review_confirmation.isChecked())
        if ready and not self.review_confirmation.isChecked():self.apply_button.setToolTip('Confirm that you reviewed the current mappings, values and missing fields.')

    def status_for(self,category,key):
        origins=self._metadata_origins if category=='metadata' else self._dataset_origins
        origin=origins.get(key,'untouched')
        if origin in ('saved','manual','missing'):
            reason='This saved choice is retained. Edit the source explicitly to change it.' if origin=='saved' else 'This explicit choice is retained when suggestions are refreshed.'
            return STATUS_LABELS[origin],reason
        result=self._suggestions.get(category,{}).get(key,{})
        status=result.get('status','unmatched');reason=result.get('reason','No clear match has been selected. Select a source or leave an optional field unknown.')
        confidence=result.get('confidence')
        if isinstance(confidence,(int,float)):reason+=f'\nName-match strength: {confidence:.2f} (rule score).'
        candidates=result.get('candidates') or []
        if status=='ambiguous' and candidates:
            names=[str(item.get('name') or (item.get('source') or {}).get('value') or item.get('pattern','')) for item in candidates[:6]]
            reason+='\nCandidates: '+', '.join(names)
        label='Uncertain — select' if status=='ambiguous' and len(candidates)<=1 else STATUS_LABELS.get(status,STATUS_LABELS['unmatched'])
        return label,reason

    def update_statuses(self):
        if not hasattr(self,'metadata_table') or self.metadata_table.columnCount()<5:return
        for row,(key,_) in enumerate(FIELDS):
            label,reason=self.status_for('metadata',key);item=self.metadata_table.item(row,4)
            item.setText(label);item.setToolTip(reason);self.metadata_fields[key][1].setToolTip(reason)
            if self._metadata_origins.get(key)=='untouched' and self.metadata_fields[key][0].currentData()=='missing':
                self.metadata_table.item(row,3).setText('Unknown — review')
        for key,label in self.dataset_status.items():
            text,reason=self.status_for('datasets',key);label.setText(text);label.setToolTip(reason);self.dataset_fields[key].setToolTip(reason)

    def mark_metadata(self,key):
        if self._loading or self._refreshing:return
        self._metadata_origins[key]='missing' if self.metadata_fields[key][0].currentData()=='missing' else 'manual'
        self.update_statuses()

    def metadata_kind_changed(self,key):
        super().metadata_kind_changed(key);self.mark_metadata(key)

    def source_changed(self,key):
        super().source_changed(key);self.mark_metadata(key)

    def update_source_example(self,key):
        super().update_source_example(key)
        if self._loading or not self.table_inventory:return
        spec=self.metadata_spec(key)
        if spec.get('kind')=='table':
            sample=next((row.get(spec['value']) for row in self.table_inventory.get('samples',[]) if row.get(spec['value']) is not None),'')
            item=self.metadata_table.item([name for name,_ in FIELDS].index(key),3);item.setText(short(sample));item.setToolTip(str(sample))

    def field_options_changed(self,*args):
        super().field_options_changed(*args)
        if self._active_field:self.mark_metadata(self._active_field)

    def dataset_edited(self,key):
        if self._loading or self._refreshing:return
        self._literal_selections.pop(key,None)
        if self._unit_origins.get(key,'untouched')=='untouched':
            self._loading=True
            try:self.unit_fields[key].setCurrentText('auto')
            finally:self._loading=False
        self._dataset_origins[key]='manual';self.update_statuses()

    def dataset_path_selected(self,key,index):
        combo=self.dataset_fields[key];item=combo.itemData(index)
        self.dataset_edited(key)
        if isinstance(item,dict) and item.get('literal_pattern'):
            self._literal_selections[key]=(combo.itemText(index),item['literal_pattern'])
        self.changed()

    def draft_mapping(self):
        mapping=super().draft_mapping()
        if mapping.get('enabled'):
            for key,(display,pattern) in self._literal_selections.items():
                if self.dataset_fields[key].currentText()==display:mapping['datasets'][key]=pattern
        return mapping

    def unit_edited(self,key):
        if self._loading or self._refreshing:return
        self._unit_origins[key]='manual';self._dataset_origins[key]='manual';self.update_statuses()

    def metadata_row_changed(self,row,*args):
        super().metadata_row_changed(row,*args)
        if row<0:return
        key=FIELDS[row][0];extra='kHz' if key=='sampling_rate' else 'ns' if key=='dt' else None
        if extra:
            previous=self._loading;self._loading=True
            try:
                if self.field_unit.findText(extra)<0:self.field_unit.addItem(extra)
                self.field_unit.setCurrentText(self._metadata_options.get(key,{}).get('unit','auto'))
            finally:self._loading=previous

    def table_inspected(self,inventory):
        super().table_inspected(inventory);self.use_suggestions()

    def table_toggled(self,*args):
        super().table_toggled(*args)
        if not self._loading and hasattr(self,'metadata_fields') and self.inventory:self.use_suggestions()

    def use_suggestions(self,only_empty=False):
        # The v2 inspection hook passes only_empty; all refreshes here preserve
        # saved/manual choices, including an explicitly cleared optional field.
        if not self.inventory:return
        from .hdf5_suggestions_v1 import suggest_fields
        result=suggest_fields(self.inventory,self.table_inventory if self.use_table.isChecked() else None,fields=[key for key,_ in FIELDS])
        previous=self._loading;self._loading=True;self._refreshing=True
        try:
            self._suggestions=copy.deepcopy(result)
            for key,widget in self.dataset_fields.items():
                if self._dataset_origins.get(key,'untouched')!='untouched':continue
                suggestion=result.get('datasets',{}).get(key,{})
                widget.setEditText(suggestion.get('pattern') or '' if suggestion.get('status')=='suggested' else '')
                unit=suggestion.get('unit') if suggestion.get('status')=='suggested' else None
                if self._unit_origins.get(key,'untouched')=='untouched':self.unit_fields[key].setCurrentText(unit if unit and self.unit_fields[key].findText(unit)>=0 else 'auto')
            for key,(kind,source) in self.metadata_fields.items():
                if self._metadata_origins.get(key,'untouched')!='untouched':continue
                suggestion=result.get('metadata',{}).get(key,{})
                spec=copy.deepcopy(suggestion.get('source')) if suggestion.get('status')=='suggested' else None
                spec=spec or {'kind':'missing','value':''}
                self._metadata_options[key]={name:value for name,value in spec.items() if name not in ('kind','value','scope')}
                set_combo(kind,spec['kind']);self.populate_source(key,spec)
        finally:self._loading=previous;self._refreshing=False
        for key in self.metadata_fields:self.update_source_example(key)
        if self._active_field:self.metadata_row_changed([key for key,_ in FIELDS].index(self._active_field))
        self.update_statuses()
        self.suggestion_note.setText('Suggested matches require review. Ambiguous and unmatched fields remain unknown until you select a source. Stored units and matrix orientation must also be checked.')
        if not previous:self.changed()

    def inspected(self,inventory):
        super().inspected(inventory)
        def literal(value):return ''.join({'[':'[[]','?':'[?]','*':'[*]'}.get(char,char) for char in value)
        paths={item['path'] for item in inventory.get('datasets',[]) if len(item.get('shape',[])) in (1,2)}
        repeated={'**/'+path.rsplit('/',1)[-1] for path in paths}
        for combo in self.dataset_fields.values():
            for index in range(combo.count()):
                text=combo.itemText(index)
                if text in paths:combo.setItemData(index,{'literal_pattern':literal(text)})
                elif text in repeated:combo.setItemData(index,{'literal_pattern':'**/'+literal(text[3:])})
        self.update_statuses()
