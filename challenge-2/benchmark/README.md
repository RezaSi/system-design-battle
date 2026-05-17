# Benchmark — challenge 2

Three files matter:

- `tests.py` — pytest functional tests. The grader counts `passed / total`
  as the **coverage** number. Read this file end-to-end. It is the
  executable spec — every assertion maps to a line in the challenge
  README.
- `locustfile.py` — Locust tasks (`POST /api/work` and `GET /api/quota`
  in a 9:1 ratio) plus a `StagedLoad` shape driven by `config.yml`.
  Each user picks a random API key from a 200-key pool. 429s are
  counted as success because they are the limiter doing its job.
- `config.yml` — resource budget, SLO, staged load profile, health path.

You should not modify any file in this folder when submitting a solution.
The PR-tests workflow rejects edits here unless a maintainer applies the
`manual-approval-granted` label.

## Running locally

```bash
./grade.sh 2 <your-github-username>
```

This runs `scripts/run_benchmark.sh` end-to-end. If you just want to
loop on a single failing test, run pytest directly against an already-up
stack — but you'll lose the capacity-at-SLO and grade numbers:

```bash
cd challenge-2/submissions/<you> && docker compose up -d --build
TARGET_HOST=http://localhost:8080 python3 -m pytest \
    challenge-2/benchmark/tests.py -v
docker compose -f challenge-2/submissions/<you>/docker-compose.yml down -v
```
