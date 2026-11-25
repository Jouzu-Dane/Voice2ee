"""Loudness per ITU-R BS.1770-4 / EBU R128 (LUFS), at 48 kHz.

Mono input is measured as "dual mono" (the same signal on left and right), which is how a loudness meter sees
a mono mic on a stereo source such as the EasyEffects mic. Stereo input (n, 2) is measured as is.
"""
import numpy as np
from scipy import signal

# K-weighting filter coefficients for 48 kHz (BS.1770-4, Table 1 and 2)
_B1 = [1.53512485958697, -2.69169618940638, 1.19839281085285]
_A1 = [1.0, -1.69065929318241, 0.73248077421585]
_B2 = [1.0, -2.0, 1.0]
_A2 = [1.0, -1.99004745483398, 0.99007225036621]
SR = 48000


def _as_channels(x):
    x = np.asarray(x, dtype=np.float64)
    return np.stack([x, x], axis=1) if x.ndim == 1 else x


def _block_power(x, win_s, hop_s):
    """Sum over channels of the K-weighted mean square in each window."""
    y = signal.lfilter(_B2, _A2, signal.lfilter(_B1, _A1, _as_channels(x), axis=0), axis=0)
    sq = np.concatenate([np.zeros((1, y.shape[1])), np.cumsum(y ** 2, axis=0)])
    w, h = int(round(win_s * SR)), int(round(hop_s * SR))
    if len(y) < w:
        return np.array([])
    starts = np.arange(0, len(y) - w + 1, h)
    return ((sq[starts + w] - sq[starts]) / w).sum(axis=1)


def _lufs(z):
    return -0.691 + 10 * np.log10(np.maximum(z, 1e-20))


def integrated(x):
    """Gated integrated loudness (LUFS). Silence and pauses are gated out, so this is 'speech loudness'."""
    z = _block_power(x, 0.4, 0.1)
    if len(z) == 0:
        return -70.0
    z = z[_lufs(z) > -70.0]                                   # absolute gate
    if len(z) == 0:
        return -70.0
    rel = _lufs(z.mean()) - 10.0                              # relative gate
    z = z[_lufs(z) > rel]
    return float(_lufs(z.mean())) if len(z) else -70.0


def short_term(x):
    """3 s short-term loudness every 100 ms (what loudness meters call 'S')."""
    return _lufs(_block_power(x, 3.0, 0.1))


def loudness_range(x):
    """Loudness range (LRA, EBU Tech 3342) in LU: how much the short-term loudness varies over the recording.
    Spoken videos sit well at about 5-8 LU; raw speech is often 10-15. None if the recording is too short."""
    st = _block_power(x, 3.0, 0.1)
    if len(st) < 20:
        return None
    st = st[_lufs(st) > -70.0]                                 # absolute gate
    if len(st) < 10:
        return None
    st = st[_lufs(st) > _lufs(st.mean()) - 20.0]               # relative gate: -20 LU
    v = _lufs(st)
    return float(np.percentile(v, 95) - np.percentile(v, 10))


def true_peak(x):
    """True peak in dBTP: the peak after 4x oversampling (what happens between samples after export)."""
    y = signal.resample_poly(_as_channels(x), 4, 1, axis=0)
    return float(20 * np.log10(np.abs(y).max() + 1e-12))


def momentary(x):
    """400 ms momentary loudness every 100 ms ('M')."""
    return _lufs(_block_power(x, 0.4, 0.1))
