"""Per-round tuning: automatic adjustments from a measurement, and the feedback keys."""
import numpy as np

from .analysis import bmean
from .config import (DEESS_FLOOR, HI_SPREAD, LEVEL_HI, LEVEL_LO, MAX_BANDS, MAX_UP_STEP, SAFETY_MARGIN,
                     TARGET_LUFS, clip)
from .eq import band_resid, glim, relayout, shift_range


def target_lufs(P):
    return P.get("target_lufs", TARGET_LUFS) + P["bias"]["level"]


def auto_adjust(P, a):
    msgs = []
    m, e, b = P["modules"], P["eq"], P["bias"]
    cap_cfg = P.get("max_gain", 12.0)
    if a.get("applied_gain") is not None:
        P["last_gain"] = round(float(a["applied_gain"]), 1)
        P["last_gain_hi"] = round(float(a.get("applied_gain_hi", a["applied_gain"])), 1)
    ag_hi, ag = a.get("applied_gain_hi"), a.get("applied_gain")

    # safety first: cut gain if EasyEffects clearly adds more than allowed, or peaks get past the limiter
    reasons, excess = [], 0.0
    if ag is not None and ag > cap_cfg + SAFETY_MARGIN:
        reasons.append(f"EasyEffects adds {ag:+.0f} dB to your voice (allowed {cap_cfg:+.0f})")
        excess = max(excess, ag - cap_cfg)
    if ag_hi is not None and ag_hi > cap_cfg + HI_SPREAD + 3.0:
        reasons.append(f"some syllables get {ag_hi:+.0f} dB")
        excess = max(excess, ag_hi - cap_cfg - HI_SPREAD)
    if m["limiter"] and a["peak_db"] > P["limiter"] + 1.5:
        reasons.append(f"peaks reach {a['peak_db']:.1f} dBFS although the limiter is set to {P['limiter']:.0f}")
        excess = max(excess, a["peak_db"] - P["limiter"])
    if reasons:
        mk0 = P["comp"]["makeup"] if m["comp"] else 0.0
        if m["comp"] and mk0 > 0:
            P["comp"]["makeup"] = round(max(0.0, mk0 - excess), 1)
            msgs.append(f"!! SAFETY: {'; '.join(reasons)} -> makeup {mk0:.1f} -> {P['comp']['makeup']:.1f} dB, "
                        "no gain increases this round")
        else:
            msgs.append(f"!! SAFETY: {'; '.join(reasons)}")
        if any("limiter" in r for r in reasons):
            msgs.append("!! Peaks going past the limiter usually means its 'Threshold Boost' (gain-boost) is on, or a "
                        "plugin after the limiter adds gain. Check the limiter in EasyEffects.")
        elif ag is not None and ag - (mk0 - P["comp"]["makeup"] if m["comp"] else 0) > cap_cfg + SAFETY_MARGIN:
            msgs.append("!! Something else in the chain adds gain the tuner doesn't control. Check in EasyEffects: an "
                        "expander/compressor in Upward or Boosting mode, or raised input/output gain sliders.")
        return msgs

    if m["eq"] and e["bands"]:
        fac = 0.6 if len(e["bands"]) <= 5 else 0.4
        ch = []
        for bd in e["bands"]:
            r = band_resid(a, bd) - bd["b"]
            if abs(r) > 1.5:
                step = clip(-fac * r, -2.5, 2.5)
                if bd["f"] >= 8000 and step > 0 and a["snr"] < 40:
                    continue
                new = clip(bd["g"] + step, *glim(bd["f"]))
                if abs(new - bd["g"]) > 0.05:
                    ch.append(f"{bd['f']:.0f} Hz {bd['g']:+.1f}->{new:+.1f}")
                    bd["g"] = new
        if ch:
            msgs.append("EQ: " + ", ".join(ch[:8]) + (f" (+{len(ch) - 8} more)" if len(ch) > 8 else "") + " dB")

    if m["comp"]:
        lo, hi = LEVEL_LO, LEVEL_HI
        c1 = P["comp"]
        rb = P["comp2"] if m["comp2"] else c1
        p10, p95 = a.get("pkp10", a["pk5"]), a.get("pkp95", a["pk98"])
        tgt = target_lufs(P)
        err = tgt - a["lufs"]
        # 1) range: firm up the compressor only if speech peaks spread clearly wider than the window, gently.
        #    Loosen it only when loudness is already on target (so it doesn't undo step 3 below).
        rng_now = p95 - p10
        if rng_now > (hi - lo) + 3.0 and rb["ratio"] < 6.0:
            new = round(min(6.0, rb["ratio"] * 1.25), 2)
            msgs.append(f"speech peaks spread {rng_now:.0f} dB (window {hi - lo:.0f}) -> compressor "
                        f"{rb['ratio']:.1f}:1 -> {new:.1f}:1")
            rb["ratio"] = new
        elif rng_now < (hi - lo) - 4.0 and rb["ratio"] > 1.5 and err <= 1.0:
            new = round(max(1.5, rb["ratio"] / 1.25), 2)
            msgs.append(f"speech peaks spread only {rng_now:.0f} dB -> compressor {rb['ratio']:.1f}:1 -> {new:.1f}:1")
            rb["ratio"] = new
        # 2) loudness: move speech loudness (LUFS) toward the target, every round
        if abs(err) > 1.0:
            mk = c1["makeup"]
            cap = 30.0 if ag is not None else max(cap_cfg, mk)
            step = clip(0.8 * err, -6.0, MAX_UP_STEP)        # gentle going up; down can be faster
            if a["peak_db"] > LEVEL_HI + 0.5:
                step = min(step, LEVEL_HI - a["peak_db"])    # something got past the ceiling: come down
            limits = {}
            if step > 0:
                # the limiter may lightly catch the loudest syllables: allow raising while under ~10% of speech
                # frames touch the ceiling (within 1 dB); slow down from 5%, stop at 10%
                frac = float(np.mean(np.asarray(a["vpk"]) > LEVEL_HI - 1.0))
                limits["peaks"] = MAX_UP_STEP if frac < 0.05 else 0.75 if frac < 0.10 else 0.0
                if ag is not None:
                    limits["gain"] = max(0.0, cap_cfg - ag)                            # gain on your voice <= cap
                if ag_hi is not None:
                    limits["gain"] = min(limits.get("gain", 99), max(0.0, cap_cfg + HI_SPREAD - ag_hi))
                step = min([step] + list(limits.values()))
            new = round(clip(mk + step, 0, cap), 1)
            peak_bound = err > 1.0 and limits.get("peaks", 99) < 0.5 and limits.get("gain", 99) >= 0.5
            if peak_bound and (rb["ratio"] < 6.0 or rb["thr"] > c1["thr"] + 1.0):
                # 3) loud peaks are (nearly) at the ceiling but the voice is still too quiet: make it denser
                #    (firmer compression, a little at a time) so the next rounds can raise the level safely
                old_r, old_t = rb["ratio"], rb["thr"]
                rb["ratio"] = round(min(6.0, old_r * 1.2), 2)
                rb["thr"] = round(max(c1["thr"] + 1.0, old_t - 1.5), 1)
                msgs.append(f"loudness {a['lufs']:.1f} LUFS is {err:.0f} LU under target and loud peaks are at the "
                            f"{LEVEL_HI:.0f} dBFS ceiling -> denser: compressor {old_r:.1f}:1 -> {rb['ratio']:.1f}:1, "
                            f"threshold {old_t:.1f} -> {rb['thr']:.1f} dB (level rises next round)")
            elif abs(new - mk) >= 0.1:
                c1["makeup"] = new
                if m["comp2"]:
                    P["comp2"]["thr"] = round(P["comp2"]["thr"] + (new - mk), 1)  # stage 2 rides the same level
                for dn in ("deess", "deess2"):                 # de-essers follow, so louder != harsher de-essing
                    P[dn]["thr"] = round(clip(P[dn]["thr"] + (new - mk), DEESS_FLOOR, -3), 1)
                    if "thr0" in P[dn]:
                        P[dn]["thr0"] = round(P[dn]["thr0"] + (new - mk), 1)
                msgs.append(f"loudness {a['lufs']:.1f} LUFS (target {tgt:.0f}) -> makeup {mk:.1f} -> {new:.1f} dB")
            elif err > 0 and limits.get("gain", 99) < 0.1:
                msgs.append(f"loudness is still low but the gain on your voice is at its +{cap_cfg:.0f} dB limit - raise "
                            f"the mic's own gain by about {err:.0f} dB or move closer (or allow more with --max-gain)")
            elif err > 0 and mk >= cap - 0.05:
                msgs.append(f"loudness is still low but makeup is at +{cap:.0f} dB - raise the mic's own gain by about "
                            f"{err:.0f} dB or move closer (or allow more with --max-gain)")
            elif err > 0:
                msgs.append(f"loudness is {err:.0f} LU under target with the compressor already firm and peaks "
                            f"at the ceiling - use a lower --target-lufs, or accept a slightly quieter voice")

    if m["deess"]:
        d = P["deess"]
        d.setdefault("thr0", d["thr"])
        d["on"] = True
        s = a["sib_diff"] - b["sib"]
        if s > 3.0:
            # automatic tightening stops at ratio 4 and 6 dB below where it started (press 's' for more)
            nr = min(4.0, d["ratio"] + 0.5)
            nt = round(max(d["thr0"] - 6.0, clip(d["thr"] - 1.5, DEESS_FLOOR, -3)), 1)
            if nr != d["ratio"] or nt != d["thr"]:
                d["ratio"], d["thr"] = nr, nt
                msgs.append(f"sibilance {s:+.1f} dB -> de-esser stronger (ratio {nr:.1f}, thr {nt:.1f})")
        elif s < -3 and d["ratio"] > 1.5:
            d["ratio"] = round(max(1.5, d["ratio"] - 0.5), 2)   # softer, but never switched off
            msgs.append(f"sibilance {s:+.1f} dB -> de-esser softer (ratio {d['ratio']:.1f})")

    if m["noise"]:
        g = P["gate"]
        reasons = []
        if a.get("click_hi30") is not None and a["click_hi30"] - a["speech_lo30"] > -6:
            reasons.append(f"clicks only {a['speech_lo30'] - a['click_hi30']:.0f} dB under quiet speech")
        if a["snr"] < 25:
            reasons.append(f"SNR only {a['snr']:.0f} dB")
        if reasons:
            if g["thr"] + 2 <= g["max"]:
                g["thr"] = round(g["thr"] + 2, 1)
                msgs.append(f"{' / '.join(reasons)} -> {m['noise']} threshold {g['thr']:.1f} dB")
            else:
                msgs.append(f"{' / '.join(reasons)}, but the {m['noise']} is at its safe limit (use 'w' if words get cut)")

    if m["exciter"]:
        ex = P["exciter"]
        r_air = bmean(a, 8000, 12000)
        if r_air < -2.5 and ex["amount"] < 4.0:
            ex["amount"] = round(ex["amount"] + 0.5, 2)
            msgs.append(f"air is {r_air:+.1f} dB under target -> exciter {ex['amount']:.1f} dB")
        elif (a["sib_diff"] - b["sib"] > 3.0 or r_air > 2.0) and ex["amount"] > 0.5:
            ex["amount"] = round(ex["amount"] - 0.5, 2)
            msgs.append(f"too bright/sibilant -> exciter {ex['amount']:.1f} dB")
    return msgs


