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


def shift_range(P, flo, fhi, d):
    bs = [x for x in P["eq"]["bands"] if flo <= x["f"] <= fhi]
    if not bs:
        return
    sc = min(1.0, 2.0 / len(bs))
    for x in bs:
        x["g"] = clip(x["g"] + d * sc, *glim(x["f"]))
        x["b"] = clip(x["b"] + d * sc, -8, 8)
