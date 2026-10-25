# Example results

These directories are example outputs of the three mappers on a few small configurations, generated with the code
in this repository (`mappers/`). Each directory holds `instructions.txt` (the instruction stream) and `log.txt`
(the progress log), as described in the top-level `README.md`. Every instruction stream was checked with
`verify/replay.py` (PASS).

Total cycles are the sum of the stage counts on the last line of `instructions.txt`.

## Methods (crossbar 32x32)

| Benchmark | OURS_TCAD26 | CONTRA | PREV_ICCD24 |
|---|---|---|---|
| c432 | 671 | mapping failed | 777 |
| c880 | 1018 | 1237 | 1216 |
| ctrl | 286 | 352 | 326 |
| c1355 | 936 | — | — |

"mapping failed" means the circuit did not fit the crossbar with that method (the run reports it and writes the
instructions up to that point).

## Placement/routing options of OURS_TCAD26 (c432, crossbar 64x64)

| Directory | `--placement` | `--routing` | Total cycles |
|---|---|---|---|
| `c432_64x64_OURS_TCAD26` | `ilp` | `hanan` | 664 |
| `c432_64x64_OURS_TCAD26_place-prev` | `prev` | `hanan` | 689 |
| `c432_64x64_OURS_TCAD26_route-astar` | `ilp` | `astar` | 779 |
| `c432_64x64_OURS_TCAD26_place-prev_route-astar` | `prev` | `astar` | 768 |

## Regenerating

```bash
python3 mappers/OURS_TCAD26.py -i benchmarks/netlists/ISCAS85/c432.v -c 32x32 -o my_results/c432_32x32_OURS_TCAD26
python3 verify/replay.py benchmarks/netlists/ISCAS85/c432.v my_results/c432_32x32_OURS_TCAD26
```

Results (cycle counts and runtime) may vary slightly depending on software versions, implementation details,
the environment and processor specifications.
