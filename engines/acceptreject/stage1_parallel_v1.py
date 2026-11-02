"""Original R accept/reject computations with one prepared event and triplet workers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from decimal import Decimal

import pandas as pd

ENGINE=Path(__file__).resolve().parent
APP=ENGINE.parents[1]
sys.path.insert(0,str(APP)); sys.path.insert(0,str(ENGINE))
from signalqc.project import ProjectLock,atomic_json,filesystem_path
import worker_runtime_v1 as runtime


def _json(path):
    value=json.loads(filesystem_path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise ValueError('Checkpoint must contain an object')
    return value


def _hash(path):
    digest=hashlib.sha256()
    with filesystem_path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def processing_signature(config):
    source=Path(config['input_hdf5']).resolve(); stat=source.stat()
    excluded={'qa','tests','verification','checkpoints','outputs','__pycache__'}
    files={str(p.relative_to(ENGINE)):_hash(p) for p in ENGINE.rglob('*')
           if p.is_file() and p.suffix.lower() in {'.py','.r','.csv','.h5','.txt','.rds','.pkl','.json'}
           and not any(part.lower() in excluded for part in p.relative_to(ENGINE).parts)}
    settings={k:config.get(k) for k in ('save_figures','acceptreject_methods','acceptreject_required_votes','python_executable','rscript_executable')}
    payload=dict(source=[str(source),stat.st_size,stat.st_mtime_ns],engines=files,settings=settings)
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def _copy(source,target):
    source=filesystem_path(source); target=filesystem_path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name('.signalqc_'+uuid.uuid4().hex+'.tmp')
    try:
        shutil.copy2(source,temporary)
        if _hash(source)!=_hash(temporary): raise OSError('Copied checkpoint verification failed')
        os.replace(temporary,target)
    finally: temporary.unlink(missing_ok=True)
    return _hash(target)


def _csv(path): return pd.read_csv(filesystem_path(path),dtype=object,keep_default_na=False)


def _atomic_csv(path,rows,columns=None):
    target=filesystem_path(path); target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name('.signalqc_'+uuid.uuid4().hex+'.tmp')
    try:
        pd.DataFrame(rows,columns=columns).to_csv(temporary,index=False)
        os.replace(temporary,target)
    finally: temporary.unlink(missing_ok=True)


def _launch(job,emit):
    folder=Path(job['config']).parent; config=_json(job['config'])
    command=[config['rscript_executable'],str(ENGINE/'Main AcceptReject_v14_app.R'),job['config']]
    env=os.environ.copy()
    env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
               RETICULATE_PYTHON=config['python_executable'],R_LIBS_USER=str(APP/'runtime'/'R-library'),
               PYTHONNOUSERSITE='1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    for name in ('PYTHONHOME','PYTHONPATH','R_HOME','LC_ALL','LC_CTYPE','LANG'): env.pop(name,None)
    with (folder/'worker.log').open('a',encoding='utf-8') as log:
        (folder/'worker_exit.json').unlink(missing_ok=True)
        (folder/'process.json').unlink(missing_ok=True)
        atomic_json(folder/'launch_pending.json',dict(mode=config['app_mode'],worker_id=job['worker_id']))
        process=subprocess.Popen(command,cwd=ENGINE,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        identity=runtime._process_info(process.pid)
        if identity is not None:
            atomic_json(folder/'process.json',dict(identity,config=job['config']))
            (folder/'launch_pending.json').unlink(missing_ok=True)
        emit(dict(type='worker_started',worker_id=job['worker_id'],worker_pid=process.pid,
                  message=f'Accept/reject {config["app_mode"]}: worker {job["worker_id"]} started.'))
        for line in process.stdout:
            log.write(line); log.flush()
            if line.startswith('SIGNALQC '):
                emit(dict(type='group_progress',worker_id=job['worker_id'],message=line.strip()))
        code=process.wait()
    atomic_json(folder/'worker_exit.json',dict(exit_code=code,worker_id=job['worker_id']))
    (folder/'launch_pending.json').unlink(missing_ok=True)
    return dict(job,exit_code=code)


def _job(folder,config,mode,worker_id,**extra):
    folder.mkdir(parents=True,exist_ok=True)
    cfg=dict(config,output_dir=str(folder),app_mode=mode,export_stage_workbooks=False)
    cfg.update(extra)
    path=folder/'worker_config.json'; atomic_json(path,cfg)
    job=dict(config=str(path),worker_id=worker_id,mode=mode,processing_signature=config['processing_signature'])
    atomic_json(folder/'job.json',job)
    return job


def _closed_launch(job,emit):
    result=_launch(job,emit)
    if not runtime._worker_is_closed(Path(job['config']).parent):
        raise RuntimeError('Worker closure is uncertain; its private files were not opened')
    return result


def _prepare(config,root,emit):
    folder=root/'prepare'; proof=folder/'prepared_receipt.json'
    if config.get('resume',True) and proof.exists() and runtime._worker_is_closed(folder):
        try:
            cached=_json(proof)
            if cached['processing_signature']==config['processing_signature'] and all(_hash(folder/name)==digest for name,digest in cached['files'].items()):
                manifest=_json(folder/'prepared_manifest.json')
                if not manifest.get('cancelled'): return manifest
        except (OSError,ValueError,KeyError,TypeError): pass
    if folder.exists() and any((folder/name).exists() for name in ('process.json','launch_pending.json','job.json')) and not runtime._worker_is_closed(folder):
        raise RuntimeError('Previous event preparation may still be running; automatic restart is held')
    job=_job(folder,config,'prepare','prepare',resume=False)
    emit(dict(type='prepare',message='Preparing full-event metadata and the original shared origin-time correction once.'))
    result=_closed_launch(job,emit)
    if result['exit_code'] not in (0,130): raise RuntimeError('Original R event preparation failed; see its private worker log')
    manifest=_json(folder/'prepared_manifest.json')
    if manifest.get('processing_signature')!=config['processing_signature']: raise ValueError('Prepared context signature mismatch')
    paths=[folder/'prepared_manifest.json',folder/'context_index.rds',folder/'stage1_status.csv']+list((folder/'contexts').glob('*.rds'))
    if not manifest.get('cancelled'):
        atomic_json(proof,dict(processing_signature=config['processing_signature'],files={str(p.relative_to(folder)):_hash(p) for p in paths}))
    return manifest


def _group_pairs(group): return [(str(group['event_id']),str(trace)) for trace in group['trace_ids']]


def _group_csv_data(path,group):
    data=_csv(path).to_dict('records')
    if [(str(r['event_id']),str(r['trace_id'])) for r in data]!=_group_pairs(group): raise ValueError('Group CSV row identity/order differs')
    if [Decimal(str(r['RSN'])) for r in data]!=[Decimal(str(i)) for i in group['rec_ids']]: raise ValueError('Original per-event RSN indices changed')
    if any(str(r.get('acrjfinal3')).lower() not in ('true','false','1','0') for r in data): raise ValueError('Group has no final acceptance decision')
    return data


def _group_statuses(data,group,config):
    figure=str(Path(config['public_output_dir'])/'Figure'/(group['group_key']+'.png')) if config.get('save_figures') else ''
    return [dict(source_file=config['input_hdf5'],event_id=str(row['event_id']),trace_id=str(row['trace_id']),
                 status='accepted' if str(row['acrjfinal3']).lower() in ('true','1') else 'rejected',
                 reason=str(row.get('rejectreason3','')),figure_path=figure) for row in data]


def _read_receipt(path,group,checkpoint,config):
    item=_json(path)
    if item.get('processing_signature')!=config['processing_signature'] or item.get('group')!=group: return None
    roles={'rds','csv'}|({'figure'} if config.get('save_figures') else set())
    if set(item.get('artifacts',{}))!=roles: return None
    for role,info in item['artifacts'].items():
        file=(checkpoint/info['path']).resolve()
        if not file.is_relative_to(checkpoint.resolve()) or _hash(file)!=info['sha256']: return None
    data=_group_csv_data(checkpoint/item['artifacts']['csv']['path'],group)
    statuses=_group_statuses(data,group,config)
    if item.get('statuses')!=statuses:
        # Statuses are derived metadata, never an independent scientific source.
        # Preserve a damaged receipt before restoring its hash-verified decisions.
        _copy(path,checkpoint/'corrupt_receipts'/(group['group_key']+'_'+uuid.uuid4().hex[:12]+'.json'))
        item['statuses']=statuses
        atomic_json(path,item)
    return item


def _commit(folder,group,checkpoint,config):
    key=group['group_key']; manifest=_json(folder/'group_results'/(key+'.json'))
    for field in ('event_id','group_key','event_index','group_index','trace_ids'):
        if manifest.get(field)!=group[field]: raise ValueError('Private group identity/order differs: '+field)
    if manifest.get('processing_signature')!=config['processing_signature']: raise ValueError('Private group signature differs')
    paths={role:Path(manifest[field]).resolve() for role,field in [('rds','checkpoint_rds'),('csv','output_csv')]}
    if config.get('save_figures'): paths['figure']=Path(manifest['figure_path']).resolve()
    if any(not path.is_relative_to(folder.resolve()) or not path.is_file() for path in paths.values()):
        raise ValueError('Private group artifact is missing or outside its worker folder')
    data=_group_csv_data(paths['csv'],group)
    artifacts={}
    for role,path in paths.items():
        relative=Path('groups')/(key+{'rds':'.rds','csv':'.csv','figure':'.png'}[role])
        artifacts[role]=dict(path=str(relative),sha256=_copy(path,checkpoint/relative))
    statuses=_group_statuses(data,group,config)
    receipt=dict(processing_signature=config['processing_signature'],group=group,artifacts=artifacts,statuses=statuses)
    atomic_json(checkpoint/'receipts'/(key+'.json'),receipt)
    return receipt


def _collect(folder,groups,checkpoint,config,receipts,statuses,issues,emit):
    # This routine is only entered after exact worker closure has been verified.
    private_status={}
    try:
        for row in _csv(folder/'stage1_status.csv').to_dict('records'):
            pair=(str(row['event_id']),str(row['trace_id']))
            if pair in private_status: private_status[pair]=None
            else: private_status[pair]=row
    except (OSError,ValueError,KeyError,TypeError): pass
    for group in groups:
        key=group['group_key']
        if key in receipts: continue
        try:
            receipt=_commit(folder,group,checkpoint,config)
            receipts[key]=receipt
            for row in receipt['statuses']: statuses[(row['event_id'],row['trace_id'])]=row
            emit(dict(type='group_saved',group_key=key,message='Accept/reject group saved: '+', '.join(group['trace_ids'])))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            for pair in _group_pairs(group):
                row=private_status.get(pair)
                if row and row.get('status') in ('error','skipped'):
                    statuses[pair]=dict(row,figure_path='')
                else: statuses[pair].update(status='pending' if _cancelled(config) else 'error',reason=str(exc),figure_path='')
            issues.append(dict(group_key=key,message=str(exc)))


def _cancelled(config): return bool(config.get('cancel_file')) and Path(config['cancel_file']).exists()


def _drain_output_workers(storage_root,current_root,emit):
    # A changed configuration gets a new private namespace, but surviving
    # workers from the previous configuration still consume the global budget.
    for namespace in storage_root.iterdir():
        if not namespace.is_dir(): continue
        runtime._drain_owned_workers(namespace,emit)
        if namespace==current_root: continue
        for folder in namespace.iterdir():
            if not folder.is_dir() or not any((folder/name).exists() for name in ('job.json','process.json','launch_pending.json')):
                continue
            if not runtime._worker_is_closed(folder):
                raise RuntimeError('Worker ownership from a previous configuration is unresolved; new workers were not launched')


def _old_jobs(root,groups,config):
    ready=[]; blocked=set(); issues=[]
    for folder in root.glob('worker_*'):
        closed=runtime._worker_is_closed(folder)
        try:
            cfg=_json(folder/'worker_config.json')
            if cfg.get('processing_signature')!=config['processing_signature'] or cfg.get('input_hdf5')!=config['input_hdf5'] or Path(cfg['output_dir']).resolve()!=folder.resolve():
                raise ValueError('Worker configuration identity differs')
            selected=[]
            for pair in cfg['worker_groups']:
                matches=[g for g in groups.values() if g['event_index']==pair['event_index'] and g['group_index']==pair['group_index']]
                if len(matches)!=1: raise ValueError('Worker group assignments are ambiguous')
                selected.extend(matches)
            if len({g['group_key'] for g in selected})!=len(selected): raise ValueError('Worker group assignments repeat')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            selected=list(groups.values()) if not closed else []
            issues.append(dict(checkpoint=str(folder),message='Worker config could not be recovered: '+str(exc)))
        if closed: ready.append((folder,selected))
        else:
            blocked.update(g['group_key'] for g in selected)
            issues.append(dict(checkpoint=str(folder),uncertain_worker=True,message='Previous worker closure is uncertain; affected groups remain pending'))
    return ready,blocked,issues


def _publish(config,root,checkpoint,receipts,emit):
    folder=root/('publish_'+uuid.uuid4().hex[:8]); private=folder/'checkpoints'; private.mkdir(parents=True)
    figures={}
    for key,receipt in receipts.items():
        _copy(checkpoint/receipt['artifacts']['rds']['path'],private/(key+'.rds'))
        if config.get('save_figures'):
            destination=Path(config['public_output_dir'])/'Figure'/(key+'.png')
            _copy(checkpoint/receipt['artifacts']['figure']['path'],destination); figures[key]=str(destination)
        else: figures[key]=''
    job=_job(folder,config,'publish','publish',publish_checkpoint_dir=str(private),publish_figures=figures,resume=True)
    result=_closed_launch(job,emit)
    if result['exit_code']!=0: raise RuntimeError('Original R metadata publisher failed')
    metadata=folder/(Path(config['input_hdf5']).name+'.meta.csv')
    actual=_csv(metadata).to_dict('records')
    expected=[pair for key in sorted(receipts) for pair in _group_pairs(receipts[key]['group'])]
    if [(str(r['event_id']),str(r['trace_id'])) for r in actual]!=expected: raise ValueError('Published R row ordering differs from original checkpoint order')
    expected_rows=[row for key in sorted(receipts) for row in _csv(checkpoint/receipts[key]['artifacts']['csv']['path']).to_dict('records')]
    for saved,written in zip(expected_rows,actual):
        if any(str(written.get(name,''))!=str(value) for name,value in saved.items() if name!='figure_path'):
            raise ValueError('Published R values differ from the verified per-group CSV')
    target=Path(config['public_output_dir'])/metadata.name
    _copy(metadata,target)
    return str(target)


def run(config,progress=None):
    config=dict(config); output=Path(config['output_dir']).resolve(); output.mkdir(parents=True,exist_ok=True)
    config.update(input_hdf5=str(Path(config['input_hdf5']).resolve()),public_output_dir=str(output))
    config.setdefault('acceptreject_methods',[str(i) for i in range(1,9)])
    config.setdefault('acceptreject_required_votes',5)
    methods=config['acceptreject_methods']; votes=config['acceptreject_required_votes']
    if not isinstance(methods,list) or not methods or len(methods)!=len(set(methods)) or any(m not in {str(i) for i in range(1,9)} for m in methods):
        raise ValueError('Accept/reject methods must be a nonempty unique list of slot IDs 1–8')
    if isinstance(votes,bool) or not isinstance(votes,int) or not 1<=votes<=len(methods): raise ValueError('Required votes must be between one and the number of enabled methods')
    config['processing_signature']=processing_signature(config)
    progress=progress or (lambda event:None); lock=threading.Lock()
    def emit(event):
        with lock: progress(dict(event,stage='acceptreject'))
    with ProjectLock(output): return _run(config,output,emit)


def _run(config,output,emit):
    signature=config['processing_signature']
    checkpoint=output/'checkpoints'/'stage1_parallel_v1'; checkpoint.mkdir(parents=True,exist_ok=True)
    storage=hashlib.sha256(str(output).encode()).hexdigest()[:20]
    storage_root=Path(tempfile.gettempdir())/'SignalQCStage1'/storage
    root=storage_root/signature[:16]; root.mkdir(parents=True,exist_ok=True)
    atomic_json(checkpoint/'storage.json',dict(path=str(root),processing_signature=signature))
    _drain_output_workers(storage_root,root,emit)
    manifest=_prepare(config,root,emit)
    groups={}
    for group in manifest.get('groups',[]):
        if not group['valid']: continue
        if group['group_key'] in groups: raise ValueError('Prepared group key collision')
        groups[group['group_key']]=group
    statuses={}
    for row in _csv(manifest['status_csv']).to_dict('records'):
        pair=(str(row['event_id']),str(row['trace_id']))
        if pair in statuses: raise ValueError('Prepared trace identity repeats')
        statuses[pair]=row
    receipts={}; issues=[]
    if config.get('resume',True):
        for key,group in groups.items():
            path=checkpoint/'receipts'/(key+'.json')
            if not filesystem_path(path).exists(): continue
            try:
                receipt=_read_receipt(path,group,checkpoint,config)
                if receipt:
                    receipts[key]=receipt
                    for row in receipt['statuses']: statuses[(row['event_id'],row['trace_id'])]=row
            except (OSError,ValueError,KeyError,TypeError) as exc:
                _copy(path,checkpoint/'corrupt_receipts'/(key+'_'+uuid.uuid4().hex[:12]+'.json'))
                issues.append(dict(group_key=key,message='Corrupt group receipt retained for diagnosis; available outputs will be verified again: '+str(exc)))
    previous,blocked,recovery_issues=_old_jobs(root,groups,config); issues.extend(recovery_issues)
    if config.get('resume',True):
        for folder,selected in previous: _collect(folder,selected,checkpoint,config,receipts,statuses,issues,emit)
    pending=[group for key,group in groups.items() if key not in receipts and key not in blocked]
    for key in blocked-set(receipts):
        for pair in _group_pairs(groups[key]): statuses[pair].update(status='pending',reason='Previous worker closure cannot be verified',figure_path='')
    count=runtime.worker_limit(config.get('workers',1),len(pending)) if pending and not _cancelled(config) else 0
    count=min(count,max(0,int(config.get('workers',1))-sum(bool(i.get('uncertain_worker')) for i in recovery_issues)))
    emit(dict(type='start',workers=count,requested_workers=config.get('workers',1),total_groups=len(groups),completed_groups=len(receipts),
              message=f'Accept/reject: {len(pending)} ready three-component groups, {count} group workers.'))
    atomic_json(checkpoint/'run.json',dict(processing_signature=signature,workers=count,requested_workers=config.get('workers',1),
        total_groups=len(groups),already_completed=len(receipts),pending_groups=len(pending),writer_pid=os.getpid()))
    jobs=[]
    for index in range(count):
        selected=pending[index::count]
        folder=root/('worker_'+uuid.uuid4().hex[:12])
        job=_job(folder,config,'worker',index+1,context_index_rds=manifest['context_index_rds'],resume=True,
                 worker_groups=[dict(event_index=g['event_index'],group_index=g['group_index']) for g in selected])
        job['groups']=selected; jobs.append(job)
    with ThreadPoolExecutor(max_workers=max(1,count)) as pool:
        futures={pool.submit(_launch,job,emit):job for job in jobs}
        for future in as_completed(futures):
            job=futures[future]; folder=Path(job['config']).parent
            try: future.result()
            except Exception as exc: issues.append(dict(worker_id=job['worker_id'],message=str(exc)))
            if runtime._worker_is_closed(folder): _collect(folder,job['groups'],checkpoint,config,receipts,statuses,issues,emit)
            else:
                for group in job['groups']:
                    for pair in _group_pairs(group): statuses[pair].update(status='pending',reason='Worker closure uncertain after launch; private results not opened',figure_path='')
    # Diagnostic rows may exist even when an unavailable vote made a group
    # indeterminate. Read only closed workers and only their exact assignments.
    method_rows={}
    fields=['event_id','trace_id','acceptreject_methods','acceptreject_required_votes','voting_reason']
    fields += [f'acrj{i}_status' for i in range(1,9)]+[f'acrj{i}' for i in range(1,9)]+[f'score{i}' for i in range(1,9)]
    for receipt in receipts.values():
        for row in _csv(checkpoint/receipt['artifacts']['csv']['path']).to_dict('records'):
            method_rows[(str(row['event_id']),str(row['trace_id']))]={name:row.get(name,'') for name in fields}
    closed_jobs,_,_= _old_jobs(root,groups,config)
    for folder,selected in sorted(closed_jobs,key=lambda item:(item[0]/'worker_config.json').stat().st_mtime_ns,reverse=True):
        path=folder/'method_execution.csv'
        if not path.exists(): continue
        allowed={pair for group in selected for pair in _group_pairs(group)}
        try:
            for row in _csv(path).to_dict('records'):
                pair=(str(row['event_id']),str(row['trace_id']))
                if pair in allowed and pair not in method_rows: method_rows[pair]=row
        except (OSError,ValueError,KeyError,TypeError) as exc:
            issues.append(dict(checkpoint=str(path),message='Method execution diagnostics could not be read: '+str(exc)))
    method_path=output/'method_execution.csv'
    if method_rows: _atomic_csv(method_path,list(method_rows.values()))
    status_path=output/'stage1_status.csv'; _atomic_csv(status_path,list(statuses.values()),columns=['source_file','event_id','trace_id','status','reason','figure_path'])
    metadata=''
    try: metadata=_publish(config,root,checkpoint,receipts,emit)
    except Exception as exc: issues.append(dict(stage='publish',message=str(exc)))
    counts={state:sum(row['status']==state for row in statuses.values()) for state in ('accepted','rejected','skipped','error','pending')}
    status='cancelled' if _cancelled(config) else 'failed' if not metadata or not (counts['accepted']+counts['rejected']) else 'completed_with_issues' if issues or any(counts[s] for s in ('skipped','error','pending')) else 'completed'
    result=dict(stage='acceptreject',status=status,counts=counts,status_counts=counts,workers=count,requested_workers=config.get('workers',1),
        input_hdf5=config['input_hdf5'],output_csv=metadata,processing_signature=signature,issues=issues,
        methods=config['acceptreject_methods'],required_votes=config['acceptreject_required_votes'],
        output_files=[str(status_path)]+([metadata] if metadata else [])+([str(method_path)] if method_rows else []))
    atomic_json(output/'acceptreject_summary.json',result); atomic_json(output/'stage1_summary.json',result)
    emit(dict(type='complete',message='Accept/reject groups and available metadata have been saved.',**result))
    return result


if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('Usage: stage1_parallel_v1.py config.json')
    def report(event): print('SIGNALQC_EVENT '+json.dumps(event,ensure_ascii=True),flush=True)
    try:
        result=run(_json(sys.argv[1]),report)
        raise SystemExit(130 if result['status']=='cancelled' else 3 if result['status']=='failed' else 2 if result['status']=='completed_with_issues' else 0)
    except Exception as exc:
        report(dict(stage='acceptreject',type='error',message=str(exc))); raise
