"""Preserve the bundled SciPy 1.18.1 filter initialization on Python 3.11.

SciPy 1.17.1 is the last tested SciPy release usable with Python 3.11. Its
``filtfilt`` implementation is identical to 1.18.1, but ``lfilter_zi`` is not.
For 1.17.1 only, this module binds the exact 1.18.1 initializer in a private
copy of filtfilt's globals. No SciPy module or installed file is modified.
SciPy 1.18 and later continue to use their native function.

The initializer below is copied verbatim (apart from its private name) from
SciPy 1.18.1, scipy/signal/_signaltools.py, lines 4330-4462. Its original
source SHA-256 is 8d7d0c310938bdedc94306a3c61b1694ce27fb968457e7e94391d3cc8b628857.
Source: https://github.com/scipy/scipy/blob/v1.18.1/scipy/signal/_signaltools.py
The full applicable SciPy BSD license is in scipy_signal_compat_LICENSE.txt.

This is a dependency compatibility adapter. It does not alter SignalQC's
filter orders, corner frequencies, padding, integration, checks or thresholds.
"""
from __future__ import annotations

from functools import update_wrapper
from types import FunctionType

from packaging.version import InvalidVersion, Version
import scipy
from scipy.signal import _signaltools
from scipy._lib._array_api import array_namespace, xp_promote
xpx = _signaltools.xpx  # SciPy moved this helper between 1.17 and 1.18.

def _lfilter_zi_118(b, a):
    r"""Construct initial conditions for `lfilter` for step response steady-state.

    Compute an initial state `zi` for the `lfilter` function that corresponds
    to the steady state of the step response.

    A typical use of this function is to set the initial state so that the
    output of the filter starts at the same value as the first element of
    the signal to be filtered.

    Parameters
    ----------
    b : array_like
        The numerator coefficient vector as a 1-D sequence.
    a : array_like
        The denominator coefficient vector as a 1-D sequence.  If ``a[0]``
        is not 1, then both `a` and `b` are normalized by ``a[0]``.
        Hence, ``a[0] != 0`` must hold.

    Returns
    -------
    zi : 1-D ndarray
        The initial state for the filter.

    Raises
    ------
    ValueError
        If ``a[0] == 0`` (invalid denominator polynomial) or
        ``sum(a) == 0`` (unstable filter).

    See Also
    --------
    lfilter, lfiltic, filtfilt

    Notes
    -----
    The parameters `b` and `a` represent a transfer function :math:`H(z) = Y(z)/X(z)`
    which is defined in the :ref:`tutorial_signal_TransferFunctionRepresentation`
    section of the :ref:`user_guide`. As discussed in [1]_, the final value of
    filtering a step response :math:`X(z) = z / (z-1)`, i.e., steady state, is given by

    .. math::

        y_\infty := \lim_{k\to\infty} y[k]
                  =  \lim_{z\to 1}\ (z-1)\, Y(z)
                  = \frac{\sum_{i=0}^M b_i}{\sum_{j=0}^N a_j} \,.

    If the denominator is zero, :math:`H(z)` has a pole at :math:`z_\infty=1`, which
    makes the filter unstable. For the transposed Direct Form II, which is implemented
    in `lfilter`, the initialization values :math:`z_k` for :math:`H(z)` can be
    determined by the recurrence equation

    .. math::

        z_k = z_{k+1} + x[0] \big( b_k - y_\infty a_k \big) \,,

    with :math:`x[0]` being the height of the input step function. Note that
    :math:`a_0=1` is assumed here, which is incorporated into this function by
    performing a normalization step.

    Examples
    --------
    The following code creates a lowpass Butterworth filter to filter a signal made up
    of ones. As expected of a lowpass filter, the output is also all ones. If the `zi`
    argument of `lfilter` had not been given, a transient signal would have been
    produced. The second signal illustrates that using the parameter `zi` supresses
    transients at the beginning of the output signal:

    >>> import numpy as np
    >>> from scipy.signal import lfilter, lfilter_zi, butter
    ...
    >>> b, a = butter(5, 0.25)
    >>> zi = lfilter_zi(b, a)
    >>> y0, zi0 = lfilter(b, a, np.ones(10), zi=zi)
    >>> y0
    array([1.,  1.,  1.,  1.,  1.,  1.,  1.,  1.,  1.,  1.])
    >>> # Another signal:
    >>> x = np.array([0.5, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0])
    >>> y1, zi1 = lfilter(b, a, x, zi=zi*x[0])
    >>> y1
    array([ 0.5       ,  0.5       ,  0.5       ,  0.49836039,  0.48610528,
        0.44399389,  0.35505241])

    Note that the `zi` argument to `lfilter` is computed using `lfilter_zi` and scaled
    by ``x[0]``. As a result, the output `y1` has no transient until the input drops
    from 0.5 to 0.

    References
    ----------
    .. [1] Boris Likhterov and Norman Kopeika. "Hardware-efficient technique for
           minimizing startup transients in Direct Form II digital filters". In:
           International Journal of Electronics -- Volume 90(7), July 2003, pp.
           471--479. :doi:`10.1080/00207210310001612482`
    """
    xp = array_namespace(b, a)

    # Note: As an alternative to this function, using `lfiltic` might work as well.
    # For example, when b,a = butter(N,Wn), then
    #    lfiltic(b, a, y=numpy.ones_like(a)*y_inf, x=numpy.ones_like(b)).
    # should produce the same result. Though, no obvious algorithmic advantages over
    # this implementation could be identified.

    # We could use scipy.signal.normalize, but it uses warnings in
    # cases where a ValueError is more appropriate, and it allows
    # b to be 2D.
    b, a = xp_promote(b, a, xp=xp, force_floating=True)  # need floats for division
    b = xpx.atleast_nd(b, ndim=1, xp=xp)
    a = xpx.atleast_nd(a, ndim=1, xp=xp)

    if not (b.ndim == a.ndim == 1):
        raise ValueError("Numerator `b` and Denominator `a` must be 1-D arrays, " +
                         f"but {b.shape = }, {a.shape = }!")

    if a[0] == 0:
        raise ValueError("First coefficient of parameter `a` must be non-zero!")

    if a[0] != 1:  # Normalize the coefficients so a[0] == 1:
        b, a = b / a[0], a / a[0]

    if (sum_a := xp.sum(a)) == 0:
        raise ValueError("Filter not stable due to sum(a) == 0, i.e., " +
                         "having a pole at z = 1!")

    y_inf = xp.sum(b) / sum_a  # y[k → ∞] for unit-step input

    # Calculate `zi[k] = zi[k+1] + b - y_inf*a` allowing different length for a, b:
    n_a, n_b = a.shape[0], b.shape[0]
    n = max(n_a, n_b)
    b = xpx.pad(b, (0, n-n_b))
    a = xpx.pad(a, (0, n-n_a))
    # `xp.cumulative_sum((b - y_inf*a)[::-1])[-2::-1]` does not work in torch due to
    # unsupported slicing with a negative step index. Hence, `flip` is used:
    return xp.flip(xp.cumulative_sum(xp.flip(b - y_inf*a)))[1:]


