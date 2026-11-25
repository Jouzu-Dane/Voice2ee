"""Step 3: A/B picks. You hear two versions and pick the better one; voice2ee learns where your taste sits on
a few 'taste directions' and moves the preset there. Your taste is saved and reused for future presets.

How it learns: each direction runs from -1 to +1 (0 = the measured preset). Your preferred spot is estimated
with a Bayesian pairwise-preference model (Bradley-Terry with a quadratic utility: the closer a version is to
your spot, the likelier you pick it). Every pick narrows the estimate; the next pair is placed where it's still
least sure, centred on the current best guess, so no pick is wasted."""
import json
import shutil
import subprocess

import numpy as np

from . import loudness
from .config import CACHE_DIR, CONFIG_DIR, SR, clip
from .eq import glim

DIMS = {
    "bright": "brightness (presence 2-6 kHz)",
    "warm":   "warmth (body 120-400 Hz)",
    "polish": "polish (compression: even and dense vs. natural dynamics)",
    "clean":  "cleanliness (noise removal: silent background vs. open and natural)",
    "air":    "air (top end above 7 kHz)",
}
SENS = 3.0                                    # how reliably a difference of 1 in 'distance to your taste' shows
STEP_DB = 4.0                                 # EQ change (dB) for a full step (1.0) along a tone direction


def dims_for(P):
    m = P["modules"]
    d = []
    if m.get("eq"):
        d += ["bright", "warm"]
    if m.get("comp"):
        d.append("polish")
    if m.get("noise") or m.get("deep"):
        d.append("clean")
    if m.get("eq") or m.get("exciter"):
        d.append("air")
    return d


def apply_taste(P, t):
    """A copy of P moved along the taste directions t ({dim: -1..1})."""
    import copy
    P = copy.deepcopy(P)
    m = P["modules"]
    if m.get("eq"):
        for bd in P["eq"]["bands"]:
            f, g = bd["f"], bd["g"]
            g += STEP_DB * t.get("warm", 0) * (120 <= f < 400)
            g += STEP_DB * t.get("bright", 0) * (2000 <= f < 6000)
            g += STEP_DB * t.get("air", 0) * (f >= 7000)
            lo, hi = glim(f)                  # taste may go a bit past the tuner's own limits, so A/B stays audible
            bd["g"] = round(clip(g, lo - 4, hi + 4), 2)
    if m.get("exciter") and "air" in t:
        P["exciter"]["amount"] = round(clip(P["exciter"]["amount"] + t["air"], 0, 6), 2)
    if m.get("comp") and "polish" in t:
        x = t["polish"]
        P["comp"]["ratio"] = round(clip(P["comp"]["ratio"] * (1 + 0.5 * x), 1.1, 6), 2)
        P["comp"]["thr"] = round(P["comp"]["thr"] - 4 * x, 1)
        if m.get("comp2"):
            P["comp2"]["ratio"] = round(clip(P["comp2"]["ratio"] * (1 + 0.3 * x), 1.2, 8), 2)
    if "clean" in t:
        x = t["clean"]
        if m.get("noise"):
            P["gate"]["thr"] = round(min(P["gate"]["thr"] + 5 * x, -10), 1)
            if m["noise"] in ("expander", "both"):
                P["gate"]["ratio"] = round(clip(P["gate"]["ratio"] * (1 + 0.25 * x), 1.2, 6), 2)
        if m.get("deep") and P.get("deep"):
            P["deep"]["atten"] = float(clip(P["deep"]["atten"] + 15 * x, 6, 100))
    return P


class Learner:
    def __init__(self, dims, prior_sd=0.6, start=None):
        self.dims = list(dims)
        self.d = len(self.dims)
        self.s0 = prior_sd
        self.mu = np.array([float((start or {}).get(k, 0.0)) for k in self.dims])
        self.prior_mu = self.mu.copy()
        self.cov = np.eye(self.d) * prior_sd ** 2
        self.data = []                         # (tA, tB, y, weight)

    def next_pair(self, rng):
        """One taste direction at a time (the one it's least sure about), so each pick compares one clear
        difference - big at first, finer as it learns."""
        i = int(np.argmax(np.diag(self.cov)))
        v = np.zeros(self.d)
        v[i] = 1.0
        delta = float(np.clip(1.5 * np.sqrt(self.cov[i, i]), 0.35, 0.8))
        a, b = np.clip(self.mu + delta * v, -1, 1), np.clip(self.mu - delta * v, -1, 1)
        return (a, b) if rng.random() < 0.5 else (b, a)

    def update(self, ta, tb, choice):
        """choice: 'a', 'b', 'same' (can't tell) or 'bad' (both bad - weak 'can't tell')."""
        y, wt = {"a": (1.0, 1.0), "b": (0.0, 1.0), "same": (0.5, 1.0), "bad": (0.5, 0.5)}[choice]
        self.data.append((np.asarray(ta, float), np.asarray(tb, float), y, wt))
        self._fit()

    def _fit(self):
        w = self.mu.copy()
        P0 = np.eye(self.d) / self.s0 ** 2
        for _ in range(25):                     # Newton steps on the MAP objective
            g = P0 @ (w - self.prior_mu)
            H = P0.copy()
            for ta, tb, y, wt in self.data:
                D, c = 2 * (ta - tb), ta @ ta - tb @ tb
                z = SENS * (w @ D - c)
                p = 1 / (1 + np.exp(-z))
                g += wt * SENS * (p - y) * D
                H += wt * SENS ** 2 * p * (1 - p) * np.outer(D, D)
            step = np.linalg.solve(H, g)
            w = np.clip(w - step, -1.5, 1.5)
            if np.max(np.abs(step)) < 1e-4:
                break
        self.mu, self.cov = np.clip(w, -1, 1), np.linalg.inv(H)

    def taste(self):
        return {k: round(float(v), 2) for k, v in zip(self.dims, self.mu)}

    def sure(self):
        return {k: round(float(np.sqrt(self.cov[i, i])), 2) for i, k in enumerate(self.dims)}