FEEDBACK_HELP = """  What do you hear?  (several allowed, e.g. "b c")
    b boxy/muddy   t thin       d dull/muffled   h harsh/too bright
    s sharp "s"    c clicks still leak   w words getting cut
    u too quiet    l too loud   e echoey/roomy (room tails between words)
    p plosives/pops still get through     m mouth clicks / lip smacks
    + more EQ bands (finer detail)       - fewer EQ bands
  Enter = measure again      q / quit / exit = I'm happy: save the result and finish"""

WORDS = {"boxy": "b", "muddy": "b", "thin": "t", "dull": "d", "muffled": "d", "harsh": "h", "bright": "h",
         "sibilant": "s", "clicks": "c", "chop": "w", "chopped": "w", "quiet": "u", "loud": "l",
         "more": "+", "fewer": "-", "pops": "p", "plosives": "p", "mouth": "m", "smacks": "m",
         "echo": "e", "echoey": "e", "roomy": "e", "reverb": "e"}


def parse_feedback(text):
    toks, bad = [], 0
    for w in text.lower().replace(",", " ").split():
        if w in WORDS:
            toks.append(WORDS[w])
        elif all(ch in "btdhscwulepm+-" for ch in w):
            toks += list(w)
        else:
            bad += 1
    if bad:  # never echo it back - it could be stray typing
        print(f"  (ignored {bad} word{'s' if bad > 1 else ''} that aren't in the list)")
    return toks


