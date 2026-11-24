"""Menus and the main flow."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import easyeffects as ee
from . import fit, taste
from .analysis import analyze_take
from .audio import (ReplayError, capture_live, list_sources, load_take, replay_available, replay_take, route_problem,
                    save_take, warn_route)
from .config import DEFAULT_MODULES, MAX_BANDS, PROFILES, TAKE_DIR, clip
from .loop import tune_loop
from .planner import finalize_mods, initial_params
from .session import Session, clean_name, load_state, next_version, save_state, split_name
from .term import Quit, ask, pick, restore_terminal_on_exit


def menu():
    print("""
voice2ee - streaming voice preset builder for EasyEffects

  1) New       - pick modules, record your RAW mic, analyze and write a new preset (name_v1), then keep
                 re-measuring through the EasyEffects mic and refining until you're happy
  2) Existing  - load one of your existing presets, test it as-is through the EasyEffects mic, and tune it
  3) Quit

  Versions: each tuning run that changes something is saved as name_v1-1, name_v1-2, ... (you choose which
  to keep); when you finish, the result becomes name_v2. q / quit / exit work at every prompt.
""")
    while True:
        s = ask("Choose [1/2/3]: ", quit_raises=False).lower()
        if s in ("3", "q"):
            raise Quit()
        if s in ("1", "2"):
            return s


def choose_profile(args):
    if args.profile:
        return args.profile
    print("""
What is this preset for?
  1) Stream  - streaming / voice chat: sounds finished live (loudness -18 LUFS, peaks under -6 dBFS,
               firm noise and click control)
  2) Record  - recording video in a lossless format (e.g. PCM 32-bit float) and editing later: natural
               dynamics, headroom (peaks under -6 dBFS, about -24 LUFS), gentle cleanup, no hard gate
""")
    while True:
        s = ask("Choose [1/2]: ")
        if s in ("", "1"):
            return "stream"
        if s == "2":
            return "record"


NOISE_CYCLE = ["both", "gate", "expander", None]
NOISE_LABEL = {"both": "GATE + EXPANDER", "gate": "GATE", "expander": "EXPANDER", None: "off"}
REMOVER_CYCLE = [("deep", False, True), ("rnnoise", True, False), ("both", True, True), ("off", False, False)]


def default_modules(profile):
    """The best default chain for a profile, adjusted to what's installed (DeepFilterNet)."""
    m = dict(DEFAULT_MODULES[profile])
    deep = ee.deep_available()
    if deep is True:                         # best click/tap removal available: use it instead of RNNoise
        m["deep"], m["rnnoise"] = True, False
    return m, deep


