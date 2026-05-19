#!/usr/bin/env python3
"""Parse grader artifacts and produce JSON summaries plus the PR markdown report.

Used by scripts/run_benchmark.sh in three modes:

1. --pytest-junit + --coverage-out
       Parse the JUnit XML produced by pytest into coverage.json.

2. --locust-csv + --benchmark-out
       Parse the Locust aggregated CSV (per-endpoint summary plus the
       Aggregated final row) and emit benchmark.json with aggregated
       rps + p50/p90/p99 + errors plus per_endpoint[] for the PR report.

3. --coverage + --benchmark + --challenge + --submission + --report-out
       Combine the two JSONs into the benchmark-report.md that the PR
       comment and the scoreboard use.

The scoreboard ranks submissions on three axes:
  1. coverage_pct  (descending)  — correctness gate
  2. rps           (descending)  — throughput
  3. p99_ms        (ascending)   — tail latency

That's it. No SLO, no letter grade, no per-stage breakdown.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Pytest coverage
# ---------------------------------------------------------------------------


def parse_pytest_junit(junit_path: Path) -> dict:
    if not junit_path.exists():
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "coverage_pct": 0.0,
            "tests": [],
        }

    tree = ET.parse(junit_path)
    root = tree.getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "coverage_pct": 0.0,
            "tests": [],
        }

    total = int(suite.get("tests", "0"))
    failures = int(suite.get("failures", "0"))
    errors = int(suite.get("errors", "0"))
    skipped = int(suite.get("skipped", "0"))
    passed = max(total - failures - errors - skipped, 0)
    coverage_pct = round((passed / total) * 100, 2) if total else 0.0

    tests = []
    for case in suite.findall("testcase"):
        name = case.get("name", "")
        status = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
        tests.append({"name": name, "status": status})

    return {
        "total": total,
        "passed": passed,
        "failed": failures + errors,
        "skipped": skipped,
        "coverage_pct": coverage_pct,
        "tests": tests,
    }


# ---------------------------------------------------------------------------
# Locust parsing
# ---------------------------------------------------------------------------


def _num(value: Any, cast=float, default=0):
    """Robust numeric cast, tolerating empty strings, 'N/A', and floats-as-ints."""
    if value in (None, "", "N/A"):
        return default
    try:
        if cast is int:
            return int(float(value))
        return cast(value)
    except (TypeError, ValueError):
        return default


def parse_per_endpoint(csv_path: Path) -> tuple[dict, list[dict]]:
    """Parse Locust's final _stats.csv into (aggregated, per_endpoint).

    Locust writes one row per endpoint plus an "Aggregated" final row.
    """
    aggregated_default = {
        "rps": 0.0,
        "total_requests": 0,
        "failures": 0,
        "failure_rate_pct": 0.0,
        "p50_ms": 0,
        "p90_ms": 0,
        "p99_ms": 0,
        "avg_ms": 0.0,
        "max_ms": 0,
    }
    if not csv_path.exists():
        return aggregated_default, []

    with csv_path.open() as f:
        rows = list(csv.DictReader(f))

    per_endpoint: list[dict] = []
    aggregated = None
    for row in rows:
        # csv.DictReader returns None for cells in short / partially
        # written rows. Locust's multi-process writer occasionally emits
        # one of those during stage transitions; treat them as empty.
        name = (row.get("Name") or "").strip()
        if name == "Aggregated":
            aggregated = row
            continue
        total = _num(row.get("Request Count"), int, 0)
        failures = _num(row.get("Failure Count"), int, 0)
        per_endpoint.append(
            {
                "method": row.get("Type", "").strip(),
                "name": name,
                "total_requests": total,
                "failures": failures,
                "rps": round(_num(row.get("Requests/s"), float, 0.0), 2),
                "p50_ms": _num(row.get("50%"), int, 0)
                or _num(row.get("Median Response Time"), int, 0),
                "p90_ms": _num(row.get("90%"), int, 0),
                "p99_ms": _num(row.get("99%"), int, 0),
                "avg_ms": round(_num(row.get("Average Response Time"), float, 0.0), 2),
            }
        )

    if aggregated is None:
        return aggregated_default, per_endpoint

    total_requests = _num(aggregated.get("Request Count"), int, 0)
    failures = _num(aggregated.get("Failure Count"), int, 0)
    failure_rate = (failures / total_requests * 100) if total_requests else 0.0

    summary = {
        "rps": round(_num(aggregated.get("Requests/s"), float, 0.0), 2),
        "total_requests": total_requests,
        "failures": failures,
        "failure_rate_pct": round(failure_rate, 2),
        "p50_ms": _num(aggregated.get("50%"), int, 0)
        or _num(aggregated.get("Median Response Time"), int, 0),
        "p90_ms": _num(aggregated.get("90%"), int, 0),
        "p99_ms": _num(aggregated.get("99%"), int, 0),
        "avg_ms": round(_num(aggregated.get("Average Response Time"), float, 0.0), 2),
        "max_ms": _num(aggregated.get("Max Response Time"), int, 0),
    }
    return summary, per_endpoint


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(coverage: dict, benchmark: dict, challenge: str, submission: str) -> str:
    challenge_name = os.path.basename(challenge.rstrip("/"))
    submission_user = os.path.basename(submission.rstrip("/"))

    failed_tests = [t["name"] for t in coverage.get("tests", []) if t["status"] == "failed"]
    failed_block = ""
    if failed_tests:
        items = "\n".join(f"- `{t}`" for t in failed_tests[:20])
        more = ""
        if len(failed_tests) > 20:
            more = f"\n- ...and {len(failed_tests) - 20} more"
        failed_block = f"\n\n**Failing tests**\n{items}{more}\n"

    rps = benchmark.get("rps", 0.0)
    p50_ms = benchmark.get("p50_ms", 0)
    p90_ms = benchmark.get("p90_ms", 0)
    p99_ms = benchmark.get("p99_ms", 0)
    err_pct = benchmark.get("failure_rate_pct", 0.0)
    total_requests = benchmark.get("total_requests", 0)
    failures = benchmark.get("failures", 0)

    endpoint_rows = []
    for ep in benchmark.get("per_endpoint", []):
        endpoint_rows.append(
            f"| `{ep['method']} {ep['name']}` | {ep['total_requests']} | "
            f"{ep['failures']} | {ep['rps']:.0f} | {ep['p50_ms']} | "
            f"{ep['p90_ms']} | {ep['p99_ms']} |"
        )
    endpoint_table = "\n".join(endpoint_rows) if endpoint_rows else "| — | — | — | — | — | — | — |"

    return f"""## Benchmark report — {challenge_name}

