"""New features: profiles, gate + expander, desk-tap catcher, deep noise remover, stereo fix, level meter."""
import copy
import json
import tempfile
from pathlib import Path

import numpy as np
from scipy import signal

import sim
from common import args, rd, take_from
from v2ee import easyeffects as ee
from v2ee.analysis import analyze, analyze_take
from v2ee.config import DEFAULT_MODULES, PROFILES
from v2ee.planner import finalize_mods, initial_params
from v2ee.tuning import auto_adjust, manual_feedback

LP = signal.butter(4, 150, "low", fs=48000, output="sos")


def _a(taps=True):
    return analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"), rd("taps.wav") if taps else None)


def _P(mods=None, **kw):
    P, notes = initial_params(_a(), args(target_lufs=kw.get("target_lufs"), mods_dict=mods or {}))
    return P, notes


def lowpk(x):
    return 20 * np.log10(np.abs(signal.sosfilt(LP, x)).max() + 1e-9)


def lowrms(x):
    y = signal.sosfilt(LP, x)
    return 10 * np.log10(np.mean(y ** 2) + 1e-20)


def test_profile_defaults():
    for prof in ("stream", "record"):
        m = finalize_mods({"profile": prof})
        for k, v in DEFAULT_MODULES[prof].items():
            assert m[k] == v, f"{prof}: {k} = {m[k]}, default {v}"
        P, _ = _P({"profile": prof})
        assert P["limiter"] == PROFILES[prof]["limiter"]
        assert P["target_lufs"] == PROFILES[prof]["target_lufs"]
    s, _ = _P({"profile": "stream"})
    r, _ = _P({"profile": "record"})
    assert s["modules"]["comp2"] and not r["modules"]["comp2"]          # no peak squashing for recordings
    assert s["rnnoise"]["vad"] and not r["modules"]["rnnoise"]
    assert r["limiter"] == -1.0 and s["limiter"] == -6.0
    assert r["modules"]["noise"] == "expander" and s["modules"]["noise"] == "both"


def test_gate_plus_expander_order():
    P, _ = _P({"noise": "both"})
    pre = ee.render(P)["input"]
    order = pre["plugins_order"]
    assert order.index("gate#0") < order.index("expander#0") < order.index("multiband_compressor#0")
    g, x = pre["gate#0"], pre["expander#0"]
    assert g["curve-threshold"] < x["threshold"] - 4                   # floor gate well below the expander
    assert g["curve-threshold"] > _a()["noise_floor"]                   # ...but above the room noise
    for noise, want in (("gate", ["gate#0"]), ("expander", ["expander#0"]), (None, [])):
        P, _ = _P({"noise": noise})
        o = ee.render(P)["input"]["plugins_order"]
        assert [k for k in o if k in ("gate#0", "expander#0")] == want, (noise, o)


def test_full_chain_order_and_completeness():
    P, _ = _P({"noise": "both", "rnnoise": True, "deep": True, "exciter": True})
    P["stereo_fix"] = "LL"
    pre = ee.render(P)["input"]
    want = ["stereo_tools#0", "gate#0", "expander#0", "rnnoise#0", "deepfilternet#0", "multiband_compressor#0",
            "equalizer#0", "compressor#0", "compressor#1", "exciter#0", "deesser#0", "deesser#1", "limiter#0",
            "level_meter#0"]
    assert pre["plugins_order"] == want, pre["plugins_order"]
    assert pre["stereo_tools#0"]["mode"] == "LR > LL (Mono Left Channel)"
    mb = pre["multiband_compressor#0"]
    assert mb["band0"]["compressor-enable"] and not mb["band1"]["compressor-enable"]
    assert mb["band1"]["enable-band"] and mb["band1"]["split-frequency"] == 150.0
    assert all(not mb[f"band{i}"]["enable-band"] for i in range(2, 8))
    assert mb["band0"]["sidechain-mode"] == "Peak" and mb["band0"]["attack-time"] <= 2
    assert P["rnnoise"]["vad"] is False                                # deep + VAD would chop words
    json.dumps(pre)


def test_tap_detection():
    a = _a()
    assert a["tap_n"] >= 5, a["tap_n"]
    assert a["tap_low_hi"] - a["low_voice_hi"] > 6                      # test knocks are clearly louder
    assert _a(taps=False)["tap_low_hi"] is None


def test_tap_catcher_reduces_knocks_not_voice():
    P, notes = _P()
    assert any("tap catcher" in n.lower() for n in notes)
    pre = ee.render(P)
    mb = pre["input"]["multiband_compressor#0"]
    knocks, voice = rd("taps.wav").astype(np.float64), rd("speech_1.wav").astype(np.float64)
    k1 = sim.multiband_compressor(knocks, mb)
    v1 = sim.multiband_compressor(voice, mb)
    print(f"\ntap catcher alone: knocks {lowpk(knocks):.1f} -> {lowpk(k1):.1f} dB, "
          f"voice bass {lowrms(v1) - lowrms(voice):+.2f} dB, voice overall "
          f"{10*np.log10(np.mean(v1**2)/np.mean(voice**2)):+.2f} dB")
    assert lowpk(k1) - lowpk(knocks) <= -6
    assert abs(lowrms(v1) - lowrms(voice)) <= 1.0
    # and through the whole chain it still helps
    q = copy.deepcopy(P)
    q["modules"]["taps"] = False
    t_on, t_off = sim.process(knocks, pre), sim.process(knocks, ee.render(q))
    assert lowpk(t_on) < lowpk(t_off) - 2


