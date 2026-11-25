"""Everything that talks to EasyEffects: writing presets (every option set explicitly), importing existing
presets, loading them through EasyEffects' local server (no window closing), and reading values back."""
import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from .config import DEFAULT_KEYS, LEVEL_HI, NEG_INF, TARGET_LUFS
from .eq import SPECIAL_Q

DEEP_LIB = "libdeep_filter_ladspa.so"
from .eq import bias_default

# ----------------------------------------------------------------------------- preset files


def preset_dir():
    native = Path.home() / ".local/share/easyeffects/input"
    flat = Path.home() / ".var/app/com.github.wwmm.easyeffects/data/easyeffects/input"
    return flat if (flat.parent.exists() and not native.parent.exists()) else native


def deep_available():
    """Is DeepFilterNet's LADSPA library (needed by EasyEffects' Deep noise remover) installed?
    Returns True, False, or "flatpak" (EasyEffects is a Flatpak: newer builds bundle it, can't check from here)."""
    dirs = [d for d in os.environ.get("LADSPA_PATH", "").split(":") if d]
    dirs += ["/usr/lib/ladspa", "/usr/lib64/ladspa", "/usr/local/lib/ladspa", "/usr/local/lib64/ladspa",
             "/usr/lib/x86_64-linux-gnu/ladspa", "/usr/lib/aarch64-linux-gnu/ladspa", str(Path.home() / ".ladspa")]
    if any((Path(d) / DEEP_LIB).exists() for d in dirs):
        return True
    if not shutil.which("easyeffects") and (Path.home() / ".var/app/com.github.wwmm.easyeffects").exists():
        return "flatpak"
    return False


def deep_merge(base, new):
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _premix():
    """LSP 'Pre-Mix' routing: all OFF (-inf). Non-off values mix signals into the sidechain/link paths."""
    return {k: NEG_INF for k in ("input-to-sidechain", "input-to-link", "sidechain-to-input",
                                 "sidechain-to-link", "link-to-input", "link-to-sidechain")}


def _io():
    return {"bypass": False, "input-gain": 0.0, "output-gain": 0.0}


def eq_band(freq, gain, q, typ="Bell", slope="x1"):
    return {"type": typ, "mode": "RLC (BT)", "slope": slope, "solo": False, "mute": False,
            "gain": round(float(gain), 2), "frequency": float(round(freq, 1)), "q": round(float(q), 3), "width": 4.0}


STEREO_MODES = {"LL": "LR > LL (Mono Left Channel)", "RR": "LR > RR (Mono Right Channel)"}


def stereo_block(fix):
    """Stereo Tools: copy a one-sided mono mic to both channels. Everything else neutral."""
    return {**_io(), "mode": STEREO_MODES[fix], "balance-in": 0.0, "balance-out": 0.0, "softclip": False,
            "mutel": False, "muter": False, "phasel": False, "phaser": False, "middle-level": 0.0,
            "middle-panorama": 0.0, "side-level": 0.0, "side-balance": 0.0, "stereo-base": 0.0,
            "stereo-phase": 0.0, "delay": 0.0, "sc-level": 1.0}


def _mb_band(i, split, enabled, compress, t):
    b = {"compressor-enable": bool(compress), "compression-mode": "Downward", "mute": False, "solo": False,
         "attack-threshold": float(t["thr"]), "attack-time": float(t["attack"]),
         "release-threshold": -100.0, "release-time": float(t["release"]),
         "ratio": round(float(t["ratio"]), 2), "knee": -6.0, "makeup": 0.0,
         "boost-amount": 0.0, "boost-threshold": -72.0,
         "sidechain-type": "Internal", "sidechain-mode": "Peak", "sidechain-source": "Middle",
         "sidechain-preamp": 0.0, "sidechain-reactivity": 5.0, "sidechain-lookahead": 2.0,
         "sidechain-custom-lowcut-filter": False, "sidechain-custom-highcut-filter": False,
         "sidechain-lowcut-frequency": 10.0, "sidechain-highcut-frequency": 500.0,
         "stereo-split-source": "Left/Right"}
    if i > 0:
        b.update({"enable-band": bool(enabled), "split-frequency": float(split)})
    return b


