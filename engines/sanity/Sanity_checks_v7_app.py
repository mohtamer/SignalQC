"""Version 7: parallel sanity checks with a fixed filter initialization profile.

Whole event/network/station/location sets stay together, including all epochs,
channel families and HH/HN pairs. Child processes execute v5; this file performs
only partitioning, lossless table transport, checkpointing and verified merging.
SciPy 1.17.1 uses the exact bundled SciPy 1.18.1 initialization through a
local compatibility adapter. No HDF5 file is opened for writing.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
APP=HERE.parents[1]
sys.path.insert(0,str(APP))
from signalqc.data import read_table

ROW_ID='__signalqc_parallel_row'
CHECK_NAMES={'1':'sc_extreme_pga_fail','2':'sc_suspect_amp_fail','3':'sc_dawood_dispvel_fail',
             '4':'sc_dawood_ratio_fail','5':'sc_multi_event_fail','6':'sc_clip_fail','7':'sc_HH_HN_ratio_fail'}
METRICS=['sc_final_disp_cm','sc_final_vel_cms','sc_disp_ratio_final_max','sc_D5_75',
         'sc_D5_75_sorted','sc_clip_ratio','sc_HH_HN_ratio_med','sc_adc_bits']


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('t'+uuid.uuid4().hex[:8])
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    os.replace(temporary,path)


def filesystem_path(path):
    """Allow deep project folders without requiring a Windows registry change."""
    path=Path(path).resolve()
    text=str(path)
    if os.name=='nt' and not text.startswith('\\\\?\\'):
        text='\\\\?\\UNC\\'+text[2:] if text.startswith('\\\\') else '\\\\?\\'+text
    return Path(text)


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def token(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def _encode(value):
    if value is pd.NA: return {'__type__':'pd.NA'}
    if value is pd.NaT: return {'__type__':'NaT'}
    if isinstance(value,np.generic): return _encode(value.item())
    if isinstance(value,pd.Timestamp): return {'__type__':'Timestamp','value':value.isoformat()}
    if isinstance(value,datetime): return {'__type__':'datetime','value':value.isoformat()}
    if isinstance(value,date): return {'__type__':'date','value':value.isoformat()}
    if isinstance(value,bytes): return {'__type__':'bytes','value':value.hex()}
    if value is None or isinstance(value,(str,int,float,bool)): return value
    raise TypeError('Unsupported table cell type in lossless transport: '+type(value).__name__)


def _decode(value):
    if not isinstance(value,dict): return value
    kind=value['__type__']
    if kind=='pd.NA': return pd.NA
    if kind=='NaT': return pd.NaT
    if kind=='Timestamp': return pd.Timestamp(value['value'])
    if kind=='datetime': return datetime.fromisoformat(value['value'])
    if kind=='date': return date.fromisoformat(value['value'])
    if kind=='bytes': return bytes.fromhex(value['value'])
    raise ValueError('Unsupported serialized table cell type')


def save_frame(path,frame):
    # Python's JSON float conversion round-trips binary64. Do not use CSV or
    # pandas JSON as intermediate numeric transport between worker processes.
    payload={'columns':[{'name':str(name),'dtype':str(frame[name].dtype),
                         'values':[_encode(v) for v in frame[name].tolist()]} for name in frame.columns]}
    atomic_json(path,payload)


def load_frame(path):
    payload=json.loads(Path(path).read_text(encoding='utf-8'))
    return pd.DataFrame({c['name']:pd.Series([_decode(v) for v in c['values']],dtype=c['dtype']) for c in payload['columns']})


def normalized_columns(frame,config):
    frame=frame.copy()
    aliases={'trace.id':['trace_id','trace'],'component':['channel'],'magnitude':['mag','Magnitude'],
             'event_id':['event','eventid'],'timeStep':['dt','time_step'],'fcHP':['fchp'],'fcLP':['fclp']}
    explicit=config.get('sanity_column_mappings',{})
    for target,choices in aliases.items():
        if target in explicit and explicit[target] in frame: frame[target]=frame[explicit[target]]
        elif target not in frame:
            found=[name for name in choices if name in frame]
            if len(found)==1: frame[target]=frame[found[0]]
    required=['event_id','station','component','trace.id','network']
    enabled={str(k) for k in config.get('enabled_checks',['1','2','3','4','6','7'])}
    if not enabled.issubset(CHECK_NAMES):raise ValueError('Sanity check identifiers must be 1 through 7.')
    if enabled & set('12'):required+=['pga']
    if enabled & set('3457'):required+=['timeStep','windowStart','windowEnd','fcHP','fcLP']
    missing=[name for name in required if name not in frame]
    if missing:raise ValueError('Filtering metadata is missing columns: '+', '.join(missing))
    return frame


def source_hdf5(row,config):
    if config.get('input_hdf5'):return Path(config['input_hdf5']).resolve()
    filename=str(row.get('filename',''))
    direct=Path(filename)
    if direct.is_file():return direct.resolve()
    relative=Path(config['filtering_metadata']).resolve().parent/filename
    if relative.is_file():return relative.resolve()
    return direct.resolve()  # v5 reports a missing source per record.


def partition_groups(frame,config):
    """Keep every potential v5 cross-component comparison in one process."""
    normalized=normalized_columns(frame,config)
    keys=['event_id','network','station']+(['location'] if 'location' in normalized else [])
    groups=[]
    # Use the same pandas equality/dropna semantics as v5, including mixed
    # numeric labels. All epochs are retained inside each enclosing scope.
    for key,group in normalized.groupby(keys,dropna=False,sort=False):
        indices=[int(i) for i in group.index]
        label=[str(value) for value in key]
        groups.append({'id':token([label,indices])[:16],'key':label,'indices':indices})
    return groups


def _source_state(frame,config):
    paths={source_hdf5(row,config) for _,row in normalized_columns(frame,config).iterrows()}
    result=[]
    for path in sorted(paths,key=str):
        if path.is_file():
            stat=path.stat(); result.append([str(path),stat.st_size,stat.st_mtime_ns])
        else:result.append([str(path),'missing'])
    return result


def _bound_inputs(frame,config):
    files=[HERE/'Sanity_checks_v5_app.py',HERE/'exact_reader.py',Path(__file__),
           HERE/'scipy_signal_compat_v1.py',
           APP/'engines'/'acceptreject'/'hdf5_reader_v10_app.py',APP/'signalqc'/'data.py',
           Path(config['filtering_metadata']).resolve()]
    if config.get('station_catalog'):files.append(Path(config['station_catalog']).resolve())
    return {'content':{str(p):file_hash(p) if p.is_file() else 'missing' for p in files},
            'hdf5_sources':_source_state(frame,config),
            'settings':{k:v for k,v in config.items() if k not in ('workers','output_dir','cancel_file','resume')}}


def effective_workers(requested,groups):
    limit=max(1,min(int(requested or 1),os.cpu_count() or 1,len(groups)))
    try:
        import psutil
        memory=max(1,int((psutil.virtual_memory().available-2*1024**3)/(512*1024**2)))
        limit=min(limit,memory)
    except ImportError:pass
    return limit


def run_child(payload_path):
    """Run frozen v5 with a lossless subset supplied through its normal table I/O."""
    payload=json.loads(Path(payload_path).read_text(encoding='utf-8'))
    for path,expected in payload['content_pins'].items():
        if expected!='missing' and file_hash(path)!=expected:raise ValueError('Bound input changed: '+path)
    if file_hash(payload['input_frame'])!=payload['input_frame_sha256']:raise ValueError('Worker metadata snapshot changed')
    frame=load_frame(payload['input_frame'])
    config=payload['config']
    output=Path(config['output_dir'])
    expected_csv=output/(Path(config['filtering_metadata']).stem+'_sc.csv')
    result_frame=output/'result_frame.json'
    import signalqc.data as data
    original_reader=data.read_table
    original_csv=pd.DataFrame.to_csv
    expected_source=Path(config['filtering_metadata']).resolve()
    def subset_reader(path):
        if Path(path).resolve()==expected_source:return frame.copy(deep=True)
        return original_reader(path)
    def capture_csv(self,path_or_buf=None,*args,**kwargs):
        result=original_csv(self,path_or_buf,*args,**kwargs)
        if isinstance(path_or_buf,(str,Path)) and Path(path_or_buf).resolve()==expected_csv.resolve():
            save_frame(result_frame,self)
        return result
    data.read_table=subset_reader
    pd.DataFrame.to_csv=capture_csv
    try:
        sys.path.insert(0,str(HERE))
        spec=importlib.util.spec_from_file_location('signalqc_sanity_frozen_v5',HERE/'Sanity_checks_v5_app.py')
        engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
        from scipy_signal_compat_v1 import compatible_filtfilt
        engine.filtfilt=compatible_filtfilt()
        result=engine.run(config)
        if result['status']=='cancelled':return 130
        if not result_frame.is_file():raise RuntimeError('v5 did not publish its final result table')
        atomic_json(output/'child_complete.json',{'status':result['status'],'result_frame':str(result_frame),
                    'result_frame_sha256':file_hash(result_frame),'summary':result})
        return 0
    finally:
        data.read_table=original_reader
        pd.DataFrame.to_csv=original_csv


def _validate_result(frame,expected,indices,config=None):
    if ROW_ID not in frame:raise ValueError('Worker result lost the exact source row identity')
    actual=pd.to_numeric(frame[ROW_ID],errors='raise').astype(int).tolist()
    if len(actual)!=len(indices) or len(set(actual))!=len(actual) or set(actual)!=set(indices):
        raise ValueError('Worker result has missing, duplicate or unexpected rows')
    keyed=frame.set_index(ROW_ID)
    normalized=normalized_columns(expected,dict(config or {},enabled_checks=[]))
    for i in indices:
        for field in ('event_id','trace.id','network','station','component'):
            if str(keyed.loc[i,field])!=str(normalized.iloc[i][field]):raise ValueError('Worker result changed identity field '+field)
    return frame


def _run_group(group,frame,config,checkpoint,bound,fingerprint):
    group_dir=checkpoint/group['id'];group_dir.mkdir(parents=True,exist_ok=True)
    receipt_path=group_dir/'receipt.json'
    if config.get('resume',True) and receipt_path.exists():
        try:
            receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
            if receipt['fingerprint']!=fingerprint or receipt['indices']!=group['indices']:raise ValueError('Checkpoint belongs to another input')
            if any(file_hash(path)!=digest for path,digest in receipt['output_hashes'].items()):raise ValueError('Saved group output changed')
            result=load_frame(receipt['result_frame'])
            _validate_result(result,frame,group['indices'],config)
            return dict(receipt,cached=True)
        except (OSError,KeyError,TypeError,ValueError):pass
    attempt=group_dir/('a'+uuid.uuid4().hex[:12]);attempt.mkdir()
    selected=frame.iloc[group['indices']].copy()
    selected[ROW_ID]=group['indices']
    input_frame=attempt/'input_frame.json';save_frame(input_frame,selected)
    child_config=dict(config,output_dir=str(attempt),export_stage_workbooks=False)
    payload={'config':child_config,'input_frame':str(input_frame),'input_frame_sha256':file_hash(input_frame),
             'content_pins':bound['content']}
    payload_path=attempt/'child_config.json';atomic_json(payload_path,payload)
    env=os.environ.copy()
    for name in ('PYTHONHOME','PYTHONPATH'):env.pop(name,None)
    env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONNOUSERSITE='1',
               OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    exe=config.get('python_executable') or sys.executable
    with (attempt/'worker.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([exe,str(Path(__file__).resolve()),'--child',str(payload_path)],cwd=HERE,
                                 env=env,stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        code=process.wait()
    if code==130:return {'status':'cancelled','id':group['id']}
    completion=attempt/'child_complete.json'
    if code or not completion.is_file():raise RuntimeError(f'Sanity group failed (exit {code}); see {attempt / "worker.log"}')
    result=json.loads(completion.read_text(encoding='utf-8'))
    if result['status'] not in ('completed','completed_with_issues'):raise ValueError('Worker did not complete its group')
    if file_hash(result['result_frame'])!=result['result_frame_sha256']:raise ValueError('Worker table changed after completion')
    _validate_result(load_frame(result['result_frame']),frame,group['indices'],config)
    artifacts=[Path(result['result_frame']),attempt/'sanity_summary.json',*sorted((attempt/'sc_plots').rglob('*.png'))]
    receipt=dict(result,fingerprint=fingerprint,indices=group['indices'],id=group['id'],
                 output_hashes={str(path):file_hash(path) for path in artifacts},cached=False)
    atomic_json(receipt_path,receipt)
    return receipt


def _failed_rows(frame,indices,config,message):
    result=normalized_columns(frame.iloc[indices],config).copy()
    result[ROW_ID]=indices
    enabled={str(k) for k in config.get('enabled_checks',['1','2','3','4','6','7'])}
    for check,column in CHECK_NAMES.items():
        result[column]=pd.NA
        result['sc_check_'+check+'_status']='not_evaluated' if check in enabled else 'disabled'
    for name in METRICS:result[name]=np.nan
    result['sc_notes']=message+'; '
    result['flag']='-999';result['sc_status']='incomplete'
    result['pipeline_outcome']=['rejected' if str(row.get('stage2_status',''))=='rejected' else 'incomplete' for _,row in result.iterrows()]
    return result


def run(config,progress=None,cancelled=None):
    config=json.loads(json.dumps(config))
    source=Path(config['filtering_metadata']).resolve()
    config['filtering_metadata']=str(source)
    output=Path(config['output_dir']).resolve();output.mkdir(parents=True,exist_ok=True)
    frame=read_table(source).reset_index(drop=True)
    if frame.empty:raise ValueError('No filtering rows available for sanity checks.')
    if ROW_ID in frame:raise ValueError('Input uses a reserved parallel row identity column')
    groups=partition_groups(frame,config)
    bound=_bound_inputs(frame,config);fingerprint=token(bound)
    checkpoint=filesystem_path(output/'checkpoints'/'sc6'/fingerprint[:24])
    if not config.get('resume',True):checkpoint=checkpoint/('run_'+uuid.uuid4().hex)
    checkpoint.mkdir(parents=True,exist_ok=True)
    atomic_json(checkpoint/'manifest.json',{'fingerprint':fingerprint,'inputs':bound,'groups':groups})
    exe=Path(config.get('python_executable') or sys.executable)
    if not exe.is_file():raise ValueError('Configured Python runtime is unavailable')
    external_cancel=cancelled or (lambda:bool(config.get('cancel_file')) and Path(config['cancel_file']).exists())
    local_cancel=checkpoint/('cancel_'+uuid.uuid4().hex)
    child_config=dict(config,cancel_file=str(local_cancel))
    workers=effective_workers(config.get('workers',1),groups)
    results={};failures={};pending=list(groups);active={};stopped=False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or active:
            if external_cancel():
                stopped=True;local_cancel.touch(exist_ok=True)
            while pending and len(active)<workers and not stopped:
                group=pending.pop(0)
                future=pool.submit(_run_group,group,frame,child_config,checkpoint,bound,fingerprint)
                active[future]=group
            if not active:break
            done,_=wait(active,timeout=.25,return_when=FIRST_COMPLETED)
            for future in done:
                group=active.pop(future)
                try:
                    result=future.result()
                    if result['status']=='cancelled':stopped=True;local_cancel.touch(exist_ok=True)
                    else:results[group['id']]=result
                except Exception as exc:
                    failures[group['id']]=str(exc)
                    atomic_json(checkpoint/group['id']/'failure.json',{'group':group,'message':str(exc)})
                if progress:progress({'stage':'sanity','message':f'Sanity groups completed: {len(results)} / {len(groups)}',
                                      'current':len(results),'total':len(groups),'workers':workers,'failed_groups':len(failures)})
    if _bound_inputs(frame,config)!=bound:raise ValueError('Sanity source data, metadata or code changed during processing; completed group checkpoints were retained')
    frames=[];issues=[];cached=0
    for group in groups:
        result=results.get(group['id'])
        if result is None:
            message=failures.get(group['id'],'Cancelled before this group completed')
            frames.append(_failed_rows(frame,group['indices'],config,message))
            for index in group['indices']:issues.append({'row':index,'message':message})
            continue
        cached+=int(result.get('cached',False))
        frames.append(load_frame(result['result_frame']))
        for issue in result['summary'].get('issues',[]):
            entry=dict(issue)
            if isinstance(entry.get('row'),int) and 0<=entry['row']<len(group['indices']):entry['row']=group['indices'][entry['row']]
            issues.append(entry)
        for filename in result['output_hashes']:
            path=Path(filename)
            if path.suffix.lower()=='.png':
                target=output/'sc_plots'/group['id']/path.name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,target)
    merged=pd.concat(frames,ignore_index=True).sort_values(ROW_ID,kind='stable').reset_index(drop=True)
    _validate_result(merged,frame,list(range(len(frame))),config)
    merged=merged.drop(columns=[ROW_ID])
    csv=output/(source.stem+'_sc.csv');temporary=csv.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    merged.to_csv(temporary,index=False);os.replace(temporary,csv)
    files=[str(csv)]
    if config.get('export_stage_workbooks',False):
        xlsx=output/(source.stem+'_sc.xlsx');merged.to_excel(xlsx,index=False);files.append(str(xlsx))
    status='cancelled' if stopped else 'failed' if not results else 'completed_with_issues' if issues else 'completed'
    summary={'status':status,'output_files':files,'counts':{str(k):int(v) for k,v in merged.sc_status.value_counts().items()},
             'enabled_checks':sorted({str(k) for k in config.get('enabled_checks',['1','2','3','4','6','7'])}),
             'issues':issues,'workers':workers,'group_count':len(groups),'completed_groups':len(results),
             'cached_groups':cached,'failed_groups':len(failures),'checkpoint':str(checkpoint)}
    atomic_json(output/'sanity_summary.json',summary)
    return summary


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':raise SystemExit(run_child(sys.argv[2]))
    with Path(sys.argv[1]).open(encoding='utf-8-sig') as handle:settings=json.load(handle)
    result=run(settings,progress=lambda event:print('SIGNALQC_EVENT '+json.dumps(event,default=str),flush=True))
    print(json.dumps(result,default=str),flush=True)
    if result['status']=='cancelled':raise SystemExit(130)
    if result['status']=='failed':raise SystemExit(2)
