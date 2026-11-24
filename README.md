# voice2ee

**Measure your voice, build an EasyEffects microphone preset from it, then tune it round by round until you're happy.**

voice2ee is a command-line tool for Linux streamers and voice-chat users. It records your mic and room, and writes a
complete [EasyEffects](https://github.com/wwmm/easyeffects) input preset fitted to *your* voice, mic and keyboard
noise. It then keeps measuring what comes out of EasyEffects and adjusts the preset, with your feedback, until it
sounds right.

- 🎬 **Two profiles:** *Stream* (finished live sound, -18 LUFS, peaks under -6 dBFS) and *Record* (for lossless
  video recordings you edit later: natural dynamics, headroom, gentle cleanup)
- 🎚️ **Loudness in LUFS**, the same measure loudness meters and platforms use
- ⌨️ **Keyboard and mouse noise** handled by a gate, an expander, or both, calibrated on your actual clicks, plus
  RNNoise and/or the neural **Deep noise remover** (DeepFilterNet)
- 🪵 **Desk knocks and mug thumps** tamed by a tap catcher that only compresses the bass band, set from a
  recording of your actual knocks
- 🐍 **Plosives and sibilance** handled by a steep high-pass, and two de-essers (wide, plus a narrow one for a "snake S")
- 🛡️ **Gain safety:** never adds more than 12 dB to your voice (measured, not guessed). It also catches settings
  that push peaks past the limiter, such as the limiter's *Threshold Boost*.
- 🔁 **Record once:** record yourself once, and every tuning round replays that recording through EasyEffects.
  No talking, and every round hears identical speech.
- 🗂️ **Versioned presets:** `name_v1` → `name_v1-1`, `name_v1-2`, … → `name_v2`. Nothing is overwritten.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Profiles: Stream and Record](#profiles-stream-and-record)
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
| **DeepFilterNet** LADSPA library | EasyEffects' *Deep noise remover* (see [DeepFilterNet](#deepfilternet)) | optional |
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

   After choosing New or Existing, pick the **profile** (Stream or Record) and, for a new preset, tick the modules
   you want. The ticked defaults are the recommended setup for that profile.
3. Each round, voice2ee shows what it measured, makes automatic adjustments, and asks what you hear (see
   [Feedback keys](#feedback-keys)). Type `q`, `quit` or `exit` when you're happy. The result is saved as the next
   version and loaded into EasyEffects.
4. In OBS, use the **EasyEffects mic** (`easyeffects_source`) and turn OBS's own noise suppression off.
   For the Record profile, see [Recording video](#recording-video).

> 🎧 Turn your headphones down a notch before the first round.

**Continue later** with `voice2ee --resume`.

## Profiles: Stream and Record

| | **Stream** | **Record** |
|---|---|---|
| For | Twitch, Discord, voice chat: sounds finished the moment it leaves your PC | Video recorded losslessly (e.g. PCM 32-bit float) and edited later |
| Loudness target | -18 LUFS | -24 LUFS (you normalise in your editor) |
| Peaks | under -6 dBFS | under -6 dBFS, safety limiter at -1 dBFS |
| Noise stage (default) | gate + expander, firm (-40 dB in silence) | expander only, gentle (-20 dB) |
| Noise remover (default) | Deep noise remover if installed, otherwise RNNoise with VAD | Deep noise remover at a gentle 15 dB if installed, otherwise off |
| Compression | leveler + fast peak compressor (+ density step if needed) | gentle leveler only |
| EQ / de-essing | full strength | 70–75 % strength, so the voice stays natural |

Why so different? A stream can't be fixed afterwards, so it's processed to sound finished. A recording will be
edited, so the preset only fixes what's hard to fix later (rumble, boxiness, harsh "s", knocks) and leaves dynamics
and room for your editor. Heavy noise removal and squashing can't be undone in post; a little noise can be removed
there with better offline tools.

### Recording video

- **OBS:** *Settings → Output → Output Mode: Advanced → Recording*: format **MKV** (or MOV), audio encoder **PCM
  32-bit float** (the exact label varies a little by OBS version). It stores exactly what EasyEffects outputs and
  can't clip after that point. It can't undo clipping at the mic itself, so keep the mic's own gain sensible.
- Add your **raw mic as a second audio track** (*Advanced Audio Properties → Tracks*) as a safety copy.
- **In DaVinci Resolve,** normalise loudness at the end (YouTube plays back around -14 LUFS).

## How it works

### The chain

```
Stereo fix* → Gate → Expander → RNNoise → Deep noise remover → Tap catcher → EQ (steep high-pass)
  → Leveler → Fast peak compressor → Exciter → De-esser (wide) → De-esser (narrow) → Limiter → Level meter
* added automatically only if your mic reaches one channel only
```

You choose the modules in a checklist at the start; everything is optional and the ticked defaults are the best
setup for the chosen profile:

| # | Module | Choices |
|---|---|---|
| 1 | Noise stage | gate + expander / gate / expander / off |
| 2 | Noise remover | Deep noise remover / RNNoise / both / off |
| 3 | Equalizer | with a steep high-pass against pops |
| 4 | Desk-tap catcher | multiband compressor on the bass band only |
| 5 | Compressors | leveler (+ fast peak compressor in Stream) |
| 6 | De-essers | wide + narrow "snake S" |
| 7 | Exciter | off by default: it adds air but also emphasises "s" and clicks |
| 8 | Limiter | -6 dBFS (Stream) / -1 dBFS safety (Record) |
| 9 | Automatic mono fix | Stereo Tools, added only when needed |
| 10 | Level meter | at the end, to watch loudness in EasyEffects |

### Gate + expander

A **gate** is a switch: below its threshold it cuts deeply. Great against clicks in silence, but it can chop soft
word endings. An **expander** is a dimmer: the further below its threshold, the more it turns down. Smooth on
speech, but on its own it lets loud clicks through partly.

**Together** (the Stream default) each does what it's good at:

1. The **gate** sits low, just above your room noise (noise floor + 8 dB), and cuts silence deeply. It never gets
   near your speech, so it can't chop words.
2. The **expander** sits at the calibrated threshold, between your clicks and your quietest speech, and gently
   pushes clicks and breaths down without a hard on/off.

The `c` and `w` keys move the expander; the gate floor stays put.

### Desk-tap catcher

Knocking the desk, putting a mug down or bumping the mic arm travels through the stand as a low **thump** (roughly
40–150 Hz), often louder than your voice's own bass. An EQ cut would thin your voice all the time, so voice2ee uses
EasyEffects' **Multiband Compressor** as a catcher instead: only the band below ~150 Hz is compressed (8:1, 1 ms
attack, peak detection), and its threshold sits just above the loudest bass your voice produces. Your voice passes
untouched; knocks are squashed.

In New mode you're asked to knock on the desk for a few seconds (without talking) so the threshold can be set from
your real knocks. Each round it re-checks and firms the catcher up if knocks still poke out. Press `k` if you still
hear them. A boom arm with a shock mount helps more than any plugin.

### DeepFilterNet

EasyEffects' *Deep noise remover* uses **DeepFilterNet**, a neural model that removes keyboard clicks and steady
noise much better than RNNoise, with fewer artefacts. EasyEffects needs its LADSPA library:

| Distro | How |
|---|---|
| Arch, CachyOS, Manjaro… | `sudo pacman -S deepfilternet-ladspa` (`install.sh` offers it) |
| Others (native EasyEffects) | Download `libdeep_filter_ladspa-<version>-x86_64-unknown-linux-gnu.so` from the [DeepFilterNet releases](https://github.com/Rikorose/DeepFilterNet/releases), copy it to `/usr/lib64/ladspa/libdeep_filter_ladspa.so` (Fedora, openSUSE) or `/usr/lib/ladspa/libdeep_filter_ladspa.so` (others), restart EasyEffects |
| Flatpak | Check whether EasyEffects shows the Deep noise remover as available; if not, see the EasyEffects docs for your version |

voice2ee detects it: when it's installed, the Deep noise remover becomes the default remover and RNNoise's voice
detection is turned off (combining them can cut words). Its attenuation limit is 40 dB (Stream) or 15 dB (Record);
`c` raises it, `w` lowers it.

### Plugin settings

Every option of every plugin is written explicitly, so nothing silently falls back to an EasyEffects default:

- sidechain filters
- pre-mix routing off
- dry/wet levels
- limiter dithering, ALR and Threshold Boost off
- "sidechain listen" and "listen" off
- both de-essers enabled

### The first preset (New mode)

voice2ee records 4 s of room tone, 20 s of you reading a passage, 6 s of you typing and clicking, and 5 s of you
knocking on the desk (skipped if the tap catcher is off). From that it measures:

| Measurement | Used for |
|---|---|
| Noise floor | gate/expander threshold, RNNoise settings |
| Your keyboard/mouse clicks vs your quietest speech | gate/expander threshold (set between them) |
| Your desk knocks vs your voice's loudest bass | tap catcher threshold |
| Left/right balance | automatic mono fix |
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
| `c` | clicks still leak | gate/expander threshold up, Deep noise remover stronger |
| `w` | words getting cut | gate/expander down, RNNoise VAD gentler, Deep noise remover gentler |
| `u` / `l` | too quiet / too loud | makeup ±, and the LUFS target moves with you |
| `e` | echoey / roomy | faster gate release, less low-mids |
| `p` | plosives / pops | high-pass up |
| `m` | mouth clicks / lip smacks | stronger de-esser, faster gate release |
| `k` | desk knocks / taps | tap catcher: lower threshold, higher ratio, wider band |
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
| `--profile {stream,record}` | asked | what the preset is for |
| `--modules LIST` | checklist | e.g. `gate+expander,deep,eq,taps,comp,deess,limiter,stereo,meter` (noise: `gate`, `expander` or `gate+expander`; also `rnnoise`, `exciter`) |
| `--replay` / `--live` | asked | how rounds are tested |
| `--resume` | | continue the last session |
| `--target-lufs N` | profile | speech loudness target (Stream -18, Record -24) |
| `--max-gain N` | 12 | most gain (dB) voice2ee may add to your voice |
| `--bands N` | 10 | EQ bands including the high-pass (6–22) |
| `--deess N` | 1.0 | initial de-esser strength (0.3 = light, 2 = strong) |
| `--device NAME` | picked | raw mic source |
| `--ee-device NAME` | `easyeffects_source` | EasyEffects mic source |
| `--list-devices` | | list source names |
| `--out DIR` | EasyEffects preset folder | where presets are written |
| `--reuse` | | (mode 1) reuse the last raw recording |
| `--no-click-test` | | skip the keyboard/mouse recording |
| `--no-tap-test` | | skip the desk-knock recording |
| `--tap-secs N` | 5 | length of the desk-knock recording |

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
Some mono mics reach EasyEffects as left-only stereo. With the automatic mono fix ticked (default), voice2ee adds
EasyEffects' *Stereo Tools* at the top of the chain with Stereo Matrix "LR > LL (Mono left channel)" (or the right
channel). With it unticked, it only warns.

**"…at its +12 dB limit, raise the mic's own gain"**
Your mic signal is too quiet to reach the target safely. Raise the mic's gain, move closer, or allow more with
`--max-gain` (carefully).

**It sounds squashed**
Use a lower `--target-lufs` (e.g. -20), press `l` a couple of times, or use the Record profile.

**"Deep noise remover not available" in EasyEffects**
DeepFilterNet isn't installed; see [DeepFilterNet](#deepfilternet), or switch the remover to RNNoise (item 2 in
the checklist).

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
- profiles, gate + expander order, tap catcher (knocks down ≥ 6 dB, voice bass unchanged), stereo fix, Deep noise
  remover detection, importing presets that use the new plugins
- quit words
- hidden keys (in a real pseudo-terminal)
- record-once end to end, against a fake EasyEffects server and fake PipeWire tools

## Known limitations

- **Not yet verified against a real EasyEffects:** the de-esser and exciter key names, and the sidechain filter
  labels, come from exported presets and the plugin docs. The read-back check flags anything EasyEffects applies
  differently.
- **The simulator doesn't model the Deep noise remover** (it's treated as pass-through in the tests), and the
  Multiband Compressor and Stereo Tools key names come from EasyEffects' source and exported presets. The read-back
  check flags anything applied differently.
- **EasyEffects can't host other plugins:** only its built-in effects (and DeepFilterNet through its own module).
- **Targets are heuristics:** the speech target curve, de-esser limits and gate placement are reasonable starting
  points, not universal truths. Your ears and the feedback keys have the final say.
- **Record-once mode** briefly changes the system default input.
- **Some package names are best-effort:** they're well established for Arch, Debian, Fedora and openSUSE, and less
  certain for Gentoo, Void and Solus. `install.sh --check` always tells you what's actually missing.
