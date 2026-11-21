"""Measurements of a take: levels, loudness (LUFS), spectrum, sibilance, clicks, pitch."""
import json
import sys

import numpy as np
from scipy import signal

from . import loudness
from .audio import channel_imbalance, measure_gain, mono
from .config import CACHE_DIR, FRAME, SR, TARGET_DB, TARGET_F


def frames_of(x, n=FRAME):
    m = len(x) // n
    return x[: m * n].reshape(m, n)


def rms_db(fr):
    return 20 * np.log10(np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)


def estimate_f0(vfr):
    lo, hi = int(SR / 400), int(SR / 70)
    f0s = []
    for f in vfr[:: max(1, len(vfr) // 400)]:
        f = f - f.mean()
        n = len(f)
        spec = np.fft.rfft(f, 2 * n)
        ac = np.fft.irfft(spec * np.conj(spec))[:n]
        ac = ac / (ac[0] + 1e-12)
        seg = ac[lo:hi]
        k = int(seg.argmax())
        if seg[k] > 0.5:
            f0s.append(SR / (k + lo))
    return float(np.median(f0s)) if len(f0s) >= 10 else 140.0


def analyze(noise, speech, clicks=None):
    """noise/speech/clicks: 1-D (mono) or (n, 2) arrays."""
    speech_st = speech
    noise, speech = mono(noise), mono(speech)
    clicks = mono(clicks) if clicks is not None else None
    noise_floor = max(float(np.median(rms_db(frames_of(noise)))), -100.0)
    fr = frames_of(speech)
    lv = rms_db(fr)
    gate = max(noise_floor + 10.0, float(np.percentile(lv, 95)) - 30.0)
    voiced = lv > gate
    if voiced.sum() < 30:
        sys.exit("Not enough speech detected - is the right source selected, and is it loud enough?")
    vfr, vlv = fr[voiced], lv[voiced]
    vpk = 20 * np.log10(np.abs(vfr).max(axis=1) + 1e-9)
    f0 = estimate_f0(vfr)
    # contiguous 10 ms RMS/peak (5 per 50 ms frame) - compressors react on this time scale
    f10 = frames_of(speech[: len(lv) * FRAME], FRAME // 5)
    lv10 = rms_db(f10)
    pk10 = 20 * np.log10(np.abs(f10).max(axis=1) + 1e-9)

    n30 = int(0.03 * SR)
    sp30 = rms_db(frames_of(speech, n30))
    v30 = sp30[sp30 > max(noise_floor + 10.0, float(np.percentile(sp30, 95)) - 35.0)]
    speech_lo30 = float(np.percentile(v30, 20)) if len(v30) >= 10 else float(vlv.min())
    click_hi30, click_n = None, 0
    if clicks is not None:
        act = rms_db(frames_of(clicks, n30))
        act = act[act > noise_floor + 6.0]
        click_n = int(len(act))
        if click_n >= 3:
            click_hi30 = float(np.percentile(act, 97))

    f, p = signal.welch(vfr.ravel(), SR, nperseg=8192)
    centers = 1000 * 2.0 ** (np.arange(-10, 11) / 3)
    lvls = np.array([10 * np.log10(p[(f >= c * 2 ** (-1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20)
                     for c in centers])
    ref_m = (centers >= 700) & (centers <= 1400)
    ref = 10 * np.log10(np.mean(10 ** (lvls[ref_m] / 10)))
    target = np.interp(np.log2(centers), np.log2(TARGET_F), TARGET_DB)
    diff = (lvls - ref) - target
    diff = np.convolve(np.pad(diff, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")

    sos = signal.butter(4, [4500, 9500], "bandpass", fs=SR, output="sos")
    sib_lv = rms_db(frames_of(signal.sosfilt(sos, speech)))[voiced]
    hm = (f >= 4000) & (f <= 10000)
    sm = np.convolve(p[hm], np.ones(85) / 85, "same")
    sib_peak = float(np.clip(f[hm][int(sm.argmax())], 5000, 9000))
    smv = np.convolve(p[hm], np.ones(85) / 85, "valid")
    sib_peaky = float(10 * np.log10(smv.max() / (np.median(smv) + 1e-20) + 1e-20))

    st = loudness.short_term(speech_st)
    return dict(
        noise_floor=noise_floor, speech_db=float(vlv.mean()),
        peak_db=float(20 * np.log10(np.abs(speech).max() + 1e-9)),
        clip_frac=float((np.abs(speech) > 0.98).mean()), f0=f0,
        snr=float(vlv.mean() - noise_floor), dr=float(np.percentile(vlv, 90) - np.percentile(vlv, 20)),
        centers=centers, diff=diff, vlv=vlv,
        sib_diff=float(diff[(centers >= 4900) & (centers <= 8100)].mean()),
        sib_lv=sib_lv, sib_peak=sib_peak, sib_peaky=sib_peaky,
        speech_lo30=speech_lo30, click_hi30=click_hi30, click_n=click_n,
        vpk=vpk, pk5=float(np.percentile(vpk, 5)), pk98=float(np.percentile(vpk, 98)),
        pkp10=float(np.percentile(vpk, 10)), pkp50=float(np.median(vpk)), pkp95=float(np.percentile(vpk, 95)),
        lv10=lv10, pk10=pk10, voiced50=voiced.astype(float),
        lufs=loudness.integrated(speech_st),
        lufs_st_max=float(st.max()) if len(st) else -70.0,
        imbalance=channel_imbalance(speech_st),
    )


def analyze_take(take):
    a = analyze(take["noise"], take["speech"], take.get("clicks"))
    if take.get("raw_speech") is not None:
        a.update(measure_gain(take["raw_speech"], take["speech"]))
    return a


def bmean(a, lo, hi):
    m = (a["centers"] >= lo) & (a["centers"] <= hi)
    return float(a["diff"][m].mean())


def save_analysis(a):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "last.json").write_text(json.dumps({k: (v.tolist() if isinstance(v, np.ndarray) else v)
                                                      for k, v in a.items()}))


def load_analysis():
    f = CACHE_DIR / "last.json"
    if not f.exists():
        sys.exit("No saved analysis yet - run once without --reuse first.")
    return {k: (np.array(v) if isinstance(v, list) else v) for k, v in json.loads(f.read_text()).items()}
