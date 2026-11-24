"""Recording, gain measurement, and 'record once' replay through EasyEffects."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .config import CACHE_DIR, PASSAGE, SR
from .term import hidden_keys

# ----------------------------------------------------------------------------- sources and recording


def _pactl(*a, timeout=4):
    """pactl with English output (its text output is translated in other locales)."""
    try:
        return subprocess.run(["pactl", *a], capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"}).stdout
    except Exception:
        return ""


def _source_index_names():
    return {ln.split("\t")[0]: ln.split("\t")[1] for ln in _pactl("list", "short", "sources").splitlines()
            if ln.count("\t") >= 1}


def list_sources():
    return [n for n in _source_index_names().values() if not n.endswith(".monitor")]


def stream_routes(prefix):
    """Which source each of our recording streams is REALLY connected to: {stream name: source name}."""
    names, routes = _source_index_names(), {}
    js = _pactl("-f", "json", "list", "source-outputs")
    try:
        for so in json.loads(js):
            mn = (so.get("properties") or {}).get("media.name", "")
            if mn.startswith(prefix):
                routes[mn] = names.get(str(so.get("source")), str(so.get("source")))
        return routes
    except (ValueError, TypeError, AttributeError):
        pass
    src = None
    for line in _pactl("list", "source-outputs").splitlines():
        line = line.strip()
        if line.startswith("Source Output #"):
            src = None
        elif line.startswith("Source:"):
            src = line.split(":", 1)[1].strip()
        elif line.startswith("media.name = "):
            mn = line.split("=", 1)[1].strip().strip('"')
            if mn.startswith(prefix):
                routes[mn] = names.get(src, src)
    return routes


LAST_ROUTES = {}  # device asked for -> source PipeWire actually connected the recording to (last record_multi)


def route_problem(dev):
    """None if the last recording of `dev` really came from `dev` (or couldn't be checked), else the actual source."""
    actual = LAST_ROUTES.get(dev)
    return actual if (dev and actual and actual != dev) else None


def warn_route(dev, actual, what="recording"):
    print(f"!! The {what} was supposed to come from '{dev}', but PipeWire connected it to '{actual}'.\n"
          "   Something (often WirePlumber remembering where an app recorded from) moved it. Check the Recording "
          "tab in pavucontrol, or clear ~/.local/state/wireplumber/ and try again.")


def _meter(x):
    db = 20 * np.log10(max(float(np.abs(x).max()), 1e-6))
    n = int(np.clip((db + 60) / 60 * 40, 0, 40))
    print("\r  [" + "#" * n + " " * (40 - n) + f"] {db:6.1f} dBFS ", end="", flush=True)


def record_multi(seconds, devices, channels=2):
    """Record several sources at the same time. Returns one (n, channels) float32 array per device."""
    need, blk = int(seconds * SR) * 4 * channels, int(0.05 * SR) * 4 * channels
    procs, bufs = [], [bytearray() for _ in devices]
    tag = f"voice2ee-{os.getpid()}-{time.monotonic_ns()}"
    LAST_ROUTES.clear()
    for i, dev in enumerate(devices):
        cmd = ["parec", "--format=float32le", f"--rate={SR}", f"--channels={channels}", "--latency-msec=50",
               "--client-name=voice2ee", f"--stream-name={tag}-{i}", "--property=node.dont-move=true"]
        if dev:
            cmd.append(f"--device={dev}")
        try:
            procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
        except FileNotFoundError:
            for p in procs:
                p.terminate()
            sys.exit("parec not found. Install it with: sudo pacman -S libpulse")

    def reader(i):
        while len(bufs[i]) < need:
            d = procs[i].stdout.read(blk)
            if not d:
                break
            bufs[i] += d

    def check_routes():
        time.sleep(min(0.6, seconds / 3))
        r = stream_routes(tag)
        for i, dev in enumerate(devices):
            if f"{tag}-{i}" in r:
                LAST_ROUTES[dev] = r[f"{tag}-{i}"]

    threads = [threading.Thread(target=reader, args=(i,), daemon=True) for i in range(len(devices))]
    for t in threads:
        t.start()
    checker = threading.Thread(target=check_routes, daemon=True)
    checker.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.05)
            n = len(bufs[0]) // 4 * 4
            if n >= blk:
                _meter(np.frombuffer(bytes(bufs[0][n - blk:n]), dtype="<f4"))
    finally:
        for p in procs:
            p.terminate()
        checker.join(timeout=2)
    print()
    outs = []
    for dev, b in zip(devices, bufs):
        if not b:
            sys.exit(f"No audio received from '{dev}'. Check the source name with --list-devices.")
        n = min(len(b), need) // (4 * channels) * (4 * channels)
        outs.append(np.frombuffer(bytes(b[:n]), dtype="<f4").reshape(-1, channels).copy())
    return outs


def record(seconds, device):
    return record_multi(seconds, [device])[0]


