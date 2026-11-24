"""The tuning rounds: measure -> auto-adjust -> your feedback -> save a versioned run -> repeat."""
import json

import numpy as np

from . import easyeffects as ee
from .config import SAFETY_MARGIN, prof
from .session import Session, next_version, save_state
from .term import ask
from .tuning import FEEDBACK_HELP, auto_adjust, manual_feedback, parse_feedback, region_summary, target_lufs


RECORD_TIPS = """
Recording tips (record profile):
  * OBS: Settings > Output > Output Mode 'Advanced' > Recording: format MKV (or MOV), audio encoder PCM 32-bit
    float (the exact name varies a little by OBS version). It stores exactly what EasyEffects outputs and can't
    clip after this point - but it can't undo clipping at the mic itself, so keep the mic's own gain sensible.
  * Add your RAW mic as a second audio track (Advanced Audio Properties > Tracks) as a safety copy.
  * In DaVinci Resolve: normalise loudness at the end (YouTube plays back around -14 LUFS).
  * This preset keeps headroom (peaks under -6 dBFS, safety limiter at -1) and natural dynamics on purpose."""


def _snapshot(P):
    return json.dumps({k: v for k, v in P.items() if not k.startswith("last_")}, sort_keys=True, default=str)


def apply(sess, name, first=False):
    """Load preset `name` into EasyEffects and check what it actually applied."""
    path = sess.out / f"{name}.json"
    how = ee.load_preset(name, path)
    if how is None:
        ask(f"Couldn't reach EasyEffects. Load preset '{name}' in its Input tab, then press Enter (q = quit)... ")
        return
    try:
        res = ee.verify_loaded(json.loads(path.read_text()), full=first)
    except (OSError, ValueError):
        res = None
    if res is None:
        return
    if res["mismatch"]:
        print("!! EasyEffects reports different values than the preset:")
        for line in res["mismatch"][:8]:
            print("   ", line)
    elif first and res["checked"]:
        print(f"(EasyEffects confirmed {res['checked']} settings, incl. every plugin's on/off state)")


def _write_and_load(P, sess, name, base):
    path = ee.write_preset(P, name, sess.out, base)
    if name not in sess.created:
        sess.created.append(name)
    sess.current, sess.changed = name, True
    apply(sess, name)
    sess.cleanup()
    return path


def _ask_keep(sess):
    """Ask about the previous run's file. Returns False if you chose to finish (q / quit / exit)."""
    name = sess.pending
    s = ask(f"\nKeep the previous run as its own preset '{name}'? [y/N, q = finish] ", quit_raises=False)
    sess.pending = None
    if s.lower() in ("y", "yes"):
        sess.kept.append(name)
        print(f"  kept {name}")
    else:
        sess.discard.append(name)
        sess.cleanup()
    return s != "q"


def report(P, a):
    tgt = target_lufs(P)
    clickinfo = (f" | clicks {a['click_hi30'] - a['speech_lo30']:+.0f} dB vs quiet speech"
                 if a.get("click_hi30") is not None else "")
    print(f"\nloudness {a['lufs']:.1f} LUFS (target {tgt:.0f}) | speech peaks {a['pkp10']:.0f}..{a['pkp95']:.0f} dBFS, "
          f"max {a['peak_db']:.1f} (peaks aim under {prof(P, 'peak_hi'):.0f}, limiter {P['limiter']:.0f}) | SNR {a['snr']:.0f} dB | sibilance {a['sib_diff']:+.1f} dB"
          f"{clickinfo}")
    if a.get("applied_gain") is not None:
        warn = "  <-- too much!" if a["applied_gain"] > P.get("max_gain", 12.0) + SAFETY_MARGIN else ""
        print(f"EasyEffects adds {a['applied_gain']:+.1f} dB to your voice on average "
              f"(most-lifted syllables {a['applied_gain_hi']:+.1f}; allowed {P.get('max_gain', 12.0):+.0f}){warn}")
    if P["modules"]["eq"] and P["eq"]["bands"]:
        print(f"vs target (dB): {region_summary(P, a)}   [{len(P['eq']['bands']) + 1} EQ bands]")


