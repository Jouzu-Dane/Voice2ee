"""Measurements of a take: levels, loudness (LUFS), spectrum, sibilance, clicks, pitch."""
import json
import sys

import numpy as np
from scipy import signal

from . import loudness
from .audio import channel_imbalance, measure_gain, mono
from .config import CACHE_DIR, FRAME, LOW_SPLIT, SR, TARGET_DB, TARGET_F


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


def _loud_channel(x):
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1 or x.shape[1] < 2:
        return None
    return int(np.argmax((x ** 2).mean(axis=0)))


def _runs(mask):
    """(start, length) of consecutive True runs."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - start)); start = None
    if start is not None:
        out.append((start, len(mask) - start))
    return out


def timing(speech, gate_db, noise_floor):
    """How your speech moves in time, on a 5 ms envelope:
    syllable rate, how fast syllables rise and fall, how long sound lingers after a word (word tail + room
    echo) and how long the short pauses between words are."""
    n5 = SR // 200
    env = rms_db(frames_of(speech, n5))
    if len(env) < 400:
        return {}
    env = np.convolve(np.pad(env, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    voiced = env > gate_db
    out = {}

    # syllables: envelope peaks with >= 4 dB prominence inside voiced stretches
    peaks, _ = signal.find_peaks(np.where(voiced, env, gate_db - 20), prominence=4.0, distance=12)  # >= 60 ms apart
    vsec = voiced.sum() * 0.005
    if len(peaks) >= 8 and vsec > 2:
        out["syl_rate"] = float(clip_(len(peaks) / vsec, 2.0, 9.0))
        rises, falls = [], []
        for pk in peaks:
            lo = max(0, pk - 40)
            i0 = lo + int(np.argmin(env[lo:pk + 1]))
            hi = min(len(env), pk + 41)
            i1 = pk + int(np.argmin(env[pk:hi]))
            if env[pk] - env[i0] >= 6:
                # time from 12 dB below the peak (or the dip) up to the peak
                seg = env[i0:pk + 1]
                k = int(np.argmax(seg >= env[pk] - 12))
                rises.append((len(seg) - 1 - k) * 5.0)
            if env[pk] - env[i1] >= 6:
                seg = env[pk:i1 + 1]
                k = int(np.argmax(seg <= env[pk] - 12)) if (seg <= env[pk] - 12).any() else len(seg) - 1
                falls.append(k * 5.0)
        if len(rises) >= 5:
            out["rise_ms"] = float(np.median(rises))
            out["rise_fast_ms"] = float(np.percentile(rises, 10))
        if len(falls) >= 5:
            out["fall_ms"] = float(np.median(falls))

    # word tails: after a voiced stretch ends, how long until the sound is 20 dB down (or at the noise floor)
    runs = _runs(voiced)
    tails, gaps = [], []
    for (s0, ln), nxt in zip(runs, runs[1:] + [(len(env), 0)]):
        end = s0 + ln
        gap = nxt[0] - end
        if 10 <= gap <= 200:                                 # 50 ms .. 1 s pause between words
            gaps.append(gap * 5.0)
        if ln < 10 or gap < 8:
            continue
        ref = float(env[max(s0, end - 10):end].max())
        stop = max(ref - 20.0, noise_floor + 3.0)
        seg = env[end - 1: min(nxt[0], end + 120)]           # up to 600 ms, never into the next word
        below = np.nonzero(seg <= stop)[0]
        if len(below):
            tails.append(below[0] * 5.0)
    # thinking pauses (> 1 s): how loud the background is in them, and how loud the first word after one is,
    # both relative to your typical syllable peaks
    ref = float(np.median(env[peaks])) if len(peaks) else float(np.percentile(env[voiced], 90))
    pause_lv, restart = [], []
    for (s0, ln), nxt in zip(runs, runs[1:]):
        gap_start, gap_end = s0 + ln, nxt[0]
        if gap_end - gap_start >= 200:
            seg = env[gap_start + 40: gap_end - 10]               # skip the word tail and the next onset
            if len(seg) > 20:
                pause_lv.append(float(np.median(seg)) - ref)
            restart.append(float(env[gap_end: gap_end + 60].max()) - ref)
    if pause_lv:
        out["long_pauses"] = len(pause_lv)
        out["pause_db"] = float(np.median(pause_lv))
        out["restart_db"] = float(np.median(restart))
    if len(tails) >= 5:
        out["decay_ms"] = float(np.median(tails))
        out["decay_long_ms"] = float(np.percentile(tails, 80))
    if len(gaps) >= 5:
        out["gap_ms"] = float(np.median(gaps))
    return out


def clip_(x, lo, hi):
    return min(max(x, lo), hi)


def analyze(noise, speech, clicks=None, taps=None):
    """noise/speech/clicks/taps: 1-D (mono) or (n, 2) arrays. taps = desk knocks / mic-stand bumps."""
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
    # width of the sibilance band: where it falls 3 dB below its peak on each side
    fh = f[hm]
    k = int(np.argmin(np.abs(fh - sib_peak)))
    half = sm[k] / 2
    lo_i = k - int(np.argmax(sm[k::-1] < half)) if (sm[k::-1] < half).any() else 0
    hi_i = k + int(np.argmax(sm[k:] < half)) if (sm[k:] < half).any() else len(sm) - 1
    sib_lo, sib_hi = float(max(fh[lo_i], 3000.0)), float(min(fh[hi_i], 12000.0))
    sib_lo, sib_hi = min(sib_lo, sib_peak * 0.92), max(sib_hi, sib_peak * 1.08)

    # noise spectrum: rumble below 80 Hz and hiss at the very top, both relative to your voice
    fn, pn = signal.welch(noise, SR, nperseg=8192)
    # rumble: noise power below 80 Hz vs your voice's total power (both as this mic hears them)
    rumble_db = float(10 * np.log10(pn[(fn >= 20) & (fn < 80)].sum() / (p.sum() + 1e-30) + 1e-30))
    top = (f >= 8000) & (f <= 14000)
    hf_snr = float(10 * np.log10(p[top].mean() / (pn[top].mean() + 1e-30) + 1e-30))
    # highest third-octave where your voice (as this mic hears it) is still within 30 dB of the 1 kHz level
    rel = lvls - ref
    above = centers[(rel > -30) & (centers >= 2000)]
    mic_top = float(above.max()) if len(above) else 8000.0

    # low band (desk taps / thumps live here). Measured as PEAKS, like the tap catcher reacts: your voice's
    # loudest bass peaks vs the loudest moment of each knock
    lp = signal.butter(4, LOW_SPLIT, "low", fs=SR, output="sos")
    n10 = FRAME // 5

    def low_peaks(x):
        y = np.abs(signal.sosfilt(lp, x))
        m = len(y) // n10
        return 20 * np.log10(y[: m * n10].reshape(m, n10).max(axis=1) + 1e-9)

    low_v = low_peaks(speech[: len(lv) * FRAME])
    v10 = np.repeat(voiced, 5)[: len(low_v)]
    low_voice_hi = float(np.percentile(low_v[v10], 99)) if v10.any() else None
    low_noise = float(np.median(low_peaks(noise)))
    tap_low_hi, tap_n, tap_ms = None, 0, None
    if taps is not None:
        lt = low_peaks(mono(taps))
        hot = lt > low_noise + 15.0
        events, lens, cur, ln = [], [], None, 0
        for i, h in enumerate(hot):                 # group consecutive loud frames into knocks
            if h:
                cur = lt[i] if cur is None else max(cur, lt[i]); ln += 1
            elif cur is not None:
                events.append(cur); lens.append(ln); cur, ln = None, 0
        if cur is not None:
            events.append(cur); lens.append(ln)
        tap_n = len(events)
        if tap_n >= 2:
            tap_low_hi = float(np.percentile(events, 75))    # a typical loud knock
            tap_ms = float(np.median(lens) * 10.0)           # how long a knock rings (10 ms frames)

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
        lufs=loudness.integrated(speech_st), lra=loudness.loudness_range(speech_st),
        true_peak=loudness.true_peak(speech_st),
        lufs_st_max=float(st.max()) if len(st) else -70.0,
        imbalance=channel_imbalance(speech_st), loud_ch=_loud_channel(speech_st),
        low_voice_hi=low_voice_hi, low_noise=low_noise, tap_low_hi=tap_low_hi, tap_n=tap_n, tap_ms=tap_ms,
        sib_lo=sib_lo, sib_hi=sib_hi, rumble_db=rumble_db, hf_snr=hf_snr, mic_top=mic_top,
        pop_db=(float(low_voice_hi - np.median(low_v[v10])) if v10.any() and low_voice_hi is not None else 0.0),
        **timing(speech, gate, noise_floor),
    )


def analyze_take(take):
    a = analyze(take["noise"], take["speech"], take.get("clicks"), take.get("taps"))
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
