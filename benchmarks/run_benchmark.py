"""Benchmark the order_quality pipeline on 5,000, 100,000 and 1,000,000 rows: runtime, peak memory, correctness.

Usage, from the repository root with the virtual environment active:

    python benchmarks/run_benchmark.py --self-test      # check the memory measurement first
    python benchmarks/run_benchmark.py --dataset 5k     # one dataset (5k, 100k, 1m or all)
    python benchmarks/run_benchmark.py --report         # rebuild results/README.md from the JSON files

Every measurement runs the command line in a fresh process (benchmarks/_child.py). Each dataset is generated
first, then measured in these scenarios per repeat: a full run on a fresh state, an unchanged rerun, a run with
--full-rebuild, an incremental run that adds the last file to a state holding the others, and full runs with
other batch sizes. Every run is checked against the expected results, and its business outputs must be
byte-identical to those of the full run. Method and reproduction: benchmarks/README.md.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

from order_quality import __version__
from order_quality.outputs import BUSINESS_OUTPUTS, QUALITY_REPORT, RUN_LOG
from order_quality.pipeline import DEFAULT_BATCH_SIZE

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks"
CHILD = BENCH / "_child.py"

DATASETS = {
    "5k": {"scenario": "baseline", "repeat": 5},
    "100k": {"scenario": "scale", "rows": 100_000, "files": 5, "seed": 1, "repeat": 5},
    "1m": {"scenario": "scale", "rows": 1_000_000, "files": 10, "seed": 1, "repeat": 3},
}
# The original scenario has no manifest; these are the figures tests/test_baseline_regression.py asserts.
BASELINE_EXPECTED = {
    "rows": {"input": 5020, "accepted": 4905, "rejected": 90, "duplicate_ignored": 15, "quarantined_conflict": 10},
    "revenue_orders": 4170,
    "revenue_usd": "333886.85",
}
ALT_BATCH_SIZES = (1_000, 50_000)
SCENARIO_LABELS = {
    "full": "Full run, fresh state",
    "unchanged": "Unchanged rerun (incremental)",
    "full_rebuild": "Rerun with --full-rebuild",
    "add_file": "Add the last file (incremental)",
    "batch_size": "Full run, fresh state, other batch size",
}
SELF_TEST_MIB = 256


# Measurement ---------------------------------------------------------------------------------------------------

def measure(mode: str, args: list[str], log_dir: Path, name: str) -> dict:
    """Run one command in a fresh process; return wall time, exit code and the process's own peak memory."""
    log_dir.mkdir(parents=True, exist_ok=True)
    report = log_dir / f"{name}.memory.json"
    report.unlink(missing_ok=True)
    with (log_dir / f"{name}.stdout.txt").open("wb") as out, (log_dir / f"{name}.stderr.txt").open("wb") as err:
        started = time.perf_counter()
        code = subprocess.call([sys.executable, str(CHILD), str(report), mode, *args], stdout=out, stderr=err, cwd=ROOT)
        wall = time.perf_counter() - started
    result = {"wall_s": round(wall, 3), "exit_code": code}
    if report.exists():
        memory = json.loads(report.read_text(encoding="utf-8"))
        result.update({k: round(v, 1) for k, v in memory.items() if k != "exit_code"})
    return result


def run_pipeline(files: list[Path], reference: Path, out: Path, log_dir: Path, name: str,
                 extra: list[str]) -> dict:
    args = ["run", *[a for f in files for a in ("--input", str(f))], "--reference", str(reference),
            "--out", str(out), "--quiet", *extra]
    result = measure("cli", args, log_dir, name)
    log = out / RUN_LOG
    if log.exists():
        last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        result["pipeline_s"] = last["seconds"]
        result["mode"] = last["mode"]
        result["file_actions"] = last["files"]
    return result


