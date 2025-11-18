from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from .benchmark import run_benchmark


def write_benchmark(output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_benchmark()
    json_path = output_dir / "benchmark.json"
    csv_path = output_dir / "scenarios.csv"
    report_path = output_dir / "report.md"
    manifest_path = output_dir / "manifest.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result["scenarios"][0]))
        writer.writeheader()
        writer.writerows(result["scenarios"])
    report_path.write_text(
        "\n".join(
            [
                "# BidFlow benchmark",
                "",
                f"- scenarios: {result['scenario_count']}",
                f"- reserve-clearing scenarios: {result['reserve_clearing_count']}",
                f"- idempotent bid reuses: {result['idempotency_reuse_count']}",
                f"- total events: {result['total_events']}",
                f"- replay checks: {'PASS' if result['all_replays_match'] else 'FAIL'}",
                "",
                "All values come from fixed synthetic auctions; no customer, payment, or marketplace records are used.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    files = [json_path, csv_path, report_path]
    manifest = {
        "schema_version": 1,
        "files": [
            {"name": file.name, "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
            for file in files
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "benchmark": str(json_path),
        "csv": str(csv_path),
        "report": str(report_path),
        "manifest": str(manifest_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bidflow-benchmark")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/benchmark"))
    args = parser.parse_args(argv)
    result = run_benchmark()
    artifacts = write_benchmark(args.output_dir)
    print(json.dumps({"status": "ok", **result, "artifacts": artifacts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
