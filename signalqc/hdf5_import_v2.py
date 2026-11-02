"""Explicit layout adaptation for external HDF5, without altering source files.

Only storage layout and declared units change. Processing remains in the stage
engines. Missing physical metadata is preserved as missing, never estimated here.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

import h5py
import numpy as np
import pandas as pd

from .project import atomic_json, signature
from . import hdf5_import_v1 as legacy
from .hdf5_metadata_v2 import MappingContext, inspect_metadata_table, stage_readiness, file_digest

SCHEMA = 'signalqc.hdf5_layout.v2'
ALIASES = {
    'sampling_rate': ['sampling_rate', 'sample_rate', 'samplingrate', 'fs', 'sps'],
    'dt': ['dt', 'delta', 'timeStep', 'time_step', 'sampling_interval'],
    'start_time': ['start_time', 'starttime', 'record_start_time', 'starttime.s'],
    'origin_time': ['origin_time', 'event_time', 'origintime', 'earthquake_time'],
    'network': ['network', 'network_code', 'net'],
    'station': ['station', 'station_code', 'sta'],
    'location': ['location', 'location_code', 'loc'],
    'channel': ['channel', 'component', 'channel_code'],
    'event_id': ['event_id', 'eventid', 'event'],
    'trace_id': ['trace_id', 'trace.id', 'record_id'],
    'station_group_id': ['station_group_id'],
    'recording_group_id': ['recording_group_id'],
    'magnitude': ['magnitude', 'mag', 'event_magnitude'],
    'event_latitude': ['event_latitude', 'evla', 'earthquake_latitude'],
    'event_longitude': ['event_longitude', 'evlo', 'earthquake_longitude'],
    'event_depth': ['event_depth', 'evdp', 'event_depth_km'],
    'station_latitude': ['station_latitude', 'stla'],
    'station_longitude': ['station_longitude', 'stlo'],
    'station_elevation': ['station_elevation', 'stel', 'elevation', 'elevation_m'],
}
WAVE_NAMES = {
    'acceleration': ['acceleration_waveform', 'acceleration', 'acc', 'accel'],
    'raw': ['raw_waveform', 'raw', 'counts', 'raw_counts'],
    'velocity': ['velocity_waveform', 'velocity', 'vel'],
    'displacement': ['displacement_waveform', 'displacement', 'disp'],
}


def _plain(value):
    if isinstance(value, bytes): return value.decode('utf-8', errors='replace')
    if isinstance(value, np.generic): return _plain(value.item())
    if isinstance(value, np.ndarray):
        if value.ndim == 0: return _plain(value.item())
        return [_plain(x) for x in value.tolist()] if value.size <= 16 else f'array ({value.size} values)'
    return value


def _inspect_legacy_headers(path, limit=5000):
    """Read names, dimensions and small attributes only; never read waveform data."""
    datasets, attributes = [], set()
    def header_attributes(obj):
        result={}
        for key in obj.attrs:
            info=obj.attrs.get_id(key)
            size=int(np.prod(info.shape))
            if size>16:
                result[str(key)]=f'array ({size} values)'
            else:
                value=_plain(obj.attrs[key])
                result[str(key)]=value[:2000]+'…' if isinstance(value,str) and len(value)>2000 else value
        return result
    with h5py.File(path, 'r') as h5:
        def visit(name, obj):
            attributes.update(map(str, obj.attrs))
            if isinstance(obj, h5py.Dataset):
                datasets.append(dict(path='/' + name, shape=list(obj.shape), dtype=str(obj.dtype),
                                     units=str(_plain(obj.attrs.get('units', ''))), record_path=obj.parent.name,
                                     attributes=header_attributes(obj)))
                if len(datasets) >= limit: return True
        attributes.update(map(str,h5.attrs))
        h5.visititems(visit)
    suggestions = {}; ambiguities = []
    for kind, names in WAVE_NAMES.items():
        found = {Path(d['path']).name for d in datasets if len(d['shape']) in (1,2)
                 and Path(d['path']).name.lower() in names}
        # Different plausible names require a choice, rather than selecting one.
        if len(found) == 1:
            suggestions[kind] = '**/' + next(iter(found))
        elif len(found) > 1:
            ambiguities.append(f'{kind}: multiple plausible dataset names ({", ".join(sorted(found))}); select the intended dataset.')
    return dict(file=str(Path(path).resolve()), datasets=datasets, attributes=sorted(attributes),
                suggestions=suggestions, ambiguities=ambiguities, truncated=len(datasets) >= limit)


def inspect_hdf5(path, limit=5000):
    """Inventory scoped header sources; sample only small metadata scalars/text."""
    result = _inspect_legacy_headers(path,limit)
    sources = {}
    def add(kind,value,scope,owner,example,sample,shape,dtype):
        key = (kind,value,scope)
        if key not in sources:
            sources[key] = dict(kind=kind,value=value,scope=scope,source=dict(kind=kind,value=value,scope=scope),
                                owner_path=owner,record_examples=[example],sample=sample,shape=shape,dtype=dtype)
        elif example not in sources[key]['record_examples'] and len(sources[key]['record_examples'])<3:
            sources[key]['record_examples'].append(example)
    with h5py.File(path,'r') as h5:
        for header in result['datasets']:
            ds=h5[header['path']]
            if ds.ndim not in (1,2) or ds.dtype.kind not in 'iuf': continue
            for position,obj in enumerate(_objects(ds)):
                scope='dataset' if position==0 else 'parent:'+str(position-1)
                for name in obj.attrs:
                    info=obj.attrs.get_id(name); size=int(np.prod(info.shape))
                    sample=_plain(obj.attrs[name]) if size<=16 else f'array ({size} values)'
                    if isinstance(sample,str) and len(sample)>2000: sample=sample[:2000]+'…'
                    add('attribute',name,scope,obj.name,ds.name,sample,list(info.shape),str(info.dtype))
                if isinstance(obj,h5py.Group):
                    for name,child in obj.items():
                        if not isinstance(child,h5py.Dataset) or child.size>256: continue
                        # Numeric vectors may themselves be short waveforms;
                        # inventory their headers without reading samples.
                        sample=_plain(child[()]) if child.ndim==0 or (child.dtype.kind in 'SUO' and child.size<=16) else None
                        add('dataset',name,scope,child.name,ds.name,sample,list(child.shape),str(child.dtype))
        # Scalar metadata elsewhere in the file remain available as an explicit
        # absolute source, clearly labelled rather than suggested globally.
        for header in result['datasets']:
            ds=h5[header['path']]
            if ds.ndim==0:
                add('dataset',ds.name,'root',ds.name,'',_plain(ds[()]),[],str(ds.dtype))
    suggestions={}
    for field,names in ALIASES.items():
        choices=[entry['source'] for entry in sources.values() if entry['value'] in names and not str(entry['value']).startswith('/')]
        if choices: suggestions[field]=choices
    result.update(sources=list(sources.values()),metadata_suggestions=suggestions)
    return result


def _matches(name, pattern):
    name, pattern = name.lstrip('/'), str(pattern).lstrip('/')
    return fnmatch.fnmatchcase(name, pattern) or (pattern.startswith('**/') and fnmatch.fnmatchcase(name, pattern[3:]))


def _preview_v1(path, mapping):
    """Count mapped records and validate required headers without reading samples."""
    pattern = mapping.get('datasets', {}).get('acceleration', '')
    issues, networks, events = [], set(), set()
    count = 0
    if not pattern:
        return dict(traces=0, events=0, networks=[], issues=['Select the acceleration dataset pattern.'], missing_acceleration=0)
    with h5py.File(path, 'r') as h5:
        selected = []
        h5.visititems(lambda name, obj: selected.append('/'+name) if isinstance(obj,h5py.Dataset) and _matches(name,pattern) else None)
        for name in selected:
            ds=h5[name]
            if ds.dtype.kind not in 'iuf' or ds.ndim not in (1,2) or not ds.size:
                issues.append(f'{name}: a nonempty numeric 1D or explicitly mapped 2D waveform is required.'); continue
            if ds.ndim==2 and mapping.get('sample_axis') not in (0,1):
                issues.append(f'{name}: select the sample dimension for this matrix.'); continue
            components=range(ds.shape[1-mapping['sample_axis']]) if ds.ndim==2 else [None]
            for component in components:
                try:
                    rate=_num(_metadata(ds,'sampling_rate',mapping,component)); dt=_num(_metadata(ds,'dt',mapping,component))
                    if not (rate>0 or dt>0): raise ValueError('Map a sampling rate (Hz) or time step (s).')
                    if rate>0 and dt>0 and not math.isclose(rate*dt,1,rel_tol=1e-8): raise ValueError('Sampling rate and time step conflict.')
                    _unit(ds,'acceleration',mapping)
                    labels=mapping.get('components',[])
                    if component is not None and labels and len(labels)!=ds.shape[1-mapping['sample_axis']]:
                        raise ValueError('Component labels must match the non-sample dimension.')
                    for field in ('start_time','origin_time'):
                        _time(_metadata(ds,field,mapping,component))
                    networks.add(_label(_metadata(ds,'network',mapping,component)) or '(missing)')
                    event=_explicit_id(_metadata(ds,'event_id',mapping,component))
                    _explicit_id(_metadata(ds,'trace_id',mapping,component))
                    events.add(event or _label(_metadata(ds,'origin_time',mapping,component)) or 'unassociated_records')
                    count+=1
                except (ValueError,TypeError,IndexError,KeyError) as exc:
                    issues.append(f'{name}' + (f' [component {component+1}]' if component is not None else '') + ': '+str(exc))
        if not selected: issues.append('The acceleration pattern matches no datasets.')
    return dict(traces=count,events=len(events),networks=sorted(networks),issues=issues,missing_acceleration=0,mapped=True)


def preview_mapping(path,mapping):
    """Resolve and validate headers and exact joins without reading waveforms."""
    context=MappingContext(path,mapping)
    pattern=mapping.get('datasets',{}).get('acceleration','')
    records=[]; issues=[]; networks=set(); events={}; traces=set(); valid_events=set()
    if not pattern:
        return dict(traces=0,events=0,networks=[],issues=['Select the acceleration dataset pattern.'],records=[],missing_acceleration=0,mapped=True,table=context.table_summary())
    with h5py.File(path,'r') as h5:
        selected=[]
        h5.visititems(lambda name,obj:selected.append('/'+name) if isinstance(obj,h5py.Dataset) and _matches(name,pattern) else None)
        for name in sorted(selected):
            ds=h5[name]
            if ds.dtype.kind not in 'iuf' or ds.ndim not in (1,2) or not ds.size:
                issues.append(f'{name}: a nonempty real numeric 1D or explicitly mapped 2D waveform is required.'); continue
            axis=mapping.get('sample_axis')
            if ds.ndim==2 and axis not in (0,1):
                issues.append(f'{name}: select the sample dimension for this matrix.'); continue
            components=range(ds.shape[1-axis]) if ds.ndim==2 else [None]
            for component in components:
                fields,provenance,errors=context.record(ds,component)
                row=dict(dataset=name,component_index=component,fields=fields,provenance=provenance,errors=errors,
                         stage_readiness=stage_readiness(fields),waveform_samples_validated=False)
                try:
                    if errors: raise ValueError('; '.join(errors))
                    labels=mapping.get('components',[])
                    if component is not None and labels and len(labels)!=ds.shape[1-axis]:
                        raise ValueError('Component labels must match the non-sample dimension.')
                    rate,dt=_num(fields['sampling_rate']),_num(fields['dt'])
                    if not (rate>0 or dt>0): raise ValueError('Map a sampling rate (Hz) or time step (s).')
                    if rate>0 and dt>0 and not math.isclose(rate*dt,1,rel_tol=1e-8): raise ValueError('Sampling rate and time step conflict.')
                    _unit(ds,'acceleration',mapping)
                    start,origin=_time(fields['start_time']),_time(fields['origin_time'])
                    event=_explicit_id(fields['event_id']) or (origin.strftime('%Y%m%d_%H%M%S') if origin is not None else 'unassociated_records')
                    explicit=_explicit_id(fields['trace_id'])
                    vals=dict(origin_time=_iso(origin),latitude=_num(fields['event_latitude']),longitude=_num(fields['event_longitude']),
                              depth=_num(fields['event_depth']),magnitude=_num(fields['magnitude']))
                    if event in events:
                        for key,value in vals.items():
                            old=events[event][key]
                            equal=(isinstance(value,float) and np.isnan(value) and isinstance(old,float) and np.isnan(old)) or old==value
                            if not equal: raise ValueError(f'Conflicting {key} values map to the same event ID; map distinct events explicitly.')
                    if explicit and (event,explicit) in traces: raise ValueError('Duplicate explicit trace ID within this event.')
                    events[event]=vals
                    if explicit: traces.add((event,explicit))
                    valid_events.add(event); networks.add(_label(fields['network']) or '(missing)')
                    row.update(valid=True,event_id=event,trace_id=explicit,normalization_ready=True)
                except (ValueError,TypeError,IndexError,KeyError,OverflowError) as exc:
                    if not errors: errors.append(str(exc))
                    row.update(valid=False,normalization_ready=False)
                    issues.append(name+(f' [component {component+1}]' if component is not None else '')+': '+str(exc))
                records.append(row)
        if not selected: issues.append('The acceleration pattern matches no datasets.')
    return dict(traces=sum(r['valid'] for r in records),events=len(valid_events),networks=sorted(networks),issues=issues,
                records=records,missing_acceleration=0,mapped=True,table=context.table_summary(),header_only=True)


def _objects(dataset):
    yield dataset
    node = dataset.parent
    while True:
        yield node
        if node.name == '/': break
        node = node.parent


def _scalar(value, index=None):
    if isinstance(value, np.ndarray):
        if value.ndim == 0: return _plain(value.item())
        if sum(d>1 for d in value.shape)>1: raise ValueError('Metadata must be scalar or a one-dimensional component vector.')
        value=value.reshape(-1)
        if value.size == 1: return _plain(value[0])
        if index is not None and 0 <= index < value.size: return _plain(value[index])
        raise ValueError('Metadata contains multiple values; select an explicit field or constant.')
    if isinstance(value, (list, tuple)):
        if len(value) == 1: return _plain(value[0])
        if index is not None and 0 <= index < len(value): return _plain(value[index])
        raise ValueError('Metadata contains multiple values; select an explicit field or constant.')
    return _plain(value)


def _metadata(dataset, field, mapping, index=None):
    spec = mapping.get('metadata', {}).get(field, {})
    if not isinstance(spec, dict): spec = dict(kind='constant', value=spec)
    kind, value = spec.get('kind', 'auto'), spec.get('value', '')
    if kind == 'constant': return value
    if kind == 'path_part':
        parts = dataset.parent.name.strip('/').split('/')
        n = int(value)
        if n < 0 or n >= len(parts): raise ValueError(f'{field}: selected path level is absent')
        return parts[n]
    if kind == 'dataset':
        name = str(value)
        target = dataset.file.get(name) if name.startswith('/') else dataset.parent.get(name)
        if not isinstance(target, h5py.Dataset) or target.size > 256:
            raise ValueError(f'{field}: metadata dataset is missing or is not a small scalar/vector')
        return _scalar(target[()], index)
    names = [str(value)] if kind == 'attribute' else ALIASES[field]
    if kind not in ('attribute', 'auto'): raise ValueError('Unsupported metadata mapping: '+kind)
    for obj in _objects(dataset):
        for name in names:
            if name in obj.attrs: return _scalar(obj.attrs[name], index)
        if kind == 'auto' and isinstance(obj, h5py.Group):
            for name in names:
                child = obj.get(name)
                if isinstance(child, h5py.Dataset) and child.size <= 256:
                    return _scalar(child[()], index)
    return ''


def _num(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result != -999 else np.nan
    except (ValueError, TypeError): return np.nan


def _time(value):
    if value is None or str(value).strip() == '': return None
    # Numeric epoch timestamps have ambiguous units. Require an explicit ISO time.
    if isinstance(value, (int, float, np.number)):
        raise ValueError('Timestamp units are ambiguous; map an ISO timestamp or provide an ISO constant.')
    stamp = pd.Timestamp(str(value))
    if pd.isna(stamp): return None
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def _iso(stamp):
    if stamp is None: return ''
    seconds, ns = divmod(stamp.value, 1_000_000_000)
    return pd.Timestamp(seconds, unit='s', tz='UTC').strftime('%Y-%m-%dT%H:%M:%S') + f'.{ns:09d}Z'


def _safe(value, fallback):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value)).strip('_.') or fallback


def _label(value):
    value = _plain(value)
    if value is None or (isinstance(value, (float, int)) and not math.isfinite(value)):
        return ''
    text = str(value).strip()
    return '' if text.lower() in ('nan', 'none', 'null', 'na', 'n/a', '-999') else text


def _explicit_id(value):
    value = _label(value)
    if value and ('/' in value or '\x00' in value or value in ('.', '..')):
        raise ValueError('An explicit identifier must be a single HDF5 name without a slash or NUL.')
    return value


def _unit(dataset, kind, mapping):
    chosen = mapping.get(kind + '_units', 'auto')
    if chosen == 'auto':
        chosen = ''
        for obj in _objects(dataset):
            value = obj.attrs.get(kind+'_units', obj.attrs.get('units',''))
            if str(value).strip(): chosen = str(_plain(value)); break
    normalized = str(chosen).lower().replace(' ', '').replace('²', '^2').replace('**', '^')
    units = {
        'acceleration': {'m/s^2':1., 'm/s2':1., 'm/s/s':1., 'cm/s^2':.01, 'cm/s2':.01, 'cm/s/s':.01, 'gal':.01, 'g':9.80665},
        'velocity': {'m/s':1., 'cm/s':.01},
        'displacement': {'m':1., 'cm':.01},
    }
    if kind == 'raw':
        if normalized not in ('count','counts','digitalcounts','adc','adu'):
            raise ValueError('Raw counts require declared count/ADC units; calibrated amplitudes cannot be used as clipping counts.')
        return 1., 'counts'
    if normalized not in units[kind]:
        raise ValueError(f'{kind}: specify the physical units; missing/unsupported units {chosen!r} were not guessed')
    return units[kind][normalized], {'acceleration':'m/s^2','velocity':'m/s','displacement':'m'}[kind]


def _vector(dataset, mapping, component):
    if dataset.ndim == 1: return dataset[:]
    if dataset.ndim != 2: raise ValueError('Only 1D waveforms and explicitly mapped 2D sample/component arrays are supported.')
    if 'sample_axis' not in mapping: raise ValueError('Choose the sample dimension explicitly for a two-dimensional dataset.')
    axis = int(mapping['sample_axis'])
    if axis not in (0,1): raise ValueError('Sample axis must be 0 or 1.')
    if component is None: raise ValueError('Two-dimensional waveforms require a component selection.')
    return dataset[:,component] if axis == 0 else dataset[component,:]


def normalize_hdf5(source, project_folder, mapping, progress=None, cancelled=None):
    """Create a canonical, validated working HDF5; preserve per-record issues."""
    source = Path(source).resolve(); project_folder = Path(project_folder).resolve()
    mapping = json.loads(json.dumps(mapping))
    if not mapping.get('enabled'): return dict(status='unchanged', output_file=str(source), issues=[])
    pattern = mapping.get('datasets', {}).get('acceleration', '')
    if not pattern: raise ValueError('Select the acceleration dataset pattern in HDF5 layout mapping.')
    context=MappingContext(source,mapping)
    dependencies=[source,Path(__file__),Path(__file__).with_name('hdf5_metadata_v2.py'),Path(__file__).with_name('hdf5_import_v1.py')]
    stamp = signature(dependencies, context.fingerprint_settings())
    folder = project_folder/'inputs'/'normalized_hdf5'/stamp[:16]
    folder.mkdir(parents=True,exist_ok=True)
    output = folder/(source.stem+'.h5'); report_path=folder/'import_report.json'
    def file_digest(path):
        with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()
    if report_path.exists() and output.exists():
        prior=json.loads(report_path.read_text(encoding='utf-8'))
        if (prior.get('input_fingerprint') == stamp and prior.get('output_fingerprint') == signature([output],{})
                and prior.get('output_sha256') == file_digest(output)):
            return dict(prior, status='cached', output_file=str(output))
    temporary = folder/(source.stem+'.'+uuid.uuid4().hex+'.pending.h5')
    issues=[]; records=[]; total=0; completed=0; stopped=False
    try:
        with h5py.File(source,'r') as h5, h5py.File(temporary,'x') as out:
            paths=[]
            h5.visititems(lambda name,obj: paths.append('/'+name) if isinstance(obj,h5py.Dataset) else None)
            selected=sorted(p for p in paths if _matches(p,pattern))
            if not selected: raise ValueError('The acceleration pattern does not match any datasets in '+source.name)
            out.attrs.update(schema=SCHEMA, import_source_hdf5=str(source), import_fingerprint=stamp)
            for path in selected:
                ds=h5[path]
                if ds.dtype.kind not in 'iuf' or ds.ndim not in (1,2):
                    issues.append(dict(dataset=path, message='A real numeric 1D or explicitly mapped 2D dataset is required.')); continue
                axis=int(mapping.get('sample_axis',0))
                if ds.ndim==2 and 'sample_axis' not in mapping:
                    issues.append(dict(dataset=path,message='Choose the sample dimension explicitly for a two-dimensional dataset.')); continue
                if ds.ndim==2 and axis not in (0,1): raise ValueError('Sample axis must be 0 or 1.')
                components=range(ds.shape[1-axis]) if ds.ndim==2 else [None]
                for component in components:
                    if cancelled and cancelled(): stopped=True; break
                    total+=1; staged=None
                    try:
                        md,provenance,metadata_errors=context.record(ds,component)
                        if metadata_errors: raise ValueError('; '.join(metadata_errors))
                        for field in ('network','station','location','channel','event_id','trace_id'):
                            md[field] = _label(md[field])
                        explicit=mapping.get('components',[])
                        if component is not None and explicit:
                            if len(explicit)!=ds.shape[1-axis]: raise ValueError('The component labels must match the non-sample dimension.')
                            md['channel']=explicit[component]
                        rate,dt=_num(md['sampling_rate']),_num(md['dt'])
                        if np.isfinite(rate) and rate>0 and np.isfinite(dt) and dt>0 and not math.isclose(rate*dt,1,rel_tol=1e-8):
                            raise ValueError('Sampling rate and time step conflict. Correct the mapping.')
                        if not (np.isfinite(rate) and rate>0): rate=1/dt if np.isfinite(dt) and dt>0 else np.nan
                        if not (np.isfinite(rate) and rate>0): raise ValueError('A valid sampling rate (Hz) or time step (s) is required.')
                        start,origin=_time(md['start_time']),_time(md['origin_time'])
                        factor,units=_unit(ds,'acceleration',mapping)
                        values=_vector(ds,mapping,component)
                        if not len(values) or not np.all(np.isfinite(values)): raise ValueError('Acceleration is empty or contains nonfinite samples.')
                        with np.errstate(over='ignore',invalid='ignore'):
                            calibrated=np.asarray(values,dtype=np.float64)*factor
                        if not np.all(np.isfinite(calibrated)): raise ValueError('Acceleration unit conversion produced nonfinite samples.')
                        event_name=_explicit_id(md['event_id']) or (origin.strftime('%Y%m%d_%H%M%S') if origin is not None else 'unassociated_records')
                        event=out.require_group(event_name)
                        original_event=str(md['event_id'] or '')
                        if 'original_event_id' in event.attrs and event.attrs['original_event_id']!=original_event:
                            raise ValueError('Distinct event IDs become identical after filename-safe formatting; map distinct event IDs explicitly.')
                        event_values=dict(origin_time=_iso(origin), latitude=_num(md['event_latitude']), longitude=_num(md['event_longitude']), depth=_num(md['event_depth']), magnitude=_num(md['magnitude']))
                        for key,value in event_values.items():
                            if key in event.attrs:
                                old=event.attrs[key]
                                equal=(isinstance(value,(float,np.floating)) and np.isnan(value) and np.isnan(old)) or old==value
                                if not equal: raise ValueError(f'Conflicting {key} values map to the same event ID; map distinct events explicitly.')
                        event.attrs.update(event_id=event_name,original_event_id=original_event,depth_units='km',event_kind='catalog_event' if origin is not None else 'unassociated_recording',**event_values)
                        station=str(md['station'] or ''); channel=str(md['channel'] or '')
                        if start is not None:
                            sec,ns=divmod(start.value,1_000_000_000)
                            label=pd.Timestamp(sec,unit='s',tz='UTC').strftime('%Y%m%dT%H%M%S')
                            if ns: label+='.'+(f'{ns:09d}' if ns%1000 else f'{ns//1000:06d}')
                            base=_safe(station,'UNNAMED')+'_'+_safe(channel,'UNKNOWN')+'_'+label
                        else: base=_safe(ds.parent.name,'record')+'_'+_safe(channel,'UNKNOWN')
                        if str(md['trace_id']).strip(): base=_explicit_id(md['trace_id'])
                        trace_id=base; ordinal=2
                        if trace_id in event and str(md['trace_id']).strip():
                            raise ValueError('Duplicate explicit trace ID; select distinct identifiers rather than relabeling prior metadata.')
                        while trace_id in event:
                            trace_id=base+f'_record{ordinal:03d}'; ordinal+=1
                        staged=event.create_group('_pending_'+uuid.uuid4().hex)
                        attrs=dict(network=str(md['network'] or ''),station=station,location=str(md['location'] or ''),channel=channel,
                                   start_time=_iso(start),sampling_rate=rate,npts=len(values),trace_id=trace_id,
                                   rec_id='.'.join(str(md[k] or '') for k in ('network','station','location','channel')),
                                   latitude=_num(md['station_latitude']),longitude=_num(md['station_longitude']),elevation=_num(md['station_elevation']),
                                   elevation_units='m',source_file=str(source),import_dataset_path=path,
                                   import_component_index=-1 if component is None else component,
                                   acceleration_units='m/s^2',ingestion_status='mapped_calibrated_acceleration',ready_for_processing=True)
                        absent = [field for field in ('magnitude','event_latitude','event_longitude','event_depth','station_latitude','station_longitude')
                                  if not np.isfinite(_num(md[field]))]
                        absent += [field for field, value in (('start_time', start),('origin_time',origin)) if value is None]
                        attrs['metadata_status'] = 'incomplete' if absent else 'complete'
                        attrs['missing_physical_metadata'] = ','.join(absent)
                        for field in ('station_group_id','recording_group_id'):
                            if _label(md[field]): attrs[field]=_label(md[field])
                        if start is not None:
                            attrs.update(start_time_ns=start.value,end_time=_iso(start+pd.Timedelta(round((len(values)-1)/rate*1e9),unit='ns')))
                        if not station or not md['network']:
                            attrs['identity_status']='source_labels_incomplete'
                        staged.attrs.update(**attrs)
                        staged.create_dataset('acceleration_waveform',data=calibrated,compression='gzip').attrs['units']=units
                        for kind in ('raw','velocity','displacement'):
                            other_pattern=mapping.get('datasets',{}).get(kind,'')
                            if not other_pattern: continue
                            matches=[p for p in paths if _matches(p,other_pattern)]
                            local=[p for p in matches if h5[p].parent.name==ds.parent.name]
                            matches=local if local else matches if len(selected)==1 else []
                            if len(matches)!=1:
                                issues.append(dict(dataset=path,component=component,message=f'{kind}: no unique matching dataset; optional waveform omitted.')); continue
                            other=h5[matches[0]]
                            try:
                                if other.dtype.kind not in 'iuf': raise ValueError('waveform samples are not real numeric values')
                                if ds.ndim==2 and ds.shape[1-axis]>1 and other.ndim==1:
                                    raise ValueError('one waveform cannot be assigned to multiple acceleration components')
                                other_rate=_num(context.resolve(other,'sampling_rate',component,anchor=ds)[0])
                                other_dt=_num(context.resolve(other,'dt',component,anchor=ds)[0])
                                if np.isfinite(other_rate) and other_rate>0 and not math.isclose(other_rate,rate,rel_tol=1e-8):
                                    raise ValueError('sampling rate differs from acceleration')
                                if np.isfinite(other_dt) and other_dt>0 and not math.isclose(other_dt*rate,1,rel_tol=1e-8):
                                    raise ValueError('time step differs from acceleration')
                                other_start=_time(context.resolve(other,'start_time',component,anchor=ds)[0])
                                if other_start is not None and start is not None and other_start.value!=start.value:
                                    raise ValueError('start time differs from acceleration')
                                other_channel=str(context.resolve(other,'channel',component,anchor=ds)[0] or '')
                                if other_channel and channel and other_channel!=channel:
                                    raise ValueError('component label differs from acceleration')
                                other_factor,other_unit=_unit(other,kind,mapping)
                                data=_vector(other,mapping,component)
                                if len(data)!=len(values): raise ValueError('length differs from acceleration; time alignment is not established')
                                with np.errstate(over='ignore',invalid='ignore'):
                                    stored=data if kind=='raw' else np.asarray(data,dtype=np.float64)*other_factor
                                if not np.all(np.isfinite(stored)): raise ValueError('waveform or unit conversion contains nonfinite samples')
                                name={'raw':'raw_waveform','velocity':'velocity_waveform','displacement':'displacement_waveform'}[kind]
                                staged.create_dataset(name,data=stored,compression='gzip').attrs['units']=other_unit
                                staged.attrs[kind+'_units']=other_unit
                            except (ValueError,TypeError,IndexError) as exc:
                                issues.append(dict(dataset=path,component=component,message=f'{kind}: {exc}; optional waveform omitted.'))
                        staged.attrs['complete']=True
                        event.move(staged.name.rsplit('/',1)[-1],trace_id); staged=None
                        records.append(dict(original_dataset=path,component_index=component,event_id=event_name,trace_id=trace_id,
                                            metadata_provenance=provenance,stage_readiness=stage_readiness(md)))
                        completed+=1
                    except (ValueError,TypeError,IndexError,KeyError) as exc:
                        if staged is not None: del staged.parent[staged.name.rsplit('/',1)[-1]]
                        issues.append(dict(dataset=path,component=component,message=str(exc)))
                    if progress: progress(dict(message=f'HDF5 layout: {completed} records prepared from {source.name}',current=total))
                if stopped: break
            out.flush()
        current_context=MappingContext(source,mapping)
        if signature(dependencies,current_context.fingerprint_settings())!=stamp:
            raise ValueError('Source HDF5, metadata table, or importer changed during import; retry when they are no longer being edited.')
        if stopped:
            return dict(status='cancelled',output_file='',records=records,issues=issues)
        if not completed: raise ValueError('No records could be prepared. '+ '; '.join(x['message'] for x in issues[:5]))
        with h5py.File(temporary,'r') as checked:
            for row in records:
                group=checked[row['event_id']][row['trace_id']]
                if not group.attrs['complete'] or not len(group['acceleration_waveform']): raise ValueError('Prepared HDF5 verification failed.')
        os.replace(temporary,output)
        report=dict(schema=SCHEMA,status='completed_with_issues' if issues else 'completed',source_file=str(source),output_file=str(output),
                    input_fingerprint=stamp,output_fingerprint=signature([output],{}),output_sha256=file_digest(output),record_count=completed,records=records,issues=issues,mapping=mapping,
                    metadata_table=context.table_summary())
        atomic_json(report_path,report)
        return report
    finally:
        temporary.unlink(missing_ok=True)