def check_expected(out: Path, expected: dict) -> tuple[bool, str]:
    path = out / QUALITY_REPORT
    if not path.exists():
        return False, "no quality report"
    report = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    if report["status"] != "PASS":
        problems.append(f"status {report['status']}")
    for key, value in expected["rows"].items():
        if report["rows"].get(key) != value:
            problems.append(f"{key} {report['rows'].get(key)} != {value}")
    if report["revenue"]["orders"] != expected["revenue_orders"]:
        problems.append(f"revenue orders {report['revenue']['orders']} != {expected['revenue_orders']}")
    if report["revenue"]["amount"] != expected["revenue_usd"]:
        problems.append(f"revenue {report['revenue']['amount']} != {expected['revenue_usd']}")
    return not problems, "; ".join(problems) or "matches"


def business_hashes(out: Path) -> dict[str, str]:
    names = sorted({*BUSINESS_OUTPUTS, *(p.name for p in out.glob("*.xlsx"))})
    return {n: hashlib.sha256((out / n).read_bytes()).hexdigest() if (out / n).exists() else "missing" for n in names}


def folder_mib(folder: Path, pattern: str = "*") -> float:
    return round(sum(p.stat().st_size for p in folder.glob(pattern) if p.is_file()) / 2**20, 1)


# Datasets ------------------------------------------------------------------------------------------------------

def prepare_dataset(name: str, data_dir: Path, log_dir: Path, reuse: bool) -> dict:
    spec = DATASETS[name]
    folder = data_dir / name
    generation = None
    if not (reuse and (folder / "raw").is_dir()):
        if folder.exists():
            shutil.rmtree(folder)
        args = ["generate", "--scenario", spec["scenario"], "--out", str(folder)]
        if spec["scenario"] == "scale":
            args += ["--rows", str(spec["rows"]), "--files", str(spec["files"]), "--seed", str(spec["seed"])]
        generation = measure("cli", args, log_dir, "generate")
        if generation["exit_code"] != 0:
            raise SystemExit(f"generating {name} failed; see {log_dir}")
    files = sorted((folder / "raw").glob("*.csv"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    if spec["scenario"] == "scale":
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        expected = {"rows": manifest["expected"]["rows"], "revenue_orders": manifest["expected"]["revenue_orders"],
                    "revenue_usd": manifest["expected"]["revenue_usd"]}
    else:
        expected = BASELINE_EXPECTED
    info = {"name": name, "scenario": spec["scenario"], "files": len(files), "input_rows": expected["rows"]["input"],
            "csv_mib": folder_mib(folder / "raw", "*.csv"), "sha256": digest.hexdigest(), "generation": generation}
    if spec["scenario"] == "scale":
        info.update(base_orders=spec["rows"], seed=spec["seed"])
    return {"info": info, "files": files, "reference": folder / "reference", "expected": expected}


# Environment ---------------------------------------------------------------------------------------------------

def _windows_memory_bytes() -> int:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullTotalPhys


def environment() -> dict:
    """Hardware and software of the measuring machine. Never the host name, user name or paths."""
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        edition = platform.win32_edition() or ""
        os_name = f"Windows {platform.release()} {edition} ({platform.version()})".replace("  ", " ")
        memory = _windows_memory_bytes()
    else:
        cpu = platform.processor() or platform.machine()
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            cpu = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                        if line.startswith("model name")), cpu)
        os_name = f"{platform.system()} {platform.release()}"
        release = Path("/etc/os-release")
        if release.exists():
            fields = dict(line.split("=", 1) for line in release.read_text().splitlines() if "=" in line)
            os_name = f"{fields.get('PRETTY_NAME', '').strip(chr(34))} (kernel {platform.release()})"
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    try:
        from importlib.metadata import version

        xlsxwriter = version("XlsxWriter")
    except Exception:
        xlsxwriter = None
    return {"os": os_name, "python": f"{platform.python_implementation()} {platform.python_version()}", "cpu": cpu,
            "logical_cpus": os.cpu_count(), "memory_gib": round(memory / 2**30, 1), "xlsxwriter": xlsxwriter}


def code_version() -> dict:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    dirty = git("status", "--porcelain", "--", ".", ":(exclude)benchmarks/results")
    return {"order_quality": __version__, "commit": git("rev-parse", "--short", "HEAD"), "uncommitted_changes": bool(dirty)}


# Benchmark -----------------------------------------------------------------------------------------------------

