"""Run progress with explicit file/stage boundaries and conservative estimates."""
from __future__ import annotations
import time
from pathlib import Path

STAGES = {'ingestion':'HDF5 preparation', 'acceptreject':'1 · Accept / reject',
          'filtering':'2 · Filtering', 'sanity':'3 · Sanity checks'}


def duration(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours} h {minutes:02} min' if hours else f'{minutes} min {seconds:02} s'


class RunProgress:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset([])

    def reset(self, stages):
        self.started = self.clock(); self.ended = None
        self.running = True; self.status = 'Preparing run'; self.stages = list(stages)
        self.files = {}; self.file = ''; self.stage = ''; self.workers = None
        self.stage_results = {}; self.file_started = None; self.file_times = []
        self.message = 'Inspecting inputs and preparing the selected stages.'
        self._phase('Preparing', 0, 0, 'items')

    def _phase(self, name, current, total, unit):
        self.phase = name; self.current = current; self.total = total; self.unit = unit
        self.phase_started = self.clock(); self.baseline = current
        self.computed = set(); self.saved = set(); self.groups = set(); self.already = 0; self.worker_groups = {}; self.issue_count=0

    def finish(self, status):
        self.status = {'completed':'Completed', 'completed_with_issues':'Finished · review issues',
                       'cancelled':'Paused · saved work retained', 'failed':'Stopped · review error'}.get(status, status)
        self.running = False; self.ended = self.clock()

    def update(self, event):
        if not isinstance(event, dict): return
        kind = event.get('type', '')
        stage = event.get('stage', self.stage)
        message = str(event.get('message', ''))
        if message: self.message = message
        if kind == 'run_started':
            self.reset(event.get('stages', [])); return
        if kind == 'files_planned':
            self.files = {str(p):'Waiting' for p in event['files']}; return
        if kind == 'file_started':
            self.file = str(event['input']); self.files[self.file] = 'Running'
            self.file_started = self.clock(); return
        if kind == 'file_finished':
            state = event.get('status', 'completed')
            self.files[str(event['input'])] = {'completed':'Completed', 'failed':'Failed',
                'completed_with_issues':'Review issues', 'cancelled':'Paused'}.get(state, state)
            if self.file_started is not None and state == 'completed' and not event.get('reused_only') and not event.get('reused_work'):
                self.file_times.append(self.clock() - self.file_started)
            return
        if kind == 'stage_started':
            self.stage = stage; self.status = 'Processing'
            self.stage_results[(self.file or str(event.get('input','')),stage)] = 'running'
            self._phase('Preparing stage', 0, 0, 'items'); self.workers = None
            return
        if kind == 'stage_finished':
            state = event.get('status', 'completed')
            self.stage_results[(str(event.get('input', self.file)), stage)] = state
            if stage != self.stage: return
            self.phase = {'cached':'Reused verified results', 'skipped':'Skipped',
                          'no_accepted_traces':'No accepted traces', 'failed':'Failed',
                          'cancelled':'Paused', 'completed_with_issues':'Finished · review issues'}.get(state, 'Saved')
            if state in ('completed','cached'): self.current = self.total
            return
        if kind == 'pause_requested':
            self.status = 'Pausing · waiting for a safe checkpoint'; return
        if kind == 'run_finished':
            self.finish(event.get('status', 'completed')); return
        if stage != self.stage: return  # late/unrelated messages cannot reset a bar
        if 'workers' in event: self.workers = event['workers']
        if stage == 'acceptreject':
            if kind == 'start':
                self._phase('Evaluating component groups', int(event.get('completed_groups',0)), int(event.get('total_groups',0)), 'component groups')
                self.already = self.current
            elif kind == 'group_saved':
                self.groups.add(event.get('group_key')); self.current = min(self.total, self.already + max(len(self.groups),sum(self.worker_groups.values())))
            elif kind == 'worker_groups_finished':
                self.worker_groups[event.get('worker_id')] = int(event.get('completed_groups',0))
                self.current = min(self.total, self.already + max(len(self.groups),sum(self.worker_groups.values())))
                if self.total and self.current == self.total: self.phase = 'Publishing decisions and figures'
            elif kind == 'prepare': self.phase = 'Preparing event metadata'
        elif stage == 'filtering':
            if kind == 'start':
                self._phase('Filtering waveforms', int(event.get('already_completed',0)), int(event.get('total',0)), 'waveforms')
                self.already = self.current
            elif kind in ('trace_finished', 'waveform_saved'):
                pair = (event.get('event_id'), event.get('trace_id'))
                self.computed.add(pair)
                if kind == 'waveform_saved': self.saved.add(pair)
                self.current = min(self.total, self.already + len(self.computed))
                if self.total and self.current == self.total:
                    self.phase = 'Saving and verifying HDF5 / figures'
        elif stage == 'sanity' and event.get('total') is not None:
            if self.unit != 'record groups': self._phase('Checking records', 0, int(event['total']), 'record groups')
            self.issue_count=int(event.get('failed_groups',0))
            self.current = int(event.get('current',0))+self.issue_count; self.total = int(event['total'])
        elif stage == 'ingestion' and event.get('total'):
            phase = ('Loading responses' if message.startswith('Response inventory') else
                     'Checking record identities' if message.startswith('Checking record identities') else
                     'Converting raw files')
            unit = 'response files' if phase == 'Loading responses' else 'raw files'
            total = int(event['total']); current = max(0, int(event.get('current',0))-1)
            if phase != self.phase: self._phase(phase, current, total, unit)
            else: self.current = max(self.current, current); self.total = total

    def snapshot(self):
        now = self.ended if self.ended is not None else self.clock()
        done = sum(v not in ('Waiting','Running','Paused') for v in self.files.values())
        errors = sum(v in ('Failed','Review issues') for v in self.files.values())
        remaining = max(0, self.total-self.current)
        advanced = self.current-self.baseline
        elapsed = max(0, now-self.phase_started)
        eta = 'Estimating after more work completes'
        if not self.running: eta = '—'
        elif self.total and not remaining: eta = 'Finalizing saved outputs'
        elif advanced >= 2 and elapsed >= 10:
            eta = 'About '+duration(elapsed/advanced*remaining)
        run_eta = 'Available after 2 completed files'
        if not self.running: run_eta = '—'
        elif len(self.file_times) >= 2:
            active_elapsed = now-self.file_started if self.file_started is not None and self.files.get(self.file)=='Running' else 0
            estimate = sum(self.file_times)/len(self.file_times)*(len(self.files)-done)-active_elapsed
            run_eta = 'About '+duration(max(0,estimate)) if estimate > 0 else 'Updating estimate'
        return dict(done=done, total_files=len(self.files), remaining_files=len(self.files)-done, errors=errors,
                    elapsed=duration(now-self.started), eta=eta, run_eta=run_eta, remaining=remaining)


