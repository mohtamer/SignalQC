"""Readable public figure names. Internal checkpoint identifiers stay unchanged."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import hashlib
import re


def _value(row, *names):
    for name in names:
        text = str(row.get(name, '')).strip()
        if text.lower() not in ('', 'nan', 'none', 'na', '-999', 'nat'):
            return text
    return ''


def figure_stem(row, *, grouped=False, identity=''):
    """Use event time and exact NSLC codes; tolerate missing/invalid metadata."""
    stamp = ''
    for name in ('origintime', 'origin_time', 'event_id'):
        value = _value(row, name)
        match = re.match(r'^(\d{8})[_T](\d{6})(?:\D|$)', value)
        if match:
            stamp = '_'.join(match.groups())
            break
        try:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%Y%m%d_%H%M%S')
            break
        except ValueError:
            pass
    network = _value(row, 'network', 'network_code')
    station = _value(row, 'station', 'station_code')
    channel = _value(row, 'channel', 'channel_code', 'component')
    location = _value(row, 'location', 'location_code')
    # Some legacy metadata has NSC only in trace.id (the display identifier).
    display = _value(row, 'trace.id')
    parts = display.split('.')
    if len(parts) >= 4 and re.match(r'^\d{8}_\d{6}$', parts[0]):
        stamp = stamp or parts[0]
        network = network or parts[1]
        station = station or parts[2]
        channel = channel or parts[-1]
        if len(parts) == 5:
            location = location or parts[3]
    if grouped and len(channel) >= 3:
        channel = channel[:-1]
    if not all((stamp, network, station, channel)):
        seed = str(identity) or repr(sorted((str(k), str(v)) for k, v in row.items()))
        return 'record_' + hashlib.sha256(seed.encode()).hexdigest()[:16]
    pieces = [stamp, network, station] + ([location] if location else []) + [channel]
    return '.'.join(re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', part).strip(' .')[:48] or 'unknown' for part in pieces)


def figure_destination(folder, row, *, grouped=False, identity=''):
    """Prevent collisions with a small per-folder ownership index, resume safely.

    Called by each stage's single publishing coordinator, never its workers.
    Existing unindexed files are preserved under their original name.
    """
    import json
    from .project import atomic_json
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / '.figure_names.json'
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if not isinstance(index, dict): index = {}
    except (OSError, ValueError):
        index = {}
    owner = str(identity) or repr(sorted((str(k), str(v)) for k, v in row.items()))
    token = hashlib.sha256(owner.encode()).hexdigest()
    stem = figure_stem(row, grouped=grouped, identity=owner)
    name = stem + '.png'
    counter = 0
    while (name in index and index[name] != token) or (name not in index and (folder / name).exists()):
        counter += 1
        name = f'{stem}__{token[:8]}' + (f'_{counter}' if counter > 1 else '') + '.png'
    index[name] = token
    atomic_json(index_path, index)
    return folder / name