def taps_block(t):
    """Multiband Compressor used as a desk-tap catcher: band 0 (below the split) compresses only when the low
    end gets louder than your voice's own bass; band 1 (everything above) passes untouched; bands 2-7 off."""
    blk = {**_io(), "compressor-mode": "Modern", "envelope-boost": "None", "dry": NEG_INF, "wet": 0.0,
           "stereo-split": False, **_premix()}
    blk["band0"] = _mb_band(0, 0, True, True, t)
    blk["band1"] = _mb_band(1, t["split"], True, False, t)
    for i, f in enumerate((500, 1000, 2000, 4000, 8000, 16000), start=2):
        blk[f"band{i}"] = _mb_band(i, f, False, False, t)
    return blk


def deep_block(dp):
    """EasyEffects' Deep noise remover (DeepFilterNet LADSPA)."""
    return {**_io(), "attenuation-limit": float(dp["atten"]), "min-processing-threshold": -10.0,
            "max-erb-processing-threshold": 30.0, "max-df-processing-threshold": 20.0,
            "min-processing-buffer": 0, "post-filter-beta": 0.02}


def render(P):
    """A complete input preset. Every option of every plugin is written so nothing falls back to a default
    you didn't choose (e.g. the limiter's Threshold Boost, de-esser 'listen' modes, pre-mix routing).
    Order: stereo fix -> gate -> expander -> RNNoise -> deep noise remover -> tap catcher -> EQ -> leveler
           -> peak compressor -> exciter -> de-essers -> limiter -> level meter"""
    m, K = P["modules"], P["keys"]
    K = {**DEFAULT_KEYS, **K}
    e, g, r = P["eq"], P["gate"], P["rnnoise"]
    order, pl = [], {}

    if P.get("stereo_fix") in STEREO_MODES:
        pl[K["stereo"]] = stereo_block(P["stereo_fix"])
        order.append(K["stereo"])

    # detector filters: ignore rumble/plosive thumps below ~90 Hz and most click energy above ~6 kHz
    det_filters = {"hpf-mode": "12 dB/oct", "hpf-frequency": 90.0, "lpf-mode": "12 dB/oct", "lpf-frequency": 6000.0}
    sc = {"type": "Internal", "mode": "RMS", "source": "Middle", "preamp": 0.0,
          "reactivity": 30.0, "lookahead": 5.0, "stereo-split-source": "Left/Right"}

    open_ms = float(g.get("attack", 8.0))

    def gate_block(thr, release, reduction):
        return {**_io(), "attack": open_ms, "release": float(release),
                "curve-threshold": round(float(thr), 1), "curve-zone": -3.0,
                "hysteresis": True, "hysteresis-threshold": -3.0, "hysteresis-zone": -1.0,
                "reduction": float(reduction), "makeup": 0.0, "dry": NEG_INF, "wet": 0.0, "stereo-split": False,
                **det_filters, "sidechain": dict(sc), **_premix()}

    noise = m.get("noise")
    if noise == "gate":
        pl[K["gate"]] = gate_block(g["thr"], g.get("release", 150.0), g.get("reduction", -40.0))
        order.append(K["gate"])
    if noise == "both":                       # deep floor cut first (raw levels), soft expander after
        f = P.get("floor") or {"thr": g["thr"] - 6, "reduction": -30.0, "release": 200.0}
        pl[K["gate"]] = gate_block(f["thr"], f.get("release", 200.0), f.get("reduction", -30.0))
        order.append(K["gate"])
    if noise in ("expander", "both"):
        pl[K["expander"]] = {
            **_io(), "mode": "Downward", "attack": open_ms, "release": float(g.get("release", 150.0)),
            "threshold": round(g["thr"], 1), "release-threshold": NEG_INF,
            "ratio": float(g.get("ratio", 3.0)), "knee": -6.0, "makeup": 0.0,
            "dry": NEG_INF, "wet": 0.0, "stereo-split": False, **det_filters,
            "sidechain": dict(sc), **_premix()}
        order.append(K["expander"])
    if m["rnnoise"]:
        pl[K["rnnoise"]] = {**_io(), "enable-vad": bool(r["vad"]), "vad-thres": float(r["vad_thres"]),
                            "release": float(r.get("release", 60.0)), "wet": 0.0, "use-standard-model": True,
                            "model-name": ""}
        order.append(K["rnnoise"])
    if m.get("deep"):
        pl[K["deep"]] = deep_block(P.get("deep") or {"atten": 40.0})
        order.append(K["deep"])
    if m.get("taps"):
        pl[K["taps"]] = taps_block(P["taps"])
        order.append(K["taps"])
    if m["eq"]:
        bands = [eq_band(e["hpf"], 0, 0.707, "Hi-pass", e.get("hpf_slope", "x4"))] + [
            eq_band(x["f"], x["g"], x["q"], x["t"]) for x in sorted(e["bands"], key=lambda y: y["f"])]
        side = {f"band{i}": b for i, b in enumerate(bands)}
        pl[K["eq"]] = {**_io(), "mode": "IIR", "num-bands": len(bands), "split-channels": False,
                       "balance": 0.0, "pitch-left": 0.0, "pitch-right": 0.0,
                       "left": side, "right": copy.deepcopy(side)}
        order.append(K["eq"])

    def comp(c):
        return {**_io(), "mode": "Downward", "attack": float(c["attack"]), "release": float(c["release"]),
                "threshold": float(c["thr"]), "release-threshold": NEG_INF, "ratio": round(float(c["ratio"]), 2),
                "knee": -6.0, "makeup": round(float(c["makeup"]), 1),
                "boost-threshold": -72.0, "boost-amount": 0.0,          # only used by Upward/Boosting modes
                "dry": NEG_INF, "wet": 0.0, "stereo-split": False,
                "hpf-mode": "12 dB/oct", "hpf-frequency": 100.0,        # bass/plosives don't pump the compressor
                "lpf-mode": "Off", "lpf-frequency": 20000.0,
                "sidechain": {"type": "Feed-forward", "mode": c.get("sc_mode", "RMS"), "source": "Middle",
                              "preamp": 0.0, "reactivity": 10.0, "lookahead": float(c.get("lookahead", 0.0)),
                              "stereo-split-source": "Left/Right"},
                **_premix()}

    def deess(d, mode, split_f, peak_f, peak_gain, q):
        return {**_io(), "bypass": not d["on"], "detection": "RMS", "mode": mode,
                "threshold": round(float(d["thr"]), 1), "ratio": round(float(d["ratio"]), 2),
                "laxity": 15, "makeup": 0.0,
                "f1-freq": float(round(split_f)), "f1-level": 0.0,      # split / sidechain high-pass
                "f2-freq": float(round(peak_f)), "f2-level": float(peak_gain), "f2-q": float(q),  # sibilance peak
                "sc-listen": False}                                      # never monitor the sidechain only

    if m["comp"]:
        pl[K["comp"]] = comp(P["comp"])
        order.append(K["comp"])
        if m["comp2"]:
            pl[K["comp2"]] = comp(P["comp2"])
            order.append(K["comp2"])
    if m["exciter"]:
        x = P["exciter"]
        pl[K["exciter"]] = {**_io(), "amount": round(float(x["amount"]), 2), "harmonics": float(x["harmonics"]),
                            "scope": float(x["scope"]), "ceil": float(x["ceil"]), "ceil-active": True,
                            "blend": float(x["blend"]), "listen": False}
        order.append(K["exciter"])
    if m["deess"]:
        d1, d2 = P["deess"], P["deess2"]
        pl[K["deess"]] = deess(d1, "Wide", d1.get("split", d1["freq"] * 0.7), d1["freq"], 6.0, 1.0)
        order.append(K["deess"])
        if m["deess2"]:
            pl[K["deess2"]] = deess(d2, "Split", d2.get("split", d2["freq"] * 0.85), d2["freq"], 9.0,
                                    d2.get("q", 4.0))
            order.append(K["deess2"])
    if m["limiter"]:
        pl[K["limiter"]] = {**_io(), "mode": "Herm Thin", "oversampling": "None", "dithering": "None",
                            "sidechain-type": "Internal", "sidechain-preamp": 0.0,
                            "threshold": float(P["limiter"]),
                            "lookahead": float(P.get("lim_t", {}).get("lookahead", 5.0)),
                            "attack": float(P.get("lim_t", {}).get("attack", 5.0)),
                            "release": float(P.get("lim_t", {}).get("release", 50.0)),
                            "stereo-link": 100.0, "gain-boost": False,   # 'Threshold Boost' OFF
                            "alr": False, "alr-attack": 5.0, "alr-release": 50.0, "alr-knee": 0.0,
                            **_premix()}
        order.append(K["limiter"])
    if m.get("meter"):
        pl[K["meter"]] = _io()
        order.append(K["meter"])
    return {"input": {"blocklist": [], "plugins_order": order, **pl}}


