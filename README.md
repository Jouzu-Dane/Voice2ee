# voice2ee

**Measure your voice, build an EasyEffects microphone preset from it, then tune it round by round until you're happy.**

voice2ee is a command-line tool for Linux streamers and voice-chat users. It records your mic and room, and writes a
complete [EasyEffects](https://github.com/wwmm/easyeffects) input preset fitted to *your* voice, mic and keyboard
noise. It then keeps measuring what comes out of EasyEffects and adjusts the preset, with your feedback, until it
sounds right.

- **Three profiles:** *Stream* (finished live sound), *Video* (ready to publish straight out of OBS, minimal
  editing) and *Record* (lossless recordings you finish in an editor)
- **Loudness in LUFS**, the same measure loudness meters and platforms use
- **Keyboard and mouse noise** handled by a gate, an expander, or both, calibrated on your actual clicks, plus
  RNNoise and/or the neural **Deep noise remover** (DeepFilterNet)
- **Desk knocks and mug thumps** tamed by a tap catcher that only compresses the bass band, set from a
  recording of your actual knocks
- **Plosives and sibilance** handled by a steep high-pass, and two de-essers (wide, plus a narrow one for a "snake S")
- **Gain safety:** never adds more than 12 dB to your voice (measured, not guessed). It also catches settings
  that push peaks past the limiter, such as the limiter's *Threshold Boost*.
- **Record once:** record yourself once, and every tuning round replays that recording through EasyEffects.
  No talking, and every round hears identical speech.
- **Optimise:** tries a few hundred variations of the preset on your recording and keeps the best, judged
  by a scorecard (loudness, clicks, knocks, words kept, tone, sibilance, background noise)
- **A/B picks:** pick the better of two versions about 12 times; voice2ee learns your taste and saves it
- **Minimal or Full:** both run your real EasyEffects plugins offline for Optimise and A/B picks; Full adds a
  speech-quality model. A built-in plugin model is the optional fallback (see [Minimal or Full](#minimal-or-full))
- **Versioned presets:** `name_v1` → `name_v1-1`, `name_v1-2`, … → `name_v2`. Nothing is overwritten.

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Minimal or Full](#minimal-or-full)
- [Quick start](#quick-start)
- [Profiles: Stream, Video and Record](#profiles-stream-video-and-record)
- [How it works](#how-it-works)
- [Optimise and A/B picks](#optimise-and-ab-picks)
- [Feedback keys](#feedback-keys)
- [Locks and ranges](#locks-and-ranges)
- [Settings](#settings)
- [Command-line options](#command-line-options)
- [Files and locations](#files-and-locations)
- [Privacy and your recordings](#privacy-and-your-recordings)
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
git clone https://github.com/Jouzu-Dane/voice2ee.git
cd voice2ee
./install.sh
```

The installer:

1. Asks whether you want the **Minimal** or the **Full** version, and explains when each makes sense.
2. Detects your distro and checks every dependency.
3. Lists every missing package with what it's for, and offers to install them with your package manager. It
   asks first, and this is the only step that uses `sudo`. (Full version on Arch/CachyOS: `python-lilv`,
   `lsp-plugins-lv2`, `calf`; on Debian/Ubuntu: `python3-lilv`, `lsp-plugins-lv2`, `calf-plugins`.)
4. Installs voice2ee for your user, into `~/.local/share/voice2ee`, with a `voice2ee` command in `~/.local/bin`.
   For the Full version it asks before setting up a private virtualenv with onnxruntime and before downloading
   the DNSMOS model.
5. Checks everything again at the end. If a part is still missing, it tells you exactly which command fixes it;
   until then voice2ee does that part the Minimal way.

| Command | What it does |
|---|---|
| `./install.sh --minimal` | Install the Minimal version with your real plugins (no questions about the edition) |
| `./install.sh --builtin` | Install the Minimal version with the built-in plugin model only |
| `./install.sh --full` | Install the Full version |
| `./install.sh --check` | Only report what's installed or missing, for both versions |
| `./install.sh --no-deps` | Install the tool without touching system packages |
| `./install.sh --venv` | Get numpy and scipy from pip in a private virtualenv |
| `./install.sh --yes` | Don't ask before installing packages |
| `./install.sh --uninstall` | Remove voice2ee, incl. the Full version's virtualenv and model, and offer to remove system packages it installed that nothing else needs (your presets, recording, taste and locks are kept) |
| `PREFIX=/opt/voice2ee ./install.sh` | Install somewhere else |

**Uninstalling.** The installer remembers which system packages *it* installed (only ones you didn't have
before). `--uninstall` checks each of them with your package manager:

- needed by another installed package (e.g. the LSP plugins by EasyEffects): kept
- only voice2ee needed it (e.g. `python-lilv`, `calf`): offered for removal, default yes
- nothing depends on it, but you may use it yourself (EasyEffects, PipeWire tools, ffmpeg, DeepFilterNet,
  Python/numpy/scipy): asked separately, default no, and never removed with `--yes`

On distros where the dependency check isn't available, packages are kept and listed. Your presets, recording,
taste and locks are always kept.

**Without installing**, you can run it straight from the clone:

```bash
python3 voice2ee.py
```

## Minimal or Full

Both versions have every feature, and both use your **real EasyEffects plugins** for Optimise and A/B picks.
Full adds a speech-quality model.

| | **Minimal** | **Full** |
|---|---|---|
| Needs | Python, numpy, scipy, PipeWire tools, `python-lilv` (about 240 KB) and the LSP/Calf LV2 plugins (usually already there with a native EasyEffects) | Minimal + onnxruntime (~60 MB, from pip into voice2ee's own virtualenv) and the DNSMOS model (a few MB) |
| Optimise and A/B picks | your real plugins, offline: what it picks is what you hear | the same |
| Quality score | – | DNSMOS, a model trained on human listening ratings, helps Optimise choose between candidates |
| Download | small | needs internet once |

**Choose Minimal if** you want a great preset without big extras. **Choose Full if** you want the best possible
fit, re-tune often, or use several mics or rooms.

**The built-in model (optional fallback).** After choosing Minimal, the installer asks whether to use your real
plugins (recommended) or voice2ee's built-in model only. The built-in model needs nothing extra: it's a numpy
re-creation of the plugins, close but not exact, and every result is checked and corrected through EasyEffects
afterwards. Pick it with the EasyEffects **Flatpak** (its plugins live inside the Flatpak) or if you don't want
extra packages. voice2ee also falls back to it by itself whenever the real plugins don't work, and says why.

| Command | What you get |
|---|---|
| `./install.sh --minimal` | Minimal with your real plugins |
| `./install.sh --builtin` | Minimal with the built-in model only |
| `./install.sh --full` | Full (real plugins + quality model) |

Switch any time. `voice2ee --check-full` shows which parts are installed and runs your plugins once in a separate
process, so a broken plugin can't crash the tool.

## Quick start

1. **Start EasyEffects**, and make sure your mic goes through it (the *Input* tab).
2. Run **`voice2ee`** and choose:

```
  1) New       - pick a profile and modules, record your RAW mic, analyze and write a new preset (name_v1),
                 then keep re-measuring through the EasyEffects mic and refining until you're happy
  2) Existing  - load one of your existing presets, test it as-is through the EasyEffects mic, and tune it
  3) Settings  - defaults for everything: profile, Optimise, CPU cores, recordings, devices, colours, ...
  4) Quit
```

   After choosing New or Existing, pick the **profile** (Stream, Video or Record) and, for a new preset, tick the
   modules you want. The ticked defaults are the recommended setup for that profile. Anything you set in
   **Settings** is used instead of asking.
3. Each round, voice2ee shows what it measured, makes automatic adjustments, and asks what you hear (see
   [Feedback keys](#feedback-keys)). Type `q`, `quit` or `exit` when you're happy. The result is saved as the next
   version and loaded into EasyEffects.
4. In OBS, use the **EasyEffects mic** (`easyeffects_source`) and turn OBS's own noise suppression off.
   For the Record profile, see [Recording video](#recording-video).

> Turn your headphones down a notch before the first round.

**Continue later** with `voice2ee --resume`.

## Profiles: Stream, Video and Record

**Quick rule:** you go live → **Stream**. You upload what you recorded → **Video**. You edit the audio
afterwards → **Record**.

| | **Stream** | **Video** (ready to publish) | **Record** (edited later) |
|---|---|---|---|
| For | Twitch, Discord, voice chat | videos that need (almost) no audio editing | videos you finish in DaVinci Resolve etc. |
| Idea | sounds finished the moment it leaves your PC | finished sound straight out of OBS | fixes only what's hard to fix later; you do the rest |
| Loudness | -18 LUFS | **-16 LUFS** (spoken YouTube level; YouTube plays at about -14) | about -24 LUFS, normalise later |
| Peaks | under -6 dBFS (OBS-friendly) | **true peak** at most -1 dBTP, measured with 4× oversampling | under -6 dBFS, safety limiter at -1 |
| Evenness | strongest compression, + density step if needed | aims for a **loudness range of 5–8 LU**: even, not squashed | natural dynamics |
| Noise stage (default) | gate + expander, firm (-40 dB in silence) | gate + expander, gentle (-25 dB), no audible cutting | expander only, gentle (-20 dB) |
| Noise remover (default) | RNNoise with voice detection | RNNoise without voice detection | RNNoise without voice detection |
| Noise stage timing | closes before the room echo ends | closes after word tails, never mid-word | keeps word tails |
| EQ / de-essing | full strength | full strength | 70–75 % strength |
| Gain cap (default) | +12 dB | +16 dB (it's louder) | +12 dB |
| Undo later? | – | no: processing is baked in, keep a raw track | yes, nearly everything |

Why so different? A stream can't be fixed afterwards, so it's processed to sound finished. A video for upload is
the same idea, but for a viewer listening closely with headphones: louder, evener and cleaner, yet without the
hard gating and squashing that are acceptable live. A recording you'll edit only gets what's hard to fix later
(rumble, boxiness, harsh "s", knocks); dynamics and room are left for your editor, because heavy noise removal and
squashing can't be undone.

### Recording a video, ready to publish (Video)

- **OBS:** *Settings → Output → Recording*: MKV (remux to MP4 afterwards) or MP4, audio AAC 320 kbps, or PCM if you
  still want lossless.
- **Strongly recommended:** your **raw mic on a second audio track** (*Advanced Audio Properties → Tracks*). The
  processing is baked into track 1; track 2 lets you redo a take's sound if something ever goes wrong.
- **Game audio or music:** keep it about 10–15 dB under your voice in OBS. Its level isn't part of this preset.
- **Still worth doing in the edit:** cut mistakes and long pauses. The voice itself needs no further processing.
- **What a preset can't fix:** room echo (EasyEffects has no real echo remover; get closer to the mic, add soft
  furnishings), and breaths and lip smacks are quieter but not gone.

### Recording video to edit later (Record)

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

RNNoise is the default remover in every profile; pick the Deep noise remover with item 2 in the module checklist
(voice2ee shows whether DeepFilterNet is installed). When it's on, RNNoise's voice detection is turned off
(combining them can cut words). Its attenuation limit is 40 dB (Stream), 25 dB (Video) or 15 dB (Record); `c`
raises it, `w` lowers it.

### Plugin settings

Every option of every plugin is written explicitly, so nothing silently falls back to an EasyEffects default:

- sidechain filters
- pre-mix routing off
- dry/wet levels
- limiter dithering, ALR and Threshold Boost off
- "sidechain listen" and "listen" off
- both de-essers enabled

### The first preset (New mode)

voice2ee records 4 s of room tone, then your speech in two parts (the same for every profile):

1. **About 35 s of free talk**, like you do on stream or in a video: normal energy, with a quiet moment, an excited
   moment and a natural pause to think. This gives the real dynamics, pauses and level swings.
2. **About 12 s of three short test sentences**, packed with "s", "sh", "p", "b" and "t" sounds, so the de-essers
   and pop control always have enough to measure.

Then 6 s of you typing and clicking, and 5 s of you knocking on the desk (skipped if the tap catcher is off).
`--speech-test read` (a passage, 20 s) or `--speech-test free` (free talk only) are the alternatives. From all of
that it measures:

| Measurement | Used for |
|---|---|
| Noise floor | gate/expander threshold, RNNoise settings |
| Your keyboard/mouse clicks vs your quietest speech | gate/expander threshold (set between them) |
| Your desk knocks vs your voice's loudest bass | tap catcher threshold |
| How long a knock rings | tap catcher release |
| Thinking pauses (> 1 s): the background in them, and how loud the first word after one is | noise stage threshold, peak compressor attack (also checked every round) |
| Left/right balance | automatic mono fix |
| Pitch | high-pass frequency |
| Rumble in the room and how strong your pops are | high-pass slope (18 or 24 dB/oct) and a few Hz higher with rumble |
| Syllables per second | leveler, peak compressor and limiter release times |
| How fast your syllables start | gate/expander opening time, peak compressor attack, limiter lookahead |
| How long sound lingers after a word (word ending + room echo) and your pauses | gate/expander release (Stream: shorter than the echo; Record: natural) |
| Where your "s" band starts and ends | de-esser split frequencies and the narrow de-esser's width (Q) |
| Air (8–12 kHz), the "s" band, hiss at the top | exciter start (scope), ceiling and harmonics |
| Spectrum vs a typical voice | EQ cuts and boosts (6–22 bands) |
| Dynamic range (at 10 ms resolution) | compressor ratios and thresholds |
| Sibilance level and how "spiky" it is | de-esser frequencies, thresholds, ratios |
| Loudness (LUFS) | makeup gain (the first preset deliberately starts 3 dB low) |

Every number in the preset is either **measured from you** (above) or a **deliberately fixed safety/neutral
value**: pre-mix routing off, dry signal off, "listen" modes off, Threshold Boost off, sidechain sources, knee
widths. Each note voice2ee prints says which measurement a setting came from.

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

## Optimise and A/B picks

### Optimise (step 2)

After measuring your voice (New mode), voice2ee asks how to fit the first preset:

```
  1) Quick     - measure and predict (a few seconds), then refine in rounds
  2) Optimise  - try a few hundred variations on your recording and keep the best, then refine in rounds
```

Optimise asks **what matters most**, then varies the settings of the modules you picked around the measured
preset and scores each try on your own recording:

| Goal | Leans towards |
|---|---|
| Balanced (default) | everything equally |
| Loud keyboard | clicks and knocks gone, even at some naturalness cost |
| Natural | fewest artefacts, keeps room and breaths |
| Broadcast | dense, even, up-front voice |
| Clarity | intelligibility first (calls, teaching) |

The scorecard:

| Part | How it's measured |
|---|---|
| Loudness | LUFS vs the profile's target |
| Clicks, knocks | what's left of your click and knock recordings, vs your speech |
| Words kept | ESTOI intelligibility of processed vs raw speech: drops when word endings get cut |
| Tone | spectrum vs the target curve (moved by your saved taste) |
| Sibilance, background noise | as in the round reports |
| Loudness range (Video) | EBU loudness range vs 5–8 LU |
| Quality (Full) | DNSMOS overall score, 1-5 |
| Hard limits | peaks over the ceiling, true peaks over -1 dBTP (Video) or more gain than allowed reject a try outright |

The search is a small evolution strategy: it tries variations around the best so far, searches wider while it
keeps improving and narrower when it doesn't. It uses 75 % of your CPU cores (`--jobs` or Settings to
change): each step of the search tries one variation per core at the same time. Typical run: 300 tries with your real
plugins (a few minutes, depending on your CPU); 200 with the built-in model (well under a minute on an 8-core
CPU). If it uses the built-in model, it says why. Press **Ctrl+C** to stop early: the best result so far is kept. A final step fine-tunes the
loudness directly. When it's done, a **before/after** comparison plays a short clip
(free talk, test sentences, a moment of keyboard noise) through the measured preset and then through the optimised
one, at the same loudness so you hear the sound itself (the real loudness difference is stated). Press Enter to
continue with the optimised preset, `r` to play again, or `k` to keep the measured one.

### What next

Once the first preset exists (after Quick, or after Optimise and its before/after), nothing runs through
EasyEffects until you choose:

```
  1) I'm happy with this - save it, load it into EasyEffects and quit
  2) Re-run Optimise (continues from this result)     (or "Optimise" after Quick)
  3) Tune - feedback keys, A/B picks, or both
```

**Tune** then asks how:

| Choice | What happens |
|---|---|
| Feedback keys | tuning rounds through EasyEffects: measure, automatic adjustments, and your feedback keys |
| A/B picks | about 12 picks, then the preset moves to your taste and you're back at the menu above (no rounds) |
| Both | A/B picks first, then the tuning rounds |

RNNoise and the Deep noise remover can't run offline, so the first tuning round is where they're judged. 

### A/B picks (step 3)

You hear version A, then version B: the same short clip (about 5 s of your free talk, 4 s of the test sentences
and a moment of keyboard noise), same loudness, random order. Press `a` or `b` for the better
one (`s` = can't tell, `x` = both bad, `r` = play again, `q` = stop). Each pair differs along up to five
**taste directions**:

| Direction | What changes |
|---|---|
| Brightness | presence, 2–6 kHz |
| Warmth | body, 120–400 Hz |
| Polish | compression: even and dense vs natural dynamics |
| Cleanliness | noise removal: silent background vs open and natural |
| Air | top end above 7 kHz (and the exciter, if on) |

A Bayesian preference model estimates where your taste sits and places the next pair where it's still least sure,
so about 12 picks are enough. The result moves the preset and is **saved** (`~/.config/voice2ee/taste.json`):
new presets start from it and Optimise aims for it. Use `--no-taste` to ignore it.

After each pick, voice2ee tells you what the difference was and by how much, for example
*"brightness: B was brighter. EQ at 2-6 kHz: A -3.2 dB, B +3.2 dB (you picked the brighter one)"*. It's shown
only after you've answered, so the test stays blind, but you learn what you're actually choosing between.

Each pair differs in **one** direction at a time, clearly at first (about 6 dB of EQ, for example) and finer
as it learns, so every pick is an easy call.

The versions are made **instantly**: with your real plugins offline in the Full version, or with the built-in
model in the Minimal version. What matters for a pick is the difference between A and B, which the built-in model
gets right; the result is applied to the real preset and checked through EasyEffects afterwards. If you prefer
every version to come from EasyEffects itself, set *A/B versions made with* to `easyeffects` in Settings (or
`--ab-render easyeffects`); each pick then takes about 20 s to prepare.

Choose A/B picks under *Tune*, or press `a` in any tuning round. In Existing mode, A/B picks only (no rounds)
saves the result as the next version.

## Feedback keys

After each round, type one or more keys (e.g. `b c`), or the word in brackets:

| Key | Meaning | What changes |
|---|---|---|
| `b` | boxy / muddy | less 250–630 Hz |
| `t` | thin | more 100–630 Hz |
| `d` | dull / muffled | more presence and air; exciter stronger and starting lower |
| `h` | harsh / too bright | less presence and air; exciter gentler and starting higher |
| `s` | sharp "s" | stronger de-essers |
| `c` | clicks still leak | gate/expander threshold up, Deep noise remover stronger |
| `w` | words getting cut | gate/expander down, RNNoise VAD gentler, Deep noise remover gentler |
| `u` / `l` | too quiet / too loud | makeup ±, and the LUFS target moves with you |
| `e` | echoey / roomy | faster gate release, less low-mids (also automatic when word tails get longer after processing) |
| `p` | plosives / pops | high-pass up |
| `m` | mouth clicks / lip smacks | stronger de-esser, faster gate release |
| `k` | desk knocks / taps | tap catcher: lower threshold, higher ratio, wider band |
| `+` / `-` | more / fewer EQ bands | re-spaces the EQ. On an existing preset the new bands are fitted to reproduce your curve, and narrow bands (notches), filters and the high-pass stay exactly as you made them |
| `a` | A/B picks | about 12 picks between two versions; learns and saves your taste |
| *Enter* | measure again | |
| `q` `quit` `exit` | happy: save and finish | |

`q`, `quit` and `exit` work at **every** prompt, in any letter case. Anything you type during a recording is hidden
and thrown away.

## Locks and ranges

Keep any setting fixed, or within limits, by creating `~/.config/voice2ee/overrides.toml` (Python 3.11+) or
`overrides.json`:

```toml
[lock]
limiter = -3.0            # always -3 dBFS
"gate.release" = 120      # ms

[range]
"comp.ratio" = [1.5, 3.0]
"eq.hpf" = [80, 110]
```

Locks and ranges are respected by the first preset, every automatic adjustment, every feedback key, Optimise and
A/B picks. Common paths: `limiter`, `gate.thr`, `gate.release`, `gate.ratio`, `floor.thr`, `deep.atten`,
`taps.thr`, `taps.ratio`, `eq.hpf`, `comp.thr`, `comp.ratio`, `comp.makeup`, `comp.release`, `comp2.thr`,
`comp2.ratio`, `deess.thr`, `deess.ratio`, `deess2.thr`, `deess2.ratio`, `exciter.amount`, `exciter.scope`.

## Settings

Choose **3) Settings** on the first screen to set defaults for everything, so voice2ee stops asking about things
you always answer the same way:

| Setting | Default |
|---|---|
| Profile, how to fit the first preset, Optimise goal, fine-tune method, tuning rounds | ask each time |
| Optimise tries | automatic (300 exact / 200 approximate) |
| Before/after after Optimise | on |
| Optimise/A-B processing | auto: your real plugins when they work, else the built-in model |
| A/B versions made with | auto (instant) |
| CPU cores for Optimise | 75 % of your cores |
| Speech recording | hybrid (free talk + test sentences); free-talk and sentence lengths |
| Keyboard/mouse and desk-knock recordings | on |
| EQ bands, gain cap, loudness target | 10, profile default, profile default |
| Start from my saved A/B taste, use the quality model | on |
| Raw mic, EasyEffects mic | ask / `easyeffects_source` |
| Colours | auto: green = working, bold yellow = warning or heads-up, bold red = problem (`NO_COLOR` turns them off) |

Settings are saved in `~/.config/voice2ee/settings.json`. Options typed on the command line still win for that
run. `r` in the settings screen resets everything.

## Command-line options

| Option | Default | Description |
|---|---|---|
| `--mode {1,2}` | menu | 1 = new preset, 2 = existing preset |
| `--preset NAME\|PATH` | | (mode 2) preset to tune |
| `--name NAME` | asked | (mode 1) name for the new preset |
| `--modules LIST` | checklist | e.g. `gate+expander,deep,eq,taps,comp,deess,limiter,stereo,meter` (noise: `gate`, `expander` or `gate+expander`; also `rnnoise`, `exciter`) |
| `--replay` / `--live` | asked | how rounds are tested |
| `--resume` | | continue the last session |
| `--profile {stream,video,record}` | asked | what the preset is for |
| `--target-lufs N` | profile | speech loudness target (Stream -18, Video -16, Record -24) |
| `--max-gain N` | profile | most gain (dB) voice2ee may add to your voice (Stream/Record 12, Video 16) |
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
| `--engine {quick,optimise}` | asked | (mode 1) how the first preset is fitted |
| `--goal NAME` | asked | Optimise goal: `balanced`, `keyboard`, `natural`, `broadcast`, `clarity` |
| `--trials N` | 300 / 200 | Optimise tries (real plugins / approximate model) |
| `--jobs N` | 75 % of your cores | CPU cores Optimise may use |
| `--finetune {keys,ab,both}` | asked | feedback keys, A/B picks first, or both |
| `--ab-render {auto,easyeffects}` | auto | A/B versions made instantly, or replayed through EasyEffects |
| `--offline {auto,builtin}` | auto | Optimise and A/B with your real plugins when they work, or always the built-in model |
| `--no-before-after` | | skip the before/after comparison after Optimise |
| `--no-taste` | | don't start from your saved taste |
| `--no-quality` | | don't use the quality model even if installed |
| `--check-full` | | show which Full-version parts are installed and work |

Speech recording: `--speech-test {hybrid,read,free}` (default `hybrid`), `--free-secs` (35), `--read-secs` (12),
`--speech-secs` (20, read only).

Fine-tuning options: `--box-cut`, `--makeup`, `--gate-offset`, `--no-vad`, `--vad-thres`, `--noise-secs`,
`--click-secs`, `--base`. See `voice2ee --help`.

## Files and locations

| Path | Contents |
|---|---|
| `~/.local/share/easyeffects/input/` | your presets (Flatpak: `~/.var/app/com.github.wwmm.easyeffects/data/easyeffects/input/`) |
| `~/.cache/voice2ee/take/` | the recording used by record-once mode |
| `~/.cache/voice2ee/state.json` | the session, for `--resume` |
| `~/.local/share/voice2ee/` | the installed tool (Full: also `venv/` and `models/`) |
| `~/.config/voice2ee/settings.json` | your settings (first screen, option 3) |
| `~/.config/voice2ee/taste.json` | your taste from A/B picks |
| `~/.config/voice2ee/overrides.toml` | your locks and ranges (optional) |

## Privacy and your recordings

voice2ee records your voice, so here is exactly what happens with it.

**Everything stays on your computer.** voice2ee has no telemetry, no analytics, no accounts and no uploads. While
it runs it makes no internet connections at all: the analysis, Optimise, A/B picks, the offline plugins and the
DNSMOS quality model all run locally. The only programs it talks to are on your own machine: PipeWire (to record
and play), and EasyEffects through its local control socket (to load presets and read their settings back).

**When it records.** Only during the steps it announces with a countdown: room tone, your speech (free talk and
the test sentences), keyboard/mouse noise and desk knocks, and the measuring rounds. It never listens in the
background, and nothing you type during a recording is kept or shown.

**What's saved, and where:**

| File | What it contains | Kept until |
|---|---|---|
| `~/.cache/voice2ee/take/speech.wav` | your raw speech recording (free talk + test sentences) | your next recording replaces it, or you delete it |
| `~/.cache/voice2ee/take/noise.wav`, `clicks.wav`, `taps.wav` | room tone, keyboard/mouse noise, desk knocks | as above |
| `~/.cache/voice2ee/take/meta.json` | where the free talk ends and the sentences begin (a number, no audio) | as above |
| `~/.cache/voice2ee/replay_tmp.wav` | the last piece of your recording that was replayed through EasyEffects | the next replay overwrites it |
| `~/.cache/voice2ee/ab_play.wav` | the last A/B clip that was played (about 11 s of your voice) | the next A/B pick overwrites it |
| `~/.cache/voice2ee/last.json` | measurements of your voice: levels, spectrum, timing (numbers, no audio) | the next analysis |
| `~/.cache/voice2ee/state.json` | the preset settings and device names, for `--resume` (no audio) | the next session |
| `~/.config/voice2ee/` | your settings, saved A/B taste and locks (no audio) | you delete them |
| EasyEffects preset folder | the presets voice2ee writes (settings only, no audio) | you delete them |

Recordings made in **live** rounds (talking again every round) are analysed in memory and never written to disk.
The processed audio recorded from EasyEffects in record-once rounds is also kept in memory only.

The free-talk part is whatever you say, so it's stored as you said it. Keep that in mind, and don't say anything
you wouldn't want saved in a file on your computer.

**Who can hear it.** During a record-once replay (about 30 s per round), a temporary microphone called
`voice2ee-replay-mic` is your system's default input. Any app listening to the default microphone at that moment,
for example OBS or Discord, hears your replayed recording. Your real microphone is restored right after. A/B
picks play through your normal speakers or headphones.

**Deleting it.** `./install.sh --uninstall` removes the tool but deliberately keeps your recording, settings and
presets. To delete your recordings and everything else voice2ee stored:

```bash
rm -rf ~/.cache/voice2ee      # recordings, temporary audio, measurements, session
rm -rf ~/.config/voice2ee     # settings, saved taste, locks
```

Your presets live in the EasyEffects preset folder (see [Files and locations](#files-and-locations)); delete the
ones you don't want from there or in EasyEffects.

**The installer** is the only part that uses the internet, and only for what you agree to: your distro's package
manager (its usual servers), pip for onnxruntime (PyPI) and the DNSMOS model (downloaded from Microsoft's
DNS-Challenge repository on GitHub) in the Full version. Those servers see a normal download request from your IP
address, as with any download. No recordings or measurements are ever sent.

**The test suite** uses only synthetic speech generated with ffmpeg's flite voices, never your recordings.

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
  fit.py             Quick/Optimise, goals, fine-tune choice, A/B renderers, Full-version status
  search.py          what Optimise may change, locks/ranges, the optimiser
  score.py           the scorecard, ESTOI, goals
  offline.py         offline chains: approximate (built-in) and exact (your LV2 plugins via lilv)
  simchain.py        the approximate plugin models
  taste.py           A/B picks, taste directions, preference model, saved taste
  quality.py         DNSMOS (optional, Full version)
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
- measurements reacting the right way: an echoey room lengthens word tails, a fast talker gets faster
  release times, a dull voice gets a low exciter start and a bright one starts above the "s", rumble steepens the
  high-pass
- versioning
- Optimise improving the score within the limits, locks and ranges respected everywhere, ESTOI, the scorecard
- A/B picks: a simulated listener's hidden taste is found, A/B through (fake) EasyEffects, the `a` key
- the exact LV2 host against a fake lilv (unit conversion, choices by label, atom ports, latency), the DNSMOS
  wrapper against a fake model, and the installer's Minimal/Full modes
- profiles, gate + expander order, tap catcher (knocks down ≥ 6 dB, voice bass unchanged), stereo fix, Deep noise
  remover detection, importing presets that use the new plugins
- quit words
- hidden keys (in a real pseudo-terminal)
- record-once end to end, against a fake EasyEffects server and fake PipeWire tools

## Known limitations

- **Offline, RNNoise and the Deep noise remover pass through** (they aren't LV2 plugins), so Optimise judges them
  in the confirmation round through EasyEffects.
- **The exact LV2 host is new:** the port names come from how EasyEffects binds its settings. `voice2ee
  --check-full` lists any setting a plugin on your system doesn't accept; please open an issue with those lines.
- **DNSMOS** was trained on general speech (calls and conferencing), not on streaming voices specifically. It's one
  part of the scorecard, not the judge.
- **EasyEffects can't host other plugins:** only its built-in effects (and DeepFilterNet through its own module).
- **Read-back:** the first load checks every number and switch of every plugin (about 150 settings); later rounds
  check the on/off states and the values the tuner changes. Text options (modes) aren't compared, because the
  server may report them as numbers.
- **Targets are heuristics:** the speech target curve, de-esser limits and gate placement are reasonable starting
  points, not universal truths. Your ears and the feedback keys have the final say.
- **Record-once mode** briefly changes the system default input.
- **Some package names are best-effort:** they're well established for Arch, Debian, Fedora and openSUSE, and less
  certain for Gentoo, Void and Solus. `install.sh --check` always tells you what's actually missing.
