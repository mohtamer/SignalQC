from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .user_paths import console_python
from .method_settings_v1 import method_defaults

APP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = APP_ROOT.parent
CHECKS = {
    '1': 'Extreme PGA', '2': 'Component amplitude ratios',
    '3': 'Final displacement and velocity', '4': 'Final / peak displacement',
    '5': 'Multiple events', '6': 'ADC clipping', '7': 'HH / HN spectral consistency',
}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def filesystem_path(path):
    """Use extended Windows paths internally; keep public paths unchanged."""
    path = Path(path)
    if os.name != 'nt': return path
    name = str(path.absolute())
    if name.startswith('\\\\?\\'): return Path(name)
    if name.startswith('\\\\'): return Path('\\\\?\\UNC\\' + name[2:])
    return Path('\\\\?\\' + name)


def atomic_json(path, value):
    path = filesystem_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A bounded temporary name also protects valid near-255-character filenames.
    temp = path.with_name('.signalqc_' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def defaults():
    from .ncm_data_v1 import ncm_defaults
    r = sorted(Path('C:/Program Files/R').glob('*/bin/Rscript.exe'))
    bundled_r = APP_ROOT/'runtime'/'R'/'bin'/'Rscript.exe'
    if bundled_r.is_file(): r = [bundled_r]
    matlab = sorted(Path('C:/Program Files/MATLAB').glob('*/bin/matlab.exe'))
    # The launcher already chooses the bundle (or an explicit approved override).
    # Keep processing in that same working interpreter rather than overriding it again.
    python = console_python()
    config = dict(name='Untitled project', raw_paths=[], response_paths=[], hdf5_paths=[],
                event_catalog='', station_catalog='', stage1_metadata='', filtering_metadata='',
                column_mappings={}, hdf5_mappings={}, grouping='monthly', remove_response=True,
                input_units='counts', stages=['acceptreject','filtering','sanity'],
                enabled_checks=['1','2','3','4','6','7'], workers=min(2, os.cpu_count() or 1),
                save_figures=True, resume=True, processed_suffix='processed_2',
                python_executable=python, rscript_executable=str(r[-1]) if r else '',
                matlab_executable=str(matlab[-1]) if matlab else '', default_bits=24,
                waveform_format='auto', event_time_tolerance_s=0, scan_all_files=False,
                **method_defaults())
    config.update(ncm_defaults())
    from .runtime_env_v1 import runtime_config
    return runtime_config(config)


class ProjectLock:
    """Operating-system lock, automatically released if the process exits."""
    def __init__(self, folder):
        self.path = Path(folder) / '.run.lock'
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, 'a+b')
        try:
            if self.path.stat().st_size == 0:
                self.handle.write(b'0'); self.handle.flush()
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close(); self.handle = None
            raise RuntimeError('This project is already running in another application window.') from exc
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            self.handle.close(); self.handle = None

def _paper_project(config):
    """Keep selected paper stages; a legacy IM-only project must select a task."""
    config = dict(config)
    config['stages'] = [s for s in config.get('stages', []) if s != 'intensity']
    if config.get('interface_task') == 'ims':
        config['interface_task'] = 'process'
    config.pop('im_metadata', None)
    config.pop('im_measures', None)
    if 'interface_saved_inputs' in config:
        saved = dict(config['interface_saved_inputs'])
        saved.pop('im_metadata', None)
        if 'processing_stages' in saved:
            saved['processing_stages'] = [s for s in saved['processing_stages'] if s != 'intensity']
        config['interface_saved_inputs'] = saved
    return config


class Project:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = self.folder / 'project.json'
        self.config = defaults()
        if self.file.exists():
            self.config.update(json.loads(self.file.read_text(encoding='utf-8')))
        self.retired_stage_removed = 'intensity' in self.config.get('stages', []) or self.config.get('interface_task') == 'ims'
        self.config = _paper_project(self.config)
        self.config['python_executable'] = console_python(self.config['python_executable'])
        # A project copied from another computer must not keep nonexistent runtime paths.
        # Valid explicitly selected runtimes are retained.
        for key in ('python_executable', 'rscript_executable', 'matlab_executable'):
            if not Path(self.config.get(key, '')).is_file():
                self.config[key] = defaults()[key]
        from .runtime_env_v1 import runtime_config
        self.config = runtime_config(self.config)
        self.db_path = self.folder / 'project.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,stage TEXT,input TEXT,fingerprint TEXT,status TEXT,started TEXT,finished TEXT,outputs TEXT,message TEXT)')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            for column in ('output_fingerprint', 'output_dir'):
                if column not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {column} TEXT DEFAULT ""')
        self.recover()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        # Lock files persist after normal exit; existence does not imply ownership.
        try:
            with ProjectLock(self.folder):
                with self.connect() as db:
                    db.execute("UPDATE jobs SET status='interrupted',message='Previous process ended before completing.' WHERE status='running'")
        except RuntimeError:
            return False
        return True

    def save(self, config=None):
        if config is not None:
            self.config.update(config)
        self.config = _paper_project(self.config)
        self.config['updated_at'] = now()
        atomic_json(self.file, self.config)

    def job(self, stage, source, fingerprint):
        key = hashlib.sha256(f'{stage}\0{source}\0{fingerprint}'.encode()).hexdigest()[:24]
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
        return key, dict(row) if row else None

    def update_job(self, key, stage, source, fingerprint, status, outputs=None, message='', output_dir=''):
        files = [str(Path(p).resolve()) for p in (outputs or [])]
        output_fingerprint = signature(files, {}) if files else ''
        with self.connect() as db:
            db.execute('''INSERT INTO jobs (id,stage,input,fingerprint,status,started,finished,outputs,message,output_fingerprint,output_dir)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                          status=excluded.status,finished=excluded.finished,outputs=excluded.outputs,message=excluded.message,
                          output_fingerprint=excluded.output_fingerprint,output_dir=excluded.output_dir,
                          started=CASE WHEN excluded.status='running' THEN excluded.started ELSE jobs.started END''',
                       (key,stage,str(source),fingerprint,status,now(), now() if status != 'running' else '',json.dumps(files),message,output_fingerprint,str(output_dir)))

    def jobs(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY started DESC')]

def signature(paths, settings):
    entries=[]
    for name in sorted(set(map(str, paths))):
        path=Path(name)
        if path.is_file():
            st=path.stat()
            entries.append((str(path.resolve()),st.st_size,st.st_mtime_ns))
        else:
            entries.append((name,'missing'))
    payload=json.dumps({'files':entries,'settings':settings},sort_keys=True,default=str).encode()
    return hashlib.sha256(payload).hexdigest()
