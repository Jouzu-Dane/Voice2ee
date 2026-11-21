# voice2ee

**Measure your voice, build an EasyEffects microphone preset from it, then tune it round by round until you're happy.**

voice2ee is a command-line tool for Linux users who don't know anything about configuring microphones. It works for general voice chat usage and streaming. It records your mic and room, and writes a
complete [EasyEffects](https://github.com/wwmm/easyeffects) input preset fitted to *your* voice, mic and keyboard
noise. It then keeps measuring what comes out of EasyEffects and adjusts the preset, with your feedback, until it
sounds right.

- **Loudness in LUFS**, the same measure loudness meters and platforms use (default target -18 LUFS)
- **Peaks held under -6 dBFS**, what OBS's mixer meter shows
- **Keyboard and mouse noise** handled by a gate (or expander) calibrated on your actual clicks, plus RNNoise
- **Plosives and sibilance** handled by a steep high-pass, and two de-essers (wide, plus a narrow one for a "snake S")
- **Gain safety:** never adds more than 12 dB to your voice (measured, not guessed). It also catches settings
  that push peaks past the limiter, such as the limiter's *Threshold Boost*.
- **Record once:** record yourself once, and every tuning round replays that recording through EasyEffects.
  No talking, and every round hears identical speech.
- **Versioned presets:** `name_v1` → `name_v1-1`, `name_v1-2`, … → `name_v2`. Nothing is overwritten.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Feedback keys](#feedback-keys)
- [Command-line options](#command-line-options)
- [Files and locations](#files-and-locations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Known limitations](#known-limitations)

## Requirements

| What | Why | Needed? |
|---|---|---|
| Linux with **PipeWire** | EasyEffects runs on PipeWire | required |
| **EasyEffects 8.0.9+** (native or Flatpak) | Local control server: presets load without closing the window, settings are read back, your raw voice is recorded via bypass | required (older versions work with reduced features) |
| **Python 3.9+** with **numpy** and **scipy** | Analysis | required |
| **pactl** and **parec** (PulseAudio utilities; work on PipeWire through pipewire-pulse) | Recording | required |
| **pw-loopback** and **pw-play** (PipeWire tools) | Record-once mode | recommended |
| **ffmpeg** built with **flite** | Synthetic test speech for the test suite | optional |

`install.sh` checks all of these, and knows the package names for these distros:

| Family | Distros |
|---|---|
| Arch | Arch, CachyOS, Manjaro, EndeavourOS, Garuda, Artix |
| Debian | Debian, Ubuntu, Mint, Pop!_OS, elementary, Zorin, KDE neon |
| Fedora | Fedora, RHEL, Rocky, Alma, Nobara |
| Others | openSUSE, Void, Alpine, Gentoo, Solus, NixOS (instructions only) |

On any other distro it tells you what to install.

> **Debian and Ubuntu LTS** ship EasyEffects 7.x. For the full feature set, install the Flatpak:
> `flatpak install flathub com.github.wwmm.easyeffects`

## Installation

```bash
git clone https://github.com/YOUR-USER/voice2ee.git
cd voice2ee
./install.sh
```

The installer:

1. Detects your distro and checks every dependency.
2. Offers to install missing packages with your package manager. It asks first, and this is the only step
   that uses `sudo`.
3. Installs voice2ee for your user, into `~/.local/share/voice2ee`, with a `voice2ee` command in `~/.local/bin`.

| Command | What it does |
|---|---|
| `./install.sh --check` | Only report what's installed or missing |
| `./install.sh --no-deps` | Install the tool without touching system packages |
| `./install.sh --venv` | Get numpy and scipy from pip in a private virtualenv |
| `./install.sh --yes` | Don't ask before installing packages |
| `./install.sh --uninstall` | Remove voice2ee (your presets and recordings are kept) |
| `PREFIX=/opt/voice2ee ./install.sh` | Install somewhere else |

**Without installing**, you can run it straight from the clone:

```bash
python3 voice2ee.py
```

## Quick start

1. **Start EasyEffects**, and make sure your mic goes through it (the *Input* tab).
2. Run **`voice2ee`** and choose:

```
  1) New       - pick modules, record your RAW mic, analyze and write a new preset (name_v1), then keep
                 re-measuring through the EasyEffects mic and refining until you're happy
  2) Existing  - load one of your existing presets, test it as-is through the EasyEffects mic, and tune it
  3) Quit
```

3. Each round, voice2ee shows what it measured, makes automatic adjustments, and asks what you hear (see
   [Feedback keys](#feedback-keys)). Type `q`, `quit` or `exit` when you're happy. The result is saved as the next
   version and loaded into EasyEffects.
4. In OBS, use the **EasyEffects mic** (`easyeffects_source`) and turn OBS's own noise suppression off.

> 🎧 Turn your headphones down a notch before the first round.

**Continue later** with `voice2ee --resume`.

## How it works

### The chain

```
Gate OR Expander → RNNoise → EQ (steep high-pass) → Leveler → Fast peak compressor → Exciter* → De-esser (wide) → De-esser (narrow) → Limiter
                                                                                      * optional
```

You choose the modules in a checklist at the start. Gate and expander are mutually exclusive. Every option of every
plugin is written explicitly, so nothing silently falls back to an EasyEffects default:

- sidechain filters
- pre-mix routing off
- dry/wet levels
- limiter dithering, ALR and Threshold Boost off
- "sidechain listen" and "listen" off
- both de-essers enabled

### The first preset (New mode)

voice2ee records 4 s of room tone, 20 s of you reading a passage, and 6 s of you typing and clicking. From that it
measures:

| Measurement | Used for |
|---|---|
| Noise floor | gate/expander threshold, RNNoise settings |
| Your keyboard/mouse clicks vs your quietest speech | gate/expander threshold (set between them) |
| Pitch | high-pass frequency |
| Spectrum vs a typical voice | EQ cuts and boosts (6–22 bands) |
| Dynamic range (at 10 ms resolution) | compressor ratios and thresholds |
| Sibilance level and how "spiky" it is | de-esser frequencies, thresholds, ratios |
| Loudness (LUFS) | makeup gain (the first preset deliberately starts 3 dB low) |

### Tuning rounds

Every round measures the **EasyEffects mic**, because that's what is being tuned. The tool then:

1. **Checks safety first:** gain on your voice compared with the cap, and peaks getting past the limiter.
2. **Corrects the EQ** toward the target curve, shifted by your feedback.
3. **Adjusts dynamics and level:** firms up compression if your speech peaks spread too widely, and moves the
   loudness toward the LUFS target (at most +1.5 dB per round). If your peaks reach the ceiling first, it makes the
   compression denser instead of adding gain.
4. **Nudges the de-esser and gate.** The de-esser can get gentler but is never switched off. The gate rises if
   clicks leak.
5. **Saves the run** as `name_vN-<run>`, loads it into EasyEffects, and reads the settings back to confirm they
   were applied.

### Record once vs live

| Mode | How a round works | Good for |
|---|---|---|
| **Record once** (default) | Your recording is played into a temporary virtual mic (`voice2ee-replay-mic`), which is the default input for about 30 s. EasyEffects processes it, and the result is recorded. Your real mic is restored right after. | Fast, consistent rounds |
| **Live** | You talk (and type) again every round | Checking the real thing |

During a replay, apps using the default mic (like OBS) hear the replay, so don't run rounds while you're live on stream.

### Raw mic vs EasyEffects mic

Your raw (unprocessed) voice is only needed for two things:

- **Record-once rounds:** the replay needs an unprocessed recording.
- **Exact gain safety:** the tool measures how many dB EasyEffects adds to your voice.

How it's recorded:

- **With EasyEffects' local server:** all effects are bypassed for that one recording, and the EasyEffects mic is
  recorded. There's no device to pick.
- **Otherwise:** the raw device is recorded, and voice2ee checks which source PipeWire really connected it to.

In **Existing** mode, recording your raw voice is optional. Without it, rounds are live, and the gain cap falls
back to the compressor's makeup setting.

### Versions

```
test_v1          ← the preset you start from
test_v1-1        ← run 1 (you're asked whether to keep each run file)
test_v1-2        ← run 2
test_v2          ← saved when you finish (q / quit / exit)
```

Run numbers continue after existing files, and finishing always uses the next free version. Existing presets are
never modified. A preset without a version (`my-voice`) is treated as v1.

## Feedback keys

After each round, type one or more keys (e.g. `b c`), or the word in brackets:

| Key | Meaning | What changes |
|---|---|---|
| `b` | boxy / muddy | less 250–630 Hz |
| `t` | thin | more 100–630 Hz |
| `d` | dull / muffled | more presence and air (and exciter) |
| `h` | harsh / too bright | less presence and air |
| `s` | sharp "s" | stronger de-essers |
| `c` | clicks still leak | gate/expander threshold up |
| `w` | words getting cut | gate/expander down, RNNoise VAD gentler |
| `u` / `l` | too quiet / too loud | makeup ±, and the LUFS target moves with you |
| `e` | echoey / roomy | faster gate release, less low-mids |
| `p` | plosives / pops | high-pass up |
| `m` | mouth clicks / lip smacks | stronger de-esser, faster gate release |
| `+` / `-` | more / fewer EQ bands | re-spaces the EQ (New mode only) |
| *Enter* | measure again | |
| `q` `quit` `exit` | happy: save and finish | |

`q`, `quit` and `exit` work at **every** prompt, in any letter case. Anything you type during a recording is hidden
and thrown away.

## Command-line options

| Option | Default | Description |
|---|---|---|
| `--mode {1,2}` | menu | 1 = new preset, 2 = existing preset |
| `--preset NAME\|PATH` | | (mode 2) preset to tune |
| `--name NAME` | asked | (mode 1) name for the new preset |
| `--modules LIST` | checklist | e.g. `gate,rnnoise,eq,comp,deess,limiter` (`gate` or `expander`; add `exciter` if wanted) |
| `--replay` / `--live` | asked | how rounds are tested |
| `--resume` | | continue the last session |
| `--target-lufs N` | -18 | speech loudness target |
| `--max-gain N` | 12 | most gain (dB) voice2ee may add to your voice |
| `--bands N` | 10 | EQ bands including the high-pass (6–22) |
| `--deess N` | 1.0 | initial de-esser strength (0.3 = light, 2 = strong) |
| `--device NAME` | picked | raw mic source |
| `--ee-device NAME` | `easyeffects_source` | EasyEffects mic source |
| `--list-devices` | | list source names |
| `--out DIR` | EasyEffects preset folder | where presets are written |
| `--reuse` | | (mode 1) reuse the last raw recording |
| `--no-click-test` | | skip the keyboard/mouse recording |

Fine-tuning options: `--box-cut`, `--makeup`, `--gate-offset`, `--no-vad`, `--vad-thres`, `--noise-secs`,
`--speech-secs`, `--click-secs`, `--base`. See `voice2ee --help`.

## Files and locations

| Path | Contents |
|---|---|
| `~/.local/share/easyeffects/input/` | your presets (Flatpak: `~/.var/app/com.github.wwmm.easyeffects/data/easyeffects/input/`) |
| `~/.cache/voice2ee/take/` | the recording used by record-once mode |
| `~/.cache/voice2ee/state.json` | the session, for `--resume` |
| `~/.local/share/voice2ee/` | the installed tool |

## Troubleshooting

**"EasyEffects' local server wasn't found"**
Start EasyEffects (8.0.9+) before voice2ee. Without the server, presets are loaded with `easyeffects -l`, which can
close its window.

**"EasyEffects didn't pick up the replayed audio"**
EasyEffects isn't following the default input. In EasyEffects, choose the input device `voice2ee-replay-mic` (or
enable using the default input), or use `--live`.

**"EasyEffects reports different values than the preset"**
A setting wasn't applied as written, usually a key name that differs in your EasyEffects version. Please open an
issue with the lines it printed.

**"…supposed to come from X, but PipeWire connected it to Y"**
Something moved the recording, often WirePlumber restoring a remembered target. Check the *Recording* tab in
`pavucontrol`, or clear `~/.local/state/wireplumber/`.

**Voice only on one side, or quieter than expected**
Some mono mics reach EasyEffects as left-only stereo. voice2ee warns about it. Fix it with EasyEffects' *Stereo
Tools* at the top of the chain: Stereo Matrix "LR > LL (Mono left channel)".

**"…at its +12 dB limit, raise the mic's own gain"**
Your mic signal is too quiet to reach the target safely. Raise the mic's gain, move closer, or allow more with
`--max-gain` (carefully).

**It sounds squashed**
Use a lower `--target-lufs` (e.g. -20), or press `l` a couple of times.

## Development

```
voice2ee.py          launcher
install.sh           dependency check + installer
v2ee/
  cli.py             menus and main flow
  loop.py            tuning rounds, versions, read-back
  tuning.py          automatic adjustments, feedback keys
  planner.py         first-preset settings from the analysis
  analysis.py        measurements of a recording
  loudness.py        LUFS (ITU-R BS.1770 / EBU R128)
  audio.py           recording, route checks, gain measurement, record-once replay
  easyeffects.py     preset writing/importing, local server, read-back, bypass
  eq.py              EQ band layout
  session.py         versioned names, saved state
  term.py            prompts, quit handling, hidden keys
  config.py          constants and targets
tests/               test suite (simulated EasyEffects chain, fake PipeWire tools, fake EasyEffects server)
```

Run the tests:

```bash
python3 tests/run_tests.py        # no pytest needed
python3 -m pytest -s tests        # or with pytest
```

The tests synthesise realistic test speech with ffmpeg's flite voices: room echo, close-mic bass, breath pops and
keyboard clicks, at a real mic's levels. Tests that need it are skipped if ffmpeg lacks flite. The tests cover:

- LUFS against ffmpeg's EBU R128 meter
- preset completeness
- closed-loop tuning and gain safety
- versioning
- quit words
- hidden keys (in a real pseudo-terminal)
- record-once end to end, against a fake EasyEffects server and fake PipeWire tools

## Known limitations

- **Not yet verified against a real EasyEffects:** the de-esser and exciter key names, and the sidechain filter
  labels, come from exported presets and the plugin docs. The read-back check flags anything EasyEffects applies
  differently.
- **Noise reduction is RNNoise.** Neural models such as DeepFilterNet remove keyboard clicks better. That's a
  possible future option.
- **Targets are heuristics:** the speech target curve, de-esser limits and gate placement are reasonable starting
  points, not universal truths. Your ears and the feedback keys have the final say.
- **Record-once mode** briefly changes the system default input.
- **Some package names are best-effort:** they're well established for Arch, Debian, Fedora and openSUSE, and less
  certain for Gentoo, Void and Solus. `install.sh --check` always tells you what's actually missing.
