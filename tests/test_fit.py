"""Steps 2 and 3: scorecard + ESTOI, optimiser (approximate chain), locks/ranges, A/B taste learning, the
exact LV2 host (against a fake lilv), DNSMOS wrapper (fake session), and the installer's editions."""
import builtins
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

from common import HERE, args, rd, take_from
from v2ee import easyeffects as ee
from v2ee import fit, offline, quality, score, search, taste
from v2ee.analysis import analyze
from v2ee.config import CONFIG_DIR, SR
from v2ee.planner import initial_params


def _P(**mods):
    t = take_from(0)
    a = analyze(t["noise"], t["speech"], t["clicks"], t["taps"])
    P, _ = initial_params(a, args(target_lufs=None, mods_dict=mods or {"profile": "stream"}))
    return P, t


def _overrides(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    f = CONFIG_DIR / "overrides.json"
    f.write_text(json.dumps(data))
    return f


# ------------------------------------------------------------------------------------------- scorecard
def test_estoi_behaves():
    x = rd("speech_1.wav")[: 8 * SR].astype(np.float64)
    r = np.random.default_rng(0)
    same = score.estoi(x, x * 0.3)                                     # level change: still identical
    noisy = score.estoi(x, x + r.standard_normal(len(x)) * np.sqrt(np.mean(x ** 2)))   # 0 dB SNR
    chop = x.copy()
    for s in range(0, len(x), int(0.2 * SR)):
        chop[s: s + int(0.08 * SR)] = 0                                 # 40 % of every 200 ms removed
    cut = score.estoi(x, chop)
    print(f"\nESTOI: same {same:.3f}, 0 dB noise {noisy:.3f}, chopped {cut:.3f}")
    assert same > 0.99 and noisy < 0.75 and cut < 0.85


def test_scorecard_rejects_limits():
    P, t = _P()
    proc = offline.process_take(offline.ApproxChain(), t, ee.render(P), short=6)
    s, parts, a = score.scorecard(proc, P)
    assert s < score.REJECT and set(parts) >= {"loud", "clicks", "words", "tone", "noise"}
    hot = dict(proc, speech=proc["speech"] * 10 ** (12 / 20))          # 12 dB too hot: peaks + gain over limits
    s2, _, a2 = score.scorecard(hot, P)
    assert s2 >= score.REJECT and "peaks" in a2["rejected"]
    # goals re-weight: 'keyboard' cares more about clicks than 'natural'
    assert score.GOALS["keyboard"]["w"]["clicks"] > score.GOALS["natural"]["w"]["clicks"]


# ------------------------------------------------------------------------------------------- optimiser
def test_optimiser_improves_and_stays_safe():
    P, t = _P()
    chain = offline.ApproxChain()

    def ev(Pc):
        return score.scorecard(offline.process_take(chain, t, ee.render(Pc), short=6), Pc)
    s0, _, _ = ev(P)
    best, sb, ab, n = search.optimise(P, ev, trials=36, seed=1)
    print(f"\noptimiser: {s0:.2f} -> {sb:.2f} in {n} tries ({score.summary(ab)})")
    assert n == 36 and sb < s0 * 0.8 and not ab.get("rejected")
    assert best["comp"]["makeup"] <= P["max_gain"] and best["limiter"] == P["limiter"]
    assert "_eqg" in best                                               # (removed by fit.run_optimise)


def test_locks_and_ranges_are_respected_everywhere():
    f = _overrides({"lock": {"gate.release": 120, "limiter": -3}, "range": {"comp.ratio": [1.5, 2.0]}})
    try:
        P, t = _P()
        paths = [d[0] for d in search.constrained_space(P)]
        assert "gate.release" not in paths
        lo, hi = next((d[1], d[2]) for d in search.constrained_space(P) if d[0] == "comp.ratio")
        assert lo >= 1.5 and hi <= 2.0
        chain = offline.ApproxChain()
        best, *_ = search.optimise(P, lambda Pc: score.scorecard(
            offline.process_take(chain, t, ee.render(Pc), short=5), Pc), trials=12, seed=2)
        assert best["gate"]["release"] == 120 and best["limiter"] == -3 and 1.5 <= best["comp"]["ratio"] <= 2.0
        P["comp"]["ratio"], P["gate"]["release"] = 5.0, 60.0             # e.g. a feedback key pushed them
        with tempfile.TemporaryDirectory() as d:
            pre = json.loads(Path(ee.write_preset(P, "x", Path(d))).read_text())["input"]
        assert pre["compressor#0"]["ratio"] == 2.0 and pre["expander#0"]["release"] == 120.0
        assert pre["limiter#0"]["threshold"] == -3.0
    finally:
        f.unlink()


def test_run_optimise_flow():
    P, t = _P()
    A = types.SimpleNamespace(goal="keyboard", trials=12, no_taste=True, no_quality=True)
    buf = io.StringIO()
    best = fit.run_optimise(P, t, A, {"exact": (False, []), "quality": (False, "")}, out=lambda *a, **k: buf.write(
        " ".join(map(str, a)) + k.get("end", "\n")))
    txt = buf.getvalue()
    assert "approximate" in txt and "goal 'keyboard'" in txt and "Best:" in txt
    assert "_eqg" not in best


# ------------------------------------------------------------------------------------------- A/B taste
def test_learner_finds_hidden_taste():
    rng = np.random.default_rng(5)
    errs, prior = [], []
    for _ in range(12):
        true = rng.uniform(-0.8, 0.8, 4)
        L = taste.Learner(["bright", "warm", "polish", "clean"])
        for _ in range(12):
            a, b = L.next_pair(rng)
            p = 1 / (1 + np.exp(-taste.SENS * (np.sum((b - true) ** 2) - np.sum((a - true) ** 2))))
            L.update(a, b, "a" if rng.random() < p else "b")
        errs.append(np.abs(L.mu - true).mean())
        prior.append(np.abs(true).mean())
    print(f"\ntaste error after 12 picks {np.mean(errs):.2f} (guessing 0: {np.mean(prior):.2f})")
    assert np.mean(errs) < 0.6 * np.mean(prior)


def test_ab_session_end_to_end():
    P, t = _P(profile="stream", exciter=True)
    clip = taste.listening_clip(t)
    chain = offline.ApproxChain()
    rendered = []

    def render(Pv):
        rendered.append(Pv)
        return chain.process(clip, ee.render(Pv))
    # a listener who likes it brighter: picks whichever version has more 2-6 kHz energy
    from scipy import signal
    sos = signal.butter(2, [2000, 6000], "band", fs=SR, output="sos")
    played = []
    answers = []

    def player(x):
        played.append(np.sqrt(np.mean(signal.sosfilt(sos, x) ** 2) / np.mean(np.square(x))))

    def ask(prompt):
        a, b = played[-2], played[-1]
        answers.append("a" if a > b else "b")
        return answers[-1]
    tst, picks, L = taste.ab_session(P, render, ask, player=player, rounds=8, seed=3, out=lambda *a: None)
    print(f"\nA/B: {picks} picks -> {taste.describe(tst)}")
    assert picks == 8 and len(rendered) == 16
    assert tst["bright"] > 0.25
    taste.save_taste(tst, picks)
    taste.save_taste({"bright": 0.5}, 2)
    saved, n = taste.load_taste()
    assert n == 10 and abs(saved["bright"] - min(1.0, round(tst["bright"] + 0.5, 2))) < 0.02
    taste.taste_file().unlink()


def test_match_pair_levels():
    x = rd("speech_1.wav")[: 5 * SR]
    a, b = taste.match_pair(x * 0.1, x * 0.8)
    from v2ee.loudness import integrated
    assert abs(integrated(a) - integrated(b)) < 0.1 and max(np.abs(a).max(), np.abs(b).max()) <= 0.891


def test_tune_loop_a_key_moves_preset():
    import v2ee.loop as loop
    import v2ee.session as session
    P, t = _P()
    out = Path(tempfile.mkdtemp())
    session.CACHE_DIR = Path(tempfile.mkdtemp())
    ee.write_preset(P, "t_v1", out)
    sess = session.Session(out, "t", 1, "t_v1")
    pres0 = sum(b["g"] for b in P["eq"]["bands"] if 2000 <= b["f"] < 6000)
    calls = []

    def ab(Pc):
        calls.append(1)
        return {"bright": 0.6}, 5
    a0 = analyze(t["noise"], t["speech"], t["clicks"], t["taps"])
    real_apply = loop.apply
    loop.apply = lambda s, name, first=False: None
    answers = iter(["a", "q", "n"])
    builtins.input = lambda p="": next(answers)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            loop.tune_loop(P, sess, lambda: a0, ab=ab)
    finally:
        loop.apply = real_apply
    print("\n" + "\n".join(l for l in buf.getvalue().splitlines() if "taste" in l or "you:" in l))
    assert calls and "moved to your taste" in buf.getvalue()
    assert sum(b["g"] for b in P["eq"]["bands"] if 2000 <= b["f"] < 6000) > pres0 + 1.0


# ------------------------------------------------------------------------------------------- DNSMOS
def test_dnsmos_wrapper():
    seen = []

    class FakeSession:
        def get_inputs(self):
            return [types.SimpleNamespace(name="input_1")]

        def run(self, _, feed):
            x = feed["input_1"]
            seen.append(x.shape)
            return [np.array([[3.0, 3.5, 2.8]], np.float32)]
    q = quality.DNSMOS(session=FakeSession())
    r = q.score(np.zeros(12 * SR, np.float32))
    assert seen[0] == (1, quality.SEG) and len(seen) == 3                # 12 s @16 kHz: 3 one-second hops
    assert abs(r["ovrl"] - quality.P_OVR(2.8)) < 1e-6 and 1 < r["sig"] < 5
    r2 = quality.DNSMOS(session=FakeSession()).score(np.zeros(2 * SR, np.float32))   # short clip: repeated
    assert np.isfinite(r2["ovrl"])
    ok, why = quality.status()
    assert ok is False and ("model" in why or "onnxruntime" in why)


# ------------------------------------------------------------------------------------------- LV2 host
def _fake_lilv(latency=64):
    """A tiny stand-in for the lilv bindings: one 'compressor_stereo' plugin whose run() multiplies by the
    linear 'al' threshold port and delays by `latency` samples, with atom ports and an enum port."""
    m = types.ModuleType("lilv")

    class Node:
        def __init__(self, v):
            self.v = v

        def __str__(self):
            return str(self.v)

        def __float__(self):
            return float(self.v)

    class Port:
        def __init__(self, i, sym, kinds, rng=None, scale=None):
            self.i, self.sym, self.kinds, self.rng, self.scale = i, sym, kinds, rng, scale or {}

        def get_symbol(self):
            return Node(self.sym)

        def is_a(self, uri):
            return str(uri).split("#")[-1] in self.kinds

        def get_range(self):
            return tuple(Node(v) for v in (self.rng or (0, 0, 1)))

        def get_scale_points(self):
            return [types.SimpleNamespace(get_label=lambda l=l: Node(l), get_value=lambda v=v: Node(v))
                    for l, v in self.scale.items()]
    ports = [Port(0, "in_l", {"AudioPort", "InputPort"}), Port(1, "in_r", {"AudioPort", "InputPort"}),
             Port(2, "out_l", {"AudioPort", "OutputPort"}), Port(3, "out_r", {"AudioPort", "OutputPort"}),
             Port(4, "al", {"ControlPort", "InputPort"}, (1.0, 0.0, 1.0)),
             Port(5, "cr", {"ControlPort", "InputPort"}, (2.0, 1.0, 100.0)),
             Port(6, "cm", {"ControlPort", "InputPort"}, (0, 0, 2), {"Downward": 0, "Upward": 1, "Boosting": 2}),
             Port(7, "in", {"AtomPort", "InputPort"}), Port(8, "out", {"AtomPort", "OutputPort"}),
             Port(9, "latency", {"ControlPort", "OutputPort"}, (0, 0, 10000))]

    class Plugin:
        def get_num_ports(self):
            return len(ports)

        def get_port_by_index(self, i):
            return ports[i]

    class Instance:
        def __init__(self, plugin, rate, features):
            self.buf, self.hist = {}, None
            m.instances.append(self)

        def connect_port(self, i, arr):
            self.buf[i] = arr

        def activate(self):
            self.hist = np.zeros((latency, 2), np.float32)
            m.log.append(("activate", float(self.buf[4][0]), float(self.buf[6][0])))

        def deactivate(self):
            pass

        def run(self, n):
            hdr = self.buf[7].view(np.uint32)
            assert hdr[0] == 8                                  # host wrote an empty atom sequence
            x = np.stack([self.buf[0][:n], self.buf[1][:n]], axis=1) * self.buf[4][0]
            y = np.concatenate([self.hist, x])
            self.buf[2][:n], self.buf[3][:n] = y[:n, 0], y[:n, 1]
            self.hist = y[n:]
            self.buf[9][0] = latency

    class Plugins:
        def get_by_uri(self, node):
            return Plugin() if str(node).endswith("compressor_stereo") else None

    class World:
        def load_all(self):
            pass

        def get_all_plugins(self):
            return Plugins()

        def new_uri(self, u):
            return Node(u)
    m.World, m.Instance, m.instances, m.log = World, Instance, [], []
    return m


def test_lv2_host_mapping_with_fake_lilv():
    fake = _fake_lilv()
    sys.modules["lilv"] = fake
    try:
        host = offline.LV2Chain()
        blk = {"bypass": False, "input-gain": 0.0, "output-gain": 0.0, "threshold": -6.0, "ratio": 4.0,
               "mode": "Upward", "knee": -6.0}
        x = (np.sin(np.arange(SR) / 10) * 0.5).astype(np.float32)
        y = host.process(x, {"input": {"plugins_order": ["compressor#0", "rnnoise#0"], "compressor#0": blk,
                                       "rnnoise#0": {"bypass": False}}})
        assert fake.log[-1] == ("activate", np.float32(10 ** (-6 / 20)), 1.0)   # dB -> linear, enum by label
        assert np.allclose(y, x * 10 ** (-6 / 20), atol=1e-5)                   # latency compensated
        assert "kn" in host.problems["compressor"]                              # unmatched symbol reported
        # a plugin that isn't installed raises (the self-test reports it; the optimiser falls back)
        try:
            host.run_plugin("limiter", {}, np.zeros((10, 2), np.float32))
            raise AssertionError("expected an error")
        except RuntimeError as ex:
            assert "not installed" in str(ex)
    finally:
        sys.modules.pop("lilv", None)


def test_lv2_control_values_cover_eq_and_multiband():
    P, _ = _P(noise="both")
    inp = ee.render(P)["input"]
    eq = dict((s, v) for s, c, v in offline.control_values("equalizer", inp["equalizer#0"]))
    nb = inp["equalizer#0"]["num-bands"]
    assert eq["ftl_0"] == "Hi-pass" and eq[f"ftl_{nb}"] == "Off" and eq["ftr_31"] == "Off"
    assert eq["gl_1"] == inp["equalizer#0"]["left"]["band1"]["gain"]
    mb = dict((s, v) for s, c, v in offline.control_values("multiband_compressor", inp["multiband_compressor#0"]))
    assert mb["sf_1"] == 150.0 and mb["ce_0"] is True and mb["cbe_2"] is False
    assert offline.convert("db", -80.01, {}) == 0.0 and offline.convert("b", True, {}) == 1.0
    assert offline.convert("e", "x4", {"scale": {"x1": 0, "x2": 1, "x3": 2, "x4": 3}}) == 3.0
    assert offline.convert("f", 500, {"min": 0, "max": 100}) == 100.0


def test_self_test_without_lilv_is_graceful():
    ok, lines = offline.self_test()
    assert ok is False and "lilv" in lines[0]
    assert isinstance(offline.best_chain(status=(False, [])), offline.ApproxChain)


# ------------------------------------------------------------------------------------------- installer
def _install(*a, stdin=None):
    env = dict(os.environ, PREFIX=tempfile.mkdtemp())
    r = subprocess.run(["bash", str(HERE.parent / "install.sh"), *a], capture_output=True, text=True,
                       env=env, input=stdin, timeout=120)
    return r, Path(env["PREFIX"])


def test_installer_editions():
    r, _ = _install("--check")
    assert r.returncode == 0 and "Full version extras" in r.stdout and "Full version:" in r.stdout
    assert "Which version" not in r.stdout                              # --check never asks
    r, pre = _install("--no-deps", stdin="")
    assert "Not interactive: installing the Minimal version" in r.stdout and "Done (minimal version)" in r.stdout
    assert 'VOICE2EE_DATA=' in (pre / "bin/voice2ee").read_text()
    # switching back to minimal removes the Full extras
    (pre / "share/voice2ee/venv").mkdir(parents=True)
    (pre / "share/voice2ee/models").mkdir(parents=True)
    env = dict(os.environ, PREFIX=str(pre))
    r = subprocess.run(["bash", str(HERE.parent / "install.sh"), "--minimal", "--no-deps"], capture_output=True,
                       text=True, env=env, timeout=120)
    assert "removed the Full version's extras" in r.stdout and not (pre / "share/voice2ee/venv").exists()
    help_txt = subprocess.run(["bash", str(HERE.parent / "install.sh"), "--help"], capture_output=True, text=True).stdout
    assert "--minimal" in help_txt and "--full" in help_txt
