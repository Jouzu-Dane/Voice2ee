"""Closed-loop tuning against the simulated chain: gain stays capped, peaks stay under the ceiling,
loudness converges toward the LUFS target, versions are named correctly."""
import builtins
import contextlib
import io
import json
import tempfile
from pathlib import Path

from common import args, processed_take, rd, take_from
import v2ee.loop as loop
import v2ee.session as session
from v2ee import easyeffects as ee
from v2ee.analysis import analyze, analyze_take
from v2ee.planner import initial_params


def _run(feedback, rounds_take=None, target=-18.0, level_db=0.0, profile="stream"):
    session.CACHE_DIR = Path(tempfile.mkdtemp())
    A = args(target_lufs=target, mods_dict={"profile": profile})
    k = 10 ** (level_db / 20)            # level_db > 0: as if you sat closer to the mic
    a0 = analyze(rd("noise.wav"), rd("speech_0.wav") * k, rd("clicks.wav"))
    P, _ = initial_params(a0, A)
    out = Path(tempfile.mkdtemp())
    ee.write_preset(P, "t_v1", out)
    sess = session.Session(out, "t", 1, "t_v1")
    log, n = [], {"i": 0}

    def measure():
        pre = json.loads((out / f"{sess.current}.json").read_text())
        i = rounds_take if rounds_take is not None else n["i"] % 6
        n["i"] += 1
        tk = take_from(i)
        tk["speech"] = tk["speech"] * k
        a = analyze_take(processed_take(tk, pre))
        log.append(dict(makeup=P["comp"]["makeup"], gain=a.get("applied_gain"), lufs=a["lufs"], peak=a["peak_db"]))
        return a

    real_apply = loop.apply
    loop.apply = lambda s, name, first=False: None
    answers = iter(feedback + ["q", "n"])
    builtins.input = lambda p="": next(answers)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            loop.tune_loop(P, sess, measure, test_mode="replay" if rounds_take is not None else "live")
    finally:
        loop.apply = real_apply                     # don't leak the stub into other test files
    return log, buf.getvalue(), out


def _table(title, log):
    print(f"\n{title}\n round | makeup | gain on voice | loudness LUFS | max peak dBFS")
    for k, r in enumerate(log, 1):
        print(f" {k:5d} | {r['makeup']:5.1f}  | {r['gain']:+6.1f}        | {r['lufs']:7.1f}       | {r['peak']:6.1f}")


def test_converges_safely_live_style():
    log, txt, _ = _run(["", "n"] * 10, level_db=4.0)   # Enter each round, don't keep runs
    _table("live-style (different take each round), voice 4 dB closer, target -18 LUFS", log)
    assert all(r["gain"] <= 12 + 3 for r in log)
    assert all(r["peak"] <= -5.5 for r in log)
    ups = [b["makeup"] - a["makeup"] for a, b in zip(log, log[1:])]
    assert max(ups) <= 1.51
    assert abs(log[-1]["lufs"] + 18) < 1.5


def test_converges_replay_style():
    log, txt, _ = _run(["", "n"] * 10, rounds_take=2, level_db=4.0)   # same recording every round (record once)
    _table("replay-style (same recording every round), voice 4 dB closer, target -18 LUFS", log)
    assert all(r["peak"] <= -5.5 for r in log)
    assert abs(log[-1]["lufs"] + 18) < 1.0


def test_real_mic_level():
    log, txt, _ = _run(["", "n"] * 10, rounds_take=2)   # the PodMic level measured earlier (gain at max)
    _table("replay-style at the measured PodMic level, target -18 LUFS", log)
    assert all(r["gain"] <= 12 + 3 for r in log) and all(r["peak"] <= -5.5 for r in log)
    assert abs(log[-1]["lufs"] + 18) < 1.5 or ("+12 dB limit" in txt and "raise" in txt)


def test_unreachable_target_says_why():
    log, txt, _ = _run(["", "n"] * 8, rounds_take=2, target=-12.0)
    assert all(r["gain"] <= 12 + 3 for r in log) and all(r["peak"] <= -5.5 for r in log)
    assert ("limit" in txt and "raise" in txt) or "already firm" in txt


def test_louder_spam_respects_cap():
    log, txt, _ = _run(["u", "n"] * 7)
    _table("pressing 'u' (louder) every round", log)
    assert all(r["gain"] <= 12 + 3 for r in log)
    assert all(r["peak"] <= -5.5 for r in log)


def test_versions():
    _, _, out = _run(["", "y", "", "n"])
    files = sorted(p.stem for p in out.glob("*.json"))
    print("\nfiles after a session:", files)
    assert "t_v1" in files and "t_v1-1" in files and "t_v2" in files
    s = session.Session(out, "t", 1, "t_v1")
    assert s.run_name(s.run + 1) == f"t_v1-{max(int(f.split('-')[1]) for f in files if '-' in f) + 1}"
    assert session.split_name("my-voice") == ("my-voice", None)
    assert session.split_name("test_v3-2") == ("test", 3)
    assert session.next_version(out, "t") == 3
