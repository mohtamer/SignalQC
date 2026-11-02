"""Explicit metadata resolution for guided HDF5 import; no waveform processing."""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

import h5py
import numpy as np
import pandas as pd

from .hdf5_import_v1 import ALIASES, _objects, _scalar, _plain, _num, _iso


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _text(value):
    """Exact textual key: no whitespace stripping, case folding, or rounding."""
    value = _plain(value)
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ''
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    return str(value)


def _headers(values):
    headers = [_text(x) for x in values]
    if not headers or any(not x for x in headers):
        raise ValueError('The metadata table must have a nonempty name for every column.')
    if len(set(headers)) != len(headers):
        raise ValueError('The metadata table has duplicate column names; rename them explicitly.')
    return headers


def _load_table(path, sheet=None, header_row=1, delimiter=None):
    path = Path(path).resolve()
    if isinstance(header_row,bool) or int(header_row)!=header_row or int(header_row)<1:
        raise ValueError('The table header row must be a positive whole number (1-based).')
    header_row=int(header_row)
    warnings = []
    if path.suffix.lower() in ('.csv', '.tsv'):
        with path.open('r', encoding='utf-8-sig', newline='') as stream:
            if delimiter in (None,'','auto'):
                sample=stream.read(8192); stream.seek(0)
                try: delimiter=csv.Sniffer().sniff(sample,delimiters=',;\t|').delimiter
                except csv.Error: delimiter='\t' if path.suffix.lower()=='.tsv' else ','
            if not isinstance(delimiter,str) or len(delimiter)!=1:
                raise ValueError('The CSV delimiter must be a single character or auto.')
            reader = csv.reader(stream, delimiter=delimiter)
            for _ in range(header_row-1):
                try: next(reader)
                except StopIteration: raise ValueError('The selected header row is beyond the metadata table.')
            try: headers = _headers(next(reader))
            except StopIteration: raise ValueError('The metadata table is empty.')
            rows = []; row_numbers=[]
            for line, values in enumerate(reader, header_row+1):
                if not values or not any(values): continue
                if len(values) != len(headers):
                    raise ValueError(f'Metadata table row {line} has {len(values)} cells; expected {len(headers)}.')
                rows.append(dict(zip(headers, values)))
                row_numbers.append(line)
        return dict(path=str(path), sheets=[], selected_sheet=None, columns=headers, rows=rows, row_numbers=row_numbers, warnings=warnings, unsafe_identifier_cells={},header_row=header_row,delimiter=delimiter)
    if path.suffix.lower() not in ('.xlsx', '.xlsm'):
        raise ValueError('Use an .xlsx, .xlsm, .csv, or .tsv metadata table.')
    import openpyxl
    with path.open('rb') as stream:
        book = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        try:
            sheets = list(book.sheetnames)
            if sheet is None or sheet == '':
                if len(sheets) != 1:
                    raise ValueError('Select the metadata worksheet explicitly; this workbook has multiple sheets.')
                sheet = sheets[0]
            if sheet not in sheets: raise ValueError(f'Metadata worksheet {sheet!r} does not exist.')
            ws = book[sheet]
            # openpyxl converts date-formatted cells to millisecond datetimes.
            # Keep their stored serial text so an explicit Excel-date mapping
            # can convert it without that intermediate loss of precision.
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read(ws._worksheet_path))
            namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            numeric = {c.attrib['r']: c.find(namespace+'v').text
                       for c in root.iter(namespace+'c')
                       if c.attrib.get('t', 'n') == 'n' and c.find(namespace+'v') is not None}
            iterator = ws.iter_rows(min_row=header_row)
            try: headers = _headers([c.value for c in next(iterator)])
            except StopIteration: raise ValueError('The metadata worksheet is empty.')
            rows = []; row_numbers=[]; unsafe={}
            for cells in iterator:
                values = []
                for cell in cells:
                    value = cell.value
                    if cell.is_date and cell.coordinate in numeric:
                        value = numeric[cell.coordinate]
                    elif isinstance(value, (int, float)) and not isinstance(value, bool):
                        if re.fullmatch(r'0+', cell.number_format or '') and float(value).is_integer():
                            if len(cell.number_format)>1:
                                warnings.append(f'{sheet}!{cell.coordinate}: zero-padding is display formatting only; exact joins use the stored numeric value. Store leading-zero identifiers as text.')
                        if abs(value) >= 10**15:
                            warnings.append(f'{sheet}!{cell.coordinate}: a numeric cell has 16 or more integer digits; Excel may already have lost identifier precision. Store identifiers as text.')
                            unsafe.setdefault(cell.row,[]).append(headers[len(values)])
                    values.append('' if value is None else value)
                if not any(_text(x) for x in values): continue
                rows.append(dict(zip(headers, values)))
                row_numbers.append(cells[0].row)
            return dict(path=str(path), sheets=sheets, selected_sheet=sheet, columns=headers, rows=rows, warnings=sorted(set(warnings)),
                        row_numbers=row_numbers,unsafe_identifier_cells=unsafe,header_row=header_row,excel_date_system='1904' if book.epoch.year == 1904 else '1900')
        finally: book.close()


