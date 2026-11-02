#!/usr/bin/env python
"""
UAE NCM - Sanity Checks Script (v1, Dawood-aligned for Checks 3 & 4)
-------------------------------------------------------------------

Reads monthly filmeta Excel files produced by the auto-filter pipeline,
loads corresponding traces from monthly HDF5 files, and applies:

  Check 1 (code "1"): Extreme PGA
      - If any component in a 3C set has PGA > 2 g  -> fail

  Check 2 (code "2"): Suspected amplitude ratios
      - For each 3C set (same event + station), we further group components
        by instrument prefix (first 2 chars of the component name, e.g. HH* vs HN*),
        and within each instrument group we check:
          * if there are at least two horizontals:
                max(H) / min(H) > 2  -> fail
          * if there is at least one vertical and one horizontal:
                max(V) / min(H) > 3  -> fail   (NOTE: uses min(H), not mean(H))
        - If any instrument group fails, the whole 3C set is flagged as Check 2.

  Check 3 (code "3"): Dawood final displacement / velocity thresholds
      - For each component, use the trimmed [windowStart, windowEnd] record
        and the high-pass corner fcHP already selected by the auto-filter
        (we DO NOT change how fcHP is computed, only how it is applied here).
      - Processing (Dawood et al. Step V(a), adapted to trimmed records):
          * Zeroth-order baseline correction (approx):
                - remove mean of first 100 samples (or all if shorter)
                - remove mean of entire trimmed record
          * Apply a symmetric Tukey taper on BOTH ends:
                - 5% of record length at each end (≈ alpha = 0.10)
          * Zero padding before and after:
                Tpad = 1.5 * N / fcHP (seconds), with N = 4 (filter order)
          * Apply a 4th-order acausal Butterworth HIGH-PASS at fcHP
            (fcHP taken directly from filmeta, unchanged)
          * Integrate this processed trace to velocity and displacement:
                acc [m/s^2] -> cm/s^2 -> integrate -> vel [cm/s] -> disp [cm]
      - Thresholds:
            if M < 7.0:
                |D_final| < 0.005 cm AND |V_final| < 0.001 cm/s
            else:
                |D_final| < 0.025 cm AND |V_final| < 0.005 cm/s
        (fail if any of these are violated)

  Check 4 (code "4"): Dawood final / max displacement ratio
      - Using the same displacement time history D(t) as Check 3
      - Ratio |D_final| / max(|D(t)|) must be < 0.2
        (again, fcHP is taken directly from filmeta)

  Check 5 (code "5"): Multi-event (Bahrampouri et al. 2021)
      - Using trimmed, bandpass-filtered acceleration:
          * use fcHP and fcLP from filmeta (unchanged)
      - Compute Husid H(t), then:
            D5-75       : time between H=0.05 and H=0.75
            D5-75,Sorted: sum of 14 shortest 5%-energy intervals
        - If D5-75 - D5-75,Sorted > 10.3 s  -> fail

  Check 6 (code "6"): Amplitude clipping (ADC saturation)
      - Uses raw integer counts from dataset 'raw_waveform' in the HDF5 file
        (full record, not trimmed by windowStart/windowEnd).
      - For each row, the station code (e.g., "AJN") is used to look up the
        digitizer resolution ("bits") from:
            UAE seismic network-02_MT.xlsx
        If the bits cell is empty / NaN for a station, 24 bits are assumed.
      - For a digitizer with N bits, the full-scale range is ±(2^(N-1)).
        We define a saturation threshold as:
            sat = 0.8 * 2^(N-1)    (80% of full scale)
      - If max(|raw_counts|) / sat ≥ 1.0  -> Check 6 fail (code "6").

  Check 7 (code "7"): HH–HN spectral consistency (accelerometer vs seismometer)
      - For stations that have both seismometer channels (HH*) and strong-motion
        channels (HN*) for the same event (e.g., HHE/HNE, HHN/HNN, HHZ/HNZ),
        we compare their Fourier amplitude spectra.
      - For each matching orientation (E, N, Z):
          * read the same trimmed window [windowStart, windowEnd] for HH* and HN*
          * compute the Fourier amplitude spectra in acceleration [m/s²]
          * take the median amplitude ratio FAS(HH) / FAS(HN) over a
            mid-band where both instruments are reliable
              (roughly 0.3–20 Hz, further limited by fcHP/fcLP and Nyquist)
      - If the median ratio deviates too much from 1.0 (e.g., < 0.7 or > 1.3),
        we flag Check 7 as failed (code "7").
      - This is a consistency check: collocated instruments, after response
        correction to acceleration, should agree within ~±30% over their
        shared usable bandwidth.
check the pga and response spectra
Outputs:
  For each input file X.h5filmeta.xlsx, creates:
      X.h5filmeta_sc.xlsx
  containing all original columns plus:
      sc_extreme_pga_fail          # Check 1
      sc_suspect_amp_fail          # Check 2
      sc_final_disp_cm             # Check 3/4 info
      sc_final_vel_cms             # Check 3/4 info
      sc_disp_ratio_final_max      # Check 4 info
      sc_dawood_dispvel_fail       # Check 3
      sc_dawood_ratio_fail         # Check 4
      sc_D5_75                     # Check 5 info
      sc_D5_75_sorted              # Check 5 info
      sc_multi_event_fail          # Check 5
      sc_clip_fail                 # Check 6
      sc_clip_ratio                # Check 6 info
      sc_HH_HN_ratio_med           # Check 7 info (median FAS(HH)/FAS(HN))
      sc_HH_HN_ratio_fail          # Check 7
      flag                         # string like "0", "3", "25", "67", ...

  Additionally, for any record that fails Check 3 or Check 4, a diagnostic
  PNG is saved (acceleration, velocity, displacement) under:
      <folder_of_filmeta>/sc_plots/

Assumptions:
  - Accelerations in HDF5 are in m/s^2.
  - 'timeStep' column in filmeta is the sampling interval [s].
  - 'windowStart'/'windowEnd' [s] are relative to trace start (trimmed window).
  - 'fcHP' and 'fcLP' are in Hz (0 or NaN if not used); fcHP is taken from
    your autofilter results without any recomputation here.
  - 'pga' in filmeta is in g (from your auto-filter step).
  - Group key for 3C sets = (event_id, station, network).
  - Station ADC resolution ("bits") is read from
    'UAE seismic network-02_MT.xlsx'; if missing for a station, 24 bits
    are assumed.

"""