def overrides(P, inp):
    """Existing-preset mode: ONLY the values the tuner changes, written under the key names already in the file."""
    m, K, out = P["modules"], P["keys"], {}

    def put(plugin, cands, val):
        have = inp.get(plugin, {})
        key = next((c for c in cands if c in have), cands[0])
        out.setdefault(plugin, {})[key] = val

    g = P["gate"]
    if m["noise"] == "gate":
        put(K["gate"], ["curve-threshold"], round(g["thr"], 1))
        put(K["gate"], ["release"], g["release"])
    elif m["noise"] in ("expander", "both"):
        put(K["expander"], ["threshold", "attack-threshold"], round(g["thr"], 1))
        put(K["expander"], ["ratio"], g["ratio"])
        put(K["expander"], ["release", "release-time"], g["release"])
    if m.get("taps"):
        t = P["taps"]
        out[K["taps"]] = {"band0": {"attack-threshold": round(t["thr"], 1), "ratio": round(t["ratio"], 2)}}
        if inp.get(K["taps"], {}).get("band1", {}).get("enable-band"):
            out[K["taps"]]["band1"] = {"split-frequency": float(t["split"])}
    if m.get("deep"):
        put(K["deep"], ["attenuation-limit"], float(P["deep"]["atten"]))
    if m["rnnoise"]:
        put(K["rnnoise"], ["enable-vad"], bool(P["rnnoise"]["vad"]))
        put(K["rnnoise"], ["vad-thres"], float(P["rnnoise"]["vad_thres"]))
    if m["eq"] and P["eq"].get("relayout"):
        pass                                   # the whole EQ is rebuilt in write_preset (new band layout)
    elif m["eq"]:
        ek, e = K["eq"], P["eq"]
        have = inp.get(ek, {})
        blk = out.setdefault(ek, {"left": {}, "right": {}})
        for bd in e["bands"]:
            if "key" in bd:
                for side in ("left", "right"):
                    if bd["key"] in have.get(side, {}):
                        blk[side][bd["key"]] = {"gain": round(bd["g"], 2)}
        if e.get("hpf_key"):
            for side in ("left", "right"):
                if e["hpf_key"] in have.get(side, {}):
                    blk[side][e["hpf_key"]] = {"frequency": float(e["hpf"])}
    if m["comp"]:
        for name in ("comp", "comp2") if m["comp2"] else ("comp",):
            c = P[name]
            put(K[name], ["threshold", "attack-threshold"], c["thr"])
            put(K[name], ["ratio"], round(c["ratio"], 2))
            put(K[name], ["makeup"], c["makeup"])
    if m["deess"]:
        for name in ("deess", "deess2") if m["deess2"] else ("deess",):
            d = P[name]
            put(K[name], ["threshold"], d["thr"])
            put(K[name], ["ratio"], d["ratio"])
            put(K[name], ["bypass"], not d["on"])
    if m["exciter"]:
        put(K["exciter"], ["amount"], round(P["exciter"]["amount"], 2))
        put(K["exciter"], ["bypass"], P["exciter"]["amount"] <= 0)
    for plugin, key, val in P.get("safety_fixes", []):
        out.setdefault(plugin, {})[key] = val
    return out


