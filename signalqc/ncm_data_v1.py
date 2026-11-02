"""Typed reference-table imports; no institution data are selected automatically."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import os
import re
import shutil
import uuid
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd

APP_ROOT=Path(__file__).resolve().parents[1]
BASE_FIELDS={
 'events':['event_id','origin_time','year','month','day','hour','minute','second','latitude','longitude','depth','magnitude','magnitude_type','source_file'],
 'stations':['network','station','location','channel','latitude','longitude','elevation','bits','station_name','emirate','station_type','digitizer','seismometer','accelerometer','installation_date','start_date','end_date']}
ALIASES={
 'event_id':['eventid','earthquakeid','publicid'], 'origin_time':['origintime','eventtime','datetime','utctime','timestamp'],
 'year':['year','yyyy'],'month':['month','mm'],'day':['day','dd'],'hour':['hour','hh'],'minute':['minute','minutes','min'],'second':['second','seconds','sec'],
 'latitude':['latitude','lat','latitudedeg','latitudedegrees','eventlatitude','stationlatitude'],
 'longitude':['longitude','lon','long','longitudedeg','longitudedegrees','eventlongitude','stationlongitude'],
 'depth':['depth','depthkm','depthm','focaldepth'],'magnitude':['magnitude','mag','m','mw','ml','mb'],
 'magnitude_type':['magnitudetype','magtype'],'source_file':['sourcefile','filename','waveformfile','filepath','file'],
 'station':['station','stationcode','stacode','sta'],'network':['network','networkcode','net'],
 'location':['location','locationcode','loc'],'channel':['channel','channelcode','component','cha'],
 'elevation':['elevation','elevationm','elevationkm','elev','heightm'],'bits':['bits','adcbits','digitizerbits','resolutionbits'],
 'station_name':['stationname','stationlocation','sitename'],'emirate':['emirate'], 'station_type':['stationtype','typeofstation'],
 'digitizer':['digitizer'],'seismometer':['seismometer','typeofsensorseismometer'],'accelerometer':['accelerometer','typeofsensoraccelerometer'],
 'installation_date':['installationdate','dateofinstallation'],'start_date':['startdate','validfrom','epochstart'],'end_date':['enddate','validto','epochend']}
NUMERIC={'year','month','day','hour','minute','second','latitude','longitude','depth','magnitude','elevation','bits'}
MISSING={'','na','n/a','nan','null','none','-999','unknown','not available','-'}

def norm(value):return re.sub(r'[^a-z0-9]','',str(value).lower())
def text(value):
    if value is None:return ''
    try:
        if pd.isna(value):return ''
    except (TypeError,ValueError):pass
    if isinstance(value,float) and value.is_integer():return str(int(value))
    return str(value).strip()

def number(value,field='',default_unit=''):
    """Read one numeric quantity; never concatenate ranges or unrelated numbers."""
    s=text(value).replace('\u2212','-').replace('\u00a0',' ').strip()
    if s.lower() in MISSING:return None
    if ',' in s and '.' not in s:
        if re.search(r'\d{1,3}(?:,\d{3})+(?!\d)',s) and field not in ('latitude','longitude','magnitude'):s=s.replace(',','')
        elif s.count(',')==1:s=s.replace(',','.')
    elif ',' in s:s=s.replace(',','')
    tokens=re.findall(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',s)
    if not tokens:raise ValueError(f'{field or "Number"}: no numeric value in {value!r}')
    if field in ('latitude','longitude') and len(tokens) in (2,3) and any(c in s for c in ('°',"'",'′','"','″')):
        parts=[float(t) for t in tokens]
        if any(not 0<=v<60 for v in parts[1:]):raise ValueError(f'{field}: invalid minutes/seconds')
        result=(abs(parts[0])+parts[1]/60+(parts[2]/3600 if len(parts)==3 else 0))*(-1 if parts[0]<0 else 1)
    elif len(tokens)==1:result=float(tokens[0])
    else:raise ValueError(f'{field or "Number"}: several values in {value!r}; enter one value')
    if not math.isfinite(result) or result==-999:return None
    if field in ('latitude','longitude'):
        direction=re.search(r'([NSEW])\s*$',s.upper())
        if direction:
            d=direction.group(1)
            if d not in ('NS' if field=='latitude' else 'EW'):raise ValueError(f'{field}: incompatible hemisphere')
            result=abs(result)*(-1 if d in 'SW' else 1)
        if abs(result)>(90 if field=='latitude' else 180):raise ValueError(f'{field}: coordinate outside degree range')
    if field in ('depth','elevation'):
        unit='km' if re.search(r'(?<![A-Za-z])(?:km|kilomet(?:er|re)s?)\b',s,re.I) else 'm' if re.search(r'(?<![A-Za-z])(?:m|met(?:er|re)s?)\b',s,re.I) else default_unit
        if field=='depth' and unit=='m':result/=1000
        if field=='elevation' and unit=='km':result*=1000
    if field in ('year','month','day','hour','minute','bits'):
        if result!=int(result):raise ValueError(f'{field}: a whole number is required')
        result=int(result)
    if field=='bits' and not 1<=result<=64:raise ValueError('bits: expected 1–64')
    return result

def _frame(path,kind,sheet=None):
    path=Path(path)
    if path.suffix.lower() in ('.xlsx','.xls'):
        if path.suffix.lower()=='.xlsx':
            # Merged station cells encode real shared values; ordinary blanks do not.
            import openpyxl
            book=openpyxl.load_workbook(path,data_only=True,read_only=False)
            sheets=[book[sheet]] if isinstance(sheet,str) else [book.worksheets[sheet]] if isinstance(sheet,int) else book.worksheets
            options=[]
            for ws in sheets:
                values=[list(row) for row in ws.values]
                for area in ws.merged_cells.ranges:
                    value=ws.cell(area.min_row,area.min_col).value
                    for r in range(area.min_row-1,area.max_row):
                        for c in range(area.min_col-1,area.max_col):values[r][c]=value
                for i,row in enumerate(values[:20]):
                    words={norm(x) for x in row}
                    score=sum(bool(words.intersection(ALIASES[f])) for f in (('station','latitude','longitude') if kind=='stations' else ('year','origin_time','latitude','longitude','magnitude','source_file')))
                    options.append((score,ws.title,i,values))
            score,title,header,values=max(options,key=lambda x:x[0])
            if score<2:raise ValueError('Could not identify the table headings in this workbook.')
            headers=[];seen={}
            for c,value in enumerate(values[header]):
                name=text(value)
                sub=text(values[header+1][c]) if header+1<len(values) else ''
                if kind=='stations' and norm(name)=='typeofsensor' and sub and sub!=name:name+=' '+sub
                if not name:name=('Previous '+sub) if sub else f'Column {c+1}'
                seen[name]=seen.get(name,0)+1
                headers.append(name if seen[name]==1 else name+f' ({seen[name]})')
            return pd.DataFrame(values[header+1:],columns=headers),title,header+2
        with pd.ExcelFile(path) as book:
            return pd.read_excel(book,sheet_name=sheet if sheet is not None else 0,dtype=object,keep_default_na=False),str(sheet or 0),2
    with path.open(encoding='utf-8-sig') as handle:
        sample=handle.read(65536)
    try:sep=csv.Sniffer().sniff(sample,delimiters=',;\t|').delimiter
    except csv.Error:sep=','
    return pd.read_csv(path,sep=sep,dtype=object,keep_default_na=False,encoding='utf-8-sig'),'',2

def resolve_columns(columns,kind,mappings=None):
    resolved={};mappings=mappings or {}
    for field in BASE_FIELDS[kind]:
        if mappings.get(field):
            if mappings[field] not in columns:raise ValueError(f'Mapped column is missing: {mappings[field]}')
            resolved[field]=mappings[field];continue
        preferred=[c for c in columns if norm(c)==norm(field)]
        if len(preferred)==1:resolved[field]=preferred[0];continue
        for alias in ALIASES.get(field,[norm(field)]):
            matches=[c for c in columns if norm(c)==alias]
            if len(matches)>1:raise ValueError(f'Ambiguous {field} columns: {matches}')
            if matches:resolved[field]=matches[0];break
    return resolved

def normalize_frame(frame,kind,mappings=None,first_row=2,source='',default_units=None):
    default_units=default_units or {}
    fields=resolve_columns(list(frame.columns),kind,mappings)
    rows=[];issues=[]
    for index,raw in enumerate(frame.to_dict('records'),first_row):
        if not any(text(v) for v in raw.values()):continue
        if kind=='stations' and (not text(raw.get(fields.get('station',''))) or norm(raw.get(fields.get('station',''))) in ('station','stationcode')):continue
        row={field:raw.get(column,'') for field,column in fields.items()}
        for field in BASE_FIELDS[kind]:row.setdefault(field,'')
        for field in NUMERIC.intersection(row):
            try:
                heading=norm(fields.get(field,field))
                unit=('m' if heading=='depthm' else 'km' if heading=='depthkm' else default_units.get('depth','km')) if field=='depth' else ('km' if heading=='elevationkm' else 'm' if heading in ('elevationm','heightm') else default_units.get('elevation','m'))
                row[field]=number(row[field],field,unit)
            except ValueError as exc:
                issues.append(dict(row=index,field=field,value=text(row[field]),message=str(exc)))
        for field in set(row)-NUMERIC:row[field]=text(row[field])
        # Retain informational columns while keeping calculation columns unique.
        used=set(fields.values())
        for name,value in raw.items():
            if name not in used and not str(name).startswith('Column ') and norm(name) not in ('no','unnamed0'):
                key=name if name not in row else 'Original '+name
                row[key]=text(value)
        row.setdefault('source_table',source);row.setdefault('source_row',index)
        row['_input_row']=index
        rows.append(row)
    if not rows:raise ValueError(f'No {kind} rows were found.')
    if kind=='stations' and 'station' not in fields:raise ValueError('A station-code column is required.')
    if kind=='events' and 'origin_time' not in fields and not all(k in fields for k in ('year','month','day','hour','minute','second')):
        raise ValueError('An origin-time column or Year, Month, Day, Hour, Minute and Second columns are required.')
    return rows,dict(columns=fields,issues=issues,rows=len(rows))

def import_table(path,kind,mappings=None,sheet=None,default_units=None):
    frame,title,first=_frame(path,kind,sheet)
    rows,report=normalize_frame(frame,kind,mappings,first,Path(path).name,default_units)
    report.update(source=str(path),sheet=title)
    return rows,report

def write_csv(path,rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    columns=list(dict.fromkeys(k for row in rows for k in row if not k.startswith('_')))
    tmp=path.with_name('.ncm_'+uuid.uuid4().hex+'.csv')
    try:
        with tmp.open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)

def ncm_defaults():
    # Legacy function name is kept for callers; public projects have no hidden library.
    return dict(ncm_profile=False,ncm_use_library=False,ncm_catalogue_path='',
                event_catalog='',station_catalog='',response_paths=[],
                event_depth_unit='km',station_elevation_unit='m',events_sheet=None,stations_sheet=None)

def bind_config(config):
    cfg=dict(config)
    cfg.update(ncm_profile=False,ncm_use_library=False)
    # pandas interprets sheet_name=None as "all sheets"; the processing reader
    # requires one DataFrame. A blank user selection means the first sheet.
    if cfg.get('stations_sheet') in (None,''):cfg['stations_sheet']=0
    if not cfg.get('remove_response',True):cfg['response_paths']=[]
    return cfg


def snapshot_tables(config,folder):
    """Keep station metadata for reproducibility; read the live catalogue in place."""
    cfg=dict(config)
    for key in ('station_catalog',):
        value=cfg.get(key)
        if not value:continue
        source=Path(value);digest=hashlib.sha256(source.read_bytes()).hexdigest()
        target=Path(folder)/'reference_data'/digest[:20]/source.name
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        cfg[key]=str(target)
    return cfg


def reference_fingerprint(config):
    from .data import walk_files
    paths=[Path(config[k]) for k in ('event_catalog','station_catalog') if config.get(k)]
    paths+=list(walk_files(config.get('response_paths',[])))
    values=[]
    for path in sorted(set(paths),key=str):
        try:
            stat=path.stat()
            # Table contents are small and must invalidate a reviewed run after an edit.
            digest=hashlib.sha256(path.read_bytes()).hexdigest() if path in paths[:2] and path.suffix.lower() in {'.csv','.xlsx','.xls','.tsv'} else ''
            values.append((str(path),stat.st_size,stat.st_mtime_ns,digest))
        except OSError:values.append((str(path),'unavailable'))
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


