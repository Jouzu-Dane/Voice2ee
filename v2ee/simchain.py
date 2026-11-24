"""Approximate EasyEffects/LSP chain renderer for a voice2ee preset JSON (offline, numpy/scipy only).
Used by the Minimal version's optimiser (results are always confirmed through the real EasyEffects) and by
the tests. The Full version uses the real plugins instead (offline.py).
Not bit-exact: gate, expander, EQ (RBJ biquads), RMS compressors, split-band de-esser, crude exciter and a
lookahead limiter - including the limiter's 'Threshold Boost' (gain-boost), which EasyEffects defaults to ON.
RNNoise is not simulated (passes through)."""
import numpy as np
from scipy import signal
SR = 48000
BLK = 48  # 1 ms control rate

def dbv(x): return 20*np.log10(np.maximum(x, 1e-9))
def env_rms_db(x, ms):
    n = max(1, int(ms*SR/1000)); p = np.convolve(x**2, np.ones(n)/n, "same")
    return 10*np.log10(np.maximum(p, 1e-18))
def blocks(v): m = len(v)//BLK; return v[:m*BLK].reshape(m, BLK).mean(1)
def smooth_db(target, att_ms, rel_ms, rising_is_attack=False):
    """one-pole per 1ms block; 'attack' = gain going DOWN (reduction) unless rising_is_attack."""
    a_att = np.exp(-1/max(att_ms, 0.1)); a_rel = np.exp(-1/max(rel_ms, 0.1))
    out = np.empty_like(target); g = target[0]
    for i, t in enumerate(target):
        down = t < g
        a = (a_rel if down else a_att) if rising_is_attack else (a_att if down else a_rel)
        g = a*g + (1-a)*t; out[i] = g
    return out
def apply_gain_db(x, gdb_blocks):
    g = np.repeat(10**(gdb_blocks/20), BLK)
    if len(g) < len(x): g = np.concatenate([g, np.full(len(x)-len(g), g[-1])])
    return x*g[:len(x)]

def detector(x, p):
    """Apply the plugin's sidechain HPF/LPF (if any) to the detector signal."""
    d = x
    for key, kind in (("hpf", "high"), ("lpf", "low")):
        mode = str(p.get(f"{key}-mode", "Off"))
        if mode.lower() != "off":
            order = {"12": 2, "24": 4, "36": 6}.get(mode.split()[0], 2)
            d = signal.sosfilt(signal.butter(order, p.get(f"{key}-frequency", 1000.0), kind, fs=SR, output="sos"), d)
    return d

def gate(x, p):
    sc = p.get("sidechain", {}); L = blocks(env_rms_db(detector(x, p), sc.get("reactivity", 10)))
    thr = p.get("curve-threshold", -40); hyst = p.get("hysteresis-threshold", -3) if p.get("hysteresis", False) else 0
    red = p.get("reduction", -40); state = False; tgt = np.empty_like(L)
    for i, l in enumerate(L):
        if not state and l > thr: state = True
        elif state and l < thr + hyst: state = False
        tgt[i] = 0.0 if state else red
    g = smooth_db(tgt, p.get("attack", 10), p.get("release", 100), rising_is_attack=True)
    return apply_gain_db(x, g + p.get("makeup", 0))

def expander(x, p):
    sc = p.get("sidechain", {}); L = blocks(env_rms_db(detector(x, p), sc.get("reactivity", 10)))
    thr, r = p.get("threshold", -40), p.get("ratio", 2)
    if str(p.get("mode", "Downward")).lower().startswith("up"):
        tgt = np.minimum(np.maximum(L-thr, 0)*(r-1), 30.0)      # boosts ABOVE threshold (hazard)
        g = smooth_db(tgt, p.get("attack", 20), p.get("release", 100), rising_is_attack=True)
    else:
        tgt = np.maximum(-np.maximum(thr-L, 0)*(r-1), -40.0)
        g = smooth_db(tgt, p.get("attack", 20), p.get("release", 100), rising_is_attack=True)
    return apply_gain_db(x, g + p.get("makeup", 0))

