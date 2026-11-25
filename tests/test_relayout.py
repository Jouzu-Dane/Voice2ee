"""More / fewer EQ bands when tuning an EXISTING preset: same sound, your notches and filters kept."""
import copy
import json
import tempfile
from pathlib import Path

import numpy as np

from common import args, rd
from v2ee import easyeffects as ee
from v2ee.analysis import analyze
from v2ee.eq import GRID, curve
from v2ee.planner import initial_params
from v2ee.tuning import manual_feedback


def _existing():
    a = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
    P, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "stream"}))
    pre = ee.render(P)
    eqb = pre["input"]["equalizer#0"]
    n = eqb["num-bands"]
    for side in ("left", "right"):                       # your own hand-made notch and a low-pass
        eqb[side][f"band{n}"] = ee.eq_band(2700, -6, 8.0, "Bell")
        eqb[side][f"band{n + 1}"] = ee.eq_band(15000, 0, 0.7, "Lo-pass")
    eqb["num-bands"] = n + 2
    return pre


def _write(Q, pre):
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "mine.json"
        base.write_text(json.dumps(pre))
        out = ee.write_preset(Q, "mine_v1-1", Path(d), base=base)
        return json.loads(Path(out).read_text())["input"]["equalizer#0"], json.loads(base.read_text())


def test_more_bands_keeps_the_sound_and_your_special_bands():
    pre = _existing()
    Q, _ = ee.import_preset(copy.deepcopy(pre))
    before = curve(Q["eq"]["bands"])
    n_reg = len([b for b in Q["eq"]["bands"] if not b.get("special")])
    msg = manual_feedback(Q, ["+"])[0]
    print("\n" + msg)
    assert "re-spread" in msg and f"{n_reg} -> {n_reg + 4}" in msg and "narrow band" in msg
    err = float(msg.split("within ")[1].split(" dB")[0])
    assert err <= 1.5
    band = (GRID >= 120) & (GRID <= 12000)
    sm = lambda x: np.convolve(np.pad(x, 1, mode="edge"), np.ones(3) / 3, "valid")   # noqa: E731
    assert np.max(np.abs(sm(curve(Q["eq"]["bands"]) - before)[band])) <= 1.6
    eqb, base_after = _write(Q, pre)
    bands = [eqb["left"][f"band{i}"] for i in range(eqb["num-bands"])]
    assert eqb["num-bands"] == len(bands) == len(pre["input"]["equalizer#0"]["left"]) + 4
    assert bands[0]["type"] == "Hi-pass" and bands[-1]["type"] == "Lo-pass"
    notch = [b for b in bands if b["type"] == "Bell" and b["q"] > 4]
    assert notch == [ee.eq_band(2700, -6, 8.0, "Bell")]                   # exactly as you made it
    assert eqb["right"] == eqb["left"]
    assert base_after == pre                                              # your original file untouched


def test_tuning_after_respread_is_written():
    pre = _existing()
    Q, _ = ee.import_preset(copy.deepcopy(pre))
    manual_feedback(Q, ["+"])
    manual_feedback(Q, ["b"])                                             # boxy: less 250-630 Hz
    eqb, _ = _write(Q, pre)
    written = {round(b["frequency"]): b["gain"] for b in eqb["left"].values() if b["type"] == "Bell" and b["q"] <= 4}
    mine = {round(b["f"]): b["g"] for b in Q["eq"]["bands"] if not b.get("special") and b["t"] == "Bell"}
    assert written == {k: round(v, 2) for k, v in mine.items()}


def test_fewer_bands_and_limits():
    pre = _existing()
    Q, _ = ee.import_preset(copy.deepcopy(pre))
    n_reg = len([b for b in Q["eq"]["bands"] if not b.get("special")])
    msgs = manual_feedback(Q, ["-"])
    assert f"{n_reg} -> {max(5, n_reg - 4)}" in msgs[0]
    for _ in range(5):
        msgs = manual_feedback(Q, ["-"])
    assert "already at min" in msgs[0]
