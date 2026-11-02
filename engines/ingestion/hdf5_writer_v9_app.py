"""Readable-identifier successor to hdf5_writer_v8_app.py.

Scientific response removal remains ObsPy remove_response(output="ACC", plot=False)
with the original defaults. No filtering, merging, resampling or gap filling is
introduced here. HDF5 writes are serialized even when trace preparation is parallel.
See docs/ingestion_audit.md for the input contract and deliberate limitations.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import csv
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid
import warnings

import h5py
import numpy as np
import obspy
from obspy import UTCDateTime
from obspy.geodetics import gps2dist_azimuth
import pandas as pd

VERSION = "9.0-app"
WAVE_EXTENSIONS = {".mseed", ".seed", ".miniseed", ".ms", ".output", ".sac", ".gcf", ".slist", ".tspair", ".wav", ".segy", ".sgy", ".su", ".ah", ".qhd"}
RESPONSE_EXTENSIONS = {".resp", ".xml", ".stationxml", ".dataless", ".xseed"}
ALIASES = {
    "event_id": ["eventid", "event", "earthquakeid", "publicid"],
    "origin_time": ["origintime", "eventtime", "datetime", "utctime", "timestamp"],
    "year": ["year", "yyyy"], "month": ["month", "mm"], "day": ["day", "dd"],
    "hour": ["hour", "hh"], "minute": ["minute", "min", "minutes"],
    "second": ["second", "sec", "seconds"],
    "latitude": ["latitude", "lat", "latitudedeg", "stationlatitude", "eventlatitude", "hypocenterlat"],
    "longitude": ["longitude", "lon", "long", "longitudedeg", "stationlongitude", "eventlongitude", "hypocenterlon"],
    "depth": ["depth", "depthkm", "depthm", "hypocenter", "hypocentre", "focaldepth"],
    "magnitude": ["magnitude", "mag", "mw", "mb", "ml"],
    "magnitude_type": ["magnitudetype", "magtype"],
    "source_file": ["sourcefile", "filename", "file", "waveformfile", "filepath"],
    "window_start": ["windowstart", "recordstart", "segmentstart"],
    "window_end": ["windowend", "recordend", "segmentend"],
    "station": ["station", "stationcode", "sta", "stacode"],
    "network": ["network", "networkcode", "net"],
    "location": ["location", "locationcode", "loc"],
    "channel": ["channel", "channelcode", "component", "cha"],
    "elevation": ["elevation", "elevationm", "elevationkm", "stationelevation", "elev", "heightm"],
    "start_date": ["startdate", "epochstart", "validfrom"],
    "end_date": ["enddate", "epochend", "validto"],
}


def _norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _text(value):
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return ""
    return str(value).strip()


def _number(value):
    if not _text(value) or _text(value).lower() in {"na", "n/a", "nan", "null", "none"}:
        return float("nan")
    out = float(value)
    return out if math.isfinite(out) and out != -999 else float("nan")


def coordinate(value, latitude=True):
    """Decimal degrees, optionally ending in N/S/E/W; preserve hemispheres."""
    s = _text(value).upper().replace("°", "").strip()
    if not s:
        return float("nan")
    direction = s[-1] if s[-1] in "NSEW" else ""
    if direction:
        if direction not in ("NS" if latitude else "EW"):
            raise ValueError(f"Wrong hemisphere for coordinate: {value}")
        result = abs(float(s[:-1].strip())) * (-1 if direction in "SW" else 1)
    else:
        result = _number(s)
    if math.isfinite(result) and abs(result) > (90 if latitude else 180):
        raise ValueError(f"Coordinate outside degree range: {value}")
    return result


def utc_iso(value):
    """Nine decimal places; IDs never depend on display-time rounding."""
    t = value if isinstance(value, UTCDateTime) else UTCDateTime(value)
    whole = UTCDateTime(ns=(t.ns // 1_000_000_000) * 1_000_000_000)
    return whole.strftime("%Y-%m-%dT%H:%M:%S") + f".{t.ns % 1_000_000_000:09d}Z"


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def _hash(data, n=16):
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()[:n]


def _safe(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value)) or "blank"


def _utc_name(value, event=False):
    """Never let datetime microsecond rounding carry a nanosecond timestamp."""
    ns = value.ns
    whole = UTCDateTime(ns=(ns // 1_000_000_000) * 1_000_000_000)
    result = whole.strftime("%Y%m%d_%H%M%S" if event else "%Y%m%dT%H%M%S")
    fraction = ns % 1_000_000_000
    if fraction:
        result += "." + (f"{fraction // 1000:06d}" if fraction % 1000 == 0 else f"{fraction:09d}")
    return result


def stable_trace_id(trace):
    """Readable base name; run() resolves collisions before writing any samples."""
    return f"{_safe(trace.stats.station) if trace.stats.station else 'UNNAMED'}_{_safe(trace.stats.channel) if trace.stats.channel else 'UNNAMED'}_{_utc_name(trace.stats.starttime)}"


def _sample_fingerprint(seed_id, start_ns, sampling_rate, data):
    data = np.asarray(data)
    canonical = np.ascontiguousarray(data.astype(data.dtype.newbyteorder("<"), copy=False))
    digest = hashlib.sha256()
    digest.update(_json([seed_id, int(start_ns), float(sampling_rate), data.dtype.str.lstrip("<>=|"), len(data)]).encode())
    digest.update(canonical.tobytes())
    return digest.hexdigest()


def _trace_descriptor(event, trace, target, source_path=None):
    fingerprint = _sample_fingerprint(trace.id, trace.stats.starttime.ns, trace.stats.sampling_rate, trace.data)
    partial = any(not str(trace.stats.get(key, "")) for key in ("network", "station", "channel"))
    provenance = str(Path(source_path).resolve()) if partial and source_path is not None else ""
    if os.name == "nt": provenance = provenance.casefold()
    return dict(target=Path(target).name, event_id=event["event_id"], base=stable_trace_id(trace), identity_source=provenance,
                network=str(trace.stats.network), location=str(trace.stats.location),
                seed_id=trace.id, start_ns=trace.stats.starttime.ns,
                sampling_rate=float(trace.stats.sampling_rate), npts=len(trace.data),
                dtype=np.asarray(trace.data).dtype.str.lstrip("<>=|"), fingerprint=fingerprint)


def _descriptor_key(record):
    return _hash([record["target"], record["event_id"], record["fingerprint"], record.get("identity_source", "")], 64)


def _assign_trace_names(records, retained=None):
    """Assign human-readable names; retained assignments are never renamed."""
    result = {key: dict(value) for key, value in (retained or {}).items()}
    for record in records:
        key = _descriptor_key(record)
        if key in result:
            expected = {k: v for k, v in result[key].items() if k != "trace_id"}
            if expected != record:
                raise ValueError("A saved waveform identity disagrees with its input descriptor.")
        else:
            result[key] = dict(record)
    used = {}
    buckets = {}
    for key, record in result.items():
        scope = (record["target"], record["event_id"])
        if "trace_id" in record:
            name = record["trace_id"]
            if not name or "/" in name or name in used.setdefault(scope, set()):
                raise ValueError("Saved waveform names are missing, invalid or duplicated.")
            used[scope].add(name)
        buckets.setdefault((*scope, record["base"]), []).append((key, record))
    for bucket in sorted(buckets):
        entries = sorted(buckets[bucket], key=lambda pair: _json(pair[1]))
        scope = bucket[:2]
        occupied = used.setdefault(scope, set())
        for key, record in entries:
            if "trace_id" in record:
                continue
            name = record["base"]
            if len(entries) > 1:
                name += "__NET_" + (_safe(record["network"]) if record["network"] else "none")
                name += "_LOC_" + (_safe(record["location"]) if record["location"] else "none")
                peers = [other for _, other in entries if other["network"] == record["network"] and other["location"] == record["location"]]
                if len(peers) > 1:
                    name += "__SR_" + _safe(format(record["sampling_rate"], '.17g')) + "Hz_N_" + str(record["npts"])
                    identical_headers = [other for other in peers if other["sampling_rate"] == record["sampling_rate"] and other["npts"] == record["npts"]]
                    if len(identical_headers) > 1:
                        rank = sorted((other["fingerprint"],other.get("identity_source", "")) for other in identical_headers).index((record["fingerprint"],record.get("identity_source", ""))) + 1
                        name += f"__record_{rank:02d}"
            candidate, ordinal = name, 1
            while candidate in occupied:
                candidate = name + f"__record_{ordinal:02d}"
                ordinal += 1
            record["trace_id"] = candidate
            occupied.add(candidate)
    return result


def _group_matches_identity(group, record):
    """A readable name alone is never evidence that two recordings are identical."""
    if "raw_waveform" not in group:
        return False
    attrs = group.attrs
    try:
        actual = _sample_fingerprint(str(attrs["seed_id"]), attrs["start_time_ns"], attrs["sampling_rate"], group["raw_waveform"][:])
    except (KeyError, TypeError, ValueError):
        return False
    source = record.get("identity_source", "")
    stored_source = str(Path(str(attrs.get("source_file", ""))).resolve())
    if os.name == "nt": stored_source = stored_source.casefold()
    return actual == record["fingerprint"] and str(attrs.get("trace_id", "")) == record["trace_id"] and (not source or stored_source == source)


def _identity_plan(raw, events, config, output_dir, grouping, fingerprint, cancelled, progress):
    """Extra input pass; only descriptors survive beyond one waveform file."""
    registry_path = output_dir / "trace_identity_registry.json"
    retained = {}
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if registry.get("schema") != "signalqc.trace_identity.v9" or registry.get("fingerprint") != fingerprint:
            raise ValueError("Saved waveform identity registry does not match this ingestion run.")
        retained = registry["records"]
        if any(key != _descriptor_key(value) or not value.get("trace_id") for key, value in retained.items()):
            raise ValueError("Saved waveform identity registry contains an invalid assignment.")
    elif any(output_dir.glob("*.h5")):
        raise ValueError("Readable-ID HDF5 files require their matching trace_identity_registry.json for safe resume.")
    records, seen = {}, 0
    selectors = config.get("trace_selectors") or []
    if isinstance(selectors, str): selectors = [selectors]
    for file_no, path in enumerate(raw, 1):
        if cancelled(): break
        progress({"stage": "ingestion", "message": f"Checking record identities {file_no}/{len(raw)}: {path.name}", "current": file_no, "total": len(raw)})
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                selected_format = config.get("waveform_format", "auto")
                stream = obspy.read(str(path), format=None if selected_format == "auto" else selected_format)
        except Exception:
            # Normal ingestion records a per-file read error and continues.
            continue
        for original in stream:
            if selectors and not any(fnmatch.fnmatchcase(original.id, selector) for selector in selectors): continue
            segments = original.split() if np.ma.isMaskedArray(original.data) else [original]
            for trace in segments:
                if cancelled(): break
                if config.get("max_traces") and seen >= int(config["max_traces"]): break
                seen += 1
                if len(trace.data) < 2 or not np.all(np.isfinite(trace.data)) or trace.stats.sampling_rate <= 0: continue
                associated, _ = _associate(trace, path, events, config)
                for event, segment in associated:
                    record = _trace_descriptor(event, segment, _target(event, output_dir, grouping), path)
                    records[_descriptor_key(record)] = record
        if config.get("max_traces") and seen >= int(config["max_traces"]): break
    if cancelled():
        # No HDF5 writes occurred; do not freeze partial first-pass assignments.
        return retained, registry_path
    assigned = _assign_trace_names(records.values(), retained)
    _atomic_json(registry_path, {"schema": "signalqc.trace_identity.v9", "fingerprint": fingerprint, "records": assigned})
    return assigned, registry_path


def _assign_event_names(events):
    """Keep identical catalog-event identities together without exposed hashes."""
    buckets = {}
    for event in events:
        identity = _json([event["origin"].ns, event["latitude"], event["longitude"], event["depth"], event["magnitude"], event["catalog_event_id"]])
        buckets.setdefault(_utc_name(event["origin"], event=True), {}).setdefault(identity, []).append(event)
    occupied = set()
    for base in sorted(buckets):
        distinct = buckets[base]
        for identity in sorted(distinct):
            group = distinct[identity]
            event = group[0]
            name = base
            if len(distinct) > 1:
                if event.get("catalog_event_id"):
                    name += "__ID_" + _safe(event["catalog_event_id"])
                else:
                    name += "__LAT_" + _safe(format(event["latitude"], '.9g'))
                    name += "_LON_" + _safe(format(event["longitude"], '.9g'))
                    name += "_DEPTH_" + _safe(format(event["depth"], '.9g')) + "km"
            candidate, ordinal = name, 1
            while candidate in occupied:
                candidate = name + f"__event_{ordinal:02d}"
                ordinal += 1
            occupied.add(candidate)
            for member in group: member["event_id"] = candidate



def distances(event_lat, event_lon, depth_km, station_lat, station_lon, elevation_m):
    """WGS84 surface distance and straight-line depth correction, in km."""
    if not all(math.isfinite(float(v)) for v in (event_lat, event_lon, station_lat, station_lon)):
        return float("nan"), float("nan")
    epi = float(gps2dist_azimuth(event_lat, event_lon, station_lat, station_lon)[0]) / 1000.0
    hyp = math.hypot(epi, depth_km + elevation_m / 1000.0) if all(math.isfinite(float(v)) for v in (depth_km, elevation_m)) else float("nan")
    return epi, hyp


def _paths(config, key, extensions, responses=False):
    selected = config.get(key) or []
    if isinstance(selected, str):
        selected = [selected]
    found = {}
    outdir = Path(config.get("output_dir", ".")).resolve()
    excluded = {".git", ".venv", "__pycache__", ".idea"}
    for item in selected:
        p = Path(item).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"Selected path does not exist: {p}")
        if p.is_file():
            found[str(p).casefold()] = p
            continue
        for base, dirs, names in os.walk(p):
            dirs[:] = [d for d in dirs if d not in excluded and Path(base, d).resolve() != outdir]
            for name in names:
                f = Path(base, name)
                if name.startswith("~$"):
                    continue
                if f.suffix.lower() in extensions or (responses and name.upper().startswith("RESP.")) or (not responses and config.get("scan_all_files", False)):
                    found[str(f).casefold()] = f
    return sorted(found.values(), key=lambda p: str(p).casefold())


def _read_table(path, mappings, kind, config):
    if not path:
        return [], {}, []
    path = Path(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        frame = pd.read_excel(path, sheet_name=config.get(f"{kind}_sheet", 0), dtype=object, keep_default_na=False)
    else:
        sep = config.get(f"{kind}_delimiter") or ("\t" if path.suffix.lower() == ".tsv" else None)
        encoding = config.get(f"{kind}_encoding", "utf-8-sig")
        if sep is None:
            with path.open(encoding=encoding) as source:
                sample = source.read(65536)
            try:
                sep = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                sep = ","
        frame = pd.read_csv(path, sep=sep, engine="python", dtype=object, encoding=encoding, keep_default_na=False)
    resolved = {}
    issues = []
    for field, aliases in ALIASES.items():
        explicit = mappings.get(field)
        if explicit:
            if explicit not in frame.columns:
                raise ValueError(f"{kind} mapping '{field}' names absent column '{explicit}'")
            resolved[field] = explicit
            continue
        matches = [c for c in frame.columns if _norm(c) in aliases]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous {kind} columns for {field}: {matches}. Set column_mappings.{kind}.{field}.")
        if matches:
            resolved[field] = matches[0]
    rows = []
    for index, row in frame.iterrows():
        if all(not _text(v) for v in row):
            continue
        rows.append({**{k: row[v] for k, v in resolved.items()}, "_row": int(index) + 2})
    return rows, resolved, issues


def _parse_time(value):
    if isinstance(value, pd.Timestamp):
        return UTCDateTime(ns=value.value)
    if isinstance(value, datetime):
        return UTCDateTime(value)
    text = _text(value)
    try:
        return UTCDateTime(ns=pd.Timestamp(text).value)
    except (ValueError, OverflowError):
        return UTCDateTime(text)


def _catalogs(config):
    mappings = config.get("column_mappings") or {}
    evrows, evcols, issues = _read_table(config.get("event_catalog"), mappings.get("events", mappings.get("event", {})), "events", config)
    strows, stcols, _ = _read_table(config.get("station_catalog"), mappings.get("stations", mappings.get("station", {})), "stations", config)
    events, stations = [], []
    depth_unit = config.get("event_depth_unit") or ("m" if _norm(evcols.get("depth", "")) == "depthm" else "km")
    elevation_unit = config.get("station_elevation_unit") or ("km" if _norm(stcols.get("elevation", "")) == "elevationkm" else "m")
    if depth_unit not in {"m", "km"} or elevation_unit not in {"m", "km"}:
        raise ValueError("Depth and elevation units must be 'm' or 'km'.")
    for row in evrows:
        try:
            if _text(row.get("origin_time")):
                origin = _parse_time(row["origin_time"])
            else:
                # Add fractional seconds; never round/clamp to 59 as v7.3 did.
                parts = {k: int(row[k]) for k in ("year", "month", "day", "hour", "minute")}
                sec = float(row["second"])
                if not 0 <= sec < 61:
                    raise ValueError("Seconds must be in [0,61); ISO timestamps recommended for leap seconds.")
                origin = UTCDateTime(**parts) + sec
            event = {"origin": origin, "latitude": coordinate(row.get("latitude")), "longitude": coordinate(row.get("longitude"), False),
                     "depth": _number(row.get("depth")) / (1000 if depth_unit == "m" else 1), "magnitude": _number(row.get("magnitude")),
                     "magnitude_type": _text(row.get("magnitude_type")), "source_file": _text(row.get("source_file")), "catalog_event_id": _text(row.get("event_id")), "catalog_row": row["_row"]}
            for field in ("window_start", "window_end"):
                event[field] = _parse_time(row[field]) if _text(row.get(field)) else None
            if (event["window_start"] is None) != (event["window_end"] is None):
                raise ValueError("Both window_start and window_end are required to define a segment.")
            if event["window_start"] is not None and event["window_start"] >= event["window_end"]:
                raise ValueError("window_end must follow window_start")
            for field in ("network", "station", "location", "channel"):
                event[field] = _text(row.get(field))
            identity = [origin.ns, event["latitude"], event["longitude"], event["depth"], event["magnitude"], event["catalog_event_id"]]
            event["event_id"] = origin.strftime("%Y%m%d_%H%M%S") + f"_{origin.ns % 1_000_000_000:09d}_{_hash(identity, 10)}"
            events.append(event)
        except Exception as exc:
            issues.append({"kind": "invalid_event_row", "row": row["_row"], "message": str(exc)})
    _assign_event_names(events)
    for row in strows:
        try:
            if not _text(row.get("station")):
                raise ValueError("A station code is required")
            station = {k: _text(row.get(k)) for k in ("network", "station", "location", "channel")}
            station.update(latitude=coordinate(row.get("latitude")), longitude=coordinate(row.get("longitude"), False), elevation=_number(row.get("elevation")) * (1000 if elevation_unit == "km" else 1))
            for field in ("start_date", "end_date"):
                station[field] = _parse_time(row[field]) if _text(row.get(field)) else None
            stations.append(station)
        except Exception as exc:
            issues.append({"kind": "invalid_station_row", "row": row["_row"], "message": str(exc)})
    if config.get("event_catalog") and not events:
        raise ValueError("No valid event rows. " + _json(issues[:5]))
    return events, stations, {"events": evcols, "stations": stcols, "event_depth_unit": depth_unit, "station_elevation_unit": elevation_unit}, issues


def discover(config):
    """Read-only candidates and column resolution, not a claim that all files decode."""
    raw = _paths(config, "raw_paths", WAVE_EXTENSIONS)
    responses = _paths(config, "response_paths", RESPONSE_EXTENSIONS, True)
    events, stations, columns, issues = _catalogs(config)
    return {"raw_files": [str(p) for p in raw], "response_files": [str(p) for p in responses], "raw_file_count": len(raw), "response_file_count": len(responses), "event_count": len(events), "station_row_count": len(stations), "resolved_columns": columns, "issues": issues}


@dataclass
class ResponseEntry:
    seed_id: str
    start: object
    end: object
    inventory: object
    response: object
    latitude: float
    longitude: float
    elevation: float
    source: str


def _load_responses(files, progress, cancelled):
    index, issues, loaded, epochs = {}, [], 0, 0
    for n, path in enumerate(files, 1):
        if cancelled():
            break
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                inventory = obspy.read_inventory(str(path))
            from obspy.io.xseed.core import _is_resp
            is_resp = _is_resp(str(path))
            for note in caught:
                issues.append({"kind": "response_warning", "file": str(path), "message": str(note.message)})
            loaded += 1
            for net in inventory:
                for sta in net:
                    for cha in sta:
                        if cha.response is None:
                            continue
                        starts = [x for x in (net.start_date, sta.start_date, cha.start_date) if x is not None]
                        ends = [x for x in (net.end_date, sta.end_date, cha.end_date) if x is not None]
                        start, end = (max(starts) if starts else None), (min(ends) if ends else None)
                        seed_id = ".".join([net.code, sta.code, cha.location_code, cha.code])
                        # Construct a single-channel inventory, so ObsPy cannot silently choose another epoch.
                        from obspy.core.inventory import Inventory, Network
                        only_sta = sta.copy()
                        only_sta.channels = [cha.copy()]
                        selected = Inventory([Network(code=net.code, stations=[only_sta])], source="SignalQC")
                        # RESP contains no geographic coordinates. ObsPy supplies placeholders
                        # (including elevation 123456); these must never enter distances.
                        lat, lon, elev = np.nan, np.nan, np.nan
                        if not is_resp:
                            try:
                                lat, lon, elev = coordinate(cha.latitude), coordinate(cha.longitude, False), _number(cha.elevation)
                            except ValueError as exc:
                                issues.append({"kind": "inventory_coordinates", "file": str(path), "message": str(exc)})
                        entry = ResponseEntry(seed_id, start, end, selected, cha.response, lat, lon, elev, str(path))
                        index.setdefault(seed_id, []).append(entry)
                        epochs += 1
        except Exception as exc:
            issues.append({"kind": "response_read_error", "file": str(path), "message": str(exc)})
        progress({"stage": "ingestion", "message": f"Response inventory {n}/{len(files)}: {path.name}", "current": n, "total": len(files)})
    return index, issues, loaded, epochs


def _response_for(trace, index):
    candidates = [e for e in index.get(trace.id, []) if (e.start is None or e.start <= trace.stats.starttime) and (e.end is None or e.end >= trace.stats.endtime)]
    if not candidates:
        return None, "No response matching network, station, location, channel and entire trace epoch."
    unique = []
    for entry in candidates:
        if not any(entry.response == old.response for old in unique):
            unique.append(entry)
    if len(unique) != 1:
        return None, f"{len(unique)} conflicting responses match {trace.id}; select an unambiguous response collection."
    return unique[0], ""


def _filename_matches(selector, path):
    for token in re.split(r"[|;]", selector):
        value = token.strip().replace("\\", "/").casefold()
        if not value:
            continue
        full = path.as_posix().casefold()
        if any(c in value for c in "*?["):
            if fnmatch.fnmatchcase(full, value) or fnmatch.fnmatchcase(path.name.casefold(), value):
                return True
        elif value in (full, path.name.casefold(), path.stem.casefold()) or full.endswith("/" + value):
            return True
    return False


def _associate(trace, path, events, config):
    if not events:
        anchor = trace.stats.starttime
        event_id = "recording_" + anchor.strftime("%Y%m%d_%H%M%S") + "_" + _hash([str(path), anchor.ns], 10)
        return [(dict(event_id=event_id, origin=None, anchor=anchor, latitude=np.nan, longitude=np.nan, depth=np.nan, magnitude=np.nan, magnitude_type="", catalog_event_id="", catalog_row=0), trace)], ""
    eligible = []
    tolerance = float(config.get("event_time_tolerance_s", 0))
    if tolerance < 0:
        raise ValueError("event_time_tolerance_s must be nonnegative")
    for event in events:
        if any(event.get(k) and event[k] != str(trace.stats.get(k, "")) for k in ("network", "station", "location", "channel")):
            continue
        source = event.get("source_file")
        if source and not _filename_matches(source, path):
            continue
        start, end = event.get("window_start"), event.get("window_end")
        if start is not None:
            if end < trace.stats.starttime or start > trace.stats.endtime:
                continue
        elif not source and not trace.stats.starttime - tolerance <= event["origin"] <= trace.stats.endtime + tolerance:
            continue
        eligible.append(event)
    unique = {}
    for event in eligible:
        key = (event["event_id"], str(event.get("window_start")), str(event.get("window_end")))
        unique[key] = event
    eligible = list(unique.values())
    if not eligible:
        return [], "No catalog event matched the trace; use exact source_file mapping or a documented time window."
    if len(eligible) > 1 and any(e.get("window_start") is None for e in eligible):
        return [], "Multiple catalog events match this trace. Supply window_start/window_end for each event; the full trace is never duplicated across events."
    if len(eligible) > 1:
        ordered = sorted(eligible, key=lambda e: e["window_start"].ns)
        if any(a["window_end"] >= b["window_start"] for a, b in zip(ordered, ordered[1:])):
            return [], "Event windows overlap; select non-overlapping bounds to avoid duplicate samples."
    results = []
    for event in eligible:
        segment = trace
        if event.get("window_start") is not None:
            segment = trace.copy().trim(starttime=event["window_start"], endtime=event["window_end"], pad=False, nearest_sample=False)
        if len(segment.data):
            results.append((event, segment))
    return results, ""


def _station_meta(trace, stations, response):
    matches = [s for s in stations if s["station"] == trace.stats.station and all(not s[k] or s[k] == str(trace.stats.get(k, "")) for k in ("network", "location", "channel")) and (s["start_date"] is None or s["start_date"] <= trace.stats.starttime) and (s["end_date"] is None or s["end_date"] >= trace.stats.endtime)]
    unique = {_json([s["latitude"], s["longitude"], s["elevation"]]): s for s in matches}
    if len(unique) > 1:
        return {"latitude": np.nan, "longitude": np.nan, "elevation": np.nan, "station_metadata_source": "ambiguous"}, "Conflicting station metadata; set network/location/channel/epochs in station catalog."
    if matches:
        s = matches[0]
        return {k: s[k] for k in ("latitude", "longitude", "elevation")} | {"station_metadata_source": "station_catalog"}, ""
    if response and all(math.isfinite(v) for v in (response.latitude, response.longitude, response.elevation)):
        return {"latitude": response.latitude, "longitude": response.longitude, "elevation": response.elevation, "station_metadata_source": response.source}, ""
    return {"latitude": np.nan, "longitude": np.nan, "elevation": np.nan, "station_metadata_source": "missing"}, "No station coordinates/elevation in catalog or matching inventory."


def _prepare(task, index, stations, config):
    event, trace, path = task[:3]
    key = task[3] if len(task) > 3 else stable_trace_id(trace)
    issues = []
    def note(kind, message):
        issues.append({"kind": kind, "file": str(path), "trace_id": key, "message": message})
    response, error = _response_for(trace, index)
    meta, station_error = _station_meta(trace, stations, response)
    if station_error:
        note("station_metadata", station_error)
    epi, hyp = distances(event["latitude"], event["longitude"], event["depth"], meta["latitude"], meta["longitude"], meta["elevation"])
    meta.update(network=str(trace.stats.network), station=str(trace.stats.station), location=str(trace.stats.location), channel=str(trace.stats.channel),
                start_time=utc_iso(trace.stats.starttime), end_time=utc_iso(trace.stats.endtime), start_time_ns=trace.stats.starttime.ns,
                sampling_rate=float(trace.stats.sampling_rate), npts=len(trace.data), trace_id=key, seed_id=trace.id, rec_id=trace.id,
                epicentral_distance_km=epi, hypocentral_distance_km=hyp, elevation_units="m", distance_units="km",
                response_file=response.source if response else "", raw_units=str(config.get("input_units", "counts")), acceleration_units="m/s^2",
                raw_source_format=str(trace.stats.get("_format", "")))
    missing_identity = [name for name in ("network", "station", "channel") if not str(trace.stats.get(name, ""))]
    meta["identity_status"] = "partial" if missing_identity else "complete"
    meta["missing_identity_fields"] = ",".join(missing_identity)
    if missing_identity:
        note("partial_record_identity", "Missing " + ", ".join(missing_identity) + "; generated display ID is used, physical metadata remains blank, and source-file identity prevents cross-file deduplication.")
    if not trace.stats.network:
        note("missing_network", "Waveform network is empty; preserved empty and never replaced with NE.")
    if not math.isfinite(hyp) or not math.isfinite(event["magnitude"]):
        note("incomplete_stage_metadata", "Magnitude or hypocentral distance is missing; processing stages may require metadata correction.")
    acceleration = np.array([], dtype=np.float32)
    status = "raw_only"
    if config.get("remove_response", True):
        if response is None:
            status = "response_missing_or_ambiguous"
            note("response_match", error)
        else:
            try:
                corrected = trace.copy()
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    # Equivalent to v7.3 attach_response + remove_response; explicit
                    # inventory avoids the deprecation without changing deconvolution.
                    corrected.remove_response(inventory=response.inventory, output="ACC", plot=False)
                for warning in caught:
                    note("response_removal_warning", str(warning.message))
                if not np.all(np.isfinite(corrected.data)):
                    raise ValueError("Response removal generated non-finite samples")
                acceleration = corrected.data.astype(np.float32)
                status = "response_removed"
            except Exception as exc:
                status = "response_failed"
                note("response_error", str(exc))
    elif config.get("input_units") in {"m/s^2", "m/s2", "m/s²", "cm/s^2", "cm/s2", "g"}:
        factor = {"cm/s^2": .01, "cm/s2": .01, "g": 9.80665}.get(config["input_units"], 1)
        acceleration = (np.asarray(trace.data, dtype=np.float64) * factor).astype(np.float32)
        status = "declared_acceleration"
    meta["ingestion_status"] = status
    meta["ready_for_processing"] = bool(len(acceleration) and math.isfinite(hyp) and math.isfinite(event["magnitude"]))
    return event, key, np.asarray(trace.data), acceleration, meta, str(path), issues


def _target(event, output_dir, grouping):
    date = event["origin"] if event.get("origin") is not None else event["anchor"]
    pattern = {"monthly": "%Y_%m", "yearly": "%Y", "daily": "%Y_%m_%d", "single": "waveforms"}[grouping]
    return output_dir / (date.strftime(pattern) + ".h5")


def _atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _fingerprint(config, responses):
    ignored = {"resume", "workers", "max_files", "max_traces", "output_dir", "raw_paths"}
    significant = {k: v for k, v in config.items() if k not in ignored}
    external = []
    for path in [*responses, *[Path(config[k]) for k in ("event_catalog", "station_catalog") if config.get(k)]]:
        stat = path.stat()
        external.append([str(path.resolve()), stat.st_size, stat.st_mtime_ns])
    return _hash([VERSION, significant, external], 32)


def _resume_complete(group):
    return bool(group.attrs.get("complete", False)) and group.attrs.get("ingestion_status") in {"response_removed", "declared_acceleration", "raw_only"}


def _prepared_tasks(tasks, executor, workers, index, stations, config, cancelled):
    """Only workers tasks may be outstanding; stop does not queue a whole file."""
    pending = deque()
    remaining = iter(tasks)
    for _ in range(workers):
        task = next(remaining, None)
        if task is not None and not cancelled():
            pending.append(executor.submit(_prepare, task, index, stations, config))
    while pending:
        if cancelled():
            for future in pending:
                future.cancel()
            return
        prepared = pending.popleft().result()
        if cancelled():
            for future in pending:
                future.cancel()
            return
        yield prepared
        task = next(remaining, None)
        if task is not None and not cancelled():
            pending.append(executor.submit(_prepare, task, index, stations, config))


def run(config, progress=None, cancelled=None):
    """Ingest data without modifying inputs. Callback messages are dictionaries."""
    config = dict(config)
    progress = progress or (lambda message: None)
    cancelled = cancelled or (lambda: False)
    output_dir = Path(config.get("output_dir") or "").expanduser().resolve()
    if not config.get("output_dir"):
        raise ValueError("output_dir is required")
    grouping = config.get("grouping", "monthly")
    if grouping not in {"monthly", "yearly", "daily", "single"}:
        raise ValueError("grouping must be monthly, yearly, daily or single")
    if config.get("input_units") not in {None, "counts", "m/s^2", "m/s2", "m/s²", "cm/s^2", "cm/s2", "g"}:
        raise ValueError("input_units must be counts, m/s^2, cm/s^2 or g")
    if config.get("remove_response", True) and config.get("input_units") not in {None, "counts"}:
        raise ValueError("Instrument-response removal expects raw counts. Disable response removal for already calibrated acceleration, or set input_units to counts for uncalibrated data.")
    workers = int(config.get("workers") or 1)
    workers = min(max(workers, 1), os.cpu_count() or 1, 32)
    raw = _paths(config, "raw_paths", WAVE_EXTENSIONS)
    response_files = _paths(config, "response_paths", RESPONSE_EXTENSIONS, True)
    if config.get("max_files"):
        raw = raw[:int(config["max_files"])]
    if not raw:
        raise ValueError("No waveform candidates detected. Choose files directly for unlisted extensions, or enable scan_all_files.")
    events, stations, columns, issues = _catalogs(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint(config, response_files)
    manifest_path = output_dir / "ingestion_manifest.json"
    old = {}
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if old.get("fingerprint") != fingerprint:
            raise ValueError("Ingestion settings or metadata changed. Select a new output folder to prevent mixing incompatible data with a previous run.")
        if not config.get("resume", True):
            raise ValueError("This output folder already has ingestion results. Enable resume or select a new empty folder.")
    elif any(output_dir.glob("*.h5")):
        raise ValueError("Output folder contains HDF5 files without this writer's manifest. Choose a new folder.")
    result = {"version": VERSION, "fingerprint": fingerprint, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat(), "output_files": [], "files_seen": 0, "traces_seen": 0, "traces_written": 0, "traces_skipped": 0, "responses_applied": 0, "ready_for_processing": 0, "workers": workers, "resolved_columns": columns, "issues": issues, "manifest": str(manifest_path)}
    result["config"] = config
    # Retain useful diagnostics when successful traces are skipped on restart.
    result["issues"].extend(old.get("issues", []))
    _atomic_json(manifest_path, result)
    response_index, response_issues, response_loaded, epochs = _load_responses(response_files, progress, cancelled)
    result.update(response_files_detected=len(response_files), response_files_loaded=response_loaded, response_channel_epochs=epochs)
    result["issues"].extend(response_issues)
    output_files = set()
    try:
        identities, registry_path = _identity_plan(raw, events, config, output_dir, grouping, fingerprint, cancelled, progress)
        result["identity_registry"] = str(registry_path)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for file_no, path in enumerate(raw, 1):
                if cancelled():
                    break
                result["files_seen"] += 1
                progress({"stage": "ingestion", "message": f"Reading {file_no}/{len(raw)}: {path.name}", "current": file_no, "total": len(raw)})
                try:
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        selected_format = config.get("waveform_format", "auto")
                        stream = obspy.read(str(path), format=None if selected_format == "auto" else selected_format)
                    for note in caught:
                        result["issues"].append({"kind": "waveform_read_warning", "file": str(path), "message": str(note.message)})
                except Exception as exc:
                    result["issues"].append({"kind": "waveform_read_error", "file": str(path), "message": str(exc)})
                    continue
                tasks = []
                for original in stream:
                    selectors = config.get("trace_selectors") or []
                    if isinstance(selectors, str):
                        selectors = [selectors]
                    if selectors and not any(fnmatch.fnmatchcase(original.id, selector) for selector in selectors):
                        continue
                    # Split masked streams into contiguous pieces; never bridge a gap.
                    segments = original.split() if np.ma.isMaskedArray(original.data) else [original]
                    for trace in segments:
                        if cancelled():
                            break
                        if config.get("max_traces") and result["traces_seen"] >= int(config["max_traces"]):
                            break
                        result["traces_seen"] += 1
                        if len(trace.data) < 2 or not np.all(np.isfinite(trace.data)) or trace.stats.sampling_rate <= 0:
                            result["issues"].append({"kind": "invalid_waveform", "file": str(path), "trace": trace.id, "message": "Trace needs at least 2 finite samples and positive sampling rate."})
                            continue
                        associated, error = _associate(trace, path, events, config)
                        if error:
                            result["issues"].append({"kind": "event_association", "file": str(path), "trace": trace.id, "message": error})
                            result["traces_skipped"] += 1
                        for event, segment in associated:
                            target = _target(event, output_dir, grouping)
                            descriptor = _trace_descriptor(event, segment, target, path)
                            identity = identities.get(_descriptor_key(descriptor))
                            if identity is None:
                                raise ValueError("Waveform content changed after record-identity planning. Restart with stable inputs.")
                            key = identity["trace_id"]
                            if target.exists() and config.get("resume", True):
                                with h5py.File(target, "r") as h5:
                                    existing = h5.get(event["event_id"] + "/" + key)
                                    if existing is not None and not _group_matches_identity(existing, identity):
                                        raise ValueError(f"Stored waveform identity differs for {event['event_id']}/{key}; existing data was preserved.")
                                    if existing is not None and _resume_complete(existing):
                                        result["traces_skipped"] += 1
                                        result["ready_for_processing"] += bool(existing.attrs.get("ready_for_processing", False))
                                        output_files.add(str(target))
                                        continue
                            tasks.append((event, segment, path, key))
                # Bound outstanding arrays to one waveform file. HDF5 is touched only here.
                for prepared in _prepared_tasks(tasks, executor, workers, response_index, stations, config, cancelled):
                    if cancelled():
                        break
                    event, key, data, acceleration, metadata, source_path, trace_issues = prepared
                    result["issues"].extend(trace_issues)
                    target = _target(event, output_dir, grouping)
                    with h5py.File(target, "a") as h5:
                        h5.attrs.update(writer_version=VERSION, ingestion_fingerprint=fingerprint, schema="SignalQC event/trace v1")
                        eg = h5.require_group(event["event_id"])
                        eg.attrs.update(event_id=event["event_id"], origin_time=utc_iso(event["origin"]) if event["origin"] is not None else "", latitude=event["latitude"], longitude=event["longitude"], depth=event["depth"], depth_units="km", magnitude=event["magnitude"], magnitude_type=event.get("magnitude_type", ""), catalog_event_id=event.get("catalog_event_id", ""), event_kind="catalog_event" if event["origin"] is not None else "unassociated_recording")
                        files = set(str(eg.attrs.get("file_format", "")).split("|")) - {""}
                        files.add(Path(source_path).name)
                        eg.attrs["file_format"] = "|".join(sorted(files))
                        for stale in [k for k in eg if k.startswith("_pending_")]:
                            del eg[stale]
                        if key in eg:
                            identity = next(record for record in identities.values() if record["target"] == target.name and record["event_id"] == event["event_id"] and record["trace_id"] == key)
                            if not _group_matches_identity(eg[key], identity):
                                raise ValueError(f"Stored waveform identity differs for {event['event_id']}/{key}; existing data was preserved.")
                            if _resume_complete(eg[key]):
                                result["traces_skipped"] += 1
                                continue
                            del eg[key]
                        temporary = "_pending_" + uuid.uuid4().hex
                        tg = eg.create_group(temporary)
                        tg.attrs.update(**metadata, source_file=source_path, complete=False)
                        tg.create_dataset("raw_waveform", data=data, compression="gzip", shuffle=True)
                        tg.create_dataset("acceleration_waveform", data=acceleration, compression="gzip", shuffle=True)
                        tg.attrs["complete"] = True
                        h5.flush()
                        eg.move(temporary, key)
                        h5.flush()
                    output_files.add(str(target))
                    result["traces_written"] += 1
                    result["responses_applied"] += metadata["ingestion_status"] == "response_removed"
                    result["ready_for_processing"] += metadata["ready_for_processing"]
                    progress({"stage": "ingestion", "message": f"Stored {metadata['seed_id']} ({result['traces_written']} traces)", "current": file_no, "total": len(raw)})
                result["output_files"] = sorted(output_files)
                _atomic_json(manifest_path, result)
                if config.get("max_traces") and result["traces_seen"] >= int(config["max_traces"]):
                    break
        result["issues"] = list({_json(issue): issue for issue in result["issues"]}.values())
        result["status"] = "cancelled" if cancelled() else ("completed_with_issues" if result["issues"] else "completed")
    except Exception as exc:
        result["status"] = "failed"
        result["issues"].append({"kind": "run_error", "message": str(exc)})
        raise
    finally:
        result["output_files"] = sorted(output_files)
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(manifest_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="JSON config path")
    parser.add_argument("--discover", action="store_true", help="Read-only inventory and column mapping")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    report = discover(config) if args.discover else run(config, progress=lambda msg: print(json.dumps(msg, default=str), flush=True))
    print(json.dumps(report, indent=2, default=str))
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
