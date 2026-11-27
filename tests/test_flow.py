"""New-preset flow: after the first preset (and Optimise's before/after) nothing runs through EasyEffects until
you choose. Menu: 1) happy, quit  2) (re-)run Optimise  3) tune -> feedback keys / A/B / both."""
import builtins
import contextlib
import copy
import io
import json
import sys
import tempfile
from pathlib import Path

from common import rd, take_from


def _run(answers, extra=()):
    import v2ee.cli as cli
    from v2ee import easyeffects as ee
    from v2ee import fit
    out = Path(tempfile.mkdtemp())
    calls = {"optimise": 0, "tune": [], "load": [], "ab": 0}
    saved = []

    def patch(obj, name, val):
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)
    take = take_from(0)
    patch(cli, "record_raw_take", lambda a, e, c: (take, None))
    patch(cli, "save_take", lambda t, f: None)
    patch(cli, "_exact_status", lambda a: {"exact": (False, []), "quality": (False, ""), "deep": False})

    def fake_opt(P, t, a, st, **k):
        calls["optimise"] += 1
        P = copy.deepcopy(P)
        P["comp"]["makeup"] += 1.0
        return P
    patch(fit, "run_optimise", fake_opt)

    def fake_ab_runner(*a, **k):
        def run(P):
            calls["ab"] += 1
            return {"bright": 0.5}, 6
        return run
    patch(fit, "make_ab_runner", fake_ab_runner)
    patch(cli, "tune_loop", lambda P, sess, measure, **kw: calls["tune"].append(kw))
    patch(ee, "load_preset", lambda name, path=None: calls["load"].append(name) or "server")
    it = iter(answers)
    builtins.input = lambda p="": next(it)
    old_argv = sys.argv
    sys.argv = ["voice2ee", "--mode", "1", "--profile", "stream", "--modules",
                "gate,expander,rnnoise,eq,comp,deess,limiter", "--name", "t", "--engine", "optimise", "--replay",
                "--out", str(out), "--ee-device", "easyeffects_source", *extra]
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            cli.main()
    finally:
        sys.argv = old_argv
        for obj, name, val in reversed(saved):
            setattr(obj, name, val)
    return calls, buf.getvalue(), out


def test_nothing_runs_automatically_after_optimise():
    calls, txt, out = _run(["1"])                                     # happy, quit
    assert calls["optimise"] == 1 and calls["tune"] == [] and calls["load"] == ["t_v1"]
    assert "What next?" in txt and "Re-run Optimise" in txt and "Saved and loaded: t_v1" in txt


def test_rerun_then_ab_only_then_happy():
    calls, txt, out = _run(["2", "3", "2", "1"])                      # re-run, tune -> A/B, happy
    assert calls["optimise"] == 2 and calls["ab"] == 1 and calls["tune"] == []   # A/B never starts rounds
    assert "Moved to your taste" in txt and calls["load"] == ["t_v1"]
    pre = json.loads((out / "t_v1.json").read_text())["input"]
    assert pre["compressor#0"]["makeup"] > 0


def test_tune_keys_and_both():
    calls, txt, _ = _run(["3", "1"])                                  # tune -> feedback keys
    assert len(calls["tune"]) == 1 and calls["tune"][0]["start_ab"] is False
    calls, txt, _ = _run(["3", "3"])                                  # tune -> both
    assert len(calls["tune"]) == 1 and calls["tune"][0]["start_ab"] is True
    assert "How do you want to tune?" in txt


def test_finetune_flag_skips_the_menu():
    calls, txt, _ = _run([], extra=("--finetune", "keys"))
    assert len(calls["tune"]) == 1 and "What next?" not in txt
