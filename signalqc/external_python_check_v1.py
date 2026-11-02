"""Dependency-only check, executed by the selected installed interpreter."""
import importlib
from importlib.metadata import distributions
from pathlib import Path
import re
import sys

root, app, requirements = map(Path, sys.argv[1:4])
root=root.resolve();sys.path.insert(0,str(app))
normalize=lambda name: re.sub(r'[-_.]+','-',name).lower()
actual={normalize(d.metadata['Name']):d.version for d in distributions(path=[str(root)])}
failures=[];count=0
for line in requirements.read_text(encoding='utf-8').splitlines():
    if not line.strip() or line.lstrip().startswith('#'):continue
    name,separator,version=line.strip().partition('==')
    if not separator:raise RuntimeError('Unpinned setup requirement: '+line)
    count+=1
    if actual.get(normalize(name))!=version:failures.append(line+' (found '+str(actual.get(normalize(name)))+')')
if failures:raise RuntimeError('Python packages are missing, inaccessible or have unexpected versions: '+', '.join(failures))
modules=('numpy','scipy','pandas','h5py','obspy','openpyxl','xlrd','matplotlib','PySide6','pyqtgraph')
for name in modules:
    module=importlib.import_module(name)
    if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(root):
        raise RuntimeError(name+' was not loaded from the prepared package folder.')
from signalqc.gui import Window
print('Verified '+str(count)+' Python package versions and scientific/interface imports; interpreter: '+sys.executable)
