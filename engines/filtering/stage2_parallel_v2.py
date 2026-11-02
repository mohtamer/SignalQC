"""Bounded waveform workers; original R main computes, one owner writes final HDF5.

Each R worker receives the entire accepted table (shared arrival context), filters
only assigned exact keys, and writes a private checkpoint HDF5. The coordinator
copies groups after the worker exits, with exact-array verification. No numerical
filter, corner-selection or unit-conversion equation is implemented here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import uuid

import h5py
import numpy as np
import pandas as pd

ENGINE=Path(__file__).resolve().parent
APP=ENGINE.parents[1]
sys.path.insert(0,str(APP)); sys.path.insert(0,str(ENGINE))
from signalqc.project import atomic_json, ProjectLock
import stage2_io_v7 as io
from signalqc.method_settings_v1 import stage_method_settings

DATASETS=('acceleration_waveform','velocity_waveform','displacement_waveform')
OUTPUT_FIELDS=('fcHP','fcLP','windowStart','windowEnd','PStart','SStart','SEnd','CStart','pga','parr1','SNRave')


def key(row):
    return str(row['event_id']),str(row['trace_id'])


def key_token(pair):
    return hashlib.sha256(json.dumps(list(pair),separators=(',',':')).encode()).hexdigest()


def _available_memory():
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in
                ('total_physical','available_physical','total_page','available_page','total_virtual','available_virtual','extended')]
        status=Memory(); status.length=ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError('Available memory could not be checked')
        return int(status.available_physical)
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'): return int(line.split()[1])*1024
    except OSError: pass
    return int(os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE'))


def _process_info(pid):
    """Read process creation identity using only the bundled Python standard library."""
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        handle=kernel.OpenProcess(0x1000|0x100000,False,int(pid))
        if not handle:
            error=ctypes.get_last_error()
            if error in (87,1168): return None
            raise OSError(error,'Cannot verify previous waveform-worker process identity')
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)): raise ctypes.WinError(ctypes.get_last_error())
            if code.value!=259: return None
            creation,exit_time,system,user=(wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exit_time),ctypes.byref(system),ctypes.byref(user)):
                raise ctypes.WinError(ctypes.get_last_error())
            buffer=ctypes.create_unicode_buffer(32768); size=wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)): raise ctypes.WinError(ctypes.get_last_error())
            return dict(pid=int(pid),created=(int(creation.dwHighDateTime)<<32)|int(creation.dwLowDateTime),executable=buffer.value)
        finally: kernel.CloseHandle(handle)
    try:
        data=Path(f'/proc/{int(pid)}/stat').read_text().split(') ',1)[1].split()
        if data[0]=='Z': return None
        return dict(pid=int(pid),created=int(data[19]),executable=os.readlink(f'/proc/{int(pid)}/exe'))
    except FileNotFoundError: return None


def worker_limit(requested,count):
    available=_available_memory()
    if available<4*1024**3:
        raise RuntimeError('Less than 4 GiB RAM is available. Close other applications before starting waveform workers.')
    memory_limit=max(1,int((available-4*1024**3)//(2.5*1024**3)))
    return max(1,min(int(requested or 1),os.cpu_count() or 1,memory_limit,count or 1))


def _read_csv(path):
    return pd.read_csv(path,dtype=object,keep_default_na=False)


def _atomic_csv(path,rows):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    pd.DataFrame(rows).to_csv(temporary,index=False)
    os.replace(temporary,path)


def _digest_dataset(ds):
    digest=hashlib.sha256(str(ds.dtype).encode()+str(ds.shape).encode())
    for start in range(0,len(ds),65536):
        digest.update(np.ascontiguousarray(ds[start:start+65536]).tobytes())
    return digest.hexdigest()


def _group_proof(group):
    lengths={len(group[name]) for name in DATASETS if name in group}
    if len(lengths)!=1 or not next(iter(lengths),0) or not all(name in group for name in DATASETS):
        raise ValueError('Processed waveform requires nonempty equal-length A/V/D datasets')
    result={}
    for name in DATASETS:
        ds=group[name]
        for start in range(0,len(ds),65536):
            if not np.isfinite(ds[start:start+65536]).all():
                raise ValueError('Non-finite processed waveform samples')
        result[name]=_digest_dataset(ds)
    return result


def _receipt_current(destination,receipt,signature):
    if receipt.get('processing_signature')!=signature or not destination.is_file(): return False
    pair=tuple(receipt['key'])
    try:
        with h5py.File(destination,'r') as f:
            group=f[pair[0]][pair[1]]
            return (str(group.attrs.get('processing_signature',''))==signature and
                    bool(group.attrs.get('stage2_complete',False)) and
                    _group_proof(group)==receipt['arrays'])
    except (OSError,KeyError,ValueError):
        return False



def _json_object(path):
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise ValueError('Checkpoint must contain an object')
    return value


def _read_receipt(path):
    receipt=_json_object(path)
    pair=receipt.get('key')
    if not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(v,str) and v for v in pair):
        raise ValueError('Invalid receipt identity')
    signature=receipt.get('processing_signature')
    if not isinstance(signature,str) or len(signature)!=64:
        raise ValueError('Invalid receipt signature')
    arrays=receipt.get('arrays'); row=receipt.get('row')
    if not isinstance(arrays,dict) or set(arrays)!=set(DATASETS) or not all(isinstance(v,str) and len(v)==64 for v in arrays.values()):
        raise ValueError('Invalid receipt array verification')
    if not isinstance(row,dict) or key(row)!=tuple(pair) or row.get('processing_signature')!=signature:
        raise ValueError('Receipt metadata identity differs')
    if path.stem!=key_token(pair)[:24]: raise ValueError('Receipt filename differs from its identity')
    return receipt


def _quarantine_receipt(path,checkpoint):
    # Only the locked coordinator writes receipts. Preserve corrupt bytes for
    # diagnosis while allowing a newly verified receipt to use the exact key.
    folder=checkpoint/'corrupt_receipts'; folder.mkdir(exist_ok=True)
    target=folder/(path.stem+'_'+uuid.uuid4().hex[:12]+'.json')
    os.replace(path,target)
    return str(target)


def _worker_is_closed(folder):
    process_file=folder/'process.json'
    if process_file.exists():
        # An unreadable ownership record deliberately raises: never guess that
        # a writer is gone merely because its state cannot be read.
        return _owned_process(process_file) is None
    if (folder/'launch_pending.json').exists(): return False
    exit_file=folder/'worker_exit.json'
    if not exit_file.exists(): return False
    try:
        result=_json_object(exit_file)
        if not isinstance(result.get('exit_code'),int): return False
        inputs=[p for p in (folder/'job.json',folder/'worker_config.json') if p.exists()]
        return bool(inputs) and exit_file.stat().st_mtime_ns>=max(p.stat().st_mtime_ns for p in inputs)
    except (OSError,ValueError,TypeError): return False


def _job_from_config(folder,signature,source,valid_keys):
    config_path=folder/'worker_config.json'
    config=_json_object(config_path)
    if config.get('coordinator_signature')!=signature: return None
    if Path(config['input_hdf5']).resolve()!=source or Path(config['output_dir']).resolve()!=folder.resolve():
        raise ValueError('Worker config source/output does not match this checkpoint')
    entries=config.get('worker_trace_keys')
    if not isinstance(entries,list) or not entries: raise ValueError('Worker config has no trace assignments')
    pairs=[key(item) for item in entries]
    if len(set(pairs))!=len(pairs) or not set(pairs)<=valid_keys:
        raise ValueError('Worker config trace assignments are ambiguous or unknown')
    return dict(worker_id='recovered',config=str(config_path),keys=[list(p) for p in pairs],processing_signature=signature)


def _recoverable_jobs(root,signature,source,valid_keys):
    jobs=[]; blocked=set(); issues=[]
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if not any((folder/name).exists() for name in ('job.json','worker_config.json','process.json','launch_pending.json')):
            continue
        closed=_worker_is_closed(folder)
        job=None; diagnostic=None
        try:
            job=_json_object(folder/'job.json')
            independent=_json_object(folder/'worker_config.json')
            old_signature=job.get('processing_signature')
            if isinstance(old_signature,str) and len(old_signature)==64 and old_signature!=signature and old_signature==independent.get('coordinator_signature'):
                if closed: continue
                job=None
                raise ValueError('Old worker has no trustworthy closure record')
            if job.get('processing_signature')!=signature:
                raise ValueError('Worker job signature differs from current independent config')
            configured=_job_from_config(folder,signature,source,valid_keys)
            if configured is None or job.get('keys')!=configured['keys'] or Path(job['config']).resolve()!=Path(configured['config']).resolve():
                raise ValueError('Worker job and independent config disagree')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            diagnostic='Worker job manifest could not be trusted: '+str(exc)
            try:
                job=_job_from_config(folder,signature,source,valid_keys)
            except (OSError,ValueError,KeyError,TypeError) as config_error:
                job=None; diagnostic+='; independent config: '+str(config_error)
        if not closed:
            affected={tuple(p) for p in job['keys']} if job else set(valid_keys)
            blocked.update(affected)
            issues.append(dict(checkpoint=str(folder),message='Worker closure cannot be verified; affected records remain pending.',
                               affected_keys=[list(p) for p in sorted(affected)],detail=diagnostic or 'No trustworthy process/exit record'))
            continue
        if diagnostic:
            issues.append(dict(checkpoint=str(folder),message=diagnostic,
                               action='Recovered exact assignments from independent config' if job else 'Closed unreadable job ignored; unverified records will be retried'))
        if job is not None: jobs.append(job)
    return jobs,blocked,issues


def _owned_process(process_file):
    """Recognize a surviving owned R process; PID reuse never establishes ownership."""
    if not process_file.exists(): return None
    try:
        item=json.loads(process_file.read_text())
        process=_process_info(int(item['pid']))
        if process is None or process['created']!=item['created']: return None
        if Path(process['executable']).resolve()!=Path(item['executable']).resolve(): return None
        return process
    except (OSError,ValueError,KeyError) as exc:
        raise RuntimeError('Previous waveform-worker ownership record is unreadable; automatic relaunch was blocked.') from exc


def _drain_owned_workers(root,emit):
    survivors=[(p,_owned_process(p)) for p in root.glob('*/process.json')]
    for path,process in survivors:
        if process is None: continue
        emit({'type':'recover_worker','message':'Waiting for a previously started waveform worker to reach its checkpoint.',
              'worker_pid':process['pid']})
        while _owned_process(path) is not None:
            time.sleep(.5)


def _launch_worker(job,emit):
    config_path=Path(job['config']); folder=config_path.parent
    config=json.loads(config_path.read_text(encoding='utf-8'))
    command=[str(config['rscript_executable']),str(ENGINE/'Main_AutoFC_v19_app.R'),str(config_path)]
    env=os.environ.copy()
    env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
               RETICULATE_PYTHON=str(config['python_executable']),R_LIBS_USER=str(APP/'runtime'/'R-library'),
               PYTHONNOUSERSITE='1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    for name in ('PYTHONHOME','PYTHONPATH','R_HOME','LC_ALL','LC_CTYPE','LANG'): env.pop(name,None)
    with (folder/'worker.log').open('a',encoding='utf-8') as log:
        (folder/'worker_exit.json').unlink(missing_ok=True)
        (folder/'process.json').unlink(missing_ok=True)
        atomic_json(folder/'launch_pending.json',dict(worker_id=job['worker_id']))
        process=subprocess.Popen(command,cwd=ENGINE,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                 text=True,encoding='utf-8',errors='replace',
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        identity=_process_info(process.pid)
        if identity is not None:
            atomic_json(folder/'process.json',dict(identity,config=str(config_path.resolve()),worker_id=job['worker_id']))
            (folder/'launch_pending.json').unlink(missing_ok=True)
        emit({'type':'worker_started','worker_id':job['worker_id'],'worker_pid':process.pid,
              'message':f'Waveform worker {job["worker_id"]} started ({len(job["keys"])} traces).'})
        for line in process.stdout:
            log.write(line); log.flush()
            if line.startswith('SIGNALQC_EVENT '):
                try:
                    event=json.loads(line[len('SIGNALQC_EVENT '):]); event['worker_id']=job['worker_id']
                    if event.get('type') in ('trace_started','trace_finished'):
                        event.pop('current',None); event.pop('total',None)
                        event['message']=f'Worker {job["worker_id"]}: {event.get("trace_id", "")} {event["type"].replace("_", " ")}'
                        emit(event)
                except ValueError: pass
        code=process.wait()
    atomic_json(folder/'worker_exit.json',dict(exit_code=code,worker_id=job['worker_id']))
    (folder/'launch_pending.json').unlink(missing_ok=True)
    return dict(job,exit_code=code)


def _copy_artifact(source,worker_folder,target):
    source=Path(source).resolve()
    if not source.is_relative_to(worker_folder.resolve()) or not source.is_file():
        raise ValueError('Worker figure is outside its private output directory or missing')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.tmp')
    shutil.copy2(source,temporary); os.replace(temporary,target)
    return str(target)


def commit_waveform(worker_h5,destination,row,signature,receipts,worker_folder):
    """Called by the coordinator thread only, after the worker process closes."""
    pair=key(row); token=key_token(pair)[:24]
    receipt_path=receipts/(token+'.json')
    if receipt_path.exists():
        prior=_read_receipt(receipt_path)
        if tuple(prior.get('key',[]))!=pair: raise ValueError('Waveform receipt identifier collision')
        if _receipt_current(destination,prior,signature): return prior['row']
    result=dict(row); result['processed_hdf5']=str(destination)
    result['processing_signature']=signature
    for field,kind in [('figure_avd','AVD'),('figure_fas','FAS')]:
        value=str(result.get(field,'')).strip()
        if value:
            result[field]=_copy_artifact(value,worker_folder,destination.parent/'Figure'/kind/(token+'.png'))
    with h5py.File(worker_h5,'r') as source:
        group=source[pair[0]][pair[1]]
        if str(group.attrs.get('processing_signature',''))!=signature or not bool(group.attrs.get('stage2_complete',False)):
            raise ValueError('Private waveform has a different signature or incomplete checkpoint')
        proof=_group_proof(group)
        audit_id=str(group.attrs['processing_audit_id'])
        audit_source=worker_folder/'diagnostics'/'processing_metadata'/(audit_id+'.json')
        audit=json.loads(audit_source.read_text(encoding='utf-8'))
        if (str(audit.get('event_id')),str(audit.get('trace_id')))!=pair or audit.get('processing_signature')!=signature:
            raise ValueError('Private waveform diagnostic identity does not match')
        audit['private_worker_output']=str(worker_h5)
        for field in ('processed_hdf5','figure_avd','figure_fas'):
            if field in result: audit['engine_metadata'][field]=result[field]
        audit_target=destination.parent/'diagnostics'/'processing_metadata'/(audit_id+'.json')
        atomic_json(audit_target,audit)
        with h5py.File(destination,'a') as target:
            for name,value in source.attrs.items(): target.attrs[name]=value
            event=target.require_group(pair[0])
            for name,value in source[pair[0]].attrs.items(): event.attrs[name]=value
            if pair[1] in event and str(event[pair[1]].attrs.get('processing_signature',''))!=signature:
                raise ValueError('Existing destination trace has a different processing signature; it was preserved')
            pending='__pending_parallel_'+token[:24]
            if pending in event: del event[pending]
            source.copy(group,event,name=pending)
            target.flush()
            if _group_proof(event[pending])!=proof:
                raise ValueError('Copied waveform arrays differ from the R worker output')
            if pair[1] in event: del event[pair[1]]
            event.move(pending,pair[1]); target.flush()
    # A reopened, verified group is required before recording committed status.
    with h5py.File(destination,'r') as target:
        if _group_proof(target[pair[0]][pair[1]])!=proof:
            raise ValueError('Saved waveform verification failed after closing HDF5')
    receipt=dict(key=list(pair),processing_signature=signature,arrays=proof,row=result,writer_pid=os.getpid())
    atomic_json(receipt_path,receipt)
    return result


def _collect_worker(job,source_name,destination,signature,receipts,rows,emit):
    folder=Path(job['config']).parent
    path=folder/(source_name+'filmeta.csv')
    errors=[]; successful=0
    assigned={tuple(k) for k in job['keys']}
    records={}
    duplicates=set()
    if path.exists():
        try:
            saved=_read_csv(path).to_dict('records')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            saved=[]; errors.append(dict(checkpoint=str(path),message='Worker metadata could not be read: '+str(exc)))
        for item in saved:
            try: pair=key(item)
            except (KeyError,TypeError) as exc:
                errors.append(dict(checkpoint=str(path),message='Worker row has no trustworthy exact identity: '+str(exc)))
                continue
            if pair in assigned:
                if pair in records or pair in duplicates:
                    duplicates.add(pair); records.pop(pair,None)
                else: records[pair]=item
    for pair in assigned:
        item=records.get(pair)
        try:
            if pair in duplicates: raise ValueError('Worker metadata has duplicate exact trace keys; only this record is retained for retry')
            if not item: raise ValueError('Worker ended before saving this waveform checkpoint')
            state=str(item.get('stage2_status',''))
            if state in ('completed','rejected'):
                private=Path(str(item['processed_hdf5'])).resolve()
                if not private.is_relative_to(folder.resolve()): raise ValueError('Private HDF5 path escapes worker folder')
                rows[pair]=commit_waveform(private,destination,item,signature,receipts,folder)
                successful+=1
                emit({'type':'waveform_saved','trace_id':pair[1],'event_id':pair[0],
                      'message':f'Filtered waveform saved and verified: {pair[1]}'})
            elif state=='error':
                rows[pair].update(item); rows[pair]['processed_hdf5']=''
                errors.append(dict(key=list(pair),message=str(item.get('stage2_error','Filtering error'))))
            elif job.get('exit_code')==130:
                rows[pair]['stage2_status']='pending'
            else:
                raise ValueError('Worker did not complete the waveform; retained for retry')
        except Exception as exc:
            rows[pair].update(stage2_status='error',stage2_error=str(exc),acrjfinal=False,
                              rejectreason3filter='processing or output error',processed_hdf5='')
            errors.append(dict(key=list(pair),message=str(exc)))
    return successful,errors


def run(config,progress=None):
    config=dict(config)
    config.update(stage_method_settings(config,'filtering'))
    output=Path(config['output_dir']).resolve(); output.mkdir(parents=True,exist_ok=True)
    progress=progress or (lambda event:None)
    progress_lock=threading.Lock()
    def emit(event):
        event=dict(event,stage='filtering')
        with progress_lock: progress(event)
    with ProjectLock(output):
        return _run(config,output,emit)


def _run(config,output,emit):
    source=Path(config['input_hdf5']).resolve(); metadata=Path(config['stage1_metadata']).resolve()
    suffix=str(config.get('processed_suffix','processed_2'))
    if not io.valid_processed_suffix(suffix):
        raise ValueError('Invalid processed HDF5 suffix')
    destination=output/(source.stem+'_'+suffix+'.h5')
    table_path=output/(source.name+'filmeta.csv')
    checkpoint=output/'checkpoints'/'waveform_parallel_v2'; checkpoint.mkdir(parents=True,exist_ok=True)
    # Windows R/HDF5 libraries may reject long paths even when Python accepts
    # them. Persistent private storage uses a short user-writable temporary root;
    # the project records its location and retains all final outputs itself.
    storage_id=hashlib.sha256(str(output).encode()).hexdigest()[:20]
    workers_root=Path(tempfile.gettempdir())/'SignalQCWorkers'/storage_id
    workers_root.mkdir(parents=True,exist_ok=True)
    atomic_json(checkpoint/'worker_storage.json',dict(path=str(workers_root),destination=str(destination)))
    receipts=checkpoint/'receipts'; receipts.mkdir(exist_ok=True)
    normalized=io.prepare_metadata(source,metadata,output)
    frame=_read_csv(normalized)
    if frame.duplicated(['event_id','trace_id']).any():
        raise ValueError('Filtering input contains duplicate exact trace identities')
    rows={key(item):dict(item) for item in frame.to_dict('records')}
    order=list(rows)
    for item in rows.values():
        item.update({field:'' for field in OUTPUT_FIELDS})
        item.update(stage2_status='pending',stage2_error='',method_errors='',method_corners='',
                    corner_fallback='',rejection_reasons='',acrjfinal=True,rejectreason3filter='accepted',
                    processed_hdf5=str(destination),figure_avd='',figure_fas='')
    semantic={k:v for k,v in config.items() if k not in ('workers','resume','cancel_file','output_dir')}
    processing_signature=io.run_signature(source,metadata,json.dumps(semantic,sort_keys=True))
    _drain_owned_workers(workers_root,emit)
    recovery_issues=[]
    for path in receipts.glob('*.json'):
        try:
            receipt=_read_receipt(path)
            pair=tuple(receipt['key'])
            if pair in rows and _receipt_current(destination,receipt,processing_signature): rows[pair]=receipt['row']
        except (OSError,ValueError,KeyError,TypeError) as exc:
            preserved=_quarantine_receipt(path,checkpoint)
            recovery_issues.append(dict(checkpoint=str(path),message='Corrupt record receipt preserved; this record will be verified or retried.',detail=str(exc),preserved_at=preserved))
    # Closed private outputs survive an interrupted coordinator and are consumed
    # before creating new work partitions. No completed waveform is recalculated.
    recovered_jobs,blocked_keys,job_issues=_recoverable_jobs(workers_root,processing_signature,source,set(rows))
    recovery_issues.extend(job_issues)
    for job in recovered_jobs:
        recovering=dict(job,keys=[k for k in job['keys'] if rows[tuple(k)].get('stage2_status') not in ('completed','rejected')])
        if recovering['keys']:
            _collect_worker(recovering,source.name,destination,processing_signature,receipts,rows,emit)
    for pair in blocked_keys:
        if rows[pair].get('stage2_status') not in ('completed','rejected'):
            rows[pair].update(stage2_status='pending',stage2_error='Previous worker closure cannot be verified; retained for safe retry',processed_hdf5='')
    pending=[pair for pair in order if rows[pair].get('stage2_status') not in ('completed','rejected') and pair not in blocked_keys]
    cancelled_before_start=bool(config.get('cancel_file')) and Path(config['cancel_file']).exists()
    uncertain_workers=sum(bool(issue.get('affected_keys')) for issue in job_issues)
    count=worker_limit(config.get('workers',1),len(pending)) if pending and not cancelled_before_start else 0
    count=min(count,max(0,int(config.get('workers',1))-uncertain_workers))
    unfinished=sum(row.get('stage2_status') not in ('completed','rejected') for row in rows.values())
    atomic_json(checkpoint/'run.json',dict(schema='signalqc.waveform_parallel.v1',processing_signature=processing_signature,
                                          requested_workers=int(config.get('workers',1)),workers=count,source=str(source),
                                          destination=str(destination),total=len(rows),pending=unfinished,schedulable=len(pending),uncertain_workers=uncertain_workers,writer_pid=os.getpid()))
    emit({'type':'start','workers':count,'requested_workers':int(config.get('workers',1)),
          'total':len(rows),'already_completed':len(rows)-unfinished,'pending_worker_verification':unfinished-len(pending),
          'message':f'Filtering {len(pending)} remaining waveforms with {count} waveform workers; one HDF5 writer.'})
    jobs=[]
    for number in range(count):
        assigned=pending[number::count]
        token=hashlib.sha256(json.dumps([processing_signature,assigned],separators=(',',':')).encode()).hexdigest()[:16]
        folder=workers_root/token; folder.mkdir(exist_ok=True)
        worker=dict(config,output_dir=str(folder),resume=True,export_stage_workbooks=False,
                    worker_trace_keys=[dict(event_id=p[0],trace_id=p[1]) for p in assigned],
                    coordinator_signature=processing_signature)
        cfg=folder/'worker_config.json'; atomic_json(cfg,worker)
        job=dict(worker_id=number+1,config=str(cfg),keys=[list(p) for p in assigned],processing_signature=processing_signature)
        atomic_json(folder/'job.json',job); jobs.append(job)
    failures=[]; closed_jobs=[]
    if jobs:
        with ThreadPoolExecutor(max_workers=count) as pool:
            submitted={pool.submit(_launch_worker,job,emit):job for job in jobs}
            for future in as_completed(submitted):
                job=submitted[future]
                try: result=future.result()
                except Exception as exc:
                    result=dict(job,exit_code=-1); failures.append(dict(worker_id=job['worker_id'],message=str(exc)))
                if _worker_is_closed(Path(job['config']).parent):
                    closed_jobs.append(result)
                    _,issues=_collect_worker(result,source.name,destination,processing_signature,receipts,rows,emit)
                    failures.extend(issues)
                else:
                    for pair in map(tuple,job['keys']):
                        rows[pair].update(stage2_status='pending',stage2_error='Worker closure cannot be verified after a launch failure; retained for safe retry',processed_hdf5='')
                    recovery_issues.append(dict(worker_id=job['worker_id'],checkpoint=str(Path(job['config']).parent),message='Worker closure cannot be verified after launch; no private file was opened.',affected_keys=job['keys']))
                _atomic_csv(table_path,[rows[p] for p in order])
    if not rows:
        frame['stage2_status']=pd.Series(dtype=str); frame.to_csv(table_path,index=False)
    else:
        _atomic_csv(table_path,[rows[p] for p in order])
    # Preserve individual-method checkpoints even if later filtering failed.
    corner_rows={}
    for job in recovered_jobs+closed_jobs:
        path=Path(job['config']).parent/'method_corners.csv'
        if path.exists():
            try:
                saved_corners=_read_csv(path).to_dict('records')
                if any('event_id' not in row or 'trace_id' not in row for row in saved_corners): raise ValueError('Method table has no exact trace identity')
                for row in saved_corners:
                    if key(row) in rows and row.get('processing_signature')==processing_signature: corner_rows[key(row)]=row
            except (OSError,ValueError,KeyError,TypeError) as exc:
                recovery_issues.append(dict(checkpoint=str(path),message='Closed worker method table could not be recovered; other records are preserved.',detail=str(exc)))
    previous=output/'method_corners.csv'
    if previous.exists():
        try:
            saved_corners=_read_csv(previous).to_dict('records')
            if any('event_id' not in row or 'trace_id' not in row for row in saved_corners): raise ValueError('Method table has no exact trace identity')
            for row in saved_corners:
                if key(row) not in corner_rows and row.get('processing_signature')==processing_signature: corner_rows[key(row)]=row
        except (OSError,ValueError,KeyError,TypeError) as exc:
            recovery_issues.append(dict(checkpoint=str(previous),message='Previous combined method table could not be recovered; verified worker tables are preserved.',detail=str(exc)))
    if corner_rows: _atomic_csv(previous,list(corner_rows.values()))
    counts={state:sum(str(r.get('stage2_status'))==state for r in rows.values()) for state in ('completed','rejected','error','pending')}
    cancelled=bool(config.get('cancel_file')) and Path(config['cancel_file']).exists()
    warnings=sum(bool(str(r.get('method_errors','')).strip()) for r in rows.values())
    status='cancelled' if cancelled else 'no_accepted_traces' if not rows else 'completed_with_issues' if failures or recovery_issues or counts['error'] or counts['pending'] or warnings else 'completed'
    result=dict(stage='filtering',status=status,total=len(rows),**counts,method_warnings=warnings,metadata_csv=str(table_path),
                processed_hdf5=str(destination),workers=count,requested_workers=int(config.get('workers',1)),
                processing_signature=processing_signature,issues=failures,recovery_issues=recovery_issues,
                **stage_method_settings(config,'filtering'),
                output_files=[str(table_path)]+([str(destination)] if destination.exists() else []))
    atomic_json(output/'filtering_summary.json',result)
    emit(dict(type='complete',message='Waveform filtering and available HDF5 updates finished.',**result))
    return result


if __name__=='__main__':
    cfg=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    def emit(event): print('SIGNALQC_EVENT '+json.dumps(event,default=str),flush=True)
    result=run(cfg,emit)
    print(json.dumps(result,default=str),flush=True)
    raise SystemExit(130 if result['status']=='cancelled' else 3 if result['status']=='completed_with_issues' else 0)
