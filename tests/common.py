"""Shared helpers for the tests: package import, test speech, simulated chain."""
import subprocess
import sys
import os
import tempfile
import types

os.environ.setdefault("VOICE2EE_CONFIG", tempfile.mkdtemp(prefix="v2ee_cfg_"))
os.environ.setdefault("VOICE2EE_DATA", tempfile.mkdtemp(prefix="v2ee_data_"))
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import sim  # noqa: E402
from v2ee.planner import finalize_mods  # noqa: E402

SPEECH = HERE / ".speech"


try:  # works with pytest (reported as skipped) and with tests/run_tests.py
    import pytest
    Skip = pytest.skip.Exception
except ImportError:
    class Skip(Exception):
        pass


def ensure_speech():
    if not (SPEECH / "speech_5.wav").exists() or not (SPEECH / "taps.wav").exists():
        try:
            subprocess.run([sys.executable, str(HERE / "corpus.py"), str(SPEECH)], check=True, capture_output=True)
        except Exception as ex:
            raise Skip(f"test speech needs ffmpeg with flite ({ex})")


def rd(name):
    from scipy.io import wavfile
    ensure_speech()
    return wavfile.read(SPEECH / name)[1].astype(np.float32)


def args(**kw):
    d = dict(box_cut=0, deess=1.0, makeup=0, gate_offset=0, no_vad=False, vad_thres=70, bands=10, base=None,
             no_click_test=False, noise_secs=4, speech_secs=20, click_secs=6, modules=None, preset=None,
             max_gain=12.0, target_lufs=-18.0, no_tap_test=False, tap_secs=5)
    d.update(kw)
    a = types.SimpleNamespace(**d)
    a.mods = finalize_mods(d.get("mods_dict") or {"noise": "gate", "rnnoise": True, "eq": True, "comp": True,
                                                  "deess": True, "exciter": False, "limiter": True})
    return a


def take_from(i, short=None):
    sp = rd(f"speech_{i}.wav")
    if short:
        sp = sp[: int(short * 48000)]
    return {"noise": rd("noise.wav"), "speech": sp, "clicks": rd("clicks.wav"), "taps": rd("taps.wav"),
            "raw_speech": None}


def processed_take(take, preset):
    return {"noise": sim.process(take["noise"], preset), "speech": sim.process(take["speech"], preset),
            "clicks": sim.process(take["clicks"], preset),
            "taps": sim.process(take["taps"], preset) if take.get("taps") is not None else None,
            "raw_speech": take["speech"]}
