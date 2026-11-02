"""Production translation of the active MATLAB Option7 v2 path.

Only the branches exercised by that driver are supported. This deliberately
retains historical one-based slices, smoothing compaction, FFT-grid failures,
threshold indexing and clamps. No scientific bug repairs belong in this port.
Final corners matched the retained MATLAB fixtures; intermediate floating-point
values need not be bitwise identical. Approved for integration on 2026-10-05.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.integrate import cumulative_trapezoid
from scipy.signal import find_peaks


def matlab_round(value, digits=0):
    factor = 10.0**digits
    return np.copysign(np.floor(np.abs(value) * factor + 0.5), value) / factor


def matlab_slice(values, start, end):
    """Inclusive, one-based slice, rejecting MATLAB-invalid array indices."""
    if start > end:
        return np.asarray(values)[:0]
    if start < 1 or end > len(values):
        raise IndexError(f"MATLAB index range {start}:{end} outside 1:{len(values)}")
    return np.asarray(values)[int(start)-1:int(end)]


def dc_mean(acc):
    x = np.asarray(acc, dtype=np.float64).reshape(-1)
    return x - np.mean(x)


def taper(dt, acc, tbegin, tend):
    x = np.asarray(acc).reshape(-1)
    nb, ne = 1, 1
    begin, end = np.ones(1), np.ones(1)
    if tbegin != 0:
        nb = math.floor(tbegin / dt)
        i = np.arange(1, nb + 1)
        begin = 0.5 * (1 + np.cos(np.pi * (nb+i-1) / nb))
    if tend != 0:
        ne = math.floor(tend / dt)
        i = np.arange(1, ne + 1)
        end = 0.5 * (1 + np.cos(np.pi * (i-1) / ne))
    return np.concatenate((matlab_slice(x, 1, nb) * begin,
                           matlab_slice(x, nb+1, len(x)-ne),
                           matlab_slice(x, len(x)-ne+1, len(x)) * end))


def zc_taper(acc, dt):
    x = np.asarray(acc).reshape(-1).copy()
    if len(x) < 2:
        raise ValueError("Reference ZC_TAPER requires at least two samples")
    tb, te = 0., 0.
    for ii in range(2, len(x)+1):
        if x[ii-1] * x[ii-2] < 0 and ii >= 5:
            tb = ii*dt
            break
    if ii == len(x):
        tb, x[0] = 0., 0.
    for ii in range(len(x), 1, -1):
        if x[ii-1] * x[ii-2] < 0 and ii <= len(x)-5:
            te = (len(x)-ii)*dt
            break
    if ii == 2:
        te, x[-1] = 0., 0.
    return taper(dt, x, tb, te)


def pad(acc):
    x = np.asarray(acc).reshape(-1) / 980
    n = len(x)
    total = 2 ** (math.ceil(math.log2(n)) + 1)
    begin = math.ceil((total-n)/2)
    end = total-begin-n
    return np.concatenate((np.zeros(begin), x, np.zeros(end))), begin, end


def smuth2(spectrum, npts, fmax, fl1, dfl, df):
    avg, place = [], []
    fl, fbu = fl1-dfl, 10.**fl1
    while fbu < fmax:
        fl += dfl
        fbl = 10.**(fl-0.5*dfl)
        fbu = 10.**(fl+0.5*dfl)
        inl = math.floor(fbl/df)+2
        if (inl-2)*df == fbl:
            inl -= 1
        inl = max(inl, 2)
        inu = min(math.floor(fbu/df)+1, npts)
        value = np.float64(0.)
        for jc in range(inl, inu+1):
            value = np.log10(spectrum[jc-1]) + value
        with np.errstate(divide="ignore", invalid="ignore"):
            value = np.divide(value, inu-inl+1)
        avg.append(value)
        place.append((inu-inl)/2+inl)
    # Preserve the reference's in-place compaction without shortening its tail.
    avg, place = np.asarray(avg), np.asarray(place)
    k = 0
    for ic in range(len(avg)):
        if np.isfinite(avg[ic]):
            avg[k] = 10.**avg[ic]
            place[k] = place[ic]
            k += 1
    return avg, place


def fas_smooth(dt, acc):
    padded, _, _ = pad(acc)
    transformed = np.fft.fft(padded) * dt
    df = 1 / (len(padded)*dt)
    n = len(padded)//2+1
    freq = np.arange(n)*df
    raw = np.abs(transformed[:n])
    avg, place = smuth2(raw, n, freq[-1], math.log10(max(freq[0], 0.001)), 0.05, df)
    peaks = find_peaks(place)[0]
    if not len(peaks):
        peaks = np.array([len(place)-1 if place[-1] > place[0] else 0])
    if len(peaks) != 1:
        raise ValueError("Reference FAS_Smooth produces a non-scalar peak index")
    b = int(peaks[0]) + 1
    indices = np.ceil(place[:b]).astype(int)-1
    if place[b-1] > len(freq):
        smoothfreq = np.r_[freq[indices[:-1]], freq[-1]]
        smooth = np.r_[avg[:b-1], raw[-1]]
    else:
        smoothfreq, smooth = freq[indices], avg[:b]
    return freq, raw, smoothfreq, smooth


def fbutter(nnyq, df, fc, n, hp, ac):
    f = df*np.arange(nnyq)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if ac == "A":
            power = (f/fc)**(2*n)
            return np.sqrt(power/(1.0+power)) if hp == "h" else np.sqrt(1.0/(1.0+power))
        if ac != "C":
            raise ValueError("Only the reference A and C filter branches are supported")
        # Construct complex components directly: 0*Inf must not corrupt real 0.
        fn = np.zeros(nnyq, dtype=np.complex128)
        fn.imag = -np.abs(fc/f) if hp == "h" else np.abs(f/fc)
        tf = np.ones(nnyq, dtype=np.complex128)
        for j in range(1, n+1):
            tf = tf * (fn-np.exp(complex(0, np.pi/(2*n)*(2*j-1+n))))
        tf = 1./tf
        # MATLAB complex arithmetic gives the analytic zero at the HP DC bin;
        # NumPy's repeated Inf multiplication gives NaN. This is compatibility,
        # not a different filter. The reference harness checks this explicitly.
        if hp == "h":
            tf[0] = complex(0, 0)
        return tf


def filter_avd(dt, acc, hpfc, lpfc, kind="A", base_tf="F"):
    if base_tf == "T":
        raise NotImplementedError("BslnAdj is absent from the active Option7 helpers")
    padded, begin, end = pad(acc)
    total = len(padded)
    fft = np.fft.fft(padded)*dt
    n = total//2+1
    af = fft[:n].copy()
    if not np.isnan(hpfc) and hpfc > 0:
        af *= fbutter(n, 1/(total*dt), hpfc, 5, "h", kind)
    if not np.isnan(lpfc) and lpfc > 0:
        af *= fbutter(n, 1/(total*dt), lpfc, 4, "l", kind)
    af[0], af[-1] = complex(af[0].real, 0), complex(af[-1].real, 0)
    complete = np.r_[af, np.conj(af[1:-1][::-1])]
    # The constructed spectrum is conjugate-symmetric; MATLAB returns real.
    acceleration = np.fft.ifft(complete/dt).real * 980
    acceleration[0] = 0
    velocity = cumulative_trapezoid(acceleration, initial=0)*dt
    displacement = cumulative_trapezoid(velocity, initial=0)*dt
    # Keep the reference's one-sample-before-data start (begin, not begin+1).
    stop = total-end-1
    return (matlab_slice(acceleration, begin, stop),
            matlab_slice(velocity, begin, stop),
            matlab_slice(displacement, begin, stop),
            acceleration, velocity, displacement, end)


def geometric_mean(x, axis):
    if np.any(np.asarray(x) < 0):
        raise ValueError("Geometric mean requires nonnegative values")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.exp(np.mean(np.log(x), axis=axis))


def calculate_snr(acc, dt, ntime, stime):
    x = np.asarray(acc).reshape(-1)
    swst, swet = math.floor(stime[0]/dt), math.floor(stime[1]/dt)
    nwst, nwet = math.floor(ntime[0]/dt)+1, math.floor(ntime[1]/dt)
    sdul, ndul = swet-swst, nwet-nwst
    def spectrum(data):
        return fas_smooth(dt, zc_taper(data, dt))
    if sdul <= ndul:
        sf = spectrum(matlab_slice(x, swst, swet))
        nf = spectrum(matlab_slice(x, nwst, nwst+sdul))
        return np.column_stack((nf[2], sf[3]/nf[3]))
    kk1, kk2 = int(np.fix(sdul/ndul)), sdul % ndul
    nf = spectrum(matlab_slice(x, nwst, nwet))
    segments = [spectrum(matlab_slice(x, swst+(ss-1)*ndul+1, swst+ss*ndul))
                for ss in range(1, kk1+1)]
    # Calculate raw ratios too: their dimensional failure occurs in MATLAB
    # even though the driver later consumes only the smoothed SNR.
    _raw_ratio = geometric_mean(np.column_stack([s[1] for s in segments]), 1) / nf[1]
    ratio1 = geometric_mean(np.column_stack([s[3] for s in segments]), 1) / nf[3]
    noiserem = np.r_[zc_taper(matlab_slice(x, nwst, nwst+kk2), dt), np.zeros(ndul-kk2)]
    signalrem = np.r_[zc_taper(matlab_slice(x, swet-kk2, swet), dt), np.zeros(ndul-kk2)]
    nrf, srf = fas_smooth(dt, noiserem), fas_smooth(dt, signalrem)
    _raw_ratio2 = srf[1]/nrf[1]
    ratio2 = srf[3]/nrf[3]
    return np.column_stack((srf[2], geometric_mean(np.column_stack((ratio1, ratio2)), 1)))


def polyfit1(x, y):
    # MATLAB polyfit uses an unscaled Vandermonde matrix and QR. np.polyfit
    # rescales columns, so use QR explicitly to preserve the algorithm.
    v = np.column_stack((np.asarray(x), np.ones(len(x))))
    q, r = np.linalg.qr(v, mode="reduced")
    return np.linalg.solve(r, q.T @ np.asarray(y))


def choose_lp(acc, dt, pt, st, se):
    snr = calculate_snr(zc_taper(dc_mean(acc), dt), dt, (0, .95*pt), (st, se))
    maximum = 70 if dt <= .005 else 40 if dt == .01 else 20 if dt == .05 else 45
    lp = float(maximum)
    for i in range(1, len(snr)):
        if snr[i, 0] > 10 and snr[i, 0] < maximum and snr[i-1, 1] >= 3 and snr[i, 1] <= 3:
            coef = polyfit1(snr[i-1:i+1, 0], snr[i-1:i+1, 1]-3)
            lp = float(matlab_round(-coef[1]/coef[0]))
            break
    return lp


def choose_hp(acc, dt, mag, pt, direction, lp, return_trials=False):
    yz1 = np.array([.0851,.394,.350,.256,.0000719,.000812,.411,.0226,.0230,.00692])
    yz2 = np.array([.0517,.284,.444,.130,.0000521,.000673,.736,.0259,.0290,.00524])
    x = zc_taper(dc_mean(acc), dt)
    _, _, freq, fas = fas_smooth(dt, x)
    n = len(acc)
    trials = []
    selected = -999.
    # MATLAB retains polypoint from the previous iteration if the search does
    # not enter its assignment branch. Deliberately preserve that behavior.
    points = None
    for hp in freq[freq <= 10]:
        a = filter_avd(dt, x, hp, lp, "A")
        c = filter_avd(dt, x, hp, lp, "C")
        dis, velocity, cropped = a[5], c[1], c[2]
        pgd = np.max(np.abs(cropped))
        ratio = np.abs(cropped[-1])/pgd
        tp = np.arange(len(dis))*dt
        first = math.floor(len(tp)-c[6]-n*.1)
        last = math.floor(len(tp)-c[6]+2*n*.1)
        tail_slope = abs(polyfit1(matlab_slice(tp, first, last), matlab_slice(dis, first, last))[0])
        for mm in range(len(fas)-9):
            if freq[mm] > hp:
                points = (freq[mm:mm+10], fas[mm:mm+10])
                break
        if points is None:
            raise ValueError("Reference polypoint is unassigned")
        pp = polyfit1(np.log10(points[0]), np.log10(points[1]))
        pre = matlab_slice(cropped, 1, math.floor(.95*pt/dt))
        std = np.std(pre, ddof=1 if len(pre) > 1 else 0)
        current = np.array([abs(velocity[-1]), ratio, tail_slope, pp[0],
                            abs(np.mean(pre))/pgd/pgd, std/pgd])
        trials.append(np.r_[hp, current])
        threshold = yz1 if mag < 7 else yz2
        offset = 0 if direction != 3 else 1
        passed = np.all(current[[0,1,2,4,5]] <= threshold[np.arange(0,10,2)+offset])
        if passed and (mag >= 6 or 1 <= current[3] <= 3):
            selected = hp
            break
    selected = float(matlab_round(selected, 4))
    return (selected, np.asarray(trials)) if return_trials else selected


def run_option7(acc, dt, mag, pt, st, se, direction, kind="A", base_tf=0):
    lp = choose_lp(acc, dt, pt, st, se)
    hp = choose_hp(acc, dt, mag, pt, direction, lp)
    nyq = .5/dt
    if not np.isfinite(lp) or lp <= 0:
        lp = .8*nyq
    if not np.isfinite(hp) or hp <= 0:
        hp = min(.1, .05*nyq)
    lp = min(lp, .8*nyq)
    if lp <= hp:
        hp = max(.9*lp/10, min(hp, lp/10))
    try:
        filter_avd(dt, acc, hp, lp, kind, base_tf)
    except Exception:
        pass  # The reference driver only warns about this unused output.
    mean_snr = float("nan")
    try:
        snr = calculate_snr(acc, dt, (0, max(0, .95*pt)), (st, se))
        idx = (snr[:,0] > hp) & (snr[:,0] < lp) & np.isfinite(snr[:,1])
        if np.any(idx):
            mean_snr = float(np.mean(snr[idx, 1]))
    except Exception:
        pass
    return dict(fHP=hp, fLP=lp, meanSNR=mean_snr)


def component_direction(component):
    """Same orientation mapping and unknown-component default as the R bridge."""
    comp = str(component).upper()
    if comp.endswith(('E', 'EW')):
        return 1
    if comp.endswith(('N', 'NS')):
        return 2
    if comp.endswith(('Z', 'UP', 'UD')):
        return 3
    return 1


def pick_corners(acc, dt, parr1, s_start, s_end, magnitude, component, kind='A', base_tf=0):
    """Production R bridge: cm/s² in, HP/LP Hz and mean SNR out.

    Keep both the MATLAB v2 driver clamps and the R v2 wrapper clamps. No
    interpolation, threshold, units or missing-result default is substituted.
    Exceptions propagate to the main script's existing per-method handler.
    """
    data = np.asarray(acc, dtype=np.float64).reshape(-1)
    dt, pt, st, se, mag = map(float, (dt, parr1, s_start, s_end, magnitude))
    if not np.isfinite(dt) or dt <= 0 or data.size < 2 or not np.all(np.isfinite(data)):
        raise ValueError('Guo requires finite acceleration samples and a positive sample interval')
    if not all(np.isfinite(value) for value in (pt, st, se, mag)):
        raise ValueError('Guo requires finite magnitude and P/S window times')
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        out = run_option7(data, dt, mag, pt, st, se, component_direction(component), kind, base_tf)
    hp, lp, snr = out['fHP'], out['fLP'], out['meanSNR']
    if not np.isfinite(hp) or hp <= 0:
        hp = 0.1
    if not np.isfinite(lp) or lp <= 0:
        lp = max(8, 10)
    if lp <= hp:
        hp = max(0.9*lp/10, min(hp, lp/10))
    return dict(fHP=float(hp), fLP=float(lp), meanSNR=float(snr))
