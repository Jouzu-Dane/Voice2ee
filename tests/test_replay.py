"""Record-once mode end to end, against fakes: a fake EasyEffects local server (socket) and fake pactl /
pw-loopback / pw-play / parec that route audio through the simulated chain in real time. Also checks that
presets load over the socket and that read-back catches a value EasyEffects didn't apply."""
import builtins
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import types
from pathlib import Path

from common import HERE, args, rd
from v2ee import easyeffects as ee
from v2ee import session
from v2ee.analysis import analyze
from v2ee.audio import ReplayError, replay_take
from v2ee.planner import initial_params

TOOLS = {
    "pactl": r'''
import sys, os
S = os.environ["V2EE_FAKE_STATE"]
a = sys.argv[1:]
if a[:3] == ["list", "short", "sources"]:
    print("1\talsa_input.podmic\tPipeWire\ts24le 1ch 48000Hz\tRUNNING")
    print("2\teasyeffects_source\tPipeWire\tfloat32le 2ch 48000Hz\tRUNNING")
    if os.path.exists(f"{S}/loopback_up"):
        print("3\tvoice2ee_replay_mic\tPipeWire\tfloat32le 2ch 48000Hz\tRUNNING")
elif a[:1] == ["get-default-source"]:
    print(open(f"{S}/default_source").read().strip())
elif a[:1] == ["set-default-source"]:
    open(f"{S}/default_source", "w").write(a[1])
''',
    "pw-loopback": r'''
import os, signal, sys, time
S = os.environ["V2EE_FAKE_STATE"]
open(f"{S}/loopback_up", "w").write("1")
def bye(*_):
    try: os.remove(f"{S}/loopback_up")
    except FileNotFoundError: pass
    sys.exit(0)
signal.signal(signal.SIGTERM, bye)
while True: time.sleep(0.1)
''',
    "pw-play": r'''
import os, sys, time, json
import numpy as np
from scipy.io import wavfile
S = os.environ["V2EE_FAKE_STATE"]
sys.path.insert(0, os.environ["V2EE_TESTS"])
import sim
x = wavfile.read(sys.argv[-1])[1].astype(np.float32)
follows = open(f"{S}/default_source").read().strip() == "voice2ee_replay_mic" and os.environ.get("V2EE_FAKE_DEAF") != "1"
pre = json.load(open(open(f"{S}/loaded").read().strip()))
y = sim.process(x, pre) if follows else np.zeros_like(x)
np.save(f"{S}/stream_tmp.npy", y.astype(np.float32)); os.replace(f"{S}/stream_tmp.npy", f"{S}/stream.npy")
open(f"{S}/stream_start", "w").write(str(time.time() + 0.08))   # 80 ms EasyEffects latency
time.sleep(len(x) / 48000)
''',
    "parec": r'''
import os, sys, time
import numpy as np
S = os.environ["V2EE_FAKE_STATE"]
dev = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--device=")), "")
ch = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--channels=")), "1"))
t0, n, blk, cache = time.time(), 0, 2400, {}
out = sys.stdout.buffer
while True:
    buf = np.zeros((blk, ch), np.float32)
    if dev == "easyeffects_source" and os.path.exists(f"{S}/stream_start"):
        m = os.path.getmtime(f"{S}/stream.npy")
        if cache.get("m") != m:
            cache = {"m": m, "y": np.load(f"{S}/stream.npy"), "s": float(open(f"{S}/stream_start").read())}
        y, s = cache["y"], cache["s"]
        for i in range(blk):
            k = int((t0 + (n + i) / 48000 - s) * 48000)
            if 0 <= k < len(y):
                buf[i] = y[k][:ch] if y.ndim == 2 else y[k]
    try:
        out.write(buf.tobytes()); out.flush()
    except BrokenPipeError:
        break
    n += blk
    time.sleep(max(0, t0 + n / 48000 - time.time()))
''',
}


class FakeEasyEffects:
    """Minimal stand-in for EasyEffects' local server."""

    def __init__(self, rt, presets, state, faults=None):
        self.path, self.presets, self.state = Path(rt) / "EasyEffectsServer", Path(presets), Path(state)
        self.loaded, self.faults, self.log = None, faults or {}, []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.path))
        self.sock.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with c:
                line = c.recv(4096).decode().strip()
                self.log.append(line)
                c.sendall(self._handle(line).encode())

    def _handle(self, line):
        p = line.split(":")
        if p[0] == "load_preset":
            self.loaded = p[2]
            (self.state / "loaded").write_text(str(self.presets / f"{p[2]}.json"))
            return ""
        if p[0] == "get_last_loaded_preset":
            return self.loaded or ""
        if p[0] == "get_property":
            base, idx, prop = p[2], p[3], p[4]
            kebab = "".join("-" + ch.lower() if ch.isupper() else ch for ch in prop)
            key = f"{base}#{idx}"
            if (key, kebab) in self.faults:
                v = self.faults[(key, kebab)]
            else:
                blk = json.loads((self.presets / f"{self.loaded}.json").read_text())["input"].get(key, {})
                if kebab not in blk:
                    return ""
                v = blk[kebab]
            return ("true" if v else "false") if isinstance(v, bool) else str(v)
        return ""

    def close(self):
        self.sock.close()


