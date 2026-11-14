"""scripts/run_tests.py — run the whole algo test suite (no pytest needed).

    python scripts/run_tests.py
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TESTS = sorted((REPO / "algo" / "tests").glob("test_*.py"))

fails = 0
for t in TESTS:
    print(f"\n=== {t.relative_to(REPO)} ===")
    r = subprocess.run([sys.executable, str(t)], cwd=REPO)
    fails += (r.returncode != 0)
print(f"\n{len(TESTS) - fails}/{len(TESTS)} test files passed")
sys.exit(1 if fails else 0)
