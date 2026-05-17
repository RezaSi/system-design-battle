# Benchmark — challenge 1

Three files matter:

- `tests.py` — pytest functional tests. The grader counts `passed / total`
  as the **coverage** number. Read this file end-to-end. It is the
  executable spec — every assertion maps to a line in the challenge
  README.
- `locustfile.py` — Locust task mix plus a `StagedLoad` shape that walks
  through the stages declared in `config.yml`. The shape reports
  per-second statistics to `stats_history.csv`, which the grader rolls
  up into the aggregated RPS and p99 numbers on the scoreboard plus a
  per-stage breakdown in the PR report.
- `config.yml` — challenge-wide configuration: resource budget, staged
  load profile, health path.

You should not modify any file in this folder when submitting a solution.
The PR-tests workflow rejects edits here unless a maintainer applies the
`manual-approval-granted` label.

## Running locally

Use the top-level grader that ships with the repo:

```bash
./grade.sh 1 <your-github-username>
```

That runs `scripts/run_benchmark.sh` against your submission in exactly
the same way CI does — budget validation, functional tests, staged
load, report rendering.

If you want to skip the full grader and only sanity-check your service
against a single endpoint, you can drive it by hand:

```bash
cd challenge-1/submissions/<you> && docker compose up -d --build
TARGET_HOST=http://localhost:8080 python3 -m pytest \
    challenge-1/benchmark/tests.py -v
docker compose -f challenge-1/submissions/<you>/docker-compose.yml down -v
```

That runs the functional tests but skips the load test. For RPS /
p99 numbers, use `./grade.sh`.