def inspect_metadata_table(path, sheet=None, limit=5, header_row=1, delimiter=None):
    """Read table fields without embedding a DataFrame in the public mapping."""
    path = Path(path).resolve()
    if path.suffix.lower() in ('.xlsx', '.xlsm') and (sheet is None or sheet == ''):
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try: sheets = list(book.sheetnames)
        finally: book.close()
        if len(sheets) != 1:
            return dict(path=str(path), sheets=sheets, selected_sheet=None, columns=[], samples=[], row_count=0,
                        warnings=['Select a worksheet to inspect its columns.'])
    data = _load_table(path, sheet,header_row,delimiter)
    rows = data.pop('rows')
    data.pop('row_numbers',None); data.pop('unsafe_identifier_cells',None)
    data.update(row_count=len(rows), samples=[{k:_text(v) for k,v in row.items()} for row in rows[:max(0,int(limit))]])
    return data


def _scope(dataset, scope):
    if scope in (None, '', 'nearest'): return None
    if scope == 'dataset': return dataset
    if scope == 'root': return dataset.file['/']
    if scope in ('record', 'parent'): return dataset.parent
    if str(scope).startswith('/'):
        obj = dataset.file.get(scope)
        if obj is None: raise ValueError(f'Metadata scope {scope!r} does not exist.')
        return obj
    if str(scope).startswith('parent:'):
        count = int(str(scope).split(':',1)[1])
        if count < 0: raise ValueError('A parent scope index must be nonnegative.')
        obj = dataset.parent
        for _ in range(count):
            if obj.name == '/': raise ValueError(f'Metadata scope {scope!r} is above the file root.')
            obj = obj.parent
        return obj
    raise ValueError(f'Unsupported metadata scope: {scope!r}.')