def choose_modules(args, profile="stream"):
    m, deep = default_modules(profile)
    if args.modules:
        toks = {t.strip().lower() for t in args.modules.split(",") if t.strip()}
        valid = {"gate", "expander", "rnnoise", "deep", "eq", "taps", "comp", "deess", "exciter", "limiter",
                 "stereo", "meter"}
        if toks - valid:
            sys.exit(f"Unknown module(s): {', '.join(sorted(toks - valid))}. Valid: {', '.join(sorted(valid))}")
        noise = ("both" if {"gate", "expander"} <= toks else "gate" if "gate" in toks
                 else "expander" if "expander" in toks else None)
        m = {"profile": profile, "noise": noise, "rnnoise": "rnnoise" in toks, "deep": "deep" in toks,
             "eq": "eq" in toks, "taps": "taps" in toks, "comp": "comp" in toks, "deess": "deess" in toks,
             "exciter": "exciter" in toks, "limiter": "limiter" in toks,
             "stereo": "auto" if "stereo" in toks else None, "meter": "meter" in toks}
        return finalize_mods(m)
    deep_note = {True: "installed", False: "NOT installed - see README", "flatpak": "Flatpak: usually bundled"}[deep]
    items = [("eq", "Equalizer (+ steep high-pass for pops)"),
             ("taps", "Desk-tap catcher (compresses only the thump band below ~150 Hz)"),
             ("comp", "Compressors (" + ("leveler + fast peak catcher" if profile == "stream" else
                                         "gentle leveler only - recordings keep their dynamics") + ")"),
             ("deess", "De-essers (wide + narrow 'snake S'; both always enabled)"),
             ("exciter", "Exciter (adds air/brightness - can emphasise sibilance and clicks)"),
             ("limiter", "Limiter (" + ("-6 dBFS ceiling" if profile == "stream" else "-1 dBFS safety only") + ")"),
             ("stereo", "Automatic mono fix (Stereo Tools, only added if your mic reaches one channel only)"),
             ("meter", "Level meter at the end (watch loudness live in EasyEffects)")]
    while True:
        print(f"\nWhat should the {profile} chain contain?  (defaults are the recommended setup - type numbers "
              "to toggle, Enter to continue, q to quit)\n")
        print(f"  1) Noise stage:   {NOISE_LABEL[m['noise']]:<16} (1 cycles: gate + expander / gate / expander / off)")
        rem = next(name for name, rn, dp in REMOVER_CYCLE if (rn, dp) == (bool(m["rnnoise"]), bool(m["deep"])))
        rem_label = {"deep": "DEEP NOISE REMOVER", "rnnoise": "RNNOISE", "both": "RNNOISE + DEEP", "off": "off"}[rem]
        print(f"  2) Noise remover: {rem_label:<16} (2 cycles: deep / RNNoise / both / off; DeepFilterNet: {deep_note})")
        for i, (k, label) in enumerate(items, 3):
            on = m[k] == "auto" if k == "stereo" else bool(m[k])
            print(f"  {i}) [{'x' if on else ' '}] {label}")
        s = ask("\nToggle: ")
        if not s:
            if m["deep"] and deep is False:
                print("  (Note: DeepFilterNet isn't installed, so EasyEffects will show the Deep noise remover as "
                      "unavailable until you install it.)")
            return finalize_mods(m)
        for tok in s.replace(",", " ").split():
            if tok == "1":
                m["noise"] = NOISE_CYCLE[(NOISE_CYCLE.index(m["noise"]) + 1) % len(NOISE_CYCLE)]
            elif tok == "2":
                idx = [n for n, _, _ in REMOVER_CYCLE].index(rem)
                _, m["rnnoise"], m["deep"] = REMOVER_CYCLE[(idx + 1) % len(REMOVER_CYCLE)]
                rem = REMOVER_CYCLE[(idx + 1) % len(REMOVER_CYCLE)][0]
            elif tok.isdigit() and 3 <= int(tok) < 3 + len(items):
                k = items[int(tok) - 3][0]
                if k == "stereo":
                    m[k] = None if m[k] == "auto" else "auto"
                else:
                    m[k] = not m[k]
            else:
                print("  (ignored something that isn't a number from the list)")


def choose_test_mode(args):
    missing = replay_available()
    if args.live or (missing and not args.replay):
        if missing and not args.live:
            print(f"\n(Record-once mode needs {', '.join(missing)} - using live mode.)")
        return "live"
    if args.replay:
        return "replay"
    print("""
How should each tuning round be tested?
  1) Record once (recommended) - you record yourself once; every round replays that recording through
     EasyEffects. No talking, and every round hears exactly the same speech, so changes are easy to judge.
     (During each replay, about 30 s, a temporary 'voice2ee-replay-mic' is the default input - apps using
     the default mic, like OBS, hear the replay. Your real mic is put back right after.)
  2) Live - you talk (and type) again every round
""")
    while True:
        s = ask("Choose [1/2]: ")
        if s in ("", "1"):
            return "replay"
        if s == "2":
            return "live"


def pick_preset(args, out):
    if args.preset:
        p = Path(args.preset).expanduser()
        if not p.exists():
            p = out / (args.preset if args.preset.endswith(".json") else args.preset + ".json")
        if not p.exists():
            sys.exit(f"Preset not found: {args.preset}")
        return p
    ps = sorted(out.glob("*.json")) if out.exists() else []
    if not ps:
        sys.exit(f"No presets found in {out}. Use --preset PATH, or --out DIR.")
    names = [p.stem for p in ps]
    latest = max(ps, key=lambda p: p.stat().st_mtime).stem
    return ps[names.index(pick(f"Select the existing preset to test (in {out})", names, default=latest))]


def make_measure(mode, take, ee_src, raw, args, sess):
    """Returns measure() for the tuning loop. Falls back to live mode if a replay fails."""
    state = {"mode": mode}

    def reload():
        ee.load_preset(sess.current, sess.out / f"{sess.current}.json")

    def measure():
        if state["mode"] == "replay":
            try:
                return analyze_take(replay_take(take, ee_src, reload=reload))
            except ReplayError as ex:
                print(f"!! Replay didn't work: {ex}\n   Switching to live mode.")
                state["mode"] = "live"
        return analyze_take(capture_live(ee_src, args, short=True, raw=raw))
    return measure


def ask_yes_no(prompt, default=True):
    while True:
        s = ask(prompt).lower()
        if not s:
            return default
        if s in ("y", "yes"):
            return True
        if s in ("n", "no"):
            return False