def bench_dataset(name: str, repeat: int, args: argparse.Namespace) -> dict:
    work = args.work_dir / name
    if work.exists():
        shutil.rmtree(work)
    print(f"== {name}: generating", flush=True)
    data = prepare_dataset(name, args.data_dir, work / "logs", args.reuse_data)
    files, reference, expected = data["files"], data["reference"], data["expected"]
    print(f"   {data['info']['input_rows']:,} rows in {len(files)} files", flush=True)
    extra = list(args.cli_arg)
    runs: list[dict] = []
    workbook_built = False

    def record(scenario: str, rep: int, batch: int, result: dict, out: Path, reference_hashes: dict | None) -> dict:
        nonlocal workbook_built
        ok, detail = check_expected(out, expected)
        hashes = business_hashes(out)
        workbook_built = workbook_built or any(n.endswith(".xlsx") for n in hashes)
        result.update(scenario=scenario, repeat=rep, batch_size=batch, matches_expected=ok, expected_detail=detail,
                      outputs_identical=(hashes == reference_hashes) if reference_hashes is not None else True)
        runs.append(result)
        flag = "ok" if ok and result["outputs_identical"] and result["exit_code"] == 0 else "MISMATCH"
        print(f"   [{rep}] {scenario:<12} batch {batch:>6,}  {result['wall_s']:>8.2f} s  "
              f"{result.get('peak_rss_mib', float('nan')):>7.1f} MiB  {flag}", flush=True)
        return hashes

    def batch_args(batch: int) -> list[str]:
        return [] if batch == DEFAULT_BATCH_SIZE else ["--batch-size", str(batch)]

    for rep in range(1, repeat + 1):
        logs = work / "logs" / f"repeat-{rep}"
        out = work / "full"
        if out.exists():
            shutil.rmtree(out)
        result = run_pipeline(files, reference, out, logs, "full", extra)
        result["outputs_mib"] = folder_mib(out)
        reference_hashes = record("full", rep, DEFAULT_BATCH_SIZE, result, out, None)
        record("unchanged", rep, DEFAULT_BATCH_SIZE,
               run_pipeline(files, reference, out, logs, "unchanged", extra), out, reference_hashes)
        record("full_rebuild", rep, DEFAULT_BATCH_SIZE,
               run_pipeline(files, reference, out, logs, "full_rebuild", ["--full-rebuild", *extra]), out,
               reference_hashes)
        added = work / "add_file"
        if added.exists():
            shutil.rmtree(added)
        setup = run_pipeline(files[:-1], reference, added, logs, "add_file_setup", extra)
        if setup["exit_code"] not in (0, 3):
            raise SystemExit(f"setup run for add_file failed; see {logs}")
        record("add_file", rep, DEFAULT_BATCH_SIZE,
               run_pipeline(files, reference, added, logs, "add_file", extra), added, reference_hashes)
        for batch in ALT_BATCH_SIZES:
            other = work / f"batch-{batch}"
            if other.exists():
                shutil.rmtree(other)
            record("batch_size", rep, batch,
                   run_pipeline(files, reference, other, logs, f"batch-{batch}", [*batch_args(batch), *extra]),
                   other, reference_hashes)
            if not args.keep_outputs:
                shutil.rmtree(other)
        if not args.keep_outputs:
            shutil.rmtree(added)

    baseline_script = run_baseline_script(data, repeat, work) if args.with_baseline_script else None
    if not args.keep_outputs:
        shutil.rmtree(work / "full", ignore_errors=True)
    return {
        "dataset": data["info"],
        "expected": expected,
        "environment": environment(),
        "code": code_version(),
        "measured_on": datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
        "settings": {"repeat": repeat, "default_batch_size": DEFAULT_BATCH_SIZE, "alt_batch_sizes": list(ALT_BATCH_SIZES),
                     "extra_cli_args": extra, "workbook_built": workbook_built},
        "runs": runs,
        "summary": summarize(runs),
        "baseline_script": baseline_script,
    }


