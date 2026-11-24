"""Hybrid speech test (free talk + test sentences) for every profile, thinking-pause measurements, and RNNoise
as the default noise remover."""
import contextlib
import io
import types

import numpy as np

from common import args, rd
from v2ee import audio
from v2ee import easyeffects as ee
from v2ee.analysis import analyze
from v2ee.config import DEFAULT_MODULES, SR
from v2ee.planner import initial_params
from v2ee.tuning import auto_adjust


def _fake_capture(mode, raw=None, **kw):
    calls = []

    def rec(secs, dev):
        calls.append(("rec", secs, dev))
        return np.full(int(secs * 100), 0.1, np.float32)       # 100 samples/s keeps it small

    def rec_multi(secs, devs, channels=2):
        calls.append(("multi", secs, tuple(devs)))
        return [np.full(int(secs * 100), 0.1, np.float32) for _ in devs]
    saved = (audio.record, audio.record_multi, audio.countdown, audio.route_problem)
    audio.record, audio.record_multi = rec, rec_multi
    audio.countdown = lambda *a, **k: None
    audio.route_problem = lambda d: None
    A = args(speech_test=mode, free_secs=35, read_secs=12, no_click_test=True, no_tap_test=True, **kw)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            take = audio.capture_live("easyeffects_source", A, raw=raw)
    finally:
        audio.record, audio.record_multi, audio.countdown, audio.route_problem = saved
    return take, calls, buf.getvalue()


def test_hybrid_speech_test():
    take, calls, txt = _fake_capture("hybrid", raw="alsa_input.podmic")
    assert "part 1 of 2, free talk" in txt and "part 2 of 2" in txt and "She sells sea shells" in txt
    secs = [c[1] for c in calls if c[0] == "multi"]
    assert secs == [35, 12]                                     # free talk, then the sentences, raw alongside
    assert len(take["speech"]) == len(take["raw_speech"]) == 35 * 100 + 12 * 100 - 480   # 10 ms crossfade
    for mode, want, txt_has in (("read", [20], "Last Tuesday"), ("free", [35], "Talk like you do")):
        take, calls, txt = _fake_capture(mode)
        assert [c[1] for c in calls if c[0] == "rec"][1:] == want and txt_has in txt   # [0] is room tone


def test_crossfade_join_has_no_click():
    a = np.ones(48000, np.float32) * 0.5
    b = -np.ones(48000, np.float32) * 0.5
    j = audio._join([a, b])
    assert len(j) == 96000 - 480 and np.max(np.abs(np.diff(j))) < 0.01


def test_thinking_pauses_measured():
    sp, nz = rd("speech_1.wav"), rd("noise.wav")
    pieces = []
    for i in range(4):
        pieces += [sp[i * 4 * SR:(i + 1) * 4 * SR], nz[: int(1.6 * SR)]]
    a = analyze(nz, np.concatenate(pieces), rd("clicks.wav"))
    print(f"\nthinking pauses: {a['long_pauses']}, background {a['pause_db']:+.1f} dB, restart {a['restart_db']:+.1f} dB")
    assert a["long_pauses"] >= 3 and -45 < a["pause_db"] < -25
    quiet = [p * (1.0 if k % 2 == 0 else 0.1) for k, p in enumerate(pieces)]    # pauses 20 dB quieter
    a2 = analyze(nz * 0.1, np.concatenate(quiet), rd("clicks.wav"))
    assert a2["pause_db"] < a["pause_db"] - 15


def test_pause_rules():
    a = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
    P, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "video"}))
    thr, att = P["gate"]["thr"], P["comp2"]["attack"]
    msgs = auto_adjust(P, dict(a, pause_db=-20.0, restart_db=6.0, click_hi30=None))
    assert P["gate"]["thr"] == thr + 1 and P["comp2"]["attack"] < att, msgs
    P, _ = initial_params(a, args(target_lufs=None, mods_dict={"profile": "video"}))
    auto_adjust(P, dict(a, pause_db=-60.0, restart_db=1.0, click_hi30=None))
    assert P["gate"]["thr"] == thr and P["comp2"]["attack"] == att


def test_rnnoise_is_the_default_everywhere():
    from v2ee import cli
    real = ee.deep_available
    ee.deep_available = lambda: True                            # even with DeepFilterNet installed
    try:
        for prof in ("stream", "video", "record"):
            m, deep = cli.default_modules(prof)
            assert m["rnnoise"] is True and m["deep"] is False and deep is True, prof
            assert DEFAULT_MODULES[prof]["rnnoise"] is True
    finally:
        ee.deep_available = real
