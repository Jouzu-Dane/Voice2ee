"""Run a preset on your recordings WITHOUT EasyEffects, so many variations can be tried in minutes.

Two chains:
  ApproxChain  built-in numpy model of the plugins (Minimal version). Close, not exact: results are always
               confirmed through the real EasyEffects afterwards.
  LV2Chain     the real LSP / Calf LV2 plugins EasyEffects itself uses, hosted through lilv (Full version).
               With a native EasyEffects these are the very same plugin files, so the result matches.
               RNNoise and the Deep noise remover aren't LV2 plugins; they're passed through (noted in the UI).

Check what's available:  voice2ee --check-full   (runs a self-test in a separate process, so a plugin that
crashes can't take the tool down)."""
import ctypes
import json
import subprocess
import sys

import numpy as np

from . import simchain
from .config import NEG_INF, SR


class ApproxChain:
    name, exact = "built-in model (approximate)", False
    passthrough = ("rnnoise", "deepfilternet", "stereo_tools", "level_meter")

    def process(self, x, preset):
        return simchain.process(x, preset)


def process_take(chain, take, preset, short=None):
    """Processed copy of a take (dict with noise/speech/clicks/taps) through `chain`."""
    def cut(x, secs):
        return x if (x is None or not short) else x[: int(secs * SR)]
    out = {}
    for k, secs in (("noise", 3), ("speech", short or 0), ("clicks", 4), ("taps", 4)):
        x = take.get(k)
        out[k] = None if x is None else chain.process(cut(x, secs), preset)
    out["raw_speech"] = cut(take["speech"], short or 0)
    return out


# ------------------------------------------------------------------------------------------- LV2 (Full)
LSP = "http://lsp-plug.in/plugins/lv2/"
CALF = "http://calf.sourceforge.net/plugins/"
LV2 = "http://lv2plug.in/ns/lv2core#"
ATOM = "http://lv2plug.in/ns/ext/atom#"

# EasyEffects preset key -> LV2 port symbol, conversion. Kinds: f number, db (dB -> linear gain), b switch,
# e choice (matched by label against the port's scale points). Taken from how EasyEffects binds its settings.
_SC = [("sidechain.type", "sct", "e"), ("sidechain.mode", "scm", "e"), ("sidechain.source", "scs", "e"),
       ("sidechain.preamp", "scp", "db"), ("sidechain.reactivity", "scr", "f"), ("sidechain.lookahead", "sla", "f"),
       ("sidechain.stereo-split-source", "sscs", "e"), ("hpf-mode", "shpm", "e"), ("hpf-frequency", "shpf", "f"),
       ("lpf-mode", "slpm", "e"), ("lpf-frequency", "slpf", "f"), ("stereo-split", "ssplit", "b"),
       ("attack", "at", "f"), ("release", "rt", "f"), ("makeup", "mk", "db"), ("dry", "cdr", "db"),
       ("wet", "cwt", "db")]
