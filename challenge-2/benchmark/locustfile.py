"""Locust load test for challenge-2 (Rate-Limited API).

Each user picks a random API key from a shared pool. The pool is sized
so that a well-behaved limiter mostly returns 200, with some 429s
sprinkled in. A limiter that takes a global lock will show high p99
latency.

Traffic mix:
  - 90% POST /api/work
  - 10% GET  /api/quota

A StagedLoad shape walks through the stages declared in
benchmark/config.yml. 429s are counted as success because they are the
limiter doing its job correctly; only 5xx / connection errors are
"failures" for SLO purposes.
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml
from locust import HttpUser, LoadTestShape, between, events, task


API_KEYS = [f"k_{i:03d}" for i in range(200)]


class ClientUser(HttpUser):
    wait_time = between(0, 0.02)

    def on_start(self) -> None:
        self.api_key = random.choice(API_KEYS)

    @task(9)
    def work(self) -> None:
        with self.client.post(
            "/api/work",
            headers={"X-API-Key": self.api_key},
            name="POST /api/work",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}")

    @task(1)
    def quota(self) -> None:
        with self.client.get(
            "/api/quota",
            headers={"X-API-Key": self.api_key},
            name="GET /api/quota",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}")


# ---------------------------------------------------------------------------
# Staged load shape driven by benchmark/config.yml
# ---------------------------------------------------------------------------


def _load_stages() -> list[dict]:
    cfg_path = Path(__file__).resolve().parent / "config.yml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        cfg = {}
    stages = cfg.get("load_stages") or []
    if not stages:
        return [{"name": "default", "users": 200, "spawn_rate": 50, "duration_s": 60, "scored": True}]
    return stages


STAGES = _load_stages()
TOTAL_DURATION_S = sum(int(s.get("duration_s", 0)) for s in STAGES)


class StagedLoad(LoadTestShape):
    def tick(self):
        elapsed = self.get_run_time()
        cumulative = 0
        for stage in STAGES:
            cumulative += int(stage.get("duration_s", 0))
            if elapsed < cumulative:
                return (int(stage["users"]), float(stage["spawn_rate"]))
        return None


@events.test_start.add_listener
def _log_plan(environment, **_kwargs):
    print("[sdb] staged load plan:")
    for stage in STAGES:
        marker = " (scored)" if stage.get("scored", True) else " (warmup)"
        print(
            f"[sdb]   {stage['name']:<11} "
            f"{stage['users']:>5} users  "
            f"{stage['duration_s']:>3}s{marker}"
        )


@events.init.add_listener
def _enable_master_response_times_cache(environment, **_kwargs):
    """Force-enable the response-times cache on the master.

    With `--processes N`, Locust runs one master + N workers. The
    master's RequestStats defaults to `use_response_times_cache=False`
    (it normally just aggregates worker reports, not raw samples). But
    we pass `--csv-full-history`, which makes the stats_history.csv
    writer call `get_current_response_time_percentile()` on master-side
    StatsEntry objects every tick — and that raises ValueError when
    the cache is off, killing the writer greenlet on its first tick.

    Net effect of the crash: stats_history.csv ends up empty and the
    per-stage breakdown in the PR report silently degrades to zeros
    (the aggregated headline numbers come from a different CSV and
    keep working).

    Flipping the flag back on lets worker reports populate the
    master's cache via StatsEntry.extend(), and the writer stops
    crashing every second. No-op on workers — they already have it on.
    """
    runner = environment.runner
    if runner is None:
        return
    stats = runner.stats
    stats.use_response_times_cache = True
    stats.total.use_response_times_cache = True
    for entry in stats.entries.values():
        entry.use_response_times_cache = True
