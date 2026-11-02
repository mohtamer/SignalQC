"""Conservative, reviewable field-name suggestions for the HDF5 mapping UI.

This module reads inventory dictionaries only. It never opens input files,
changes mappings, joins table rows, infers physical units from values, or runs
any scientific computation. Scores express naming evidence, not probabilities.
"""
from __future__ import annotations

import copy
from glob import escape as glob_escape
import math
import re


FIELDS = (
    'sampling_rate', 'dt', 'start_time', 'origin_time', 'network', 'station',
    'location', 'channel', 'magnitude', 'event_latitude', 'event_longitude',
    'event_depth', 'station_latitude', 'station_longitude', 'station_elevation',
    'event_id', 'trace_id', 'station_group_id', 'recording_group_id',
)
WAVE_KINDS = ('acceleration', 'raw', 'velocity', 'displacement')
ALIASES = {
    'sampling_rate': ('sampling rate', 'sample rate', 'sampling frequency', 'sample frequency', 'sampling freq', 'sample freq', 'fs', 'sps', 'samp', 'sampling'),
    'dt': ('dt', 'delta', 'time step', 'sample interval', 'sampling interval', 'samp interval', 'sample period', 'sampling period', 'delta t'),
    'start_time': ('start time', 'starttime s', 'record start', 'recording start', 'trace start', 'recording utc', 'record utc', 'recording time', 'acquisition time', 'begin time', 'start timestamp', 'start epoch', 'record timestamp', 'trace timestamp'),
    'origin_time': ('origin time', 'event time', 'earthquake time', 'earthquake utc', 'event utc', 'origin utc', 'origin timestamp', 'origin epoch', 'event timestamp'),
    'network': ('network', 'network code', 'network identifier', 'net', 'net code', 'knetwk'),
    'station': ('station', 'station code', 'station identifier', 'sta', 'sta code', 'kstnm'),
    'location': ('location code', 'location identifier', 'location', 'loc', 'khole'),
    'channel': ('channel', 'channel code', 'channel identifier', 'component', 'component code', 'kcmpnm', 'chan'),
    'magnitude': ('magnitude', 'mag', 'mw', 'ml', 'mb', 'ms', 'm'),
    'event_latitude': ('event latitude', 'event lat', 'earthquake latitude', 'earthquake lat', 'epicentre latitude', 'epicenter latitude', 'evla'),
    'event_longitude': ('event longitude', 'event lon', 'event long', 'earthquake longitude', 'earthquake lon', 'epicentre longitude', 'epicenter longitude', 'evlo'),
    'event_depth': ('event depth', 'earthquake depth', 'focal depth', 'hypocentral depth', 'hypocentre depth', 'hypocenter depth', 'evdp', 'depth'),
    'station_latitude': ('station latitude', 'station lat', 'sensor latitude', 'sensor lat', 'site latitude', 'receiver latitude', 'stla'),
    'station_longitude': ('station longitude', 'station lon', 'station long', 'sensor longitude', 'sensor lon', 'site longitude', 'receiver longitude', 'stlo'),
    'station_elevation': ('station elevation', 'sensor elevation', 'site elevation', 'receiver elevation', 'elevation', 'altitude', 'stel', 'elev'),
    'event_id': ('event id', 'event identifier', 'event token', 'event key', 'earthquake id', 'earthquake identifier', 'evid'),
    'trace_id': ('trace id', 'trace identifier', 'trace token', 'record id', 'record identifier', 'record token', 'rec id', 'rsn'),
    'station_group_id': ('station group id', 'station group', 'station association'),
    'recording_group_id': ('recording group id', 'recording group', 'recording association'),
}
WAVE_ALIASES = {
    'acceleration': ('acceleration waveform', 'acceleration', 'accel', 'acc', 'accelerogram'),
    'raw': ('raw waveform', 'raw counts', 'digitizer counts', 'digital counts', 'instrument counts', 'counts', 'raw'),
    'velocity': ('velocity waveform', 'velocity', 'vel', 'velocigram'),
    'displacement': ('displacement waveform', 'displacement', 'disp'),
}
_EVENT = {'event', 'events', 'earthquake', 'earthquakes', 'epicenter', 'epicentre', 'hypocenter', 'hypocentre', 'hypocentral'}
_STATION = {'station', 'stations', 'sensor', 'sensors', 'site', 'sites', 'receiver', 'receivers'}
_QUALIFIERS = {'error', 'err', 'uncertainty', 'uncertainties', 'sigma', 'std', 'variance', 'quality', 'flag', 'type', 'method', 'scale', 'description', 'unit', 'units', 'accuracy', 'precision', 'resolution'}
_SUMMARY = {'peak', 'max', 'min', 'rms', 'mean', 'median', 'std', 'variance', 'summary', 'stat', 'stats', 'statistics', 'pga', 'pgv', 'pgd', 'psa', 'sa', 'fas', 'fft', 'psd', 'spectrum', 'spectra', 'spectral', 'fourier', 'response', 'frequency', 'frequencies', 'period', 'periods', 'transfer', 'histogram'}
_NUMERIC = {'sampling_rate', 'dt', 'magnitude', 'event_latitude', 'event_longitude', 'event_depth', 'station_latitude', 'station_longitude', 'station_elevation'}