def rbj(typ, f, g, q):
    A = 10**(g/40); w = 2*np.pi*f/SR; al = np.sin(w)/(2*q); c = np.cos(w)
    if typ == "Bell":
        b = [1+al*A, -2*c, 1-al*A]; a = [1+al/A, -2*c, 1-al/A]
    elif typ in ("Hi-shelf", "Lo-shelf"):
        s = 1 if typ == "Hi-shelf" else -1; sa = 2*np.sqrt(A)*al
        if s > 0:
            b = [A*((A+1)+(A-1)*c+sa), -2*A*((A-1)+(A+1)*c), A*((A+1)+(A-1)*c-sa)]
            a = [(A+1)-(A-1)*c+sa, 2*((A-1)-(A+1)*c), (A+1)-(A-1)*c-sa]
        else:
            b = [A*((A+1)-(A-1)*c+sa), 2*A*((A-1)-(A+1)*c), A*((A+1)-(A-1)*c-sa)]
            a = [(A+1)+(A-1)*c+sa, -2*((A-1)+(A+1)*c), (A+1)+(A-1)*c-sa]
    else:
        return None
    return np.array(b)/a[0], np.array(a)/a[0]
def equalizer(x, p):
    y = x.copy()
    for k, b in sorted(p.get("left", {}).items(), key=lambda kv: int(kv[0][4:])):
        if b.get("mute"): continue
        t = b.get("type", "Off")
        if t == "Hi-pass":
            order = {"x1": 1, "x2": 2, "x3": 3, "x4": 4}.get(b.get("slope", "x1"), 1)
            y = signal.sosfilt(signal.butter(order, b["frequency"], "high", fs=SR, output="sos"), y)
        elif t == "Lo-pass":
            y = signal.sosfilt(signal.butter(2, b["frequency"], "low", fs=SR, output="sos"), y)
        else:
            c = rbj(t, b["frequency"], b.get("gain", 0), b.get("q", 0.7))
            if c: y = signal.lfilter(c[0], c[1], y)
    return y

def env_peak_db(x, ms):
    from scipy.ndimage import maximum_filter1d
    n = max(1, int(ms * SR / 1000))
    return dbv(maximum_filter1d(np.abs(x), size=n))

