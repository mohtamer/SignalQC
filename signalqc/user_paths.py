"""Writable, per-user state; no settings or logs need the installation directory."""
import os
from pathlib import Path
import sys
import tempfile
import uuid


def user_state_dir():
    override = os.environ.get('SIGNALQC_USER_DIR')
    local = os.environ.get('LOCALAPPDATA')
    candidates = [Path(override)] if override else []
    candidates += [Path(local) / 'SignalQC'] if local else [Path.home() / '.signalqc']
    candidates += [Path(tempfile.gettempdir()) / 'SignalQC']
    errors = []
    for folder in candidates:
        probe = folder / ('.signalqc_write_' + uuid.uuid4().hex + '.tmp')
        created = False
        try:
            folder.mkdir(parents=True, exist_ok=True)
            # On Windows tempfile may retry PermissionError up to TMP_MAX when
            # an ACL/sandbox disagrees with os.access. One exclusive probe is
            # sufficient; denied folders must fall through promptly.
            with probe.open('xb'):
                created = True
            probe.unlink()
            created = False
            return folder
        except OSError as exc:
            errors.append(str(exc))
        finally:
            if created:
                try: probe.unlink(missing_ok=True)
                except OSError: pass
    raise OSError('No writable SignalQC settings folder: ' + '; '.join(errors))


def console_python(executable=None):
    """Reticulate and computational jobs need python.exe, even after a .pyw launch."""
    path = Path(executable or sys.executable)
    if path.name.lower() == 'pythonw.exe':
        sibling = path.with_name('python.exe')
        if sibling.is_file():
            return str(sibling)
    return str(path)


def default_projects_dir():
    folder = user_state_dir() / 'projects'
    folder.mkdir(parents=True, exist_ok=True)
    return folder