def tokens(value):
    """Split boundaries, including camel case; never match a character substring."""
    text = str(value or '')
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    text = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', text)
    return tuple(re.findall(r'[a-z]+|[0-9]+', text.lower()))


def _phrase_score(name, aliases):
    words = tokens(name); compact = ''.join(words)
    best = (0., '')
    for alias in aliases:
        wanted = tokens(alias)
        if words == wanted or compact == ''.join(wanted):
            score = .96
        elif wanted and any(words[i:i+len(wanted)] == wanted for i in range(len(words)-len(wanted)+1)):
            score = .90
        else:
            continue
        # Broad abbreviations require corroboration below.
        if alias in ('samp', 'sampling', 'm', 'depth', 'delta'):
            score = min(score, .80)
        if score > best[0]: best = score, alias
    return best


def _samples(entry):
    sample = entry.get('sample')
    if isinstance(sample, (list, tuple)):
        values = list(sample)
    elif sample is None:
        values = []
    else:
        values = [sample]
    return [v for v in values if v is not None and str(v).strip() not in ('', 'nan', 'NaN', 'None', 'NA')]


def _numbers(entry):
    values = _samples(entry)
    numbers = []
    for value in values:
        if isinstance(value, bool): return None
        try: number = float(value)
        except (ValueError, TypeError, OverflowError): return None
        if not math.isfinite(number): return None
        numbers.append(number)
    return numbers


def _context(name, owner):
    # The field's own qualifier wins; otherwise the nearest named owner wins.
    for part in [name, *reversed(str(owner).strip('/').split('/'))]:
        words = set(tokens(part)); event = bool(words & _EVENT); station = bool(words & _STATION)
        if event and station: return 'mixed'
        if event: return 'event'
        if station: return 'station'
        if words & {'record','records','recording','recordings','trace','traces','waveform','waveforms','component','components'}:
            return ''
    return ''


def _unit(text):
    """Recognize explicit unit expressions, not arbitrary letters in a name."""
    value = str(text or '').strip().replace('²', '^2').replace('μ', 'u').replace('µ', 'u')
    normalized = value.lower().replace(' ', '').replace('**', '^')
    exact = {'hz':'Hz', 'khz':'kHz', 'sps':'Hz', 'samples/s':'Hz', 'samples/sec':'Hz',
             's':'s', 'sec':'s', 'second':'s', 'seconds':'s', 'ms':'ms', 'millisecond':'ms', 'milliseconds':'ms',
             'us':'us', 'microseconds':'us', 'ns':'ns', 'nanoseconds':'ns',
             'm':'m', 'meter':'m', 'meters':'m', 'metre':'m', 'metres':'m', 'km':'km', 'kilometers':'km', 'kilometres':'km',
             'cm':'cm', 'g':'g', 'counts':'counts', 'count':'counts', 'deg':'degrees', 'degree':'degrees', 'degrees':'degrees',
             'radians':'radians', 'rad':'radians', 'm/s^2':'m/s^2', 'm/s2':'m/s^2', 'mps2':'m/s^2',
             'cm/s^2':'cm/s^2', 'cm/s2':'cm/s^2', 'cmps2':'cm/s^2', 'gal':'cm/s^2', 'm/s':'m/s', 'mps':'m/s', 'cm/s':'cm/s', 'cmps':'cm/s'}
    if normalized in exact: return exact[normalized]
    return None


