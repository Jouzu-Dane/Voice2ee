"""LUFS implementation vs ffmpeg's EBU R128 meter (skipped if ffmpeg isn't installed)."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from common import Skip, rd
from v2ee import loudness


def _ffmpeg_lufs(x):
    from scipy.io import wavfile
    f = Path(tempfile.mkdtemp()) / "x.wav"
    wavfile.write(f, 48000, np.asarray(x, dtype=np.float32))
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(f), "-filter_complex", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])


def test_matches_ffmpeg():
    if not shutil.which("ffmpeg"):
        raise Skip("ffmpeg not installed")
    for name in ("speech_0.wav", "speech_3.wav"):
        x = rd(name).astype(np.float64)
        st = np.stack([x, 0.5 * x], axis=1)
        assert abs(loudness.integrated(st) - _ffmpeg_lufs(st)) < 0.15
        assert abs(loudness.integrated(x) - (_ffmpeg_lufs(x) + 3.01)) < 0.15   # mono measured as dual mono


def test_tone_reference():
    t = np.arange(48000 * 10) / 48000
    tone = 0.1 * np.sin(2 * np.pi * 1000 * t)  # -20 dBFS peak sine on both channels -> -20 LUFS
    assert abs(loudness.integrated(np.stack([tone, tone], 1)) + 20.0) < 0.1
