"""Terminal handling: prompts, quitting (q / quit / exit, any case) and hiding keys during recordings."""
import sys

QUIT_WORDS = ("q", "quit", "exit")
_CODES = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "bold": "1",
          "alert": "1;33", "problem": "1;31"}
WARN_HEADS = ("Heads-up", "Note:", "(Note:", "Warning", "Tip:")


def colors_on():
    """Colours: setting 'auto' (only in a terminal), 'on' or 'off'; NO_COLOR always turns them off."""
    import os
    if os.environ.get("NO_COLOR"):
        return False
    mode = os.environ.get("VOICE2EE_COLORS", "auto")
    if mode == "off":
        return False
    if mode == "on":
        return True
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def color(text, name):
    return f"\033[{_CODES[name]}m{text}\033[0m" if colors_on() else text


def good(text):
    """A confirmation that something works (green)."""
    return color(text, "green")


def bad(text):
    """A failure or a problem (red)."""
    return color(text, "red")


def warn(text):
    return color(text, "yellow")


def install_tinted_print():
    """Every line the tool prints is coloured by how it starts (only when colours are on):
    '!!' / '✗' problems bold red, 'Heads-up' / 'Note:' warnings bold yellow, '✓' confirmations green."""
    import builtins
    if getattr(builtins.print, "_v2ee", False):
        return
    plain = builtins.print

    def tinted(*a, sep=" ", end="\n", file=None, flush=False):
        if (file is None or file is sys.stdout) and a and colors_on():
            text = sep.join(str(x) for x in a)
            body = text.lstrip("\r\n ")
            head = body.split(":", 1)[1].lstrip() if body.startswith(("auto:", "you:")) else body
            lead = text[: len(text) - len(text.lstrip("\r\n"))]   # keep leading newlines outside the colour
            rest = text[len(lead):]
            if head.startswith(("!!", "✗")):
                text = lead + color(rest, "problem")   # a problem: bold red
            elif head.startswith(WARN_HEADS):
                text = lead + color(rest, "alert")     # a warning / heads-up: bold yellow
            elif head.startswith("✓"):
                text = lead + good(rest)
            return plain(text, end=end, file=file, flush=flush)
        return plain(*a, sep=sep, end=end, file=file, flush=flush)
    tinted._v2ee = True
    builtins.print = tinted


def tint(line):
    """Colour a status line by its content: '!!' problems red, confirmations green."""
    s = line.lstrip()
    if s.startswith("!!") or s.startswith("✗"):
        return color(line, "problem")
    if s.startswith(WARN_HEADS):
        return color(line, "alert")
    if s.startswith("✓"):
        return good(line)
    return line


class Quit(Exception):
    """Raised when you type q / quit / exit at a prompt."""


def is_quit(text):
    return text.strip().lower() in QUIT_WORDS


def _tty():
    try:
        return sys.stdin.fileno() if sys.stdin.isatty() else None
    except (AttributeError, ValueError, OSError):
        return None


def flush_typeahead():
    """Throw away anything typed before a prompt appears (e.g. keys pressed during a recording)."""
    fd = _tty()
    if fd is not None:
        try:
            import termios
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:
            pass


class hidden_keys:
    """While recording: nothing you type is shown, and all of it is discarded afterwards,
    so keyboard noise you make for the click test never reaches a prompt or gets printed."""

    def __enter__(self):
        self.fd, self.old = _tty(), None
        if self.fd is not None:
            try:
                import termios
                self.old = termios.tcgetattr(self.fd)
                new = termios.tcgetattr(self.fd)
                new[3] &= ~(termios.ECHO | termios.ICANON | termios.ECHONL)
                termios.tcsetattr(self.fd, termios.TCSANOW, new)
            except Exception:
                self.old = None
        return self

    def __exit__(self, *exc):
        if self.old is not None:
            try:
                import termios
                termios.tcflush(self.fd, termios.TCIFLUSH)
                termios.tcsetattr(self.fd, termios.TCSANOW, self.old)
            except Exception:
                pass
        return False


def ask(prompt, quit_raises=True):
    """Prompt for input. q / quit / exit (any case) raise Quit, or return "q" when quit_raises=False."""
    flush_typeahead()
    try:
        text = input(prompt).strip()
    except EOFError:
        text = "q"
    if is_quit(text):
        if quit_raises:
            raise Quit()
        return "q"
    return text


def pick(title, names, default=None):
    """Numbered list picker. Enter = default, q / quit / exit = Quit."""
    if not names:
        return ask(f"{title}\nType a name (blank = system default, q = quit): ") or ""
    print("\n" + title)
    for i, n in enumerate(names, 1):
        print(f"  {i}) {n}")
    print("  q) quit")
    while True:
        d = f" [{names.index(default) + 1}]" if default in names else ""
        s = ask(f"Choice{d}: ")
        if not s and default in names:
            return default
        if s.isdigit() and 1 <= int(s) <= len(names):
            return names[int(s) - 1]
        if s in names:
            return s
        print("Not a valid choice.")


def restore_terminal_on_exit():
    fd = _tty()
    if fd is None:
        return
    try:
        import atexit
        import termios
        orig = termios.tcgetattr(fd)
        atexit.register(lambda: termios.tcsetattr(fd, termios.TCSANOW, orig))
    except Exception:
        pass
