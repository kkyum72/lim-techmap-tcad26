#!/usr/bin/env python3
"""Replay a mapper's instruction stream value by value and check every LUT against the netlist.

  python3 verify/replay.py <netlist.v> <result dir> [-n 256] [--seed 1]

The result directory is the one a mapper writes (instructions.txt). Every primary input gets -n random bit
vectors; each instruction is applied to cell values and every LUT output is compared with its value computed
directly from the netlist.

Cell labels of each placed row are read either inline in the Place line (OURS_TCAD26)
    C<n> Place LUT<x> (r, c0), (r, c1) | <label c0> ... <label c1>
or from the comment line written right after the placement (CONTRA, PREV_ICCD24)
    #LABELS LUT<x> (r, c0), (r, c1) cols <c,...> | <one label per listed column>

Value model (MAGIC):
  - a cell placed with literal label L stores NOT(L): primary-input literals get their value at Place;
    internal-net literals stay unknown until a copy writes them; '0' fillers store 0.
  - Copy (r1, c1) to (r2, c2): cell2 = NOT(cell1)            (MAGIC NOT)
  - HNOR col:[c...] -> k: for the term rows of every LUT placed since the previous NOR batch whose term
    columns are exactly c... and whose output column is k: cell(r, k) = NOR(cells (r, c...))
  - VNOR ... row:[r...] -> r_out: for every such LUT whose term rows are r... and output row r_out:
    cell(r_out, k) = NOR(cells (r..., k)) = NOT(SOP of the placed terms).
  - Reset IR: (r, c) / Reset IR (c=k) / Reset Route (...): cells -> those cells become unknown
  - Reset Column: cols -> every cell of those columns becomes unknown except stored intermediate results
    (a LUT output written by its VNOR, or the destination of a 'Copy IR' move); such resets are counted as
    `masked_resets`.
Netlist semantics: `assign ~X = expr` means X = expr (the '~' on the left is the NOR-of-NOR notation).
Reported errors: a NOR or copy that reads an unknown cell, a LUT value that differs from the netlist value
on any input vector, and LUTs that are placed but never computed. Exit code 0 = PASS.
"""
import argparse
import glob
import random
import re
import sys


def golden(netlist_path, nvec, seed):
    text = open(netlist_path, encoding="utf-8").read()
    pis = [x.strip() for x in re.search(r"input\s+(.*?);", text, re.S).group(1).split(",") if x.strip()]
    exprs = dict(re.findall(r"assign\s+~(\S+)\s+=\s+(.*?);", text))
    mask = (1 << nvec) - 1
    rng = random.Random(seed)
    env = {p: rng.getrandbits(nvec) for p in pis}
    sys.setrecursionlimit(100000)
    order, seen = [], set()
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_\[\]\.]*")

    def visit(n):
        if n in seen or n not in exprs:
            return
        seen.add(n)
        for d in ident.findall(exprs[n]):
            visit(d)
        order.append(n)

    for n in exprs:
        visit(n)
    for n in order:
        e = exprs[n].replace("1'b0", "0").replace("1'b1", "-1")
        val = eval(ident.sub(lambda m: f"env[{m.group(0)!r}]", e), {"env": env})
        env[n] = val & mask             # assign ~n = expr  means  n = expr (NoN notation)
    outer_neg = {n: _outer_negated(e) for n, e in exprs.items()}
    return env, set(pis), mask, outer_neg


def _outer_negated(expr):
    """True if expr is ~( ... ) with the parenthesis spanning the whole expression."""
    e = expr.strip()
    if not e.startswith("~("):
        return False
    depth = 0
    for i, ch in enumerate(e[1:], start=1):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return i == len(e) - 1
    return False


def lit_value(label, env, pis, mask):
    """Stored value of a placed cell, or None if it waits for a copy."""
    if label in ("0", "."):
        return 0
    neg = label.startswith("~")
    name = label[1:] if neg else label
    if name not in pis:
        return None
    v = env[name]
    return v if neg else (~v) & mask   # stores NOT(label)


