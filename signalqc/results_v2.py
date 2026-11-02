"""Unified public results; scientific calculations remain in the stage engines.

Tables are joined to source HDF5 groups, never by row position, station alone or
rounded timestamps. Stage CSV/JSON files are the complete diagnostic record.
"""
from __future__ import annotations

import hashlib
import csv
import json
import math
import os
from pathlib import Path
import re
import uuid

import pandas as pd

from .data import hdf5_records
from .project import atomic_json, signature

SCHEMA = 'signalqc.unified_results.v2'
METHODS = {'SNR_Kishida': 'SNR-Kishida', 'Empirical': 'Empirical',
           'Ancheta': 'Displacement-Ancheta', 'Guo': 'Guo', 'HK': 'Hussein/Kishida'}
IDENTITY = ['record_number', 'RSN', 'rec_id', 'event_id', 'trace_id', 'network', 'station', 'location', 'component',
            'starttime.s', 'origintime', 'magnitude', 'hypdis', 'epidis', 'timeStep',
            'count', 'latitude.s', 'longitude.s', 'elevation.s',
            'event_latitude', 'event_longitude', 'event_depth', 'event_depth_units']
STAGE1 = ['stage1_status', 'stage1_reason', 'acrjfinal3', 'rejectreason3', 'rawpga',
          'parr'] + [f'score{i}' for i in range(1, 9)] + [f'acrj{i}' for i in range(1, 9)] + ['coh', 'cohL', 'cohH']
STAGE2 = ['stage2_status', 'stage2_error', 'fcHP', 'fcLP', 'corner_fallback',
          'windowStart', 'windowEnd', 'PStart', 'SStart', 'SEnd', 'CStart', 'pga',
          'SNRave', 'rejection_reasons', 'rejectreason3filter', 'method_errors']
SANITY = ['sc_status', 'flag'] + [f'sc_check_{i}_status' for i in range(1, 8)] + ['sc_notes']
LINKS = ['source_file', 'processed_hdf5', 'hdf5_path', 'figure_path', 'figure_avd', 'figure_fas']
NUMERIC = set(['record_number', 'RSN', 'event_latitude', 'event_longitude', 'event_depth',
               'magnitude', 'hypdis', 'epidis', 'timeStep', 'count', 'latitude.s',
               'longitude.s', 'elevation.s', 'rawpga', 'parr', 'coh', 'cohL', 'cohH',
               'fcHP', 'fcLP', 'windowStart', 'windowEnd', 'PStart', 'SStart', 'SEnd',
               'CStart', 'pga', 'SNRave'] + [f'score{i}' for i in range(1, 9)])


def _present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in ('', 'NA', 'NaN', 'nan', 'None', 'null')
    try:
        return not bool(pd.isna(value))
    except (ValueError, TypeError):
        return True


def _text(value):
    return str(value).strip() if _present(value) else ''


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value != -999 else None
    except (ValueError, TypeError):
        return None


def _boolean(value):
    text = _text(value).lower()
    if text in ('true', '1', '1.0', 'accepted', 'yes'):
        return True
    if text in ('false', '0', '0.0', 'rejected', 'no'):
        return False
    return None


def _normalize(row):
    row = {str(k).removeprefix('meta.').removesuffix('.acfilename.'): v for k, v in row.items()}
    for target, aliases in {'event_id': ['eventid', 'event'], 'trace_id': ['trace.id', 'trace'],
                            'component': ['channel'], 'starttime.s': ['record_start_time', 'start_time'],
                            'timeStep': ['dt', 'delta'], 'hypdis': ['hypocentral_distance_km'],
                            'epidis': ['epicentral_distance_km']}.items():
        if target in ('event_id', 'trace_id', 'component'):
            identifiers={_text(row.get(k)) for k in [target]+aliases if _present(row.get(k))}
            if len(identifiers)>1:
                raise ValueError(f'Conflicting {target} identifier columns')
        if not _present(row.get(target)):
            found = [row[a] for a in aliases if _present(row.get(a))]
            if found:
                row[target] = found[0]
    return row


