"""Install a private dependency environment for existing Python/R executables.

The launcher and setup UI use only the Python standard library. No interpreter
is installed, replaced or copied. Scientific processing is never run by setup.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from .user_paths import user_state_dir, console_python

APP = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

@contextmanager
def setup_lock(folder):
    """A crash releases this lock; a stale filename does not block later setup."""
    import msvcrt
    with (Path(folder)/'.setup.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:
            raise RuntimeError('Another setup is using this environment. Wait for it to finish, then try again.') from exc
        try:yield
        finally:
            handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clean_path(value):
    return str(Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser().resolve())


def clean_environment():
    env = os.environ.copy()
    for key in ('PYTHONHOME','PYTHONPATH','R_HOME','R_LIBS','R_LIBS_USER','R_LIBS_SITE',
                'R_PROFILE_USER','R_ENVIRON_USER','RETICULATE_PYTHON','SIGNALQC_EXTERNAL_CONFIG',
                'SIGNALQC_R_LIBRARY','SIGNALQC_R_IM_LIBRARY','SIGNALQC_PYTHON','LC_ALL','LC_CTYPE','LANG'):
        env.pop(key, None)
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               PYTHONIOENCODING='utf-8', R_PROFILE_USER=os.devnull, R_ENVIRON_USER=os.devnull)
    return env


def environment_for(settings):
    env = clean_environment()
    env.update(PYTHONPATH=settings['python_site'], RETICULATE_PYTHON=settings['python'],
               SIGNALQC_R_LIBRARY=settings['r_library'],
               R_LIBS_USER=settings['r_library'], OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    return env


def run_capture(command, env=None, timeout=60):
    result = subprocess.run(command, cwd=APP, env=env or clean_environment(), capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=FLAGS)
    if result.returncode:
        raise RuntimeError((result.stdout+'\n'+result.stderr).strip()[-6000:] or 'The selected program could not run.')
    return result.stdout


def inspect_interpreters(python, rscript):
    python = console_python(clean_path(python)); rscript = clean_path(rscript)
    if Path(rscript).name.lower()!='rscript.exe':
        raise ValueError('For R, select Rscript.exe from the installed R bin folder (not R.exe or Rgui.exe).')
    for label, path in [('Python',python),('Rscript',rscript)]:
        if not Path(path).is_file(): raise ValueError(label+' executable not found: '+path)
        if Path(path).is_relative_to(APP/'runtime'):
            raise ValueError('Select the computer\'s installed '+label+', outside this app\'s runtime folder. Use the normal launcher for the bundle.')
    code = ('import sys,sysconfig,struct,platform,json; print(json.dumps(dict(version=list(sys.version_info[:3]),'
            'bits=struct.calcsize("P")*8,platform=sys.platform,machine=platform.machine(),implementation=sys.implementation.name,'
            'isolated=bool(sys.flags.isolated),free_threaded=bool(sysconfig.get_config_var("Py_GIL_DISABLED")))))')
    info = json.loads(run_capture([python,'-s','-c',code]))
    if info['platform']!='win32' or info['bits']!=64 or info['machine'].lower() not in ('amd64','x86_64'):
        raise ValueError('This setup supports 64-bit Windows x64 Python and R.')
    if info['implementation']!='cpython' or info['free_threaded'] or info['isolated']:
        raise ValueError('Choose a standard CPython installation, not an embedded, isolated or free-threaded build.')
    if tuple(info['version'][:2]) not in ((3,11),(3,12),(3,13),(3,14)):
        raise ValueError('Choose Python 3.11, 3.12, 3.13 or 3.14. Required binary packages must be available for the selected version.')
    output = run_capture([rscript,'--vanilla',str(APP/'signalqc/external_probe_v1.R')])
    version, bits, arch = output.strip().split('|')
    if bits!='8' or arch not in ('x86_64','x64') or tuple(map(int,version.split('.')[:2])) < (4,5):
        raise ValueError('Choose 64-bit R 4.5 or later. R 4.5.1 is the bundled reference; other versions must pass package validation.')
    return python, rscript, info, version


def default_rscript():
    paths=[]
    for root in (Path(os.environ.get('ProgramFiles','C:/Program Files'))/'R',
                 Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'Programs/R'):
        paths.extend(root.glob('*/bin/Rscript.exe'))
    return str(sorted(paths)[-1]) if paths else ''


def file_hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024): result.update(block)
    return result.hexdigest()


class Setup:
    def __init__(self, root, emit=print, stop=None):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.emit=emit; self.stop=stop or threading.Event()
        self.log=self.root/'setup.log'

    def say(self, message):
        text=str(message).rstrip()
        with self.log.open('a',encoding='utf-8') as stream: stream.write(text+'\n')
        self.emit(text)

    def checkpoint(self):
        if self.stop.is_set(): raise RuntimeError('Setup stopped after the current step. Run this launcher again to continue.')

    def command(self, command, env, timeout=2400):
        self.checkpoint()
        # Redirect to a file, so inherited subprocess handles cannot block a pipe reader.
        output=self.root/('command-'+uuid.uuid4().hex+'.log')
        deadline=time.monotonic()+timeout
        with output.open('wb') as sink:
            child=subprocess.Popen(command,cwd=APP,env=env,stdout=sink,stderr=subprocess.STDOUT,creationflags=FLAGS)
            position=0
            while True:
                time.sleep(.15)
                with output.open('rb') as reader:
                    reader.seek(position); chunk=reader.read(); position=reader.tell()
                if chunk: self.say(chunk.decode('utf-8',errors='replace'))
                if child.poll() is not None:
                    with output.open('rb') as reader:
                        reader.seek(position); final=reader.read()
                    if final:self.say(final.decode('utf-8',errors='replace'))
                    break
                if time.monotonic()>deadline:
                    # This is only the setup subprocess created above, never a user's R/Python session.
                    child.terminate(); child.wait(timeout=20)
                    raise RuntimeError('Setup timed out. Check internet access and the setup log: '+str(self.log))
        if child.returncode:
            raise RuntimeError('Dependency setup/check failed. No app was started. See '+str(output)+
                '\nCheck the log for unavailable versions, network access or IT restrictions. Python 3.12 and R 4.5 are the reference environment.')
        self.checkpoint()

    def pip_wheel(self):
        cache=self.root/'downloads'; cache.mkdir(exist_ok=True)
        saved=cache/'pip_download.json'
        if saved.is_file():
            entry=json.loads(saved.read_text()); path=cache/entry['filename']
            if path.is_file() and file_hash(path)==entry['sha256']:return path
        self.say('Downloading the Python package installer from PyPI (no global installation).')
        with urllib.request.urlopen('https://pypi.org/pypi/pip/json',timeout=60) as response:
            data=json.load(response)
        entry=next(x for x in data['urls'] if x['filename'].endswith('-py3-none-any.whl'))
        from urllib.parse import urlparse
        if urlparse(entry['url']).scheme!='https' or urlparse(entry['url']).hostname!='files.pythonhosted.org':
            raise RuntimeError('Unexpected PyPI download address.')
        expected=entry['digests']['sha256']
        if not re.fullmatch('[a-f0-9]{64}',expected):raise RuntimeError('Invalid PyPI download checksum.')
        path=cache/Path(entry['filename']).name
        with urllib.request.urlopen(entry['url'],timeout=60) as response, path.open('wb') as stream:
            total=0
            while chunk:=response.read(1024*1024):
                total+=len(chunk)
                if total>50*1024*1024:raise RuntimeError('Unexpected package-installer download size.')
                stream.write(chunk)
        if file_hash(path)!=expected:raise RuntimeError('Python package-installer checksum verification failed.')
        atomic_json(saved,dict(filename=path.name,sha256=expected))
        return path

    def check(self, settings):
        env=environment_for(settings)
        requirement=APP/('requirements-python311.txt' if settings['identity']['python_version'][1]==11 else 'runtime/requirements-portable.lock.txt')
        self.command([settings['python'],'-s','-B',str(APP/'signalqc/external_python_check_v1.py'),
                      settings['python_site'],str(APP),str(requirement)],env,120)
        self.command([settings['rscript'],'--vanilla',str(APP/'signalqc/external_packages_v1.R'),'check',
                str(APP/'runtime/setup_packages/R-packages.tsv'),settings['r_library'],
                str(APP),settings['python']],env,180)

    def prepare(self, python, rscript):
        self.say('1 / 4  Checking the selected installed Python and R.')
        python,rscript,info,rversion=inspect_interpreters(python,rscript)
        requirement=APP/('requirements-python311.txt' if info['version'][1]==11 else 'runtime/requirements-portable.lock.txt')
        archives=json.loads((APP/'runtime/setup_packages/checksums.json').read_text())
        for name,digest in archives.items():
            if file_hash(APP/'runtime/setup_packages'/name)!=digest:raise RuntimeError('Included setup package failed its integrity check: '+name)
        identity=dict(python=python,python_version=info['version'],rscript=rscript,r_version=rversion,
            requirements=file_hash(requirement),setup=file_hash(APP/'signalqc/external_packages_v1.R'),packages=archives)
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20]
        folder=self.root/'environments'/key; folder.mkdir(parents=True,exist_ok=True)
        config=folder/'environment.json'
        settings=dict(schema=1,ready=False,python=python,rscript=rscript,python_site=str(folder/'python-packages'),
            r_library=str(folder/'R-library'),identity=identity)
        # Serialize setup per environment; Windows releases this lock even after an interrupted launcher.
        with setup_lock(folder):
            if config.is_file() and any(Path(settings['python_site']).glob('*.dist-info')):
                self.say('Saved setup found. Checking it without downloading packages.')
                try:
                    self.check(settings)
                    return self.finish(settings,config)
                except RuntimeError as exc:
                    self.say('The saved environment needs repair: '+str(exc))
            for name in ('python_site','r_library'):Path(settings[name]).mkdir(exist_ok=True)
            atomic_json(config,settings)
            self.say('2 / 4  Installing the complete Python dependency set into '+settings['python_site'])
            pip=self.pip_wheel(); env=clean_environment(); env['PYTHONPATH']=str(pip)
            self.command([python,'-s','-B','-m','pip','--isolated','--disable-pip-version-check','install',
                '--index-url','https://pypi.org/simple','--only-binary=:all:','--no-input','--no-cache-dir',
                '--no-compile','--upgrade','--ignore-installed','--target',settings['python_site'],'-r',str(requirement)],env)
            self.say('3 / 4  Installing R dependencies, in the private package library.')
            self.command([rscript,'--vanilla',str(APP/'signalqc/external_packages_v1.R'),'install',
                str(APP/'runtime/setup_packages/R-packages.tsv'),settings['r_library'],
                str(APP),python],environment_for(settings))
            self.say('4 / 4  Checking every stage\'s dependencies and the R-to-Python connection.')
            self.check(settings)
            return self.finish(settings,config)

    def finish(self,settings,config):
        self.checkpoint(); settings['ready']=True
        atomic_json(config,settings)
        atomic_json(self.root/'preferences.json',dict(python=settings['python'],rscript=settings['rscript']))
        self.say('Setup verified. Future launches reuse these packages. No waveform processing was run.')
        return config

    def launch(self,config):
        settings=json.loads(Path(config).read_text()); env=environment_for(settings)
        env['SIGNALQC_EXTERNAL_CONFIG']=str(config)
        env['SIGNALQC_USER_DIR']=str(self.root/'app-state')
        ready=self.root/('gui-'+uuid.uuid4().hex+'.ready')
        logfile=self.root/'app_startup.log'
        env.update(SIGNALQC_STARTUP_READY_FILE=str(ready),SIGNALQC_STARTUP_LOG=str(logfile))
        with logfile.open('ab') as log:
            child=subprocess.Popen([settings['python'],'-s','-B',str(APP/'app.py')],cwd=APP,env=env,
                stdout=log,stderr=subprocess.STDOUT,creationflags=FLAGS)
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if ready.exists():
                ready.unlink(); self.say('SignalQC is open using the selected installed Python and R.'); return child.pid
            if child.poll() is not None:raise RuntimeError('The interface could not start. See '+str(logfile))
            time.sleep(.2)
        raise RuntimeError('The interface has not confirmed startup yet. Check '+str(logfile)+' before launching another copy.')


def show_setup(python,rscript,folder):
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox
    root=tk.Tk(); root.title('SignalQC - Installed Python and R'); root.geometry('850x620'); root.minsize(700,490)
    root.option_add('*Font',('Segoe UI',10))
    style=ttk.Style(root);style.theme_use('clam')
    style.configure('.',background='#f4f7fb',foreground='#17324d')
    style.configure('TEntry',fieldbackground='white',padding=5)
    style.configure('TButton',padding=(12,7))
    root.columnconfigure(0,weight=1); root.rowconfigure(0,weight=1)
    frame=ttk.Frame(root,padding=20); frame.grid(sticky='nsew'); frame.columnconfigure(1,weight=1); frame.rowconfigure(6,weight=1)
    ttk.Label(frame,text='Use installed Python and R',font=('Segoe UI',17,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
    ttk.Label(frame,text='First use downloads the required packages into your user folder. Later runs reuse them.\n'
        'Select python.exe and Rscript.exe from the computer\'s installed programs.',wraplength=760).grid(row=1,column=0,columnspan=3,sticky='w',pady=(0,15))
    values=[tk.StringVar(value=python),tk.StringVar(value=rscript)]; controls=[]
    for row,(label,value) in enumerate(zip(('Python executable','Rscript executable'),values),2):
        ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=5)
        entry=ttk.Entry(frame,textvariable=value); entry.grid(row=row,column=1,sticky='ew',pady=5); controls.append(entry)
        def browse(v=value):
            selected=filedialog.askopenfilename(parent=root,title='Select executable',filetypes=[('Windows executable','*.exe')])
            if selected:v.set(selected)
        button=ttk.Button(frame,text='Browse...',command=browse); button.grid(row=row,column=2,padx=(8,0)); controls.append(button)
    ttk.Label(frame,text='Internet is needed for first setup. No administrator elevation is requested.\n'
        'IT must permit package downloads and execution of the selected programs and package libraries.',wraplength=760).grid(row=4,column=0,columnspan=3,sticky='w',pady=12)
    status=tk.StringVar(value='Ready to set up. No processing will start automatically.')
    ttk.Label(frame,textvariable=status,wraplength=760).grid(row=5,column=0,columnspan=3,sticky='w')
    box=ttk.Frame(frame); box.grid(row=6,column=0,columnspan=3,sticky='nsew',pady=10); box.rowconfigure(0,weight=1); box.columnconfigure(0,weight=1)
    log=tk.Text(box,wrap='word',height=12,font=('Consolas',10),state='disabled'); log.grid(row=0,column=0,sticky='nsew')
    scroll=ttk.Scrollbar(box,command=log.yview); scroll.grid(row=0,column=1,sticky='ns'); log.configure(yscrollcommand=scroll.set)
    progress=ttk.Progressbar(frame,mode='indeterminate'); progress.grid(row=7,column=0,columnspan=3,sticky='ew')
    actions=ttk.Frame(frame);actions.grid(row=8,column=0,columnspan=3,sticky='e',pady=(12,0))
    events=queue.Queue(); stop=threading.Event(); busy=[False]
    def start():
        paths=[v.get() for v in values];stop.clear();busy[0]=True
        for control in controls+[start_button]:control.configure(state='disabled')
        stop_button.configure(state='normal');progress.start();status.set('Preparing the environment...')
        def work():
            try:
                setup=Setup(folder,lambda text:events.put(('log',text)),stop)
                config=setup.prepare(*paths);setup.launch(config);events.put(('done',None))
            except Exception as exc:events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()
    def request_stop():
        stop.set();status.set('Stopping after the current install/check step finishes...');stop_button.configure(state='disabled')
    stop_button=ttk.Button(actions,text='Stop after current step',command=request_stop,state='disabled');stop_button.pack(side='left',padx=(0,8))
    start_button=ttk.Button(actions,text='Set up and open SignalQC',command=start);start_button.pack(side='left')
    def poll():
        while True:
            try:kind,text=events.get_nowait()
            except queue.Empty:break
            if kind=='log':
                log.configure(state='normal');log.insert('end',text+'\n');log.see('end');log.configure(state='disabled')
                if re.match(r'[1-4] / 4',text):status.set(text)
            else:
                busy[0]=False;progress.stop();stop_button.configure(state='disabled')
                for control in controls+[start_button]:control.configure(state='normal')
                if kind=='done':root.destroy();return
                status.set('Setup did not complete. Details are shown below and saved in the setup log.')
                log.configure(state='normal');log.insert('end','\n'+text+'\n');log.see('end');log.configure(state='disabled')
                messagebox.showerror('SignalQC setup',text,parent=root)
        root.after(150,poll)
    def close():
        if busy[0]:request_stop();messagebox.showinfo('Setup is finishing its current step','Wait for the current step to finish, then close this window.',parent=root)
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);root.after(150,poll);root.mainloop();return 0


def main(default_python='',default_r=''):
    parser=argparse.ArgumentParser(description='Set up SignalQC with installed Python and R; no bundled executables are used.')
    parser.add_argument('--python');parser.add_argument('--rscript');parser.add_argument('--state-dir')
    parser.add_argument('--console',action='store_true');parser.add_argument('--setup-only',action='store_true')
    args=parser.parse_args()
    folder=Path(args.state_dir) if args.state_dir else user_state_dir()/'installed-runtimes'
    prefs={}
    try:prefs=json.loads((folder/'preferences.json').read_text())
    except (OSError,ValueError):pass
    python=args.python or default_python or prefs.get('python') or console_python()
    rscript=args.rscript or default_r or prefs.get('rscript') or default_rscript()
    if not args.console and not args.setup_only:
        try:return show_setup(python,rscript,folder)
        except ImportError:print('This Python has no Tkinter. Continuing with text prompts; PyCharm is not required.')
        except Exception as exc:
            if type(exc).__name__!='TclError':raise
            print('The setup window is unavailable in this Python installation. Continuing with text prompts: '+str(exc))
    if not args.python and not default_python:
        python=input('Installed python.exe ['+python+']: ').strip() or python
    if not args.rscript and not default_r:
        rscript=input('Installed Rscript.exe ['+rscript+']: ').strip() or rscript
    try:
        setup=Setup(folder);config=setup.prepare(python,rscript)
        if not args.setup_only:setup.launch(config)
        return 0
    except Exception as exc:
        print('SignalQC setup did not complete:\n'+str(exc),file=sys.stderr);return 1