def record_raw_take(args, ee_src, choose_raw):
    """One recording of your UNPROCESSED voice. Returns (take, raw_device_or_None).
    With EasyEffects' local server: effects are bypassed for the recording and the EasyEffects mic is recorded,
    so there's no separate device to pick (and nothing PipeWire can route to the wrong place).
    Without it: the raw mic device is recorded, and the tool checks PipeWire really connected it there."""
    if ee_src and ee.server_path() is not None:
        print(f"\nRecording your RAW voice: EasyEffects' effects are switched off (bypassed) during this recording, "
              f"so '{ee_src}' carries your unprocessed mic. They come back on right after.")
        with ee.bypassed():
            take = capture_live(ee_src, args)
        return take, None
    raw = choose_raw()
    print(f"\nRecording your RAW voice from '{raw}'.")
    take = capture_live(raw, args)
    if route_problem(raw):
        warn_route(raw, route_problem(raw), "raw recording")
        if not ask_yes_no("Use this recording anyway? It may already be processed. [y/N]: ", default=False):
            raise Quit()
    return take, raw


def print_raw_analysis(a):
    print("\n--- Raw mic analysis ---")
    print(f"Noise floor {a['noise_floor']:.1f} dBFS | speech {a['lufs']:.1f} LUFS | peak {a['peak_db']:.1f} dBFS | "
          f"SNR {a['snr']:.0f} dB | f0 ~ {a['f0']:.0f} Hz")
    if a["clip_frac"] > 0.0005 or a["peak_db"] > -1.0:
        print("!! Your input is clipping. Lower the mic gain and re-run.")
    elif a["peak_db"] < -24:
        print("!! Input is very quiet (peaks below -24 dBFS). Raise the mic gain or move closer.")


