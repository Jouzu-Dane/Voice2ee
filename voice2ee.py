#!/usr/bin/env python3
"""voice2ee - analyze your voice and build / tune EasyEffects input presets for streaming.

Needs:  sudo pacman -S python-numpy python-scipy
        (recording uses parec from libpulse; record-once mode also uses pw-loopback and pw-play from PipeWire)

Run:    ./voice2ee.py            (menu)
        ./voice2ee.py --resume   (continue the last tuning session)
        ./voice2ee.py --help     (all options)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v2ee.cli import run  # noqa: E402

if __name__ == "__main__":
    run()