def _label_units(name):
    result = set()
    if str(name).strip() in ('Mw','MW','Ml','ML','Ms','MS','Mb','MB'):
        return result
    if {'samples','per','second'} <= set(tokens(name)):
        return {'Hz'}
    # Expressions in brackets or suffix tokens are explicitly attached units.
    for part in re.findall(r'[\[(]([^\])]+)[\])]', str(name)):
        unit = _unit(part)
        if unit: result.add(unit)
    words = tokens(name)
    for word in words:
        # A lone M is a possible magnitude label; metres require a qualifier.
        if word in ('hz', 'khz', 'milliseconds', 'millisecond', 'microseconds', 'nanoseconds', 'seconds', 'metres', 'meters', 'kilometres', 'kilometers', 'degrees', 'radians'):
            result.add(_unit(word))
    suffix = re.split(r'[_\s]+', str(name).strip())[-1]
    if len(words)>1:
        unit = _unit(suffix)
        # ML/MS/Mw are magnitude scales, not a duration suffix.
        if unit and not (unit=='ms' and {'mag','magnitude','event'} & set(words)):
            magnitude_m = suffix=='M' and bool(set(words) & {'mag','magnitude','event','earthquake'}) and not bool(set(words) & {'depth','elevation','latitude','longitude','distance'})
            if not magnitude_m:result.add(unit)
    for expression in ('cm/s^2', 'cm/s2', 'm/s^2', 'm/s2', 'cm/s', 'm/s'):
        if expression in str(name).lower():
            result.add(_unit(expression)); break
    return result - {None}


def _units(entry):
    units = _label_units(entry['name'])
    explicit = _unit(entry.get('units'))
    if explicit: units.add(explicit)
    return units


def _metadata_entries(inventory, table):
    entries = []
    headers = {d.get('path'): d for d in inventory.get('datasets', [])}
    for raw in inventory.get('sources', []):
        source = copy.deepcopy(raw.get('source') or {k:raw[k] for k in ('kind','value','scope') if k in raw})
        if source.get('kind') not in ('attribute', 'dataset'): continue
        entry = copy.deepcopy(raw)
        entry.update(source=source, name=str(source.get('value', '')).rsplit('/',1)[-1])
        # A waveform's units describe its samples, not metadata attributes such
        # as sampling_rate or magnitude stored on that same dataset.
        entry['units'] = raw.get('units') or (headers.get(raw.get('owner_path'), {}).get('units','') if source['kind']=='dataset' else '')
        entries.append(entry)
    if table:
        for column in table.get('columns', []):
            entries.append(dict(name=str(column), source=dict(kind='table', value=str(column)),
                                sample=[row.get(column) for row in table.get('samples', [])],
                                owner_path='', shape=[], dtype='table'))
    return entries


