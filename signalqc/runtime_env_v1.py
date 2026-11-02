"""Runtime routing for the optional installed-interpreter launcher (stdlib only)."""
from __future__ import annotations
import json
import os
from pathlib import Path


def external_settings():
    path = os.environ.get('SIGNALQC_EXTERNAL_CONFIG')
    if not path:
        return None
    settings = json.loads(Path(path).read_text(encoding='utf-8'))
    if settings.get('schema') != 1 or not settings.get('ready'):
        raise RuntimeError('The installed-interpreter environment is not ready. Run its setup launcher again.')
    for key in ('python', 'rscript', 'python_site', 'r_library'):
        if not Path(settings[key]).exists():
            raise RuntimeError('The installed-interpreter environment is missing '+key+'. Run its setup launcher again.')
    return settings


def runtime_config(config):
    """An explicitly launched external session also overrides saved project runtimes."""
    config = dict(config)
    settings = external_settings()
    if settings:
        config.update(python_executable=settings['python'], rscript_executable=settings['rscript'])
    return config


def runtime_environment(env=None):
    env = dict(os.environ if env is None else env)
    settings = external_settings()
    if settings:
        for key in ('PYTHONHOME', 'R_HOME', 'R_LIBS', 'R_LIBS_SITE'):
            env.pop(key, None)
        env.update(PYTHONPATH=settings['python_site'], PYTHONNOUSERSITE='1',
                   PYTHONDONTWRITEBYTECODE='1', RETICULATE_PYTHON=settings['python'],
                   SIGNALQC_R_LIBRARY=settings['r_library'],
                   R_LIBS_USER=settings['r_library'], R_PROFILE_USER=os.devnull, R_ENVIRON_USER=os.devnull)
    return env