def test_tap_feedback_and_auto():
    P, _ = _P()
    thr, ratio, split = P["taps"]["thr"], P["taps"]["ratio"], P["taps"]["split"]
    changes = manual_feedback(P, ["k"])
    assert P["taps"]["thr"] < thr and P["taps"]["ratio"] > ratio and P["taps"]["split"] > split, changes
    for _ in range(20):
        manual_feedback(P, ["k"])
    assert P["taps"]["ratio"] <= 20 and P["taps"]["split"] <= 220
    # automatic: a processed take where knocks still poke out tightens the catcher, a clean one leaves it
    P, _ = _P()
    a = analyze_take(sim_take(P))
    thr = P["taps"]["thr"]
    a["tap_low_hi"], a["low_voice_hi"] = -5.0, -22.0
    auto_adjust(P, a)
    assert P["taps"]["thr"] < thr
    P, _ = _P()
    thr = P["taps"]["thr"]
    a["tap_low_hi"] = a["low_voice_hi"] + 1
    auto_adjust(P, a)
    assert P["taps"]["thr"] == thr


def sim_take(P):
    t = take_from(1, short=8)
    pre = ee.render(P)
    return {k: (sim.process(v, pre) if v is not None else None) for k, v in t.items() if k != "raw_speech"} | \
        {"raw_speech": t["speech"]}


def test_stereo_auto_fix():
    one = np.stack([rd("speech_0.wav"), np.zeros_like(rd("speech_0.wav"))], axis=1)
    nz = np.stack([rd("noise.wav"), rd("noise.wav") * 0.01], axis=1)
    a = analyze(nz, one, None, None)
    P, notes = initial_params(a, args())
    assert P["stereo_fix"] == "LL", P["stereo_fix"]
    assert ee.render(P)["input"]["plugins_order"][0] == "stereo_tools#0"
    P, _ = initial_params(a, args(mods_dict={"stereo": False}))
    assert "stereo_tools#0" not in ee.render(P)["input"]["plugins_order"]
    a2 = _a()
    P, _ = initial_params(a2, args())
    assert P["stereo_fix"] is None                                    # a normal mono-fallback mic: nothing added


def test_imported_preset_keeps_new_plugins():
    P, _ = _P({"noise": "both", "deep": True})
    pre = ee.render(P)
    Q, notes = ee.import_preset(copy.deepcopy(pre))
    m = Q["modules"]
    assert m["noise"] == "both" and m["taps"] and m["deep"], m
    assert abs(Q["taps"]["thr"] - P["taps"]["thr"]) < 0.11
    Q["taps"]["thr"] -= 3
    Q["deep"]["atten"] = 55.0
    Q["gate"]["thr"] -= 2
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "b.json"
        base.write_text(json.dumps(pre))
        out = ee.write_preset(Q, "b2", Path(d), base=base)
        new = json.loads(Path(out).read_text())["input"]
    assert new["multiband_compressor#0"]["band0"]["attack-threshold"] == round(P["taps"]["thr"] - 3, 1)
    assert new["deepfilternet#0"]["attenuation-limit"] == 55.0
    assert new["expander#0"]["threshold"] == round(Q["gate"]["thr"], 1)
    assert new["gate#0"]["curve-threshold"] == pre["input"]["gate#0"]["curve-threshold"]   # floor untouched
    # a one-sided mic gets the stereo fix inserted into an imported preset too
    Q["stereo_fix"] = "RR"
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "b.json"
        base.write_text(json.dumps(pre))
        out = ee.write_preset(Q, "b3", Path(d), base=base)
        new = json.loads(Path(out).read_text())["input"]
    assert new["plugins_order"][0] == "stereo_tools#0"


def test_deep_available_paths():
    import os
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("LADSPA_PATH")
        try:
            os.environ["LADSPA_PATH"] = d
            before = ee.deep_available()
            (Path(d) / "libdeep_filter_ladspa.so").write_bytes(b"")
            assert ee.deep_available() is True
            assert before in (False, "flatpak", True)
        finally:
            if old is None:
                os.environ.pop("LADSPA_PATH", None)
            else:
                os.environ["LADSPA_PATH"] = old


def test_record_profile_converges():
    from test_tuning import _run
    rows, text, out = _run(["", "n"] * 12, rounds_take=0, target=None, profile="record")
    from test_tuning import _table
    _table("record profile, target -24 LUFS, peaks under -6", rows)
    P = json.loads(sorted(out.glob("*.json"))[-1].read_text())["input"]
    last = rows[-3:]
    assert all(r["peak"] <= -5.5 for r in rows), [r["peak"] for r in rows]
    lufs = np.mean([r["lufs"] for r in last])
    print(f"\nrecord profile ends at {lufs:.1f} LUFS, peaks {max(r['peak'] for r in rows):.1f} dBFS")
    assert lufs > -26.5
    assert P["limiter#0"]["threshold"] == -1.0 and "compressor#1" not in P["plugins_order"]
