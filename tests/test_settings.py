"""Settings menu (first screen), saved defaults vs command-line options, colours, CPU-core default."""
import builtins
import contextlib
import io
import os
import sys

from common import HERE
from v2ee import settings, term


def _parser_args(argv):
    import v2ee.cli as cli
    captured = {}
    real_parse = __import__("argparse").ArgumentParser.parse_args

    def grab(self, *a, **k):
        ns = real_parse(self, *a, **k)
        captured["ap"], captured["args"] = self, ns
        raise SystemExit(0)
    import argparse
    argparse.ArgumentParser.parse_args = grab
    old = sys.argv
    sys.argv = ["voice2ee"] + argv
    try:
        try:
            cli.main()
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real_parse
        sys.argv = old
    return captured["ap"], captured["args"]


def test_saved_settings_fill_in_but_command_line_wins():
    s = dict(settings.DEFAULTS, profile="video", engine="optimise", jobs=3, test_mode="replay",
             click_test=False, use_taste=False, bands=14, ee_device="my_ee_mic")
    ap, args = _parser_args([])
    settings.apply(args, ap, s)
    assert args.profile == "video" and args.engine == "optimise" and args.jobs == 3 and args.replay
    assert args.no_click_test and args.no_taste and args.bands == 14 and args.ee_device == "my_ee_mic"
    ap, args = _parser_args(["--profile", "record", "--live", "--jobs", "6"])
    settings.apply(args, ap, s)
    assert args.profile == "record" and args.live and not args.replay and args.jobs == 6


def test_settings_menu_changes_and_saves():
    idx = {k: str(i) for i, (k, *_) in enumerate(settings.ITEMS, 1)}
    answers = iter([idx["profile"], "3", idx["jobs"], "6", idx["click_test"], idx["colors"], "2", ""])
    builtins.input = lambda p="": next(answers)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        settings.menu()
    s = settings.load()
    assert s["profile"] == "video" and s["jobs"] == 6 and s["click_test"] is False and s["colors"] == "on"
    assert "Settings" in buf.getvalue() and "75 %" in buf.getvalue()
    answers = iter(["r", ""])
    builtins.input = lambda p="": next(answers)
    with contextlib.redirect_stdout(io.StringIO()):
        settings.menu()
    assert settings.load() == settings.DEFAULTS
    settings.path().unlink(missing_ok=True)
    os.environ.pop("VOICE2EE_COLORS", None)


def test_main_menu_has_settings():
    from v2ee.cli import menu
    for ans, want in (("1", "1"), ("2", "2"), ("3", "3")):
        it = iter([ans])
        builtins.input = lambda p="": next(it)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            assert menu() == want
    assert "3) Settings" in buf.getvalue() and "4) Quit" in buf.getvalue()


def test_colours():
    os.environ["VOICE2EE_COLORS"] = "on"
    try:
        assert term.good("ok") == "\033[32mok\033[0m" and term.bad("no") == "\033[31mno\033[0m"
        os.environ["NO_COLOR"] = "1"
        assert term.good("ok") == "ok"
    finally:
        os.environ.pop("NO_COLOR", None)
        os.environ["VOICE2EE_COLORS"] = "off"
        assert term.bad("no") == "no"
        os.environ.pop("VOICE2EE_COLORS", None)
    os.environ["VOICE2EE_COLORS"] = "on"
    term.install_tinted_print()
    try:
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            print("!! something broke")
            print("  auto:", "!! SAFETY: too loud")
            print("✓ all good")
            print("plain line")
    finally:
        os.environ.pop("VOICE2EE_COLORS", None)
    out = buf.getvalue().splitlines()
    assert out[0].startswith("\033[1;31m") and out[1].startswith("\033[1;31m") and out[2].startswith("\033[32m")
    assert out[3] == "plain line"


def test_default_jobs_is_75_percent():
    from v2ee import fit
    real = os.cpu_count
    try:
        for n, want in ((1, 1), (2, 1), (4, 3), (8, 6), (16, 12), (32, 24)):
            os.cpu_count = lambda n=n: n
            assert fit.default_jobs() == want, (n, fit.default_jobs())
    finally:
        os.cpu_count = real