MAP = {
    "compressor": (LSP + "compressor_stereo", _SC + [
        ("mode", "cm", "e"), ("threshold", "al", "db"), ("release-threshold", "rrl", "db"), ("ratio", "cr", "f"),
        ("knee", "kn", "db"), ("boost-threshold", "bth", "db"), ("boost-amount", "bsa", "db")]),
    "expander": (LSP + "expander_stereo", _SC + [
        ("mode", "em", "e"), ("threshold", "al", "db"), ("release-threshold", "rrl", "db"), ("ratio", "er", "f"),
        ("knee", "kn", "db")]),
    "gate": (LSP + "gate_stereo", _SC + [
        ("curve-threshold", "gt", "db"), ("curve-zone", "gz", "db"), ("hysteresis", "gh", "b"),
        ("hysteresis-threshold", "ht", "db"), ("hysteresis-zone", "hz", "db"), ("reduction", "gr", "db")]),
    "limiter": (LSP + "limiter_stereo", [
        ("mode", "mode", "e"), ("oversampling", "ovs", "e"), ("dithering", "dith", "e"), ("threshold", "th", "db"),
        ("lookahead", "lk", "f"), ("attack", "at", "f"), ("release", "rt", "f"), ("gain-boost", "boost", "b"),
        ("alr", "alr", "b"), ("alr-attack", "aat", "f"), ("alr-release", "art", "f"), ("alr-knee", "akn", "db"),
        ("sidechain-preamp", "scp", "db"), ("stereo-link", "slink", "f")]),
    "deesser": (CALF + "Deesser", [
        ("detection", "detection", "e"), ("mode", "mode", "e"), ("threshold", "threshold", "db"),
        ("ratio", "ratio", "f"), ("laxity", "laxity", "f"), ("makeup", "makeup", "db"),
        ("f1-freq", "f1_freq", "f"), ("f2-freq", "f2_freq", "f"), ("f1-level", "f1_level", "db"),
        ("f2-level", "f2_level", "db"), ("f2-q", "f2_q", "f"), ("sc-listen", "sc_listen", "b")]),
    "exciter": (CALF + "Exciter", [
        ("amount", "amount", "db"), ("harmonics", "drive", "f"), ("scope", "freq", "f"), ("ceil", "ceil", "f"),
        ("ceil-active", "ceil_active", "b"), ("blend", "blend", "f"), ("listen", "listen", "b")]),
    "equalizer": (LSP + "para_equalizer_x32_lr", [("mode", "mode", "e"), ("balance", "bal", "f")]),
    "multiband_compressor": (LSP + "mb_compressor_stereo", [
        ("compressor-mode", "mode", "e"), ("envelope-boost", "envb", "e"), ("dry", "g_dry", "db"),
        ("wet", "g_wet", "db"), ("stereo-split", "ssplit", "b")]),
}
_EQ_BAND = [("type", "ft{s}_{i}", "e"), ("mode", "fm{s}_{i}", "e"), ("slope", "s{s}_{i}", "e"),
            ("solo", "xs{s}_{i}", "b"), ("mute", "xm{s}_{i}", "b"), ("frequency", "f{s}_{i}", "f"),
            ("gain", "g{s}_{i}", "db"), ("q", "q{s}_{i}", "f"), ("width", "w{s}_{i}", "f")]
_MB_BAND = [("compressor-enable", "ce_{i}", "b"), ("mute", "bm_{i}", "b"), ("solo", "bs_{i}", "b"),
            ("compression-mode", "cm_{i}", "e"), ("attack-threshold", "al_{i}", "db"), ("attack-time", "at_{i}", "f"),
            ("release-threshold", "rrl_{i}", "db"), ("release-time", "rt_{i}", "f"), ("ratio", "cr_{i}", "f"),
            ("knee", "kn_{i}", "db"), ("makeup", "mk_{i}", "db"), ("boost-amount", "bsa_{i}", "db"),
            ("boost-threshold", "bth_{i}", "db"), ("sidechain-mode", "scm_{i}", "e"),
            ("sidechain-source", "scs_{i}", "e"), ("sidechain-preamp", "scp_{i}", "db"),
            ("sidechain-reactivity", "scr_{i}", "f"), ("sidechain-lookahead", "sla_{i}", "f"),
            ("enable-band", "cbe_{i}", "b"), ("split-frequency", "sf_{i}", "f")]


def _get(blk, dotted):
    for part in dotted.split("."):
        if not isinstance(blk, dict) or part not in blk:
            return None
        blk = blk[part]
    return blk


def control_values(kind, blk):
    """[(symbol, conversion, value)] for one plugin block of a preset."""
    out = [(sym, conv, _get(blk, key)) for key, sym, conv in MAP[kind][1]]
    if kind == "equalizer":
        for side, s in (("left", "l"), ("right", "r")):
            bands = blk.get(side, {})
            for i in range(32):
                b = bands.get(f"band{i}")
                if b is None:
                    out.append((f"ft{s}_{i}", "e", "Off"))
                    continue
                out += [(sym.format(s=s, i=i), conv, b.get(key)) for key, sym, conv in _EQ_BAND]
    if kind == "multiband_compressor":
        for i in range(8):
            b = blk.get(f"band{i}", {})
            out += [(sym.format(i=i), conv, b.get(key)) for key, sym, conv in _MB_BAND
                    if not (i == 0 and key in ("enable-band", "split-frequency"))]
    return [(sym, conv, v) for sym, conv, v in out if v is not None]


def _norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def convert(conv, value, port):
    """Preset value -> port value. port: dict with 'scale' {label: value}, 'min', 'max'."""
    if conv == "db":
        v = 0.0 if float(value) <= NEG_INF + 0.02 else 10 ** (float(value) / 20)
    elif conv == "b":
        v = 1.0 if value else 0.0
    elif conv == "e":
        want, scale = _norm(value), port.get("scale") or {}
        match = [v for lab, v in scale.items() if _norm(lab) == want]
        match = match or [v for lab, v in scale.items() if want and (_norm(lab).startswith(want[:4]) or
                                                                      want.startswith(_norm(lab)[:4]))]
        if not match:
            raise KeyError(f"no choice '{value}' (has: {', '.join(scale)})")
        v = float(match[0])
    else:
        v = float(value)
    lo, hi = port.get("min"), port.get("max")
    if lo is not None and hi is not None and hi > lo:
        v = min(max(v, lo), hi)
    return float(v)


