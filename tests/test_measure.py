"""Step 1 of 'measure everything': time constants, exciter range, high-pass slope and de-esser width come from
measurements of the voice, change in the right direction when the voice/room changes, and land in the preset."""
import json

import numpy as np
from scipy import signal

from common import args, rd
from v2ee import easyeffects as ee
from v2ee.analysis import analyze
from v2ee.planner import initial_params
from v2ee.tuning import auto_adjust, manual_feedback

SR = 48000


def _an(speech, noise=None):
    return analyze(rd("noise.wav") if noise is None else noise, speech, rd("clicks.wav"), rd("taps.wav"))


def _P(a, **mods):
    return initial_params(a, args(target_lufs=None, mods_dict=mods))


def _reverb(x, secs):
    """Add a room tail: exponentially decaying noise impulse response (RT60 = secs)."""
    r = np.random.default_rng(1)
    n = int(secs * SR)
    ir = r.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
    ir[0] = 1.0 / 0.3
    y = signal.fftconvolve(x, ir * 0.3)[: len(x)]
    return (y / np.abs(y).max() * np.abs(x).max()).astype(np.float32)


def test_room_echo_measured_and_used():
    dry = rd("speech_1.wav")
    a0, a1 = _an(dry), _an(_reverb(dry, 0.8))
    print(f"\nword tails: dry {a0['decay_ms']:.0f} ms, echoey room {a1['decay_ms']:.0f} ms")
    assert a1["decay_ms"] > a0["decay_ms"] + 20
    P0, _ = _P(a0, profile="record", noise="expander")
    P1, _ = _P(a1, profile="record", noise="expander")
    assert P1["gate"]["release"] > P0["gate"]["release"]                 # record: keep the natural tail
    S1, _ = _P(a1, profile="stream", noise="gate")
    assert S1["gate"]["release"] < 0.85 * a1["decay_ms"] + 1             # stream: close before the echo is over
    assert S1["gate"]["release"] <= 0.8 * a1["gap_ms"] + 1 or S1["gate"]["release"] <= 60  # ...and inside a pause


def test_fast_talker_gets_faster_dynamics():
    x = rd("speech_1.wav")
    fast = signal.resample(x, int(len(x) / 1.35)).astype(np.float32)   # 35 % faster
    a0, a1 = _an(x), _an(fast)
    print(f"\nsyllables/s: normal {a0['syl_rate']:.1f}, fast {a1['syl_rate']:.1f}")
    assert a1["syl_rate"] > a0["syl_rate"] * 1.15
    P0, _ = _P(a0)
    P1, _ = _P(a1)
    assert P1["comp"]["release"] < P0["comp"]["release"]
    assert P1["comp2"]["release"] <= P0["comp2"]["release"]
    assert P1["lim_t"]["release"] <= P0["lim_t"]["release"]


def test_exciter_follows_the_voice():
    x = rd("speech_0.wav")
    dull = signal.sosfilt(signal.butter(6, 3500, "low", fs=SR, output="sos"), x).astype(np.float32)
    bp = signal.sosfilt(signal.butter(2, [6000, 8000], "band", fs=SR, output="sos"), x)
    bright = (x + 3.0 * bp).astype(np.float32)
    ad, ab = _an(dull), _an(bright)
    Pd, _ = _P(ad, exciter=True)
    Pb, notes = _P(ab, exciter=True)
    print(f"\nexciter scope: dull voice {Pd['exciter']['scope']} Hz, bright voice {Pb['exciter']['scope']} Hz "
          f"('s' band {ab['sib_lo']:.0f}-{ab['sib_hi']:.0f})")
    assert Pd["exciter"]["scope"] <= 4500 and Pd["exciter"]["amount"] >= 2.0
    assert Pb["exciter"]["scope"] >= 7000 and Pb["exciter"]["scope"] > ab["sib_hi"]
    for P in (Pd, Pb):
        assert P["exciter"]["ceil"] >= P["exciter"]["scope"] + 2000 and P["exciter"]["ceil"] <= 16000
    R, _ = initial_params(ad, args(target_lufs=None, mods_dict={"profile": "record", "exciter": True}))
    assert R["exciter"]["ceil"] >= Pd["exciter"]["ceil"]                 # lossless keeps more top end


