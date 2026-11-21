"""Quit words at every prompt, and keys typed during a recording never shown or passed on (real pty)."""
import builtins
import contextlib
import io
import os
import pty
import select
import sys
import time
import types
from pathlib import Path

from common import HERE
from v2ee import term
from v2ee.cli import choose_modules, menu

WORDS = ["q", "Q", "quit", "QUIT", "Quit", "exit", "EXIT", "eXiT", " exit "]


def _try(fn, word):
    builtins.input = lambda p="": word
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return fn()
    except term.Quit:
        return "QUIT"


def test_quit_everywhere():
    for w in WORDS + ["3"]:
        assert _try(menu, w) == "QUIT", w
    for w in WORDS:
        assert _try(lambda: term.pick("x", ["a", "b"], "a"), w) == "QUIT", w
        assert _try(lambda: choose_modules(types.SimpleNamespace(modules=None)), w) == "QUIT", w
        assert _try(lambda: term.ask("> ", quit_raises=False), w) == "q", w
    for w in ["qq", "quiet", "u", "exits"]:
        assert _try(lambda: term.ask("> ", quit_raises=False), w) == w


CHILD = """
import sys, time
sys.path.insert(0, %r)
from v2ee import term
print("RECORDING", flush=True)
with term.hidden_keys():
    time.sleep(1.5)
print("GOT", repr(term.ask("PROMPT> ", quit_raises=False)), flush=True)
""" % str(HERE.parent)


def test_keys_hidden_during_recording():
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.executable, [sys.executable, "-c", CHILD])
    out, t0, typed, sent = b"", time.time(), False, False
    while time.time() - t0 < 10:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 1024)
            except OSError:
                break
            if not d:
                break
            out += d
        if b"RECORDING" in out and not typed:
            time.sleep(0.3)
            os.write(fd, b"secret keys asdf\r")
            typed = True
        if b"PROMPT>" in out and not sent:
            time.sleep(0.2)
            os.write(fd, b"b c\r")
            sent = True
        if b"GOT" in out and out.endswith(b"\n"):
            break
    os.waitpid(pid, 0)
    txt = out.decode(errors="replace")
    assert "secret" not in txt.split("GOT")[0], "keys were shown"
    assert "GOT 'b c'" in txt, txt
