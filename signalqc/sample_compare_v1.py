"""Read-only comparison of a completed sample run with the supplied reference."""
from pathlib import Path
import json
import h5py
import numpy as np
import pandas as pd
from .project import APP_ROOT

RTOL=1e-7
ATOL=1e-10
PATH_FIELDS={'source_file','processed_hdf5','hdf5_path','figure_path','figure_avd','figure_fas'}

def _files(folder,pattern):
    return sorted(p for p in Path(folder).rglob(pattern) if 'checkpoints' not in p.parts)

def _run_files(root,stage,pattern):
    """Prefer the completed run manifest over outputs from obsolete attempts."""
    manifest=root/'last_run.json'
    if manifest.is_file():
        report=json.loads(manifest.read_text(encoding='utf-8'))
        if report.get('status')!='completed':
            raise ValueError('This project has not completed its latest run. Finish the sample run before comparing.')
        paths=[]
        for item in report.get('reports',[]):
            if item.get('stage')!=stage or item.get('status') not in ('completed','cached'):continue
            for value in item.get('output_files',[]):
                p=Path(value)
                if not p.is_absolute():p=root/p
                if p.match(pattern):
                    if not p.resolve().is_relative_to(root):
                        # Use this project's copy even if the former location exists.
                        parts=p.parts
                        if 'outputs' in parts:p=root/Path(*parts[parts.index('outputs'):])
                    if not p.resolve().is_relative_to(root):
                        raise ValueError('An output points outside the selected project: '+p.name)
                    if not p.is_file():raise ValueError('A completed output is missing: '+p.name)
                    paths.append(p)
        return sorted(set(paths))
    return _files(root,pattern)

def _missing(v):
    return v is None or (isinstance(v,str) and v.strip()=='') or bool(pd.isna(v))

def _equal(a,b):
    if _missing(a) or _missing(b):return _missing(a) and _missing(b)
    if isinstance(a,(bool,np.bool_)) or isinstance(b,(bool,np.bool_)):return str(a).lower()==str(b).lower()
    if isinstance(a,(int,float,np.number)) and isinstance(b,(int,float,np.number)):
        return bool(np.isclose(a,b,rtol=RTOL,atol=ATOL,equal_nan=True))
    return str(a)==str(b)

def compare_sample(folder):
    root=Path(folder).resolve();reference=APP_ROOT/'test/reference_results'
    if root==reference.resolve() or root.is_relative_to(reference.resolve()):
        raise ValueError('Choose your new run folder, not the supplied reference folder.')
    issues=[];counts={'table_cells':0,'waveform_datasets':0,'waveform_samples':0}
    def issue(message):
        if len(issues)<100:issues.append(message)
    expected=json.loads((APP_ROOT/'test/reference_manifest.json').read_text(encoding='utf-8'))
    event=expected['event_id']
    candidates=[]
    for p in _run_files(root,'results','*_results.xlsx'):
        d=pd.read_excel(p,sheet_name='Results',dtype=object)
        if 'event_id' in d and event in set(d.event_id.astype(str)):candidates.append((p,d))
    if len(candidates)!=1:
        raise ValueError(f'Found {len(candidates)} matching result workbooks. Choose the project folder containing project.json and last_run.json from one completed sample run.')
    result_path,actual=candidates[0]
    if result_path.resolve().is_relative_to(reference.resolve()):
        raise ValueError('Choose your new project folder, not a folder containing the supplied reference results.')
    ref=pd.read_excel(reference/expected['results_workbook'],sheet_name='Results',dtype=object)
    keys=['event_id','trace_id']
    for label,d in [('Reference',ref),('New run',actual)]:
        if not set(keys).issubset(d):raise ValueError(label+' lacks exact event/trace identifiers.')
        if d.duplicated(keys).any():raise ValueError(label+' contains duplicate event/trace identifiers.')
    a=actual.set_index(keys);r=ref.set_index(keys)
    if set(a.index)!=set(r.index):issue('The event/trace identifiers do not match the reference.')
    for col in r.columns:
        if col in PATH_FIELDS or col=='record_number':continue
        if col not in a:issue('Missing result column: '+col);continue
        for key in r.index.intersection(a.index):
            counts['table_cells']+=1
            if not _equal(r.at[key,col],a.at[key,col]):issue(f'{key[1]} / {col}: reference {r.at[key,col]!s}; new {a.at[key,col]!s}')
    candidates=[]
    for p in _run_files(root,'filtering','*_processed_2.h5'):
        with h5py.File(p,'r') as f:
            if event in f:candidates.append(p)
    if len(candidates)!=1:issue(f'Expected one matching processed HDF5; found {len(candidates)}.')
    else:
        with h5py.File(reference/expected['processed_hdf5'],'r') as r,h5py.File(candidates[0],'r') as a:
            rd={};ad={}
            r.visititems(lambda n,o:rd.update({n:o}) if isinstance(o,h5py.Dataset) else None)
            a.visititems(lambda n,o:ad.update({n:o}) if isinstance(o,h5py.Dataset) else None)
            if set(rd)!=set(ad):issue('Processed HDF5 dataset names differ.')
            for k in rd.keys()&ad.keys():
                x=rd[k][...];y=ad[k][...];counts['waveform_datasets']+=1;counts['waveform_samples']+=int(x.size)
                if x.shape!=y.shape or not np.allclose(x,y,rtol=RTOL,atol=ATOL,equal_nan=True):issue('Processed waveform differs: '+k)
    return dict(matches=not issues,issues=issues,counts=counts,rtol=RTOL,atol=ATOL,
                results_workbook=str(result_path),comparison='Decisions/identities exact; numerical tolerance; paths and file timestamps excluded.')

def format_report(report):
    c=report['counts']
    title='Results match the reference within the stated tolerance.' if report['matches'] else 'Differences found; review the details below.'
    return title+f'\n\nCompared {c["table_cells"]:,} result cells, {c["waveform_datasets"]} waveform datasets ({c["waveform_samples"]:,} samples).\nRelative tolerance: {RTOL:g}; absolute tolerance: {ATOL:g}.\n'+report['comparison']+'\n\n'+'\n'.join(report['issues'])
