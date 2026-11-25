"""Choosing how a preset is fitted (Quick / Optimise), fine-tuned (keys / A/B / both), and what the Full
version adds. Everything heavy is optional and detected at run time."""
import contextlib
import copy
import multiprocessing as mp
import os
import signal
import tempfile
import time
from pathlib import Path

from . import easyeffects as ee
from . import offline, quality, score, search, taste
import numpy as np

from .audio import ReplayError, VirtualMic, mono, replay_available
from .term import ask, bad, good, hidden_keys


# ------------------------------------------------------------------------------------------- what's available
def full_status(run_selftest=True):
    """{'exact': (ok, lines), 'quality': (ok, reason), 'deep': ...}"""
    exact = offline.self_test() if run_selftest else (False, ["not checked"])
    return {"exact": exact, "quality": quality.status(), "deep": ee.deep_available()}


def print_full_status(st):
    ok_e, lines = st["exact"]
    ok_q, why_q = st["quality"]
    print("\nFull version components:")
    print((good if ok_e else bad)(f"  {'✔' if ok_e else '✘'} exact offline plugins (lilv + your LSP/Calf LV2 plugins)"))
    for l in lines:
        print(f"      {l}")
    print((good if ok_q else bad)(f"  {'✔' if ok_q else '✘'} quality model (DNSMOS on onnxruntime): {why_q}"))
    d = st["deep"]
    print((good if d is True else bad)(f"  {'✔' if d is True else '?' if d == 'flatpak' else '✘'} DeepFilterNet "
                                       "(Deep noise remover)"))
    import os
    if not (ok_e and ok_q) and not os.environ.get("VOICE2EE_FROM_INSTALLER"):
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


# Worker processes: each builds its own chain (and quality model) once, then scores candidates. Only small,
# plain results travel back, so the parent stays responsive.
_W = {}
_KEEP = ("lufs", "peak_db", "click_hi30", "speech_lo30", "estoi", "dnsmos", "rejected", "lra", "true_peak")


def _slim(a):
    return {k: a[k] for k in _KEEP if k in a}


def _setup(spec):
    _W.clear()
    _W.update(spec)
    _W["chain"] = offline.LV2Chain() if spec["exact"] else offline.ApproxChain()
    _W["q"] = quality.load() if spec["quality"] else None


def _worker_init(spec):
    signal.signal(signal.SIGINT, signal.SIG_IGN)        # Ctrl+C is handled by the main process
    _setup(spec)


def _score(Pc):
    proc = offline.process_take(_W["chain"], _W["take"], preset_json(Pc, _W["base"]), short=_W["short"])
    s, parts, a = score.scorecard(proc, Pc, _W["goal"], quality=_W["q"], taste=_W["taste"])
    return s, parts, _slim(a)


def _shift_makeup(P, delta):
    """Change the leveler's makeup and let the stages after it follow (as the tuning rounds do)."""
    from .config import DEESS_FLOOR, clip
    P["comp"]["makeup"] = round(P["comp"]["makeup"] + delta, 1)
    if P["modules"].get("comp2"):
        P["comp2"]["thr"] = round(P["comp2"]["thr"] + delta, 1)
    for d in ("deess", "deess2"):
        P[d]["thr"] = round(clip(P[d]["thr"] + delta, DEESS_FLOOR, -3), 1)


def _loudness_polish(P, s, a, batch):
    """The search lands near the loudness target; a few direct makeup corrections finish the job (kept only
    when the whole score improves, so limits and the rest still win)."""
    from .config import clip
    tgt = P.get("target_lufs", -18.0)
    for _ in range(3):
        err = tgt - a["lufs"]
        if abs(err) < 0.4:
            break
        cands = []
        for f in (0.6, 0.9):
            c = copy.deepcopy(P)
            delta = clip(err * f, -c["comp"]["makeup"], c.get("max_gain", 12.0) - c["comp"]["makeup"])
            if abs(delta) < 0.1:
                continue
            _shift_makeup(c, delta)
            cands.append(c)
        if not cands:
            break
        res = batch(cands)
        k = min(range(len(res)), key=lambda i: res[i][0])
        if res[k][0] >= s:
            break
        P, (s, _, a) = cands[k], res[k]
    return P, s, a


def default_jobs():
    """75 % of your CPU cores (at least 1): fast, and leaves room for you and EasyEffects."""
    n = os.cpu_count() or 1
    return max(1, int(n * 0.75))