class LV2Chain:
    """Hosts the real plugins in-process with lilv (python bindings). Provides the URID map and options
    features LSP plugins need, buffers for their atom (UI message) ports, and compensates reported latency."""
    name, exact = "your real plugins (exact)", True
    passthrough = ("rnnoise", "deepfilternet", "stereo_tools", "level_meter")
    BLOCK = 512

    def __init__(self):
        import lilv                       # Full version only
        self.lilv = lilv
        self.world = lilv.World()
        self.world.load_all()
        self.all = self.world.get_all_plugins()
        self.urids, self._keep = {}, []
        self.features = self._features()
        self.cache, self.problems = {}, {}

    # --- LV2 features: urid:map + options (block length, sample rate)
    def urid(self, uri):
        if isinstance(uri, bytes):
            uri = uri.decode()
        return self.urids.setdefault(uri, len(self.urids) + 1)

    def _features(self):
        class Feature(ctypes.Structure):
            _fields_ = [("URI", ctypes.c_char_p), ("data", ctypes.c_void_p)]
        MAPFN = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_char_p)

        class URIDMap(ctypes.Structure):
            _fields_ = [("handle", ctypes.c_void_p), ("map", MAPFN)]

        class Option(ctypes.Structure):
            _fields_ = [("context", ctypes.c_uint32), ("subject", ctypes.c_uint32), ("key", ctypes.c_uint32),
                        ("size", ctypes.c_uint32), ("type", ctypes.c_uint32), ("value", ctypes.c_void_p)]
        mapfn = MAPFN(lambda handle, uri: self.urid(uri))
        umap = URIDMap(None, mapfn)
        BUF, ATOMNS = "http://lv2plug.in/ns/ext/buf-size#", ATOM
        block, rate = ctypes.c_int32(self.BLOCK), ctypes.c_float(SR)
        opts = (Option * 4)(
            Option(0, 0, self.urid(BUF + "maxBlockLength"), 4, self.urid(ATOMNS + "Int"),
                   ctypes.cast(ctypes.pointer(block), ctypes.c_void_p)),
            Option(0, 0, self.urid(BUF + "nominalBlockLength"), 4, self.urid(ATOMNS + "Int"),
                   ctypes.cast(ctypes.pointer(block), ctypes.c_void_p)),
            Option(0, 0, self.urid("http://lv2plug.in/ns/ext/parameters#sampleRate"), 4,
                   self.urid(ATOMNS + "Float"), ctypes.cast(ctypes.pointer(rate), ctypes.c_void_p)),
            Option(0, 0, 0, 0, 0, None))
        feats = [Feature(b"http://lv2plug.in/ns/ext/urid#map", ctypes.cast(ctypes.pointer(umap), ctypes.c_void_p)),
                 Feature(b"http://lv2plug.in/ns/ext/options#options", ctypes.cast(opts, ctypes.c_void_p)),
                 Feature(BUF.encode() + b"boundedBlockLength", None)]
        arr = (ctypes.POINTER(Feature) * (len(feats) + 1))(*[ctypes.pointer(f) for f in feats], None)
        self._keep += [Feature, mapfn, umap, block, rate, opts, feats, arr]
        lf = getattr(self.lilv, "LV2_Feature", None)
        return ctypes.cast(arr, ctypes.POINTER(ctypes.POINTER(lf))) if lf is not None else arr

    # --- plugin discovery
    def find(self, uri):
        node = self.world.new_uri(uri)
        for get in (lambda: self.all.get_by_uri(node), lambda: self.all[node]):
            try:
                p = get()
                if p is not None:
                    return p
            except Exception:
                pass
        return None

    def ports(self, plugin):
        w, out = self.world, []
        is_ = {k: w.new_uri(v) for k, v in (("audio", LV2 + "AudioPort"), ("control", LV2 + "ControlPort"),
                                             ("atom", ATOM + "AtomPort"), ("cv", LV2 + "CVPort"),
                                             ("input", LV2 + "InputPort"), ("output", LV2 + "OutputPort"))}
        for i in range(plugin.get_num_ports()):
            p = plugin.get_port_by_index(i)
            kind = next((k for k in ("audio", "control", "atom", "cv") if p.is_a(is_[k])), "other")
            d = {"index": i, "symbol": str(p.get_symbol()), "kind": kind,
                 "dir": "in" if p.is_a(is_["input"]) else "out", "scale": {}}
            if kind == "control":
                rng = p.get_range()
                d["default"], d["min"], d["max"] = (_num(n) for n in rng)
                sp = p.get_scale_points()
                for s in (sp or []):
                    d["scale"][str(s.get_label())] = _num(s.get_value())
            out.append(d)
        return out

    def _instance(self, kind):
        if kind in self.cache:
            return self.cache[kind]
        uri = MAP[kind][0]
        plug = self.find(uri)
        if plug is None:
            raise RuntimeError(f"plugin not installed: {uri}")
        inst = self.lilv.Instance(plug, SR, self.features)
        ports = self.ports(plug)
        bufs, ctl = {}, {}
        for p in ports:
            if p["kind"] in ("audio", "cv"):
                bufs[p["index"]] = np.zeros(self.BLOCK, np.float32)
            elif p["kind"] == "control":
                bufs[p["index"]] = np.array([p["default"] if p["default"] is not None else 0.0], np.float32)
                ctl[p["symbol"]] = p
            elif p["kind"] == "atom":
                bufs[p["index"]] = np.zeros(65536, np.uint8)
            if p["index"] in bufs:
                inst.connect_port(p["index"], bufs[p["index"]])
        st = {"inst": inst, "ports": ports, "bufs": bufs, "ctl": ctl,
              "ain": [p["index"] for p in ports if p["kind"] == "audio" and p["dir"] == "in"],
              "aout": [p["index"] for p in ports if p["kind"] == "audio" and p["dir"] == "out"],
              "atoms": [p for p in ports if p["kind"] == "atom"],
              "lat": next((p["index"] for p in ports if p["kind"] == "control" and p["dir"] == "out"
                           and "latency" in p["symbol"].lower()), None)}
        self.cache[kind] = st
        return st

    def _reset_atoms(self, st):
        seq, chunk = self.urid(ATOM + "Sequence"), self.urid(ATOM + "Chunk")
        for p in st["atoms"]:
            b = st["bufs"][p["index"]].view(np.uint32)
            if p["dir"] == "in":            # empty sequence: header {size=8 (unit+pad), type=Sequence}
                b[0], b[1], b[2], b[3] = 8, seq, 0, 0
            else:                           # space the plugin may write into
                b[0], b[1] = len(st["bufs"][p["index"]]) - 8, chunk

    def set_controls(self, kind, blk):
        st = self._instance(kind)
        for p in st["ports"]:                         # start from the plugin's defaults every time
            if p["kind"] == "control" and p["dir"] == "in" and p["default"] is not None:
                st["bufs"][p["index"]][0] = p["default"]
        missing = []
        for sym, conv, val in control_values(kind, blk):
            port = st["ctl"].get(sym)
            if port is None or port["dir"] != "in":
                missing.append(sym)
                continue
            try:
                st["bufs"][port["index"]][0] = convert(conv, val, port)
            except (KeyError, ValueError) as ex:
                missing.append(f"{sym} ({ex})")
        if missing:
            self.problems[kind] = missing
        return st

    def run_plugin(self, kind, blk, x):
        """x: (n, 2) float32. Returns processed (n, 2), latency-compensated."""
        st = self.set_controls(kind, blk)
        inst, bufs, B = st["inst"], st["bufs"], self.BLOCK
        ain, aout = st["ain"], st["aout"]
        pre = int(0.25 * SR) // B * B                 # settle smoothed parameters on silence first
        n = len(x)
        total = pre + n + B * 64                       # room for latency
        y = np.zeros((total, 2), np.float32)
        xin = np.zeros((total, 2), np.float32)
        xin[pre:pre + n] = x
        inst.activate()
        lat = 0
        try:
            for s in range(0, total - B + 1, B):
                self._reset_atoms(st)
                for k, idx in enumerate(ain):          # main L/R first; extra inputs (sidechain) get the same
                    bufs[idx][:] = xin[s:s + B, k % 2]
                inst.run(B)
                for k, idx in enumerate(aout[:2]):
                    y[s:s + B, k] = bufs[idx]
                if len(aout) == 1:
                    y[s:s + B, 1] = bufs[aout[0]]
                if st["lat"] is not None:
                    lat = int(round(float(bufs[st["lat"]][0])))
        finally:
            inst.deactivate()
        return y[pre + lat: pre + lat + n]

    def process(self, x, preset):
        x = np.asarray(x, np.float32)
        mono_in = x.ndim == 1
        y = np.stack([x, x], axis=1) if mono_in else x[:, :2].copy()
        inp = preset["input"]
        for key in inp.get("plugins_order", []):
            blk, kind = inp.get(key, {}), key.split("#")[0]
            if not isinstance(blk, dict) or blk.get("bypass", False):
                continue
            y = y * 10 ** (blk.get("input-gain", 0) / 20)
            if kind in MAP:
                y = self.run_plugin(kind, blk, y)
            elif kind not in self.passthrough:
                y = simchain.process(y.mean(axis=1), {"input": {"plugins_order": [key], key: blk}})[:, None] \
                    .repeat(2, axis=1)
            y = y * 10 ** (blk.get("output-gain", 0) / 20)
        return y.mean(axis=1) if mono_in else y


