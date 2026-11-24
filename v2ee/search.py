"""Step 2: try many variations of a preset on your recording and keep the best one.

- SPACE: which settings may move, and how far, around the planner's first guess (only modules you picked)
- overrides: your own locks and ranges (~/.config/voice2ee/overrides.toml or .json), respected everywhere
- optimise(): a small evolution strategy - sample variations around the best so far, keep what scores better,
  search wider while it keeps finding improvements and narrower when it doesn't. No extra libraries: each try
  takes well under a second, so plain trial-and-error with a few hundred tries beats fancier methods here."""
import copy
import json

import numpy as np

from .config import CONFIG_DIR, clip
from .eq import glim

EQ_GROUPS = {"low": (60, 180), "mud": (180, 500), "box": (500, 1200), "presence": (2000, 5000), "air": (6000, 14000)}


def get(P, path):
    if path.startswith("eqg."):
        return P.get("_eqg", {}).get(path[4:], 0.0)
    cur = P
    for part in path.split("."):
        cur = cur[part]
    return cur


def put(P, path, value):
    if path.startswith("eqg."):
        P.setdefault("_eqg", {})[path[4:]] = float(value)
        return
    parts = path.split(".")
    cur = P
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = float(value) if isinstance(cur.get(parts[-1], 0.0), (int, float)) else value


def space(P):
    """[(path, lo, hi)] for the modules in this preset."""
    m, d = P["modules"], []

    def rel(path, below, above, lo=-90.0, hi=0.0):
        x = get(P, path)
        d.append((path, max(lo, x - below), min(hi, x + above)))

    if m.get("noise"):
        rel("gate.thr", 6, 6, -80, -10)
        d.append(("gate.release", 30.0, 300.0))
        if m["noise"] in ("expander", "both"):
            d.append(("gate.ratio", 1.5, 4.0))
        if m["noise"] == "both":
            rel("floor.thr", 6, 4, -85, -20)
    if m.get("deep"):
        d.append(("deep.atten", 10.0, 60.0))
    if m.get("taps"):
        rel("taps.thr", 6, 4, -70, -6)
        d.append(("taps.ratio", 4.0, 20.0))
    if m.get("eq"):
        d.append(("eq.hpf", 60.0, 140.0))
        d += [(f"eqg.{g}", -4.0, 4.0) for g in EQ_GROUPS]
    if m.get("comp"):
        rel("comp.thr", 6, 6, -70, -6)
        d.append(("comp.ratio", 1.2, 4.0))
        d.append(("comp.makeup", 0.0, float(P.get("max_gain", 12.0))))
        d.append(("comp.release", 100.0, 600.0))
        if m.get("comp2"):
            rel("comp2.thr", 6, 6, -60, 0)
            d.append(("comp2.ratio", 1.5, 6.0))
    if m.get("deess"):
        rel("deess.thr", 8, 6, -60, -3)
        d.append(("deess.ratio", 1.2, 5.0))
        if m.get("deess2"):
            rel("deess2.thr", 8, 6, -60, -3)
            d.append(("deess2.ratio", 1.2, 6.0))
    if m.get("exciter"):
        d.append(("exciter.amount", 0.0, 4.0))
        d.append(("exciter.scope", 3000.0, 10000.0))
    return d


def apply_eq_groups(P, base):
    """EQ group offsets ('_eqg') on top of the base preset's band gains."""
    off = P.get("_eqg") or {}
    if not off or not P["modules"].get("eq"):
        return P
    for bd, b0 in zip(P["eq"]["bands"], base["eq"]["bands"]):
        g = b0["g"]
        for name, (lo, hi) in EQ_GROUPS.items():
            if lo <= bd["f"] < hi:
                g += off.get(name, 0.0)
        bd["g"] = round(clip(g, *glim(bd["f"])), 2)
    return P


# ------------------------------------------------------------------------------------------- overrides
def overrides_file():
    for name in ("overrides.toml", "overrides.json"):
        f = CONFIG_DIR / name
        if f.exists():
            return f
    return None