def _metadata_candidate(field, entry):
    name = entry['name']; words = set(tokens(name)); context = _context(name, entry.get('owner_path',''))
    score, alias = _phrase_score(name, ALIASES.get(field, ()))
    explanation = f'Name matches {alias!r}.' if score else ''
    if field in _NUMERIC and words & (_QUALIFIERS | {'count','counts','number','npts','index','min','max','mean','median','rms','peak'}): return None
    if field == 'sampling_rate' and words & {'count','counts','number','npts','samples','interval','period','step','duration','length','dt','delta'}:
        # "samples per second" is a recognized rate expression.
        if not {'samples','per','second'} <= words: return None
    if field == 'sampling_rate' and {'samples','per','second'} <= words:
        score,alias,explanation = .94,'samples per second','Name explicitly describes samples per second.'
    if field == 'dt' and words & {'rate','frequency','freq','count','counts','number','npts','duration'}: return None
    if field in ('network','station','location','channel'):
        if words & (_QUALIFIERS | {'latitude','longitude','lat','lon','long','elevation','depth','distance','group','association','count','number'}): return None
        if score and field in ('network','station') and 'id' in words: score = max(score,.91)
    if field.endswith('_latitude') or field.endswith('_longitude'):
        desired = field.split('_')[0]
        if context in ('event','station') and context != desired: return None
        coordinate = ('latitude','lat') if field.endswith('_latitude') else ('longitude','lon','long','lng')
        generic_score,generic_alias = _phrase_score(name, coordinate)
        if generic_score and not score:
            if context == desired:
                score,alias = .90,generic_alias
                explanation = f'Coordinate label and {desired} group context agree.'
            else:
                score,alias = .57,generic_alias
                explanation = 'Coordinate label has no unambiguous event/station context; select its meaning manually.'
        if context == 'mixed': score = min(score,.57)
    if field == 'event_depth' and context == 'station': return None
    if field == 'station_elevation' and context == 'event': return None
    if field == 'magnitude' and words & {'id','identifier','token','key','time','date','file','path','station','network','channel','moment','depth','elevation','latitude','longitude','distance','sampling','rate','duration'}: return None
    if field == 'magnitude' and alias=='ms' and str(name).strip() not in ('Ms','MS') and not words & {'mag','magnitude','event','earthquake'}:
        return None
    if field in ('event_id','trace_id','station_group_id','recording_group_id') and words & {'count','number','error','type','time'}:
        return None
    if field in ('start_time','origin_time') and words & (_QUALIFIERS | {'duration','end','stop','window','count','counts','number','index'}): return None
    if not score: return None
    if field=='trace_id' and alias in ('rec id','rsn'):
        score=min(score,.68)
        explanation='This may identify a station group or event sequence rather than one waveform. Select it only after reviewing its meaning and uniqueness.'
    numeric = _numbers(entry)
    if field in _NUMERIC:
        if numeric is None: return None
        # Arrays are not scalar/per-component metadata unless very small.
        shape = entry.get('shape') or []
        if len(shape)>1 or (shape and math.prod(shape)>16): return None
        if shape and math.prod(shape)>1:
            score = min(score,.70)
            explanation += ' Multiple stored values require an explicit component/layout decision.'
        dtype = str(entry.get('dtype','')).lower()
        if not numeric and dtype.startswith(('str','object','|s','<u','bool')): return None
        if numeric:
            if field in ('sampling_rate','dt') and any(v<=0 for v in numeric): return None
            if field=='magnitude' and any(not -5<=v<=12 for v in numeric): return None
            if field.endswith('_latitude') and any(abs(v)>90 for v in numeric): return None
            if field.endswith('_longitude') and any(abs(v)>360 for v in numeric): return None
            if field=='event_depth' and any(v<0 for v in numeric): return None
        elif entry.get('sample') is not None or entry['source']['kind']=='table':
            score=min(score,.70)
            explanation+=' No usable numeric examples are present; verify the values manually.'
        if alias in ('samp','sampling'):
            # Positive sampled values support the abbreviation without changing
            # its interpretation or assuming units from its numerical size.
            if not numeric: score = .70
            else: score = min(score+.05,.85)
            explanation = 'Abbreviated sampling label with positive numeric examples; verify that this is a rate and confirm its units.'
        if alias=='m':
            if not numeric or name.strip() != 'M':
                # "event M" is valid only when M remains an isolated uppercase token.
                if not numeric or not re.search(r'(^|[_\s])M($|[_\s])', name): return None
            score = min(score+.05,.85)
            explanation = 'Isolated M label with magnitude-like numeric examples; confirm that it denotes event magnitude.'
        if alias in ('delta','depth'):
            score = min(score+.06,.86) if numeric else min(score,.76)
            explanation = 'Abbreviated physical field name; confirm its meaning and units.'
    units = _units(entry)
    allowed = {'sampling_rate': {'Hz','kHz'}, 'dt': {'s','ms','us','ns'},
               'event_depth': {'m','km'}, 'station_elevation': {'m','km'},
               'event_latitude': {'degrees'}, 'event_longitude': {'degrees'},
               'station_latitude': {'degrees'}, 'station_longitude': {'degrees'},
               'magnitude': set()}.get(field)
    if allowed is not None and units and (not units <= allowed or len(units)>1): return None
    if allowed is not None and entry.get('units') and not _unit(entry['units']):
        score = min(score,.70)
        explanation += ' Stored units are not recognized; confirm their interpretation manually.'
    source = copy.deepcopy(entry['source'])
    unit = next(iter(units)) if len(units)==1 else None
    if unit and field in ('sampling_rate','dt','event_depth','station_elevation'):
        source['unit'] = unit; explanation += f' Explicit unit: {unit}.'
    if field in ('start_time','origin_time'):
        values = _samples(entry)
        if 'epoch' in words and unit in ('s','ms','us','ns'):
            source.update(format='epoch', time_unit=unit)
        elif values and all(re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}',str(v)) for v in values):
            source['format']='iso'
        elif values:
            explanation += ' Review the stored time format explicitly.'
    result = dict(source=source, confidence=round(score,3), reason=explanation,
                  name=name, sample=copy.deepcopy(entry.get('sample')), owner_path=entry.get('owner_path',''))
    if unit: result['unit']=unit
    result['_entry']=entry
    return result


