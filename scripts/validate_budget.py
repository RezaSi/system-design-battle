#!/usr/bin/env python3
"""Validate a submission's docker-compose.yml against the challenge budget.

Every service in the submission MUST declare both a CPU and a memory cap.
The sum across all services MUST NOT exceed the challenge's
`resource_budget` from benchmark/config.yml. Contestants choose how to
slice the budget across their services — that allocation is part of the
design.

Accepted cap syntax (mix-and-match per service):

  services:
    app:
      cpus: 0.5
      mem_limit: 512m

  or:

  services:
    app:
      deploy:
        resources:
          limits:
            cpus: "0.5"
            memory: 512M

Memory units recognised: B (bytes), K/KB/KiB, M/MB/MiB, G/GB/GiB.

Usage:
    scripts/validate_budget.py \
        --compose <path/to/docker-compose.yml> \
        --budget-cpus 1.0 --budget-memory-mb 1024

Exit code 0 = valid. Non-zero = caps missing or sum over budget; stderr
contains a human-readable explanation that the PR comment will surface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Memory parsing
# ---------------------------------------------------------------------------


_MEM_MULTIPLIERS = {
    "": 1,           # bytes
    "b": 1,
    "k": 1024,
    "kb": 1000,
    "ki": 1024,
    "kib": 1024,
    "m": 1024 * 1024,
    "mb": 1000 * 1000,
    "mi": 1024 * 1024,
    "mib": 1024 * 1024,
    "g": 1024 ** 3,
    "gb": 1000 ** 3,
    "gi": 1024 ** 3,
    "gib": 1024 ** 3,
}


def parse_memory_mb(value) -> float | None:
    """Parse `512m`, `1g`, `"1024Mi"`, or plain int → megabytes.

    Returns None if it can't be parsed. Megabytes here means 1024 * 1024
    bytes (binary), to match Docker's reporting and the typical reading
    of "MB" in container contexts.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / (1024 * 1024)
    s = str(value).strip()
    if not s:
        return None

    digits = []
    i = 0
    while i < len(s) and (s[i].isdigit() or s[i] == "."):
        digits.append(s[i])
        i += 1
    if not digits:
        return None
    try:
        number = float("".join(digits))
    except ValueError:
        return None
    unit = s[i:].strip().lower()
    multiplier = _MEM_MULTIPLIERS.get(unit)
    if multiplier is None:
        return None
    return number * multiplier / (1024 * 1024)


def parse_cpus(value) -> float | None:
    """Parse `0.5` or `"0.5"` into float CPUs. Returns None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_service_caps(service: dict) -> tuple[float | None, float | None, str]:
    """Return (cpus, memory_mb, source) for one service.

    Looks at top-level `cpus:` / `mem_limit:` first, then falls back to
    `deploy.resources.limits.cpus` / `deploy.resources.limits.memory`.
    `source` is one of "top-level", "deploy.resources", "mixed", or
    "missing" — used only for the human-readable summary.
    """
    cpus = parse_cpus(service.get("cpus"))
    mem = parse_memory_mb(service.get("mem_limit"))
    top_level_used = cpus is not None or mem is not None

    deploy_limits = (
        service.get("deploy", {})
        .get("resources", {})
        .get("limits", {})
    ) or {}
    if cpus is None:
        cpus = parse_cpus(deploy_limits.get("cpus"))
    if mem is None:
        mem = parse_memory_mb(deploy_limits.get("memory"))
    deploy_used = bool(deploy_limits)

    if cpus is None and mem is None:
        return None, None, "missing"
    if top_level_used and deploy_used:
        return cpus, mem, "mixed"
    if deploy_used:
        return cpus, mem, "deploy.resources"
    return cpus, mem, "top-level"


# ---------------------------------------------------------------------------
# Validation entry point
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--compose", type=Path, required=True)
    p.add_argument("--budget-cpus", type=float, required=True)
    p.add_argument("--budget-memory-mb", type=int, required=True)
    args = p.parse_args()

    try:
        data = yaml.safe_load(args.compose.read_text()) or {}
    except Exception as exc:
        print(f"validate_budget: failed to parse {args.compose}: {exc}", file=sys.stderr)
        return 2

    services = data.get("services") or {}
    if not isinstance(services, dict) or not services:
        print(
            f"validate_budget: no services found in {args.compose}",
            file=sys.stderr,
        )
        return 2

    errors: list[str] = []
    rows: list[tuple[str, float | None, float | None, str]] = []
    total_cpus = 0.0
    total_mem = 0.0

    for name, svc in services.items():
        if not isinstance(svc, dict):
            errors.append(f"service '{name}': definition is not a mapping")
            continue
        cpus, mem, source = extract_service_caps(svc)
        rows.append((name, cpus, mem, source))
        if cpus is None:
            errors.append(
                f"service '{name}': must declare a CPU cap "
                f"(top-level `cpus:` or `deploy.resources.limits.cpus`)"
            )
        else:
            total_cpus += cpus
        if mem is None:
            errors.append(
                f"service '{name}': must declare a memory cap "
                f"(top-level `mem_limit:` or `deploy.resources.limits.memory`)"
            )
        else:
            total_mem += mem

    print(
        f"[sdb] resource budget: {args.budget_cpus} CPU / {args.budget_memory_mb} MB "
        f"(stack total across {len(services)} service(s))",
        file=sys.stderr,
    )
    print(
        f"{'service':<20} {'cpus':>8} {'mem (MB)':>12}  source",
        file=sys.stderr,
    )
    print(f"{'-' * 20} {'-' * 8} {'-' * 12}  {'-' * 18}", file=sys.stderr)
    for name, cpus, mem, source in rows:
        cpu_str = f"{cpus:.2f}" if cpus is not None else "  —"
        mem_str = f"{mem:.0f}" if mem is not None else "—"
        print(
            f"{name:<20} {cpu_str:>8} {mem_str:>12}  {source}",
            file=sys.stderr,
        )
    print(f"{'-' * 20} {'-' * 8} {'-' * 12}", file=sys.stderr)
    print(
        f"{'total':<20} {total_cpus:>8.2f} {total_mem:>12.0f}  "
        f"(budget: {args.budget_cpus:.2f} / {args.budget_memory_mb})",
        file=sys.stderr,
    )

    EPSILON = 0.01
    if total_cpus > args.budget_cpus + EPSILON:
        errors.append(
            f"stack exceeds CPU budget: declared {total_cpus:.2f} CPU > "
            f"budget {args.budget_cpus:.2f} CPU"
        )
    if total_mem > args.budget_memory_mb + 1:
        errors.append(
            f"stack exceeds memory budget: declared {total_mem:.0f} MB > "
            f"budget {args.budget_memory_mb} MB"
        )

    if errors:
        print(file=sys.stderr)
        print("[sdb] budget validation FAILED:", file=sys.stderr)
        for err in errors:
            print(f"[sdb]   - {err}", file=sys.stderr)
        print(file=sys.stderr)
        print(
            "[sdb] Fix: declare `cpus:` and `mem_limit:` on every service so the "
            "sum fits in the challenge budget. Example:",
            file=sys.stderr,
        )
        print(
            "[sdb]   services:\n"
            "[sdb]     app:\n"
            "[sdb]       cpus: 0.6\n"
            "[sdb]       mem_limit: 640m",
            file=sys.stderr,
        )
        return 1

    headroom_cpu = args.budget_cpus - total_cpus
    headroom_mem = args.budget_memory_mb - total_mem
    print(
        f"[sdb] budget OK. headroom: {headroom_cpu:.2f} CPU, "
        f"{headroom_mem:.0f} MB",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