def _isolated_filtfilt():
    """Clone the tested implementation, leaving SciPy's global state intact."""
    native = _signaltools.filtfilt
    if not isinstance(native, FunctionType) or native.__closure__ is not None:
        raise RuntimeError(
            "SignalQC cannot preserve filtering with this SciPy build. "
            "Use the bundled runtime or the tested SciPy 1.17.1 distribution."
        )
    private_globals = native.__globals__.copy()
    private_globals["lfilter_zi"] = _lfilter_zi_118
    isolated = FunctionType(
        native.__code__, private_globals, native.__name__, native.__defaults__
    )
    isolated.__kwdefaults__ = (
        None if native.__kwdefaults__ is None else native.__kwdefaults__.copy()
    )
    return update_wrapper(isolated, native)


def compatible_filtfilt():
    """Return the established filter behavior without patching SciPy globally.

    The compatibility branch is limited to the tested SciPy 1.17.1 release.
    Native SciPy >=1.18 remains untouched; no claim of exact equivalence with
    future SciPy versions is implied by returning their native implementation.
    """
    try:
        version = Version(scipy.__version__)
    except InvalidVersion as exc:
        raise RuntimeError(
            "SignalQC cannot identify this SciPy version. Use the bundled "
            "runtime, or install the supplied requirements for your Python version."
        ) from exc
    if version >= Version("1.18"):
        return _signaltools.filtfilt
    if version == Version("1.17.1"):
        return _isolated_filtfilt()
    raise RuntimeError(
        f"SignalQC requires SciPy 1.17.1 with its compatibility adapter, or "
        f"SciPy 1.18 or later; found {scipy.__version__}. Use the bundled runtime "
        "or install the supplied requirements for your Python version."
    )