def _read_stage_table(path):
    # Legacy aliases such as meta.event_id.acfilename. are not covered by a
    # fixed dtype map. Read all identifiers as stored, then type public metrics.
    path=Path(path)
    if path.suffix.lower() in ('.xlsx', '.xls'):
        return pd.read_excel(path,dtype=object,keep_default_na=False)
    with path.open(encoding='utf-8-sig') as handle:
        sample=handle.read(65536)
    try:
        separator=csv.Sniffer().sniff(sample,delimiters=',;\t|').delimiter
    except csv.Error:
        separator=','
    return pd.read_csv(path,sep=separator,dtype=object,keep_default_na=False,encoding='utf-8-sig')


def _timestamp(value):
    # pandas Timestamp retains nanoseconds; no truncation to milliseconds/seconds.
    try:
        ts = pd.Timestamp(value)
        return ts.tz_localize('UTC').value if ts.tzinfo is None else ts.tz_convert('UTC').value
    except (ValueError, TypeError, OverflowError):
        return None


def _inventory(source):
    rows = []
    aliases = {}
    manifest = Path(source).parent/'import_report.json'
    if manifest.is_file():
        try:
            imported = json.loads(manifest.read_text(encoding='utf-8'))
            if (imported.get('schema') in ('signalqc.hdf5_layout.v1','signalqc.hdf5_layout.v2')
                    and Path(imported.get('output_file','')).resolve() == Path(source).resolve()
                    and imported.get('output_fingerprint') == signature([source],{})):
                aliases = {(r['event_id'], r['trace_id']):
                           (imported['source_file'], r['original_dataset'].rsplit('/',1)[0])
                           for r in imported.get('records',[])}
        except (OSError, ValueError, TypeError, KeyError):
            pass
    for record in hdf5_records(source):
        path = record['hdf5_path']
        missing_identity=[k for k in ('network','station','channel') if not _text(record.get(k))]
        # The path is authoritative even if an imported event_id attribute disagrees.
        event = path.strip('/').rsplit('/', 1)[0]
        rate = _number(record.get('sampling_rate'))
        rows.append(dict(event_id=event, trace_id=path.rsplit('/', 1)[1],
                         rec_id='.'.join(_text(record.get(k)) for k in ('network','station','location','channel')),
                         network=record.get('network', ''), station=record.get('station', ''),
                         location=record.get('location', ''), component=record.get('channel', ''),
                         **{'starttime.s': record.get('start_time', ''),
                            'latitude.s': record.get('latitude'), 'longitude.s': record.get('longitude'),
                            'elevation.s': record.get('elevation', record.get('elevation_m'))},
                         origintime=record.get('origin_time', ''), magnitude=record.get('event_magnitude'),
                         event_latitude=record.get('event_latitude'), event_longitude=record.get('event_longitude'),
                         event_depth=record.get('event_depth'), event_depth_units=record.get('depth_units', 'unspecified'),
                         hypdis=record.get('hypocentral_distance_km'), epidis=record.get('epicentral_distance_km'),
                         timeStep=1/rate if rate and rate > 0 else None, count=record.get('samples'),
                         ingestion_status=record.get('ingestion_status', ''),
                         identity_status='partial' if missing_identity else 'complete',
                         missing_identity_fields=','.join(missing_identity),
                         _import_alias=aliases.get((event,path.rsplit('/',1)[1])),
                         source_file=str(Path(source).resolve()), hdf5_path=path))
    return rows


