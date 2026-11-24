"""Turns a raw-mic analysis into the first preset's parameters (the dict 'P' that tuning refines)."""
import numpy as np

from .analysis import bmean
from .config import (DEESS_FLOOR, DEFAULT_KEYS, DEFAULT_MODULES, LOW_SPLIT, PROFILES, START_HEADROOM, clip)
from .eq import band_resid, glim, make_layout


def finalize_mods(m):
    """Fill in anything not chosen with the profile's best default, and derive the second stages."""
    prof_name = m.get("profile") or "stream"
    for k, v in DEFAULT_MODULES[prof_name].items():
        m.setdefault(k, v)
    if m.get("noise") is True:
        m["noise"] = "gate"
    m["comp2"] = bool(m["comp"]) and PROFILES[prof_name]["comp2"]
    m["deess2"] = bool(m["deess"])
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


def derive_timing(a, pname="stream"):
    """Every time constant from how YOU speak, instead of fixed numbers:
    - syllable period (1 / syllable rate): the leveler rides phrases (slower than a syllable), the peak
      compressor and limiter recover between syllables (faster than one)
    - fastest syllable onsets: how quickly gates/expanders must open and how much lookahead catches a peak
    - word tails (word ending + room echo): how long gates/expanders stay open after a word; shorter than
      the tail cuts echo, longer keeps it natural (record)
    - pauses between words: a gate must be able to close inside one
    - knock length: how long the tap catcher holds"""
    style = PROFILES[pname]["timing"]
    rec = style == "natural"
    rate = a.get("syl_rate") or 5.0
    T = 1000.0 / rate                                  # ms per syllable
    fast = a.get("rise_fast_ms") or 15.0
    decay = a.get("decay_ms") or 80.0
    gap = a.get("gap_ms") or 150.0
    t = {"T": T, "decay": decay, "gap": gap, "measured": a.get("syl_rate") is not None}
    t["lev_att"] = round(clip(0.15 * T, 10, 40), 1)
    t["lev_rel"] = round(clip(1.6 * T * (1.3 if rec else 1.0), 150, 600))
    t["pk_att"] = round(clip(0.15 * fast, 0.5, 5), 1)
    t["pk_rel"] = round(clip(0.3 * T, 30, 120))
    t["lim_look"] = round(clip(0.3 * fast, 2, 8), 1)
    t["lim_rel"] = round(clip((0.5 if rec else 0.25) * T, 20, 150))
    t["open"] = round(clip(0.3 * fast, 1.5, 8), 1)        # gate/expander attack
    if rec:
        t["gate_rel"] = round(clip(1.2 * decay, 80, 300))
    elif style == "smooth":                            # videos: close after the word tail, never mid-word
        t["gate_rel"] = round(clip(1.0 * decay, 60, max(80, min(250, 0.9 * gap))))
    else:
        t["gate_rel"] = round(clip(0.8 * decay, 50, max(60, min(250, 0.8 * gap))))
    t["exp_rel"] = round(clip(0.9 * t["gate_rel"], 40, 300))
    t["floor_rel"] = round(clip(1.5 * decay, 100, 300))
    t["rnn_rel"] = round(clip(0.8 * decay, 20, 150))
    t["tap_rel"] = round(clip(0.6 * (a.get("tap_ms") or 130.0), 40, 200))
    return t


