from __future__ import annotations
import os
import csv
import math
from pathlib import Path
import h5py
import numpy as np
import pandas as pd

WAVE_EXT={'.seed','.mseed','.miniseed','.ms','.output','.sac','.segy','.sgy','.gse','.gse2','.wav','.gcf','.slist','.tspair','.su','.ah','.qhd'}
RESPONSE_EXT={'.resp','.dataless','.xml','.stationxml','.xseed'}
SKIP_DIRS={'.git','.venv','__pycache__','.idea','node_modules','build','runtime'}

def walk_files(paths, excludes=()):
    seen=set()
    roots=[Path(p).resolve() for p in excludes]
    if isinstance(paths,(str,Path)): paths=[paths]
    for name in paths:
        p=Path(name).expanduser().resolve()
        if not p.exists():
            continue
        if any(p==x or x in p.parents for x in roots): continue
        def scan(folder):
            for root,dirs,names in os.walk(folder):
                dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS and not any(Path(root,d).resolve()==x or x in Path(root,d).resolve().parents for x in roots))
                for name in sorted(names):
                    if not name.startswith('~$'): yield Path(root,name)
        files=[p] if p.is_file() else scan(p)
        for f in files:
            f=f.resolve()
            if any(f==x or x in f.parents for x in roots) or f in seen:
                continue
            seen.add(f)
            yield f

def text_attr(value):
    if isinstance(value, bytes):
        return value.decode('utf-8',errors='replace')
    if isinstance(value,np.generic):
        return value.item()
    if isinstance(value,np.ndarray):
        return value.tolist()
    return value

def hdf5_records(path, limit=None):
    records=[]
    with h5py.File(path,'r') as f:
        def visitor(name,obj):
            if not isinstance(obj,h5py.Group):
                return
            if any(part.startswith('_pending_') for part in name.split('/')): return
            datasets={n:ds for n,ds in obj.items() if isinstance(ds,h5py.Dataset) and ds.ndim==1}
            if not any(n in datasets for n in ('acceleration_waveform','raw_waveform','acceleration','acc','velocity_waveform','velocity','vel','displacement_waveform','displacement','disp')):
                return
            row={k:text_attr(v) for k,v in obj.parent.attrs.items()}
            row.update({f'event_{k}':text_attr(obj.parent.attrs[k]) for k in ('latitude','longitude','depth','magnitude') if k in obj.parent.attrs})
            row.update({k:text_attr(v) for k,v in obj.attrs.items()})
            row.update(file=str(Path(path).resolve()),hdf5_path='/'+name,
                       event_id=row.get('event_id',name.split('/')[0]),
                       trace_id=name.split('/')[-1],datasets=list(datasets),
                       dataset_sizes={n:int(ds.size) for n,ds in datasets.items()},
                       samples=max((ds.size for ds in datasets.values()),default=0))
            records.append(row)
            if limit and len(records)>=limit:
                return True
        f.visititems(visitor)
    return records