def _resolve(row, inventory, source):
    declared_sources=[]
    for field in ('source_file', 'source_hdf5'):
        if _present(row.get(field)):
            declared = Path(str(row[field])).expanduser()
            if declared.is_absolute(): declared_sources.append((field,declared.resolve()))
    event, trace = _text(row.get('event_id')), _text(row.get('trace_id'))
    path = _text(row.get('hdf5_path'))
    candidates = [i for i, item in enumerate(inventory)
                  if (not event or item['event_id'] == event) and
                  ((path and (item['hdf5_path'] == '/' + path.lstrip('/') or
                              item.get('_import_alias') and item['_import_alias'][1] == '/' + path.lstrip('/'))) or
                   (not path and trace and item['trace_id'] == trace))]
    if path and trace:
        candidates=[i for i in candidates if inventory[i]['trace_id']==trace]
    if not candidates and not trace and not path:
        stamp = _timestamp(row.get('starttime.s'))
        if not event or not _text(row.get('station')) or not _text(row.get('component')) or stamp is None:
            raise ValueError('An exact event/trace key or event, station, component and precise start time is required')
        candidates = [i for i, item in enumerate(inventory)
                      if item['event_id'] == event and item['station'] == _text(row['station'])
                      and item['component'] == _text(row['component'])
                      and _timestamp(item['starttime.s']) == stamp]
    for field in ('network', 'station', 'location', 'component'):
        if _present(row.get(field)):
            candidates = [i for i in candidates if _text(inventory[i].get(field)) == _text(row[field])]
    for field, declared in declared_sources:
        if declared != Path(source).resolve():
            candidates=[i for i in candidates if inventory[i].get('_import_alias')
                        and Path(inventory[i]['_import_alias'][0]).resolve()==declared]
            if not candidates: raise ValueError(f'{field} identifies a different HDF5 file')
    if len(candidates) != 1:
        raise ValueError(f'Record identity has {len(candidates)} exact matches; no inferred link was made')
    if path and trace and inventory[candidates[0]]['trace_id'] != trace:
        raise ValueError('Trace ID conflicts with HDF5 group path')
    return candidates[0]


def _method_values(row):
    value = row.get('method_corners')
    corners = {}
    parse_error = ''
    if _present(value):
        try:
            corners = json.loads(value) if isinstance(value, str) else value
            if not isinstance(corners, dict):
                raise ValueError('Expected an object')
        except (ValueError, TypeError) as exc:
            parse_error = 'Saved method corners could not be read: ' + str(exc)
            corners = {}
    errors = _text(row.get('method_errors'))
    output = {}
    for name, label in METHODS.items():
        values = corners.get(name)
        if isinstance(values, dict):
            hp, lp = values.get('fchp'), values.get('fclp')
        elif isinstance(values, (list, tuple)):
            hp = values[0] if len(values) else None
            lp = values[1] if len(values) > 1 else None
        else:
            hp = lp = None
        # Also accept explicit columns from imported consolidated output tables.
        hp = _number(hp if _present(hp) else row.get(name + '_HP_Hz'))
        lp = _number(lp if _present(lp) else row.get(name + '_LP_Hz'))
        issue = next((part.strip() for part in errors.split(' | ') if part.strip().startswith(label + ':')), '')
        state = 'error' if issue or parse_error else 'available' if hp is not None and lp is not None else 'partial' if hp is not None or lp is not None else 'unavailable' if values is not None else 'not_recorded'
        declared_state = _text(values.get('status')) if isinstance(values,dict) else _text(row.get(name+'_status'))
        if declared_state=='disabled' and not parse_error:
            hp=lp=None
            state='disabled'
            issue=''
        output.update({name + '_HP_Hz': hp, name + '_LP_Hz': lp, name + '_status': state})
        if issue or parse_error:
            output[name + '_reason'] = issue or parse_error
    return output


def _outcome(row, selected):
    if _text(row.get('report_issue')):
        return 'incomplete'
    s1, s2, sc = (_text(row.get(k)) for k in ('stage1_status', 'stage2_status', 'sc_status'))
    if s1 == 'rejected' or s2 == 'rejected' or sc == 'fail':
        return 'rejected'
    if s1 in ('error', 'failed', 'skipped', 'pending') or s2 in ('error', 'pending', 'skipped') or sc == 'incomplete':
        return 'incomplete'
    if 'acceptreject' in selected and s1 != 'accepted':
        return 'not_processed'
    if 'filtering' in selected and s2 not in ('completed', 'rejected'):
        return 'not_processed'
    if 'sanity' in selected and sc not in ('pass', 'fail'):
        return 'QC not applied' if sc == 'not_applicable' else 'not_processed'
    if _text(row.get('method_errors')):
        return 'incomplete'
    if selected & {'acceptreject', 'filtering', 'sanity'}:
        return 'accepted'
    return 'converted'


