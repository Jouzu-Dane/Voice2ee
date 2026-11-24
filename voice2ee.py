#!/usr/bin/env python3
"""voice2ee - analyze your voice and build / tune EasyEffects input presets for streaming.

Needs:  Python 3.9+, numpy, scipy, PipeWire tools (see ./install.sh --check). The Full version adds lilv +
        LV2 plugins, onnxruntime and the DNSMOS model (./install.sh --full).

Run:    ./voice2ee.py            (menu)
        ./voice2ee.py --resume   (continue the last tuning session)
        ./voice2ee.py --help     (all options)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Full version extras (onnxruntime) live in voice2ee's own virtualenv; make them visible when running from a
# source checkout with the system Python too.
_venv = Path.home() / ".local/share/voice2ee/venv/lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
if (_venv / "site-packages").is_dir() and str(_venv / "site-packages") not in sys.path:
    sys.path.append(str(_venv / "site-packages"))

from v2ee.cli import run  # noqa: E402

if __name__ == "__main__":
    run()
