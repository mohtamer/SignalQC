"""Stage 1/2 HDF5 adapter; exact identities, UTC times and explicit metadata.

Only I/O and metadata normalization lives here. No signal filtering or quality
classifier is reimplemented by this adapter.
"""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import re
from functools import lru_cache

import h5py
import numpy as np
from obspy import UTCDateTime
from obspy.geodetics import gps2dist_azimuth


def decode_attribute(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _pick(attrs, *names, default=None):
    normalized = {re.sub(r"[^a-z0-9]", "", str(k).lower()): decode_attribute(v)
                  for k, v in attrs.items()}
    for name in names:
        value = normalized.get(re.sub(r"[^a-z0-9]", "", name.lower()))
        if value is not None and str(value).strip().lower() not in ("", "n/a", "nan", "none", "null"):
            return value
    return default


def clean_float(value, default=float("nan")):
    try:
        value = decode_attribute(value)
        if isinstance(value, str):
            text = value.strip()
            negative = bool(re.search(r"[SW]\s*$", text, re.I))
            text = re.sub(r"\s*(?:km|m|degrees?|[NSEW])\s*$", "", text, flags=re.I)
            result = float(text)
            if negative:
                result = -abs(result)
        else:
            result = float(value)
        return result if math.isfinite(result) and result != -999 else default
    except (TypeError, ValueError):
        return default


def _time(value):
    if value is None or str(value).strip() in ("", "N/A", "nan"):
        return ""
    try:
        # UTCDateTime preserves sub-second instants; trace keys are never derived
        # from the formatted R timestamp, so decimal spelling cannot break IDs.
        decoded = decode_attribute(value)
        match = re.fullmatch(r"(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)(?:[.](\d+))?(Z|[+-]\d\d:?\d\d)?", str(decoded))
        if match:
            base = UTCDateTime(match[1] + (match[3] or "Z"), precision=9)
            fractional_ns = int(((match[2] or "") + "000000000")[:9])
            moment = UTCDateTime(ns=base.ns + fractional_ns, precision=9)
        else:
            moment = UTCDateTime(decoded, precision=9)
        return str(moment).replace("T", " ").rstrip("Z")
    except (ValueError, TypeError):
        return ""


def _event_meta(group, event_id):
    attrs = group.attrs
    result = {k: decode_attribute(v) for k, v in attrs.items()}
    result.update(event_id=str(event_id),
                  latitude=clean_float(_pick(attrs, "latitude", "event_latitude", "event_lat", "eqlat", "latitude_e")),
                  longitude=clean_float(_pick(attrs, "longitude", "event_longitude", "event_lon", "eqlon", "longitude_e")),
                  depth=clean_float(_pick(attrs, "depth_km", "event_depth_km", "depth", "eqdep", "depth_e")),
                  magnitude=clean_float(_pick(attrs, "magnitude", "mag", "event_magnitude", "mw", "ml")),
                  origin_time=_time(_pick(attrs, "origin_time", "origintime", "event_time", "eqtime")))
    return result


def hdf5_eventlist(hdf5_filename):
    with h5py.File(str(hdf5_filename), "r") as handle:
        return [_event_meta(group, key) for key, group in handle.items()
                if isinstance(group, h5py.Group)]


def _trace_meta(group):
    attrs = group.attrs
    return {"station": str(_pick(attrs, "station", "station_code", "sta", default="")),
            "channel": str(_pick(attrs, "channel", "component", "channel_code", "cha", default="")),
            "network": str(_pick(attrs, "network", "network_code", "net", default="")),
            "location": str(_pick(attrs, "location", "location_code", "loc", default=""))}


def hdf5_station_channels(hdf5_filename, event_id):
    with h5py.File(str(hdf5_filename), "r") as handle:
        rows = []
        for key, group in handle[str(event_id)].items():
            if not isinstance(group, h5py.Group):
                continue
            m = _trace_meta(group)
            rows.append((m["station"], m["channel"], m["network"], str(key)))
        return rows


def _find_trace_key(handle, event_id, station, channel, trace=None):
    event_id, station, channel = str(event_id), str(station), str(channel)
    group = handle[event_id]
    if trace is not None and str(trace) not in ("", "None", "NA"):
        if str(trace) not in group:
            raise KeyError(f"Exact trace key {trace!r} not found in event {event_id!r}; no ambiguous fallback attempted")
        candidates = [str(trace)]
    else:
        candidates = [key for key, item in group.items() if isinstance(item, h5py.Group)
                      and _trace_meta(item)["station"] == station and _trace_meta(item)["channel"] == channel]
    if len(candidates) != 1:
        raise ValueError(f"Expected one trace for {event_id}/{station}/{channel}; found {len(candidates)}. Supply exact trace_id.")
    key = candidates[0]
    meta = _trace_meta(group[key])
    if meta["station"] != station or meta["channel"] != channel:
        raise ValueError(f"Trace key {key!r} does not match requested station/channel")
    return key


def hdf5_acceleration(hdf5_filename, event_id, station, channel, trace=None):
    with h5py.File(str(hdf5_filename), "r") as handle:
        key = _find_trace_key(handle, str(event_id), station, channel, trace)
        group = handle[str(event_id)][key]
        for dataset in ("acceleration_waveform", "acceleration", "acc"):
            if dataset in group:
                values = np.asarray(group[dataset][:], dtype=float)
                if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
                    raise ValueError(f"Trace {key} requires a non-empty, finite, one-dimensional acceleration dataset")
                unit = str(_pick(group[dataset].attrs, "units", "unit", default=
                           _pick(group.attrs, "acceleration_units", "units", default="m/s^2"))).lower().replace(" ", "")
                scales = {"m/s^2": 1, "m/s2": 1, "m/s/s": 1, "m.s-2": 1,
                          "m/s²": 1, "cm/s^2": .01, "cm/s2": .01, "cm/s/s": .01,
                          "gal": .01, "g": 9.80665, "mg": .00980665}
                if unit not in scales:
                    raise ValueError(f"Unsupported acceleration units {unit!r} on {key}; provide explicit physical acceleration units")
                return values * scales[unit]
        raise ValueError(f"Trace {key!r} has no acceleration waveform; remove instrument response or map a physical acceleration dataset first")


def hdf5_acceleration_meta(hdf5_filename, event_id, station, channel, trace=None):
    with h5py.File(str(hdf5_filename), "r") as handle:
        key = _find_trace_key(handle, str(event_id), station, channel, trace)
        event = _event_meta(handle[str(event_id)], event_id)
        group = handle[str(event_id)][key]
        attrs = group.attrs
        result = _trace_meta(group)
        slat = clean_float(_pick(attrs, "latitude", "station_lat", "station_latitude", "latitude_s"))
        slon = clean_float(_pick(attrs, "longitude", "station_lon", "station_longitude", "longitude_s"))
        elev = clean_float(_pick(attrs, "elevation_m", "station_elevation_m", "elevation", "elevation_s"))
        # ObsPy's RESP reader uses this unmistakable placeholder because RESP
        # does not carry station coordinates. It must never enter distance math.
        if elev == 123456:
            elev = float("nan")
        depth = event["depth"]
        sr = clean_float(_pick(attrs, "sampling_rate", "sample_rate", "sampling_frequency", "fs"))
        if not math.isfinite(sr):
            dt = clean_float(_pick(attrs, "delta", "timeStep", "dt"))
            sr = 1 / dt if dt > 0 else float("nan")
        if not sr > 0:
            raise ValueError(f"Missing or invalid sampling rate for {event_id}/{key}")
        epi = hyp = float("nan")
        if all(math.isfinite(x) for x in (slat, slon, event["latitude"], event["longitude"])):
            epi = gps2dist_azimuth(event["latitude"], event["longitude"], slat, slon)[0] / 1000
            if math.isfinite(depth) and math.isfinite(elev):
                hyp = math.hypot(epi, depth + elev / 1000)
        start = _time(_pick(attrs, "start_time", "record_start_time", "starttime", "starttime.s"))
        end = _time(_pick(attrs, "end_time", "record_end_time", "endtime"))
        if not end and start:
            sizes = [len(group[name]) for name in ("acceleration_waveform", "acceleration", "acc") if name in group]
            if sizes:
                end = _time(UTCDateTime(start.replace(" ", "T")) + (sizes[0]-1)/sr)
        result.update(trace_id=key, record_start_time=start, record_end_time=end,
                      sampling_rate=sr, station_lat=slat, station_lon=slon,
                      station_elevation_m=elev, depth_km=depth,
                      origin_time=event["origin_time"], magnitude=event["magnitude"],
                      epicentral_distance_km=float(epi), hypocentral_distance_km=float(hyp))
        return result


@lru_cache(maxsize=1)
def app_engine_fingerprint():
    engine = Path(globals().get("__file__", Path.cwd() / "hdf5_reader_v9_app.py")).resolve().parent
    code = hashlib.sha256()
    for name in ("Main AcceptReject_v11_app.R", "app_support.R", "hdf5_reader_v9_app.py"):
        code.update((engine / name).read_bytes())
    return code.hexdigest()


def app_checkpoint_key(input_path, event_id, trace_ids, save_figures=False):
    path = Path(str(input_path)).resolve()
    stat = path.stat()
    identity = ["acceptreject-v11-app-2", app_engine_fingerprint(), str(path), stat.st_size, stat.st_mtime_ns,
                str(event_id), sorted(str(x) for x in trace_ids), bool(save_figures)]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()[:24]