def _timestamp(value, spec):
    if value is None or _text(value).strip() == '': return ''
    mode = spec.get('format', 'iso')
    zone = spec.get('timezone') or 'UTC'
    if mode == 'epoch':
        unit = spec.get('time_unit')
        scales = {'s':10**9, 'ms':10**6, 'us':10**3, 'ns':1}
        if unit not in scales: raise ValueError('Epoch timestamps require an explicit s, ms, us, or ns time unit.')
        if zone not in ('UTC', 'Etc/UTC', 'Z'):
            raise ValueError('Epoch timestamps are UTC; use timezone UTC.')
        try: ns = Decimal(_text(value)) * scales[unit]
        except InvalidOperation: raise ValueError('The epoch timestamp is not numeric.')
        if not ns.is_finite() or ns != ns.to_integral_value():
            raise ValueError('Timestamp precision must be an exact whole number of nanoseconds.')
        return _iso(pd.Timestamp(int(ns), unit='ns', tz='UTC'))
    if mode == 'excel':
        system = str(spec.get('excel_date_system', ''))
        if system not in ('1900', '1904'): raise ValueError('Excel date system must be 1900 or 1904.')
        if spec.get('time_unit', 'days') != 'days': raise ValueError('Excel serial timestamps use days.')
        try: serial = Decimal(_text(value))
        except InvalidOperation: raise ValueError('An Excel timestamp must be its numeric day serial.')
        if not serial.is_finite(): raise ValueError('Excel timestamp is nonfinite.')
        if system == '1900' and 60 <= serial < 61:
            raise ValueError('Excel serial day 60 is the nonexistent 1900-02-29 date.')
        base = pd.Timestamp('1904-01-01' if system == '1904' else ('1899-12-31' if serial < 60 else '1899-12-30'))
        nanos = serial * Decimal(86400 * 10**9)
        if nanos != nanos.to_integral_value():
            raise ValueError('Excel timestamp has sub-nanosecond precision; provide an explicit ISO timestamp instead of rounding it.')
        stamp = base + pd.Timedelta(int(nanos), unit='ns')
    elif mode == 'strptime':
        pattern = spec.get('datetime_format')
        if not pattern: raise ValueError('A custom timestamp requires datetime_format.')
        stamp = pd.Timestamp(pd.to_datetime(value, format=pattern, exact=True, errors='raise'))
    elif mode in ('iso', '', None):
        if isinstance(value, (int,float,np.number)) or re.fullmatch(r'[+-]?\d+(\.\d+)?', _text(value).strip()):
            raise ValueError('Numeric timestamps require an explicit epoch or Excel format and time unit.')
        if isinstance(value,str) and value.strip().lower() not in ('nat','nan') and not re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T\s]|$)',value.strip()):
            raise ValueError('Use an ISO/year-first timestamp or select an explicit custom datetime format for ambiguous dates.')
        stamp = pd.Timestamp(value)
    else: raise ValueError(f'Unsupported timestamp format: {mode!r}.')
    if pd.isna(stamp): return ''
    # DST ambiguities/nonexistent local times raise instead of choosing an offset.
    try:
        stamp = stamp.tz_localize(zone, ambiguous='raise', nonexistent='raise') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    except Exception as exc:
        raise ValueError(f'Timestamp timezone cannot be resolved unambiguously: {exc}') from exc
    return _iso(stamp.tz_convert('UTC'))


def _convert(value, field, spec):
    if field in ('start_time', 'origin_time') or (field == '__join__' and spec.get('format')):
        return _timestamp(value, spec)
    unit = spec.get('unit')
    if unit in (None, ''): return value
    conversions = {'event_depth': {'km':1., 'm':.001}, 'station_elevation': {'m':1., 'km':1000.},
                   'dt': {'s':1., 'ms':.001, 'us':.000001, 'ns':.000000001}, 'sampling_rate': {'Hz':1., 'hz':1., 'kHz':1000., 'khz':1000.}}
    if field not in conversions or unit not in conversions[field]:
        raise ValueError(f'{field}: unsupported declared unit {unit!r}.')
    if _text(value).strip() == '': return ''
    number = _num(value)
    if not np.isfinite(number): return np.nan
    result = number * conversions[field][unit]
    if not np.isfinite(result): raise ValueError(f'{field}: unit conversion produced a nonfinite value.')
    return result


