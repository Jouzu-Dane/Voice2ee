"""Versioned preset names and the saved session state (for --resume)."""
import json
import re
from pathlib import Path

from .config import CACHE_DIR

VER_RE = re.compile(r"^(?P<stem>.+?)_v(?P<ver>\d+)(?:-(?P<run>\d+))?$")


def split_name(name):
    """'test_v1' -> ('test', 1);  'test_v1-3' -> ('test', 1);  'my-voice' -> ('my-voice', None)."""
    m = VER_RE.match(name)
    return (m["stem"], int(m["ver"])) if m else (name, None)


def _versions(out, stem):
    pat = re.compile(rf"^{re.escape(stem)}_v(\d+)(?:-(\d+))?$")
    vs, runs = set(), {}
    for f in (Path(out).glob("*.json") if Path(out).exists() else []):
        m = pat.match(f.stem)
        if m:
            v = int(m[1])
            vs.add(v)
            if m[2]:
                runs.setdefault(v, set()).add(int(m[2]))
    return vs, runs


def next_version(out, stem, at_least=0):
    vs, _ = _versions(out, stem)
    return max(vs | {at_least}) + 1


def last_run(out, stem, ver):
    _, runs = _versions(out, stem)
    return max(runs.get(ver, {0}))


def clean_name(text):
    return re.sub(r"[^A-Za-z0-9._ -]", "", text).strip().replace(" ", "-")


class Session:
    """Start: STEM_vN  ->  each run that changes something: STEM_vN-<run>  ->  finish: STEM_v<next free>."""

    def __init__(self, out, stem, ver, current):
        self.out, self.stem, self.ver, self.current = Path(out), stem, int(ver), current
        self.run = last_run(self.out, stem, self.ver)  # continue numbering after existing run files
        self.pending = None       # the previous run's file, waiting for "keep it?"
        self.kept, self.created, self.discard = [], [], []
        self.changed = False

    def run_name(self, k):
        return f"{self.stem}_v{self.ver}-{k}"

    def new_run(self):
        self.run += 1
        return self.run_name(self.run)

    def cleanup(self):
        """Delete run files you chose not to keep (only ones this tool created, never the loaded one)."""
        for n in list(self.discard):
            if n != self.current and n in self.created:
                try:
                    (self.out / f"{n}.json").unlink()
                except FileNotFoundError:
                    pass
                self.discard.remove(n)

    def to_dict(self):
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d):
        s = cls.__new__(cls)
        s.__dict__.update(d)
        s.out = Path(d["out"])
        return s


def save_state(P, sess, base_file=None, raw=None, mode=None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "state.json").write_text(json.dumps({"P": P, "sess": sess.to_dict(), "base_file": base_file,
                                                      "raw": raw, "test_mode": mode}))


def load_state():
    f = CACHE_DIR / "state.json"
    return json.loads(f.read_text()) if f.exists() else None
