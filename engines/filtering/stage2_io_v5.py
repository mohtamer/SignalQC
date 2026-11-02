"""Non-computational I/O for Main_AutoFC_v17_app.R.

Signal filtering is performed only in the R main and its original dependencies.
This module joins exact trace identities, repairs metadata and persists R results.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from obspy.geodetics import gps2dist_azimuth


# Only attributes written by known app engines are omitted from new outputs.
# Arbitrary imported scientific/user metadata is retained unchanged.
INTERNAL_TRACE_ATTRIBUTES = frozenset({"processing_metadata_json", "method_corners",
    "method_errors", "figure_path", "figure_avd", "figure_fas"})


def valid_processed_suffix(value):
    value=str(value)
    return bool(value.strip()) and not value.endswith((' ','.')) and not any(c in value for c in '/\\:*?"<>|')


def _identity_reader():
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "acceptreject" / "hdf5_reader_v10_app.py"
    spec = importlib.util.spec_from_file_location("signalqc_identity_reader_v10_filtering", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(v):
    return v.decode("utf-8") if isinstance(v, bytes) else str(v)


def _number(v):
    try:
        value = float(v)
        return value if math.isfinite(value) and value != -999 else float("nan")
    except (ValueError, TypeError):
        return float("nan")


def _bool(v):
    return _text(v).strip().lower() in ("true", "1", "yes", "accepted", "accept")


def _time(v):
    s = _text(v).strip().replace(" ", "T")
    try:
        # Preserve all nine fractional digits for legacy identity comparisons.
        fractional = re.search(r"\d{2}:\d{2}:\d{2}(\.\d+)?", s)
        digits = (fractional.group(1) or ".0")[1:] if fractional else "0"
        without_fraction = re.sub(r"(?<=\d{2}:\d{2}:\d{2})\.\d+", "", s)
        dt = datetime.fromisoformat(without_fraction.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "." + digits.ljust(9, "0") + "Z"
    except ValueError:
        return s


def _shift_time(value, offset_seconds):
    canonical = _time(value)
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z", canonical):
        raise ValueError("Invalid precise timestamp")
    dt = datetime.fromisoformat(canonical[:19]).replace(tzinfo=timezone.utc)
    total_ns = int(dt.timestamp()) * 1_000_000_000 + int(canonical[20:29]) + round(float(offset_seconds) * 1_000_000_000)
    seconds, nanoseconds = divmod(total_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanoseconds:09d}Z"


def _timestamp_ns(value):
    canonical = _time(value)
    dt = datetime.fromisoformat(canonical[:19]).replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000 + int(canonical[20:29])


def _group_records(frame):
    """Group compatible components within one sample, preserving exact keys.

    This follows Stage 1's tolerance; duplicate components and unequal rates
    are separate records. No waveform is shifted or interpolated here.
    """
    buckets, result = {}, {}
    for index, row in frame.iterrows():
        base = tuple(str(row[field]) for field in ("event_id", "network", "station", "location")) + (str(row.component)[:2],)
        if not str(row.network).strip() or not str(row.station).strip():
            token = str(row.get("identity_group", "")).strip() or "isolated_" + str(row.trace_id)
            base = (str(row.event_id), "partial", token, str(row.component)[:2])
        try:
            timestamp = _timestamp_ns(row["starttime.s"])
            dt = _number(row.timeStep)
            if not math.isfinite(dt) or dt <= 0:
                raise ValueError("Invalid sampling interval")
            clusters = buckets.setdefault(base, [])
            match = next((c for c in clusters if len(c["components"]) < 3 and str(row.component) not in c["components"] and
                          math.isclose(c["dt"], dt, rel_tol=1e-10) and abs(c["start"] - timestamp) <= round(dt * 1e9)), None)
            if match is None:
                match = dict(start=timestamp, dt=dt, components=set(), group="||".join(base) + f"||{timestamp}||{len(clusters)}")
                clusters.append(match)
            match["components"].add(str(row.component))
            result[index] = match["group"]
        except (ValueError, TypeError):
            result[index] = "||".join(base) + "||invalid_time||" + str(index)
    return pd.Series(result)


def prepare_metadata(h5_path, meta_path, output_dir):
    """Normalize Stage 1 table; source HDF5 attributes are authoritative.

    A missing exact key is matched only if NSLC and precise time select ONE trace.
    Ambiguity is an error on that row, never a first-candidate selection.
    """
    meta_path = str(meta_path)
    df = (pd.read_excel(meta_path, dtype=object) if meta_path.lower().endswith((".xlsx", ".xls"))
          else pd.read_csv(meta_path, dtype=object, keep_default_na=False))
    aliases = {"event_id": ["eventid"], "component": ["channel"], "timeStep": ["dt", "delta"],
               "trace_id": ["trace.id", "trace"], "starttime.s": ["record_start_time", "start_time"],
               "acrjfinal3": ["accepted", "acrjfinal"], "hypdis": ["hypocentral_distance_km"],
               "epidis": ["epicentral_distance_km"], "parr": ["p_arrival", "PStart"]}
    for col in list(df.columns):
        clean = str(col).removeprefix("meta.").removesuffix(".acfilename.")
        if clean not in df:
            df[clean] = df[col]
    for dest, names in aliases.items():
        if dest not in df:
            for name in names:
                if name in df:
                    df[dest] = df[name]
                    break
    for required in ("event_id", "station", "component", "acrjfinal3", "parr"):
        if required not in df:
            raise ValueError(f"Stage 1 metadata is missing {required!r}")
    df = df.loc[df.acrjfinal3.map(_bool)].copy()
    for name in ("trace_id", "network", "location", "starttime.s", "metadata_error"):
        if name not in df:
            df[name] = ""
    for name in ("magnitude", "hypdis", "epidis", "timeStep", "count"):
        if name not in df:
            df[name] = float("nan")
    df["filename"] = str(Path(h5_path).resolve())
    df["source_file"] = str(Path(h5_path).resolve())
    with h5py.File(h5_path, "r") as hf:
        for index, row in df.iterrows():
            try:
                event_id = str(row.event_id)
                if not str(row.component).strip():
                    raise ValueError('Missing channel/component required to determine frequency-selection orientation')
                if event_id not in hf:
                    raise ValueError(f"Event {event_id!r} is absent from HDF5")
                event = hf[event_id]
                key = _text(row.trace_id)
                if key not in event:
                    candidates = []
                    for candidate, group in event.items():
                        if not isinstance(group, h5py.Group):
                            continue
                        at = group.attrs
                        if (_text(at.get("station", "")) != str(row.station) or
                                _text(at.get("channel", "")) != str(row.component)):
                            continue
                        if str(row.network) and _text(at.get("network", "")) != str(row.network):
                            continue
                        if str(row.location) and _text(at.get("location", "")) != str(row.location):
                            continue
                        if str(row["starttime.s"]) and _time(at.get("start_time", "")) != _time(row["starttime.s"]):
                            continue
                        candidates.append(candidate)
                    if len(candidates) != 1:
                        raise ValueError(f"Trace identity is missing/ambiguous ({len(candidates)} matches); regenerate Stage 1 metadata")
                    key = candidates[0]
                trace = event[key]
                at = trace.attrs
                if _text(at.get("station", "")) != str(row.station) or _text(at.get("channel", "")) != str(row.component):
                    raise ValueError("Exact trace key conflicts with station/channel metadata")
                for field, attr in (("network", "network"), ("location", "location"), ("starttime.s", "start_time")):
                    df.at[index, field] = _text(at.get(attr, row[field]))
                df.at[index, "trace_id"] = key
                rate = _number(at.get("sampling_rate"))
                df.at[index, "timeStep"] = 1 / rate if rate > 0 else _number(row.timeStep)
                if "acceleration_waveform" not in trace:
                    raise ValueError("Response-corrected acceleration_waveform is absent")
                df.at[index, "count"] = len(trace["acceleration_waveform"])
                magnitude = _number(event.attrs.get("magnitude"))
                if math.isfinite(magnitude):
                    df.at[index, "magnitude"] = magnitude
                coords = [_number(at.get("latitude")), _number(at.get("longitude")),
                          _number(event.attrs.get("latitude")), _number(event.attrs.get("longitude"))]
                if all(map(math.isfinite, coords)):
                    repi = gps2dist_azimuth(*coords)[0] / 1000
                    df.at[index, "epidis"] = repi
                    depth = _number(event.attrs.get("depth"))
                    elevation = _number(at.get("elevation", at.get("elevation_m")))
                    if not math.isfinite(elevation) or abs(elevation) > 10000:
                        elevation = float("nan")
                    if math.isfinite(depth) and math.isfinite(elevation):
                        df.at[index, "hypdis"] = math.hypot(repi, depth + elevation / 1000)
                    else:
                        df.at[index, "hypdis"] = _number(at.get("hypocentral_distance_km"))
                else:
                    # The old hypdis column may actually contain epicentral distance.
                    # Accept only a distance explicitly stored under its physical name.
                    df.at[index, "epidis"] = _number(at.get("epicentral_distance_km"))
                    df.at[index, "hypdis"] = _number(at.get("hypocentral_distance_km"))
                for name in ("magnitude", "hypdis", "parr", "timeStep", "count"):
                    val = _number(df.at[index, name])
                    df.at[index, name] = val
                    if not math.isfinite(val):
                        raise ValueError(f"Missing/invalid {name}; provide event/station metadata before filtering")
            except Exception as exc:
                df.at[index, "metadata_error"] = str(exc)
    # Identity labels are administrative; physical station/network stay blank.
    # Recompute against the exact source so imported metadata cannot invent a
    # three-component association for missing labels.
    for column in ("identity_group", "identity_grouping_status", "identity_status", "missing_identity_fields"):
        df[column] = ""
    partial = (df.network.astype(str).str.strip().eq("") | df.station.astype(str).str.strip().eq("")) & df.metadata_error.eq("")
    if partial.any():
        identity_reader = _identity_reader()
        for event_id in df.loc[partial, "event_id"].astype(str).unique():
            identities = identity_reader.hdf5_identity_groups(h5_path, event_id)
            for index in df.index[partial & df.event_id.astype(str).eq(event_id)]:
                item = identities.get(str(df.at[index, "trace_id"]), {})
                for column in ("identity_group", "identity_grouping_status", "identity_status", "missing_identity_fields"):
                    df.at[index, column] = str(item.get(column, ""))
    df["trace.id"] = df.trace_id
    df["rec_id"] = [f"row_{i}" for i in range(len(df))]
    df["group_id"] = _group_records(df)
    df["acrjfinal"] = True
    valid = df.metadata_error.eq("")
    df = pd.concat([df.loc[valid].drop_duplicates(subset=["event_id", "trace_id"], keep="first"), df.loc[~valid]]).sort_index()
    target = Path(output_dir) / "normalized_stage1.csv"
    df.to_csv(target, index=False)
    return str(target)


def read_acceleration(h5_path, event_id, trace_id):
    with h5py.File(h5_path, "r") as hf:
        group = hf[str(event_id)][str(trace_id)]
        data = group["acceleration_waveform"]
        unit = _text(data.attrs.get("units", group.attrs.get("acceleration_units", "m/s^2"))).lower().replace(" ", "")
        factors = {"m/s^2": 1, "m/s2": 1, "m/sÃ‚Â²": 1, "m/s/s": 1,
                   "cm/s^2": .01, "cm/s2": .01, "cm/sÃ‚Â²": .01, "cm/s/s": .01,
                   "gal": .01, "g": 9.80665, "mg": .00980665}
        if unit not in factors:
            raise ValueError(f"Unrecognized acceleration units {unit!r}")
        return np.asarray(data, dtype=float) * factors[unit]


def run_signature(h5_path, metadata_path, config_json):
    signature = hashlib.sha256()
    for path in (Path(h5_path), Path(metadata_path)):
        stat = path.stat()
        signature.update(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode())
    config = json.loads(config_json)
    for key in ("resume", "workers", "output_dir"):
        config.pop(key, None)
    signature.update(json.dumps(config, sort_keys=True).encode())
    engine = Path(__file__).parent
    active = ("Main_AutoFC_v17_app.R", "stage2_io_v5.py", "stage2_parallel_v1.py", "corner_selection_v1_app.R", "autofc_v2_app.R", "autoparr.R", "Baseline.r", "DC.mean.r",
              "fbutter.r", "FAS.r", "Filter.r", "Integrate2VD.r", "Taper.r", "windows.r", "SMOOTH.r",
              "hk_nn_wrapper.R", "hk_nn.py", "autofc_option7_matlab_v2_app.R", "period_NGA.dat")
    dependencies = [engine / name for name in active] + sorted((engine / "Option 7").glob("*.m")) + sorted((engine / "RCTC" / "src").glob("*.cpp"))
    for path in dependencies:
        signature.update(path.read_bytes())
    signature.update((engine.parent / "acceptreject" / "hdf5_reader_v10_app.py").read_bytes())
    return signature.hexdigest()


def is_complete(path, event_id, trace_id, signature):
    if not Path(path).exists():
        return False
    try:
        with h5py.File(path, "r") as hf:
            gr = hf[str(event_id)][str(trace_id)]
            return (_text(gr.attrs.get("processing_signature", "")) == signature and
                    bool(gr.attrs.get("stage2_complete", False)) and
                    all(name in gr for name in ("acceleration_waveform", "velocity_waveform", "displacement_waveform")))
    except (OSError, KeyError):
        return False


def write_processed(source, destination, event_id, trace_id, acceleration_g, velocity_cms, displacement_cm,
                    dt, first_sample_index, metadata_json, signature):
    """Persist exact returned R arrays (unit conversion only), transaction per trace."""
    arrays = [np.asarray(v, dtype=float).reshape(-1) for v in (acceleration_g, velocity_cms, displacement_cm)]
    if len({len(a) for a in arrays}) != 1 or not all(np.isfinite(a).all() for a in arrays):
        raise ValueError("Filtered A/V/D arrays must have equal length and finite values")
    md = json.loads(metadata_json)
    event_id, trace_id = str(event_id), str(trace_id)
    # Complete engine diagnostics stay beside this new output, not in every HDF5
    # trace. Archive before writing; never alter any source HDF5 attribute.
    audit_id = hashlib.sha256((event_id + "\0" + trace_id).encode()).hexdigest()[:24]
    audit_dir = Path(destination).parent / "diagnostics" / "processing_metadata"
    audit_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(source, "r") as original:
        archived = {key: value.tolist() if isinstance(value, np.ndarray) else value.item() if isinstance(value, np.generic) else value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                    for key, value in original[event_id][trace_id].attrs.items() if key in INTERNAL_TRACE_ATTRIBUTES}
    audit = {"schema": "signalqc.processing_metadata.v3", "event_id": event_id,
             "trace_id": trace_id, "source_hdf5": str(Path(source).resolve()),
             "processing_signature": signature, "engine_metadata": md,
             "inherited_internal_attributes": archived}
    audit_path = audit_dir / (audit_id + ".json")
    if audit_path.exists():
        previous=json.loads(audit_path.read_text(encoding='utf-8'))
        if (str(previous.get('event_id')),str(previous.get('trace_id')))!=(event_id,trace_id):
            raise ValueError('Processing audit identifier collision; existing audit was retained')
    temporary = audit_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, audit_path)
    with h5py.File(source, "r") as src, h5py.File(destination, "a") as out:
        out.attrs["schema"] = "signalqc.processed.v1"
        out.attrs["source_hdf5"] = str(Path(source).resolve())
        event = out.require_group(event_id)
        for key, val in src[event_id].attrs.items():
            event.attrs[key] = val
        pending = "__pending_" + hashlib.sha256(trace_id.encode()).hexdigest()[:20]
        if pending in event:
            del event[pending]
        group = event.create_group(pending)
        for key, val in src[event_id][trace_id].attrs.items():
            if key not in INTERNAL_TRACE_ATTRIBUTES:
                group.attrs[key] = val
        group.attrs["original_start_time"] = group.attrs.get("start_time", "")
        for attr in ("start_time_ns", "end_time", "end_time_ns", "npts"):
            if attr in group.attrs:
                group.attrs["original_" + attr] = group.attrs[attr]
        group.attrs["trim_start_sample"] = int(first_sample_index)
        group.attrs["sampling_rate"] = 1 / float(dt)
        group.attrs["npts"] = len(arrays[0])
        start = _text(group.attrs.get("start_time", ""))
        try:
            group.attrs["start_time"] = _shift_time(start, int(first_sample_index) * float(dt))
            group.attrs["end_time"] = _shift_time(start, (int(first_sample_index) + len(arrays[0]) - 1) * float(dt))
            group.attrs["original_start_time_ns"] = _timestamp_ns(start)
            group.attrs["start_time_ns"] = _timestamp_ns(group.attrs["start_time"])
            group.attrs["end_time_ns"] = _timestamp_ns(group.attrs["end_time"])
        except ValueError:
            pass
        group.attrs["processing_engine"] = "Main_AutoFC_v17_app.R"
        group.attrs["processing_signature"] = signature
        group.attrs["original_trace_id"] = trace_id
        group.attrs["processing_audit_id"] = audit_id
        for field in ("fcHP", "fcLP", "windowStart", "windowEnd", "magnitude", "epidis", "hypdis", "PStart", "SStart", "SEnd", "CStart", "acrjfinal", "rejectreason3filter", "stage2_status"):
            if field in md and md[field] is not None:
                group.attrs[field] = md[field]
        for name, values, scale, unit, original_unit in zip(
                ("acceleration_waveform", "velocity_waveform", "displacement_waveform"), arrays,
                (9.80665, .01, .01), ("m/s^2", "m/s", "m"), ("g", "cm/s", "cm")):
            dataset = group.create_dataset(name, data=values * scale, compression="gzip", shuffle=True)
            dataset.attrs["units"] = unit
            dataset.attrs["engine_units"] = original_unit
        group.attrs["acceleration_units"] = "m/s^2"
        group.attrs["stage2_complete"] = True
        out.flush()
        if trace_id in event:
            del event[trace_id]
        event.move(pending, trace_id)
        out.flush()
    return str(destination)


def atomic_export(csv_path, xlsx_path):
    frame = pd.read_csv(csv_path, dtype={"location": str, "network": str, "station": str, "event_id": str, "trace_id": str}, keep_default_na=False)
    temporary = str(xlsx_path) + ".partial.xlsx"
    frame.to_excel(temporary, index=False)
    os.replace(temporary, xlsx_path)
    return str(xlsx_path)


def replace_checkpoint(temporary, target):
    os.replace(temporary, target)
    return str(target)


def record_method_corners(output_dir, source, event_id, trace_id, corners_json, errors, signature):
    """Retain estimates even if later aggregation/filtering fails on this trace."""
    path = Path(output_dir) / 'method_corners.csv'
    key = (str(event_id), str(trace_id))
    rows = []
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        rows = [row for row in old.to_dict('records')
                if (row['event_id'], row['trace_id']) != key]
    rows.append(dict(event_id=key[0], trace_id=key[1], source_file=str(Path(source).resolve()),
                     method_corners=str(corners_json), method_errors=str(errors),
                     processing_signature=str(signature)))
    temporary = path.with_suffix('.tmp')
    pd.DataFrame(rows).to_csv(temporary, index=False)
    os.replace(temporary, path)
    return str(path)
