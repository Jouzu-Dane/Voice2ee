"""Constants shared by every module."""
import os
from pathlib import Path

import numpy as np

SR = 48000
FRAME = int(0.05 * SR)                   # 50 ms analysis frames
CACHE_DIR = Path.home() / ".cache/voice2ee"
TAKE_DIR = CACHE_DIR / "take"            # the one recording that "record once" mode replays
DATA_DIR = Path(os.environ.get("VOICE2EE_DATA", Path.home() / ".local/share/voice2ee"))   # models (Full version)
CONFIG_DIR = Path(os.environ.get("VOICE2EE_CONFIG", Path.home() / ".config/voice2ee"))   # overrides, taste

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

# Hybrid speech test: free talk (real dynamics, pauses, level swings) + short sentences that make sure there are
# plenty of "s", "sh", "p", "b" and "t" sounds for the de-essers and pop control.
FREE_TALK = ("Talk like you do on stream or in a video - about your day, a game, anything. Normal energy:\n"
             "include a quiet moment, an excited moment and a natural pause to think.")
TEST_SENTENCES = (
    "She sells sea shells by the seashore, and the shells she sells are surely seashells.\n"
    "Peter Piper picked a peck of pickled peppers - big, bold, bright purple peppers.\n"
    "Six sharp students shouted 'stop!' as the speeding bus passed the station at twelve."
)

# approximate long-term average speech spectrum, 1/3-octave levels in dB relative to 1 kHz
TARGET_F = np.array([100, 160, 250, 500, 1000, 2000, 4000, 8000, 10000], dtype=float)
TARGET_DB = np.array([0, 3, 3, 2, 0, -5, -10, -17, -20], dtype=float)

DEFAULT_KEYS = {"gate": "gate#0", "expander": "expander#0", "rnnoise": "rnnoise#0", "eq": "equalizer#0",
                "comp": "compressor#0", "comp2": "compressor#1", "deess": "deesser#0", "deess2": "deesser#1",
                "exciter": "exciter#0", "limiter": "limiter#0", "deep": "deepfilternet#0",
                "taps": "multiband_compressor#0", "stereo": "stereo_tools#0", "meter": "level_meter#0"}

LOW_SPLIT = 150.0                        # Hz: below this, desk taps / thumps live (the "thump catcher" band)

# Profiles: what each preset is FOR. Values are the targets the planner and tuner aim at.
# timing: "tight" closes the noise stage before the room echo ends; "smooth" a bit later (no audible cutting
# in videos); "natural" keeps word tails. lra: loudness range (EBU Tech 3342) to aim for, in LU (None = don't).
# max_gain: most gain voice2ee may add to your voice by default (Video is louder, so it may need more; the
# ramp is still gentle). true_peak: highest true peak allowed (dBTP, measured with 4x oversampling) - None = sample peaks only.
PROFILES = {
    "stream": dict(
        label="Streaming / voice chat - sounds finished live",
        target_lufs=-18.0, peak_lo=-18.0, peak_hi=-6.0, limiter=-6.0, lufs_tol=1.0,
        comp2=True, density=True, leveler_ratio=2.0, deess_cap=4.0, vad=True,
        gate_reduction=-40.0, floor_reduction=-30.0, expander_ratio=2.5, deep_atten=40.0, name="stream-voice",
        timing="tight", eq_strength=1.0, deess_strength=1.0, exciter_ceil=14000.0, echo_check=True,
        click_target=-20.0, snr_target=45.0, lra=None, true_peak=None, max_gain=12.0),
    "video": dict(
        label="Video, ready to publish - finished sound straight out of OBS, minimal editing",
        target_lufs=-16.0, peak_lo=-14.0, peak_hi=-2.0, limiter=-1.5, lufs_tol=1.0,
        comp2=True, density=False, leveler_ratio=2.2, deess_cap=4.0, vad=False,
        gate_reduction=-25.0, floor_reduction=-25.0, expander_ratio=2.0, deep_atten=25.0, name="video-voice",
        timing="smooth", eq_strength=1.0, deess_strength=1.0, exciter_ceil=16000.0, echo_check=True,
        click_target=-18.0, snr_target=42.0, lra=(5.0, 8.0), true_peak=-1.0, max_gain=16.0),
    "record": dict(
        label="Recording video, edited later (lossless) - natural, with headroom",
        target_lufs=-24.0, peak_lo=-20.0, peak_hi=-6.0, limiter=-1.0, lufs_tol=2.0,
        comp2=False, density=False, leveler_ratio=1.8, deess_cap=3.0, vad=False,
        gate_reduction=-20.0, floor_reduction=-20.0, expander_ratio=1.8, deep_atten=15.0, name="record-voice",
        timing="natural", eq_strength=0.7, deess_strength=0.75, exciter_ceil=16000.0, echo_check=False,
        click_target=-10.0, snr_target=35.0, lra=None, true_peak=None, max_gain=12.0),
}

# Best defaults per profile. noise: "gate" | "expander" | "both" | None.
# rnnoise/deep: the noise removers ("deep" = EasyEffects' Deep noise remover, DeepFilterNet).
# RNNoise is the default noise remover in every profile; the Deep noise remover (DeepFilterNet) is an option.
DEFAULT_MODULES = {
    "stream": {"profile": "stream", "noise": "both", "rnnoise": True, "deep": False, "eq": True, "taps": True,
               "comp": True, "deess": True, "exciter": False, "limiter": True, "stereo": "auto", "meter": True},
    "video": {"profile": "video", "noise": "both", "rnnoise": True, "deep": False, "eq": True, "taps": True,
              "comp": True, "deess": True, "exciter": False, "limiter": True, "stereo": "auto", "meter": True},
    "record": {"profile": "record", "noise": "expander", "rnnoise": True, "deep": False, "eq": True,
               "taps": True, "comp": True, "deess": True, "exciter": False, "limiter": True, "stereo": "auto",
               "meter": True},
}


def prof(P, key):
    """A profile target for preset P (P may override it, e.g. via --target-lufs)."""
    return P.get(key, PROFILES[P.get("profile", "stream")][key])


def clip(x, lo, hi):
    return float(np.clip(x, lo, hi))
