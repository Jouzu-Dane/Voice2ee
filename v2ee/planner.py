"""Turns a raw-mic analysis into the first preset's parameters (the dict 'P' that tuning refines)."""
import numpy as np

from .analysis import bmean
from .config import (DEESS_FLOOR, DEFAULT_KEYS, LEVEL_HI, LEVEL_LO, START_HEADROOM, TARGET_LUFS, clip)
from .eq import band_resid, glim, make_layout


def finalize_mods(m):
    m["comp2"] = m["comp"]
    m["deess2"] = m["deess"]
    return m


def sim_chain(L, crest, c1, c2):
    """Static model on 50 ms frames (fallback when no 10 ms data is cached)."""
    L1 = L - np.maximum(L - c1["thr"], 0) * (1 - 1 / c1["ratio"]) + c1["makeup"]
    L2 = L1 - np.maximum(L1 - c2["thr"], 0) * (1 - 1 / c2["ratio"])
    return L1, L2, L2 + crest


def _smooth(x, att_ms, rel_ms, step_ms=10.0):
    aa, ar = np.exp(-step_ms / max(att_ms, 1.0)), np.exp(-step_ms / max(rel_ms, 1.0))
    out, g = np.empty_like(x), 0.0
    for i, t in enumerate(x):
        a = aa if t > g else ar
        g = a * g + (1 - a) * t
        out[i] = g
    return out


def sim_chain10(lv10, pk10, c1, c2):
    """Leveler (RMS) + fast peak compressor on contiguous 10 ms frames.
    Returns 50 ms frame peaks, the peak level going into stage 2, and per-10 ms output levels."""
    gr1 = _smooth(np.maximum(lv10 - c1["thr"], 0) * (1 - 1 / c1["ratio"]), c1["attack"], c1["release"])
    p1 = pk10 - gr1 + c1["makeup"]                       # stage 2 reacts to peaks
    gr2 = _smooth(np.maximum(p1 - c2["thr"], 0) * (1 - 1 / c2["ratio"]), c2["attack"], c2["release"])
    out_pk = p1 - gr2
    m = len(out_pk) // 5
    return out_pk[: m * 5].reshape(m, 5).max(axis=1), p1, (lv10 - gr1 + c1["makeup"] - gr2)


def plan_dynamics(a, extra=0.0, cap=12.0, target_lufs=TARGET_LUFS, headroom=START_HEADROOM):
    """Compressor settings + gain. The gain aims your speech loudness at `target_lufs`, never adds more than
    `cap` dB to your voice, never pushes loud peaks past LEVEL_HI, and a new preset starts `headroom` dB below
    that so tuning walks it up gently."""
    c1 = {"thr": 0.0, "ratio": 2.0, "makeup": 0.0, "attack": 25.0, "release": 300.0}
    c2 = {"thr": 0.0, "ratio": 1.5, "makeup": 0.0, "attack": 2.0, "release": 60.0, "sc_mode": "Peak",
          "lookahead": 2.0}
    span = LEVEL_HI - LEVEL_LO
    lv10, pk10 = np.asarray(a["lv10"], float), np.asarray(a["pk10"], float)
    v50 = np.asarray(a["voiced50"]) > 0.5
    v10 = np.repeat(v50, 5)[: len(lv10)]
    c1["thr"] = round(float(np.percentile(lv10[v10], 50)), 1)

    def run(c1, c2):
        pk50, p1, l2 = sim_chain10(lv10, pk10, c1, c2)
        n = min(len(pk50), len(v50))
        return pk50[:n][v50[:n]], p1, float(np.median((l2 - lv10)[: len(v10)][v10]))

    for r2 in (1.5, 2.0, 2.5, 3.0, 4.0):
        c2["ratio"] = r2
        _, l1, _ = run(c1, {**c2, "thr": 99.0})
        c2["thr"] = float(np.percentile(l1[v10], 85))
        vp, _, _ = run(c1, c2)
        if float(np.percentile(vp, 98) - np.percentile(vp, 5)) <= span + 2.0:
            break
    vp0, _, g0 = run(c1, c2)                                   # zero makeup: peaks and net gain on the voice
    raw_lufs = a.get("lufs", -40.0)
    wanted = max(0.0, target_lufs - (raw_lufs + g0) + extra)  # makeup to reach the loudness target
    room_peaks = max(0.0, LEVEL_HI - float(np.percentile(vp0, 98)))
    room_gain = max(0.0, cap - g0)
    full = min(wanted, room_peaks, room_gain)
    start = max(0.0, full - headroom)
    c1["makeup"] = round(start, 1)
    c2["thr"] = round(c2["thr"] + start, 1)                    # second stage follows the makeup
    net = g0 + start
    return c1, c2, dict(pk5=float(np.percentile(vp0, 5)) + start, pk98=float(np.percentile(vp0, 98)) + start,
                        net_gain=net, wanted=wanted, wanted_net=g0 + wanted, full=full, start=start,
                        est_lufs=raw_lufs + net, limited_by=("peaks" if full == room_peaks < min(wanted, room_gain)
                                                             else "gain cap" if full == room_gain < wanted else None))