Submission: `{submission_user}`

### Headline

| Metric | Value |
|--------|------:|
| Coverage | **{coverage.get('coverage_pct', 0.0)}%** ({coverage.get('passed', 0)}/{coverage.get('total', 0)} tests) |
| Throughput | **{rps:.0f} req/s** ({total_requests} requests over the whole run) |
| Tail latency | p50 = **{p50_ms} ms**, p90 = **{p90_ms} ms**, p99 = **{p99_ms} ms** |
| Errors | **{err_pct:.2f}%** ({failures} failed requests) |

### Per-endpoint summary (whole run)

| Endpoint | Requests | Failures | RPS | p50 (ms) | p90 (ms) | p99 (ms) |
|----------|---------:|---------:|----:|---------:|---------:|---------:|
{endpoint_table}
{failed_block}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pytest-junit", type=Path)
    p.add_argument("--coverage-out", type=Path)
    p.add_argument("--locust-csv", type=Path)
    p.add_argument("--benchmark-out", type=Path)
    p.add_argument("--coverage", type=Path)
    p.add_argument("--benchmark", type=Path)
    p.add_argument("--challenge", type=str)
    p.add_argument("--submission", type=str)
    p.add_argument("--report-out", type=Path)
    args = p.parse_args()

    if args.pytest_junit and args.coverage_out:
        data = parse_pytest_junit(args.pytest_junit)
        args.coverage_out.write_text(json.dumps(data, indent=2))
        print(f"Wrote coverage to {args.coverage_out}")
        return 0

    if args.locust_csv and args.benchmark_out:
        aggregated, per_endpoint = parse_per_endpoint(args.locust_csv)
        data = {
            **aggregated,
            "per_endpoint": per_endpoint,
        }
        args.benchmark_out.write_text(json.dumps(data, indent=2))
        print(f"Wrote benchmark to {args.benchmark_out}")
        return 0

    if args.coverage and args.benchmark and args.report_out and args.challenge and args.submission:
        cov = json.loads(args.coverage.read_text()) if args.coverage.exists() else {}
        bench = json.loads(args.benchmark.read_text()) if args.benchmark.exists() else {}
        report = render_report(cov, bench, args.challenge, args.submission)
        args.report_out.write_text(report)
        print(f"Wrote report to {args.report_out}")
        return 0

    print("No matching mode for the provided arguments.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
