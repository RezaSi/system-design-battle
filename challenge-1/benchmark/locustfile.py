"""Locust load test for challenge-1 (URL Shortener).

Traffic mix:
  - 70% resolves   (GET /:code)
  - 25% shortens   (POST /shorten)
  - 5%  metadata   (GET /api/codes/:code)

Each user starts by seeding a small pool of codes so the resolve path has
something to hit. Then they hit endpoints in the ratios above.

The active load profile is driven by a LoadTestShape that reads
benchmark/config.yml at import time and ramps through the declared
stages. The grader reads only Locust's aggregated stats (final
locust_stats.csv) — the headline RPS, p50/p90/p99, and error rate
that the scoreboard ranks on.
"""

from __future__ import annotations

import os
import random
import uuid
from pathlib import Path

import yaml
from locust import HttpUser, LoadTestShape, between, events, task


class ShortenerUser(HttpUser):
    wait_time = between(0, 0.05)

    # Class-level shared pool of codes so we don't have to seed on every
    # user spawn. The pool fills organically as `shorten` tasks succeed;
    # `resolve` tasks no-op while it's empty (first ~1s of warmup). This
    # avoids seed-write bursts whenever a stage transition spawns new
    # users, which would otherwise pollute per-stage p99.
    _shared_codes: list[str] = []

    @task(14)
    def resolve(self) -> None:
        if not self._shared_codes:
            return
        code = random.choice(self._shared_codes)
        with self.client.get(
            f"/{code}",
            allow_redirects=False,
            name="GET /:code",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (301, 302, 307, 308):
                resp.failure(f"unexpected status {resp.status_code}")

    @task(5)
    def shorten(self) -> None:
        url = f"https://example.com/{uuid.uuid4().hex}"
        with self.client.post(
            "/shorten",
            json={"url": url},
            name="POST /shorten",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201):
                resp.failure(f"unexpected status {resp.status_code}")
                return
            try:
                code = resp.json().get("code")
            except Exception:
                resp.failure("response was not JSON")
                return
            if code and len(self._shared_codes) < 500:
                self._shared_codes.append(code)

    @task(1)
    def metadata(self) -> None:
        if not self._shared_codes:
            return
        code = random.choice(self._shared_codes)
        with self.client.get(
            f"/api/codes/{code}",
            name="GET /api/codes/:code",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}")


# ---------------------------------------------------------------------------
# Staged load shape driven by benchmark/config.yml
# ---------------------------------------------------------------------------


def _load_stages() -> list[dict]:
    """Read load_stages from benchmark/config.yml next to this file.

    Falls back to a single 80-user / 60s stage if config can't be parsed,
    so the file is still useful when run standalone.
    """
    cfg_path = Path(__file__).resolve().parent / "config.yml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        cfg = {}
    stages = cfg.get("load_stages") or []
    if not stages:
        return [{"name": "default", "users": 80, "spawn_rate": 20, "duration_s": 60, "scored": True}]
    return stages


STAGES = _load_stages()
# Total runtime is sum of stage durations. Locust requires --run-time on
# the CLI, but the LoadTestShape below will end the test as soon as it
# walks past the last stage.
TOTAL_DURATION_S = sum(int(s.get("duration_s", 0)) for s in STAGES)


class StagedLoad(LoadTestShape):
    """Walks through STAGES sequentially, snapping users/spawn_rate per stage."""

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
    """Print the load plan once so it shows up in CI logs."""
    print("[sdb] staged load plan:")
    for stage in STAGES:
        marker = " (scored)" if stage.get("scored", True) else " (warmup)"
        print(
            f"[sdb]   {stage['name']:<11} "
            f"{stage['users']:>4} users  "
            f"{stage['duration_s']:>3}s{marker}"
        )