def test_rumble_steepens_high_pass():
    sp = rd("speech_1.wav")
    n = rd("noise.wav")
    t = np.arange(len(n)) / SR
    rum = (n + 0.004 * np.sin(2 * np.pi * 45 * t)).astype(np.float32)
    a0, a1 = _an(sp), _an(sp, noise=rum)
    P0, _ = _P(a0)
    P1, notes = _P(a1)
    print(f"\nrumble {a0['rumble_db']:.0f} -> {a1['rumble_db']:.0f} dB: HPF {P0['eq']['hpf']} Hz {P0['eq']['hpf_slope']}"
          f" -> {P1['eq']['hpf']} Hz {P1['eq']['hpf_slope']}")
    assert P1["eq"]["hpf_slope"] == "x4" and P1["eq"]["hpf"] >= P0["eq"]["hpf"]
    assert any("rumble" in x for x in notes)


def test_measured_values_land_in_the_preset():
    a = _an(rd("speech_2.wav"))
    P, _ = _P(a, noise="both", rnnoise=True, exciter=True)
    inp = ee.render(P)["input"]
    assert inp["gate#0"]["attack"] == P["gate"]["attack"] == inp["expander#0"]["attack"]
    assert inp["gate#0"]["release"] == P["floor"]["release"]
    assert inp["expander#0"]["release"] == P["gate"]["release"]
    assert inp["compressor#0"]["attack"] == P["comp"]["attack"] and inp["compressor#1"]["release"] == P["comp2"]["release"]
    assert inp["limiter#0"]["lookahead"] == P["lim_t"]["lookahead"] and inp["limiter#0"]["release"] == P["lim_t"]["release"]
    assert inp["rnnoise#0"]["release"] == P["rnnoise"]["release"]
    assert inp["multiband_compressor#0"]["band0"]["release-time"] == P["taps"]["release"]
    assert inp["deesser#1"]["f2-q"] == P["deess2"]["q"] and inp["deesser#0"]["f1-freq"] == P["deess"]["split"]
    assert inp["equalizer#0"]["left"]["band0"]["slope"] == P["eq"]["hpf_slope"]
    assert inp["exciter#0"]["scope"] == P["exciter"]["scope"] and inp["exciter#0"]["ceil"] == P["exciter"]["ceil"]
    # nothing is still the old hard-coded value by accident
    assert inp["limiter#0"]["release"] != 50.0 or P["lim_t"]["release"] == 50.0
    assert 1.5 <= P["gate"]["attack"] <= 8 and 10 <= P["comp"]["attack"] <= 40 and 0.5 <= P["comp2"]["attack"] <= 5
    json.dumps(inp)


def test_feedback_moves_exciter_scope():
    P, _ = _P(_an(rd("speech_0.wav")), exciter=True)
    s0 = P["exciter"]["scope"]
    manual_feedback(P, ["d"])
    assert P["exciter"]["scope"] == max(3000, s0 - 500)
    manual_feedback(P, ["h"])
    manual_feedback(P, ["h"])
    assert P["exciter"]["scope"] > s0 - 1
    assert P["exciter"]["ceil"] >= P["exciter"]["scope"] + 2000


def test_auto_echo_check():
    a = _an(rd("speech_1.wav"))
    P, _ = _P(a, noise="expander")
    rel = P["gate"]["release"]
    proc = dict(a)
    proc["decay_ms"] = P["timing"]["decay"] + 60                        # processing lifted the room tails
    msgs = auto_adjust(P, proc)
    assert P["gate"]["release"] < rel, msgs
    assert any("word tails" in m for m in msgs)
    P, _ = _P(a, noise="expander")
    proc["decay_ms"] = P["timing"]["decay"]
    auto_adjust(P, proc)
    assert P["gate"]["release"] == rel


def test_full_readback_checks_every_setting():
    from test_replay import _env
    tmp, fake = _env(faults={("gate#0", "attack"): 42.0})
    try:
        a0 = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
        P, _ = initial_params(a0, args())
        path = ee.write_preset(P, "t_v1", tmp / "presets")
        assert ee.load_preset("t_v1") == "server"
        pre = json.loads(path.read_text())
        quick = ee.verify_loaded(pre)
        full = ee.verify_loaded(pre, full=True)
        print(f"\nread-back: quick {quick['checked']} settings, full {full['checked']} settings")
        assert full["checked"] > 2 * quick["checked"]
        assert not quick["mismatch"]                                     # attack isn't in the quick set...
        assert any("gate#0 attack" in m for m in full["mismatch"])       # ...but the full check catches it
    finally:
        fake.close()
