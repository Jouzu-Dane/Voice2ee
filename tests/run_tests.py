#!/usr/bin/env python3
"""Run all tests without needing pytest:  python3 tests/run_tests.py [name-filter]
(pytest works too:  python3 -m pytest -s tests)"""
import importlib
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import Skip  # noqa: E402

flt = sys.argv[1] if len(sys.argv) > 1 else ""
passed = failed = skipped = 0
for f in sorted(HERE.glob("test_*.py")):
    mod = importlib.import_module(f.stem)
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        if flt and flt not in f"{f.stem}.{name}":
            continue
        t = time.time()
        try:
            getattr(mod, name)()
            passed += 1
            print(f"PASS  {f.stem}.{name}  ({time.time() - t:.1f}s)")
        except Skip as ex:
            skipped += 1
            print(f"SKIP  {f.stem}.{name}: {ex}")
        except Exception:
            failed += 1
            print(f"FAIL  {f.stem}.{name}")
            traceback.print_exc()
print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
sys.exit(1 if failed else 0)