def run_baseline_script(data: dict, repeat: int, work: Path) -> dict:
    """The original script from tag `baseline`, unchanged, on the same files (laid out where it expects them)."""
    folder = work / "baseline-script"
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "src").mkdir(parents=True)
    script = subprocess.run(["git", "show", "baseline:src/clean_orders.py"], cwd=ROOT, capture_output=True,
                            check=True).stdout
    (folder / "src" / "clean_orders.py").write_bytes(script)
    shutil.copytree(data["reference"], folder / "data" / "reference")
    (folder / "data" / "raw").mkdir(parents=True)
    for path in data["files"]:
        shutil.copy2(path, folder / "data" / "raw" / path.name)
    runs = []
    for rep in range(1, repeat + 1):
        logs = work / "logs" / f"repeat-{rep}"
        result = measure("script", [str(folder / "src" / "clean_orders.py")], logs, "baseline-script")
        stdout = (logs / "baseline-script.stdout.txt").read_text(encoding="utf-8")
        metrics = json.loads(stdout[stdout.index("{"):]) if "{" in stdout else {}
        result["repeat"] = rep
        result["reported"] = {k: metrics.get(k) for k in ("input_rows", "accepted_rows", "rejected_rows",
                                                          "duplicate_occurrences", "conflicting_order_ids",
                                                          "completed_order_count", "completed_revenue_usd")}
        runs.append(result)
        print(f"   [{rep}] baseline script          {result['wall_s']:>8.2f} s  "
              f"{result.get('peak_rss_mib', float('nan')):>7.1f} MiB", flush=True)
    return {"runs": runs, "wall_s": stats([r["wall_s"] for r in runs]),
            "peak_rss_mib": stats([r["peak_rss_mib"] for r in runs if "peak_rss_mib" in r])}


def stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {"median": round(statistics.median(values), 3), "min": round(min(values), 3), "max": round(max(values), 3)}


def summarize(runs: list[dict]) -> list[dict]:
    groups: dict[tuple[str, int], list[dict]] = {}
    for run in runs:
        groups.setdefault((run["scenario"], run["batch_size"]), []).append(run)
    summary = []
    for (scenario, batch), group in groups.items():
        phases = {}
        for phase in ("stage", "resolve", "export", "checks", "total"):
            values = [r["pipeline_s"][phase] for r in group if phase in r.get("pipeline_s", {})]
            if values:
                phases[phase] = stats(values)
        summary.append({
            "scenario": scenario, "batch_size": batch, "runs": len(group),
            "wall_s": stats([r["wall_s"] for r in group]),
            "pipeline_s": phases,
            "peak_rss_mib": stats([r["peak_rss_mib"] for r in group if "peak_rss_mib" in r]),
            "all_exit_zero": all(r["exit_code"] == 0 for r in group),
            "all_match_expected": all(r["matches_expected"] for r in group),
            "all_outputs_identical": all(r["outputs_identical"] for r in group),
            "file_actions": group[0].get("file_actions", {}),
        })
    return summary


# Self-test -----------------------------------------------------------------------------------------------------

def _handle_peak_mib(handle: int) -> float:
    """Peak working set read through a process handle (Windows), used only to show the launcher effect."""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    *[(n, ctypes.c_size_t) for n in ("a", "b", "c", "d", "e", "f")]]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    return counters.PeakWorkingSetSize / 2**20


