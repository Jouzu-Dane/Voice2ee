"""The Video profile (ready to publish), loudness range / true peak, and the faster optimiser."""
import builtins
import types

import numpy as np

from common import args, rd, take_from
from v2ee import easyeffects as ee
from v2ee import fit, loudness, offline, score, search
from v2ee.analysis import analyze
from v2ee.config import PROFILES, SR
from v2ee.planner import finalize_mods, initial_params
from v2ee.simchain import _movavg
from v2ee.tuning import auto_adjust


def test_loudness_range_and_true_peak():
    r = np.random.default_rng(0)
    steady = r.standard_normal(30 * SR) * 0.05
    lra0 = loudness.loudness_range(steady)
    seg = int(5 * SR)
    up_down = np.concatenate([steady[i * seg:(i + 1) * seg] * (1.0 if i % 2 else 10 ** (-10 / 20)) for i in range(6)])
    lra10 = loudness.loudness_range(up_down)
    print(f"\nLRA: steady {lra0:.1f} LU, 10 dB steps {lra10:.1f} LU")
    assert lra0 < 1.5 and 8.0 < lra10 < 11.0
    assert loudness.loudness_range(steady[: SR]) is None               # too short to say
    n = np.arange(SR)
    x = np.sin(2 * np.pi * (SR / 4) * n / SR + np.pi / 4) * 0.5        # peaks fall between samples
    sp = 20 * np.log10(np.abs(x).max())
    tp = loudness.true_peak(x)
    assert tp > sp + 2.5, (sp, tp)                                      # ~3 dB hidden between the samples


def test_video_profile_plan():
    a = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"), rd("taps.wav"))
    P, notes = initial_params(a, args(target_lufs=None, mods_dict={"profile": "video"}))
    pr = PROFILES["video"]
    assert P["target_lufs"] == -16.0 and P["limiter"] == pr["limiter"] and P["max_gain"] == 16.0
    assert P["modules"]["comp2"] and P["modules"]["noise"] == "both" and not P["rnnoise"]["vad"]
    S, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "stream"}))
    R, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "record"}))
    # video: full-strength EQ like stream (record is 70 %), gentler background cut than stream
    assert abs(sum(b["g"] for b in P["eq"]["bands"]) - sum(b["g"] for b in S["eq"]["bands"])) < 0.5
    assert abs(sum(b["g"] for b in R["eq"]["bands"])) < abs(sum(b["g"] for b in P["eq"]["bands"]))
    assert pr["gate_reduction"] > PROFILES["stream"]["gate_reduction"]
    assert P["timing"]["gate_rel"] >= S["timing"]["gate_rel"]           # never cuts word tails short
    assert finalize_mods({"profile": "video"})["profile"] == "video"


def test_video_tuning_converges():
    from test_tuning import _run, _table
    rows, text, out = _run(["", "n"] * 12, rounds_take=0, target=None, profile="video")
    _table("video profile, target -16 LUFS, true peak <= -1 dBTP", rows)
    assert abs(rows[-1]["lufs"] + 16) < 1.0
    tps = [float(l.split("true peak ")[1].split()[0]) for l in text.splitlines() if "true peak " in l and "dBTP (" in l]
    lras = [float(l.split("loudness range ")[1].split()[0]) for l in text.splitlines() if l.startswith("loudness range")]
    print(f"true peaks {tps[-1]:.1f} dBTP, loudness range {lras[-1]:.1f} LU")
    assert tps[-1] <= -1.0 + 0.3 and 4.5 <= lras[-1] <= 8.5
    assert "Recording tips (video profile" in text


def test_lra_and_true_peak_rules():
    a = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
    P, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "video"}))
    r0, lim0 = P["comp"]["ratio"], P["limiter"]
    proc = dict(a, lra=12.0, true_peak=0.2, lufs=-16.0)
    msgs = auto_adjust(P, proc)
    assert P["comp"]["ratio"] > r0 and P["limiter"] < lim0, msgs
    assert any("loudness range" in m for m in msgs) and any("true peak" in m for m in msgs)
    S, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "stream"}))
    rs = S["comp"]["ratio"]
    auto_adjust(S, dict(a, lra=12.0, lufs=-18.0))
    assert S["comp"]["ratio"] == rs or "lra" not in PROFILES["stream"] or PROFILES["stream"]["lra"] is None


def test_choose_profile_three_ways():
    from v2ee.cli import choose_profile
    for ans, want in (("", "stream"), ("1", "stream"), ("2", "video"), ("3", "record")):
        it = iter([ans])
        builtins.input = lambda p="": next(it)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert choose_profile(types.SimpleNamespace(profile=None)) == want
    txt = buf.getvalue()
    assert "Quick rule" in txt and "Video" in txt and "baked in" in txt


def test_fast_moving_average_matches():
    x = np.random.default_rng(1).standard_normal(5001)
    for n in (1, 5, 48, 1440):
        assert np.allclose(_movavg(x, n), np.convolve(x, np.ones(n) / n, "same"), atol=1e-12)


def test_ctrl_c_keeps_best_so_far():
    t = take_from(0)
    a = analyze(t["noise"], t["speech"], t["clicks"], t["taps"])
    P, _ = initial_params(a, args(target_lufs=None))
    chain = offline.ApproxChain()
    calls = {"n": 0}

    def batch(Ps):
        calls["n"] += 1
        if calls["n"] == 4:
            raise KeyboardInterrupt
        return [score.scorecard(offline.process_take(chain, t, ee.render(p), short=5), p) for p in Ps]
    info = {}
    best, s, ab, n = search.optimise(P, None, trials=200, batch=batch, info=info, seed=3)
    assert info["stopped"] and n == 1 + 2 * 6 and s < score.REJECT


def test_parallel_optimise():
    t = take_from(0)
    a = analyze(t["noise"], t["speech"], t["clicks"], t["taps"])
    P, _ = initial_params(a, args(target_lufs=None))
    lines = []
    A = types.SimpleNamespace(goal="balanced", trials=14, no_taste=True, no_quality=True, jobs=2)
    best = fit.run_optimise(P, t, A, {"exact": (False, []), "quality": (False, "")},
                            out=lambda *x, **k: lines.append(" ".join(map(str, x))))
    txt = "\n".join(lines)
    assert "2 CPU cores" in txt and "Best:" in txt and "parallel workers stopped" not in txt
    assert fit._W == {}                                                  # nothing left behind