def plan_exciter(a, pname="stream"):
    """Where the exciter works, from your voice:
    - dull voice (little 8-12 kHz): start low (~3-4.5 kHz) to add presence
    - bright / sibilant voice: start ABOVE your 's' band so it only adds air and never feeds the 's'
    - otherwise: start where your 's' band begins
    Ceiling: about where streaming audio (AAC) stops (14 kHz) or 16 kHz for lossless recordings; lower when
    the top band is mostly hiss, because the exciter brightens hiss too."""
    dull = bmean(a, 8000, 12000) < -2.0
    sd = a.get("sib_diff", 0.0)
    bright = sd > 1.5 or (a.get("sib_peaky", 0.0) > 6.0 and sd > -3.0)    # a spike only counts if the 's' is there
    lo, hi = a.get("sib_lo", 4500.0), a.get("sib_hi", 8000.0)
    if dull and sd <= 1.5:
        bright = False
    if bright:
        scope, harm, why = clip(hi * 1.1, 7000, 10000), 2.0, f"above your 's' band, {lo:.0f}-{hi:.0f} Hz"
    elif dull:
        scope, harm, why = clip(lo * 0.8, 3000, 4500), 3.0, "low - your voice is dull up top"
    else:
        scope, harm, why = clip(lo, 4000, 6500), 2.5, "where your 's' band starts"
    cap = PROFILES[pname]["exciter_ceil"]
    hiss = a.get("hf_snr", 40.0) < 12.0
    ceil = clip(11000.0 if hiss else cap, scope + 2000, cap)
    ex = {"amount": 2.0 if dull and not bright else 1.0, "harmonics": harm, "scope": round(scope),
          "ceil": round(ceil), "blend": 3.0}
    note = (f"Exciter {ex['amount']:.1f} dB from {ex['scope']:.0f} Hz ({why}) up to {ex['ceil']:.0f} Hz"
            + (" - the top band is mostly hiss, so it stays low" if hiss else ""))
    return ex, note


def plan_dynamics(a, extra=0.0, cap=12.0, target_lufs=-18.0, headroom=START_HEADROOM, lo=-18.0, hi=-6.0,
                  use_c2=True, lev_ratio=2.0, tm=None):
    """Compressor settings + gain. The gain aims your speech loudness at `target_lufs`, never adds more than
    `cap` dB to your voice, never pushes loud peaks past `hi`, and a new preset starts `headroom` dB below
    that so tuning walks it up gently."""
    tm = tm or derive_timing(a)
    c1 = {"thr": 0.0, "ratio": float(lev_ratio), "makeup": 0.0, "attack": tm["lev_att"], "release": tm["lev_rel"]}
    c2 = {"thr": 0.0, "ratio": 1.5, "makeup": 0.0, "attack": tm["pk_att"], "release": tm["pk_rel"], "sc_mode": "Peak",
          "lookahead": 2.0}
    span = hi - lo
    lv10, pk10 = np.asarray(a["lv10"], float), np.asarray(a["pk10"], float)
    v50 = np.asarray(a["voiced50"]) > 0.5
    v10 = np.repeat(v50, 5)[: len(lv10)]
    c1["thr"] = round(float(np.percentile(lv10[v10], 50)), 1)

    def run(c1, c2):
        pk50, p1, l2 = sim_chain10(lv10, pk10, c1, c2 if use_c2 else {**c2, "ratio": 1.0, "thr": 99.0})
        n = min(len(pk50), len(v50))
        return pk50[:n][v50[:n]], p1, float(np.median((l2 - lv10)[: len(v10)][v10]))

    for r2 in ((1.5, 2.0, 2.5, 3.0, 4.0) if use_c2 else (1.0,)):
        c2["ratio"] = r2
        _, l1, _ = run(c1, {**c2, "thr": 99.0})
        c2["thr"] = float(np.percentile(l1[v10], 85))
        vp, _, _ = run(c1, c2)
        if float(np.percentile(vp, 98) - np.percentile(vp, 5)) <= span + 2.0:
            break
    vp0, _, g0 = run(c1, c2)                                   # zero makeup: peaks and net gain on the voice
    raw_lufs = a.get("lufs", -40.0)
    wanted = max(0.0, target_lufs - (raw_lufs + g0) + extra)  # makeup to reach the loudness target
    room_peaks = max(0.0, hi - float(np.percentile(vp0, 98)))
    room_gain = max(0.0, cap - g0)
    full = min(wanted, room_peaks, room_gain)
    start = max(0.0, full - headroom)
    c1["makeup"] = round(start, 1)
    c2["thr"] = round(c2["thr"] + start, 1)                    # second stage follows the makeup
    net = g0 + start
    return c1, c2, dict(pk5=float(np.percentile(vp0, 5)) + start, pk98=float(np.percentile(vp0, 98)) + start,
                        net_gain=net, wanted=wanted, wanted_net=g0 + wanted, full=full, start=start,
                        est_lufs=raw_lufs + net)