def mono(x):
    """Best single channel: the louder one (a mono mic can show up on the left channel only)."""
    x = np.asarray(x)
    if x.ndim == 1:
        return x
    rms = np.sqrt((x.astype(np.float64) ** 2).mean(axis=0))
    return x[:, int(np.argmax(rms))]


def channel_imbalance(x):
    """dB difference between the louder and quieter channel (0 for mono / identical channels)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1 or x.shape[1] < 2:
        return 0.0
    r = 10 * np.log10((x ** 2).mean(axis=0) + 1e-20)
    return float(r.max() - r.min())


def _env_db(x, ms=10):
    n = int(ms * SR / 1000)
    x = mono(x)
    m = len(x) // n
    return 20 * np.log10(np.sqrt((x[: m * n].reshape(m, n).astype(np.float64) ** 2).mean(axis=1)) + 1e-9)


def find_lag(ref, rec, max_s=1.5):
    """Lag (samples) of `rec` behind `ref`, from 10 ms envelopes. Returns (lag, correlation)."""
    r, e = _env_db(ref), _env_db(rec)
    m = min(len(r), len(e))
    if m < 50:
        return 0, -1.0
    best, lag = -2.0, 0
    for L in range(0, min(int(max_s * 100), m - 20) + 1):
        x, y = r[: m - L], e[L:m]
        if x.std() > 0 and y.std() > 0:
            c = float(np.corrcoef(x, y)[0, 1])
            if c > best:
                best, lag = c, L
    return lag * int(SR / 100), best


def measure_gain(raw, proc):
    """How much louder the processed signal is than the raw mic, on speech, after aligning for latency."""
    r, e = _env_db(raw), _env_db(proc)
    m = min(len(r), len(e))
    if m < 200:
        return {}
    r, e = r[:m], e[:m]
    best, lag = -2.0, 0
    for L in range(0, 51):  # processed signal can lag the raw one by up to 500 ms
        x, y = r[: m - L], e[L:]
        if x.std() > 0 and y.std() > 0:
            c = float(np.corrcoef(x, y)[0, 1])
            if c > best:
                best, lag = c, L
    x, y = r[: m - lag], e[lag:]
    voiced = x > max(float(np.percentile(x, 95)) - 25.0, float(np.percentile(x, 10)) + 10.0)
    if voiced.sum() < 30:
        return {}
    g = y[voiced] - x[voiced]
    return {"applied_gain": float(np.median(g)), "applied_gain_hi": float(np.percentile(g, 90)),
            "gain_lag_ms": lag * 10.0, "gain_corr": best}


def countdown(msg, n=3):
    for i in range(n, 0, -1):
        print(f"\r{msg} in {i}... ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 60 + "\r", end="")


# ----------------------------------------------------------------------------- live capture


def capture_live(device, args, short=False, raw=None):
    """Room tone + speech (+ clicks) from `device`. Typing is hidden and discarded the whole time.
    raw: also record the raw mic during speech (to measure the gain EasyEffects adds).
    Returns a 'take': {"noise", "speech", "clicks" (or None), "raw_speech" (or None)}."""
    with hidden_keys():
        ns, ss, cs = (3, 12, 5) if short else (args.noise_secs, args.speech_secs, args.click_secs)
        cd = 2 if short else 3
        print("\n[1] Room tone - stay completely silent")
        countdown("Silence starting", cd)
        noise = record(ns, device)
        if route_problem(device):
            warn_route(device, route_problem(device))
        print("\n[2] Speech - " + ("talk like you do on stream (or read the passage)" if short
                                  else "read this at your normal volume:\n\n" + PASSAGE + "\n"))
        countdown("Start talking", cd + 1)
        raw_speech = None
        if raw and raw != device:
            speech, raw_speech = record_multi(ss, [device, raw])
            if route_problem(raw):
                warn_route(raw, route_problem(raw), "raw-mic recording (used to measure the added gain)")
                print("   Ignoring it this round - the gain cap falls back to the makeup setting.")
                raw_speech = None
        else:
            speech = record(ss, device)
        clicks = None
        if not args.no_click_test:
            print("\n[3] Your usual noise - do NOT talk; type and click like you do while streaming")
            countdown("Start", cd)
            clicks = record(cs, device)
        taps = None
        if not getattr(args, "no_tap_test", False):
            print("\n[4] Desk knocks - do NOT talk; tap the desk, put your mug down, bump the mic arm a few times")
            countdown("Start", cd)
            taps = record(4 if short else getattr(args, "tap_secs", 5), device)
    return {"noise": noise, "speech": speech, "clicks": clicks, "taps": taps, "raw_speech": raw_speech}


def save_take(take, folder):
    from scipy.io import wavfile
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for k in ("noise", "speech", "clicks", "taps"):
        f = folder / f"{k}.wav"
        if take.get(k) is not None:
            wavfile.write(f, SR, np.asarray(take[k], dtype=np.float32))
        elif f.exists():
            f.unlink()


def load_take(folder):
    from scipy.io import wavfile
    folder = Path(folder)
    if not (folder / "speech.wav").exists():
        return None
    take = {}
    for k in ("noise", "speech", "clicks", "taps"):
        f = folder / f"{k}.wav"
        take[k] = wavfile.read(f)[1].astype(np.float32) if f.exists() else None
    return take


# ----------------------------------------------------------------------------- record once: replay through EasyEffects


class ReplayError(Exception):
    pass


def replay_available():
    missing = [t for t in ("pw-loopback", "pactl", "parec") if not shutil.which(t)]
    if not (shutil.which("pw-play") or shutil.which("paplay")):
        missing.append("pw-play (or paplay)")
    return missing


class VirtualMic:
    """A temporary virtual microphone. While active it is the system's default input, so EasyEffects processes
    whatever we play into it. The previous default input is restored afterwards (also on errors / Ctrl-C).
    reload: called after each device switch, because EasyEffects may autoload a different preset then."""
    SRC = "voice2ee_replay_mic"
    SINK = "voice2ee_replay_in"

    def __init__(self, reload=None):
        self.reload, self.proc, self.prev = reload, None, None

    def __enter__(self):
        cmd = ["pw-loopback", "-n", "voice2ee-replay", "-m", "[ FL FR ]",
               "--capture-props", f"media.class=Audio/Sink node.name={self.SINK} "
                                  "node.description=voice2ee-replay-in",
               "--playback-props", f"media.class=Audio/Source node.name={self.SRC} "
                                   "node.description=voice2ee-replay-mic"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            if self.SRC in list_sources():
                break
            time.sleep(0.1)
        else:
            self._stop()
            raise ReplayError("couldn't create the temporary replay microphone (pw-loopback)")
        try:
            self.prev = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True).stdout.strip()
        except Exception:
            self.prev = None
        subprocess.run(["pactl", "set-default-source", self.SRC], check=False)
        time.sleep(1.2)                       # let EasyEffects follow the new default input
        if self.reload:
            self.reload()
        return self

    def play_record(self, seg, ee_src, lead=0.4, tail=0.8, lag=None):
        """Play `seg` into the virtual mic while recording `ee_src`. Returns (processed, lag_samples)."""
        from scipy.io import wavfile
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_DIR / "replay_tmp.wav"
        wavfile.write(tmp, SR, np.asarray(seg, dtype=np.float32))
        total = len(seg) / SR + lead + tail
        box = {}
        t = threading.Thread(target=lambda: box.update(rec=record_multi(total, [ee_src])[0]), daemon=True)
        t.start()
        time.sleep(lead)
        player = (["pw-play", "--target", self.SINK, str(tmp)] if shutil.which("pw-play")
                  else ["paplay", f"--device={self.SINK}", str(tmp)])
        subprocess.run(player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=total + 15)
        t.join(timeout=total + 15)
        rec = box.get("rec")
        if rec is None or len(rec) == 0:
            raise ReplayError("nothing was recorded from the EasyEffects mic")
        if lag is None:
            lag, corr = find_lag(seg, rec, max_s=lead + 1.0)
            if corr < 0.3:
                lag = int(lead * SR)
        out = rec[lag: lag + len(seg)]
        if len(out) < len(seg):
            out = np.concatenate([out, np.zeros((len(seg) - len(out), out.shape[1]), np.float32)])
        return out, lag

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __exit__(self, *exc):
        if self.prev:
            subprocess.run(["pactl", "set-default-source", self.prev], check=False)
        self._stop()
        time.sleep(0.8)
        if self.reload:
            self.reload()
        return False


def replay_take(take, ee_src, reload=None):
    """Play the saved recording through EasyEffects and record the result. Returns a processed take."""
    with hidden_keys(), VirtualMic(reload=reload) as vm:
        print("  replaying your recording through EasyEffects (speech)...")
        speech, lag = vm.play_record(take["speech"], ee_src)
        if 20 * np.log10(np.sqrt(np.mean(mono(speech).astype(np.float64) ** 2)) + 1e-12) < -75:
            raise ReplayError("EasyEffects didn't pick up the replayed audio. In EasyEffects, set the input device to "
                              "'voice2ee-replay-mic' (or turn on 'use default input'), or use live mode.")
        print("  ...room tone...")
        noise, _ = vm.play_record(take["noise"][: 3 * SR], ee_src, lag=lag)
        clicks = None
        if take.get("clicks") is not None:
            print("  ...keyboard/mouse noise...")
            clicks, _ = vm.play_record(take["clicks"], ee_src, lag=lag)
        taps = None
        if take.get("taps") is not None:
            print("  ...desk knocks...")
            taps, _ = vm.play_record(take["taps"], ee_src, lag=lag)
    return {"noise": noise, "speech": speech, "clicks": clicks, "taps": taps, "raw_speech": take["speech"]}
