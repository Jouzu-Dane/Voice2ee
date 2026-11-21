"""Constants shared by every module."""
from pathlib import Path

import numpy as np

SR = 48000
FRAME = int(0.05 * SR)                   # 50 ms analysis frames
CACHE_DIR = Path.home() / ".cache/voice2ee"
TAKE_DIR = CACHE_DIR / "take"            # the one recording that "record once" mode replays

# Levels
LEVEL_LO, LEVEL_HI = -18.0, -6.0         # dBFS: speech-peak window (range check) and the limiter ceiling.
                                         # These are what OBS's mixer meter shows (sample peak, dBFS).
TARGET_LUFS = -18.0                      # default speech loudness, measured like a loudness meter on the
                                         # (stereo) EasyEffects mic. Change with --target-lufs.
DEESS_FLOOR = -60.0                      # lowest de-esser threshold (the plugin's range ends at -60 dB)
START_HEADROOM = 3.0                     # a new preset starts this many dB below the computed gain
MAX_UP_STEP = 1.5                        # most gain added per tuning round (going down can be fast)
SAFETY_MARGIN = 3.0                      # average gain above max-gain + this -> emergency cut
HI_SPREAD = 4.0                          # most-lifted syllables may get max-gain + this
MAX_BANDS = 22                           # EQ bands incl. high-pass

NEG_INF = -80.01                         # how EasyEffects stores "-inf" (dry, pre-mix routing, ...)

PASSAGE = (
    "Last Tuesday, Sam sent six short messages to the station about the strange,\n"
    "silver ship that cruised past the sunset. Honestly, nobody believed his story,\n"
    "but he kept telling it with a smile, from the first sip of coffee to the last\n"
    "light of the evening. Tell me what you think, and keep talking like you normally would."
)

# approximate long-term average speech spectrum, 1/3-octave levels in dB relative to 1 kHz
TARGET_F = np.array([100, 160, 250, 500, 1000, 2000, 4000, 8000, 10000], dtype=float)
TARGET_DB = np.array([0, 3, 3, 2, 0, -5, -10, -17, -20], dtype=float)

DEFAULT_KEYS = {"gate": "gate#0", "expander": "expander#0", "rnnoise": "rnnoise#0", "eq": "equalizer#0",
                "comp": "compressor#0", "comp2": "compressor#1", "deess": "deesser#0", "deess2": "deesser#1",
                "exciter": "exciter#0", "limiter": "limiter#0"}


def clip(x, lo, hi):
    return float(np.clip(x, lo, hi))
