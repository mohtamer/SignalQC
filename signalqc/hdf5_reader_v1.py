"""Read-only HDF5 browsing and bounded waveform previews; no processing engines."""
from __future__ import annotations

import math
import re
from pathlib import PurePosixPath

import h5py
import numpy as np

PAGE_SIZE = 250
DISPLAY_BINS = 50000
TIME_KEYS = {"dt", "delta", "timestep", "sampleinterval", "samplinginterval",
             "dts", "deltaseconds", "sampleintervalseconds", "samplingrate",
             "samplerate", "samplingfrequency", "fs", "sps", "samplingratehz",
             "sampleratehz", "samplingfrequencyhz", "fshz"}
KINDS = {
    "raw": {"raw", "rawwaveform", "counts", "rawcounts"},
    "acceleration": {"accelerationwaveform", "acceleration", "acc", "accel"},
    "velocity": {"velocitywaveform", "velocity", "vel"},
    "displacement": {"displacementwaveform", "displacement", "disp"},
}


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def readable(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        return np.array2string(value, threshold=12, edgeitems=3)
    return str(value)


def waveform_kind(name):
    key = normalized(PurePosixPath(name).name)
    return next((kind for kind, aliases in KINDS.items() if key in aliases), "waveform")


def numeric_waveform(ds):
    return (isinstance(ds, h5py.Dataset) and ds.ndim in (1, 2)
            and ds.dtype.kind in "iuf" and ds.size > 1)


def describe(parent, name):
    path = parent.name.rstrip("/") + "/" + name
    link = parent.get(name, getlink=True)
    if isinstance(link, (h5py.ExternalLink, h5py.SoftLink)):
        return dict(path=path, name=name, kind="link", detail="Link (not followed automatically)")
    try:
        obj = parent[name]
        if isinstance(obj, h5py.Group):
            return dict(path=path, name=name, kind="group", detail=f"{len(obj):,} items")
        return dict(path=path, name=name, kind="dataset", shape=obj.shape,
                    detail=f"{obj.shape} · {obj.dtype}", numeric=numeric_waveform(obj))
    except Exception as exc:
        return dict(path=path, name=name, kind="unavailable", detail=str(exc))


def metadata(obj):
    """Closest attributes override ancestors, but every source stays visible."""
    lineage = []
    current = obj
    while True:
        lineage.append(current)
        if current.name == "/":
            break
        current = current.parent
    attrs = {}
    sections = []
    for current in reversed(lineage):
        entries = []
        local_attrs = {}
        for key in current.attrs:
            try:
                value = current.attrs[key]
                local_attrs[normalized(key)] = value
                entries.append(f"{key}: {readable(value)[:1500]}")
            except Exception as exc:
                entries.append(f"{key}: unavailable ({exc})")
        # Scalar metadata alongside the waveform is common in external layouts.
        if isinstance(current, h5py.Group):
            for key in current.keys():
                if normalized(key) not in TIME_KEYS:
                    continue
                if not isinstance(current.get(key, getlink=True), h5py.HardLink):
                    continue
                ds = current[key]
                if isinstance(ds, h5py.Dataset) and ds.size == 1:
                    value = np.asarray(ds[()]).reshape(-1)[0]
                    local_attrs.setdefault(normalized(key), value)
                    entries.append(f"{key} [scalar dataset]: {readable(value)}")
        # A record's sampling_rate must override a different dt on the file.
        # Invalid local timing remains unknown rather than silently inheriting
        # another waveform's timing from a more distant ancestor.
        if TIME_KEYS.intersection(local_attrs):
            for key in TIME_KEYS:
                attrs.pop(key, None)
        attrs.update(local_attrs)
        if entries:
            sections.append(current.name + "\n" + "\n".join(entries))
    return attrs, "\n\n".join(reversed(sections)) or "No stored attributes."


def positive_number(value):
    try:
        result = float(np.asarray(value).reshape(-1)[0])
    except (ValueError, TypeError, IndexError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def time_step(attrs, override_hz=0):
    if override_hz > 0:
        return 1.0 / override_hz, "Manual display sampling rate"
    # Only explicit names are used. 'samp' could mean the number of samples.
    for key in ("dt", "delta", "timestep", "sampleinterval", "samplinginterval",
                "dts", "deltaseconds", "sampleintervalseconds"):
        value = positive_number(attrs.get(key))
        if value is not None:
            return value, f"Stored {key}"
    for key in ("samplingrate", "samplerate", "samplingfrequency", "fs", "sps",
                "samplingratehz", "sampleratehz", "samplingfrequencyhz", "fshz"):
        value = positive_number(attrs.get(key))
        if value is not None:
            return 1.0 / value, f"Stored {key}"
    return None, "Timing unavailable; sample index shown"


def display_series(ds, sample_axis=0, component=0):
    """Keep original extrema for large arrays; read at most ~1M samples at once."""
    axis = sample_axis if ds.ndim == 2 else 0
    if ds.ndim == 2 and not 0 <= component < ds.shape[1 - axis]:
        raise ValueError(f"Component {component + 1} does not exist; choose a smaller component number.")
    n = ds.shape[axis]
    if not n:
        raise ValueError("This dataset is empty.")

    def read(start, stop):
        selector = slice(start, stop) if ds.ndim == 1 else (
            (slice(start, stop), component) if axis == 0 else (component, slice(start, stop)))
        return np.asarray(ds[selector], dtype=np.float64)

    if n <= DISPLAY_BINS * 2:
        return np.arange(n, dtype=np.float64), read(0, n), n, False
    bin_size = math.ceil(n / DISPLAY_BINS)
    bins_per_chunk = max(1, 1000000 // bin_size)
    indices, values = [], []
    for start in range(0, n, bin_size * bins_per_chunk):
        y = read(start, min(n, start + bin_size * bins_per_chunk))
        for offset in range(0, len(y), bin_size):
            block = y[offset:offset + bin_size]
            valid = np.isfinite(block)
            if not valid.any():
                indices.append(start + offset); values.append(float("nan")); continue
            finite_indices = np.flatnonzero(valid)
            subset = block[valid]
            lo = int(finite_indices[np.argmin(subset)])
            hi = int(finite_indices[np.argmax(subset)])
            for index in sorted({lo, hi}):
                indices.append(start + offset + index); values.append(float(block[index]))
            if not valid.all():
                indices.append(start + offset + len(block) - 1); values.append(float("nan"))
    return np.asarray(indices, dtype=float), np.asarray(values), n, True


def read_preview(ds, sample_axis=0, component=0, override_hz=0):
    attrs, _ = metadata(ds)
    dt, timing = time_step(attrs, override_hz)
    indices, values, n, reduced = display_series(ds, sample_axis, component)
    kind = waveform_kind(ds.name)
    # Do not label raw counts or unrelated arrays as calibrated acceleration.
    own_units = next((readable(ds.attrs[key]) for key in ds.attrs
                      if normalized(key) in ("units", "unit", "physicalunits")), None)
    units = own_units or readable(attrs.get(kind + "units", "unknown"))
    if kind == "waveform" and not own_units:
        units = readable(attrs.get("units", "unknown"))
    start = next((readable(attrs[key]) for key in ("starttime", "recordstarttime", "starttimes")
                  if key in attrs), "Unknown")
    return dict(path=ds.name, kind=kind, x=indices * dt if dt else indices, y=values,
                samples=n, reduced=reduced, dt=dt, timing=timing, units=units, start=start,
                component=component + 1 if ds.ndim == 2 else None)


def inspect_node(filename, path="/", offset=0, plot=True, sample_axis=0,
                 component=0, override_hz=0):
    # Short-lived, read-only handle: never use r+, disable HDF5 locks, or run a stage.
    with h5py.File(filename, "r") as source:
        obj = source[path]
        _, details = metadata(obj)
        result = dict(path=path, children=[], more=None, offset=offset,
                      details=details, series=[], notes=[], shape=None, has_matrix=False)
        if isinstance(obj, h5py.Group):
            names = list(obj.keys())
            result["children"] = [describe(obj, name) for name in names[offset:offset + PAGE_SIZE]]
            if offset + PAGE_SIZE < len(names):
                result["more"] = offset + PAGE_SIZE
            result["summary"] = f"Group · {len(names):,} items"
            candidates = []
            if plot and offset == 0:
                # Select by known waveform names, not by assuming all arrays are signals.
                for kind in ("raw", "acceleration", "velocity", "displacement"):
                    for name in names:
                        if waveform_kind(name) != kind:
                            continue
                        if not isinstance(obj.get(name, getlink=True), h5py.HardLink):
                            continue
                        ds = obj[name]
                        if numeric_waveform(ds):
                            candidates.append(ds); break
        else:
            result["summary"] = f"Dataset · shape {obj.shape} · {obj.dtype}"
            result["shape"] = obj.shape
            candidates = [obj] if plot and numeric_waveform(obj) else []
            if not candidates:
                if obj.shape is not None and obj.size <= 16:
                    result["details"] = "Stored value: " + readable(obj[()]) + "\n\n" + details
                result["notes"].append("Select a one-dimensional numeric waveform or a two-dimensional component matrix to plot it.")
        result["has_matrix"] = any(ds.ndim == 2 for ds in candidates)
        for ds in candidates:
            try:
                result["series"].append(read_preview(ds, sample_axis, component, override_hz))
            except Exception as exc:
                result["notes"].append(f"{ds.name}: {exc}")
        return result