def inspect_inputs(config, project_folder=None, progress=None):
    from .workflow_v2 import relevant_input_config
    config = relevant_input_config(config)
    from .ncm_data_v1 import reference_fingerprint
    reference_before=reference_fingerprint(config)
    paths=config.get('raw_paths',[])+config.get('hdf5_paths',[])+config.get('response_paths',[])
    from .project import APP_ROOT
    selected=[]
    for name in paths:
        p=Path(name).resolve()
        # Explicitly selecting existing outputs is allowed. Exclude generated
        # app/project trees only when scanning a broader ancestor directory.
        exclude=[root for root in [Path(project_folder).resolve() if project_folder else None,APP_ROOT]
                 if root is not None and root!=p and p in root.parents]
        selected.extend(walk_files([p],exclude))
    files=sorted(set(selected),key=str)
    from .hdf5_profiles_v1 import hdf5_review_fingerprint
    hdf5_files=[str(path) for path in files if path.suffix.lower() in ('.h5','.hdf5')]
    hdf5_review_before=hdf5_review_fingerprint(config,hdf5_files)
    explicit_raw={Path(p).resolve() for p in config.get('raw_paths',[]) if Path(p).is_file()}
    explicit_response={Path(p).resolve() for p in config.get('response_paths',[]) if Path(p).is_file()}
    result=dict(raw_files=[],hdf5_files=[],response_files=[],other_files=[],issues=[],trace_count=0,
                event_count=0,networks=[],formats={},hdf5_details=[])
    networks=set()
    for i,path in enumerate(files):
        suffix=path.suffix.lower()
        if progress and (i%20==0):
            progress({'message':f'Inspecting {i+1} / {len(files)} files','current':i+1,'total':len(files)})
        if suffix in ('.h5','.hdf5'):
            result['hdf5_files'].append(str(path))
            try:
                from .hdf5_profiles_v1 import mapping_for_source, adapter_for_mapping
                mapping=mapping_for_source(config,path)
                if mapping.get('enabled'):
                    detail=adapter_for_mapping(mapping).preview_mapping(path,mapping)
                    result['trace_count']+=detail['traces']
                    result['event_count']+=detail['events']
                    networks.update(detail['networks'])
                    result['hdf5_details'].append(dict(detail,file=str(path)))
                    result['issues'].extend(f'{path.name}: {issue}' for issue in detail['issues'])
                    continue
                rows=hdf5_records(path)
                result['trace_count']+=len(rows)
                result['event_count']+=len({r['event_id'] for r in rows})
                for r in rows:
                    networks.add(str(r.get('network','unknown')))
                missing=sum(not any(r['dataset_sizes'].get(n,0)>0 for n in ('acceleration_waveform','acceleration','acc')) for r in rows)
                result['hdf5_details'].append({'file':str(path),'traces':len(rows),'events':len({r['event_id'] for r in rows}),'missing_acceleration':missing})
                if not rows: result['issues'].append(f'{path.name}: no supported event/trace waveform groups detected.')
                if missing: result['issues'].append(f'{path.name}: {missing} traces have no nonempty calibrated acceleration dataset.')
            except Exception as e:
                result['issues'].append(f'{path.name}: {e}')
                result['hdf5_details'].append(dict(file=str(path),traces=0,events=0,missing_acceleration=0,
                                                  mapping_error=str(e)))
        elif path in explicit_response or suffix in RESPONSE_EXT or path.name.upper().startswith('RESP.'):
            result['response_files'].append(str(path))
        elif suffix in WAVE_EXT or path in explicit_raw or config.get('scan_all_files',False) and any(Path(root).resolve() in path.parents for root in config.get('raw_paths',[])):
            result['raw_files'].append(str(path))
        else: result['other_files'].append(str(path))
    for name in paths:
        if not Path(name).exists(): result['issues'].append(f'Path does not exist: {name}')
    # Read waveform headers; one file may contain many records, stations and events.
    from obspy import read
    raw_traces=0
    association_engine=None;events=[]
    if 'ingestion' in config.get('stages',[]):
        from .pipeline import load_engine
        association_engine=load_engine('ingestion','hdf5_writer_v10_ncm.py')
        result['association_preview']=dict(filename=0,time=0,multiple=0,unmatched=0)
        try:events,_,_,notices=association_engine._catalogs(config)
        except Exception as exc:
            result['catalogue_error']=str(exc)
            result['issues'].append('Catalogue matching preview: '+str(exc))
    for i,name in enumerate(result['raw_files']):
        try:
            stream=read(name,headonly=True,format=None if config.get('waveform_format','auto')=='auto' else config['waveform_format'])
            raw_traces+=len(stream)
            formats={str(tr.stats.get('_format',Path(name).suffix or 'unidentified')) for tr in stream}
            for fmt in formats: result['formats'][fmt]=result['formats'].get(fmt,0)+1
            networks.update(tr.stats.network or '(missing)' for tr in stream)
            if association_engine:
                for trace in stream:
                    associations,_=association_engine._associate(trace,Path(name),events,config)
                    preview=result['association_preview']
                    if not associations:preview['unmatched']+=1;continue
                    preview['filename' if associations[0][0]['association_method']=='catalogue_filename' else 'time']+=1
                    if len(associations)>1:preview['multiple']+=1
        except Exception as e:
            result['issues'].append(f'{Path(name).name}: waveform header could not be read: {str(e)[:200]}')
        if progress: progress({'message':f'Waveform headers: {i+1} / {len(result["raw_files"])}','current':i+1,'total':len(result['raw_files'])})
    result['raw_trace_count']=raw_traces
    result['networks']=sorted(networks)
    result['counts']={k:len(result[k]) for k in ('raw_files','hdf5_files','response_files','other_files')}
    result['hdf5_review_fingerprint']=hdf5_review_fingerprint(config,result['hdf5_files'])
    result['ncm_reference_fingerprint']=reference_fingerprint(config)
    result['ncm_reference_changed']=reference_before!=result['ncm_reference_fingerprint']
    if result['ncm_reference_changed']:result['issues'].append('Selected metadata or response files changed during the scan. Scan again.')
    result['hdf5_review_changed']=result['hdf5_review_fingerprint']!=hdf5_review_before
    if result['hdf5_review_changed']:
        result['issues'].append('HDF5 files or their mapped metadata tables changed during inspection. Scan again before running.')
    return result

def read_table(path):
    p=Path(path)
    if p.suffix.lower() in ('.xlsx','.xls'):
        return pd.read_excel(p, dtype={'event_id':str,'trace_id':str,'trace.id':str,'network':str,'station':str,'location':str},keep_default_na=False)
    with p.open(encoding='utf-8-sig') as source: sample=source.read(65536)
    try: separator=csv.Sniffer().sniff(sample,delimiters=',;\t|').delimiter
    except csv.Error: separator=','
    return pd.read_csv(p, sep=separator, dtype={'event_id':str,'trace_id':str,'trace.id':str,'network':str,'station':str,'location':str},encoding='utf-8-sig',keep_default_na=False)

def read_waveform(record, dataset):
    with h5py.File(record['file'],'r') as f:
        group=f[record['hdf5_path']]
        ds=group[dataset]
        y=np.asarray(ds[:],dtype=float)
        attrs={k:text_attr(v) for k,v in group.attrs.items()}
        attrs.update({k:text_attr(v) for k,v in ds.attrs.items()})
    dt=None
    for key in ('dt','time_step','timeStep','delta'):
        if key in attrs:
            dt=float(attrs[key]); break
    if dt is None:
        rate=float(attrs.get('sampling_rate',float('nan')))
        dt=1/rate if math.isfinite(rate) and rate>0 else float('nan')
    if not math.isfinite(dt) or dt<=0:
        raise ValueError('Waveform has no valid sampling rate or time step; its time axis cannot be inferred.')
    if 'units' not in attrs:
        field='raw_units' if dataset=='raw_waveform' else 'acceleration_units' if dataset in ('acceleration_waveform','acceleration','acc') else 'velocity_units' if dataset in ('velocity_waveform','velocity','vel') else 'displacement_units'
        attrs['units']=attrs.get(field,'unknown')
    t=np.arange(len(y))*dt
    return t,y,attrs
