"""Choosing how a preset is fitted (Quick / Optimise), fine-tuned (keys / A/B / both), and what the Full
version adds. Everything heavy is optional and detected at run time."""
import contextlib
import copy
import tempfile
import time
from pathlib import Path

from . import easyeffects as ee
from . import offline, quality, score, search, taste
from .audio import ReplayError, VirtualMic, mono, replay_available
from .term import ask, hidden_keys


# ------------------------------------------------------------------------------------------- what's available
def full_status(run_selftest=True):
    """{'exact': (ok, lines), 'quality': (ok, reason), 'deep': ...}"""
    exact = offline.self_test() if run_selftest else (False, ["not checked"])
    return {"exact": exact, "quality": quality.status(), "deep": ee.deep_available()}


def print_full_status(st):
    ok_e, lines = st["exact"]
    ok_q, why_q = st["quality"]
    print("\nFull version components:")
    print(f"  {'✔' if ok_e else '✘'} exact offline plugins (lilv + your LSP/Calf LV2 plugins)")
    for l in lines:
        print(f"      {l}")
    print(f"  {'✔' if ok_q else '✘'} quality model (DNSMOS on onnxruntime): {why_q}")
    d = st["deep"]
    print(f"  {'✔' if d is True else '?' if d == 'flatpak' else '✘'} DeepFilterNet (Deep noise remover)")
    if not (ok_e and ok_q):
        print("  Get the missing parts with:  ./install.sh --full   (see README: 'Minimal or Full')")


# ------------------------------------------------------------------------------------------- choices
def choose_engine(args, st):
    if getattr(args, "engine", None):
        return args.engine
    ok_e = st["exact"][0]
    print(f"""
How should the first preset be fitted?
  1) Quick     - measure and predict (a few seconds), then refine in rounds
  2) Optimise  - try a few hundred variations on your recording and keep the best, then refine in rounds
                 ({'EXACT: runs your real EasyEffects plugins' if ok_e else
                   'approximate built-in model, confirmed through EasyEffects; the Full version makes it exact'})
""")
    while True:
        s = ask("Choose [1/2]: ")
        if s in ("", "1"):
            return "quick"
        if s == "2":
            return "optimise"


def choose_goal(args):
    if getattr(args, "goal", None):
        return args.goal
    keys = list(score.GOALS)
    print("\nWhat matters most?")
    for i, k in enumerate(keys, 1):
        print(f"  {i}) {score.GOALS[k]['label']}")
    while True:
        s = ask(f"Choose [1-{len(keys)}, Enter = 1]: ")
        if s == "":
            return keys[0]
        if s.isdigit() and 1 <= int(s) <= len(keys):
            return keys[int(s) - 1]


def choose_finetune(args):
    if getattr(args, "finetune", None):
        return args.finetune
    print("""
How do you want to fine-tune?
  1) Feedback keys - describe what you hear (boxy, harsh, clicks, ...) each round
  2) A/B picks     - pick the better of two versions, about 12 picks; learns your taste and saves it
  3) Both          - A/B picks first, then rounds with feedback keys (you can press 'a' for more picks anytime)
""")
    while True:
        s = ask("Choose [1/2/3, Enter = 1]: ")
        if s in ("", "1"):
            return "keys"
        if s == "2":
            return "ab"
        if s == "3":
            return "both"


# ------------------------------------------------------------------------------------------- optimise
def preset_json(P, base=None):
    """The preset EasyEffects would get (works for new and imported presets)."""
    if not P.get("imported"):
        return ee.render(P)
    with tempfile.TemporaryDirectory() as d:
        import json
        return json.loads(Path(ee.write_preset(copy.deepcopy(P), "tmp", Path(d), base)).read_text())