def initial_params(a, tw):
    """Raw-mic analysis -> parameter set P (single source of truth that gets tuned later)."""
    mods = finalize_mods(dict(tw.mods))
    pname = mods["profile"]
    pr = PROFILES[pname]
    target = float(tw.target_lufs) if getattr(tw, "target_lufs", None) is not None else pr["target_lufs"]
    notes = [f"Profile: {pname} - {pr['label']}"]
    cap = float(tw.max_gain) if getattr(tw, "max_gain", None) is not None else pr["max_gain"]
    tm = derive_timing(a, pname)
    if tm["measured"]:
        notes.append(f"Your timing: ~{1000 / tm['T']:.1f} syllables/s, word tails ~{tm['decay']:.0f} ms, pauses "
                     f"~{tm['gap']:.0f} ms -> all attack/release times below are set from these")

    # noise stage: calibrated threshold sits between your clicks and your quietest speech
    lo_sp = a.get("speech_lo30", a["speech_db"] - 8)
    ch = a.get("click_hi30")
    floor_thr = a["noise_floor"] + 15.0
    if ch is not None:
        gap = lo_sp - ch
        thr = ch + (0.4 * gap if gap >= 4 else gap / 2)
        gnote = (f"between clicks ({ch:.1f} dB) and quiet speech ({lo_sp:.1f} dB)" if gap >= 4 else
                 f"!! clicks ({ch:.1f} dB) are nearly as loud as quiet speech ({lo_sp:.1f} dB): a level-based stage "
                 "can't separate them cleanly. Move keyboard/mouse away or lean on the noise remover.")
    else:
        thr = floor_thr
        gnote = "set from noise floor only (no click test)"
    thr = clip(min(max(thr, floor_thr), lo_sp - 1.0) + tw.gate_offset, -70, -10)
    noise = mods["noise"]
    gate = {"thr": round(thr, 1), "max": round(min(thr + 8, -10), 1), "attack": tm["open"],
            "release": float(tm["gate_rel"] if noise == "gate" else tm["exp_rel"]),
            "ratio": pr["expander_ratio"] if noise in ("expander", "both") else 3.0,
            "reduction": pr["gate_reduction"]}
    # with gate + expander: the expander works at the calibrated threshold (soft: clicks, breaths, room tails),
    # the gate is a deep floor cut just above your room noise that only acts in real silence
    floor = {"thr": round(clip(min(a["noise_floor"] + 8.0, thr - 6.0), -80, -20), 1),
             "reduction": pr["floor_reduction"], "release": float(tm["floor_rel"])}
    if noise == "both":
        notes.append(f"Expander {gate['ratio']:g}:1 at {thr:.1f} dB - {gnote}; gate floor at {floor['thr']:.1f} dB "
                     f"({floor['reduction']:.0f} dB in silence); opens in {gate['attack']:g} ms, expander release "
                     f"{gate['release']:.0f} ms, gate release {floor['release']:.0f} ms")
    elif noise:
        notes.append(f"{noise.capitalize()} threshold {thr:.1f} dB - {gnote}; opens in {gate['attack']:g} ms, "
                     f"release {gate['release']:.0f} ms")

    # desk-tap catcher: compress ONLY the band below ~150 Hz, and only above your voice's own loudest bass
    taps = {"split": LOW_SPLIT, "ratio": 8.0, "attack": 1.0, "release": float(tm["tap_rel"]), "thr": -30.0}
    lv_hi, tp_hi = a.get("low_voice_hi"), a.get("tap_low_hi")
    if lv_hi is not None:
        taps["thr"] = round(clip(lv_hi + 1.0, -70, -6), 1)     # just above your voice's own loudest bass peaks
    taps["thr0"] = taps["thr"]
    if mods["taps"]:
        if tp_hi is not None and lv_hi is not None:
            gap = tp_hi - lv_hi
            notes.append(f"Desk-tap catcher: below {taps['split']:.0f} Hz, {taps['ratio']:g}:1 above {taps['thr']:.1f} dB"
                         f" - your knocks peak {gap:+.0f} dB above your voice's loudest bass"
                         + ("" if gap > 3 else " (close: taps this soft are mostly harmless)"))
        else:
            notes.append(f"Desk-tap catcher: below {taps['split']:.0f} Hz above {taps['thr']:.1f} dB "
                         "(no knock test - set from your voice alone)")

    # EQ
    bands = make_layout(tw.bands)
    for bd in bands:
        f, r = bd["f"], band_resid(a, bd)
        fac = 0.5 if bd["t"] == "Hi-shelf" else 0.8 if 220 <= f <= 650 else 0.6
        w = 1.0 if 220 <= f < 420 else 0.6 if 420 <= f <= 650 else 0.0
        g = -fac * (r - bd["b"]) - w * tw.box_cut
        if f >= 8000 and a["snr"] < 40:
            g = min(g, 0.0)
        g *= pr["eq_strength"]                        # record: corrective only, the rest is left for the edit
        bd["g"] = clip(g, *glim(f))
    # high-pass: frequency from your pitch; slope from how much rumble and how many pops there are
    rumble = a.get("rumble_db", -60.0) > -32.0
    pops = a.get("pop_db", 0.0) > 18.0
    hpf = clip(a["f0"] * 0.85 + (10 if rumble else 0), 70, 130)
    slope = "x4" if (rumble or pops) else "x3"
    eq = {"hpf": round(hpf), "hpf_slope": slope, "bands": bands}
    if mods["eq"]:
        big = sorted(bands, key=lambda x: -abs(x["g"]))[:4]
        why = " + ".join(w for w, on in (("room rumble", rumble), ("strong pops", pops)) if on) or "little rumble/pops"
        notes.append(f"High-pass {eq['hpf']} Hz at {6 * int(slope[1:])} dB/oct ({why})")
        notes.append(f"EQ: HPF {eq['hpf']} Hz + {len(bands)} bands (f0 ~ {a['f0']:.0f}); biggest moves: "
                     + ", ".join(f"{x['f']:.0f} Hz {x['g']:+.1f} dB" for x in big))

    # dynamics
    c1, c2, info = plan_dynamics(a, tw.makeup, cap, target, lo=pr["peak_lo"], hi=pr["peak_hi"],
                                 use_c2=mods["comp2"], lev_ratio=pr["leveler_ratio"], tm=tm)
    if mods["comp"]:
        notes.append(f"Leveler {c1['ratio']:g}:1 @ {c1['thr']:.1f} dB, {c1['attack']:g}/{c1['release']:g} ms" +
                     (f", fast peak compressor {c2['ratio']:g}:1 @ {c2['thr']:.1f} dB, {c2['attack']:g}/"
                      f"{c2['release']:g} ms" if mods["comp2"] else
                      " (no peak squashing - recording keeps its dynamics)"))
        notes.append(f"Loudness: your raw speech is {a['lufs']:.1f} LUFS; this preset starts around "
                     f"{info['est_lufs']:.1f} LUFS ({info['net_gain']:+.1f} dB, {START_HEADROOM:g} dB short on purpose); "
                     f"target {target:.0f} LUFS, peaks under {pr['peak_hi']:.0f} dBFS, never more than "
                     f"+{cap:.0f} dB")
        if info["wanted_net"] > cap + 0.5:
            notes.append(f"Reaching {target:.0f} LUFS would need about {info['wanted_net']:+.0f} dB on your voice. "
                         f"Raise the mic's own gain by ~{info['wanted_net'] - cap:.0f} dB (or move closer) "
                         "rather than allowing more here.")
    net = info["net_gain"] if mods["comp"] else 0.0

    # de-essers: both stay ENABLED whenever you pick them. The narrow one targets a "snake S" spike; if your
    # sibilance has no narrow spike it gets gentle settings so it only touches the worst "s" sounds.
    sd_ = a["sib_diff"]
    pres_g = max([x["g"] for x in bands if 2200 <= x["f"] <= 4500] or [0.0]) if mods["eq"] else 0.0
    gain_pre = net + pres_g * 0.5
    strength = tw.deess * pr["deess_strength"]
    d_ratio = 1 + ((3.0 if sd_ > 4 else 2.0 if sd_ > 1.5 else 1.5) - 1) * strength
    de_thr = clip(float(np.percentile(a["sib_lv"], 90)) + gain_pre, DEESS_FLOOR, -3)
    snake = a.get("sib_peaky", 0.0) > 6.0
    s_lo, s_hi = a.get("sib_lo", a["sib_peak"] * 0.85), a.get("sib_hi", a["sib_peak"] * 1.15)
    narrow_q = round(clip(a["sib_peak"] / max(s_hi - s_lo, 200.0), 1.5, 8.0), 2)
    d2_thr = clip(float(np.percentile(a["sib_lv"], 95)) + gain_pre + (0.0 if snake else 3.0), DEESS_FLOOR, -3)
    d2_ratio = 1 + ((4.0 if snake else 2.0) - 1) * strength
    on = tw.deess > 0
    if mods["deess"]:
        notes.append(f"De-esser (wide): thr {de_thr:.1f} dB, ratio {d_ratio:.1f}; narrow de-esser at "
                     f"{a['sib_peak']:.0f} Hz (Q {narrow_q:g}, your 's' band {s_lo:.0f}-{s_hi:.0f} Hz): "
                     + ("'snake S' spike found - ratio %.1f" % d2_ratio if snake else
                        "no narrow spike - gentle, only the worst 's' sounds"))

    # exciter
    ex, ex_note = plan_exciter(a, pname)
    if mods["exciter"]:
        notes.append(ex_note)

    # noise removers
    vad = (not tw.no_vad) and pr["vad"] and not mods["deep"]   # VAD gating on top of DeepFilterNet = double gating
    if mods["rnnoise"]:
        notes.append(f"RNNoise on, VAD {'on at %g%%, release %d ms' % (tw.vad_thres, tm['rnn_rel']) if vad else 'off'}")
    deep = {"atten": pr["deep_atten"]}
    if mods["deep"]:
        notes.append(f"Deep noise remover (DeepFilterNet) on, attenuation limit {deep['atten']:.0f} dB"
                     + (" (light - natural for recordings)" if pname == "record" else ""))

    # mono mic seen as left-only (or right-only) on the EasyEffects mic -> Stereo Tools copies it to both sides
    stereo_fix = None
    if mods["stereo"] == "auto" and a.get("imbalance", 0) > 10 and a.get("loud_ch") is not None:
        stereo_fix = "LL" if a["loud_ch"] == 0 else "RR"
        notes.append(f"Your mic only reaches the {'left' if stereo_fix == 'LL' else 'right'} channel - "
                     "Stereo Tools copies it to both sides")
    if mods["meter"]:
        notes.append("Level meter at the end of the chain (watch loudness live in EasyEffects)")

    P = {
        "modules": mods, "keys": dict(DEFAULT_KEYS), "imported": False, "profile": pname,
        "gate": gate, "floor": floor, "taps": taps, "deep": deep, "stereo_fix": stereo_fix,
        "rnnoise": {"on": mods["rnnoise"], "vad": vad, "vad_thres": float(tw.vad_thres),
                    "release": float(tm["rnn_rel"])},
        "eq": eq, "comp": c1, "comp2": c2,
        "deess": {"on": on, "thr": round(de_thr, 1), "ratio": round(d_ratio, 2), "freq": round(a["sib_peak"]),
                  "split": round(clip(s_lo * 0.85, 2500, a["sib_peak"] * 0.9))},
        "deess2": {"on": on, "thr": round(d2_thr, 1), "ratio": round(d2_ratio, 2), "freq": round(a["sib_peak"]),
                   "q": narrow_q, "split": round(clip(s_lo * 0.95, 2500, a["sib_peak"] * 0.95))},
        "exciter": ex, "limiter": pr["limiter"],
        "lim_t": {"lookahead": tm["lim_look"], "attack": tm["lim_look"], "release": float(tm["lim_rel"])},
        "timing": {k: v for k, v in tm.items() if k != "measured"},
        "bias": {"sib": 0.0, "level": 0.0}, "max_gain": cap, "target_lufs": target,
    }
    if mods["limiter"]:
        notes.append(f"Limiter {pr['limiter']:g} dBFS, lookahead/attack {tm['lim_look']:g} ms (your fastest onsets), "
                     f"release {tm['lim_rel']:g} ms")
    if mods["comp"] and not mods["limiter"]:
        notes.append("!! Compressors without a limiter: nothing stops sudden loud peaks. The limiter is recommended.")
    return P, notes