import os
import glob
import math

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Import your existing HDF5 helpers (must be reachable: same folder or PYTHONPATH)
from exact_reader import hdf5_acceleration, hdf5_raw_counts  # uses same interface as in R code

# =============== USER SETTINGS ===============

# Root directory that contains year/month subfolders with:
#   2008_09.h5        +  2008_09.h5filmeta.xlsx
BASE_DIR = r"D:\KU\PhD\PhD Project\NCM Data\Waveform Data\Additional miniseed data\Additional_HDF5_yearly"

# Find all monthly filmeta files produced by autofilter:
FILMETA_PATTERN = os.path.join(BASE_DIR, "**", "*.h5filmeta.xlsx")  # recursive

# Excel file that contains station ADC "bits" information
BITS_XLSX = r"D:\KU\PhD\PhD Project\Earthquake catalog and NCM station locations\UAE seismic network-02_MT.xlsx"

# Internal cache so we read the Excel only once
_STATION_BITS_CACHE = None

# =============== HELPER FUNCTIONS ===============

def has_valid_trace_id(x) -> bool:
    if x is None:
        return False
    if isinstance(x, float) and np.isnan(x):
        return False
    s = str(x).strip()
    if s == "" or s.lower() == "nan":
        return False
    return True

def bandpass_filter(acc, dt, f_hp, f_lp, order=4):
    """
    Zero-phase Butterworth band/high/low-pass for general QC (e.g. multi-event).
    Uses fcHP and fcLP exactly as provided (no recomputation).
    """
    fs = 1.0 / dt
    nyq = fs / 2.0

    has_hp = f_hp is not None and not np.isnan(f_hp) and f_hp > 0.0
    has_lp = f_lp is not None and not np.isnan(f_lp) and f_lp > 0.0 and f_lp < nyq

    if has_hp and has_lp:
        low = f_hp / nyq
        high = f_lp / nyq
        if low <= 0 or high >= 1 or low >= high:
            return acc
        b, a = butter(order, [low, high], btype="band")
    elif has_hp:
        low = f_hp / nyq
        if low <= 0 or low >= 1:
            return acc
        b, a = butter(order, low, btype="high")
    elif has_lp:
        high = f_lp / nyq
        if high <= 0 or high >= 1:
            return acc
        b, a = butter(order, high, btype="low")
    else:
        # No valid cutoffs -> no filtering
        return acc

    return filtfilt(b, a, acc)


def integrate_trapezoidal(x, dt):
    """
    Cumulative trapezoidal integration, starting from 0.
    """
    out = np.zeros_like(x, dtype=float)
    if len(x) <= 1:
        return out
    incr = 0.5 * (x[1:] + x[:-1]) * dt
    out[1:] = np.cumsum(incr)
    return out


def tukey_window(n, alpha=0.1):
    """
    Simple Tukey window.

    alpha ~ fraction of the window in the cosine tapers.
    For Dawood: 5% taper at each end -> alpha ≈ 0.10.
    """
    if n <= 0:
        return np.array([], dtype=float)
    if alpha <= 0:
        return np.ones(n, dtype=float)
    if alpha >= 1:
        # Pure Hann
        return 0.5 * (1 - np.cos(2 * np.pi * np.arange(n) / (n - 1)))

    w = np.ones(n, dtype=float)
    m = n - 1
    edge = int(math.floor(alpha * m / 2.0))

    # Rising cosine at the start
    if edge > 0:
        n1 = np.arange(0, edge + 1)
        w[:edge+1] = 0.5 * (1 + np.cos(np.pi * (2*n1/(alpha*m) - 1)))

    # Falling cosine at the end
    if edge > 0:
        n2 = np.arange(m - edge, m + 1)
        w[-(edge+1):] = 0.5 * (1 + np.cos(np.pi * (2*(m-n2)/(alpha*m) - 1)))

    return w