def self_test(work: Path) -> int:
    """A child that writes SELF_TEST_MIB MiB must report about that much more peak memory than an idle child."""
    logs = work / "self-test"
    idle = measure("idle", [], logs, "idle")
    alloc = measure("alloc", [str(SELF_TEST_MIB)], logs, "alloc")
    delta = alloc["peak_rss_mib"] - idle["peak_rss_mib"]
    ok = 0.95 * SELF_TEST_MIB <= delta <= 1.05 * SELF_TEST_MIB + 8
    print(f"idle child: peak {idle['peak_rss_mib']:.1f} MiB")
    print(f"child holding {SELF_TEST_MIB} MiB: peak {alloc['peak_rss_mib']:.1f} MiB (difference {delta:.1f} MiB)")
    if sys.platform == "win32":
        proc = subprocess.Popen([sys.executable, str(CHILD), str(logs / "handle.json"), "alloc", str(SELF_TEST_MIB)])
        proc.wait()
        launcher = _handle_peak_mib(int(proc._handle))
        print(f"same child read through the launched process handle: {launcher:.1f} MiB "
              f"({'launcher process, not the interpreter' if launcher < SELF_TEST_MIB / 2 else 'interpreter'})")
    print("memory self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# Report --------------------------------------------------------------------------------------------------------

def _seconds(value: float) -> str:
    return f"{value:.2f}" if value < 10 else f"{value:.1f}"


def _mib(value: float) -> str:
    return f"{value:.1f}" if value < 100 else f"{value:,.0f}"


def _range(stat: dict, fmt) -> str:
    if not stat:
        return "—"
    if stat["min"] == stat["max"]:
        return fmt(stat["median"])
    return f"{fmt(stat['median'])} ({fmt(stat['min'])}–{fmt(stat['max'])})"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _actions(group: dict) -> str:
    """Files the run read and staged, against files whose staged rows it reused unchanged."""
    actions = group.get("file_actions", {})
    staged = sum(actions.get(a, 0) for a in ("NEW", "CHANGED", "RESTAGED"))
    return f"{staged} / {actions.get('UNCHANGED', 0)}" if actions else "—"


def write_report(results_dir: Path) -> Path:
    results = [json.loads(p.read_text(encoding="utf-8")) for name in DATASETS
               if (p := results_dir / f"{name}.json").exists()]
    lines = ["# Benchmark results", "",
             "Generated by `python benchmarks/run_benchmark.py --report` from the JSON files in this folder, which hold "
             "every individual run. Do not edit by hand. Method: [../README.md](../README.md).", ""]
    if not results:
        lines.append("No results yet.")
    environments = {json.dumps(r["environment"], sort_keys=True) for r in results}
    for env in map(json.loads, sorted(environments)):
        names = ", ".join(r["dataset"]["name"] for r in results if r["environment"] == env)
        lines += [f"## Environment ({names})", "", "| | |", "|---|---|",
                  f"| OS | {env['os']} |", f"| Python | {env['python']} |", f"| CPU | {env['cpu']} |",
                  f"| Logical CPUs | {env['logical_cpus']} |", f"| Memory | {env['memory_gib']} GiB |",
                  f"| XlsxWriter | {env['xlsxwriter'] or 'not installed'} |", ""]
    if results:
        lines += ["## Datasets", "",
                  "| Dataset | Input rows | Files | CSV size | Generator | Generation time | Fingerprint |",
                  "|---|---:|---:|---:|---|---:|---|"]
        for r in results:
            d = r["dataset"]
            generator = "baseline scenario" if d["scenario"] == "baseline" else \
                f"scale, {d['base_orders']:,} orders, seed {d['seed']}"
            generation = _seconds(d["generation"]["wall_s"]) + " s" if d.get("generation") else "reused"
            lines.append(f"| {d['name']} | {d['input_rows']:,} | {d['files']} | {_mib(d['csv_mib'])} MiB | {generator} "
                         f"| {generation} | `{d['sha256'][:12]}` |")
        lines += ["", "The fingerprint is a SHA-256 over every input file's name and content hash: the same value means "
                  "the same generated data.", ""]
    for r in results:
        d, s = r["dataset"], r["settings"]
        summary = summarize(r["runs"])  # recomputed from the individual runs, not read back
        lines += [f"## {d['name']}: {d['input_rows']:,} rows", "",
                  f"Measured {r['measured_on']} at commit `{r['code']['commit']}`"
                  f"{' (with uncommitted changes)' if r['code']['uncommitted_changes'] else ''}, "
                  f"{_plural(s['repeat'], 'repeat')}, workbook built: {'yes' if s['workbook_built'] else 'no'}"
                  f"{', extra arguments: ' + ' '.join(s['extra_cli_args']) if s['extra_cli_args'] else ''}. "
                  "Wall time is the whole process (start-up included); pipeline time is the pipeline's own total. "
                  "Values are median (min–max).", "",
                  "| Scenario | Batch size | Files read / reused | Runs | Wall time (s) | Pipeline time (s) | "
                  "Peak memory (MiB) | Results = expected | Outputs = full run |",
                  "|---|---:|---|---:|---:|---:|---:|---|---|"]
        for g in summary:
            lines.append(f"| {SCENARIO_LABELS[g['scenario']]} | {g['batch_size']:,} | {_actions(g)} | {g['runs']} | "
                         f"{_range(g['wall_s'], _seconds)} | {_range(g['pipeline_s'].get('total', {}), _seconds)} | "
                         f"{_range(g['peak_rss_mib'], _mib)} | "
                         f"{'PASS' if g['all_match_expected'] and g['all_exit_zero'] else 'FAIL'} | "
                         f"{'PASS' if g['all_outputs_identical'] else 'FAIL'} |")
        full = next(g for g in summary if g["scenario"] == "full")
        phases = ", ".join(f"{p} {_seconds(v['median'])} s" for p, v in full["pipeline_s"].items() if p != "total")
        sizes = [run.get("outputs_mib") for run in r["runs"] if run["scenario"] == "full" and "outputs_mib" in run]
        lines += ["", f"Full run phases (median): {phases}."
                  + (f" Output folder including the state database: {_mib(statistics.median(sizes))} MiB." if sizes else ""),
                  ""]
        b = r.get("baseline_script")
        if b:
            reported = b["runs"][0]["reported"]
            lines += ["Original script from tag `baseline`, unchanged, on the same files: wall time "
                      f"{_range(b['wall_s'], _seconds)} s, peak memory {_range(b['peak_rss_mib'], _mib)} MiB "
                      f"({_plural(len(b['runs']), 'run')}). It holds every row in memory and writes four CSV files and a "
                      "JSON summary, with no row outcomes, corrections, processing state or reconciliation checks. "
                      f"Its own summary reported {reported['accepted_rows']:,} accepted, "
                      f"{reported['rejected_rows']:,} rejected, {reported['duplicate_occurrences']:,} duplicate "
                      f"occurrences and revenue {reported['completed_revenue_usd']}; it counts duplicates "
                      "differently from this pipeline (see docs/baseline-audit.md).", ""]
    path = results_dir / "README.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return path


# Command line --------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", choices=(*DATASETS, "all"), help="dataset to measure")
    parser.add_argument("--repeat", type=int, help="repeats per scenario (default: 5 for 5k and 100k, 3 for 1m)")
    parser.add_argument("--self-test", action="store_true", help="validate the peak-memory measurement and exit")
    parser.add_argument("--report", action="store_true", help="rebuild results/README.md from the JSON files and exit")
    parser.add_argument("--with-baseline-script", action="store_true",
                        help="also measure the original script from tag `baseline` on the same files")
    parser.add_argument("--cli-arg", action="append", default=[], help="extra argument for every pipeline run")
    parser.add_argument("--reuse-data", action="store_true", help="reuse an already generated dataset")
    parser.add_argument("--keep-outputs", action="store_true", help="keep the run folders under the work directory")
    parser.add_argument("--results-dir", type=Path, default=BENCH / "results")
    parser.add_argument("--data-dir", type=Path, default=BENCH / "data")
    parser.add_argument("--work-dir", type=Path, default=BENCH / "runs")
    args = parser.parse_args(argv)
    args.results_dir, args.data_dir, args.work_dir = (p.resolve() for p in (args.results_dir, args.data_dir,
                                                                              args.work_dir))
    if args.self_test:
        return self_test(args.work_dir)
    if args.report:
        print(write_report(args.results_dir))
        return 0
    if not args.dataset:
        parser.error("choose --dataset, --self-test or --report")
    failures = 0
    for name in (DATASETS if args.dataset == "all" else [args.dataset]):
        result = bench_dataset(name, args.repeat or DATASETS[name]["repeat"], args)
        args.results_dir.mkdir(parents=True, exist_ok=True)
        (args.results_dir / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8",
                                                       newline="\n")
        failures += sum(not (g["all_exit_zero"] and g["all_match_expected"] and g["all_outputs_identical"])
                        for g in result["summary"])
        print(f"   written {args.results_dir / (name + '.json')}", flush=True)
    print(write_report(args.results_dir))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
