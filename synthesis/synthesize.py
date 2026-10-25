#!/usr/bin/env python3
"""Synthesize a combinational circuit into the NOR-of-NOR (NoN) LUT netlist that the mappers read.

Flow (ABC, then synthesis_LUT_SoP.py):
  1. ABC: read the circuit; strash; resyn; if -K <K> (K-input LUT mapping); write BLIF
     (abc_script_template.abc; `resyn` is an alias defined in ABC's abc.rc). With --script extended,
     resyn2; resyn2rs; compress2rs follow resyn (stronger AIG optimization, much longer runtime).
  2. Each LUT (.names) becomes a sum of products of its on-set, or the complement of the sum of products of
     its off-set; ports are renamed I<name> / O<name>. ABC `cec` checks the standard Verilog form against the
     input circuit.
  3. The NoN form `assign ~X = expr;` (meaning X = expr) is written for the mappers, after dropping constant
     outputs and removing underscores from assigned names (an underscore separates fields in the mappers'
     cell labels).

  python3 synthesis/synthesize.py --input benchmarks/original/ISCAS85/c432.bench --out my_netlists
  python3 synthesis/synthesize.py --input benchmarks/original/EPFL/sqrt.v --out my_netlists --script extended
  -> my_netlists/c432.v       (NoN netlist, mapper input)
     my_netlists/c432_sop.v   (the same LUT network as standard Verilog, the form checked by `cec`)

ABC is located by --abc, then the ABC_DIR environment variable, then the directory of `abc` on PATH. The
directory must contain the `abc` binary and `abc.rc` (both are in an ABC build directory).
Intermediate files are kept in <out>/.work/<bench>/.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSIGN = re.compile(r"(\s*assign\s+~?)(\w*)(\s*=.*)")


def find_abc(arg):
    for d in (arg, os.environ.get("ABC_DIR")):
        if d:
            return os.path.abspath(d)
    exe = shutil.which("abc")
    return os.path.dirname(os.path.realpath(exe)) if exe else None


def postprocess(text):
    """Drop constant outputs and remove underscores from assigned names; fail on an unnamed assignment."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("assign"):
            if re.search(r"=\s*1'b[01]\s*;", s):
                continue
            m = ASSIGN.match(line)
            if m:
                if not m.group(2):
                    raise SystemExit(f"[synthesize] unnamed assignment in the LUT netlist: {s[:80]}")
                line = m.group(1) + m.group(2).replace("_", "") + m.group(3)
        out.append(line)
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="circuit (.bench, .v or .blif)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--abc", default=None, help="ABC directory containing `abc` and `abc.rc`")
    ap.add_argument("-K", type=int, default=4, help="LUT input count (default 4)")
    ap.add_argument("--script", choices=["default", "extended"], default="default",
                    help="ABC optimization before LUT mapping: default = strash; resyn, "
                         "extended = strash; resyn; resyn2; resyn2rs; compress2rs")
    a = ap.parse_args()

    abc = find_abc(a.abc)
    if not abc or not os.path.isfile(os.path.join(abc, "abc")) or not os.path.isfile(os.path.join(abc, "abc.rc")):
        sys.exit("[synthesize] ABC not found: give --abc <dir> (or set ABC_DIR) pointing to a directory with `abc` and `abc.rc`")
    inp = os.path.abspath(a.input)
    ext = os.path.splitext(inp)[1]
    fmt = {".bench": "bench", ".v": "verilog", ".blif": "blif"}.get(ext)
    if fmt is None:
        sys.exit(f"[synthesize] unsupported input format: {ext}")
    bench = os.path.splitext(os.path.basename(inp))[0]
    out = os.path.abspath(a.out)
    work = os.path.join(out, ".work", bench)
    os.makedirs(os.path.join(work, "LUT"), exist_ok=True)
    shutil.copy(os.path.join(HERE, "synthesis_LUT_SoP.py"), work)
    with open(os.path.join(HERE, "abc_script_template.abc")) as f:
        script = f.read()
    if a.script == "extended":
        script = script.replace("resyn\n", "resyn\nresyn2\nresyn2rs\ncompress2rs\n", 1)
    with open(os.path.join(work, "abc_script_template.abc"), "w") as f:
        f.write(script)
    sop = os.path.join(work, f"{bench}_mapped.v")
    with open(os.path.join(work, "LUT_syn_config.cfg"), "w") as f:
        f.write("[input_output]\n"
                f"input_path={inp}\ninput_format={fmt}\nLUT_format=blif\n"
                f"LUT_path=./LUT/tmp.blif\noutput_path={sop}\n\n"
                f"[abc]\nabc_dir_path={abc}\n\n[LUT_Mapping]\nK={a.K}\n")
    r = subprocess.run([sys.executable, "synthesis_LUT_SoP.py"], cwd=work)
    named = os.path.join(work, f"{bench}_mapped_named.v")
    if r.returncode != 0 or not os.path.isfile(named):
        sys.exit(f"[synthesize] synthesis failed (see {work})")
    with open(named) as f:
        text = postprocess(f.read())
    with open(os.path.join(out, f"{bench}.v"), "w") as f:
        f.write(text)
    shutil.copy(sop, os.path.join(out, f"{bench}_sop.v"))
    # equivalence of the final LUT network with the input circuit (ports matched by order)
    r = subprocess.run([os.path.join(abc, "abc"), "-c", f'cec -n "{inp}" "{os.path.join(out, bench + "_sop.v")}"'],
                       capture_output=True, text=True)
    eq = "equivalent" if "Networks are equivalent" in r.stdout else "NOT equivalent (see ABC output above)"
    print(f"[synthesize] {bench}: {sum(1 for l in text.splitlines() if l.strip().startswith('assign'))} LUTs, "
          f"ABC cec: {eq} -> {os.path.join(out, bench + '.v')}")


if __name__ == "__main__":
    main()