def dawood_process(acc_mps2, dt, f_hp, hp_order=4):
    """
    Full Dawood-style processing chain for a single component, using fcHP from
    filmeta (unchanged):

      - Baseline removal (zeroth-order)
      - Tukey taper 5% at each end
      - Zero-padding before & after: Tpad = 1.5*N/f_hp, N=hp_order
      - 4th-order acausal Butterworth high-pass at f_hp
      - Double integration to vel, disp (cm/s, cm)

    Returns:
        t               : time vector [s], with t=0 at the start of the original
                          trimmed window (padding extends to negative/positive t)
        acc_cm_s2       : processed acceleration [cm/s^2]
        vel_cms         : processed velocity [cm/s]
        disp_cm         : processed displacement [cm]
    """
    if f_hp is None or np.isnan(f_hp) or f_hp <= 0.0:
        return None, None, None, None

    acc = np.asarray(acc_mps2, dtype=float)
    n = len(acc)
    if n < 5:
        return None, None, None, None

    # ---- Baseline removal (Step I approximation) ----
    n0 = min(100, n)
    acc = acc - np.mean(acc[:n0])
    acc = acc - np.mean(acc)

    # ---- Tukey taper on both ends ----
    w = tukey_window(n, alpha=0.10)
    acc_tapered = acc * w

    # ---- Zero padding ----
    t_pad = 1.5 * float(hp_order) / float(f_hp)   # seconds
    pad_samples = int(round(t_pad / dt))
    if pad_samples < 1:
        pad_samples = 1

    pad = np.zeros(pad_samples, dtype=float)
    acc_padded = np.concatenate([pad, acc_tapered, pad])

    # ---- High-pass filter at fcHP ----
    fs = 1.0 / dt
    nyq = fs / 2.0
    low = f_hp / nyq

    if low <= 0 or low >= 1:
        acc_filt = acc_padded
    else:
        b, a = butter(hp_order, low, btype="high")
        acc_filt = filtfilt(b, a, acc_padded)

    # ---- Integrate to vel & disp (cm/s^2 -> cm/s -> cm) ----
    acc_cm_s2 = acc_filt * 100.0
    vel_cms = integrate_trapezoidal(acc_cm_s2, dt)
    disp_cm = integrate_trapezoidal(vel_cms, dt)

    # Time axis: 0 at start of original (un-padded) window
    t = (np.arange(len(acc_padded)) - pad_samples) * dt

    return t, acc_cm_s2, vel_cms, disp_cm


def dawood_disp_vel_metrics(acc_mps2, dt, f_hp, hp_order=4, return_series=False):
    """
    Wrapper to compute Dawood metrics and optionally return full series.

    Returns:
        if return_series == False:
            (d_final_cm, v_final_cms, d_max_cm)
        if return_series == True:
            (d_final_cm, v_final_cms, d_max_cm,
             t, acc_cm_s2, vel_cms, disp_cm)
    """
    t, acc_cm_s2, vel_cms, disp_cm = dawood_process(acc_mps2, dt, f_hp, hp_order)

    if t is None or acc_cm_s2 is None:
        if return_series:
            return np.nan, np.nan, np.nan, None, None, None, None
        else:
            return np.nan, np.nan, np.nan

    if len(disp_cm) == 0:
        if return_series:
            return np.nan, np.nan, np.nan, t, acc_cm_s2, vel_cms, disp_cm
        else:
            return np.nan, np.nan, np.nan

    d_final = float(disp_cm[-1])
    v_final = float(vel_cms[-1]) if len(vel_cms) > 0 else np.nan
    if np.any(np.isfinite(disp_cm)):
        d_max = float(np.nanmax(np.abs(disp_cm)))
    else:
        d_max = np.nan

    if return_series:
        return d_final, v_final, d_max, t, acc_cm_s2, vel_cms, disp_cm
    else:
        return d_final, v_final, d_max


def husid_curve(acc, dt):
    """
    Normalized cumulative energy H(t) from a(t)^2.
    (Constant factors for Arias cancel; we only need shape.)
    """
    e = np.cumsum(acc ** 2) * dt
    total = e[-1]
    if total <= 0:
        return np.zeros_like(e)
    return e / total


def D5_75(H, dt):
    t = np.arange(len(H)) * dt
    idx5 = np.where(H >= 0.05)[0]
    idx75 = np.where(H >= 0.75)[0]
    if len(idx5) == 0 or len(idx75) == 0:
        return np.nan
    return max(t[idx75[0]] - t[idx5[0]], 0.0)


def D5_75_sorted(H, dt):
    """
    Walling & Abrahamson (2017) / Bahrampouri et al. (2021):

    - Find times for H = 0.05, 0.10, ..., 1.00 (20 levels).
    - Build 20 intervals (each 5% of total energy).
    - Sort intervals; sum 14 shortest = 70% energy -> D5-75,Sorted.
    """
    t = np.arange(len(H)) * dt
    levels = np.linspace(0.05, 1.0, 20)
    times = []
    for lvl in levels:
        idx = np.where(H >= lvl)[0]
        if len(idx) == 0:
            return np.nan
        times.append(t[idx[0]])

    intervals = []
    prev_t = 0.0
    for ti in times:
        intervals.append(max(ti - prev_t, 0.0))
        prev_t = ti

    if len(intervals) != 20:
        return np.nan

    intervals = np.sort(np.array(intervals))
    return float(np.sum(intervals[:14]))

def husid_largest_gap_end_time(H, dt):
    """
    Returns the END time (seconds) of the largest 5%-energy interval
    in the Husid curve. This approximates when the next major energy
    burst starts after a lull.
    """
    t = np.arange(len(H)) * dt
    levels = np.linspace(0.05, 1.0, 20)

    times = []
    for lvl in levels:
        idx = np.where(H >= lvl)[0]
        if len(idx) == 0:
            return np.nan
        times.append(t[idx[0]])

    prev = 0.0
    intervals = []
    ends = []
    for ti in times:
        intervals.append(max(ti - prev, 0.0))
        ends.append(ti)
        prev = ti

    k = int(np.argmax(np.array(intervals)))
    return float(ends[k])