def consolidate(source, tables, selected_stages, stage_reports=()):
    """Return public rows and a complete join audit without touching any HDF5.

    tables is an ordered sequence of (stage, path). stage1_status is separate
    from acceptreject so its skipped/error records remain visible.
    """
    selected = set(selected_stages)
    inventory = _inventory(source)
    rows = [dict(r) for r in inventory]
    unmatched, issues = [], []
    audits = []
    for stage, path in tables:
        frame = _read_stage_table(path)
        matched = {}
        audit = dict(stage=stage, file=str(path), input_rows=len(frame), matched_rows=0, unmatched_rows=0)
        for row_no, raw in enumerate(frame.to_dict('records'), 2):
            row = dict(raw)
            try:
                row = _normalize(raw)
                index = _resolve(row, inventory, source)
                if index in matched:
                    raise ValueError('Duplicate rows identify the same source trace in one stage table')
                matched[index] = row_no
                # Never change canonical identity or source HDF5 physical attributes
                # from stale metadata. Tables supplement missing acquisition fields.
                for key, value in row.items():
                    if key in ('source_file', 'hdf5_path', 'event_id', 'trace_id'):
                        continue
                    if stage=='acceptreject_execution' and _present(rows[index].get(key)):
                        # The original R-published scientific row is authoritative;
                        # diagnostics supplement incomplete groups and missing states.
                        continue
                    if key in IDENTITY and _present(rows[index].get(key)):
                        if key=='rec_id' and stage=='acceptreject':
                            rows[index][key]=value
                        continue
                    if _present(value):
                        rows[index][key] = value
                if stage == 'stage1_status':
                    rows[index]['stage1_status'] = _text(row.get('status'))
                    rows[index]['stage1_reason'] = _text(row.get('reason'))
                audit['matched_rows'] += 1
            except ValueError as exc:
                issue = f'{Path(path).name}, row {row_no}: {exc}'
                issues.append(issue)
                audit['unmatched_rows'] += 1
                extra = {k: v for k, v in row.items() if k not in ('source_file', 'hdf5_path', 'processed_hdf5')}
                extra.update(source_file='', hdf5_path='', report_issue=issue)
                unmatched.append(extra)
                # A conflicting duplicate invalidates the first row too.
                if 'Duplicate rows' in str(exc):
                    rows[index]['report_issue'] = issue
        audits.append(audit)
    rows.extend(unmatched)
    stages = {report.get('stage'): report.get('status') for report in stage_reports}
    for number, row in enumerate(rows, 1):
        row['record_number']=number
        if not _present(row.get('stage1_status')):
            accepted = _boolean(row.get('acrjfinal3'))
            row['stage1_status'] = ('accepted' if accepted else 'rejected') if accepted is not None else ('not_processed' if 'acceptreject' in selected else 'not_selected')
        for stage, column in (('filtering', 'stage2_status'), ('sanity', 'sc_status')):
            if not _present(row.get(column)):
                row[column] = 'not_processed' if stage in selected else 'not_selected'
                if stage in selected and row['stage1_status'] == 'rejected':
                    row[column] = 'not_applicable'
                elif stages.get(stage) == 'failed':
                    row[column] = 'error' if stage == 'filtering' else 'incomplete'
        if 'filtering' in selected or _present(row.get('method_corners')):
            row.update(_method_values(row))
        if row.get('stage2_status') in ('error', 'pending', 'skipped', 'not_processed', 'not_applicable') or _present(row.get('report_issue')):
            # A partial HDF5 may exist without this failed trace. Link its raw
            # source rather than presenting the partial file as a saved waveform.
            row['processed_hdf5'] = None
        row['pipeline_outcome'] = _outcome(row, selected)
    columns = IDENTITY + ['pipeline_outcome']
    if any(_present(r.get('missing_identity_fields')) for r in rows):
        columns += ['identity_status','missing_identity_fields','identity_grouping_status']
    if 'ingestion' in selected:
        columns += ['ingestion_status']
    if 'acceptreject' in selected or any(_present(r.get('acrjfinal3')) for r in rows):
        columns += STAGE1
        if any(_present(r.get(f'acrj{i}_status')) for r in rows for i in range(1,9)):
            columns += [f'acrj{i}_status' for i in range(1,9)]
    if 'filtering' in selected or any(_present(r.get('fcHP')) for r in rows):
        columns += STAGE2
        for method in METHODS:
            columns += [method + '_HP_Hz', method + '_LP_Hz', method + '_status']
            if any(_present(r.get(method + '_reason')) for r in rows):
                columns.append(method + '_reason')
    if 'sanity' in selected or any(r.get('sc_status') not in ('not_selected', None) for r in rows):
        columns += SANITY
    if issues:
        columns += ['report_issue']
    columns += LINKS
    numeric = NUMERIC | {m + suffix for m in METHODS for suffix in ('_HP_Hz', '_LP_Hz')}
    public = []
    for row in rows:
        item = {}
        for col in columns:
            value = row.get(col)
            item[col] = _number(value) if col in numeric else _boolean(value) if re.fullmatch(r'acrj[1-8]|acrjfinal3', col) else value if _present(value) else None
        public.append(item)
    return public, dict(schema=SCHEMA, source=str(Path(source).resolve()), source_traces=len(inventory),
                        output_rows=len(public), unmatched_rows=len(unmatched), issues=issues,
                        tables=audits, selected_stages=sorted(selected))


