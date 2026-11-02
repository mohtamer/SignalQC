"""NCM intensity-measure inputs and execution; computations remain in the R main."""
from __future__ import annotations

from pathlib import Path
import os
import json
import subprocess
import math
import sys
import hashlib
import uuid
import re
from collections import Counter
import h5py
import pandas as pd

from .project import APP_ROOT, atomic_json, signature
from .data import hdf5_records, text_attr
from .results_v2 import _normalize, _read_stage_table

MEASURES = {
    'psa_h1':'PSA — H1 (g)', 'psa_h2':'PSA — H2 (g)', 'psa_v':'PSA — vertical (g)',
    'rotd50':'RotD50', 'rotd100':'RotD100', 'gmrotd50':'gmRotD50',
    'eas':'Horizontal EAS (g·s)', 'fav':'Vertical FAS / FAV (g·s)',
    'duration':'Arias intensity and durations', 'cav':'CAV, CAV5 and CAV0.05g (g·s)',
}
REQUIRED = ['trace.id','event_id','component','station','network']
OPTIONAL = ['rec_id','timeStep','pga','rawpga','hypdis','epidis','est.hypdis.Pwave.duration','fcHP','fcLP']

def _trace_key(value):
    # Legacy NCM filmeta may retain fractional seconds omitted from old HDF5 keys.
    return re.sub(r'(T[0-9]{6})\.[0-9]+$',r'\1',str(value).strip())

def settings_errors(config):
    if 'intensity' not in config.get('stages',[]):return []
    selected=config.get('im_measures',list(MEASURES))
    errors=[]
    if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected) or any(k not in MEASURES for k in selected):
        errors.append('Select at least one supported intensity-measure group without duplicates.')
    if config.get('im_metadata') and not Path(config['im_metadata']).is_file():
        errors.append('The selected intensity-measure metadata table is unavailable.')
    return errors

def _usable(record):
    for key in ('Trimmed_Filtered','Filtered_Trimmed'):
        if key in record:
            return str(record[key]).strip().upper()=='YES'
    return str(record.get('stage2_complete','')).strip().lower() in ('true','1','yes')

def _dt(record):
    for key in ('timeStep','dt','delta','time_step'):
        try:
            value=float(record.get(key,''))
            if math.isfinite(value) and value>0:return value
        except (ValueError,TypeError):pass
    try:
        sr=float(record.get('sampling_rate',''))
        return 1/sr if math.isfinite(sr) and sr>0 else None
    except (ValueError,TypeError):return None

def metadata_file(source, explicit=''):
    if explicit:return str(Path(explicit).resolve())
    source=Path(source)
    base=source.stem
    for suffix in ('_processed_2','_processed'):
        if base.endswith(suffix):base=base[:-len(suffix)];break
    candidates=[source.parent/(base+'.h5filmeta.xlsx'),source.parent/(base+'.h5filmeta.csv'),
                source.parent/(base+'_results.xlsx'),source.parent/(base+'.h5filmeta_sc.xlsx')]
    return next((str(p) for p in candidates if p.is_file()),'')

def review_fingerprint(config, files):
    paths=list(files)
    paths.extend(metadata_file(p,config.get('im_metadata','')) for p in files)
    return signature([p for p in paths if p], {'im_measures':config.get('im_measures',list(MEASURES))})

def inspect_processed(source):
    rows=hdf5_records(source)
    accepted=[]; issues=[]; groups=set()
    with h5py.File(source,'r') as hf:
        for row in rows:
            # The original IM main associates records by their event/trace paths.
            row['event_id']=row['hdf5_path'].strip('/').split('/')[0]
            if not _usable(row):continue
            gr=hf[row['hdf5_path']]
            missing=[]
            expected={'acceleration_waveform':('m/s^2','m/s2','m/s²','m/s/s'),
                      'velocity_waveform':('m/s',), 'displacement_waveform':('m',)}
            for name,units in expected.items():
                if name not in gr or not isinstance(gr[name],h5py.Dataset) or gr[name].ndim!=1 or gr[name].size<10:
                    missing.append(name);continue
                unit=str(text_attr(gr[name].attrs.get('units',row.get(name.removesuffix('_waveform')+'_units',units[0])))).lower().replace(' ','')
                if unit not in units:missing.append(f'{name} has unsupported units {unit!r}; expected {units[0]}')
            if _dt(row) is None:missing.append('valid sampling rate or time step')
            if missing:
                issues.append(row['trace_id']+': '+', '.join(missing));continue
            component=str(row.get('component',row.get('channel',''))).upper()
            if not component.endswith(('E','N','Z')):
                issues.append(row['trace_id']+': an E/N/Z component code is required by IMs_FINAL_v4');continue
            accepted.append(row)
            groups.add((str(row['event_id']),str(row.get('network','')),str(row.get('station','')),component[:2]))
    return dict(file=str(Path(source).resolve()),records=rows,usable=accepted,usable_traces=len(accepted),record_sets=len(groups),issues=issues)

