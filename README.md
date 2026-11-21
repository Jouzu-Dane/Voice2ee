# voice2ee

Builds and tunes EasyEffects microphone presets for streaming: loudness in LUFS, peaks under -6 dBFS,
noise / keyboard / mouse pushed down, plosives and sibilance tamed.

## Install

```bash
sudo pacman -S python-numpy python-scipy      # parec (libpulse) and pw-loopback / pw-play (PipeWire) are usually there
chmod +x voice2ee.py
./voice2ee.py
```

Keep EasyEffects (8.0.9 or newer) running. The tool talks to its local server, so presets load without
closing the window, and it reads back what EasyEffects actually applied.

## Menu

1. **New**: pick modules, record your raw mic, write `name_v1`, then tune it round by round.
2. **Existing**: load one of your presets, test it as-is, and tune it. The original is never changed.
3. **Quit**. `q`, `quit` and `exit` work at every prompt, in any letter case.

**Raw mic vs EasyEffects mic**: the tuning rounds always measure the EasyEffects mic, because that's what
is being tuned. Your raw (unprocessed) voice is only needed for two things:

- **Record-once rounds:** the replay needs an unprocessed recording to play through EasyEffects.
- **Exact gain safety:** the tool measures how many dB EasyEffects adds to your voice.

How the raw voice is recorded:

- **With EasyEffects' local server:** all effects are bypassed for that one recording, and the EasyEffects mic
  is recorded. There's no device to pick.
- **Otherwise:** the raw mic device is recorded, and the tool checks which source PipeWire really connected the
  recording to, and warns if it was moved.

In **Existing** mode, recording your raw voice is optional. If you skip it, every round is live, and the gain
cap falls back to the makeup setting (the peak ceiling and Threshold Boost checks still run).

**Testing each round**: either *record once* (recommended) or *live*.

- **Record once:** your recording is replayed through EasyEffects each round, using a temporary virtual mic
  that is the default input for about 30 s. No talking, and every round hears identical speech.
- **Live:** you talk again every round.

**Versions**: each run that changes something becomes `name_v1-1`, `name_v1-2`, ..., and you choose which to
keep. Finishing saves `name_v2`. `--resume` continues from the latest version.

## Targets

- **Loudness:** `--target-lufs` (default -18), measured like a loudness meter on the stereo EasyEffects mic.
  OBS's own mixer meter shows dBFS peaks, not LUFS. A loudness plugin or dock shows LUFS.
- **Peak ceiling:** -6 dBFS, enforced by the limiter with Threshold Boost off.
- **Gain cap:** the tool never adds more than `--max-gain` (default 12 dB) to your voice. This is
  measured live, by recording the raw mic and the EasyEffects mic at the same time.

## Chain

Gate **or** expander → RNNoise → EQ (steep high-pass) → leveler → fast peak compressor → exciter (optional)
→ de-esser (wide) → de-esser (narrow) → limiter.

Every option of every plugin is written explicitly:

- sidechain filters, with pre-mix routing off and dry/wet set
- limiter dithering and ALR off
- de-esser "sidechain listen" and exciter "listen" off
- both de-essers enabled

## Tests

```bash
python3 tests/run_tests.py         # or: python3 -m pytest -s tests
```

The tests need ffmpeg built with flite (to synthesise test speech). Tests that need it are skipped
otherwise. They cover:

- LUFS against ffmpeg's EBU R128 meter
- complete presets
- closed-loop tuning on a simulated chain
- versioning
- quit words
- keys hidden during recording (real pty)
- record-once end to end, with a fake EasyEffects server and fake PipeWire tools

## Known limits

- **Not verified against the real EasyEffects:** the de-esser and exciter key names, and the sidechain
  filter labels (`"12 dB/oct"`), come from real exported presets and the plugin docs. I couldn't read
  EasyEffects' source for them. The read-back check after every load prints any setting EasyEffects
  reports differently.
- **Record-once mode needs EasyEffects to follow the default input** ("use default input"). If it
  doesn't, the tool says so and switches to live mode.
- **Mono mics on one channel:** a mono mic that shows up on the left channel only is detected and reported.
  The fix is EasyEffects' Stereo Tools set to "LR > LL".