# ------------------------------------------------------------------------------------------- saved taste
def taste_file():
    return CONFIG_DIR / "taste.json"


def load_taste():
    try:
        d = json.loads(taste_file().read_text())
        return {k: float(v) for k, v in d.get("taste", {}).items() if k in DIMS}, int(d.get("picks", 0))
    except (OSError, ValueError):
        return {}, 0


def save_taste(t, picks):
    """t is relative to the preset you listened to; the saved taste accumulates (relative to a measured preset)."""
    old, n0 = load_taste()
    merged = dict(old)
    for k, v in t.items():
        merged[k] = round(clip(old.get(k, 0.0) + v, -1, 1), 2)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    taste_file().write_text(json.dumps({"taste": merged, "picks": n0 + picks}, indent=2))


def describe(t):
    words = {"bright": ("darker", "brighter"), "warm": ("leaner", "warmer"), "polish": ("more natural", "more polished"),
             "clean": ("more open", "cleaner"), "air": ("less air", "more air")}
    out = []
    for k, v in t.items():
        if abs(v) >= 0.15:
            out.append(f"{words[k][v > 0]} ({v:+.1f})")
    return ", ".join(out) or "close to the measured preset"


# ------------------------------------------------------------------------------------------- listening
def match_pair(a, b, target=-20.0):
    """Same loudness for both (louder always sounds 'better'), with a common peak guard."""
    out = []
    for x in (a, b):
        lu = loudness.integrated(x)
        out.append(np.asarray(x, np.float32) * 10 ** ((target - lu) / 20) if lu > -70 else np.asarray(x, np.float32))
    pk = max(float(np.abs(x).max()) for x in out)
    if pk > 0.89:                                # -1 dBFS
        out = [x * (0.89 / pk) for x in out]
    return out


def play(x):
    """Play through your normal output (headphones/speakers). Blocks until done."""
    from scipy.io import wavfile
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / "ab_play.wav"
    wavfile.write(f, SR, np.asarray(x, np.float32))
    cmd = (["pw-play", str(f)] if shutil.which("pw-play") else ["paplay", str(f)] if shutil.which("paplay")
           else None)
    if cmd is None:
        raise RuntimeError("no player found (pw-play or paplay)")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=len(x) / SR + 10)