def prepare_metadata(source, supplied, target):
    review=inspect_processed(source)
    if not review['usable']:raise ValueError('No complete, processed SI acceleration/velocity/displacement histories were found. '+ '; '.join(review['issues'][:3]))
    rows=review['usable']
    supplied=metadata_file(source,supplied)
    exact={}; by_trace={}; legacy={}; issues=list(review['issues'])
    source_keys=Counter((str(r['event_id']),_trace_key(r['trace_id'])) for r in rows)
    source_traces=Counter(_trace_key(r['trace_id']) for r in rows)
    if supplied:
        frame=_read_stage_table(supplied)
        for raw in frame.to_dict('records'):
            row=_normalize(raw)
            trace=str(row.get('trace_id','')).strip(); event=str(row.get('event_id','')).strip()
            if not trace:continue
            token=(event,trace)
            if token in exact:raise ValueError(f'Duplicate intensity metadata key: {token}')
            exact[token]=row;by_trace.setdefault(trace,[]).append(row)
            legacy.setdefault((event,_trace_key(trace)),[]).append(row)
        if not exact:raise ValueError('The metadata table contains no trace identifiers. Select processing results, not an IM workbook.')
    normalized=[]; unmatched=0
    for record in rows:
        event=str(record['event_id']);trace=str(record['trace_id'])
        matched=exact.get((event,trace))
        if matched is None:
            candidates=[r for r in by_trace.get(trace,[]) if not str(r.get('event_id','')).strip()]
            if len(candidates)>1:raise ValueError('Ambiguous metadata trace identifier: '+trace)
            matched=candidates[0] if candidates else {}
        if not matched:
            token=(event,_trace_key(trace)); candidates=legacy.get(token,[])
            if len(candidates)==1 and source_keys[token]==1:
                matched=candidates[0]
            elif not candidates and source_traces[_trace_key(trace)]==1:
                candidates=legacy.get(('',_trace_key(trace)),[])
                if len(candidates)==1:matched=candidates[0]
        if not matched:unmatched+=1
        values={name:record.get(name,None) for name in OPTIONAL}
        values.update({'trace.id':trace,'event_id':event,'component':record.get('component',record.get('channel','')),
                       'station':record.get('station',''),'network':record.get('network',''),'timeStep':_dt(record)})
        for name in OPTIONAL+['station','network','component']:
            value=matched.get(name)
            if value is not None and str(value).strip() not in ('','nan','NaN','None'):
                values[name]=value
        if str(values['component']).upper()!=str(record.get('component',record.get('channel',''))).upper():
            raise ValueError('Metadata component conflicts with the stored waveform: '+trace)
        for field in ('station','network'):
            stored=str(record.get(field,'')).strip()
            if stored and str(values[field]).strip()!=stored:
                raise ValueError(f'Metadata {field} conflicts with the stored waveform: {trace}')
        # The HDF5 sampling interval is authoritative; reject stale table values.
        try:
            candidate=float(values['timeStep'])
            if not math.isclose(candidate,_dt(record),rel_tol=1e-9,abs_tol=1e-12):
                raise ValueError('Metadata sample interval conflicts with HDF5: '+trace)
        except (TypeError,ValueError) as exc:raise ValueError(str(exc)) from exc
        normalized.append(values)
    if supplied:
        if unmatched:issues.append(f'{unmatched} processed traces had no metadata-table match; stored HDF5 metadata was used.')
    table=pd.DataFrame(normalized,columns=REQUIRED+OPTIONAL)
    # Preserve an unchanged metadata file's timestamp for the RDS resume manifest.
    text=table.to_json(orient='split',default_handler=str)
    digest=hashlib.sha256(text.encode()).hexdigest()
    workbook=Path(target)/'im_metadata.xlsx'; marker=Path(target)/'im_metadata.sha256'
    if not workbook.is_file() or not marker.is_file() or marker.read_text()!=digest:
        temp=workbook.with_name('.im_metadata_'+uuid.uuid4().hex+'.xlsx')
        table.to_excel(temp,index=False);os.replace(temp,workbook);marker.write_text(digest)
    audit=dict(source_hdf5=str(Path(source).resolve()),metadata_source=supplied or 'stored HDF5 attributes',
               usable_traces=review['usable_traces'],record_sets=review['record_sets'],issues=issues,
               qc_selection='Legacy Trimmed_Filtered=Yes or stage2_complete; no additional sanity-check acceptance rule',
               rows=normalized)
    atomic_json(Path(target)/'im_input_review.json',audit)
    return workbook,audit