def num(d, cands, default):
    for c in cands:
        if c in d and isinstance(d[c], (int, float)):
            return float(d[c])
    return default


def import_preset(js):
    inp = js.get("input")
    if not isinstance(inp, dict):
        sys.exit("That file has no 'input' section - is it an output preset?")
    order = inp.get("plugins_order", [])

    def inst(prefix, active=True):
        return [k for k in order if k.split("#")[0] == prefix and isinstance(inp.get(k), dict)
                and not (active and inp[k].get("bypass", False))]

    K = dict(DEFAULT_KEYS)
    m = {"noise": None, "rnnoise": False, "eq": False, "comp": False, "comp2": False, "deess": False,
         "deess2": False, "exciter": False, "limiter": False, "deep": False, "taps": False, "meter": False,
         "stereo": "auto", "profile": "stream"}
    notes, found = [], []

    G = {"thr": -45.0, "max": -37.0, "release": 150.0, "ratio": 3.0}
    gk, xk = inst("gate"), inst("expander")
    F = {"thr": -57.0, "reduction": -30.0, "release": 200.0}
    if gk and xk:                                    # gate + expander: the expander is the one that gets tuned
        m["noise"], K["gate"], K["expander"] = "both", gk[0], xk[0]
        d = inp[xk[0]]
        G["thr"] = num(d, ["threshold", "attack-threshold"], -45.0)
        G["ratio"], G["release"] = num(d, ["ratio"], 3.0), num(d, ["release", "release-time"], 150.0)
        F["thr"] = num(inp[gk[0]], ["curve-threshold"], -57.0)
        found.append("gate + expander")
    elif gk:
        m["noise"], K["gate"] = "gate", gk[0]
        G["thr"], G["release"] = num(inp[gk[0]], ["curve-threshold"], -45.0), num(inp[gk[0]], ["release"], 150.0)
        found.append("gate")
    elif xk:
        m["noise"], K["expander"] = "expander", xk[0]
        d = inp[xk[0]]
        G["thr"] = num(d, ["threshold", "attack-threshold"], -45.0)
        G["ratio"], G["release"] = num(d, ["ratio"], 3.0), num(d, ["release", "release-time"], 150.0)
        found.append("expander")
    G["max"] = round(min(G["thr"] + 8, -10), 1)

    T = {"split": 150.0, "ratio": 8.0, "attack": 1.0, "release": 80.0, "thr": -24.0, "thr0": -24.0}
    mk = inst("multiband_compressor")
    if mk and isinstance(inp[mk[0]].get("band0"), dict) and inp[mk[0]]["band0"].get("compressor-enable"):
        b0, b1 = inp[mk[0]]["band0"], inp[mk[0]].get("band1", {})
        m["taps"], K["taps"] = True, mk[0]
        T.update(thr=num(b0, ["attack-threshold"], -24.0), ratio=num(b0, ["ratio"], 8.0),
                 split=num(b1, ["split-frequency"], 150.0))
        T["thr0"] = T["thr"]
        found.append("multiband compressor (tap catcher)")
    DP = {"atten": 40.0}
    dk = inst("deepfilternet")
    if dk:
        m["deep"], K["deep"] = True, dk[0]
        DP["atten"] = num(inp[dk[0]], ["attenuation-limit"], 40.0)
        found.append("deep noise remover")

    R = {"on": False, "vad": True, "vad_thres": 70.0}
    rk = inst("rnnoise")
    if rk:
        m["rnnoise"], K["rnnoise"] = True, rk[0]
        R = {"on": True, "vad": bool(inp[rk[0]].get("enable-vad", False)),
             "vad_thres": num(inp[rk[0]], ["vad-thres"], 70.0)}
        found.append("RNNoise")

    E = {"hpf": 90, "hpf_key": None, "bands": [], "raw": {}}
    ek = inst("equalizer")
    if ek:
        blk = inp[ek[0]]
        left = blk.get("left", {})
        bands = []
        for i in range(int(blk.get("num-bands", len(left)))):
            key, b = f"band{i}", left.get(f"band{i}")
            if not isinstance(b, dict):
                continue
            t, f = b.get("type", "Off"), num(b, ["frequency"], 1000.0)
            E["raw"][key] = copy.deepcopy(b)
            if t == "Hi-pass" and E["hpf_key"] is None:
                E["hpf"], E["hpf_key"] = f, key
            elif t in ("Bell", "Lo-shelf", "Hi-shelf"):
                q = num(b, ["q"], 1.0)
                bands.append({"f": f, "g": num(b, ["gain"], 0.0), "q": q, "t": t, "key": key,
                              "b": bias_default(f), "special": t == "Bell" and q > SPECIAL_Q})
        bands.sort(key=lambda x: x["f"])
        for i, b in enumerate(bands):
            b["lo"] = float(np.sqrt(bands[i - 1]["f"] * b["f"])) if i > 0 else b["f"] / 1.41
            b["hi"] = float(np.sqrt(b["f"] * bands[i + 1]["f"])) if i < len(bands) - 1 else b["f"] * 1.41
        E["bands"] = bands
        m["eq"], K["eq"] = True, ek[0]
        found.append(f"EQ ({len(bands)} tunable bands)")

    def parse_comp(d):
        return {"thr": num(d, ["threshold", "attack-threshold"], -30.0), "ratio": num(d, ["ratio"], 2.0),
                "makeup": num(d, ["makeup"], 0.0), "attack": num(d, ["attack", "attack-time"], 10.0),
                "release": num(d, ["release", "release-time"], 100.0)}

    C1 = {"thr": -35.0, "ratio": 2.0, "makeup": 0.0, "attack": 25.0, "release": 300.0}
    C2 = {"thr": -25.0, "ratio": 3.0, "makeup": 0.0, "attack": 5.0, "release": 80.0}
    ck = inst("compressor")
    if ck:
        m["comp"], K["comp"] = True, ck[0]
        C1 = parse_comp(inp[ck[0]])
        if len(ck) > 1:
            m["comp2"], K["comp2"] = True, ck[1]
            C2 = parse_comp(inp[ck[1]])
        found.append(f"{min(len(ck), 2)} compressor(s)")

    # de-essers are managed by the tuner, so they are always ENABLED in the new version (bypassed ones too)
    D1 = {"on": True, "thr": -30.0, "ratio": 3.0, "freq": 6000}
    D2 = {"on": True, "thr": -28.0, "ratio": 4.0, "freq": 6500}
    dk = inst("deesser", active=False)
    if dk:
        m["deess"], K["deess"] = True, dk[0]
        for i, (key, D) in enumerate(zip(dk[:2], (D1, D2))):
            blk = inp[key]
            D.update(thr=num(blk, ["threshold"], D["thr"]), ratio=max(1.5, num(blk, ["ratio"], D["ratio"])),
                     freq=num(blk, ["f2-freq", "f1-freq"], D["freq"]), on=True)
            if blk.get("bypass", False):
                notes.append(f"{key} was bypassed in this preset - it is enabled in the new version")
        if len(dk) > 1:
            m["deess2"], K["deess2"] = True, dk[1]
        found.append(f"{min(len(dk), 2)} de-esser(s)")

    X = {"amount": 1.0, "harmonics": 2.5, "scope": 4000.0, "ceil": 14000.0, "blend": 3.0}
    xk2 = inst("exciter")
    if xk2:
        m["exciter"], K["exciter"] = True, xk2[0]
        d = inp[xk2[0]]
        X = {"amount": num(d, ["amount"], 1.0), "harmonics": num(d, ["harmonics"], 2.5),
             "scope": num(d, ["scope"], 4000.0), "ceil": num(d, ["ceil"], 14000.0), "blend": num(d, ["blend"], 3.0)}
        found.append("exciter")

    lim = LEVEL_HI
    lk = inst("limiter")
    if lk:
        m["limiter"], K["limiter"] = True, lk[0]
        lim = num(inp[lk[0]], ["threshold"], LEVEL_HI)
        found.append("limiter")

    # hazards: settings that add level the tuner doesn't control, or that make you hear only part of the signal.
    # Fixed in the NEW file only.
    fixes = []
    for k in order:
        d = inp.get(k)
        if not isinstance(d, dict):
            continue
        kind = k.split("#")[0]
        if kind == "deesser" and d.get("sc-listen") is True:
            fixes.append((k, "sc-listen", False))
            notes.append(f"!! {k} had 'sidechain listen' on (you'd hear only the 's' band). Turned off.")
        if kind == "exciter" and d.get("listen") is True:
            fixes.append((k, "listen", False))
            notes.append(f"!! {k} had 'listen' on (you'd hear only the added harmonics). Turned off.")
        if d.get("bypass", False):
            continue
        md = d.get("mode")
        if kind == "expander" and isinstance(md, str) and md.lower().startswith("up"):
            fixes.append((k, "mode", "Downward"))
            notes.append(f"!! {k} is in UPWARD mode, which makes loud sound louder. Switched to Downward in the new file.")
        if kind == "compressor" and isinstance(md, str) and md.lower() in ("upward", "boosting"):
            fixes.append((k, "mode", "Downward"))
            notes.append(f"!! {k} is in {md} mode, which raises quiet sound a lot. Switched to Downward in the new file.")
        if kind == "limiter" and d.get("gain-boost") is not False:
            fixes.append((k, "gain-boost", False))
            thr = abs(num(d, ["threshold"], 0.0))
            notes.append(f"!! {k}: Threshold Boost was "
                         f"{'on' if d.get('gain-boost') else 'not set (EasyEffects defaults it to on)'}"
                         + (f" - it adds {thr:.0f} dB after limiting, pushing peaks back to 0 dBFS." if thr >= 0.5 else ".")
                         + " Turned off in the new file.")
        extra = sum(v for v in (d.get("input-gain"), d.get("output-gain")) if isinstance(v, (int, float)) and v > 0)
        if extra > 0.5:
            notes.append(f"!! {k} has its input/output gain sliders raised by +{extra:.1f} dB (left as is).")

    known = {"gate", "expander", "rnnoise", "equalizer", "compressor", "deesser", "exciter", "limiter"}
    known |= {"multiband_compressor"} if m["taps"] else set()
    known |= {"deepfilternet"} if m["deep"] else set()
    other = [k for k in order if k.split("#")[0] not in known]
    bypassed = [k for k in order if isinstance(inp.get(k), dict) and inp[k].get("bypass") and k.split("#")[0] != "deesser"]
    notes.insert(0, "Found: " + (", ".join(found) if found else "nothing the tuner understands"))
    if other:
        notes.append("Left untouched (not managed by the tuner): " + ", ".join(other))
    if bypassed:
        notes.append("Bypassed in this preset and left that way: " + ", ".join(bypassed))
    P = {"modules": m, "keys": K, "imported": True, "gate": G, "rnnoise": R, "eq": E, "comp": C1, "comp2": C2,
         "deess": D1, "deess2": D2, "exciter": X, "limiter": lim, "bias": {"sib": 0.0, "level": 0.0},
         "safety_fixes": fixes, "target_lufs": TARGET_LUFS, "floor": F, "taps": T, "deep": DP,
         "profile": "stream", "stereo_fix": None}
    return P, notes


