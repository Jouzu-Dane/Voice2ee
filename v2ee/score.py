"""The scorecard: how good a processed recording is, as one number (lower = better) plus the parts.

Every part is a 'cost' on a common scale (about 1 = clearly noticeable). Goals re-weight the parts, hard limits
(peaks over the ceiling, more gain than allowed) reject a candidate outright."""
import numpy as np
from scipy import signal

from .analysis import analyze
from .audio import measure_gain, mono
from .config import PROFILES, SR, prof

GOALS = {
    "balanced":  dict(label="Balanced - everything equally (recommended)",
                      w=dict(loud=1, clicks=1, knocks=1, words=1, tone=1, sib=1, noise=1, quality=1)),
    "keyboard":  dict(label="Loud keyboard - clicks and knocks gone, even at some naturalness cost",
                      w=dict(loud=1, clicks=2.5, knocks=2, words=0.8, tone=0.8, sib=1, noise=1.8, quality=0.8)),
    "natural":   dict(label="Natural - fewest artefacts, keeps room and breaths",
                      w=dict(loud=0.8, clicks=0.6, knocks=1, words=2, tone=1.2, sib=1, noise=0.5, quality=1.5)),
    "broadcast": dict(label="Broadcast - dense, even, up-front voice",
                      w=dict(loud=1.8, clicks=1, knocks=1, words=1, tone=1.3, sib=1.3, noise=1, quality=1)),
    "clarity":   dict(label="Clarity - intelligibility first (calls, teaching)",
                      w=dict(loud=1, clicks=1, knocks=1, words=2.5, tone=0.8, sib=1.5, noise=1, quality=1)),
}
REJECT = 1000.0


