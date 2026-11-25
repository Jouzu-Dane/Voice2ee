"""Tool settings: an interactive menu on the first screen, saved in ~/.config/voice2ee/settings.json.
Saved settings become the defaults; options typed on the command line still win for that run."""
import json
import os

from .config import CONFIG_DIR, MAX_BANDS
from .term import ask, color

ASK = "ask"
# key, label, kind, choices or (lo, hi), default, help
ITEMS = [
    ("profile", "Profile", "choice", [ASK, "stream", "video", "record"], ASK, "what new presets are for"),
    ("engine", "Fit the first preset", "choice", [ASK, "quick", "optimise"], ASK, "quick prediction or Optimise"),
    ("goal", "Optimise goal", "choice", [ASK, "balanced", "keyboard", "natural", "broadcast", "clarity"], ASK,
     "what Optimise prioritises"),
    ("trials", "Optimise tries", "number", (20, 2000), None, "empty = automatic: 300 real plugins / 200 built-in"),
    ("jobs", "CPU cores for Optimise", "number", (1, 256), None, "empty = 75 % of your cores"),
    ("finetune", "Fine-tune with", "choice", [ASK, "keys", "ab", "both"], ASK, "feedback keys, A/B picks or both"),
    ("offline", "Optimise/A-B processing", "choice", ["auto", "builtin"], "auto",
     "auto = your real plugins when they work, else the built-in model"),
    ("ab_render", "A/B versions made with", "choice", ["auto", "easyeffects"], "auto",
     "auto = instant; easyeffects = replay each version through EasyEffects (slower)"),
    ("test_mode", "Tuning rounds", "choice", [ASK, "replay", "live"], ASK, "record once and replay, or talk live"),
    ("speech_test", "Speech recording", "choice", ["hybrid", "read", "free"], "hybrid",
     "free talk + test sentences, read a passage, or free talk only"),
    ("free_secs", "Free-talk length (s)", "number", (10, 120), 35, ""),
    ("read_secs", "Test-sentence length (s)", "number", (5, 60), 12, ""),
    ("click_test", "Keyboard/mouse recording", "bool", None, True, "used to set the gate/expander"),
    ("tap_test", "Desk-knock recording", "bool", None, True, "used to set the tap catcher"),
    ("bands", "EQ bands", "number", (6, MAX_BANDS), 10, "including the high-pass"),
    ("max_gain", "Gain cap (dB)", "number", (3, 30), None, "empty = profile default (12, Video 16)"),
    ("target_lufs", "Loudness target (LUFS)", "number", (-30, -10), None, "empty = profile default"),
    ("use_taste", "Start from my saved A/B taste", "bool", None, True, ""),
    ("use_quality", "Use the quality model (Full)", "bool", None, True, ""),
    ("device", "Raw mic", "text", None, None, "empty = ask"),
    ("ee_device", "EasyEffects mic", "text", None, None, "empty = easyeffects_source / ask"),
    ("colors", "Colours", "choice", ["auto", "on", "off"], "auto", "green = working, red = problem"),
]
DEFAULTS = {k: d for k, _, _, _, d, _ in ITEMS}


def path():
    return CONFIG_DIR / "settings.json"


def load():
    s = dict(DEFAULTS)
    try:
        s.update({k: v for k, v in json.loads(path().read_text()).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s


def save(s):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps({k: v for k, v in s.items() if v != DEFAULTS[k]}, indent=2))


def apply(args, parser, s=None):
    """Fill in every option you didn't type on the command line from the saved settings."""
    s = s or load()
    os.environ.setdefault("VOICE2EE_COLORS", s["colors"])

    def unset(dest):
        return getattr(args, dest, None) == parser.get_default(dest)

    for key in ("profile", "engine", "goal", "finetune", "speech_test", "ab_render", "offline"):
        if unset(key) and s[key] not in (ASK, None):
            setattr(args, key, s[key])
    for key in ("trials", "jobs", "free_secs", "read_secs", "bands", "max_gain", "target_lufs", "device",
                "ee_device"):
        if unset(key) and s[key] not in (None, ""):
            setattr(args, key, s[key])
    if not args.replay and not args.live and s["test_mode"] != ASK:
        args.replay, args.live = s["test_mode"] == "replay", s["test_mode"] == "live"
    if unset("no_click_test") and not s["click_test"]:
        args.no_click_test = True
    if unset("no_tap_test") and not s["tap_test"]:
        args.no_tap_test = True
    if unset("no_taste") and not s["use_taste"]:
        args.no_taste = True
    if unset("no_quality") and not s["use_quality"]:
        args.no_quality = True
    return args


def _show(v, kind):
    if kind == "bool":
        return color("on", "green") if v else color("off", "yellow")
    if v in (None, ""):
        return "automatic" if kind == "number" else "ask"
    return "ask each time" if v == ASK else str(v)


def menu():
    """The settings screen. Returns when you press Enter (changes are saved as you make them)."""
    s = load()
    while True:
        print(f"\nSettings  (saved in {path()}; options on the command line still win)\n")
        for i, (k, label, kind, ch, d, hlp) in enumerate(ITEMS, 1):
            mark = "" if s[k] == d else color(" *", "cyan")
            print(f"  {i:2d}) {label:<32} {_show(s[k], kind)}{mark}" + (f"   ({hlp})" if hlp else ""))
        print("\n  number = change   r = reset everything to defaults   Enter = back to the menu")
        a = ask("> ", quit_raises=False).strip().lower()
        if a in ("", "q"):
            return s
        if a == "r":
            s = dict(DEFAULTS)
            save(s)
            print(color("  all settings reset to defaults", "green"))
            continue
        if not a.isdigit() or not 1 <= int(a) <= len(ITEMS):
            continue
        k, label, kind, ch, d, hlp = ITEMS[int(a) - 1]
        if kind == "bool":
            s[k] = not s[k]
        elif kind == "choice":
            print(f"\n  {label}:")
            for j, c in enumerate(ch, 1):
                print(f"    {j}) {'ask each time' if c == ASK else c}")
            b = ask("  choose (Enter = keep): ", quit_raises=False).strip()
            if b.isdigit() and 1 <= int(b) <= len(ch):
                s[k] = ch[int(b) - 1]
        elif kind == "number":
            lo, hi = ch
            b = ask(f"  {label} ({lo}-{hi}, empty = {'automatic' if d is None else d}): ", quit_raises=False).strip()
            if b == "":
                s[k] = d
            else:
                try:
                    v = float(b)
                    if lo <= v <= hi:
                        s[k] = int(v) if v.is_integer() and k not in ("max_gain", "target_lufs") else v
                    else:
                        print(color(f"  must be between {lo} and {hi}", "red"))
                except ValueError:
                    print(color("  not a number", "red"))
        else:
            b = ask(f"  {label} (source name, empty = ask): ", quit_raises=False).strip()
            s[k] = b or None
        save(s)
        os.environ["VOICE2EE_COLORS"] = s["colors"]