def column_guide(name):
    definitions = {
        'record_number': ('Sequential row number within this workbook; not a scientific record identifier.', 'integer'),
        'RSN': ('Original Stage 1 record sequence number within its event; may repeat across events. Blank if not generated.', 'integer'),
        'rec_id': ('Original Stage 1 network.station.location.component identifier. Derived from source NSLC only if Stage 1 did not supply it; not a unique event or three-component identifier.', 'text'),
        'event_id': ('Exact event group identifier in the source HDF5.', 'text'),
        'trace_id': ('Exact trace group identifier in the source HDF5. Together with event_id and source_file identifies a waveform.', 'text'),
        'network': ('Acquisition network code from the waveform.', 'text'),
        'station': ('Station code from the waveform.', 'text'),
        'location': ('SEED location code; blank is a valid location.', 'text'),
        'component': ('Acquisition channel/component code.', 'text'),
        'starttime.s': ('Original waveform start time; fractional seconds preserved.', 'UTC'),
        'origintime': ('Catalog event origin time.', 'UTC'),
        'magnitude': ('Event magnitude from the source metadata; not a fixed default.', 'magnitude'),
        'hypdis': ('Hypocentral source-to-station distance.', 'km'),
        'epidis': ('Epicentral surface distance.', 'km'),
        'timeStep': ('Waveform sampling interval.', 's'),
        'count': ('Number of samples in the original source waveform.', 'samples'),
        'latitude.s': ('Station latitude.', 'degrees'),
        'longitude.s': ('Station longitude.', 'degrees'),
        'elevation.s': ('Station elevation.', 'm'),
        'event_latitude': ('Event latitude from the source HDF5.', 'degrees'),
        'event_longitude': ('Event longitude from the source HDF5.', 'degrees'),
        'event_depth': ('Event depth from the source HDF5; check event_depth_units.', 'event_depth_units'),
        'event_depth_units': ('Depth unit declared by the source; unspecified means no unit was supplied.', 'text'),
        'pipeline_outcome': ('Overall outcome for the selected stages. Rejected, incomplete and not_processed are distinct; converted denotes ingestion only.', 'status'),
        'ingestion_status': ('Raw conversion/response-removal state reported by the writer.', 'status'),
        'identity_status': ('Acquisition labels are complete or partial. Missing labels do not imply a waveform processing failure.', 'status'),
        'missing_identity_fields': ('Missing original acquisition labels; display IDs do not invent these physical metadata values.', 'text'),
        'identity_grouping_status': ('Evidence used to associate components with missing station/network labels; isolated means association is unavailable or ambiguous.', 'status'),
        'stage1_status': ('Accept/reject state including skipped, error and not_processed records.', 'status'),
        'stage1_reason': ('Stage 1 status explanation, including skipped/error causes.', 'text'),
        'acrjfinal3': ('Final original Stage 1 three-component acceptance decision.', 'boolean'),
        'rejectreason3': ('Original Stage 1 three-component rejection reason.', 'text'),
        'rawpga': ('Original peak acceleration as saved by Stage 1.', 'm/s^2'),
        'parr': ('Stage 1 estimated P arrival relative to original trace start.', 's'),
        'stage2_status': ('Filtering state. A rejected waveform may still have saved filtered arrays.', 'status'),
        'stage2_error': ('Filtering execution error; not a scientific rejection criterion.', 'text'),
        'fcHP': ('Selected final high-pass corner.', 'Hz'),
        'fcLP': ('Selected final low-pass corner.', 'Hz'),
        'corner_fallback': ('Explicit combined-selection fallback applied when candidate estimates are unavailable.', 'text'),
        'pga': ('Peak filtered acceleration from the original filtering engine.', 'g'),
        'SNRave': ('Mean SNR used by the original filtering quality criteria.', 'ratio'),
        'rejection_reasons': ('All Stage 2 scientific rejection reasons recorded by the engine.', 'text'),
        'rejectreason3filter': ('Final legacy Stage 2 rejection reason.', 'text'),
        'method_errors': ('Method execution errors recorded independently from missing frequency estimates.', 'text'),
        'sc_status': ('Overall sanity status. Disabled/inapplicable checks do not count as passes.', 'status'),
        'flag': ('Failing sanity-check numbers concatenated; 0 means completed applicable checks passed; -999 means incomplete/no applicable result.', 'code'),
        'sc_notes': ('Sanity-check assumptions, missing inputs and diagnostics relevant to interpreting the result.', 'text'),
        'report_issue': ('Metadata alignment problem. No uncertain waveform association is accepted.', 'text'),
        'source_file': ('Original source HDF5 path used by the waveform browser.', 'path'),
        'processed_hdf5': ('Saved processed HDF5 path for this trace when filtering completed.', 'path'),
        'hdf5_path': ('Exact event/trace group path inside the source HDF5.', 'HDF5 path'),
        'figure_path': ('Saved accept/reject figure path, when enabled.', 'path'),
        'figure_avd': ('Saved acceleration/velocity/displacement figure path, when enabled.', 'path'),
        'figure_fas': ('Saved frequency-amplitude spectrum figure path, when enabled.', 'path'),
    }
    if name in definitions:
        return definitions[name]
    windows={'windowStart':'Trimming window start','windowEnd':'Trimming window end','PStart':'P-wave window start',
             'SStart':'S-wave window start','SEnd':'S-wave window end','CStart':'Coda window start'}
    if name in windows:
        return (windows[name] + ', measured from the original trace start.', 's')
    if re.fullmatch(r'score[1-8]', name):
        from .method_settings_v1 import ACCEPTREJECT_METHODS
        return (ACCEPTREJECT_METHODS[name[-1]]+' score. Blank if disabled, not evaluated or unavailable; consult the matching vote status.', 'engine score')
    if re.fullmatch(r'acrj[1-8]', name):
        from .method_settings_v1 import ACCEPTREJECT_METHODS
        return (ACCEPTREJECT_METHODS[name[-1]]+' acceptance vote; blank is not a rejecting vote.', 'boolean')
    if re.fullmatch(r'acrj[1-8]_status',name):
        from .method_settings_v1 import ACCEPTREJECT_METHODS
        return (ACCEPTREJECT_METHODS[name[4]]+' execution state, including disabled, early_stopped, unavailable and not_evaluated.', 'status')
    if name in ('coh','cohL','cohH'):
        return ('Original three-component coherence measure '+name+'. Blank means unavailable.', 'engine measure')
    if re.fullmatch(r'sc_check_[1-7]_status', name):
        from .project import CHECKS
        return ('Check '+name[9]+': '+CHECKS[name[9]]+'. pass, fail, disabled, not_applicable or not_evaluated.', 'status')
    for method,label in METHODS.items():
        if name==method+'_HP_Hz': return (label+' high-pass estimate; blank if unavailable/not recorded.', 'Hz')
        if name==method+'_LP_Hz': return (label+' low-pass estimate; blank if unavailable/not recorded.', 'Hz')
        if name==method+'_status': return (label+' estimate state: available, partial, unavailable, disabled, not_recorded or error.', 'status')
        if name==method+'_reason': return (label+' reported execution error or saved-data interpretation issue.', 'text')
    return ('Engine result retained without numerical recomputation.', 'engine units')