def load_overrides():
    """{'lock': {path: value}, 'range': {path: [lo, hi]}} - from overrides.toml (Python 3.11+) or .json.
    Paths look like 'limiter', 'gate.release', 'comp.ratio', 'eq.hpf' (see README)."""
    f = overrides_file()
    if f is None:
        return {"lock": {}, "range": {}}
    try:
        if f.suffix == ".toml":
            import tomllib
            data = tomllib.loads(f.read_text())
        else:
            data = json.loads(f.read_text())
    except Exception as ex:
        print(f"!! Couldn't read {f}: {ex} - ignoring it")
        return {"lock": {}, "range": {}}
    return {"lock": dict(data.get("lock", {})), "range": {k: list(v) for k, v in data.get("range", {}).items()}}


def apply_overrides(P, ov=None):
    """Force locks and clamp ranges. Returns the list of what it changed (for the round notes)."""
    ov = ov if ov is not None else load_overrides()
    msgs = []
    for path, val in ov.get("lock", {}).items():
        try:
            if get(P, path) != val:
                put(P, path, val)
                msgs.append(f"{path} locked at {val}")
        except (KeyError, TypeError):
            pass
    for path, (lo, hi) in ov.get("range", {}).items():
        try:
            v = get(P, path)
            if not lo <= v <= hi:
                put(P, path, clip(v, lo, hi))
                msgs.append(f"{path} kept within {lo}..{hi}")
        except (KeyError, TypeError):
            pass
    return msgs


def constrained_space(P, ov=None):
    ov = ov if ov is not None else load_overrides()
    out = []
    for path, lo, hi in space(P):
        if path in ov.get("lock", {}):
            continue
        if path in ov.get("range", {}):
            rlo, rhi = ov["range"][path]
            lo, hi = max(lo, rlo), min(hi, rhi)
            if hi <= lo:
                continue
        out.append((path, lo, hi))
    return out


# ------------------------------------------------------------------------------------------- optimiser
def optimise(P0, evaluate, trials=200, seed=0, progress=None, ov=None):
    """evaluate(P) -> (score, parts, analysis). Returns (best_P, best_score, best_analysis, n_tried)."""
    rng = np.random.default_rng(seed)
    base = copy.deepcopy(P0)
    dims = constrained_space(base, ov)
    lo = np.array([d[1] for d in dims]); hi = np.array([d[2] for d in dims])
    span = np.maximum(hi - lo, 1e-9)

    def make(u):
        P = copy.deepcopy(base)
        for (path, _, _), v in zip(dims, lo + np.clip(u, 0, 1) * span):
            put(P, path, round(float(v), 2))
        apply_eq_groups(P, base)
        apply_overrides(P, ov)
        return P

    u0 = np.array([(get(base, p) - l) / max(h - l, 1e-9) for p, l, h in dims]) if dims else np.zeros(0)
    u0 = np.clip(u0, 0, 1)
    best_u = u0
    s0, parts0, a0 = evaluate(make(u0))
    best = (s0, a0)
    tried, sigma, lam = 1, 0.18, 6
    if progress:
        progress(tried, trials, s0, s0, a0)
    if not dims:
        return make(u0), s0, a0, tried
    mean = u0.copy()
    while tried < trials:
        gen = []
        for _ in range(min(lam, trials - tried)):
            u = np.clip(mean + sigma * rng.standard_normal(len(dims)), 0, 1)
            s, _, a = evaluate(make(u))
            tried += 1
            gen.append((s, u, a))
        gen.sort(key=lambda t: t[0])
        if gen[0][0] < best[0]:
            best, best_u = (gen[0][0], gen[0][2]), gen[0][1]
            sigma = min(0.35, sigma * 1.15)
        else:
            sigma = max(0.02, sigma * 0.8)
        second = gen[0][1] if len(gen) else best_u
        mean = 0.7 * best_u + 0.3 * second
        if progress:
            progress(tried, trials, best[0], s0, best[1])
    return make(best_u), best[0], best[1], tried