def imported_eq_side(P):
    """The full band list of a re-spread imported EQ: your high-pass, filters and notches exactly as they were
    (notch gains as tuned), plus the new evenly spread bells/shelves."""
    E = P["eq"]
    tuned = {b["key"]: b for b in E["bands"] if b.get("key")}
    regular_keys = {k for k, b in E["raw"].items()
                    if b.get("type") in ("Bell", "Lo-shelf", "Hi-shelf") and not (k in tuned and tuned[k].get("special"))}
    bands = []
    for k, b in E["raw"].items():
        if k in regular_keys or b.get("type", "Off") == "Off":
            continue
        b = copy.deepcopy(b)
        if k == E.get("hpf_key"):
            b["frequency"] = float(E["hpf"])
        if k in tuned:
            b["gain"] = round(float(tuned[k]["g"]), 2)
        bands.append(b)
    bands += [eq_band(b["f"], b["g"], b["q"], b["t"]) for b in E["bands"] if not b.get("key")]
    bands.sort(key=lambda b: float(b.get("frequency", 0)))
    return {f"band{i}": b for i, b in enumerate(bands)}


def write_preset(P, name, out, base=None):
    from .search import apply_overrides
    apply_overrides(P)                         # your locks/ranges always win (overrides.toml / .json)
    if isinstance(base, (str, Path)):
        base = json.loads(Path(base).expanduser().read_text())
    if P.get("imported"):
        preset = copy.deepcopy(base)
        deep_merge(preset["input"], overrides(P, base["input"]))
        inp = preset["input"]
        if P["modules"].get("eq") and P["eq"].get("relayout") and P["keys"].get("eq") in inp:
            side = imported_eq_side(P)
            blk = inp[P["keys"]["eq"]]
            blk["num-bands"] = len(side)
            blk["left"], blk["right"] = side, copy.deepcopy(side)
        order = inp.setdefault("plugins_order", [])
        if P.get("stereo_fix") in STEREO_MODES and not any(k.startswith("stereo_tools#") for k in order):
            inp["stereo_tools#0"] = stereo_block(P["stereo_fix"])
            order.insert(0, "stereo_tools#0")
    else:
        preset = render(P)
        if base:
            b = copy.deepcopy(base)
            b.setdefault("input", {})
            deep_merge(b["input"], preset["input"])
            preset = b
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.json"
    path.write_text(json.dumps(preset, indent=2))
    return path


