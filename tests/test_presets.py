"""Preset rendering: every plugin enabled, every option set, safe values; import fixes hazards."""
import copy
import json

from common import args, rd
from v2ee import easyeffects as ee
from v2ee.analysis import analyze
from v2ee.config import NEG_INF
from v2ee.planner import initial_params

REQUIRED = {
    "gate": ["bypass", "input-gain", "output-gain", "attack", "release", "curve-threshold", "curve-zone",
             "hysteresis", "hysteresis-threshold", "hysteresis-zone", "reduction", "makeup", "dry", "wet",
             "stereo-split", "hpf-mode", "hpf-frequency", "lpf-mode", "lpf-frequency", "sidechain",
             "input-to-sidechain", "input-to-link", "sidechain-to-input", "sidechain-to-link", "link-to-input",
             "link-to-sidechain"],
    "expander": ["bypass", "mode", "threshold", "release-threshold", "ratio", "knee", "attack", "release",
                 "makeup", "dry", "wet", "sidechain", "hpf-mode", "lpf-mode", "input-to-link"],
    "rnnoise": ["bypass", "enable-vad", "vad-thres", "release", "wet", "use-standard-model", "model-name"],
    "equalizer": ["bypass", "mode", "num-bands", "split-channels", "balance", "pitch-left", "pitch-right",
                  "left", "right"],
    "compressor": ["bypass", "mode", "attack", "release", "threshold", "release-threshold", "ratio", "knee",
                   "makeup", "boost-threshold", "boost-amount", "dry", "wet", "stereo-split", "hpf-mode",
                   "hpf-frequency", "lpf-mode", "lpf-frequency", "sidechain", "input-to-sidechain",
                   "link-to-sidechain"],
    "deesser": ["bypass", "detection", "mode", "threshold", "ratio", "laxity", "makeup", "f1-freq", "f1-level",
                "f2-freq", "f2-level", "f2-q", "sc-listen"],
    "exciter": ["bypass", "amount", "harmonics", "scope", "ceil", "ceil-active", "blend", "listen"],
    "limiter": ["bypass", "mode", "oversampling", "dithering", "sidechain-type", "sidechain-preamp", "threshold",
                "lookahead", "attack", "release", "stereo-link", "gain-boost", "alr", "alr-attack", "alr-release",
                "alr-knee", "input-to-sidechain", "link-to-input"],
}


def _preset(mods):
    a = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
    P, _ = initial_params(a, args(mods_dict=mods))
    return P, ee.render(P)


def test_everything_enabled_and_complete():
    for noise in ("gate", "expander"):
        mods = {"noise": noise, "rnnoise": True, "eq": True, "comp": True, "deess": True, "exciter": True,
                "limiter": True}
        P, pre = _preset(mods)
        inp = pre["input"]
        order = inp["plugins_order"]
        assert ("gate#0" in order) != ("expander#0" in order)
        assert order.count("deesser#0") == 1 and order.count("deesser#1") == 1
        for key in order:
            blk, kind = inp[key], key.split("#")[0]
            assert blk["bypass"] is False, f"{key} is bypassed"
            missing = [k for k in REQUIRED[kind] if k not in blk]
            assert not missing, f"{key} missing {missing}"
        assert inp["limiter#0"]["gain-boost"] is False
        assert inp["limiter#0"]["alr"] is False and inp["limiter#0"]["dithering"] == "None"
        assert inp["deesser#0"]["sc-listen"] is False and inp["deesser#1"]["sc-listen"] is False
        assert inp["exciter#0"]["listen"] is False
        for key in order:
            for pm in ("input-to-sidechain", "input-to-link", "sidechain-to-input", "sidechain-to-link",
                       "link-to-input", "link-to-sidechain"):
                if pm in inp[key]:
                    assert inp[key][pm] == NEG_INF, f"{key} {pm} not off"
            if "dry" in inp[key]:
                assert inp[key]["dry"] == NEG_INF
        if noise == "expander":
            assert inp["expander#0"]["mode"] == "Downward"
        json.dumps(pre)


def test_deessers_stay_enabled_while_tuning():
    from v2ee.tuning import auto_adjust
    P, _ = _preset({"noise": "gate", "rnnoise": True, "eq": True, "comp": True, "deess": True, "exciter": False,
                    "limiter": True})
    a = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
    for _ in range(12):  # very low sibilance every round -> softer, never off
        a2 = dict(a, sib_diff=-10.0)
        auto_adjust(P, a2)
    pre = ee.render(P)
    assert pre["input"]["deesser#0"]["bypass"] is False and pre["input"]["deesser#1"]["bypass"] is False
    assert P["deess"]["ratio"] >= 1.5


def test_import_fixes_hazards_and_enables_deessers():
    P, pre = _preset({"noise": "expander", "rnnoise": True, "eq": True, "comp": True, "deess": True,
                      "exciter": True, "limiter": True})
    bad = copy.deepcopy(pre)
    inp = bad["input"]
    inp["limiter#0"]["gain-boost"] = True
    inp["expander#0"]["mode"] = "Upward"
    inp["deesser#1"]["bypass"] = True
    inp["deesser#0"]["sc-listen"] = True
    inp["exciter#0"]["listen"] = True
    Pi, notes = ee.import_preset(bad)
    out = ee.overrides(Pi, inp)
    merged = ee.deep_merge(copy.deepcopy(inp), out)
    assert merged["limiter#0"]["gain-boost"] is False
    assert merged["expander#0"]["mode"] == "Downward"
    assert merged["deesser#1"]["bypass"] is False
    assert merged["deesser#0"]["sc-listen"] is False
    assert merged["exciter#0"]["listen"] is False
    text = "\n".join(notes)
    assert "Threshold Boost" in text and "UPWARD" in text and "deesser#1 was bypassed" in text


def test_server_property_names():
    assert ee._camel("curve-threshold") == "curveThreshold"
    assert ee._camel("gain-boost") == "gainBoost"
    assert ee._camel("bypass") == "bypass"
    assert ee._parse("true") is True and ee._parse("-20.5") == -20.5 and ee._parse("threshold: 3") == 3.0
