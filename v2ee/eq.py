"""EQ band layout and helpers."""
import numpy as np

from .config import MAX_BANDS, clip


def glim(f):
    """Allowed gain range (dB) for a band at frequency f."""
    if f < 200:
        return (-6, 4)
    if f < 700:
        return (-8, 2)
    if f < 2200:
        return (-4, 4)
    if f < 6000:
        return (-4, 6)
    return (-4, 5)


def bias_default(f):
    return 1.5 if 2200 <= f <= 4500 else 0.0  # sit a touch above typical in the presence range


def make_layout(n):
    """n = total EQ bands incl. the high-pass. 6 = the classic hand-picked set; more = log-spaced bells."""
    n = int(clip(n, 6, MAX_BANDS))
    if n == 6:
        spec = [(150, 100, 200, 0.8, "Bell"), (320, 250, 400, 1.0, "Bell"), (500, 400, 630, 1.0, "Bell"),
                (3000, 2500, 4000, 0.9, "Bell"), (10000, 8000, 12000, 0.7, "Hi-shelf")]
    else:
        fs = np.geomspace(110, 8000, n - 2)
        bw = float(np.log2(fs[1] / fs[0]))
        q = 2 ** (bw / 2) / (2 ** bw - 1)
        spec = [(f, f / 2 ** (bw / 2), f * 2 ** (bw / 2), q, "Bell") for f in fs]
        spec.append((10000, 8000, 12000, 0.7, "Hi-shelf"))
    return [{"f": round(float(f), 1), "lo": float(lo), "hi": float(hi), "q": round(float(q), 3), "t": t,
             "g": 0.0, "b": bias_default(f)} for f, lo, hi, q, t in spec]


def band_resid(a, b):
    c = a["centers"]
    m = (c >= b["lo"]) & (c <= b["hi"])
    if m.any():
        return float(a["diff"][m].mean())
    return float(np.interp(np.log2(b["f"]), np.log2(c), a["diff"]))


def relayout(P, n):
    """Change the number of EQ bands, carrying the current curve and biases over."""
    old = sorted(P["eq"]["bands"], key=lambda x: x["f"])
    lf = np.log2([x["f"] for x in old])
    new = make_layout(n)
    for x in new:
        x["g"] = float(np.interp(np.log2(x["f"]), lf, [o["g"] for o in old]))
        x["b"] = float(np.interp(np.log2(x["f"]), lf, [o["b"] for o in old]))
    P["eq"]["bands"] = new


GRID = np.geomspace(40, 16000, 90)          # where curves are compared
SPECIAL_Q = 4.0                              # bells narrower than this are deliberate notches/resonance fixes


def curve(bands, grid=GRID):
    """Combined response (dB) of bell/shelf bands at the frequencies in grid."""
    from scipy import signal
    from .simchain import SR, rbj
    out = np.zeros(len(grid))
    for b in bands:
        c = rbj(b["t"], float(b["f"]), float(b["g"]), float(max(b["q"], 0.05)))
        if c is None or abs(b["g"]) < 1e-6:
            continue
        _, h = signal.freqz(c[0], c[1], worN=grid, fs=SR)
        out += 20 * np.log10(np.abs(h) + 1e-12)
    return out


def relayout_imported(P, n_regular):
    """Existing preset: re-spread its ordinary bells/shelves over n_regular new bands that reproduce the same
    curve (least-squares fit), keeping narrow bells (notches), filters and the high-pass exactly as they were.
    Returns (n_old, n_new, max_error_dB at 1/3-octave resolution, n_kept)."""
    E = P["eq"]
    regs = [b for b in E["bands"] if not b.get("special")]
    keep = [b for b in E["bands"] if b.get("special")]
    target = curve(regs)
    new = make_layout(n_regular + 1)          # make_layout counts the high-pass
    lf = np.log2([b["f"] for b in regs]) if regs else None
    for b in new:
        b["b"] = float(np.interp(np.log2(b["f"]), lf, [o["b"] for o in regs])) if regs else b["b"]
    A = np.stack([curve([dict(b, g=1.0)]) for b in new], axis=1)      # dB per dB of gain (linearised)
    # reproducing YOUR curve may need a little more than the tuner's own limits (narrower bands sit deeper)
    lo = np.array([min(glim(b["f"])[0], -12.0) for b in new])
    hi = np.array([max(glim(b["f"])[1], 12.0) for b in new])
    g = np.zeros(len(new))
    ridge = 0.05 * np.eye(len(new))
    for _ in range(4):                        # Gauss-Newton style: fit, measure the true error, correct
        err = target - curve([dict(b, g=float(x)) for b, x in zip(new, g)])
        g = np.clip(g + np.linalg.solve(A.T @ A + ridge, A.T @ err), lo, hi)
    for b, x in zip(new, g):
        b["g"] = round(float(x), 2)
    # compared at 1/3-octave resolution (what you hear): tiny ripples between band centres don't count
    band = (GRID >= max(80.0, 1.2 * float(E.get("hpf") or 0))) & (GRID <= 12000)   # above your high-pass
    diff = np.convolve(np.pad(target - curve(new), 1, mode="edge"), np.ones(3) / 3, "valid")
    max_err = float(np.max(np.abs(diff[band]))) if regs else 0.0
    E["bands"] = keep + new
    E["relayout"] = True
    return len(regs), len(new), max_err, len(keep)


def shift_range(P, flo, fhi, d):
    bs = [x for x in P["eq"]["bands"] if flo <= x["f"] <= fhi]
    if not bs:
        return
    sc = min(1.0, 2.0 / len(bs))
    for x in bs:
        x["g"] = clip(x["g"] + d * sc, *glim(x["f"]))
        x["b"] = clip(x["b"] + d * sc, -8, 8)