def _worker_count(requested,sets):
    # Same conservative memory budget as filtering; one pool across record sets.
    from .pipeline import load_engine
    scheduler=load_engine('filtering','stage2_parallel_v2.py')
    return min(30,scheduler.worker_limit(requested,sets))

def run_intensity(config,progress=lambda event:None,cancelled=lambda:False):
    problems=settings_errors(dict(config,stages=['intensity']))
    if problems:raise ValueError(' '.join(problems))
    target=Path(config['output_dir']);target.mkdir(parents=True,exist_ok=True)
    source=Path(config['input_hdf5']).resolve()
    metadata,audit=prepare_metadata(source,config.get('im_metadata',''),target)
    count=_worker_count(config.get('workers',1),audit['record_sets'])
    output=target/(source.stem+'_IMs_output.xlsx')
    cfg=dict(input_hdf5=str(source),metadata_file=str(metadata),output_workbook=str(output),output_dir=str(target),
             workers=count,resume=config.get('resume',True),cancel_file=config.get('cancel_file',''),
             enabled_measures=config.get('im_measures',list(MEASURES)),processing_signature=config['processing_signature'])
    config_file=target/'run_config.json';atomic_json(config_file,cfg)
    exe=Path(config.get('rscript_executable',''))
    if not exe.is_file():raise ValueError('Select a valid Rscript executable in Runtime settings.')
    env=os.environ.copy()
    for key in ('R_HOME','LC_ALL','LC_CTYPE','LANG','PYTHONHOME','PYTHONPATH'):env.pop(key,None)
    env.update(R_LIBS_USER=os.pathsep.join(str(APP_ROOT/'runtime'/name) for name in ('R-IM-library','R-library')),
               OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    from .runtime_env_v1 import runtime_environment
    env=runtime_environment(env,intensity=True)
    script=APP_ROOT/'engines/intensity/IMs_FINAL_v7_app.R'
    summary_file=target/'intensity_summary.json'
    summary_file.unlink(missing_ok=True)
    progress(dict(stage='intensity',type='start',total=audit['record_sets'],workers=count,
                  message=f'Intensity measures: {audit["usable_traces"]} traces, {audit["record_sets"]} record sets, {count} worker(s).'))
    with (target/'run.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([str(exe),str(script),str(config_file)],cwd=script.parent,env=env,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        for line in process.stdout:
            log.write(line);log.flush()
            text=line.strip()
            if text.startswith('SIGNALQC_EVENT '):
                try:progress(json.loads(text[len('SIGNALQC_EVENT '):]))
                except ValueError:pass
            elif text:progress(dict(stage='intensity',message=text))
        code=process.wait()
    if not summary_file.is_file():raise RuntimeError(f'Intensity-measure execution failed (exit {code}); see {target / "run.log"}')
    result=json.loads(summary_file.read_text(encoding='utf-8-sig'))
    if cancelled() or result.get('status')=='cancelled':return dict(result,status='cancelled')
    if code or result['status']=='failed':raise RuntimeError(result.get('message','Intensity-measure calculation failed'))
    if not output.is_file():raise RuntimeError('The IM main did not produce its workbook.')
    result.update(stage='intensity',input=str(source),output_files=[str(output)],input_review=audit)
    atomic_json(summary_file,result)
    return result