def processing_settings_rows(reports):
    """Use actual stage configuration, never infer settings for imported results."""
    from .method_settings_v1 import ACCEPTREJECT_METHODS, CORNER_METHODS, method_defaults
    defaults=method_defaults()
    labels={
        'acceptreject_methods':('Accept/reject methods','Enabled voting methods; STA/LTA arrival preparation remains independent.'),
        'acceptreject_required_votes':('Required accepting votes','Default: five with all eight methods and the original conditional execution. Custom settings require this many accepting votes; earlier rejection rules still apply.'),
        'filtering_methods':('Corner-frequency methods','Only enabled methods contribute estimates. Guo uses the Python translation of the MATLAB v2 method.'),
        'filtering_hp_fallback_hz':('High-pass fallback (Hz)','Used only when no valid high-pass candidate is available.'),
        'filtering_lp_min_hz':('Low-pass minimum/fallback (Hz)','Minimum combined low-pass corner, also used when no valid low-pass candidate is available.'),
        'enabled_checks':('Sanity checks','Only enabled applicable checks are evaluated.'),
    }
    stages={'acceptreject':'Accept/reject','filtering':'Filtering','sanity':'Sanity checks'}
    output=[]
    for report in reports:
        stage=report.get('stage')
        for key,value in (report.get('scientific_settings') or {}).items():
            if key not in labels: continue
            choices=ACCEPTREJECT_METHODS if key=='acceptreject_methods' else CORNER_METHODS if key=='filtering_methods' else {}
            def display(item):
                return ', '.join(choices.get(str(x),str(x)) for x in item) if isinstance(item,(list,tuple)) else item
            default=defaults.get(key,['1','2','3','4','6','7'] if key=='enabled_checks' else '')
            label,meaning=labels[key]
            output.append([stages.get(stage,stage),label,display(value),display(default),meaning])
    return output