def tune_loop(P, sess, measure, base=None, base_file=None, raw=None, test_mode="live"):
    """measure(): returns an analysis of the CURRENT EasyEffects output (live talking or replayed recording)."""
    print("\nHeads-up: turn your headphone/speaker volume down a notch before the preset loads.")
    if P["modules"]["comp"] and not P["modules"]["limiter"]:
        print("!! This chain has compressors but no limiter, so sudden loud peaks are not capped.")
    print(f"\nTesting '{sess.current}' first. Runs that change something are saved as "
          f"{sess.run_name(sess.run + 1)}, -{sess.run + 2}, ...; when you finish, the result becomes "
          f"{sess.stem}_v{next_version(sess.out, sess.stem, sess.ver)}.")
    apply(sess, sess.current, first=True)
    if test_mode == "live" and not raw:
        print("(No raw mic: the gain EasyEffects adds isn't measured. The makeup cap, the limiter ceiling and "
              "the Threshold Boost check still apply.)")
    rnd, prev_sig, prev_changed, prev_mk, warned_mono = 0, None, False, None, False
    replay = test_mode == "replay"
    while True:
        rnd += 1
        if sess.pending and not _ask_keep(sess):
            break
        this_run = None
        print(f"\n===== Round {rnd}: measuring '{sess.current}' ({'replaying your recording' if replay else 'live'}) =====")
        try:
            a = measure()
        except SystemExit as ex:
            print(f"!! {ex}")
            a = None
        if a is not None:
            report(P, a)
            if a.get("imbalance", 0) > 10 and not warned_mono and P["modules"].get("stereo") != "auto":
                warned_mono = True
                print("!! Your mic comes out on one channel only of the EasyEffects mic (a mono mic seen as left-only), "
                      "so OBS may hear it on one side and quieter. Fix: add EasyEffects' 'Stereo Tools' at the top of "
                      "the chain with Stereo Matrix 'LR > LL (Mono left channel)'.")
            sig = np.append(a["diff"], a["speech_db"])
            mk = P["comp"]["makeup"] if P["modules"]["comp"] else 0.0
            stuck = False
            if prev_changed and prev_sig is not None:
                delta = float(np.max(np.abs(sig - prev_sig)))
                # replay: same input every round, so any real change shows up. live: only judge big gain changes
                stuck = delta < 0.02 if replay else (prev_mk is not None and abs(mk - prev_mk) >= 1.0 and
                                                     abs(sig[-1] - prev_sig[-1]) < 0.3)
            before = _snapshot(P)
            if stuck:
                print("!! Nothing changed since the last adjustment. The preset probably isn't loaded in "
                      "EasyEffects,\n   or this isn't the EasyEffects mic. Load it in the Input tab and check the "
                      "source name.\n   Skipping auto-adjust this round so it doesn't drift.")
                msgs = []
            else:
                msgs = auto_adjust(P, a)
            for mm in msgs:
                print("  auto:", mm)
            if not msgs and not stuck:
                print("  auto: nothing to change - it matches the target.")
            prev_sig, prev_mk = sig, mk
            prev_changed = _snapshot(P) != before or stuck
            if _snapshot(P) != before:
                this_run = sess.new_run()
                _write_and_load(P, sess, this_run, base)
        print("\n" + FEEDBACK_HELP)
        fb = ask("> ", quit_raises=False)
        if fb != "q":
            toks = parse_feedback(fb)
            if toks:
                before = _snapshot(P)
                for mm in manual_feedback(P, toks):
                    print("  you:", mm)
                if _snapshot(P) != before:
                    prev_changed = True
                    this_run = this_run or sess.new_run()
                    _write_and_load(P, sess, this_run, base)
        if this_run:
            print(f"  -> now loaded: {this_run}")
            sess.pending = this_run
        save_state(P, sess, base_file, raw, test_mode)
        if fb == "q":
            break
    return finish(P, sess, base, base_file, raw, test_mode)


def finish(P, sess, base, base_file=None, raw=None, test_mode="live"):
    if sess.pending:
        print("(the last run has the same settings as the final version)")
        _ask_keep(sess)
    if not sess.changed:
        sess.cleanup()
        print(f"\nNo changes were made, so nothing new was saved. '{sess.current}' is still loaded.")
        save_state(P, sess, base_file, raw, test_mode)
        return None
    ver = next_version(sess.out, sess.stem, sess.ver)
    name = f"{sess.stem}_v{ver}"
    path = ee.write_preset(P, name, sess.out, base)
    sess.current = name
    apply(sess, name)
    sess.cleanup()
    print(f"\nSaved and loaded: {name}  ({path})")
    if P.get("profile") == "record":
        print(RECORD_TIPS)
    if sess.kept:
        print("Runs you kept: " + ", ".join(sess.kept))
    save_state(P, Session(sess.out, sess.stem, ver, name), base_file, raw, test_mode)  # --resume continues here
    return path