# ----------------------------------------------------------------------------- talking to the running EasyEffects

APP_ID = "com.github.wwmm.easyeffects"


def server_path():
    """EasyEffects' local server socket ($XDG_RUNTIME_DIR/EasyEffectsServer since 8.0.9; /tmp before that)."""
    rt = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    for p in (rt / "EasyEffectsServer", rt / "app" / APP_ID / "EasyEffectsServer", Path("/tmp/EasyEffectsServer")):
        if p.exists():
            return p
    return None


def server_send(cmd, want_reply=False, timeout=1.5):
    """Send one command. Returns the reply text ('' if none expected), or None if the server isn't reachable."""
    p = server_path()
    if p is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(p))
            s.sendall((cmd + "\n").encode())
            if not want_reply:
                time.sleep(0.05)
                return ""
            data = b""
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
                s.settimeout(0.25)
            return data.decode(errors="replace").strip()
    except OSError:
        return None


def ee_cmd(path, name):
    flat = "flatpak" in str(path) or ".var/app" in str(path)
    return (["flatpak", "run", APP_ID] if flat else ["easyeffects"]) + ["-l", name]


_warned_cli = False


def load_preset(name, path=None):
    """Load an input preset into the running EasyEffects without touching its window.
    Uses the local server; falls back to `easyeffects -l` (which can close the window) if it isn't there."""
    global _warned_cli
    if server_path() is not None:
        server_send(f"load_preset:input:{name}")
        for _ in range(10):
            time.sleep(0.15)
            r = server_send("get_last_loaded_preset:input", want_reply=True)
            if not r or r.strip().splitlines()[-1].strip() == name:
                break
        time.sleep(0.3)
        return "server"
    cmd = ee_cmd(path or "", name)
    if shutil.which(cmd[0]):
        if not _warned_cli:
            print("(EasyEffects' local server wasn't found, so presets are loaded with 'easyeffects -l', which can "
                  "close its window. EasyEffects 8.0.9+ running in the background avoids that.)")
            _warned_cli = True
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        return "cli"
    return None