def write_workbook(path, rows, settings_rows=()):
    """Application runtime export using the packaged openpyxl dependency.

    No formulas are generated: the workbook contains exact engine results.
    Text is explicitly typed to avoid interpreting trace IDs as Excel formulas.
    """
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + '.' + uuid.uuid4().hex + '.tmp.xlsx')
    book = Workbook()
    sheet = book.active
    sheet.title = 'Results'
    headers = list(rows[0]) if rows else IDENTITY + ['pipeline_outcome']
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(column) for column in headers])
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    sheet.freeze_panes = 'J2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 44
    for i, name in enumerate(headers, 1):
        cell = sheet.cell(1, i)
        cell.font = Font(name='Calibri', bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='17384D')
        cell.alignment = Alignment(wrap_text=True, vertical='center')
        width = 24 if len(name) > 19 else max(13, len(name) + 2)
        if name in ('trace_id', 'event_id', 'starttime.s', 'origintime'):
            width = 32
        if name.endswith('_reason') or name in ('sc_notes', 'report_issue', 'method_errors', 'rejection_reasons'):
            width = 48
        letter = get_column_letter(i)
        sheet.column_dimensions[letter].width = width
        if name in LINKS:
            sheet.column_dimensions[letter].hidden = True
        for (cell,) in sheet.iter_rows(min_row=2, min_col=i, max_col=i):
            if isinstance(cell.value, float):
                cell.number_format = '0.###############'
            cell.alignment = Alignment(vertical='top')
    guide=book.create_sheet('Column guide')
    guide.append(['Column','Definition','Units / type'])
    for name in headers:
        definition,unit=column_guide(name)
        guide.append([name,definition,unit])
    guide.freeze_panes='A2'; guide.auto_filter.ref=guide.dimensions
    guide.column_dimensions['A'].width=29
    guide.column_dimensions['B'].width=95
    guide.column_dimensions['C'].width=24
    for cell in guide[1]:
        cell.font=Font(name='Calibri',bold=True,color='FFFFFF')
        cell.fill=PatternFill('solid',fgColor='17384D')
    for line in guide.iter_rows(min_row=2):
        for cell in line: cell.alignment=Alignment(wrap_text=True,vertical='top')
        guide.row_dimensions[line[0].row].height=max(32,15*math.ceil(len(str(line[1].value))/90))
    if settings_rows:
        settings=book.create_sheet('Processing settings')
        settings.append(['Stage','Setting','Used for this run','Application default','Meaning'])
        for line in settings_rows:
            settings.append(line)
            for cell in settings[settings.max_row]:
                if isinstance(cell.value,str): cell.data_type='s'
                cell.alignment=Alignment(wrap_text=True,vertical='top')
            settings.row_dimensions[settings.max_row].height=60
        for column,width in zip('ABCDE',(20,30,65,65,90)):
            settings.column_dimensions[column].width=width
        for cell in settings[1]:
            cell.font=Font(name='Calibri',bold=True,color='FFFFFF')
            cell.fill=PatternFill('solid',fgColor='17384D')
        settings.freeze_panes='C2'
    try:
        book.save(temporary)
        # Read back the exact archive before replacing a prior public report.
        check = load_workbook(temporary, read_only=True, data_only=False)
        try:
            saved = check['Results']
            if saved.max_row != len(rows) + 1 or saved.max_column != len(headers):
                raise ValueError('Unified workbook row/column verification failed')
            if tuple(next(saved.values)) != tuple(headers):
                raise ValueError('Unified workbook headers changed during export')
            if check['Column guide'].max_row != len(headers)+1:
                raise ValueError('Unified workbook column guide is incomplete')
            if settings_rows and list(check['Processing settings'].values)[1:] != [tuple(r) for r in settings_rows]:
                raise ValueError('Processing settings changed during workbook export')
        finally:
            check.close()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def publish_results(project_folder, source, tables, stages, stage_reports=()):
    source = Path(source).resolve()
    token = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    stem = re.sub(r'[^A-Za-z0-9_.-]', '_', source.stem) or 'waveforms'
    target = Path(project_folder) / 'outputs' / 'results' / (stem + '_' + token)
    path = target / (stem + '_results.xlsx')
    audit_path = target / 'report_audit.json'
    inputs = [source, Path(__file__)] + [Path(p) for _, p in tables]
    stable_reports = [dict(r, status='completed' if r.get('status') == 'cached' else r.get('status')) for r in stage_reports]
    fingerprint = signature(inputs, dict(schema=SCHEMA, stages=sorted(stages), reports=stable_reports))
    if audit_path.exists() and path.exists():
        prior = json.loads(audit_path.read_text(encoding='utf-8'))
        if prior.get('fingerprint') == fingerprint and prior.get('workbook_fingerprint') == signature([path], {}):
            return dict(stage='results', input=str(source), status='cached', output_files=[str(path)], row_count=prior['output_rows'])
    rows, audit = consolidate(source, tables, stages, stage_reports)
    settings_rows=processing_settings_rows(stage_reports)
    write_workbook(path, rows, settings_rows)
    audit['scientific_settings']=settings_rows
    audit.update(fingerprint=fingerprint, workbook=str(path), workbook_fingerprint=signature([path], {}))
    atomic_json(audit_path, audit)
    return dict(stage='results', input=str(source), status='completed_with_issues' if audit['issues'] else 'completed',
                output_files=[str(path)], row_count=len(rows), issues=audit['issues'])