def _env(faults=None):
    tmp = Path(tempfile.mkdtemp())
    for d in ("bin", "rt", "state", "presets"):
        (tmp / d).mkdir()
    for name, code in TOOLS.items():
        f = tmp / "bin" / name
        f.write_text(f"#!{sys.executable}\n{code}")
        f.chmod(0o755)
    (tmp / "state" / "default_source").write_text("alsa_input.podmic")
    os.environ.update(PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", XDG_RUNTIME_DIR=str(tmp / "rt"),
                      V2EE_FAKE_STATE=str(tmp / "state"), V2EE_TESTS=str(HERE))
    os.environ.pop("V2EE_FAKE_DEAF", None)
    return tmp, FakeEasyEffects(tmp / "rt", tmp / "presets", tmp / "state", faults)


def _short_take():
    return {"noise": rd("noise.wav")[: 3 * 48000], "speech": rd("speech_1.wav")[: 10 * 48000],
            "clicks": rd("clicks.wav")[: 4 * 48000]}


def test_replay_rounds_end_to_end():
    import v2ee.loop as loop
    from v2ee.cli import make_measure
    tmp, fake = _env()
    try:
        session.CACHE_DIR = tmp / "cache"
        A = args()
        a0 = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
        P, _ = initial_params(a0, A)
        ee.write_preset(P, "t_v1", tmp / "presets")
        sess = session.Session(tmp / "presets", "t", 1, "t_v1")
        measure = make_measure("replay", _short_take(), "easyeffects_source", "alsa_input.podmic", A, sess)
        answers = iter(["", "n", "q", "n"])
        builtins.input = lambda p="": next(answers)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loop.tune_loop(P, sess, measure, raw="alsa_input.podmic", test_mode="replay")
        txt = buf.getvalue()
        print("\n" + "\n".join(l for l in txt.splitlines() if l.startswith(("loudness", "EasyEffects", "  auto: loud",
                                                                               "(EasyEffects confirmed", "Saved"))))
        assert "Replay didn't work" not in txt
        assert txt.count("loudness ") >= 2 and "EasyEffects adds" in txt
        assert "(EasyEffects confirmed" in txt
        assert (tmp / "state" / "default_source").read_text().strip() == "alsa_input.podmic"   # real mic restored
        assert not (tmp / "state" / "loopback_up").exists()                                  # virtual mic removed
        assert any(l.startswith("load_preset:input:t_v") for l in fake.log)                  # loaded via socket
        assert (tmp / "presets" / "t_v2.json").exists()
    finally:
        fake.close()


def test_replay_detects_easyeffects_not_listening():
    tmp, fake = _env()
    try:
        session.CACHE_DIR = tmp / "cache"
        A = args()
        a0 = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
        P, _ = initial_params(a0, A)
        ee.write_preset(P, "t_v1", tmp / "presets")
        ee.load_preset("t_v1")
        os.environ["V2EE_FAKE_DEAF"] = "1"            # EasyEffects ignores the default-input switch
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                replay_take(_short_take(), "easyeffects_source")
            raise AssertionError("expected ReplayError")
        except ReplayError as ex:
            assert "voice2ee-replay-mic" in str(ex)
        assert (tmp / "state" / "default_source").read_text().strip() == "alsa_input.podmic"
    finally:
        os.environ.pop("V2EE_FAKE_DEAF", None)
        fake.close()


def test_readback_catches_unapplied_setting():
    tmp, fake = _env(faults={("deesser#1", "bypass"): True, ("limiter#0", "gain-boost"): True})
    try:
        A = args()
        a0 = analyze(rd("noise.wav"), rd("speech_0.wav"), rd("clicks.wav"))
        P, _ = initial_params(a0, A)
        path = ee.write_preset(P, "t_v1", tmp / "presets")
        assert ee.load_preset("t_v1") == "server"
        res = ee.verify_loaded(json.loads(path.read_text()))
        print("\nread-back:", res)
        assert res["checked"] >= 15
        assert any("deesser#1 bypass" in m for m in res["mismatch"])
        assert any("limiter#0 gain-boost" in m for m in res["mismatch"])
        assert len(res["mismatch"]) == 2
    finally:
        fake.close()