def _most_active(x, secs):
    from .analysis import frames_of, rms_db
    n = int(secs * SR)
    if len(x) <= n:
        return x
    lv = rms_db(frames_of(x, SR // 2))
    k = max(1, int(secs * 2))
    best = int(np.argmax(np.convolve(lv, np.ones(k), "valid")))
    return x[best * SR // 2: best * SR // 2 + n]


def listening_clip(take, free_secs=5.0, read_secs=4.0):
    """What each A/B pick plays: ~5 s of your free talk, ~4 s of the test sentences (the "s" and "p" sounds show
    brightness and air best) and 1.5 s of keyboard noise (so cleanliness can be judged)."""
    sp = np.asarray(take["speech"], np.float32)
    sp = sp.mean(axis=1) if sp.ndim == 2 else sp
    split = take.get("speech_split")
    if not split or not 0 < split < len(sp):              # no marker (older recording): last quarter = sentences
        split = int(len(sp) * 0.75)
    parts = [_most_active(sp[:split], free_secs)]
    if len(sp) - split > SR:
        gap = np.zeros(int(0.25 * SR), np.float32)
        parts += [gap, _most_active(sp[split:], read_secs)]
    if take.get("clicks") is not None:
        c = np.asarray(take["clicks"], np.float32)
        parts.append(np.zeros(int(0.25 * SR), np.float32))
        parts.append((c.mean(axis=1) if c.ndim == 2 else c)[: int(1.5 * SR)])
    return np.concatenate(parts)


def _eq_shift(P, Pv, lo, hi):
    d = [bv["g"] - b["g"] for b, bv in zip(P["eq"]["bands"], Pv["eq"]["bands"]) if lo <= b["f"] < hi]
    return float(np.mean(d)) if d else None


def explain_pair(P, Pa, Pb, dim):
    """What A and B actually were, in plain numbers (shown AFTER you pick, so the test stays blind)."""
    m = P["modules"]
    if dim in ("bright", "warm", "air"):
        lo, hi, name = {"bright": (2000, 6000, "2-6 kHz"), "warm": (120, 400, "120-400 Hz"),
                        "air": (7000, 30000, "7 kHz and up")}[dim]
        parts = []
        if m.get("eq"):
            a, b = _eq_shift(P, Pa, lo, hi), _eq_shift(P, Pb, lo, hi)
            if a is not None:
                parts.append(f"EQ at {name}: A {a:+.1f} dB, B {b:+.1f} dB")
        if dim == "air" and m.get("exciter"):
            parts.append(f"exciter: A {Pa['exciter']['amount']:.1f} dB, B {Pb['exciter']['amount']:.1f} dB")
        return "; ".join(parts)
    if dim == "polish":
        s = (f"leveler: A {Pa['comp']['ratio']:.1f}:1 @ {Pa['comp']['thr']:.1f} dB, "
             f"B {Pb['comp']['ratio']:.1f}:1 @ {Pb['comp']['thr']:.1f} dB")
        if m.get("comp2"):
            s += f"; peak compressor: A {Pa['comp2']['ratio']:.1f}:1, B {Pb['comp2']['ratio']:.1f}:1"
        return s
    if dim == "clean":
        parts = []
        if m.get("noise"):
            stage = "expander" if m["noise"] in ("expander", "both") else "gate"
            parts.append(f"{stage} threshold: A {Pa['gate']['thr']:.1f} dB, B {Pb['gate']['thr']:.1f} dB")
            if m["noise"] in ("expander", "both"):
                parts.append(f"ratio A {Pa['gate']['ratio']:.1f}:1, B {Pb['gate']['ratio']:.1f}:1")
        if m.get("deep") and P.get("deep"):
            parts.append(f"Deep noise remover limit: A {Pa['deep']['atten']:.0f} dB, B {Pb['deep']['atten']:.0f} dB")
        return "; ".join(parts)
    return ""


SIDES = {"bright": ("brighter", "darker"), "warm": ("warmer", "leaner"), "polish": ("more polished", "more natural"),
         "clean": ("cleaner", "more open"), "air": ("airier", "less airy")}


def ab_session(P, render, ask, player=None, rounds=12, seed=None, out=print):
    """render(P_variant) -> mono clip. ask(prompt) -> answer. Returns (taste, picks, learner)."""
    player = player or play
    dims = dims_for(P)
    if not dims:
        out("(Nothing to A/B in this chain - add an EQ, compressor or noise stage.)")
        return {}, 0, None
    L = Learner(dims)                          # relative to the preset as it is now
    rng = np.random.default_rng(seed)
    out(f"\nA/B picks: {rounds} rounds. Each round plays version A, then B (same loudness, random order).\n"
        "  a / b = that one is better   s = can't tell   x = both bad   r = play again   q = stop here")
    picks = 0
    for rnd in range(1, rounds + 1):
        ta, tb = L.next_pair(rng)
        Pa, Pb = apply_taste(P, dict(zip(dims, ta))), apply_taste(P, dict(zip(dims, tb)))
        va, vb = match_pair(render(Pa), render(Pb))
        while True:
            out(f"\nPick {rnd}/{rounds}:  playing A...")
            player(va)
            out("               playing B...")
            player(vb)
            ans = ask("  better? [a/b/s/x/r/q] ").strip().lower()
            if ans in ("a", "b", "s", "x", "q"):
                break
        if ans == "q":
            break
        k = int(np.argmax(np.abs(np.asarray(ta) - np.asarray(tb))))
        dim = dims[k]
        hi_side = "A" if ta[k] > tb[k] else "B"
        more, less = SIDES[dim]
        verdict = {"a": f"you picked the {more if hi_side == 'A' else less} one",
                   "b": f"you picked the {more if hi_side == 'B' else less} one",
                   "s": "you couldn't tell them apart", "x": "neither was right"}[ans]
        out(f"  -> {DIMS[dim].split(' (')[0]}: {hi_side} was {more}. {explain_pair(P, Pa, Pb, dim)}  ({verdict})")
        L.update(ta, tb, {"a": "a", "b": "b", "s": "same", "x": "bad"}[ans])
        picks += 1
    t = L.taste()
    if picks:
        out(f"\nYour taste so far: {describe(t)}")
        sure = L.sure()
        out("  (" + ", ".join(f"{k} {t[k]:+.1f} ±{sure[k]:.1f}" for k in dims) + ")")
    return t, picks, L
