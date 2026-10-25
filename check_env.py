#!/usr/bin/env python3
"""Check the environment: Python version, Gurobi (package and license) and, optionally, ABC for synthesis.

  python3 check_env.py [--abc <ABC directory>]
"""
import argparse
import os
import shutil
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--abc", default=None, help="ABC directory (default: $ABC_DIR, then `abc` on PATH)")
    a = ap.parse_args()
    ok = True

    v = sys.version_info
    good = v >= (3, 8)
    ok &= good
    print(f"[{'ok' if good else 'FAIL'}] Python {v.major}.{v.minor}.{v.micro} (3.8 or later required; tested 3.8.10)")

    try:
        import gurobipy
        ver = ".".join(map(str, gurobipy.gurobi.version()))
        print(f"[ok] gurobipy {ver} (tested 11.0.3)")
        try:
            m = gurobipy.Model()
            x = m.addVars(2001, vtype=gurobipy.GRB.BINARY)   # above the size-limited license
            m.setParam("OutputFlag", 0)
            m.optimize()
            print("[ok] Gurobi license (a 2001-variable model solved)")
        except gurobipy.GurobiError as e:
            ok = False
            print(f"[FAIL] Gurobi license: {e}  -> see README, Installation")
    except ImportError:
        ok = False
        print("[FAIL] gurobipy not installed: pip install -r requirements.txt")

    d = a.abc or os.environ.get("ABC_DIR")
    if not d and shutil.which("abc"):
        d = os.path.dirname(os.path.realpath(shutil.which("abc")))
    if d and os.path.isfile(os.path.join(d, "abc")) and os.path.isfile(os.path.join(d, "abc.rc")):
        print(f"[ok] ABC found in {d} (needed only for synthesis/synthesize.py)")
    else:
        print("[--] ABC not found (optional; needed only for synthesis/synthesize.py; give --abc or set ABC_DIR)")

    print("environment OK" if ok else "environment NOT ready")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
