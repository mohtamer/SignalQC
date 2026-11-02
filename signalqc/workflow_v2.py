"""Task-specific input selection and preflight, without changing engine equations."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from .method_settings_v1 import method_setting_errors

STAGES = ('ingestion', 'acceptreject', 'filtering', 'sanity')


def relevant_input_config(config):
    """Exclude inactive inputs from scans. The saved project remains unchanged."""
    cfg = deepcopy(config)
    if 'stages' not in cfg:  # Generic inventory callers retain the original behavior.
        return cfg
    stages = set(cfg.get('stages') or [])
    if 'ingestion' not in stages:
        cfg.update(raw_paths=[], response_paths=[], event_catalog='')
    if not stages.intersection({'acceptreject', 'filtering', 'sanity'}):
        cfg['hdf5_paths'] = []
    if 'filtering' not in stages or 'acceptreject' in stages:
        cfg['stage1_metadata'] = ''
    if 'sanity' not in stages or 'filtering' in stages:
        cfg['filtering_metadata'] = ''
    if not stages.intersection({'ingestion', 'sanity'}):
        cfg['station_catalog'] = ''
    return cfg


def validate_workflow(config, inventory=None):
    """Return actionable blocking errors; inspect only the selected workflow."""
    cfg = relevant_input_config(config)
    stages = set(cfg.get('stages') or [])
    errors = []
    if not stages:
        return ['Select at least one processing task.']
    if stages.difference(STAGES):
        errors.append('The project contains an unsupported processing stage.')
    errors.extend(method_setting_errors(cfg))
    ingest = 'ingestion' in stages
    process = bool(stages.intersection({'acceptreject', 'filtering', 'sanity'}))
    if cfg.get('interface_task') in ('process', 'prepare_process') and not process:
        errors.append('Select at least one processing stage, or choose Create HDF5 files.')
    if ingest and not cfg.get('raw_paths'):
        errors.append('Select the raw waveform files or folders to convert.')
    if process and not ingest and not cfg.get('hdf5_paths'):
        errors.append('Select the existing HDF5 files or folders to process.')
    for key, label in [('raw_paths', 'Waveform input'), ('response_paths', 'Response input'),
                       ('hdf5_paths', 'HDF5 input')]:
        for name in cfg.get(key, []):
            if not Path(name).exists():
                errors.append(f'{label} is unavailable: {name}')
    for needed, key, label in [
        ('filtering' in stages and 'acceptreject' not in stages,
         'stage1_metadata', 'accept/reject results'),
        ('sanity' in stages and 'filtering' not in stages,
         'filtering_metadata', 'filtering results'),
    ]:
        if needed and not Path(cfg.get(key) or '').is_file():
            errors.append(f'Select the existing {label} table (a combined results workbook is also supported).')
    for key, label in [('event_catalog', 'Event catalog'), ('station_catalog', 'Station catalog')]:
        if cfg.get(key) and not Path(cfg[key]).is_file():
            errors.append(f'{label} is unavailable: {cfg[key]}')
    if 'filtering' in stages:
        suffix = str(cfg.get('processed_suffix', 'processed_2'))
        if not suffix.strip() or suffix.endswith((' ', '.')) or any(c in suffix for c in '/\\:*?"<>|'):
            errors.append('Enter a valid processed-file suffix without path separators or reserved filename characters.')
    if inventory is not None:
        if inventory.get('ncm_reference_changed'):
            errors.append('Reference data changed during inspection. Scan again.')
        if ingest and not cfg.get('event_catalog'):
            errors.append('An earthquake catalogue is required for event matching.')
        if ingest and inventory.get('catalogs',{}).get('event_count')==0:
            errors.append('No valid catalogue events were found. Review the catalogue table.')
        if ingest and inventory.get('catalogue_error'):
            errors.append('The catalogue could not be used: '+inventory['catalogue_error'])
        if inventory.get('hdf5_review_changed'):
            errors.append('HDF5 files or mapped metadata changed during inspection. Scan the current inputs again.')
        counts = inventory.get('counts', {})
        if ingest and counts.get('raw_files', 0) == 0:
            errors.append('No supported raw waveform files were detected in the selected inputs.')
        if process and not ingest and counts.get('hdf5_files', 0) == 0:
            errors.append('No HDF5 files were detected in the selected inputs.')
        if process and not ingest and counts.get('hdf5_files', 0) and not inventory.get('trace_count', 0):
            errors.append('The selected HDF5 files contain no readable waveform records.')
        if 'sanity' in stages and 'filtering' not in stages and counts.get('hdf5_files', 0) > 1:
            errors.append('For sanity checks only, select one source HDF5 file and its matching results table per run.')
        details = inventory.get('hdf5_details', [])
        if not ingest and stages.intersection({'acceptreject', 'filtering'}) and details:
            total = sum(d.get('traces', 0) for d in details)
            missing = sum(d.get('missing_acceleration', 0) for d in details)
            if total and missing == total:
                errors.append('No calibrated acceleration waveforms were found. Convert the raw data with response removal, or select calibrated HDF5 inputs.')
    return list(dict.fromkeys(errors))