def run_optimise(P, take, args, st, base=None, out=print):
    exact = bool(st["exact"][0])
    chain_name = offline.LV2Chain.name if exact else offline.ApproxChain.name
    use_q = bool(st["quality"][0]) and not getattr(args, "no_quality", False)
    goal = choose_goal(args)
    trials = int(getattr(args, "trials", None) or (300 if exact else 200))
    tst = None if getattr(args, "no_taste", False) else taste.load_taste()[0]
    short = 10 if exact else 8
    jobs = int(getattr(args, "jobs", None) or default_jobs())
    spec = {"exact": exact, "quality": use_q, "take": {k: (v[: int((short + 1) * 48000)] if k == "speech" else v)
                                                        for k, v in take.items() if v is not None},
            "goal": goal, "taste": tst, "base": base, "short": short}
    pool, batch = None, None
    if jobs > 1 and "fork" in mp.get_all_start_methods():
        import warnings
        from concurrent.futures import ProcessPoolExecutor
        # fork: workers start instantly with everything already loaded (spawn/forkserver would re-import the
        # program). Newer Pythons warn about fork in a process with threads (numpy's); the workers only run
        # numpy/scipy code, which handles this, so the warning is silenced.
        warnings.filterwarnings("ignore", message=".*fork.*", category=DeprecationWarning)
        try:
            pool = ProcessPoolExecutor(max_workers=jobs, mp_context=mp.get_context("fork"),
                                       initializer=_worker_init, initargs=(spec,))
        except (OSError, ValueError):
            pool = None
    state = {"pool": pool}

    def batch(Ps):
        if state["pool"] is not None:
            try:
                return list(state["pool"].map(_score, Ps))
            except KeyboardInterrupt:
                raise
            except Exception:                            # a worker died: carry on in this process
                state["pool"].shutdown(wait=False, cancel_futures=True)
                state["pool"] = None
                out("\n  (parallel workers stopped - continuing on one core)")
        if not _W:
            _setup(spec)
        return [_score(p) for p in Ps]
    if pool is None:
        jobs = 1
    evaluate = _score
    chain = types_ns(name=chain_name, exact=exact, passthrough=offline.ApproxChain.passthrough)

    out(f"\nOptimising with the {chain.name} chain, goal '{goal}'"
        + (", quality model on" if use_q else "") + f": {trials} tries on your recording, "
        f"{jobs} CPU core{'s' if jobs > 1 else ''}...  (Ctrl+C = stop early, keep the best so far)")
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

    info = {"workers": jobs}
    try:
        with hidden_keys():
            best, s_best, a_best, n = search.optimise(P, evaluate, trials=trials, progress=progress,
                                                      seed=int(time.time()) % 1000, batch=batch, info=info)
        if not info.get("stopped") and best["modules"].get("comp"):
            best, s_best, a_best = _loudness_polish(best, s_best, a_best, batch)
    finally:
        if state["pool"] is not None:
            state["pool"].shutdown(wait=False, cancel_futures=True)
        _W.clear()
    out("")
    if info.get("stopped"):
        out(f"  stopped early after {n} tries - keeping the best so far")
    best.pop("_eqg", None)
    out(good(f"  ✔ done in {time.time() - t0:.0f} s. Best: {score.summary(a_best)}"))
    if a_best.get("rejected"):
        out(f"  !! even the best try breaks a limit ({a_best['rejected']}) - keeping the measured preset")
        return P
    for line in changes(P, best):
        out(f"   * {line}")
    if not chain.exact:
        out("  (approximate model: the first round through EasyEffects confirms it and corrects what differs)")
    return best


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


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
        self.vm = self.stack.enter_context(VirtualMic(reload=self._reload))
        return self

    def _reload(self):
        p = self.sess.out / f"{self.NAME}.json"
        if p.exists():
            ee.load_preset(self.NAME, p)

    def __call__(self, Pv):
        with hidden_keys():                    # nothing typed while recording ends up anywhere
            path = ee.write_preset(copy.deepcopy(Pv), self.NAME, self.sess.out, self.base)
            ee.load_preset(self.NAME, path)
            time.sleep(0.4)
            rec, _ = self.vm.play_record(self.clip, self.ee_src, meter=False)
        rec = mono(rec)
        if 20 * np.log10(np.sqrt(np.mean(np.square(rec, dtype=np.float64))) + 1e-12) < -75:
            raise ReplayError("EasyEffects returned silence. In EasyEffects, set the input device to "
                              "'voice2ee-replay-mic' (or turn on 'use default input'), or use the instant A/B "
                              "rendering (Settings: A/B versions made with = automatic)")
        return rec

    def __exit__(self, *exc):
        self.stack.close()
        with contextlib.suppress(OSError):
            (self.sess.out / f"{self.NAME}.json").unlink()
        ee.load_preset(self.sess.current, self.sess.out / f"{self.sess.current}.json")
        return False


def make_ab_runner(take, ee_src, sess, base=None, st=None, rounds=12, render_with="auto"):
    """Returns run(P) -> (taste, picks) or None if A/B can't run here.
    render_with: 'auto' = instant (your real plugins offline in the Full version, the built-in model otherwise),
                 'easyeffects' = replay every version through EasyEffects (exact on any version, ~20 s per pick)."""
    if take is None:
        return None
    clip = taste.listening_clip(take)
    st = st or {"exact": (False, [])}

    def run(P):
        if render_with == "easyeffects" and ee_src and not replay_available():
            ctx = _EERender(sess, ee_src, clip, base)
            how = "through EasyEffects - each pick takes ~20 s to prepare"
        elif st["exact"][0]:
            chain = offline.LV2Chain()
            ctx = contextlib.nullcontext(lambda Pv: chain.process(clip, preset_json(Pv, base)))
            how = "your real plugins, offline - instant"
        else:
            chain = offline.ApproxChain()
            ctx = contextlib.nullcontext(lambda Pv: chain.process(clip, preset_json(Pv, base)))
            how = ("the built-in model - instant. The differences between A and B are what matters; your pick is "
                   "applied to the real preset and checked through EasyEffects")
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