def _num(node):
    if node is None:
        return None
    for f in (float, lambda n: float(str(n))):
        try:
            return f(node)
        except (TypeError, ValueError):
            pass
    return None


# ------------------------------------------------------------------------------------------- availability
def lilv_importable():
    try:
        import lilv  # noqa: F401
        return True
    except Exception:
        return False


def self_test(timeout=90):
    """Run the LV2 chain on a test preset in a SEPARATE process (a crashing plugin can't take us down).
    Returns (ok, report_lines)."""
    if not lilv_importable():
        return False, ["lilv python bindings not installed - package python-lilv (Arch, CachyOS, Manjaro) or "
                       "python3-lilv (Debian, Ubuntu, Fedora, openSUSE)"]
    try:
        r = subprocess.run([sys.executable, "-m", "v2ee.offline", "--selftest"], capture_output=True, text=True,
                           timeout=timeout, cwd=str(_pkg_root()))
    except subprocess.TimeoutExpired:
        return False, ["the LV2 self-test hung (timeout)"]
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-3:]
        return False, lines + [f"self-test failed (exit {r.returncode})"] + tail
    return any(l.startswith("OK") for l in lines), lines


def _pkg_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def best_chain(prefer_exact=True, status=None):
    """The best chain available. status: result of self_test() if already run."""
    if prefer_exact:
        ok, _ = status if status is not None else self_test()
        if ok:
            try:
                return LV2Chain()
            except Exception:
                pass
    return ApproxChain()