def compressor(x, p):
    sc = p.get("sidechain", {})
    d = detector(x, p)
    la = int(sc.get("lookahead", 0) * SR / 1000)
    if la > 0:
        d = np.concatenate([d[la:], np.zeros(la)])                # lookahead: react before the peak arrives
    env = env_peak_db(d, max(1.0, sc.get("reactivity", 10) / 4)) if sc.get("mode") == "Peak" else env_rms_db(d, sc.get("reactivity", 10))
    L = np.array([e.max() for e in env[: len(env) // BLK * BLK].reshape(-1, BLK)]) if sc.get("mode") == "Peak" else blocks(env)
    thr, r = p.get("threshold", -20), p.get("ratio", 2); kw = abs(p.get("knee", -6))
    over = L - thr
    gr = np.where(over <= -kw/2, 0.0, np.where(over >= kw/2, over*(1-1/r), (1-1/r)*(over+kw/2)**2/(2*kw)))
    tgt = -gr
    md = str(p.get("mode", "Downward")).lower()
    if md in ("upward", "boosting"):   # hazard: lifts quiet sound up to boost-amount
        tgt = np.minimum(np.maximum(-over, 0)*(1-1/r), p.get("boost-amount", 6))
    g = smooth_db(tgt, p.get("attack", 20), p.get("release", 100))
    return apply_gain_db(x, g + p.get("makeup", 0))

def deesser(x, p):
    f1 = p.get("f1-freq", 6000)
    sos = signal.butter(2, f1*0.8, "high", fs=SR, output="sos")
    hf = signal.sosfilt(sos, x); lf = x - hf
    L = blocks(env_rms_db(hf, 5))
    over = L - p.get("threshold", -20)
    tgt = -np.maximum(over, 0)*(1-1/p.get("ratio", 3))
    g = smooth_db(tgt, 1.0, max(5, p.get("laxity", 15)))
    return lf + apply_gain_db(hf, g + p.get("makeup", 0))

def exciter(x, p):
    hp = signal.sosfilt(signal.butter(2, p.get("scope", 4000), "high", fs=SR, output="sos"), x)
    h = np.tanh(hp*10**(p.get("harmonics", 2)/6))/10**(p.get("harmonics", 2)/6) - hp
    if p.get("ceil-active", True):
        h = signal.sosfilt(signal.butter(2, min(p.get("ceil", 16000), SR/2-100), "low", fs=SR, output="sos"), h)
    return x + h*10**(p.get("amount", 0)/20)*4

def limiter(x, p):
    thr = 10**(p.get("threshold", 0)/20); la = int(p.get("lookahead", 5)*SR/1000)
    pk = np.abs(x)
    if la > 1:
        from scipy.ndimage import maximum_filter1d
        pk = maximum_filter1d(pk, size=2*la+1)
    tg = np.minimum(1.0, thr/np.maximum(pk, 1e-9))
    tgb = dbv(blocks(tg))
    g = smooth_db(tgb, max(0.5, p.get("attack", 5)/2), p.get("release", 20))
    y = apply_gain_db(x, np.minimum(g, 0))
    y = np.clip(y, -thr, thr)                               # brickwall safety (true LSP is cleaner)
    if p.get("gain-boost", True):                           # EasyEffects default: ON
        y = y/thr
    return y

def multiband_compressor(x, p):
    """Enabled bands split by Linkwitz-Riley-ish crossovers; only bands with compressor-enable are compressed.
    (voice2ee uses it as a 2-band 'desk-tap catcher'.)"""
    splits = [p[f"band{i}"]["split-frequency"] for i in range(1, 8)
              if p.get(f"band{i}", {}).get("enable-band")]
    bands, rest = [], x
    for f in splits:
        sl = signal.butter(2, f, "low", fs=SR, output="sos")
        sh = signal.butter(2, f, "high", fs=SR, output="sos")
        bands.append(signal.sosfilt(sl, signal.sosfilt(sl, rest)))   # Linkwitz-Riley 4: LP + HP sum flat
        rest = signal.sosfilt(sh, signal.sosfilt(sh, rest))
    bands.append(rest)
    out = np.zeros_like(x)
    idx = [0] + [i for i in range(1, 8) if p.get(f"band{i}", {}).get("enable-band")]
    for b, i in zip(bands, idx):
        bp = p[f"band{i}"]
        if bp.get("compressor-enable") and not bp.get("mute"):
            from scipy.ndimage import maximum_filter1d
            la = int(bp.get("sidechain-lookahead", 0) * SR / 1000)
            d = np.concatenate([b[la:], np.zeros(la)]) if la > 0 else b
            if bp.get("sidechain-mode") == "Peak":
                env = dbv(maximum_filter1d(np.abs(d), size=max(1, int(bp.get("sidechain-reactivity", 5) * SR / 4000))))
                L = np.array([e.max() for e in env[: len(env) // BLK * BLK].reshape(-1, BLK)])
            else:
                L = blocks(env_rms_db(d, bp.get("sidechain-reactivity", 10)))
            r, thr = bp["ratio"], bp["attack-threshold"]
            tgt = -np.maximum(L - thr, 0) * (1 - 1 / r)
            g = smooth_db(tgt, bp.get("attack-time", 10), bp.get("release-time", 100))
            b = apply_gain_db(b, g + bp.get("makeup", 0))
        out += b
    return out

def identity(x, p):
    return x

PLUG = {"gate": gate, "expander": expander, "equalizer": equalizer, "compressor": compressor,
        "deesser": deesser, "exciter": exciter, "limiter": limiter,
        "multiband_compressor": multiband_compressor,
        "deepfilternet": identity, "stereo_tools": identity, "level_meter": identity}  # not modelled

def process(x, preset):
    """x: mono (n,) or stereo (n, 2); processed per channel-sum mono and returned in the same shape."""
    x = np.asarray(x)
    if x.ndim == 2:
        y = process(x.mean(axis=1), preset)
        return np.stack([y, y], axis=1)
    inp = preset["input"]; y = x.astype(np.float64)
    for k in inp.get("plugins_order", []):
        p = inp.get(k, {}); kind = k.split("#")[0]
        if not isinstance(p, dict) or p.get("bypass", False): continue
        y = y*10**(p.get("input-gain", 0)/20)
        if kind in PLUG: y = PLUG[kind](y, p)
        y = y*10**(p.get("output-gain", 0)/20)
    return y.astype(np.float32)