def multi_event_metrics(acc_g, dt, threshold_diff=10.3, p_rel=None, s_rel=None):
    """
    Returns:
        (flag_multi_event, D5_75, D5_75_sorted, suppressed_by_S)
    """
    if len(acc_g) < 10:
        return False, np.nan, np.nan, False

    H = husid_curve(acc_g, dt)
    d1 = D5_75(H, dt)
    d2 = D5_75_sorted(H, dt)

    if np.isnan(d1) or np.isnan(d2):
        return False, d1, d2, False

    flag = (d1 - d2) > threshold_diff
    suppressed_by_S = False

    # suppress false multi-event when the "second jump" aligns with S-wave timing
    if flag and (s_rel is not None) and np.isfinite(s_rel):
        t_gap_end = husid_largest_gap_end_time(H, dt)

        if np.isfinite(t_gap_end):
            tol = 2.0
            if (p_rel is not None) and np.isfinite(p_rel) and (s_rel > p_rel):
                tol = max(tol, 0.2 * (s_rel - p_rel))

            if abs(t_gap_end - s_rel) <= tol:
                suppressed_by_S = True
                flag = False

    return flag, d1, d2, suppressed_by_S

def read_acc_window_mps2(h5_path, row):
    """
    Use hdf5_reader_v7.hdf5_acceleration (same as in your R code)
    to get the full acceleration trace [m/s^2], then trim to the
    [windowStart, windowEnd] (seconds) from filmeta.
    """
    eventid = str(row["event_id"])
    station = str(row["station"])
    component = str(row["component"])
    trace_id = str(row["trace.id"])

    acc = hdf5_acceleration(h5_path, eventid, station, component, trace_id)
    acc = np.asarray(acc, dtype=float)

    dt = float(row["timeStep"])
    w_start = float(row["windowStart"])
    w_end = float(row["windowEnd"])

    n = len(acc)
    i0 = int(round(w_start / dt))
    i1 = int(round(w_end / dt))
    i0 = max(0, min(i0, n - 1))
    i1 = max(i0 + 1, min(i1, n))

    return acc[i0:i1], dt

def fas_median_ratio_for_pair(row_hh, row_hn, h5_path,
                              base_fmin=0.3, base_fmax=20.0):
    """
    Compute median FAS amplitude ratio FAS(HH) / FAS(HN) for a HH–HN pair
    (same event/station/orientation), using the trimmed window.

    Returns:
        r_med  (float)  median(FAS_HH / FAS_HN) over a mid-band
                        (or NaN if cannot be computed)
    """
    # Read trimmed windows for both traces
    acc_hh, dt1 = read_acc_window_mps2(h5_path, row_hh)
    acc_hn, dt2 = read_acc_window_mps2(h5_path, row_hn)

    if len(acc_hh) < 10 or len(acc_hn) < 10:
        return np.nan
    if not np.isclose(dt1, dt2):
        return np.nan

    n = min(len(acc_hh), len(acc_hn))
    acc_hh = np.asarray(acc_hh[:n], dtype=float)
    acc_hn = np.asarray(acc_hn[:n], dtype=float)

    # Remove means (same preprocessing for both)
    acc_hh -= acc_hh.mean()
    acc_hn -= acc_hn.mean()

    # FFT (zero-padded to next power of 2)
    nfft = int(2 ** math.ceil(math.log2(n)))
    f = np.fft.rfftfreq(nfft, d=dt1)
    F_hh = np.fft.rfft(acc_hh, nfft)
    F_hn = np.fft.rfft(acc_hn, nfft)
    A_hh = np.abs(F_hh)
    A_hn = np.abs(F_hn)

    # Frequency band: mid-band, limited by fcHP/fcLP and Nyquist
    nyq = 0.5 / dt1
    fmax_eff = min(base_fmax, 0.9 * nyq)

    # Respect any low-pass corners if available (use 80% of min LP)
    try:
        f_lp1 = float(row_hh.get("fcLP", np.nan))
        f_lp2 = float(row_hn.get("fcLP", np.nan))
        flps = [x for x in (f_lp1, f_lp2) if not np.isnan(x) and x > 0.0]
    except Exception:
        flps = []
    if flps:
        fmax_eff = min(fmax_eff, 0.8 * min(flps))

    # Respect any high-pass corners if available (start at 1.5 * min HP)
    try:
        f_hp1 = float(row_hh.get("fcHP", np.nan))
        f_hp2 = float(row_hn.get("fcHP", np.nan))
        fhps = [x for x in (f_hp1, f_hp2) if not np.isnan(x) and x > 0.0]
    except Exception:
        fhps = []
    if fhps:
        fmin_eff = max(base_fmin, 1.5 * min(fhps))
    else:
        fmin_eff = base_fmin

    band = (f >= fmin_eff) & (f <= fmax_eff) & (A_hh > 0) & (A_hn > 0)
    if not np.any(band):
        return np.nan

    ratio = A_hh[band] / A_hn[band]
    ratio = ratio[np.isfinite(ratio)]
    if ratio.size == 0:
        return np.nan

    return float(np.median(ratio))

