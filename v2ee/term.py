"""Terminal handling: prompts, quitting (q / quit / exit, any case) and hiding keys during recordings."""
import sys

QUIT_WORDS = ("q", "quit", "exit")


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