def _selftest_main():
    """Child process: map a full default preset onto the plugins and push a second of noise through it."""
    from .planner import finalize_mods
    host = LV2Chain()
    missing = [MAP[k][0] for k in MAP if host.find(MAP[k][0]) is None]
    for uri in missing:
        print(f"MISSING {uri}")
    preset = json.loads(json.dumps(_test_preset(finalize_mods)))
    x = (np.random.default_rng(0).standard_normal(SR) * 0.05).astype(np.float32)
    y = host.process(x, {"input": {**preset["input"], "plugins_order": [
        k for k in preset["input"]["plugins_order"] if MAP.get(k.split("#")[0], ("",))[0] not in missing]}})
    for kind, probs in host.problems.items():
        print(f"PORTS {kind}: not matched: {', '.join(probs[:12])}" + (" ..." if len(probs) > 12 else ""))
    finite = bool(np.isfinite(y).all())
    print(("OK" if finite and not missing else "PARTIAL" if finite else "BROKEN") +
          f" ran {len(preset['input']['plugins_order'])} plugins, output rms "
          f"{20 * np.log10(np.sqrt(np.mean(np.square(y))) + 1e-12):.1f} dB")


def _test_preset(finalize_mods):
    """A complete preset from a synthetic analysis (no recording needed)."""
    from .easyeffects import render
    from .planner import initial_params
    import types
    rng = np.random.default_rng(1)
    t = np.arange(4 * SR) / SR
    speech = (0.05 * np.sin(2 * np.pi * 140 * t) * (np.sin(2 * np.pi * 4 * t) > 0) +
              0.005 * rng.standard_normal(len(t))).astype(np.float32)
    noise = (0.0005 * rng.standard_normal(2 * SR)).astype(np.float32)
    from .analysis import analyze
    a = analyze(noise, speech)
    args = types.SimpleNamespace(box_cut=0, deess=1.0, makeup=0, gate_offset=0, no_vad=False, vad_thres=70,
                                 bands=10, max_gain=12.0, target_lufs=None,
                                 mods=finalize_mods({"noise": "both", "exciter": True}))
    P, _ = initial_params(a, args)
    return render(P)


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest_main()