def _load_station_bits(path=BITS_XLSX):
    """
    Read the station -> bits mapping from the Excel file.

    Looks for a column whose header contains 'station code' (case-insensitive)
    and a column whose header starts with 'bits'. Any station with missing
    bits is assumed to have 24 bits.
    """
    global _STATION_BITS_CACHE
    if _STATION_BITS_CACHE is not None:
        return _STATION_BITS_CACHE

    try:
        df_bits = pd.read_excel(path)
    except Exception as e:
        print(f"Warning: could not read bits Excel '{path}': {e}")
        _STATION_BITS_CACHE = {}
        return _STATION_BITS_CACHE

    station_col = None
    bits_col = None
    for c in df_bits.columns:
        name = str(c).strip().lower()
        if "station code" in name and station_col is None:
            station_col = c
        if name.startswith("bits") and bits_col is None:
            bits_col = c

    if station_col is None or bits_col is None:
        print("Warning: could not find 'Station Code' or 'bits' columns in bits Excel.")
        _STATION_BITS_CACHE = {}
        return _STATION_BITS_CACHE

    mapping = {}
    for _, row in df_bits.iterrows():
        st = str(row[station_col]).strip()
        if not st or st.lower() == "nan":
            continue
        val = row[bits_col]
        try:
            bits = int(val)
        except Exception:
            bits = 24  # default if cell is empty / non-numeric
        mapping[st.upper()] = bits

    _STATION_BITS_CACHE = mapping
    return _STATION_BITS_CACHE


def get_station_bits(station_code):
    """
    Return ADC bits for a station, defaulting to 24 bits if not found.
    """
    bits_map = _load_station_bits()
    return bits_map.get(str(station_code).upper(), 24)


def clipping_metrics(raw_counts, bits):
    """
    Given raw integer counts and ADC bits, compute:

        sat = 0.8 * 2^(bits-1)

    and return (clip_fail, ratio), where:

        ratio = max(|raw_counts|) / sat
        clip_fail = (ratio >= 1.0)
    """
    raw_counts = np.asarray(raw_counts)
    if raw_counts.size == 0:
        return False, np.nan

    sat = (2 ** (bits - 1)) * 0.8
    if sat <= 0:
        return False, np.nan

    max_abs = float(np.max(np.abs(raw_counts)))
    ratio = max_abs / sat
    clip_fail = ratio >= 1.0
    return clip_fail, ratio


def save_dawood_plot(t, acc_cm_s2, vel_cms, disp_cm,
                     out_path, title=None):
    """
    Save diagnostic plots (acc, vel, disp) for Dawood-processed series.
    """
    if t is None or acc_cm_s2 is None or vel_cms is None or disp_cm is None:
        return

    try:
        fig, axes = plt.subplots(3, 1, sharex=True, figsize=(8, 6))

        axes[0].plot(t, acc_cm_s2)
        axes[0].set_ylabel("Acc (cm/s²)")

        axes[1].plot(t, vel_cms)
        axes[1].set_ylabel("Vel (cm/s)")

        axes[2].plot(t, disp_cm)
        axes[2].set_ylabel("Disp (cm)")
        axes[2].set_xlabel("Time (s, Dawood-processed)")

        if title:
            fig.suptitle(title)

        fig.tight_layout()
        fig.savefig(out_path, dpi=150)
        plt.close(fig)
    except Exception as e:
        print(f"    Warning: could not save plot {out_path}: {e}")


