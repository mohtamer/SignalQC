"""Standard-library-only bootstrap. Never invokes a command shell or requests elevation."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from .user_paths import console_python, user_state_dir

APP_ROOT = Path(__file__).resolve().parents[1]
PROBE_CODE = r'''
import json, sys, traceback
result = {'python': sys.executable, 'version': sys.version.split()[0], 'gui_ready': False}
try:
    if sys.version_info < (3, 11):
        raise RuntimeError('SignalQC requires Python 3.11 or newer; compatible application packages are also required.')
    import numpy, scipy, pandas, h5py, obspy, openpyxl, xlrd, matplotlib
    from signalqc.gui import Window
    result['gui_ready'] = True
except Exception:
    result['error'] = traceback.format_exc()
print(json.dumps(result))
sys.exit(0 if result['gui_ready'] else 1)
'''


def hidden_flags():
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def python_environment():
    env = os.environ.copy()
    # A desktop session/another program may have left another Python's paths here.
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    return env


def candidate_pythons(root=APP_ROOT, explicit=None):
    # An explicitly selected interpreter must not silently be replaced.
    selected = explicit or os.environ.get('SIGNALQC_PYTHON')
    if selected:
        candidates = [selected]
    else:
        candidates = []
        if (root / 'runtime' / 'portable_manifest.json').is_file():
            candidates.append(root / 'runtime' / 'python' / 'python.exe')
        candidates += [root / '.venv' / 'Scripts' / 'python.exe', sys.executable]
        found = shutil.which('python')
        # The Store execution alias can open a store window rather than run Python.
        if found and not any(part.lower() == 'windowsapps' for part in Path(found).parts):
            candidates.append(found)
    seen = set()
    for item in candidates:
        path = Path(console_python(item)).expanduser().resolve()
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            yield path


def probe_python(executable, root=APP_ROOT):
    result = {'candidate': str(executable), 'gui_ready': False}
    if not executable.is_file():
        result['error'] = 'Python executable does not exist.'
        return result
    try:
        completed = subprocess.run(
            [str(executable), '-c', PROBE_CODE], cwd=root, env=python_environment(),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=45, shell=False,
            creationflags=hidden_flags())
        result['returncode'] = completed.returncode
        for line in reversed(completed.stdout.splitlines()):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict) and 'gui_ready' in payload:
                    result.update(payload)
                    break
            except ValueError:
                continue
        result['gui_ready'] = bool(result['gui_ready'] and completed.returncode == 0)
        if not result['gui_ready']:
            result.setdefault('error', (completed.stderr or completed.stdout)[-6000:] or
                              'Python exited without a valid dependency check.')
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['error'] = str(exc)
    return result


def diagnose(root=APP_ROOT, explicit=None):
    attempts = []
    chosen = None
    for candidate in candidate_pythons(root, explicit):
        result = probe_python(candidate, root)
        attempts.append(result)
        if result['gui_ready']:
            chosen = str(candidate)
            break
    return {'checked_at': datetime.now().isoformat(), 'application': str(root),
            'selected_python': chosen, 'attempts': attempts,
            'scope': 'Python and GUI imports only; launch and R are separate checks.'}


def show_message(title, message, error=True):
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10 if error else 0x40)
    elif sys.stderr:
        print(title + '\n' + message, file=sys.stderr)


def start_gui(executable, arguments, log_path, ready_path, root=APP_ROOT, timeout=60):
    """Return after the GUI acknowledges startup, keeping the application independent."""
    env = python_environment()
    env['SIGNALQC_STARTUP_READY_FILE'] = str(ready_path)
    env['SIGNALQC_STARTUP_LOG'] = str(log_path)
    with log_path.open('a', encoding='utf-8') as log:
        process = subprocess.Popen(
            [str(executable), str(root / 'app.py'), *arguments], cwd=root, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            shell=False, creationflags=hidden_flags())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_path.is_file():
            ready_path.unlink(missing_ok=True)
            return process
        code = process.poll()
        if code is not None:
            raise RuntimeError(f'The interface exited before opening (exit code {code}).')
        time.sleep(0.1)
    # Do not kill a potentially healthy, slow startup. Warn against repeated launches.
    raise RuntimeError('The interface has not confirmed startup after 60 seconds. '
                       'It may still be opening; check before launching again.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Open SignalQC directly through Python.')
    parser.add_argument('project', nargs='?', help='Optional project folder or project.json')
    parser.add_argument('--python', dest='python_executable', help='Use this approved Python executable')
    parser.add_argument('--diagnose', action='store_true', help='Save a diagnostic report without opening the GUI')
    parser.add_argument('--show-report', action='store_true', help='Show the diagnostic report location in a dialog')
    parser.add_argument('--report', help='Diagnostic JSON output path')
    args = parser.parse_args(argv)
    log_path = None
    try:
        state = user_state_dir()
        logs = state / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        token = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        log_path = logs / ('launch-' + token + '.log')
        report = diagnose(explicit=args.python_executable)
        report_path = Path(args.report).expanduser().resolve() if args.report else logs / ('diagnostic-' + token + '.json')
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        if args.diagnose:
            if sys.stdout:
                print(str(report_path))
            if args.show_report or not sys.stdout:
                show_message('SignalQC Python check',
                             ('Python dependencies are ready.' if report['selected_python'] else 'No working SignalQC Python was found.') +
                             '\n\nReport: ' + str(report_path), error=not bool(report['selected_python']))
            return 0 if report['selected_python'] else 1
        if not report['selected_python']:
            raise RuntimeError(
                'No tested Python environment can open SignalQC. Make sure the entire app folder was copied, '
                'including runtime/python and its packages. No package installation should be needed for the complete bundle.\n\n'
                'If Windows says an administrator blocked the included Python, IT must approve it or provide a compatible environment.\n\n'
                'Dependency report: ' + str(report_path) + '\nInstructions: ' + str(APP_ROOT / 'docs' / 'installed_runtime_launcher.txt'))
        project_args = []
        if args.project:
            project = Path(args.project).expanduser().resolve()
            if project.name.lower() == 'project.json':
                project = project.parent
            project_args.append(str(project))
        log_path.write_text('SignalQC GUI startup\nPython: ' + report['selected_python'] + '\n', encoding='utf-8')
        start_gui(report['selected_python'], project_args, log_path, logs / (token + '.ready'))
        return 0
    except Exception:
        detail = traceback.format_exc()
        if log_path:
            try:
                with log_path.open('a', encoding='utf-8') as handle:
                    handle.write(detail)
            except OSError:
                pass
        show_message('SignalQC could not open', str(sys.exc_info()[1]) +
                     ('\n\nStartup log: ' + str(log_path) if log_path else '') +
                     '\n\nThis launcher does not request administrator access or change security settings.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