def main():
    ap = argparse.ArgumentParser(description="Analyze your voice and build/tune EasyEffects input presets")
    ap.add_argument("--mode", choices=["1", "2"], help="1 = new preset, 2 = existing preset (skips the menu)")
    ap.add_argument("--preset", help="(mode 2) existing preset name or path")
    ap.add_argument("--modules", help="skip the checklist: comma list of gate|expander,rnnoise,eq,comp,deess,exciter,limiter")
    ap.add_argument("--name", help="(mode 1) name for the new preset (asked if not given)")
    ap.add_argument("--device", help="RAW mic source name (see --list-devices)")
    ap.add_argument("--ee-device", help="EasyEffects mic source name (default: easyeffects_source)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--resume", action="store_true", help="continue tuning the last session")
    tm = ap.add_mutually_exclusive_group()
    tm.add_argument("--replay", action="store_true", help="record once, replay it through EasyEffects every round")
    tm.add_argument("--live", action="store_true", help="talk live every round")
    ap.add_argument("--reuse", action="store_true", help="(mode 1) reuse the last raw recording instead of recording")
    ap.add_argument("--profile", choices=["stream", "record"], help="what the preset is for (asked if not given)")
    ap.add_argument("--target-lufs", type=float, default=None,
                    help="speech loudness target in LUFS (default: stream -18, record -24)")
    ap.add_argument("--noise-secs", type=float, default=4)
    ap.add_argument("--speech-secs", type=float, default=20)
    ap.add_argument("--click-secs", type=float, default=6)
    ap.add_argument("--bands", type=int, default=10, help=f"total EQ bands incl. high-pass, 6-{MAX_BANDS} (default 10)")
    ap.add_argument("--no-click-test", action="store_true")
    ap.add_argument("--no-tap-test", action="store_true", help="skip the desk-knock recording")
    ap.add_argument("--tap-secs", type=float, default=5)
    ap.add_argument("--box-cut", type=float, default=0.0)
    ap.add_argument("--deess", type=float, default=1.0, help="initial de-esser strength: 0.3=light, 1=default, 2=strong")
    ap.add_argument("--makeup", type=float, default=0.0)
    ap.add_argument("--max-gain", type=float, default=12.0, help="most gain (dB) the tool may add to your voice (default 12)")
    ap.add_argument("--gate-offset", type=float, default=0.0)
    ap.add_argument("--no-vad", action="store_true")
    ap.add_argument("--vad-thres", type=float, default=70.0)
    ap.add_argument("--base", help="(mode 1) existing exported preset JSON to merge the new values into")
    ap.add_argument("--out", help="preset directory (default: EasyEffects input preset folder)")
    g = ap.add_argument_group("fitting (steps 2 and 3)")
    g.add_argument("--engine", choices=["quick", "optimise"], help="(mode 1) quick prediction, or optimise on your "
                   "recording (asked if not given)")
    g.add_argument("--goal", choices=list(fit.score.GOALS), help="what the optimiser prioritises (asked if not given)")
    g.add_argument("--trials", type=int, help="optimiser tries (default 300 exact / 200 approximate)")
    g.add_argument("--finetune", choices=["keys", "ab", "both"], help="feedback keys, A/B picks, or both (asked)")
    g.add_argument("--no-taste", action="store_true", help="don't start from your saved A/B taste")
    g.add_argument("--no-quality", action="store_true", help="don't use the quality model even if installed")
    g.add_argument("--check-full", action="store_true", help="show which Full-version parts are installed and work")
    args = ap.parse_args()

    if args.check_full:
        fit.print_full_status(fit.full_status())
        return

    if args.list_devices:
        print("Source names (use as --device / --ee-device):\n")
        subprocess.run(["pactl", "list", "short", "sources"], check=False)
        return

    args.bands = int(clip(args.bands, 6, MAX_BANDS))
    args.deess = max(0.3, args.deess)
    out = Path(args.out).expanduser() if args.out else ee.preset_dir()
    sources = [s for s in list_sources() if not s.startswith("voice2ee_replay")]
    ee_names = [n for n in sources if n.startswith("easyeffects_source")]

    def choose_raw(default=None):
        if args.device:
            return args.device
        raw_c = [n for n in sources if not n.startswith("easyeffects")]
        return pick("Select your RAW microphone (the real mic, before EasyEffects).\n"
                    "Note: the tuning rounds always measure the EasyEffects mic - that's what is being tuned. "
                    "The raw mic is\nrecorded alongside it to measure how much gain EasyEffects adds.", raw_c,
                    default=default if default in raw_c else (raw_c[0] if raw_c else None))

    def choose_ee():
        if args.ee_device:
            return args.ee_device
        if not ee_names:
            print("\n(EasyEffects mic not found - EasyEffects must be running for 'easyeffects_source' to exist.)")
        return pick("Select the EasyEffects mic to keep tuning", ee_names + [n for n in sources if n not in ee_names],
                    default=ee_names[0] if ee_names else None)

    if ee.server_path() is None:
        print("(Note: EasyEffects' local server wasn't found. Start EasyEffects (8.0.9 or newer) so presets load "
              "without closing its window and the tool can read back what it applied.)")

    if args.resume:
        st = load_state()
        if not st:
            sys.exit("Nothing to resume yet.")
        if "sess" not in st or "modules" not in st.get("P", {}):
            sys.exit("Saved state is from an older version of this script - start a new session from the menu.")
        P, sess, bf = st["P"], Session.from_dict(st["sess"]), st.get("base_file")
        base = json.loads(Path(bf).read_text()) if bf else None
        P["max_gain"] = max(args.max_gain, P["comp"]["makeup"] if P["modules"]["comp"] else 0.0)
        print(f"Resuming with '{sess.current}'.")
        mode = "live" if args.live else ("replay" if (args.replay or st.get("test_mode") == "replay") else "live")
        raw = choose_raw(st.get("raw")) if (mode == "live" and st.get("raw")) else None
        take = load_take(TAKE_DIR) if mode == "replay" else None
        if mode == "replay" and take is None:
            print("(No saved recording to replay - using live mode.)")
            mode = "live"
        ee_src = choose_ee()
        ab = fit.make_ab_runner(take if take is not None else load_take(TAKE_DIR), ee_src, sess, base)
        tune_loop(P, sess, make_measure(mode, take, ee_src, raw, args, sess), base=base, base_file=bf, raw=raw,
                  test_mode=mode, ab=ab)
        return

    choice = args.mode or menu()

    if choice == "2":
        src = pick_preset(args, out)
        base = json.loads(src.read_text())
        profile = choose_profile(args)
        P, notes = import_preset_with_args(base, args, profile)
        stem, ver = split_name(src.stem)
        sess = Session(src.parent, stem, ver or 1, src.stem)
        print(f"\nLoaded: {src}")
        for n in notes:
            print(" *", n)
        print("\nYour original stays untouched.")
        ee_src = choose_ee()
        print("""
Record your raw (unprocessed) voice once?
  Yes (recommended): enables record-once rounds (your recording is replayed through EasyEffects, no talking
       each round) and exact gain safety (the tool measures how many dB EasyEffects adds to your voice).
  No:  every round is live (you talk each time). The gain cap then works from the compressor's makeup
       setting; the -6 dBFS peak ceiling and Threshold Boost checks still run.""")
        take, raw = None, None
        if ask_yes_no("Record your raw voice? [Y/n]: ", default=True):
            mode = choose_test_mode(args)
            if mode == "replay":
                take, _ = record_raw_take(args, ee_src, choose_raw)
                save_take(take, TAKE_DIR)
            else:
                raw = choose_raw()
        else:
            mode = "live"
        save_state(P, sess, str(src), raw, mode)
        ab, start_ab = None, False
        if take is not None:
            ab = fit.make_ab_runner(take, ee_src, sess, base, st=_exact_status(args))
            start_ab = fit.choose_finetune(args) in ("ab", "both")
        final = tune_loop(P, sess, make_measure(mode, take, ee_src, raw, args, sess), base=base,
                          base_file=str(src), raw=raw, test_mode=mode, ab=ab, start_ab=start_ab)
        if final:
            print("Resume later with:  ./voice2ee.py --resume")
        return

    # 1) new preset
    profile = choose_profile(args)
    args.mods = choose_modules(args, profile)
    if not args.mods.get("taps"):
        args.no_tap_test = True                    # no tap catcher -> no need to record knocks
    dflt = PROFILES[profile]["name"]
    stem = clean_name(args.name or "") or clean_name(ask(f"\nName for the new preset [{dflt}]: ") or dflt)
    stem = split_name(stem)[0] or dflt
    mode = choose_test_mode(args)
    ee_src = choose_ee() if (ee.server_path() is not None or mode == "replay") else None
    take = load_take(TAKE_DIR) if args.reuse else None
    raw = None
    if take is None:
        take, raw = record_raw_take(args, ee_src, choose_raw)
        save_take(take, TAKE_DIR)
    if mode == "live" and raw is None:
        raw = choose_raw()
    a = analyze_take(take)
    print_raw_analysis(a)

    P, notes = initial_params(a, args)
    if not args.no_taste:
        saved, picks = taste.load_taste()
        mine = {k: v for k, v in saved.items() if k in taste.dims_for(P)}
        if picks and any(abs(v) >= 0.05 for v in mine.values()):
            P = taste.apply_taste(P, mine)
            notes.append(f"Your saved taste (from {picks} A/B picks) applied: {taste.describe(mine)} "
                         "(--no-taste to skip)")
    for n in notes:
        print(" *", n)
    st = _exact_status(args)
    if fit.choose_engine(args, st) == "optimise":
        P = fit.run_optimise(P, take, args, st)
    ver = next_version(out, stem)
    name0 = f"{stem}_v{ver}"
    path = ee.write_preset(P, name0, out, args.base)
    sess = Session(out, stem, ver, name0)
    save_state(P, sess, raw=raw, mode=mode)
    print(f"\nSaved: {path}")
    print("\nNext, tuning measures the sound AFTER EasyEffects, so the preset gets loaded there.")
    if ee_src is None:
        try:
            ee_src = choose_ee()
        except Quit:
            print(f"\n'{name0}' is saved. Continue tuning later with:  ./voice2ee.py --resume")
            return
    start_ab = fit.choose_finetune(args) in ("ab", "both")
    ab = fit.make_ab_runner(take, ee_src, sess, args.base, st=st)
    tune_loop(P, sess, make_measure(mode, take, ee_src, raw, args, sess), base=args.base, raw=raw, test_mode=mode,
              ab=ab, start_ab=start_ab)
    print("Resume later with:  ./voice2ee.py --resume")


_STATUS = {}


def _exact_status(args):
    """Full-version check, once per run (the LV2 self-test takes a few seconds)."""
    if "st" not in _STATUS:
        _STATUS["st"] = fit.full_status(run_selftest=getattr(args, "engine", None) != "quick")
    return _STATUS["st"]


def import_preset_with_args(base, args, profile="stream"):
    P, notes = ee.import_preset(base)
    P["max_gain"] = max(args.max_gain, P["comp"]["makeup"] if P["modules"]["comp"] else 0.0)
    P["profile"] = profile
    P["modules"]["profile"] = profile
    P["target_lufs"] = float(args.target_lufs) if args.target_lufs is not None else PROFILES[profile]["target_lufs"]
    return P, notes


def run():
    restore_terminal_on_exit()
    try:
        main()
    except Quit:
        print("\nBye.")
    except KeyboardInterrupt:
        print("\nStopped. Run files made so far are kept; continue with:  ./voice2ee.py --resume")