# v3 app: numerical helper functions above are retained from v2. The following
# orchestration adds selectable checks and makes missing evaluations explicit.
def run(config, progress=None, cancelled=None):
    import json
    from pathlib import Path
    import re
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from signalqc.data import read_table
    from signalqc.project import atomic_json

    if cancelled is None:
        cancelled=lambda: bool(config.get('cancel_file')) and Path(config['cancel_file']).exists()
    enabled={str(x) for x in config.get('enabled_checks',['1','2','3','4','6','7'])}
    if not enabled.issubset(set('1234567')):
        raise ValueError('Sanity check identifiers must be 1 through 7.')
    source=Path(config['filtering_metadata'])
    outdir=Path(config['output_dir']); outdir.mkdir(parents=True,exist_ok=True)
    df=read_table(source).reset_index(drop=True)
    aliases={'trace.id':['trace_id','trace'], 'component':['channel'], 'magnitude':['mag','Magnitude'],
             'event_id':['event','eventid'], 'timeStep':['dt','time_step'],
             'fcHP':['fchp'], 'fcLP':['fclp']}
    explicit=config.get('sanity_column_mappings',{})
    for target,choices in aliases.items():
        if target in explicit and explicit[target] in df: df[target]=df[explicit[target]]
        elif target not in df:
            found=[x for x in choices if x in df]
            if len(found)==1: df[target]=df[found[0]]
    required=['event_id','station','component','trace.id','network']
    if enabled & set('12'): required+=['pga']
    if enabled & set('3457'): required+=['timeStep','windowStart','windowEnd','fcHP','fcLP']
    missing=[c for c in required if c not in df]
    if missing: raise ValueError('Filtering metadata is missing columns: '+', '.join(missing))
    if df.empty: raise ValueError('No filtering rows available for sanity checks.')
    for column in ('pga','timeStep','windowStart','windowEnd','fcHP','fcLP','magnitude','PStart','SStart'):
        if column in df: df[column]=pd.to_numeric(df[column],errors='coerce')
    names={'1':'sc_extreme_pga_fail','2':'sc_suspect_amp_fail','3':'sc_dawood_dispvel_fail',
           '4':'sc_dawood_ratio_fail','5':'sc_multi_event_fail','6':'sc_clip_fail','7':'sc_HH_HN_ratio_fail'}
    columns={}
    for k,name in names.items():
        columns[name]=pd.Series([pd.NA]*len(df),dtype='boolean')
        columns['sc_check_'+k+'_status']='not_evaluated' if k in enabled else 'disabled'
    metrics=['sc_final_disp_cm','sc_final_vel_cms','sc_disp_ratio_final_max','sc_D5_75',
             'sc_D5_75_sorted','sc_clip_ratio','sc_HH_HN_ratio_med','sc_adc_bits']
    for c in metrics: columns[c]=np.nan
    columns['sc_notes']=''
    df=df.drop(columns=[c for c in columns if c in df]).copy()
    df=pd.concat([df,pd.DataFrame(columns,index=df.index)],axis=1)
    issues=[]
    def note(i,message):
        df.at[i,'sc_notes']+=message+'; '
        issues.append({'row':int(i),'trace_id':str(df.at[i,'trace.id']),'message':message})
    def set_check(i,k,fail,status=None):
        if k not in enabled: return
        df.at[i,names[k]]=bool(fail)
        df.at[i,'sc_check_'+k+'_status']=status or ('fail' if fail else 'pass')
    bits_records=[]
    station_file=config.get('station_catalog','')
    bits_warning=''
    if station_file and '6' in enabled:
        try:
            p=Path(station_file)
            bitsdf=pd.read_excel(p,sheet_name=config.get('stations_sheet',0),keep_default_na=False) if p.suffix.lower() in ('.xlsx','.xls') else pd.read_csv(p,sep=config.get('stations_delimiter') or None,engine='python',keep_default_na=False)
            cols={str(c).lower().replace(' ','').replace('_',''):c for c in bitsdf}
            mapping=config.get('column_mappings',{}).get('stations',config.get('column_mappings',{}).get('station',{}))
            scol=mapping.get('station') or next((cols[k] for k in ['stationcode','station','sta'] if k in cols),None)
            bcol=mapping.get('bits') or next((cols[k] for k in ['bits','adcbits','resolution'] if k in cols),None)
            identity_cols={key:mapping.get(key) or next((cols[a] for a in aliases if a in cols),None)
                           for key,aliases in {'network':['network','networkcode','net'],'location':['location','locationcode','loc'],
                            'channel':['channel','channelcode','component'],'start_date':['startdate','validfrom','epochstart'],
                            'end_date':['enddate','validto','epochend']}.items()}
            if scol and bcol:
                for _,r in bitsdf.iterrows():
                    value=pd.to_numeric(r[bcol],errors='coerce')
                    if np.isfinite(value) and 1<=value<=64 and value==int(value):
                        record={'station':str(r[scol]).strip().upper(),'bits':int(value)}
                        for key,column in identity_cols.items():
                            if column and str(r[column]).strip(): record[key]=str(r[column]).strip()
                        bits_records.append(record)
                    elif str(r[bcol]).strip(): bits_warning='Invalid ADC resolution in table; affected records use the configured fallback'
            else: bits_warning='ADC table has no mapped station/bits columns; configured fallback used'
        except Exception as e: bits_warning='ADC table could not be read; configured fallback used: '+str(e)
    def h5_for(row):
        explicit_h5=config.get('input_hdf5','')
        if explicit_h5: return str(explicit_h5)
        file=str(row.get('filename',''))
        p=Path(file)
        if p.is_file(): return str(p)
        p=source.parent/file
        if p.is_file(): return str(p)
        raise FileNotFoundError(f'Cannot locate source HDF5 for {file}. Select it explicitly.')
    def bits_for(row):
        matches=[]
        for record in bits_records:
            if record['station']!=str(row['station']).upper(): continue
            if any(key in record and record[key].upper()!=str(row.get(field,'')).upper()
                   for key,field in [('network','network'),('location','location'),('channel','component')]): continue
            if 'start_date' in record or 'end_date' in record:
                time=pd.Timestamp(row.get('starttime.s',row.get('start_time','')))
                if pd.isna(time): raise ValueError('ADC epoch matching requires the recording start time')
                if time.tzinfo is None: time=time.tz_localize('UTC')
                for bound in ('start_date','end_date'):
                    if bound in record:
                        t=pd.Timestamp(record[bound]); t=t.tz_localize('UTC') if t.tzinfo is None else t
                        if (bound=='start_date' and time<t) or (bound=='end_date' and time>t): break
                else:
                    matches.append(record); continue
                continue
            matches.append(record)
        if not matches: return int(config.get('default_bits',24)),True
        specificity=max(len(x) for x in matches)
        values={x['bits'] for x in matches if len(x)==specificity}
        if len(values)!=1: raise ValueError('Conflicting ADC resolutions match this network/station/location/channel/epoch')
        return values.pop(),False
    for i,row in df.iterrows():
        if cancelled and cancelled():
            partial=outdir/(source.stem+'_sc_partial.csv'); df.to_csv(partial,index=False)
            return {'status':'cancelled','output_files':[str(partial)],'issues':issues}
        if progress: progress({'stage':'sanity','message':f'Sanity checks: trace {i+1} / {len(df)}','current':i+1,'total':len(df)})
        if not has_valid_trace_id(row['trace.id']):
            note(i,'Missing trace key'); continue
        if str(row.get('stage2_status','')) in ('error','pending','skipped'):
            note(i,'Filtering did not finish for this trace'); continue
        if str(row.get('method_errors','')).strip() not in ('','nan','None'):
            note(i,'Filtering reported incomplete method execution; inspect method_errors')
        try: h5=h5_for(row)
        except Exception as e: note(i,str(e)); continue
        if '6' in enabled:
            try:
                bits,assumed=bits_for(row)
                if not 1<=bits<=64: raise ValueError('ADC bits must be in 1..64')
                df.at[i,'sc_adc_bits']=bits
                if assumed:
                    df.at[i,'sc_notes']+=f'ADC resolution assumed {bits} bits; '
                    if bits_warning: note(i,bits_warning)
                counts=hdf5_raw_counts(h5,str(row['event_id']),str(row['station']),str(row['component']),str(row['trace.id']))
                if not len(counts) or not np.isfinite(counts).all(): raise ValueError('Raw counts empty or non-finite')
                fail,ratio=clipping_metrics(counts,bits)
                set_check(i,'6',fail); df.at[i,'sc_clip_ratio']=ratio
            except Exception as e: note(i,'Clipping check: '+str(e))
        if not enabled & set('345'): continue
        try:
            dt=float(row['timeStep']); hp=float(row['fcHP']); lp=float(row['fcLP'])
            if not np.isfinite(dt) or dt<=0: raise ValueError('Invalid sampling interval')
            if not (np.isfinite(row['windowStart']) and np.isfinite(row['windowEnd']) and row['windowEnd']>row['windowStart']>=0):
                raise ValueError('Invalid trimming window')
            acc,dt=read_acc_window_mps2(h5,row)
            if len(acc)<10 or not np.isfinite(acc).all(): raise ValueError('Window is too short or has non-finite samples')
        except Exception as e: note(i,'Waveform checks: '+str(e)); continue
        if enabled & set('34'):
            try:
                if not np.isfinite(hp) or not 0<hp<.5/dt: raise ValueError('Valid high-pass corner required')
                d,v,peak,t,a_series,v_series,d_series=dawood_disp_vel_metrics(acc,dt,hp,hp_order=4,return_series=True)
                if not np.isfinite([d,v,peak]).all(): raise ValueError('Non-finite Dawood metrics')
                df.at[i,'sc_final_disp_cm']=d; df.at[i,'sc_final_vel_cms']=v
                ratio=abs(d)/peak if peak>0 else np.nan
                df.at[i,'sc_disp_ratio_final_max']=ratio
                if '3' in enabled:
                    mag=float(row.get('magnitude',np.nan))
                    if not np.isfinite(mag) or mag==-999: note(i,'Check 3 needs actual event magnitude')
                    else:
                        dl,vl=(.005,.001) if mag<7 else (.025,.005)
                        set_check(i,'3',abs(d)>dl or abs(v)>vl)
                if '4' in enabled:
                    if np.isfinite(ratio): set_check(i,'4',ratio>.2)
                    else: note(i,'Check 4 cannot evaluate zero peak displacement')
                failed=any(df.at[i,'sc_check_'+k+'_status']=='fail' for k in ('3','4'))
                if config.get('save_figures',True) and failed:
                    plotdir=outdir/'sc_plots'; plotdir.mkdir(exist_ok=True)
                    safe=re.sub(r'[^A-Za-z0-9._-]','_',str(row['event_id'])+'_'+str(row['trace.id']))[:170]
                    save_dawood_plot(t,a_series,v_series,d_series,str(plotdir/(safe+'.png')),title=f'{row["station"]} {row["component"]} | fcHP {hp:g} Hz')
            except Exception as e: note(i,'Dawood checks: '+str(e))
        if '5' in enabled:
            try:
                if not 0<hp<lp<.5/dt: raise ValueError('Valid band-pass corners required')
                p=row.get('PStart',np.nan)-row['windowStart']; s=row.get('SStart',np.nan)-row['windowStart']
                duration=len(acc)*dt
                p=p if np.isfinite(p) and 0<=p<=duration else None
                s=s if np.isfinite(s) and 0<=s<=duration else None
                if p is not None and s is not None and s<=p: s=None
                fail,d1,d2,suppressed=multi_event_metrics(bandpass_filter(acc,dt,hp,lp)/9.80665,dt,p_rel=p,s_rel=s)
                if not np.isfinite([d1,d2]).all(): raise ValueError('Insufficient energy for valid Husid durations')
                df.at[i,'sc_D5_75']=d1; df.at[i,'sc_D5_75_sorted']=d2
                set_check(i,'5',fail)
            except Exception as e: note(i,'Multi-event check: '+str(e))
    base_keys=['event_id','network','station']+(['location'] if 'location' in df else [])
    # Distinct recordings at the same station must not enter one component set.
    # Match starts within one sample, retaining HH/HN pairs across instruments.
    df['__sc_recording']=''
    for _,station_group in df.groupby(base_keys,dropna=False):
        timed=[]; unknown=[]
        for i,row in station_group.iterrows():
            start=row.get('starttime.s',row.get('record_start_time',row.get('start_time','')))
            try:
                if not str(start).strip():
                    import h5py
                    with h5py.File(h5_for(row),'r') as f: start=f[str(row['event_id'])][str(row['trace.id'])].attrs.get('start_time','')
                if isinstance(start,bytes): start=start.decode('utf-8')
                timestamp=pd.Timestamp(start)
                if pd.isna(timestamp): raise ValueError('No start time')
                step=float(row.get('timeStep',.01))
                timed.append((timestamp.value,i,step if np.isfinite(step) and step>0 else .01))
            except Exception: unknown.append(i)
        anchor=None; group_no=-1; step0=.01
        for timestamp,i,step in sorted(timed):
            if anchor is None or timestamp-anchor>max(step,step0)*1e9+1:
                group_no+=1; anchor=timestamp; step0=step
            df.at[i,'__sc_recording']=str(group_no)
        ambiguous=bool(unknown and station_group.loc[unknown,'component'].duplicated().any())
        for i in unknown:
            df.at[i,'__sc_recording']='unknown_'+str(i) if ambiguous else 'unknown'
            if ambiguous: note(i,'Repeated components without recording start times cannot be grouped safely')
    keys=base_keys+['__sc_recording']
    for _,group in df.groupby(keys,dropna=False):
        valid=group[group['trace.id'].apply(has_valid_trace_id)]
        if 'stage2_status' in valid:
            valid=valid[~valid['stage2_status'].isin(['error','pending','skipped'])]
        if not len(valid): continue
        if enabled & set('12'):
            pgas=pd.to_numeric(valid['pga'],errors='coerce').abs()
            usable=pgas.notna() & (pgas<999)
            if usable.all():
                if '1' in enabled:
                    for i in valid.index: set_check(i,'1',bool((pgas>2).any()))
                if '2' in enabled:
                    any_pairs=False; suspected=False
                    for _,instrument in valid.groupby(valid['component'].astype(str).str[:2]):
                        vertical=instrument['component'].astype(str).str.upper().str.contains('Z')
                        H=pd.to_numeric(instrument.loc[~vertical,'pga']).abs().to_numpy()
                        V=pd.to_numeric(instrument.loc[vertical,'pga']).abs().to_numpy()
                        if len(H)>=2:
                            any_pairs=True; suspected |= H.max()/max(H.min(),1e-12)>2
                        if len(H) and len(V):
                            any_pairs=True; suspected |= V.max()/max(H.min(),1e-12)>3
                    for i in valid.index:
                        if any_pairs: set_check(i,'2',suspected)
                        else: df.at[i,'sc_check_2_status']='not_applicable'
            else:
                for i in valid.index: note(i,'PGA missing/invalid in component group')
        if '7' in enabled:
            for i in valid.index: df.at[i,'sc_check_7_status']='not_applicable'
            for orientation in 'ENZ':
                hh=valid[valid['component']=='HH'+orientation]; hn=valid[valid['component']=='HN'+orientation]
                if not len(hh) or not len(hn): continue
                if len(hh)!=1 or len(hn)!=1:
                    for i in list(hh.index)+list(hn.index):
                        df.at[i,'sc_check_7_status']='not_evaluated'; note(i,'Ambiguous HH/HN pair')
                    continue
                indices=[hh.index[0],hn.index[0]]
                try:
                    if h5_for(hh.iloc[0])!=h5_for(hn.iloc[0]): raise ValueError('HH/HN pair spans different HDF5 files')
                    ratio=fas_median_ratio_for_pair(hh.iloc[0],hn.iloc[0],h5_for(hh.iloc[0]))
                    if not np.isfinite(ratio): raise ValueError('No common usable HH/HN spectral band')
                    for i in indices:
                        df.at[i,'sc_HH_HN_ratio_med']=ratio; set_check(i,'7',ratio<.7 or ratio>1.3)
                except Exception as e:
                    for i in indices:
                        df.at[i,'sc_check_7_status']='not_evaluated'; note(i,'HH/HN check: '+str(e))
    flags=[]; overall=[]
    for _,row in df.iterrows():
        failures=''.join(k for k in sorted(enabled) if row['sc_check_'+k+'_status']=='fail')
        incomplete=any(row['sc_check_'+k+'_status']=='not_evaluated' for k in enabled)
        applicable=any(row['sc_check_'+k+'_status'] in ('pass','fail') for k in enabled)
        flags.append(failures or ('-999' if incomplete or not applicable else '0'))
        overall.append('fail' if failures else 'incomplete' if incomplete else 'pass' if applicable else 'not_applicable')
    df['flag']=flags; df['sc_status']=overall
    combined=[]
    for _,row in df.iterrows():
        upstream=str(row.get('stage2_status',''))
        if upstream=='rejected' or row.sc_status=='fail': combined.append('rejected')
        elif upstream in ('error','pending','skipped') or row.sc_status=='incomplete' or str(row.get('method_errors','')).strip() not in ('','nan','None'): combined.append('incomplete')
        elif row.sc_status=='pass': combined.append('accepted')
        else: combined.append('QC not applied')
    df['pipeline_outcome']=combined
    df.drop(columns=['__sc_recording'],inplace=True)
    csv=outdir/(source.stem+'_sc.csv'); xlsx=outdir/(source.stem+'_sc.xlsx')
    df.to_csv(csv,index=False); df.to_excel(xlsx,index=False)
    result={'status':'completed_with_issues' if issues else 'completed', 'output_files':[str(csv),str(xlsx)],
            'counts':df.sc_status.value_counts().to_dict(),'enabled_checks':sorted(enabled),'issues':issues}
    atomic_json(outdir/'sanity_summary.json',result)
    return result


if __name__=='__main__':
    import json,sys
    with open(sys.argv[1],encoding='utf-8-sig') as config_file: cfg=json.load(config_file)
    def emit(event): print('SIGNALQC_EVENT '+json.dumps(event,default=str),flush=True)
    result=run(cfg,progress=emit)
    print(json.dumps(result,default=str),flush=True)
    if result['status']=='cancelled': raise SystemExit(130)