def manual_feedback(P, toks):
    m, b, msgs = P["modules"], P["bias"], []

    def off(what):
        msgs.append(f"({what} is not in your chain - skipped)")

    for t in toks:
        if t == "b":
            if m["eq"]:
                shift_range(P, 220, 650, -1.5); msgs.append("less 250-630 Hz (mud/box)")
            else:
                off("EQ")
        elif t == "t":
            if m["eq"]:
                shift_range(P, 100, 650, 1.0); msgs.append("more body (100-630 Hz)")
            else:
                off("EQ")
        elif t in ("d", "h"):
            sgn = 1 if t == "d" else -1
            if m["eq"]:
                shift_range(P, 2200, 12000, 1.5 * sgn)
            if m["exciter"]:
                P["exciter"]["amount"] = round(clip(P["exciter"]["amount"] + 0.5 * sgn, 0, 6), 2)
            if m["eq"] or m["exciter"]:
                msgs.append("more presence + air" if sgn > 0 else "less presence + air")
            else:
                off("EQ/exciter")
        elif t in "+-":
            n = len(P["eq"]["bands"]) + 1
            if P.get("imported"):
                msgs.append("(changing the band count isn't available when tuning an existing preset)")
            elif not m["eq"]:
                off("EQ")
            else:
                new = int(clip(n + 4 if t == "+" else n - 4, 6, MAX_BANDS))
                if new == n:
                    msgs.append(f"EQ already at {'max' if t == '+' else 'min'} ({n} bands)")
                else:
                    relayout(P, new); msgs.append(f"EQ now {new} bands (incl. high-pass)")
        elif t == "s":
            if m["deess"]:
                d = P["deess"]
                d.setdefault("thr0", d["thr"])
                d["thr0"] = d["thr"] - 2  # you asked for more: let the automatic range follow
                b["sib"] = clip(b["sib"] - 2, -8, 8)
                d["on"] = True
                d["ratio"] = min(6.0, d["ratio"] + 0.5)
                d["thr"] = clip(d["thr"] - 2, DEESS_FLOOR, -3)
                if m["deess2"]:
                    d2 = P["deess2"]
                    d2["on"] = True
                    d2["ratio"] = min(8.0, d2["ratio"] + 0.5)
                    d2["thr"] = clip(d2["thr"] - 2, DEESS_FLOOR, -3)
                msgs.append("stronger de-essers")
            else:
                off("de-esser")
        elif t == "c":
            if m["noise"]:
                g = P["gate"]
                g["thr"] = round(min(g["thr"] + 3, -10), 1)
                g["max"] = round(min(max(g["max"], g["thr"] + 8), -10), 1)
                msgs.append(f"{m['noise']} up to {g['thr']:.1f} dB")
            else:
                off("gate/expander")
        elif t == "w":
            did = False
            if m["noise"]:
                g = P["gate"]
                g["thr"] = round(g["thr"] - 3, 1)
                g["max"] = g["thr"]
                msgs.append(f"{m['noise']} down to {g['thr']:.1f} dB"); did = True
            r = P["rnnoise"]
            if m["rnnoise"] and r["vad"]:
                r["vad_thres"] = max(30.0, r["vad_thres"] - 10)
                msgs.append(f"VAD {r['vad_thres']:g}%"); did = True
            if not did:
                off("gate/expander/RNNoise VAD")
        elif t == "p":
            if m["eq"]:
                e = P["eq"]
                e["hpf"] = min(160, e["hpf"] + 15)
                msgs.append(f"high-pass up to {e['hpf']:.0f} Hz (also try a pop filter / speaking slightly off-axis)")
            else:
                off("EQ (high-pass)")
        elif t == "m":
            did = False
            if m["deess"]:
                d = P["deess"]
                d["on"] = True
                d["ratio"] = min(6.0, d["ratio"] + 0.5)
                d["thr"] = clip(d["thr"] - 2.5, DEESS_FLOOR, -3)
                msgs.append("de-esser stronger (also catches lip smacks)"); did = True
            if m["noise"]:
                g = P["gate"]
                g["release"] = float(max(40, round(g.get("release", 150.0) * 0.8)))
                msgs.append(f"{m['noise']} release {g['release']:.0f} ms"); did = True
            if not did:
                off("de-esser/gate")
        elif t == "e":
            did = False
            if m["noise"]:
                g = P["gate"]
                g["release"] = float(max(40, round(g.get("release", 150.0) * 0.6)))
                g["thr"] = round(min(g["thr"] + 2, g["max"]), 1)
                msgs.append(f"{m['noise']} release {g['release']:.0f} ms"); did = True
            if m["comp"]:
                c = P["comp"]
                c["thr"] = round(min(c["thr"] + 3, -10), 1)
                msgs.append(f"leveler threshold {c['thr']:.1f} dB"); did = True
            if m["eq"]:
                shift_range(P, 220, 650, -1.0)
                msgs.append("less 220-650 Hz"); did = True
            if not did:
                off("gate/compressor/EQ")
        elif t in ("u", "l"):
            if m["comp"]:
                dlt = MAX_UP_STEP if t == "u" else -2
                mk = P["comp"]["makeup"]
                lg, lgh = P.get("last_gain"), P.get("last_gain_hi")
                mg = P.get("max_gain", 12.0)
                cap = 30.0 if lg is not None else max(mg, mk)
                if dlt > 0 and lg is not None:
                    dlt = min(dlt, max(0.0, mg - lg))
                    if lgh is not None:
                        dlt = min(dlt, max(0.0, mg + HI_SPREAD - lgh))
                        P["last_gain_hi"] = round(lgh + dlt, 1)
                    P["last_gain"] = round(lg + dlt, 1)
                new = clip(mk + dlt, 0, cap)
                if new == mk and dlt >= 0:
                    msgs.append(f"already at the +{mg:.0f} dB gain cap - raise the mic's own gain instead (or --max-gain)")
                else:
                    P["comp"]["makeup"] = round(new, 1)
                    b["level"] = clip(b["level"] + dlt, -6, 3)  # loudness target moves with you, within limits
                    msgs.append(f"louder (target now {target_lufs(P):.0f} LUFS)" if dlt > 0 else
                                f"quieter (target now {target_lufs(P):.0f} LUFS)")
            else:
                off("compressors")
    return msgs


def region_summary(P, a):
    regs = [("low", 100, 200), ("mud", 220, 420), ("box", 420, 650), ("presence", 2200, 4500), ("air", 8000, 12000)]
    out = []
    for name, lo, hi in regs:
        bs = [x for x in P["eq"]["bands"] if lo <= x["f"] <= hi]
        r = float(np.mean([band_resid(a, x) - x["b"] for x in bs])) if bs else bmean(a, lo, hi)
        out.append(f"{name} {r:+.1f}")
    return "  ".join(out)
