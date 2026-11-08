# Benchmarks

Measured runtime and peak memory of the pipeline on 5,000, 100,000 and 1,000,000 rows, with every run checked for correct results. The numbers are in [results/README.md](results/README.md), written by the harness together with one JSON file per dataset that holds every individual run.

## What is measured

| Dataset | Data | Files |
|---|---|---|
| `5k` | The original scenario (`generate --scenario baseline`): 5,020 rows | 3 |
| `100k` | `generate --scenario scale --rows 100000 --files 5 --seed 1`: about 100,000 rows | 5 |
| `1m` | `generate --scenario scale --rows 1000000 --files 10 --seed 1`: about 1,000,000 rows | 10 |

Each dataset is generated first (generation is timed on its own). Then every repeat runs these scenarios:

| Scenario | What happens |
|---|---|
| Full run, fresh state | All files, empty output folder and state, default batch size |
| Unchanged rerun | The same command again: every file is recognized as unchanged and its staged rows are reused |
| Rerun with `--full-rebuild` | The state is discarded and every file is read again |
| Add the last file | A state that holds all files but the last (prepared by a run that is not measured), then a run with all files: one file is read, the others are reused |
| Other batch sizes | Full runs on a fresh state with `--batch-size 1000` and `--batch-size 50000` |

## How correctness is checked

- **Expected results.** Every run's `quality_report.json` must report `PASS` and match the expected row counts and revenue: the generator's `manifest.json` for the scale datasets, and the figures that `tests/test_baseline_regression.py` asserts for `5k`.
- **Identical outputs.** Every business output (all files in `order_quality.outputs.BUSINESS_OUTPUTS`, plus any workbook) must be byte-identical to the full run of the same repeat. This shows that incremental runs, full rebuilds and batch sizes give the same results.

A failure shows as `FAIL` in the results table and makes the harness exit with status 1.

## Method

- **Fresh process per measurement.** Every run starts `benchmarks/_child.py`, which calls the same command-line entry point as `python -m order_quality`.
- **Wall time** is measured by the harness around the whole process, including interpreter start-up. **Pipeline time** is the pipeline's own total from `run_log.jsonl`, with its phases: stage (read, normalize, validate, insert), resolve (duplicates), export (CSV files), checks (reconciliation and report), workbook (Excel report).
- **Peak memory** is the peak working set on Windows (`PeakWorkingSetSize` from `GetProcessMemoryInfo`) and the peak resident set size elsewhere (`getrusage` `ru_maxrss`). The process reads it about itself just before it exits. Reading it from the parent through the process handle would be wrong on Windows: a virtual environment's `python.exe` is a launcher that starts the real interpreter as a second process, so the handle belongs to the launcher.
- **Self-test.** `--self-test` checks the memory method: a child that writes 256 MiB must report about 256 MiB more than an idle child. It also prints the launcher's own reading to show the difference.
- **Statistics.** Each scenario is repeated (5 times for `5k` and `100k`, 3 times for `1m` by default) and reported as median and min–max.
- **Environment.** OS, Python, CPU model, logical CPUs, memory and the XlsxWriter version are recorded. Host names, user names and paths are not.
- **Limits.** One machine, warm operating-system file cache (the data was just generated), other software running as usual. Absolute times depend on the hardware; compare scenarios with each other rather than with other machines.

## Original script for comparison

`--with-baseline-script` also runs the original `src/clean_orders.py` from the `baseline` tag, unchanged, on the same files (copied into the folder layout it expects). It holds every row in memory and writes four CSV files and a JSON summary, with no row outcomes, corrections, processing state or reconciliation checks, and it counts duplicates differently (see [../docs/baseline-audit.md](../docs/baseline-audit.md)). Its time and memory show the cost of the extra guarantees. They do not show that one is better than the other.

## Reproduce

From the repository root, with the virtual environment from the main README active:

```bash
python benchmarks/run_benchmark.py --self-test
python benchmarks/run_benchmark.py --dataset 5k --with-baseline-script
python benchmarks/run_benchmark.py --dataset 100k --with-baseline-script
python benchmarks/run_benchmark.py --dataset 1m
```

`--dataset all` runs the three in turn, and `--repeat N` changes the number of repeats. Generated data goes to `benchmarks/data/` and run folders to `benchmarks/runs/`; both are ignored by Git. The run folders are deleted after each repeat unless `--keep-outputs` is given. A run folder holds the outputs and the state database; the results report its size for each dataset. `--report` rebuilds `results/README.md` from the JSON files without running anything.