def initial_params(a, tw):
    """Raw-mic analysis -> parameter set P (single source of truth that gets tuned later)."""
    mods = dict(tw.mods)
    target = float(getattr(tw, "target_lufs", TARGET_LUFS))
    notes = []

    # noise stage threshold (shared by gate and expander)
    lo = a.get("speech_lo30", a["speech_db"] - 8)
    ch = a.get("click_hi30")
    floor_thr = a["noise_floor"] + 15.0
    if ch is not None:
        gap = lo - ch
        thr = ch + (0.4 * gap if gap >= 4 else gap / 2)
        gnote = (f"between clicks ({ch:.1f} dB) and quiet speech ({lo:.1f} dB)" if gap >= 4 else
                 f"!! clicks ({ch:.1f} dB) are nearly as loud as quiet speech ({lo:.1f} dB): a level-based stage can't "
                 "separate them cleanly. Move keyboard/mouse away or lean on RNNoise VAD.")
    else:
        thr = floor_thr
        gnote = "set from noise floor only (no click test)"
    thr = clip(min(max(thr, floor_thr), lo - 1.0) + tw.gate_offset, -70, -10)
    gate = {"thr": round(thr, 1), "max": round(min(thr + 8, -10), 1), "release": 150.0, "ratio": 3.0}
    if mods["noise"]:
        notes.append(f"{mods['noise'].capitalize()} threshold {thr:.1f} dB - {gnote}")

    # EQ
    bands = make_layout(tw.bands)
    for bd in bands:
        f, r = bd["f"], band_resid(a, bd)
        fac = 0.5 if bd["t"] == "Hi-shelf" else 0.8 if 220 <= f <= 650 else 0.6
        w = 1.0 if 220 <= f < 420 else 0.6 if 420 <= f <= 650 else 0.0
        g = -fac * (r - bd["b"]) - w * tw.box_cut
        if f >= 8000 and a["snr"] < 40:
            g = min(g, 0.0)
        bd["g"] = clip(g, *glim(f))
    eq = {"hpf": round(clip(a["f0"] * 0.85, 70, 120)), "bands": bands}
    if mods["eq"]:
        big = sorted(bands, key=lambda x: -abs(x["g"]))[:4]
        notes.append(f"EQ: HPF {eq['hpf']} Hz + {len(bands)} bands (f0 ~ {a['f0']:.0f}); biggest moves: "
                     + ", ".join(f"{x['f']:.0f} Hz {x['g']:+.1f} dB" for x in big))

    # dynamics
    c1, c2, info = plan_dynamics(a, tw.makeup, tw.max_gain, target)
    if mods["comp"]:
        notes.append(f"Leveler {c1['ratio']:g}:1 @ {c1['thr']:.1f} dB, fast compressor {c2['ratio']:g}:1 @ "
                     f"{c2['thr']:.1f} dB")
        notes.append(f"Loudness: your raw speech is {a['lufs']:.1f} LUFS; this preset starts around "
                     f"{info['est_lufs']:.1f} LUFS ({info['net_gain']:+.1f} dB, {START_HEADROOM:g} dB short of the "
                     f"target on purpose); target {target:.0f} LUFS, never more than +{tw.max_gain:.0f} dB")
        if info["wanted_net"] > tw.max_gain + 0.5:
            notes.append(f"Reaching {target:.0f} LUFS would need about {info['wanted_net']:+.0f} dB on your voice. "
                         f"Raise the mic's own gain by ~{info['wanted_net'] - tw.max_gain:.0f} dB (or move closer) "
                         "rather than allowing more here.")
    net = info["net_gain"] if mods["comp"] else 0.0

    # de-essers: both stay ENABLED whenever you pick them. The narrow one targets a "snake S" spike; if your
    # sibilance has no narrow spike it gets gentle settings so it only touches the worst "s" sounds.
    sd_ = a["sib_diff"]
    pres_g = max([x["g"] for x in bands if 2200 <= x["f"] <= 4500] or [0.0]) if mods["eq"] else 0.0
    gain_pre = net + pres_g * 0.5
    d_ratio = 1 + ((3.0 if sd_ > 4 else 2.0 if sd_ > 1.5 else 1.5) - 1) * tw.deess
    de_thr = clip(float(np.percentile(a["sib_lv"], 90)) + gain_pre, DEESS_FLOOR, -3)
    snake = a.get("sib_peaky", 0.0) > 6.0
    d2_thr = clip(float(np.percentile(a["sib_lv"], 95)) + gain_pre + (0.0 if snake else 3.0), DEESS_FLOOR, -3)
    d2_ratio = 1 + ((4.0 if snake else 2.0) - 1) * tw.deess
    on = tw.deess > 0
    if mods["deess"]:
        notes.append(f"De-esser (wide): thr {de_thr:.1f} dB, ratio {d_ratio:.1f}; narrow de-esser at "
                     f"{a['sib_peak']:.0f} Hz: " + ("'snake S' spike found - ratio %.1f" % d2_ratio if snake else
                                                   "no narrow spike - gentle, only the worst 's' sounds"))

    # exciter
    dull = bmean(a, 8000, 12000) < -2.0
    ex = {"amount": 2.0 if dull else 1.0, "harmonics": 2.5, "scope": 4000.0, "ceil": 14000.0, "blend": 3.0}
    if mods["exciter"]:
        notes.append(f"Exciter {ex['amount']:.1f} dB above {ex['scope']:.0f} Hz (placed before the de-essers)")
    if mods["rnnoise"]:
        notes.append(f"RNNoise on, VAD {'on at %g%%' % tw.vad_thres if not tw.no_vad else 'off'}")

    P = {
        "modules": mods, "keys": dict(DEFAULT_KEYS), "imported": False,
        "gate": gate,
        "rnnoise": {"on": mods["rnnoise"], "vad": not tw.no_vad, "vad_thres": float(tw.vad_thres)},
        "eq": eq, "comp": c1, "comp2": c2,
        "deess": {"on": on, "thr": round(de_thr, 1), "ratio": round(d_ratio, 2), "freq": round(a["sib_peak"])},
        "deess2": {"on": on, "thr": round(d2_thr, 1), "ratio": round(d2_ratio, 2), "freq": round(a["sib_peak"])},
        "exciter": ex, "limiter": LEVEL_HI,
        "bias": {"sib": 0.0, "level": 0.0}, "max_gain": float(tw.max_gain), "target_lufs": target,
    }
    if mods["comp"] and not mods["limiter"]:
        notes.append("!! Compressors without a limiter: nothing stops sudden loud peaks. The limiter is recommended.")
    return P, notes
