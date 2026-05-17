# System Design Battle

[![PR Tests](https://img.shields.io/github/actions/workflow/status/RezaSi/system-design-battle/pr-tests.yml?branch=main&label=PR%20tests)](https://github.com/RezaSi/system-design-battle/actions/workflows/pr-tests.yml)
[![Challenges](https://img.shields.io/badge/Challenges-2-brightgreen)](https://github.com/RezaSi/system-design-battle)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A practical playground for system design. You ship a real service as a
`docker-compose` stack, CI runs it under a fixed resource budget, hits
it with [Locust](https://locust.io) through a staged load curve, and
posts three numbers back on the PR: **coverage**, **throughput**, and
**p99 latency**. The leaderboard sorts submissions on those three
axes — that's the entire scoring system.

No diagrams on a whiteboard. No "imagine a load balancer". Real code,
real containers, real numbers on a real PR.

## How it works

1. Pick a challenge from the list below.
2. Build a service that matches its API spec. Use any language, any
   framework, any database — as long as it ships as a `docker-compose.yml`
   listening on port `8080`.
3. Drop your code into `challenge-N/submissions/<your-github-username>/`.
4. Open a pull request. CI spins up your stack, runs the functional
   coverage tests, then a Locust load test, and posts the results as a
   comment on your PR.
5. After the run is green and the cool-down expires, the PR auto-merges
   and the scoreboard updates.

What the grader reports back on every PR:

- **Coverage** — percentage of functional tests that passed.
  Correctness is the gate; a submission that fails any functional test
  ranks below one that passes them all, regardless of throughput.
- **RPS** — aggregated requests/second across the whole staged load
  test (warmup + light → saturation). Higher is better.
- **p99** — 99th-percentile latency across the whole run, in
  milliseconds. Lower is better. Used to break ties between submissions
  with similar coverage and throughput.

## Methodology

Two constraints turn this from a microbench into a system-design
exercise.

### 1. Stack-wide resource budget

Every challenge declares a budget — for example, **1.0 vCPU and
1024 MB total** for the whole `docker-compose` stack. Every service in
your `docker-compose.yml` must declare both `cpus:` and `mem_limit:`,
and the sum across services must fit inside the budget. The grader
validates this before it even starts your stack:

```yaml
services:
  app:
    cpus: 0.6
    mem_limit: 640m
  db:
    cpus: 0.3
    mem_limit: 320m
  cache:
    cpus: 0.1
    mem_limit: 64m
```

How you slice the budget across services is up to you. That allocation
is part of the design — and the cap is what makes microservice vs.
monolith vs. SQLite-on-a-volume an actual trade-off instead of a
preference.

### 2. Staged load profile

The Locust load test walks through five stages: a short warmup, then
*light → target → heavy → saturation*. Stage sizes are challenge-specific.
The per-stage breakdown is in the PR report so you can see the whole
load curve, but the scoreboard ranks on the **aggregated** RPS and p99
across the whole run — gaming a single stage doesn't help.

### Hardware caveat

All numbers on this scoreboard come from GitHub Actions `ubuntu-latest`
runners. Your laptop will produce different absolute numbers, but the
internal ranking is consistent because every submission is re-run on
the same CI runner on every merge.

## Leaderboards

Top 10 per challenge, updated automatically when PRs are merged. See
each challenge's `SCOREBOARD.md` for the full table. Sorted by coverage
(desc), then RPS (desc), then p99 (asc).

<!-- BEGIN_MAIN_LEADERBOARD -->
### Challenge 1 — URL Shortener

| Rank | Developer | Coverage | RPS | p99 (ms) |
|:---:|:---|:---:|---:|---:|
| 1 | [RezaSi](https://github.com/RezaSi) | 100.0% | 1067.5 | 2300 |

### Challenge 2 — Rate-Limited API

| Rank | Developer | Coverage | RPS | p99 (ms) |
|:---:|:---|:---:|---:|---:|
| 1 | [RezaSi](https://github.com/RezaSi) | 100.0% | 2308.3 | 790 |
<!-- END_MAIN_LEADERBOARD -->

## The challenges

| # | Challenge | Difficulty | Theme |
|---|-----------|:----------:|-------|
| 1 | [URL Shortener](./challenge-1) | Easy | HTTP service + storage |
| 2 | [Rate-Limited API](./challenge-2) | Easy-Medium | Per-key limits, fairness |

More will be added over time.

Each challenge directory contains:

- `README.md` — problem statement and API contract.
- `learning.md` — background reading on the relevant design patterns.
- `hints.md` — gentle nudges if you get stuck.
- `benchmark/` — functional tests (`tests.py`) and Locust load test
  (`locustfile.py`) that the grader runs.
- `template/` — a minimal working skeleton (`docker-compose.yml`,
  `Dockerfile`, source). It boots and answers `/healthz`, but fails most
  functional tests. Copy this into your submission folder and build from
  there.
- `SCOREBOARD.md` — auto-generated ranking of submissions.
- `submissions/<username>/` — where your solution lives.

## Run it locally

You need two things on your machine: **Docker** and **Python 3.11+**.
Everything else (pytest, locust, pyyaml) is installed automatically into a
local `.venv/` the first time you run the grader.

### 1. Fork and clone

```bash
git clone https://github.com/<your-username>/system-design-battle.git
cd system-design-battle
```

### 2. Bootstrap a submission

```bash
./create_submission.sh 1
```

This copies `challenge-1/template/` to
`challenge-1/submissions/<your-github-username>/`. Open that folder in your
editor and start hacking on `app/main.py`.

### 3. Run your service

```bash
cd challenge-1/submissions/<your-github-username>
docker compose up --build
```

In another terminal, smoke-test it:

```bash
curl -X POST localhost:8080/shorten \
     -H 'content-type: application/json' \
     -d '{"url":"https://example.com"}'
```

### 4. Benchmark your submission with one command

From the repo root:

```bash
./grade.sh 1 <your-github-username>
```

That single command:

- Creates `.venv/` and installs the grader's Python dependencies (one-time).
- Validates that your `docker-compose.yml` declares `cpus:` /
  `mem_limit:` on every service and the total fits in the challenge
  budget.
- Builds and starts your stack with `docker compose up -d --build`.
- Waits for `/healthz` to return `200`.
- Runs the functional tests (`pytest`) — that's your **coverage** number.
- Drives the service through the staged load profile in
  `benchmark/config.yml` (~95 seconds total — warmup, light, target,
  heavy, saturation), captures per-second stats, and reports
  **aggregated RPS** and **aggregated p99**.
- Prints a `benchmark-report.md` summary identical to what CI will post
  on your PR.
- Tears the stack down with `docker compose down -v`.

Try it on the reference submission first to see what a green run looks
like:

```bash
./grade.sh 1 RezaSi
```

Output goes to `challenge-1/results/<username>/`.

### 5. Submit

```bash
git checkout -b challenge-1-solution
git add challenge-1/submissions/<your-github-username>
git commit -m "challenge-1: URL shortener submission"
git push origin challenge-1-solution
```

Open a pull request to `main`. The CI does the rest. Same grader, same
numbers. After the run is green and the cool-down expires, the PR
auto-merges and the scoreboard updates.

## Repository layout

```
system-design-battle/
├── .github/workflows/        pr-tests, auto-merge, update-scoreboards
├── challenge-1/              URL shortener
│   ├── README.md             Problem statement + API contract
│   ├── learning.md           Background reading
│   ├── hints.md              Progressive nudges
│   ├── benchmark/
│   │   ├── config.yml        Resource budget + staged load profile
│   │   ├── tests.py          Functional tests (the executable spec)
│   │   ├── locustfile.py     Staged load shape + task mix
│   │   └── README.md
│   ├── template/             Minimal working scaffold to fork from
│   ├── submissions/          One folder per contributor
│   ├── SCOREBOARD.md         Auto-generated leaderboard
│   └── run_tests.sh          Local wrapper around scripts/run_benchmark.sh
├── challenge-2/              Rate-limited API (same shape)
├── scripts/
│   ├── run_benchmark.sh      The grader; same script CI runs
│   ├── validate_budget.py    Stack-wide resource budget validator
│   ├── parse_results.py      JUnit + Locust → benchmark.json + report.md
│   ├── update_scoreboard.sh  Re-runs every submission, rewrites SCOREBOARD.md
│   ├── generate_main_scoreboard.py   Mirrors top-10 into README
│   └── requirements.txt      pytest, locust, requests, pyyaml
├── grade.sh                  One-command local grader  ← start here
├── create_submission.sh      Bootstrap a new submission from a template
├── CONTRIBUTING.md           How to submit a solution and add a challenge
└── README.md                 This file
```

## A note on the grader

The grader is deliberately not language-aware. It only cares that
`docker compose up` brings up a service that answers on port `8080` and
that the service passes the functional tests. Use Go, Python, Rust, Node,
Java, Elixir — whatever. The only thing the leaderboard compares is
coverage, aggregated RPS, and aggregated p99.

The benchmark parameters (resource budget, staged load profile,
target host) live in each challenge's `benchmark/config.yml`. CI uses
those exact values, so your local run and the PR result match.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version:

- **Submitting a solution** — only touch
  `challenge-N/submissions/<your-github-username>/`. CI rejects PRs that
  modify other directories without maintainer approval.
- **Adding a new challenge** — open an issue first to discuss scope and
  difficulty, then follow the template layout described in
  `CONTRIBUTING.md`.

## License

MIT, see [LICENSE](LICENSE).
