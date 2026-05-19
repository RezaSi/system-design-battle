#!/usr/bin/env python3
"""Parse grader artifacts and produce JSON summaries plus the PR markdown report.

Used by scripts/run_benchmark.sh in three modes:

1. --pytest-junit + --coverage-out
       Parse the JUnit XML produced by pytest into coverage.json.

2. --locust-csv + --locust-history + --config + --benchmark-out
       Parse the Locust aggregated CSV (per-endpoint summary) plus the
       per-second history CSV (per-stage breakdown), combine with the
       challenge config (load_stages, for the per-stage table only),
       and emit benchmark.json with aggregated rps + p99_ms + errors
       plus per_endpoint[] and per_stage[] for the PR report.

3. --coverage + --benchmark + --challenge + --submission + --report-out
       Combine the two JSONs into the benchmark-report.md that the PR
       comment and the scoreboard use.

The scoreboard ranks submissions on three axes:
  1. coverage_pct  (descending)  — correctness gate
  2. rps           (descending)  — throughput
  3. p99_ms        (ascending)   — tail latency

That's it. No SLO, no letter grade.
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

import yaml


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


# Reasonable physical bounds for cells in Locust's stats_history.csv.
# Anything outside these ranges is almost certainly a row that got
# corrupted by the multi-process writer (column shift, partial flush).
_LATENCY_MAX_MS = 600_000  # 10 minutes — clearly bogus for our benches
_THROUGHPUT_MAX = 1_000_000  # one million req/s on a single-CPU stack: bogus


def _is_plausible_history_row(row: dict) -> bool:
    """Return True only if the row's numeric cells are within physical bounds.

    The expected shape is: Timestamp (epoch seconds), Requests/s,
    Failures/s, and a handful of percentile columns in milliseconds.
    Reject the row if any of those is None, negative, or off-the-charts.
    """
    ts = _num(row.get("Timestamp"), int, 0)
    # Plausible Unix epoch second is between 2001-09-09 (10^9) and roughly
    # the year 5000 (10^11). Anything else is from a column shift.
    if ts < 10**9 or ts > 10**11:
        return False
    for col, ceiling in (
        ("Requests/s", _THROUGHPUT_MAX),
        ("Failures/s", _THROUGHPUT_MAX),
        ("50%", _LATENCY_MAX_MS),
        ("90%", _LATENCY_MAX_MS),
        ("99%", _LATENCY_MAX_MS),
    ):
        v = row.get(col)
        if v is None:
            return False
        if v == "N/A":
            continue  # "no data this tick" is a legitimate state
        num = _num(v, float, None)
        if num is None or num < 0 or num > ceiling:
            return False
    return True


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


def parse_per_stage(history_path: Path, stages: list[dict]) -> list[dict]:
    """Slice stats_history.csv by stage time-window and compute per-stage metrics.

    Used by the PR report to show how the system degrades under load.
    Not used by the scoreboard — the scoreboard reads only the
    aggregated row.

    Strategy:
      - Filter rows where Name == "Aggregated" and the row passes the
        physical-bound sanity check.
      - Group by stage based on elapsed time since the first row.
      - Drop the first 5 seconds of each stage (ramp-up and connection
        warm-up are not representative of steady-state behavior).
      - RPS and error rate are computed from the cumulative
        `Total Request Count` / `Total Failure Count` columns
        (delta across the window divided by elapsed seconds). The
        per-tick `Requests/s` column is sparse on the master in
        multi-process mode — workers report every few seconds, so
        most ticks read 0 — and averaging it produces misleadingly
        low numbers.
      - p50 / p90 / p99 are averaged across the per-tick
        percentile snapshots, which DO populate every tick even when
        the master hasn't received a worker delta yet.
    """
    if not stages or not history_path.exists():
        return []

    with history_path.open() as f:
        rows = list(csv.DictReader(f))

    # `or ""` defends against partial-write rows where DictReader returns
    # None for the value (Locust master-side writer occasionally emits one
    # of those when workers are spawning/shutting down in multi-process
    # mode).
    agg_rows = [r for r in rows if (r.get("Name") or "").strip() == "Aggregated"]
    # Reject rows that look corrupted: partial writes, column-shifted
    # rows, or otherwise out-of-physical-bound cells. Without this filter
    # a single bad row can poison `t0`, the per-stage mean, or the
    # per-stage error rate (e.g. dividing a 1e8 "Failures/s" by a
    # legitimate 100 "Requests/s" yields a 1e8% error rate in the report).
    agg_rows = [r for r in agg_rows if _is_plausible_history_row(r)]
    if not agg_rows:
        return []

    # Sort by timestamp so window slicing and cumulative-delta math are
    # both well-defined regardless of CSV row order.
    agg_rows.sort(key=lambda r: _num(r.get("Timestamp"), int, 0))

    # Multi-process mode interleaves two row populations in the same CSV:
    # per-worker "Aggregated" snapshots (small cumulative counts) and the
    # master's true aggregated rows (much larger counts). Mixing them
    # blows up the cumulative-delta RPS calculation. Filter to the
    # master rows by keeping only rows whose Total Request Count is
    # monotonically non-decreasing — worker snapshots break monotonicity
    # because each worker counts its own slice.
    monotonic: list[dict] = []
    running_max = -1
    for r in agg_rows:
        cnt = _num(r.get("Total Request Count"), int, 0)
        if cnt >= running_max:
            monotonic.append(r)
            running_max = cnt
    agg_rows = monotonic
    if not agg_rows:
        return []

    timestamps = [int(_num(r.get("Timestamp"), int, 0)) for r in agg_rows]
    t0 = min(timestamps)

    cumulative = 0
    results: list[dict] = []
    for stage in stages:
        name = stage.get("name", "stage")
        duration = int(stage.get("duration_s", 0))
        users = int(stage.get("users", 0))
        scored = bool(stage.get("scored", True))
        start = cumulative
        end = cumulative + duration
        cumulative = end

        # Window includes [start + 5, end). The 5s skip lets the user
        # count stabilise after the previous stage's spawn-rate ramp and
        # gives connection pools / JIT a chance to warm up.
        window_start = t0 + start + 5
        window_end = t0 + end

        in_window = [
            r for r, ts in zip(agg_rows, timestamps)
            if window_start <= ts < window_end
        ]

        if not in_window:
            results.append(
                {
                    "name": name,
                    "users": users,
                    "duration_s": duration,
                    "scored": scored,
                    "samples": 0,
                    "rps": 0.0,
                    "p50_ms": 0,
                    "p90_ms": 0,
                    "p99_ms": 0,
                    "error_rate_pct": 0.0,
                }
            )
            continue

        # RPS and error rate from cumulative counters: delta across the
        # window divided by elapsed seconds. This is immune to the
        # master-side "Requests/s" sparsity in --csv-full-history mode.
        first, last = in_window[0], in_window[-1]
        first_ts = _num(first.get("Timestamp"), int, 0)
        last_ts = _num(last.get("Timestamp"), int, 0)
        elapsed = max(last_ts - first_ts, 1)
        first_reqs = _num(first.get("Total Request Count"), int, 0)
        last_reqs = _num(last.get("Total Request Count"), int, 0)
        first_fails = _num(first.get("Total Failure Count"), int, 0)
        last_fails = _num(last.get("Total Failure Count"), int, 0)
        d_reqs = max(last_reqs - first_reqs, 0)
        d_fails = max(last_fails - first_fails, 0)
        rps = d_reqs / elapsed
        err_pct = (d_fails / d_reqs * 100) if d_reqs > 0 else 0.0

        p50_values = [_num(r.get("50%"), int, 0) for r in in_window]
        p90_values = [_num(r.get("90%"), int, 0) for r in in_window]
        p99_values = [_num(r.get("99%"), int, 0) for r in in_window]

        def _mean(xs):
            return int(round(sum(xs) / len(xs))) if xs else 0

        results.append(
            {
                "name": name,
                "users": users,
                "duration_s": duration,
                "scored": scored,
                "samples": len(in_window),
                "rps": round(rps, 2),
                "p50_ms": _mean(p50_values),
                "p90_ms": _mean(p90_values),
                "p99_ms": _mean(p99_values),
                # Worst per-window p99 — surfaced for debugging.
                "p99_max_ms": max(p99_values) if p99_values else 0,
                "error_rate_pct": round(err_pct, 2),
            }
        )
    return results


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

    # Per-stage table — kept as diagnostic information.
    stage_rows = []
    for stage in benchmark.get("per_stage", []):
        scored_marker = "" if stage.get("scored", True) else " _(warmup)_"
        stage_rows.append(
            f"| {stage['name']}{scored_marker} | {stage['users']} | "
            f"{stage['rps']:.0f} | {stage['p50_ms']} | {stage['p90_ms']} | "
            f"{stage['p99_ms']} | {stage['error_rate_pct']:.2f}% |"
        )
    stage_table = "\n".join(stage_rows) if stage_rows else "| — | — | — | — | — | — | — |"

    # Per-endpoint table
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

### Load curve

Each row is a stage from the staged load profile (diagnostic only —
the scoreboard uses the aggregated row across the whole run).

| Stage | Users | RPS | p50 (ms) | p90 (ms) | p99 (ms) | Errors |
|-------|------:|----:|---------:|---------:|---------:|-------:|
{stage_table}

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
    p.add_argument("--locust-history", type=Path)
    p.add_argument("--config", type=Path)
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

        stages = []
        if args.config and args.config.exists():
            cfg = yaml.safe_load(args.config.read_text()) or {}
            stages = cfg.get("load_stages") or []

        per_stage = parse_per_stage(args.locust_history, stages) if args.locust_history else []

        data = {
            **aggregated,
            "per_endpoint": per_endpoint,
            "per_stage": per_stage,
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