def estoi(clean, proc, fs=SR):
    """Extended short-time objective intelligibility (Jensen & Taal 2016), 0..1. Compares how the band
    envelopes of `proc` follow those of `clean` over ~384 ms segments - level- and EQ-robust, but drops when
    syllables are cut, smeared or buried in noise."""
    x = signal.resample_poly(np.asarray(mono(clean), np.float64), 5, int(fs / 2000))      # -> 10 kHz
    y = signal.resample_poly(np.asarray(mono(proc), np.float64), 5, int(fs / 2000))
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    N, hop, nfft, J, N_seg = 256, 128, 512, 15, 30
    w = np.hanning(N + 2)[1:-1]
    idx = np.arange(0, n - N, hop)
    if len(idx) < N_seg + 2:
        return 1.0
    fx = np.stack([x[i:i + N] * w for i in idx])
    fy = np.stack([y[i:i + N] * w for i in idx])
    en = 20 * np.log10(np.linalg.norm(fx, axis=1) + 1e-12)
    keep = en > en.max() - 40                                    # drop silent frames (of the clean signal)
    fx, fy = fx[keep], fy[keep]
    if len(fx) < N_seg + 2:
        return 1.0
    X = np.abs(np.fft.rfft(fx, nfft, axis=1)) ** 2
    Y = np.abs(np.fft.rfft(fy, nfft, axis=1)) ** 2
    f = np.linspace(0, 10000, nfft + 1)[: nfft // 2 + 1]
    k = np.arange(J)
    lo, hi = 150 * 2 ** ((2 * k - 1) / 6), 150 * 2 ** ((2 * k + 1) / 6)
    obm = np.zeros((J, len(f)))
    for j in range(J):
        obm[j, np.argmin((f - lo[j]) ** 2): np.argmin((f - hi[j]) ** 2)] = 1
    Xb, Yb = np.sqrt(obm @ X.T), np.sqrt(obm @ Y.T)              # (J, frames)
    d = []
    for m in range(N_seg, Xb.shape[1] + 1):
        xs, ys = Xb[:, m - N_seg:m], Yb[:, m - N_seg:m]
        xs = xs - xs.mean(axis=1, keepdims=True); ys = ys - ys.mean(axis=1, keepdims=True)
        xs = xs / (np.linalg.norm(xs, axis=1, keepdims=True) + 1e-12)
        ys = ys / (np.linalg.norm(ys, axis=1, keepdims=True) + 1e-12)
        xs = xs - xs.mean(axis=0, keepdims=True); ys = ys - ys.mean(axis=0, keepdims=True)
        xs = xs / (np.linalg.norm(xs, axis=0, keepdims=True) + 1e-12)
        ys = ys / (np.linalg.norm(ys, axis=0, keepdims=True) + 1e-12)
        d.append(np.sum(xs * ys) / N_seg)
    return float(np.clip(np.mean(d), 0, 1))


def taste_tone_shift(centers, taste):
    """How a stored taste moves the tonal target (dB per third-octave)."""
    if not taste:
        return np.zeros(len(centers))
    c = np.asarray(centers)
    return (2.5 * taste.get("warm", 0) * ((c >= 120) & (c <= 400)) +
            2.5 * taste.get("bright", 0) * ((c >= 2000) & (c <= 6000)) +
            2.5 * taste.get("air", 0) * (c >= 7000))


def scorecard(proc, P, goal="balanced", quality=None, taste=None):
    """proc: processed take (noise/speech/clicks/taps + raw_speech). Returns (total, parts, analysis)."""
    pname = P.get("profile", "stream")
    pr = PROFILES[pname]
    w = GOALS.get(goal, GOALS["balanced"])["w"]
    a = analyze(proc["noise"], proc["speech"], proc.get("clicks"), proc.get("taps"))
    parts, why = {}, []
    # hard limits
    ceiling = prof(P, "peak_hi")
    if a["peak_db"] > ceiling + 0.5:
        why.append(f"peaks {a['peak_db']:.1f} dBFS over {ceiling:.0f}")
    g = None
    if proc.get("raw_speech") is not None:
        g = measure_gain(proc["raw_speech"], proc["speech"])
        a.update(g)
        if g.get("applied_gain") is not None and g["applied_gain"] > P.get("max_gain", 12.0) + 1.0:
            why.append(f"adds {g['applied_gain']:+.1f} dB (allowed {P.get('max_gain', 12.0):+.0f})")
    # soft parts
    tgt = P.get("target_lufs", PROFILES[pname]["target_lufs"])
    tol = prof(P, "lufs_tol")
    parts["loud"] = max(0.0, abs(a["lufs"] - tgt) - 0.5 * tol)
    if a.get("click_hi30") is not None:
        leak = a["click_hi30"] - a["speech_lo30"]
        parts["clicks"] = max(0.0, leak - pr["click_target"]) / 3
    if a.get("tap_low_hi") is not None and a.get("low_voice_hi") is not None:
        parts["knocks"] = max(0.0, a["tap_low_hi"] - a["low_voice_hi"] - 2.0) / 3
    if proc.get("raw_speech") is not None:
        e = estoi(proc["raw_speech"], proc["speech"])
        a["estoi"] = e
        parts["words"] = max(0.0, 0.95 - e) * 40
    band = (a["centers"] >= 100) & (a["centers"] <= 8000)
    resid = a["diff"] - taste_tone_shift(a["centers"], taste)
    parts["tone"] = float(np.sqrt(np.mean(resid[band] ** 2))) / 2
    parts["sib"] = max(0.0, a["sib_diff"] - 1.0) / 2
    parts["noise"] = max(0.0, pr["snr_target"] - a["snr"]) / 5
    if a.get("pause_db") is not None:                          # thinking pauses should be quiet
        parts["pauses"] = max(0.0, a["pause_db"] + (pr["snr_target"] - 5.0)) / 5
    if pr["lra"] and a.get("lra") is not None:                 # video: even enough, not squashed
        lo, hi = pr["lra"]
        parts["lra"] = max(0.0, a["lra"] - hi, lo - a["lra"]) / 2
    if pr["true_peak"] is not None and a["true_peak"] > pr["true_peak"] + 0.3:
        why.append(f"true peak {a['true_peak']:.1f} dBTP over {pr['true_peak']:.0f}")
    if quality is not None:
        q = quality.score(proc["speech"])
        a["dnsmos"] = q
        parts["quality"] = max(0.0, 4.4 - q["ovrl"]) * 3
    total = sum(w.get(k, 1.0) * v for k, v in parts.items())
    if why:
        total += REJECT
        a["rejected"] = "; ".join(why)
    return float(total), parts, a


def summary(a):
    bits = [f"{a['lufs']:.1f} LUFS", f"peaks {a['peak_db']:.1f}"]
    if a.get("click_hi30") is not None:
        bits.append(f"clicks {a['click_hi30'] - a['speech_lo30']:+.0f} dB")
    elif "click_hi30" in a:
        bits.append("clicks gone")
    if a.get("estoi") is not None:
        bits.append(f"words kept {a['estoi']:.2f}")
    if a.get("dnsmos"):
        bits.append(f"quality {a['dnsmos']['ovrl']:.2f}/5")
    return " | ".join(bits)