def _numeric_dtype(dtype):
    value=str(dtype).lower()
    return bool(re.match(r'^(?:[<>|=]?[iuf]\d+|u?int\d*|float\d*)$',value))


def _wave_candidates(kind, inventory):
    grouped = {}
    for entry in inventory.get('datasets', []):
        shape=entry.get('shape') or []
        if len(shape) not in (1,2) or not _numeric_dtype(entry.get('dtype')) or math.prod(shape)<=1: continue
        path=entry.get('path',''); name=path.rsplit('/',1)[-1]
        words=set(tokens(name)); path_words=set(tokens(path))
        if path_words & (_SUMMARY | _QUALIFIERS): continue
        score,alias=_phrase_score(name,WAVE_ALIASES[kind])
        if not score: continue
        other=[other for other in WAVE_KINDS if other!=kind and _phrase_score(name,WAVE_ALIASES[other])[0]]
        if other: continue
        declared=_unit(entry.get('units')); named=_label_units(name)
        units=named | ({declared} if declared else set())
        allowed={'acceleration':{'m/s^2','cm/s^2','g'},'raw':{'counts'},'velocity':{'m/s','cm/s'},'displacement':{'m','cm'}}[kind]
        if units and (not units <= allowed or len(units)>1): continue
        if entry.get('units') and not declared:
            score=min(score,.72)
        pattern='**/'+glob_escape(name)
        reason=f'Numeric {len(shape)}D dataset name matches {alias!r}.'
        if len(shape)==2: reason+=' Select the sample direction manually.'
        if not units: reason+=' Units remain unconfirmed.'
        unit=next(iter(units)) if len(units)==1 else None
        if unit: reason+=f' Explicit unit: {unit}.'
        candidate=dict(pattern=pattern,name=name,confidence=round(score,3),reason=reason,examples=[path],shapes=[shape])
        if unit: candidate['unit']=unit
        if pattern not in grouped: grouped[pattern]=candidate
        else:
            current=grouped[pattern]
            if current.get('unit') != unit:
                current.pop('unit',None);current['confidence']=.60
                current['reason']='Repeated dataset names have inconsistent or missing units; review each stored layout.'
            if path not in current['examples'] and len(current['examples'])<3: current['examples'].append(path)
            if shape not in current['shapes'] and len(current['shapes'])<3: current['shapes'].append(shape)
    for candidate in grouped.values():
        siblings=[entry for entry in inventory.get('datasets',[]) if entry.get('path','').rsplit('/',1)[-1]==candidate['name']]
        for entry in siblings:
            shape=entry.get('shape') or []
            units=_label_units(candidate['name']);declared=_unit(entry.get('units'))
            if declared:units.add(declared)
            allowed={'acceleration':{'m/s^2','cm/s^2','g'},'raw':{'counts'},'velocity':{'m/s','cm/s'},'displacement':{'m','cm'}}[kind]
            excluded_path=bool(set(tokens(entry.get('path',''))) & (_SUMMARY | _QUALIFIERS))
            if len(shape) not in (1,2) or math.prod(shape)<=1 or not _numeric_dtype(entry.get('dtype')) or (units and not units<=allowed) or excluded_path:
                candidate['confidence']=.60
                candidate['reason']='This repeated-name pattern also includes incompatible datasets; choose a more specific path manually.'
                candidate.pop('unit',None)
    return list(grouped.values())


