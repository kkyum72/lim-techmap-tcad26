# Parallelism-Driven, Area-Aware Technology Mapping for Memristive Logic-in-Memory

Code for the IEEE TCAD 2026 paper

> I. Kim, M. Kim, and H. Park, "A Parallelism-Driven, Area-Aware Technology Mapping Framework for Memristive
> Logic-in-Memory," *IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems*, 2026.
> DOI: [10.1109/TCAD.2026.3699619](https://doi.org/10.1109/TCAD.2026.3699619)

The mappers take a NOR-of-NOR (NoN) LUT netlist and map it onto an R×C memristor crossbar that computes with
MAGIC NOR/NOT operations. The output is the cycle-by-cycle instruction stream (LUT placement, fan-in copies,
parallel horizontal/vertical NORs, intermediate-result handling and resets) and its total cycle count.

| Method | File | Description |
|---|---|---|
| Ours | `mappers/OURS_TCAD26.py` | The proposed framework: size-aware floorplanning, ILP-based LUT placement, Hanan-grid fan-in routing, parallel HNOR/VNOR execution |
| CONTRA | `mappers/CONTRA.py` | Baseline: our re-implementation of CONTRA [D. Bhattacharjee et al., ICCAD 2020], written for the comparison in the paper (not the original authors' code, which is available at <https://github.com/debjyoti0891/arche>) |
| Prev. | `mappers/PREV_ICCD24.py` | Baseline (previous work, ICCD 2024) used for comparison in the paper |

## Contents

```
mappers/              OURS_TCAD26.py, CONTRA.py, PREV_ICCD24.py
benchmarks/
  netlists/           NoN LUT netlists of the 29 benchmarks used in the paper (mapper inputs)
    ISCAS85/          c432 ... c7552
    EPFL/             adder, arbiter, bar, cavlc, ctrl, dec, div, i2c, int2float, log2, max, mem_ctrl,
                      multiplier, priority, router, sin, sqrt, square, voter
  original/           the original circuits (ISCAS'85 .bench, EPFL .v) for re-synthesis
synthesis/            circuit -> NoN LUT netlist (ABC + converter)
verify/replay.py      value-level replay of an instruction stream against the netlist
results/              example outputs (see results/README.md)
requirements.txt      Python dependencies
check_env.py          checks Python, Gurobi (package and license) and ABC
```

## Installation

1. **Python 3.8 or later** (tested with 3.8.10).
2. **Gurobi** (needed by `OURS_TCAD26.py`; the baselines do not use it).
   ```bash
   pip install -r requirements.txt          # gurobipy 11.0.3 (the tested version)
   ```
   The ILP models exceed the size limit of the license that ships with `gurobipy`, so a full license is needed.
   Academic users can get a free license at <https://www.gurobi.com/academia/>: register, request an
   academic named-user license, and run the shown `grbgetkey <key>` command on the machine (it writes
   `~/gurobi.lic`). If the license file is elsewhere, set `GRB_LICENSE_FILE=<path>/gurobi.lic`.
3. **ABC** (optional, only for `synthesis/synthesize.py`): build it from <https://github.com/berkeley-abc/abc>
   ```bash
   git clone https://github.com/berkeley-abc/abc.git && cd abc && make -j8
   export ABC_DIR=$PWD                      # the directory with the `abc` binary and `abc.rc`
   ```
4. Check the environment:
   ```bash
   python3 check_env.py                     # add --abc <dir> if ABC_DIR is not set
   ```

## Quick start

```bash
python3 mappers/OURS_TCAD26.py -i benchmarks/netlists/ISCAS85/c432.v -c 32x32
# [OURS_TCAD26] c432 32x32: <total> cycles (Place Cycle ... / Reset_Cycle ...) -> results/c432_32x32_OURS_TCAD26
python3 mappers/CONTRA.py      -i benchmarks/netlists/ISCAS85/c432.v -c 64x64
python3 mappers/PREV_ICCD24.py -i benchmarks/netlists/ISCAS85/c432.v -c 64x64
python3 verify/replay.py benchmarks/netlists/ISCAS85/c432.v results/c432_32x32_OURS_TCAD26
```

Run the commands from this directory. A run either finishes with a summary line (total cycle count) or reports
that the mapping failed (for example, when the circuit does not fit the crossbar).

## Command-line options

All three mappers:

| Option | Meaning |
|---|---|
| `-i, --input` | NoN LUT netlist (`.v`) |
| `-c, --crossbar` | crossbar size `RxC`, e.g. `64x64` (non-square sizes such as `128x256` are allowed) |
| `-o, --out` | output directory (default `results/<bench>_<RxC>_<method>[_options]`) |
| `--threads` | Gurobi threads for `OURS_TCAD26.py` (default 4) |

`OURS_TCAD26.py` only (the ablation study of the paper):

| Option | Values | Meaning |
|---|---|---|
| `--placement` | `ilp` (default), `prev` | LUT placement: proposed ILP placement, or the placement of the previous work |
| `--routing` | `hanan` (default), `astar` | fan-in routing: proposed Hanan-grid routing, or the A* routing of the previous work |

The four combinations give the ablation table, for example:

```bash
for p in ilp prev; do for r in hanan astar; do
  python3 mappers/OURS_TCAD26.py -i benchmarks/netlists/ISCAS85/c1355.v -c 128x128 --placement $p --routing $r
done; done
```

## Output

Each run writes one directory:

| File | Content |
|---|---|
| `instructions.txt` | the instruction stream, one instruction per line (`C<cycle> Place LUT...`, `Copy LUT...`, `Copy IR...`, `HNOR...`, `VNOR...`, `Reset...`). The last line gives the cycle count of each stage: `Place Cycle / A_star_Cycle (fan-in routing) / Copy_IR_Cycle / HNOR_Cycle / VNOR_Cycle / Reset_Cycle`; the total cycle count is their sum |
| `log.txt` | progress log (per-level placement, ILP solver output, routing) |
| `failure.txt` | written only when a fan-in cannot be routed |

`verify/replay.py <netlist> <result dir>` replays `instructions.txt` on random input vectors and checks every
LUT value against the netlist (exit code 0 and `PASS` when all LUTs are computed correctly).

## Benchmarks and crossbar sizes in the paper

The paper evaluates each benchmark on the crossbar sizes below (Tables II and III).

| Benchmark | Suite | Small | Medium | Large |
|---|---|---|---|---|
| c432, c499, c880 | ISCAS85 | 16x16 | 32x32 | 64x64 |
| c1355, c1908, c2670, c3540, c5315, c6288, c7552 | ISCAS85 | 32x32 | 64x64 | 128x128 |
| cavlc, dec, i2c, priority | EPFL | 32x32 | 64x64 | 128x128 |
| ctrl, router | EPFL | 16x16 | 32x32 | 64x64 |
| adder, bar, max, sin | EPFL | 64x64 | 128x128 | 256x256 |
| int2float | EPFL | — | — | 128x128 |
| arbiter, mem_ctrl, voter, div, log2, multiplier, sqrt, square | EPFL | — | — | 256x256 |

Other experiments: the cycle breakdown of adder uses a 128x256 crossbar; the placement/routing ablation uses
adder, c432, c499, c880 (64x64), c1355, c1908, c2670, c3540, c5315, c6288, dec (128x128) and c7552 (256x256).

Runtime: the placement ILP of `OURS_TCAD26.py` has a time limit of 120 s per level, so small and medium
configurations take seconds to minutes and the largest 256x256 designs a few hours. The baselines do not use an
ILP and are faster.

Results (cycle counts and runtime) may vary slightly depending on software versions, implementation details,
the environment and processor specifications.

## Synthesis (optional)

The netlists in `benchmarks/netlists/` were produced from `benchmarks/original/` with this flow: log2 and sqrt
with `--script extended`, the other 27 benchmarks with the default script. The flow can also prepare other
circuits:

```bash
python3 synthesis/synthesize.py --input benchmarks/original/ISCAS85/c432.bench --out my_netlists [--abc $ABC_DIR] [-K 4]
python3 synthesis/synthesize.py --input benchmarks/original/EPFL/sqrt.v --out my_netlists --script extended
# -> my_netlists/<bench>.v (NoN LUT netlist for the mappers) and my_netlists/<bench>_sop.v (the same network as
#    standard Verilog); ABC `cec` checks it against the input circuit
```

The flow runs ABC (`strash; resyn; if -K 4`; with `--script extended`, `resyn2; resyn2rs; compress2rs` follow
`resyn`, which takes much longer), writes each 4-input LUT as a sum of products (or its complement), and emits
the NoN notation `assign ~X = expr;` (meaning X = expr) that the mappers read. Inputs may be `.bench`, `.v` or
`.blif`.

## Citation

```bibtex
@article{kim2026lim,
  author  = {Kim, Ikkyum and Kim, Minhong and Park, Heechun},
  title   = {A Parallelism-Driven, Area-Aware Technology Mapping Framework for Memristive Logic-in-Memory},
  journal = {IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems},
  year    = {2026},
  doi     = {10.1109/TCAD.2026.3699619}
}
```

## License

MIT License (see `LICENSE`). The original benchmark circuits in `benchmarks/original/` come from the ISCAS'85
and EPFL combinational benchmark suites and remain under their own terms. The EPFL circuits
(<https://github.com/lsils/benchmarks>) and the netlists derived from them are distributed under the MIT
License of that suite (see `benchmarks/original/EPFL/LICENSE`).