class MappingContext:
    """A private per-file resolver; table data never enter serialized mapping."""
    def __init__(self, source, mapping):
        self.source = Path(source).resolve()
        self.mapping = mapping
        self.table = None
        self.table_error = None
        self.table_index = {}
        self.table_used = any(isinstance(s,dict) and s.get('kind') == 'table' for s in mapping.get('metadata',{}).values())
        self.table_fingerprint = None
        self.rows = {}
        if self.table_used:
            try:
                config = mapping.get('metadata_table', {})
                if not config.get('path'): raise ValueError('Select the metadata table file.')
                self.table_fingerprint = file_digest(config['path'])
                self.table = _load_table(config['path'], config.get('sheet'),config.get('header_row',1),config.get('delimiter'))
                joins = config.get('join')
                if not isinstance(joins,list) or not joins: raise ValueError('Specify at least one exact metadata-table join key.')
                columns = []
                for join in joins:
                    column = join.get('column')
                    if column not in self.table['columns']: raise ValueError(f'Join column {column!r} does not exist.')
                    columns.append(column)
                    spec = join.get('source',{})
                    if spec.get('kind') == 'table': raise ValueError('Table joins cannot depend on table values.')
                    if spec.get('kind') == 'field':
                        target = mapping.get('metadata',{}).get(spec.get('value'),{})
                        if isinstance(target,dict) and target.get('kind') in ('table','field'):
                            raise ValueError('Table join keys cannot depend on table-backed or circular metadata fields.')
                if len(set(columns)) != len(columns): raise ValueError('A table column may occur only once in a composite join.')
                for ordinal,row in zip(self.table['row_numbers'],self.table['rows']):
                    key = tuple(_text(row[c]) for c in columns)
                    if any(not k for k in key): continue
                    self.table_index.setdefault(key,[]).append((ordinal,row))
            except Exception as exc:
                self.table_error = str(exc)

    def fingerprint_settings(self):
        return dict(mapping=self.mapping, metadata_table_sha256=self.table_fingerprint, metadata_table_error=self.table_error)

    def table_summary(self):
        if not self.table_used: return dict(used=False)
        return dict(used=True,path=self.mapping.get('metadata_table',{}).get('path'),
                    sheet=self.table.get('selected_sheet') if self.table else None,
                    row_count=len(self.table['rows']) if self.table else 0,
                    sha256=self.table_fingerprint,error=self.table_error,
                    warnings=self.table.get('warnings',[]) if self.table else [],
                    duplicate_keys=sum(len(v)>1 for v in self.table_index.values()))

    def _table_row(self, dataset, index):
        if self.table_error: raise ValueError(self.table_error)
        key_id = (dataset.name,index)
        if key_id not in self.rows:
            joins = self.mapping['metadata_table']['join']
            key = tuple(_text(self.resolve(dataset,'__join__',index,spec=j['source'])[0]) for j in joins)
            if any(not k for k in key): raise ValueError('Metadata table join key is missing for this record.')
            matches = self.table_index.get(key,[])
            if not matches: raise ValueError(f'No metadata table row matches the exact key {key!r}.')
            if len(matches) != 1: raise ValueError(f'Ambiguous metadata table key {key!r}: {len(matches)} rows match.')
            unsafe=self.table['unsafe_identifier_cells'].get(matches[0][0],[])
            if any(j['column'] in unsafe for j in joins):
                raise ValueError(f'Metadata table row {matches[0][0]} has a numeric join identifier with 16 or more digits. Store the original identifier as text; precision cannot be recovered.')
            self.rows[key_id] = (matches[0][0],matches[0][1],key)
        return self.rows[key_id]

    def resolve(self, dataset, field, index=None, spec=None, anchor=None):
        if spec is None: spec = self.mapping.get('metadata',{}).get(field,{})
        if not isinstance(spec,dict): spec = dict(kind='constant',value=spec)
        kind,value = spec.get('kind','auto'),spec.get('value','')
        evidence = dict(kind=kind,value=value)
        if kind == 'missing': raw = ''
        elif kind == 'constant': raw = value
        elif kind == 'reserved':
            values = dict(dataset_path=dataset.name,record_path=dataset.parent.name,
                          source_filename=self.source.name,component_index=0 if index is None else index)
            if value not in values: raise ValueError(f'Unsupported reserved source {value!r}.')
            raw = values[value]
        elif kind == 'field':
            if value not in ALIASES: raise ValueError(f'Unknown join metadata field {value!r}.')
            target = self.mapping.get('metadata',{}).get(value,{})
            if isinstance(target,dict) and target.get('kind') in ('table','field'):
                raise ValueError('Metadata fields cannot depend on table-backed or circular fields for joining.')
            labels=self.mapping.get('components',[])
            if value=='channel' and index is not None and labels and index<len(labels):
                raw,origin=labels[index],dict(kind='component_label',value=labels[index])
            else: raw,origin = self.resolve(dataset,value,index)
            evidence['resolved_from'] = origin
        elif kind == 'table':
            ordinal,row,key = self._table_row(anchor if anchor is not None else dataset,index)
            if value not in row: raise ValueError(f'Metadata column {value!r} does not exist.')
            if value in self.table['unsafe_identifier_cells'].get(ordinal,[]) and field in ('event_id','trace_id','station','network','location','channel','station_group_id','recording_group_id','start_time','origin_time'):
                raise ValueError(f'{field}: metadata table row {ordinal} uses an Excel numeric identifier/time with 16 or more digits; use exact text instead.')
            raw = row[value]
            if spec.get('format')=='excel' and 'excel_date_system' not in spec and self.table.get('excel_date_system'):
                spec=dict(spec,excel_date_system=self.table['excel_date_system'])
            evidence.update(scope=self.table['path'],sheet=self.table['selected_sheet'],row=ordinal,join_key=list(key))
        elif kind == 'path_part':
            parts = dataset.parent.name.strip('/').split('/')
            n = int(value)
            if n < 0 or n >= len(parts): raise ValueError(f'{field}: selected path level is absent.')
            raw = parts[n]; evidence['scope'] = dataset.parent.name
        elif kind in ('attribute','dataset','auto'):
            owner = _scope(dataset,spec.get('scope'))
            objects = [owner] if owner is not None else list(_objects(dataset))
            raw = ''
            if kind == 'dataset':
                relative_owner=owner if owner is not None else dataset.parent
                if not str(value).startswith('/') and not isinstance(relative_owner,h5py.Group):
                    raise ValueError('A relative metadata dataset requires a group scope, such as parent:0.')
                target = dataset.file.get(str(value)) if str(value).startswith('/') else relative_owner.get(str(value))
                if not isinstance(target,h5py.Dataset) or target.size>256:
                    raise ValueError(f'{field}: metadata dataset is missing or is not a small scalar/vector.')
                raw = _scalar(target[()],index); evidence['scope'] = target.name
            else:
                names = [str(value)] if kind == 'attribute' else ALIASES.get(field,[])
                found = False
                for obj in objects:
                    for name in names:
                        if name in obj.attrs:
                            raw = _scalar(obj.attrs[name],index); evidence.update(scope=obj.name,name=name); found=True; break
                    if found: break
                    if kind == 'auto' and isinstance(obj,h5py.Group):
                        for name in names:
                            child = obj.get(name)
                            if isinstance(child,h5py.Dataset) and child.size<=256:
                                raw = _scalar(child[()],index); evidence.update(scope=child.name,name=name); found=True; break
                    if found: break
        else: raise ValueError(f'Unsupported metadata mapping: {kind!r}.')
        evidence['raw_value'] = _plain(raw)
        converted = _convert(raw,field,spec)
        conversion = {k:spec[k] for k in ('format','time_unit','timezone','excel_date_system','datetime_format','unit') if k in spec}
        if conversion: evidence['conversion'] = conversion
        return converted,evidence

    def record(self,dataset,index=None):
        fields,provenance,errors = {},{},[]
        for field in ALIASES:
            try: fields[field],provenance[field] = self.resolve(dataset,field,index)
            except (ValueError,TypeError,IndexError,KeyError,OverflowError) as exc:
                fields[field]=''; provenance[field]={'kind':'error','message':str(exc)}; errors.append(f'{field}: {exc}')
        labels = self.mapping.get('components',[])
        if index is not None and labels and index<len(labels):
            fields['channel']=labels[index]; provenance['channel']={'kind':'component_label','value':labels[index]}
        return fields,provenance,errors


def stage_readiness(fields):
    physical = ('magnitude','event_latitude','event_longitude','event_depth','station_latitude','station_longitude')
    missing = [f for f in physical if not math.isfinite(_num(fields.get(f)))]
    missing += [f for f in ('start_time','origin_time') if not fields.get(f)]
    return dict(metadata_only=True,
                acceptreject=dict(status='metadata_incomplete' if missing else 'metadata_available',missing=list(missing)),
                filtering=dict(status='metadata_incomplete' if missing else 'metadata_available',missing=list(missing)),
                sanity=dict(status='requires_filtered_results',note='Check-specific requirements, including raw counts for clipping, are assessed by the sanity stage.'))