def run_optimise(P, take, args, st, base=None, out=print):
    chain = offline.LV2Chain() if st["exact"][0] else offline.ApproxChain()
    q = quality.load() if st["quality"][0] and not getattr(args, "no_quality", False) else None
    goal = choose_goal(args)
    trials = int(getattr(args, "trials", None) or (300 if chain.exact else 200))
    tst = None if getattr(args, "no_taste", False) else taste.load_taste()[0]
    short = 10 if chain.exact else 8

    def evaluate(Pc):
        proc = offline.process_take(chain, take, preset_json(Pc, base), short=short)
        return score.scorecard(proc, Pc, goal, quality=q, taste=tst)

    out(f"\nOptimising with the {chain.name} chain, goal '{goal}'"
        + (", quality model on" if q else "") + f": {trials} tries on your recording...")
    passed = [k for k in chain.passthrough if P["modules"].get({"rnnoise": "rnnoise", "deepfilternet": "deep"}
                                                                 .get(k, "-"))]
    if passed:
        out(f"  (not simulated offline, so judged in the confirmation round: {', '.join(passed)})")
    t0 = time.time()
    last = {"t": 0.0}

    def progress(n, total, best, start, a):
        if time.time() - last["t"] > 1.0 or n == total:
            last["t"] = time.time()
            out(f"\r  {n}/{total}  score {best:.2f} (start {start:.2f})  {score.summary(a)}      ", end="")

    with hidden_keys():
        best, s_best, a_best, n = search.optimise(P, evaluate, trials=trials, progress=progress,
                                                  seed=int(time.time()) % 1000)
    out("")
    best.pop("_eqg", None)
    out(f"  done in {time.time() - t0:.0f} s. Best: {score.summary(a_best)}")
    if a_best.get("rejected"):
        out(f"  !! even the best try breaks a limit ({a_best['rejected']}) - keeping the measured preset")
        return P
    for line in changes(P, best):
        out(f"   * {line}")
    if not chain.exact:
        out("  (approximate model: the first round through EasyEffects confirms it and corrects what differs)")
    return best


def changes(P0, P1):
    out = []
    for path, _, _ in search.space(P0):
        if path.startswith("eqg."):
            continue
        try:
            a, b = search.get(P0, path), search.get(P1, path)
        except (KeyError, TypeError):
            continue
        if abs(float(b) - float(a)) >= 0.05 * max(1.0, abs(float(a))):
            out.append(f"{path}: {a:g} -> {b:g}")
    if P0["modules"].get("eq"):
        moved = [f"{b1['f']:.0f} Hz {b0['g']:+.1f}->{b1['g']:+.1f}" for b0, b1 in zip(P0["eq"]["bands"], P1["eq"]["bands"])
                 if abs(b1["g"] - b0["g"]) >= 0.5]
        if moved:
            out.append("EQ: " + ", ".join(moved[:6]))
    return out


# ------------------------------------------------------------------------------------------- A/B
class _EERender:
    """Renders a variant through the real EasyEffects: writes a temporary preset, loads it, replays the clip
    through the temporary replay mic and records the EasyEffects mic."""
    NAME = "voice2ee-ab"

    def __init__(self, sess, ee_src, clip, base):
        self.sess, self.ee_src, self.clip, self.base = sess, ee_src, clip, base
        self.stack = contextlib.ExitStack()

    def __enter__(self):
        self.vm = self.stack.enter_context(VirtualMic())
        return self

    def __call__(self, Pv):
        with hidden_keys():                    # nothing typed while recording ends up anywhere
            path = ee.write_preset(copy.deepcopy(Pv), self.NAME, self.sess.out, self.base)
            ee.load_preset(self.NAME, path)
            time.sleep(0.4)
            rec, _ = self.vm.play_record(self.clip, self.ee_src)
        return mono(rec)

    def __exit__(self, *exc):
        self.stack.close()
        with contextlib.suppress(OSError):
            (self.sess.out / f"{self.NAME}.json").unlink()
        ee.load_preset(self.sess.current, self.sess.out / f"{self.sess.current}.json")
        return False


def make_ab_runner(take, ee_src, sess, base=None, st=None, rounds=12):
    """Returns run(P) -> (taste, picks) or None if A/B can't run here."""
    if take is None:
        return None
    clip = taste.listening_clip(take)
    st = st or {"exact": (False, [])}

    def run(P):
        if st["exact"][0]:
            chain = offline.LV2Chain()
            render = (lambda Pv: chain.process(clip, preset_json(Pv, base)))
            ctx = contextlib.nullcontext(render)
            how = "your real plugins, offline - instant"
        elif ee_src and not replay_available():
            ctx = _EERender(sess, ee_src, clip, base)
            how = "through EasyEffects - each pick takes ~20 s to prepare"
        else:
            chain = offline.ApproxChain()
            ctx = contextlib.nullcontext(lambda Pv: chain.process(clip, preset_json(Pv, base)))
            how = "approximate built-in model (EasyEffects replay unavailable) - close, not exact"
        print(f"(A/B versions are made with: {how})")
        try:
            with ctx as render:
                t, picks, _ = taste.ab_session(P, render, lambda p: ask(p), rounds=rounds)
        except (ReplayError, RuntimeError) as ex:
            print(f"!! A/B didn't work: {ex}")
            return {}, 0
        if picks:
            taste.save_taste(t, picks)
            print(f"  saved to {taste.taste_file()} - new presets start from it (--no-taste to ignore)")
        return t, picks
    return run