def replay(netlist, rdir, nvec=256, seed=1, trace=()):
    env, pis, mask, outer_neg = golden(netlist, nvec, seed)
    found = glob.glob(rdir + "/instructions.txt") or glob.glob(rdir + "/output*.txt")
    if not found:
        raise SystemExit(f"{rdir}: no instructions.txt")
    out = found[0]
    cell = {}
    pending = {}            # lut -> dict(rows=[...], c0, c1)
    computed, errors = set(), []
    placed = set()
    ir_cells = set()        # cells holding a stored IR (written by 'Copy IR', not yet reset)
    masked_resets = 0
    re_place = re.compile(r"C(\d+) Place LUT(\S+) \((\d+), (\d+)\), \((\d+), (\d+)\)\s*(?:\|\s*(.*))?$")
    re_labels = re.compile(r"#LABELS LUT(\S+) \((\d+), (\d+)\), \((\d+), (\d+)\)(?: cols ([\d,]+))?\s*\|\s*(.*)$")
    re_copy = re.compile(r"C(\d+) Copy (?:LUT|IR:)(\S+) \((\d+), (\d+)\) to \((\d+), (\d+)\)")
    re_hnor = re.compile(r"C(\d+) HNOR\s+col:\{?\[([\d, ]*)\]\}?\s*(?:->|to)\s*(\d+)")
    re_vnor = re.compile(r"C(\d+) VNOR\s+(?:col:\d+,\s*)?row:[\[{]([\d, ]*)[\]}]\s*(?:->|to)\s*(\d+)")
    nums = lambda s: [int(x) for x in s.split(",") if x.strip()]
    labels_seen = False
    for line in open(out, encoding="utf-8", errors="replace"):
        m = re_place.match(line)
        if m:
            _, x, r, c0, _, c1, labs = m.groups()
            r, c0, c1 = int(r), int(c0), int(c1)
            p = pending.setdefault(x, {"rows": [], "c0": c0, "c1": c1, "labels": {},
                                       "cols": list(range(c0, c1 + 1))})
            p["rows"].append(r)
            placed.add(x)
            # cell values come from the labels (inline or #LABELS): a CONTRA row may skip an occupied
            # column inside [c0, c1] (e.g. a stored IR), which must not be touched here
            if labs:
                labels_seen = True
                for i, lab in enumerate(labs.split()):
                    p["labels"][(r, c0 + i)] = lab
                    cell[(r, c0 + i)] = lit_value(lab, env, pis, mask)
            continue
        m = re_labels.match(line)
        if m:
            labels_seen = True
            x, r, c0, _, c1, cols_s, labs = m.groups()
            r, c0, c1 = int(r), int(c0), int(c1)
            cols_l = nums(cols_s) if cols_s else list(range(c0, c1 + 1))
            p = pending.get(x)
            if p is not None:
                p["cols"] = cols_l
            for c, lab in zip(cols_l, labs.split()):
                if p is not None:
                    p["labels"][(r, c)] = lab
                cell[(r, c)] = lit_value(lab, env, pis, mask)
            continue
        m = re_copy.match(line)
        if m:
            cyc, x, r1, c1, r2, c2 = m.groups()
            src = cell.get((int(r1), int(c1)))
            dst = (int(r2), int(c2))
            if src is None:
                errors.append(f"C{cyc}: copy of {x} from unknown cell ({r1}, {c1})")
                cell[dst] = None
            else:
                cell[dst] = (~src) & mask
            if " Copy IR:" in line:          # an IR moved to the IR region: the old cell is no longer an IR
                ir_cells.discard((int(r1), int(c1)))
                ir_cells.add(dst)
            else:
                ir_cells.discard(dst)
            continue
        m = re_hnor.match(line)
        if m:
            cyc, cols, k = m.group(1), tuple(nums(m.group(2))), int(m.group(3))
            for x, p in pending.items():
                if tuple(p["cols"][:-1]) == cols and p["cols"][-1] == k:
                    if x in trace:
                        for (rr, cc), lab in sorted(p["labels"].items()):
                            if cc == k or lab in ("0", "."):
                                continue
                            neg = lab.startswith("~"); nm = lab[1:] if neg else lab
                            want = (env[nm] if neg else (~env[nm]) & mask) if nm in env else None
                            got = cell.get((rr, cc))
                            st = "unknown" if got is None else ("ok" if got == want else f"WRONG on {bin(got ^ want).count('1')}/{nvec}")
                            print(f"[trace C{cyc}] {x} cell ({rr}, {cc}) label {lab}: {st}")
                    for r in p["rows"]:
                        ins = [cell.get((r, c)) for c in cols]
                        if any(v is None for v in ins):
                            errors.append(f"C{cyc}: HNOR of {x} row {r} reads an unknown cell")
                            cell[(r, k)] = None
                        else:
                            acc = 0
                            for v in ins:
                                acc |= v
                            cell[(r, k)] = (~acc) & mask
            continue
        m = re_vnor.match(line)
        if m:
            cyc, rows, r_out = m.group(1), tuple(nums(m.group(2))), int(m.group(3))
            for x, p in list(pending.items()):
                if tuple(sorted(p["rows"])) == rows and max(rows) + 1 == r_out:
                    k = p["cols"][-1]
                    ins = [cell.get((r, k)) for r in rows]
                    if any(v is None for v in ins):
                        errors.append(f"C{cyc}: VNOR of {x} reads an unknown cell")
                        cell[(r_out, k)] = None
                    else:
                        acc = 0
                        for v in ins:
                            acc |= v
                        val = (~acc) & mask
                        cell[(r_out, k)] = val
                        ir_cells.add((r_out, k))   # the LUT output (IR) stays until reset or moved
                        want = env[x] if outer_neg.get(x) else ((~env[x]) & mask) if x in env else None
                        if x in env and val != want:
                            bad = bin(val ^ want).count("1")
                            errors.append(f"C{cyc}: {x} = wrong value on {bad}/{nvec} vectors")
                    computed.add(x)
                    del pending[x]
            continue
        if " Reset" in line:
            if "Reset Column:" in line:
                cols = set(nums(line.split("Reset Column:")[1]))
                kept = [k for k in ir_cells if k[1] in cols]
                masked_resets += bool(kept)
                for key in [k for k in cell if k[1] in cols and k not in ir_cells]:
                    cell[key] = None
            else:
                for r, c in re.findall(r"\((\d+), (\d+)\)", line):
                    cell[(int(r), int(c))] = None
                    ir_cells.discard((int(r), int(c)))
    if not labels_seen:
        return None
    never = sorted(placed - computed)
    return {"placed": len(placed), "computed": len(computed), "never": never, "errors": errors,
            "masked_resets": masked_resets}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("netlist")
    ap.add_argument("rdir")
    ap.add_argument("-n", type=int, default=256)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--trace", default="", help="comma-separated LUT names: print the state of their input cells at HNOR time")
    a = ap.parse_args()
    res = replay(a.netlist, a.rdir, a.n, a.seed, set(t for t in a.trace.split(',') if t))
    if res is None:
        print(f"{a.rdir}: Place lines carry no cell labels; cannot replay")
        sys.exit(2)
    ok = not res["errors"] and not res["never"]
    print(f"{a.rdir}: placed {res['placed']}, computed {res['computed']}, never computed {len(res['never'])}, "
          f"errors {len(res['errors'])}, masked_resets {res['masked_resets']} -> {'PASS' if ok else 'FAIL'}")
    for e in res["errors"][:8]:
        print("   ", e)
    if res["never"]:
        print("    never computed e.g.", res["never"][:5])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