def _equivalent_candidates(candidates, waveform_paths):
    """Prefer scopes demonstrated relative to plausible acceleration datasets."""
    grouped={}
    for candidate in candidates:
        entry=candidate['_entry']; source=candidate['source']
        owner=entry.get('owner_path','')
        # The same physical scalar may be inventoried both as an absolute path
        # and a scoped sibling. Distinct physical sources are never collapsed.
        key=(source['kind'], owner, source.get('value','').rsplit('/',1)[-1]) if owner else tuple(sorted((k,str(v)) for k,v in source.items()))
        examples=set(entry.get('record_examples',[]))
        preferred=bool(examples & waveform_paths)
        absolute=source.get('kind')=='dataset' and str(source.get('value','')).startswith('/')
        priority=(preferred,not absolute,source.get('scope')=='dataset')
        if key not in grouped or priority>grouped[key][0]: grouped[key]=(priority,candidate)
    result=[]
    for _,candidate in grouped.values():
        cleaned={k:v for k,v in candidate.items() if not k.startswith('_')};result.append(cleaned)
    return result


def _decision(candidates, key):
    candidates=sorted(candidates,key=lambda c:(-c['confidence'],c.get('name',''),repr(c.get(key))))
    result=dict(status='unmatched',confidence=0.,reason='No confident automatic match. Select a source manually.',candidates=candidates,**{key:None})
    if not candidates:return result
    best=candidates[0];result['confidence']=best['confidence']
    if best['confidence']<.82:
        result.update(status='ambiguous',reason=best['reason']+' Select manually; the naming evidence is insufficient.')
    elif len(candidates)>1 and best['confidence']-candidates[1]['confidence']<.12:
        result.update(status='ambiguous',reason='Multiple plausible matches were found. Select the intended source manually.')
    else:
        result.update(status='suggested',reason=best['reason']+' Review and confirm before applying.',**{key:copy.deepcopy(best[key])})
        if 'unit' in best:result['unit']=best['unit']
    return result


def suggest_fields(inventory, table_inventory=None, fields=None):
    """Return review-only suggestions from HDF5/table inventory dictionaries.

    Each result has status ``suggested``, ``ambiguous`` or ``unmatched``, a
    confidence score in [0, 1], a reason and ranked candidates. Only suggested
    metadata results contain a non-None ``source``; only suggested waveform
    results contain a non-None ``pattern``. Canonical v2 source specifications
    include units/formats only when explicitly identified. No joins are guessed.
    """
    inventory=inventory or {}
    datasets={kind:_decision(_wave_candidates(kind,inventory),'pattern') for kind in WAVE_KINDS}
    waveform_paths={path for candidate in datasets['acceleration']['candidates'] for path in candidate.get('examples',[])}
    entries=_metadata_entries(inventory,table_inventory)
    metadata={}
    for field in FIELDS if fields is None else fields:
        candidates=[candidate for entry in entries if (candidate:=_metadata_candidate(field,entry)) is not None]
        metadata[field]=_decision(_equivalent_candidates(candidates,waveform_paths),'source')
    return dict(metadata=metadata,datasets=datasets)