class bypassed:
    """All EasyEffects effects off while active (via the local server), so the EasyEffects mic carries your
    raw voice. The previous bypass state is restored afterwards."""

    def __enter__(self):
        if server_path() is None:
            raise RuntimeError("EasyEffects' local server isn't available")
        self.prev = (server_send("get_global_bypass", want_reply=True) or "").strip()
        server_send("global_bypass:1")
        time.sleep(0.6)
        return self

    def __exit__(self, *exc):
        if not self.prev.endswith("1"):        # it wasn't bypassed before: turn effects back on
            server_send("global_bypass:0")
            time.sleep(0.3)
        return False


def _camel(k):
    head, *rest = k.split("-")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def _parse(text):
    t = text.strip().splitlines()[-1].strip() if text.strip() else ""
    t = t.split(":")[-1].strip().lower()
    if t in ("true", "false"):
        return t == "true"
    try:
        return float(t)
    except ValueError:
        return None


VERIFY = {"compressor": ["threshold", "ratio", "makeup"], "gate": ["curve-threshold"],
          "expander": ["threshold", "ratio"], "limiter": ["threshold", "gain-boost"],
          "deesser": ["threshold", "ratio"], "exciter": ["amount"], "rnnoise": ["enable-vad"]}


def verify_loaded(preset, full=False, budget=12.0):
    """Ask EasyEffects what it actually applied and compare with the preset. Returns a dict, or None if the
    local server isn't available.
    full=False: on/off state + the values the tuner changes. full=True (first load): every number and switch
    at the top level of every plugin. Text options (modes) are skipped - the server may report them as an
    index. A plugin whose first extra properties are all unknown to the server is skipped after 3 misses, and
    the whole check stops after `budget` seconds."""
    if server_path() is None:
        return None
    inp = preset["input"]
    checked, unknown, bad, skipped = 0, 0, [], 0
    t_end = time.monotonic() + budget
    for key in inp.get("plugins_order", []):
        base, _, idx = key.partition("#")
        blk = inp.get(key, {})
        props = ["bypass"] + VERIFY.get(base, [])
        if full:
            props += [k for k, v in blk.items() if k not in props and isinstance(v, (bool, int, float))]
        misses = 0
        for n, prop in enumerate(props):
            if prop not in blk:
                continue
            if time.monotonic() > t_end or (misses >= 3 and n >= len(VERIFY.get(base, [])) + 1):
                skipped += 1
                continue
            reply = server_send(f"get_property:input:{base}:{idx or 0}:{_camel(prop)}", want_reply=True)
            got = _parse(reply) if reply else None
            if got is None:
                unknown += 1
                misses += 1
                continue
            misses = 0
            want = blk[prop]
            checked += 1
            if isinstance(want, bool):
                ok = bool(got) == want
            else:
                ok = abs(float(got) - float(want)) <= max(0.06, abs(float(want)) * 0.01)
            if not ok:
                bad.append(f"{key} {prop}: preset {want}, EasyEffects reports {got}")
    return {"checked": checked, "unknown": unknown, "mismatch": bad, "skipped": skipped}