def build_progress_panel(parent=None):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QProgressBar,
                                  QGroupBox,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView)

    class ProgressPanel(QGroupBox):
        def __init__(self):
            super().__init__('Processing progress', parent)
            self.model = RunProgress(); self.model.running = False; self.model.status = 'Ready'; self.model.ended=self.model.started
            self.queue_dirty=True; self.queue_cache={}
            layout = QVBoxLayout(self)
            self.heading = QLabel('Ready'); self.heading.setStyleSheet('font-size:13pt;font-weight:600;'); layout.addWidget(self.heading)
            self.context = QLabel('Start a run to track files, stages and saved results.'); self.context.setWordWrap(True); layout.addWidget(self.context)
            cards = QGridLayout(); self.labels = {}
            for i,(key,title) in enumerate([('files','HDF5 FILES'),('records','CURRENT STAGE'),('elapsed','ELAPSED'),('eta','CURRENT STAGE REMAINING')]):
                box=QWidget(); b=QVBoxLayout(box); b.setContentsMargins(8,5,8,5)
                t=QLabel(title); t.setStyleSheet('font-size:8pt;color:#4b6075;'); b.addWidget(t)
                value=QLabel('—'); value.setWordWrap(True); value.setStyleSheet('font-size:10pt;font-weight:600;'); b.addWidget(value)
                self.labels[key]=value; cards.addWidget(box,0,i)
            layout.addLayout(cards)
            self.files_bar=QProgressBar(); self.files_bar.setAccessibleName('Finished HDF5 files'); layout.addWidget(self.files_bar)
            self.stage_bar=QProgressBar(); self.stage_bar.setAccessibleName('Current stage progress'); layout.addWidget(self.stage_bar)
            self.detail=QLabel(); self.detail.setWordWrap(True); layout.addWidget(self.detail)
            self.stages_label=QLabel(); self.stages_label.setWordWrap(True); layout.addWidget(self.stages_label)
            self.queue=QTableWidget(0,2); self.queue.setHorizontalHeaderLabels(['HDF5 file','Status'])
            self.queue.setEditTriggers(QAbstractItemView.NoEditTriggers); self.queue.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.queue.setAccessibleName('Processing file queue'); self.queue.verticalHeader().hide()
            self.queue.setMinimumHeight(105); self.queue.setMaximumHeight(160); layout.addWidget(self.queue)
            self.note=QLabel('Time estimates adapt to completed work; record lengths and selected methods affect the remaining time.')
            self.note.setWordWrap(True); self.note.setStyleSheet('font-size:9pt;color:#4b6075;'); layout.addWidget(self.note)
            self.timer=QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.refresh); self.timer.start()
            self.refresh()

        def begin(self, stages): self.model.reset(stages); self.queue_dirty=True; self.refresh()
        def update_event(self, event):
            if not self.model.running and not (isinstance(event,dict) and event.get('type')=='run_started'): return
            self.model.update(event)
            if isinstance(event,dict) and event.get('type') in ('run_started','files_planned','file_started','file_finished','stage_started','stage_finished'):
                self.queue_dirty=True
                self.refresh()
        def finish(self, status): self.model.finish(status); self.refresh()

        def refresh(self):
            m=self.model; s=m.snapshot()
            self.heading.setText(m.status)
            context=(Path(m.file).name+'  ·  ' if m.file else '')+STAGES.get(m.stage,'Preparing inputs')
            if m.workers is not None: context+=f'  ·  {m.workers} workers'
            self.context.setText(context)
            self.context.setToolTip(m.file)
            self.labels['files'].setText(f"{s['done']} / {s['total_files']} finished · {s['remaining_files']} left" if s['total_files'] else 'Determined after HDF5 preparation')
            self.labels['records'].setText(f'{m.current} / {m.total} {m.unit}\n{s["remaining"]} left' if m.total else m.phase)
            self.labels['elapsed'].setText(s['elapsed']); self.labels['eta'].setText(s['eta'])
            self.files_bar.setRange(0,max(1,s['total_files'])); self.files_bar.setValue(s['done'])
            self.files_bar.setFormat(f"HDF5 files: {s['done']} of {s['total_files']} finished" if s['total_files'] else 'HDF5 file list pending')
            self.stage_bar.setRange(0,m.total if m.total else (0 if m.running else 1)); self.stage_bar.setValue(m.current)
            self.stage_bar.setFormat(f'{m.phase}: %v / %m (%p%)' if m.total else m.phase)
            detail=f'Whole-run remaining: {s["run_eta"]}'
            if m.stage=='filtering' and m.total: detail+=f'  ·  {min(m.total,m.already+len(m.saved))} / {m.total} waveforms saved and verified'
            if s['errors']: detail+=f'  ·  {s["errors"]} file(s) need review'
            if m.issue_count: detail+=f'  ·  {m.issue_count} failed group(s); see Activity log'
            self.detail.setText(detail)
            self.stages_label.setText('Selected stages: '+'  →  '.join(STAGES.get(x,x) for x in m.stages))
            if self.queue_dirty:
                self.queue_dirty=False
                stages=[x for x in m.stages if x!='ingestion']
                self.queue.setColumnCount(2+len(stages))
                names={'acceptreject':'Accept / reject','filtering':'Filtering','sanity':'Sanity checks'}
                self.queue.setHorizontalHeaderLabels(['HDF5 file','Status']+[names.get(x,x) for x in stages])
                self.queue.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
                for col in range(1,2+len(stages)): self.queue.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeToContents)
                if self.queue.rowCount()!=len(m.files): self.queue.setRowCount(len(m.files))
                labels={'completed':'Done','cached':'Reused','completed_with_issues':'Review','failed':'Failed','cancelled':'Paused','no_accepted_traces':'None accepted','not_run':'Not run','skipped':'Skipped','running':'Running'}
                for row,(path,status) in enumerate(m.files.items()):
                    states=[m.stage_results.get((path,x),'Waiting') for x in stages]
                    for col,text in enumerate([Path(path).name,status]+[labels.get(s,s) for s in states]):
                        cell=self.queue.item(row,col)
                        if cell is None: cell=QTableWidgetItem(); self.queue.setItem(row,col,cell)
                        if cell.text()!=text: cell.setText(text)
                        cell.setToolTip(path if col==0 else text)

    return ProgressPanel()
