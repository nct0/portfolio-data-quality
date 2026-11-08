"""Command line: `python -m order_quality run ...` and `python -m order_quality generate ...`."""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from order_quality import __version__, log
from order_quality.errors import EXIT_UNEXPECTED, EXIT_USAGE, OrderQualityError
from order_quality.generate import generate_baseline, generate_late_batch, generate_scale
from order_quality.pipeline import DEFAULT_BATCH_SIZE, RunOptions, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="order-quality", description="Clean, validate, deduplicate and reconcile "
                                     "order exports into audited CSV and Excel reports.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("run", help="process order exports")
    p.add_argument("--input", action="append", required=True, type=Path,
                   help="input CSV file or folder (repeatable; folders are searched for the configured file pattern)")
    p.add_argument("--reference", type=Path, default=Path("data/reference"),
                   help="folder with products.csv and channels.csv (default: data/reference)")
    p.add_argument("--out", type=Path, default=Path("output"), help="output folder (default: output)")
    p.add_argument("--config", type=Path, help="rules file (default: the built-in rules, same as config/rules.toml)")
    p.add_argument("--corrections", type=Path, help="reviewer decision file (an edited correction_review.csv)")
    p.add_argument("--state", type=Path, help="processing state file (default: <out>/state.sqlite)")
    p.add_argument("--full-rebuild", action="store_true", help="discard the processing state and restage every file")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                   help=f"rows per database insert batch (default: {DEFAULT_BATCH_SIZE}); never changes results")
    p.add_argument("--no-workbook", action="store_true", help="skip the Excel report (the CSV and JSON outputs are "
                   "always written)")
    p.add_argument("--log-format", choices=("text", "json"), default="text", help="log format on stderr")
    p.add_argument("--quiet", action="store_true", help="log warnings and errors only")

    g = commands.add_parser("generate", help="write synthetic order exports")
    g.add_argument("--scenario", choices=("baseline", "scale", "late-batch"), required=True)
    g.add_argument("--out", type=Path, default=Path("data"), help="output folder (default: data)")
    g.add_argument("--rows", type=int, default=100_000, help="scale: number of base orders (default: 100000)")
    g.add_argument("--files", type=int, default=5, help="scale: number of export files (default: 5)")
    g.add_argument("--seed", type=int, default=1, help="scale: random seed (default: 1)")
    g.add_argument("--decisions-dir", type=Path, default=Path("corrections"),
                   help="late-batch: folder for demo-decisions.csv (default: corrections)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            return _generate(args)
        if args.batch_size < 1:
            print("error: --batch-size must be at least 1", file=sys.stderr)
            return EXIT_USAGE
        log.configure(args.log_format, 30 if args.quiet else 20)
        result = run(RunOptions(inputs=args.input, reference=args.reference, out=args.out, config=args.config,
                                corrections=args.corrections, state=args.state, full_rebuild=args.full_rebuild,
                                batch_size=args.batch_size, workbook=not args.no_workbook))
    except OrderQualityError as exc:
        print(f"error {exc.code}: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted; outputs from the previous run were left unchanged", file=sys.stderr)
        return EXIT_UNEXPECTED
    _summary(result)
    return result.exit_code


def _generate(args: argparse.Namespace) -> int:
    if args.scenario == "baseline":
        info = generate_baseline(args.out)
        print(f"baseline scenario: {info['rows']:,} rows in {info['files']} files under {args.out / 'raw'}")
    elif args.scenario == "scale":
        manifest = generate_scale(args.out, args.rows, args.files, args.seed)
        rows = manifest["expected"]["rows"]["input"]
        print(f"scale scenario: {rows:,} rows in {args.files} files under {args.out / 'raw'}; "
              f"expected results in {args.out / 'manifest.json'}")
    else:
        manifest = generate_late_batch(args.out, args.decisions_dir)
        print(f"late batch: {manifest['rows']:,} rows in {args.out / 'late-batch' / manifest['file']}; "
              f"demo decisions in {args.decisions_dir / 'demo-decisions.csv'}")
    return 0


def _summary(result) -> None:
    report = result.report
    rows, revenue = report["rows"], report["revenue"]
    lines = [
        f"Run {result.run_id}: {report['status']}  ->  {result.out}",
        f"  input rows           {rows['input']:>12,}",
        f"  accepted             {rows['accepted']:>12,}",
        f"  rejected             {rows['rejected']:>12,}",
        f"  duplicates ignored   {rows['duplicate_ignored']:>12,}",
        f"  quarantined          {rows['quarantined_conflict']:>12,}",
    ]
    if rows["conflict_overridden"]:
        lines.append(f"  conflicts overridden {rows['conflict_overridden']:>12,}")
    lines.append(f"  revenue ({', '.join(revenue['statuses'])}) {revenue['orders']:,} orders, "
                 f"{revenue['currency']} {Decimal(revenue['amount']):,.2f}")
    corrections = report["corrections"]
    if corrections["suggested"] or corrections["stale"]:
        lines.append(f"  corrections          {corrections['suggested']} suggested, {corrections['applied']} applied, "
                     f"{corrections['rejected']} rejected, {corrections['pending']} pending, {corrections['stale']} stale")
    lines.extend(f"  check {c['check']:<28} {c['status']}" for c in report["checks"])
    print("\n".join(lines))
