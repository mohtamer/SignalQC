"""Explicit per-source mapping selection; no inference from filename or station."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path


def source_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def mapping_files(paths, project_folder=None):
    from .data import walk_files
    from .project import APP_ROOT
    found=set()
    for name in paths:
        path=Path(name).expanduser().resolve()
        # Match the input inventory: explicitly selected outputs are valid,
        # while broad ancestor scans omit application/generated project trees.
        excludes=[root for root in (APP_ROOT,Path(project_folder).resolve() if project_folder else None)
                  if root is not None and root!=path and path in root.parents]
        found.update(p for p in walk_files([path],excludes) if p.suffix.lower() in ('.h5','.hdf5'))
    return [str(path) for path in sorted(found,key=str)]


def mapping_for_source(config, path):
    """Exact overrides, including explicit disabled overrides, precede v1 default."""
    overrides=config.get('hdf5_mappings',{})
    if not isinstance(overrides,dict):
        raise ValueError('The saved HDF5 file mappings are invalid. Review data mapping again.')
    key=source_key(path)
    matches=[value for name,value in overrides.items() if source_key(name)==key]
    if matches:
        if any(value!=matches[0] for value in matches[1:]):
            raise ValueError('This HDF5 file has conflicting saved mappings. Review its data mapping again.')
        chosen=matches[0]
    else:
        chosen=config.get('hdf5_mapping',{})
    if not isinstance(chosen,dict):
        raise ValueError('The saved HDF5 layout mapping is invalid. Review data mapping again.')
    return deepcopy(chosen)


def review_hdf5_files(config, project_folder=None):
    from .workflow_v2 import relevant_input_config
    selected=relevant_input_config(config)
    paths=[]
    for key in ('raw_paths','hdf5_paths','response_paths'):
        paths.extend(selected.get(key,[]))
    return mapping_files(paths,project_folder)


def set_source_mapping(config, paths, mapping):
    """Return new configuration without extending a mapping to future inputs."""
    if not isinstance(mapping,dict): raise ValueError('A file mapping must be an object.')
    updated=deepcopy(config)
    overrides=updated.setdefault('hdf5_mappings',{})
    if not isinstance(overrides,dict): raise ValueError('The saved HDF5 file mappings are invalid.')
    for path in paths:
        key=source_key(path)
        for old in list(overrides):
            if source_key(old)==key: del overrides[old]
        overrides[str(Path(path).expanduser().resolve())]=deepcopy(mapping)
    return updated


def metadata_table_paths(mapping):
    """Only an actually referenced external table is a mapping dependency."""
    if not mapping.get('enabled'): return []
    fields=mapping.get('metadata',{})
    if not any(isinstance(spec,dict) and spec.get('kind')=='table' for spec in fields.values()):
        return []
    table=mapping.get('metadata_table') or {}
    path=table.get('path','') if isinstance(table,dict) else ''
    return [str(Path(path).expanduser().resolve())] if str(path).strip() else []


def adapter_for_mapping(mapping):
    """Keep older project imports resumable until their mapping is revised."""
    schema=mapping.get('schema','')
    if schema not in ('','signalqc.hdf5_mapping.v1','signalqc.hdf5_mapping.v2'):
        raise ValueError('Unsupported HDF5 mapping version. Review data mapping again.')
    uses_new_fields=any(isinstance(spec,dict) and (
        spec.get('kind') in ('table','missing','reserved','field') or
        any(key in spec for key in ('scope','format','time_unit','timezone','unit')))
        for spec in mapping.get('metadata',{}).values())
    if schema=='signalqc.hdf5_mapping.v2' or uses_new_fields:
        from . import hdf5_import_v2 as adapter
    else:
        from . import hdf5_import_v1 as adapter
    return adapter


def hdf5_review_fingerprint(config, files):
    """Bind a reviewed inventory to source headers/settings and table contents.

    Large waveform files use file identity/size/mtime; only metadata tables are
    hashed in full. Missing/invalid inputs stay in the inventory for diagnosis.
    """
    sources=[]; tables=set()
    for name in sorted(set(map(str,files))):
        path=Path(name).expanduser().resolve()
        try:
            stat=path.stat(); state=(stat.st_size,stat.st_mtime_ns)
        except OSError: state='unavailable'
        try:
            mapping=mapping_for_source(config,path)
            tables.update(metadata_table_paths(mapping))
        except (ValueError,TypeError,AttributeError) as exc:
            mapping={'invalid_mapping':str(exc)}
        sources.append((source_key(path),state,mapping))
    dependencies=[]
    for name in sorted(tables):
        try:
            with Path(name).open('rb') as stream:
                value=hashlib.file_digest(stream,'sha256').hexdigest()
        except OSError: value='unavailable'
        dependencies.append((source_key(name),value))
    payload=json.dumps({'sources':sources,'tables':dependencies},sort_keys=True,default=str)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
