# Contributing to System Design Battle

Thanks for taking the time to contribute. There are two ways to participate:
**submitting a solution** to an existing challenge, and **proposing a new
challenge**. Both are welcome.

## Table of contents

- [Code of conduct](#code-of-conduct)
- [Submitting a solution](#submitting-a-solution)
  - [The grader contract](#the-grader-contract)
  - [Local testing](#local-testing)
  - [Pull request workflow](#pull-request-workflow)
- [Proposing a new challenge](#proposing-a-new-challenge)
- [Style guidelines](#style-guidelines)
- [Reporting issues](#reporting-issues)

## Code of conduct

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before contributing.

## Submitting a solution

1. **Fork** the repository.
2. **Clone** your fork:

   ```bash
   git clone https://github.com/<your-username>/system-design-battle.git
   cd system-design-battle
   ```

3. **Create a branch**:

   ```bash
   git checkout -b challenge-<n>-solution
   ```

4. **Bootstrap the submission folder**:

   ```bash
   ./create_submission.sh <n>
   ```

   This copies `challenge-<n>/template/` to
   `challenge-<n>/submissions/<your-github-username>/`. The template boots,
   answers `/healthz`, and fails most functional tests. Your job is to fix
   that.

5. **Build your service**. Use any language, framework, or storage. The
   only hard requirements are listed in
   [The grader contract](#the-grader-contract).

6. **Grade locally** with one command:

   ```bash
   ./grade.sh <n> <your-github-username>
   ```

7. **Commit, push, and open a pull request** targeting `main`.

### The grader contract

Every submission must satisfy four things:

1. A `docker-compose.yml` at the root of your submission folder. Running
   `docker compose up --build` from that folder must bring the service up.
2. The primary application service must listen on **port 8080** on the
   host. Map it explicitly: `ports: ["8080:8080"]`.
3. A `healthz` endpoint that returns `200 OK` once the service is ready
   to serve traffic. The grader polls this until it passes (with a 60
   second timeout) before running the benchmark.
4. **Every service** in your compose file must declare `cpus:` and
   `mem_limit:`. The sum across services must fit in the challenge's
   resource budget (see the challenge README — currently 1.0 vCPU /
   1024 MB for both challenges). The grader rejects the submission
   before starting it if any service is missing a cap or the total goes
   over budget.

The grader will then run, in order:

1. `scripts/validate_budget.py` — confirms the resource caps are
   declared and sum to within the budget.
2. `pytest` against `challenge-<n>/benchmark/tests.py` — these are the
   functional / coverage tests. Coverage = passed / total. Below 100%
   fails the correctness gate.
3. `locust --headless` with `challenge-<n>/benchmark/locustfile.py`
   driving a staged load profile (warmup → light → target → heavy →
   saturation). Per-stage RPS, p99, and error rate are extracted from
   `locust_stats_history.csv` to compute the **Capacity at SLO** number
   and the grade.

You **cannot** modify files under `challenge-<n>/benchmark/` from a
submission PR. CI rejects that. If you think a test is wrong, open an
issue.

### Local testing

Use `./grade.sh` from the repo root. It is the same grader CI runs:

```bash
./grade.sh 1 <your-github-username>
```

What happens under the hood:

- Creates `.venv/` and installs `scripts/requirements.txt` (one-time).
- Runs `scripts/validate_budget.py` against your compose file.
- Builds your stack with `docker compose up -d --build`.
- Waits for `/healthz` to return 200 (up to 60s).
- Runs `pytest` and parses the JUnit XML into `coverage.json`.
- Runs `locust --headless` against a staged load shape and parses the
  per-second history CSV into `benchmark.json` (per-stage metrics,
  capacity at SLO, grade, per-endpoint breakdown).
- Renders `benchmark-report.md` — identical to the PR comment.
- Tears everything down with `docker compose down -v`.

All artifacts land in `challenge-<n>/results/<username>/`.

Requirements: Docker (with `docker compose`) and Python 3.11+. Everything
else is auto-installed.

### Pull request workflow

- **One challenge per PR.** If you want to submit to multiple challenges,
  open one PR per challenge.
- **Only modify your own submission folder.** CI enforces this. If you
  need to change something outside `challenge-*/submissions/<username>/`,
  open a separate PR and explain why; a maintainer will need to apply the
  `manual-approval-granted` label.
- **CI runs automatically** and posts a comment on your PR with the
  benchmark numbers. If anything fails, the comment explains what.
- **Auto-merge.** Once the PR passes and sits without changes for the
  cool-down window, it merges automatically and the scoreboard updates
  within a few minutes.

## Proposing a new challenge

New challenges are very welcome. Before you start writing one, open an
issue describing:

- What the service is and why it is interesting.
- The HTTP API contract.
- The intended difficulty and an estimate of how long it should take.
- Why it can't be trivially solved with one cached value.

### Directory layout

Once we agree on the shape, follow this layout — it mirrors what
`challenge-1/` and `challenge-2/` already look like, and the grader is
written to discover challenges by this convention:

```
challenge-<n>/
├── README.md                  Problem statement, API contract, SLO, scoring
├── learning.md                Background reading on the relevant patterns
├── hints.md                   Progressive nudges, no spoilers
├── benchmark/
│   ├── config.yml             Resource budget, SLO, staged load profile
│   ├── tests.py               pytest functional / coverage tests
│   ├── locustfile.py          Locust tasks + StagedLoad shape
│   └── README.md              Short note on what these files are
├── template/                  Minimal scaffold a contributor copies from
│   ├── docker-compose.yml     With cpus + mem_limit so it passes validation
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .dockerignore
│   └── app/                   Source for the scaffold (boots, /healthz works)
├── run_tests.sh               Per-challenge wrapper around the grader
├── SCOREBOARD.md              Empty placeholder; auto-rewritten on merge
└── submissions/               One folder per contributor (initially empty)
```

### benchmark/config.yml — the canonical shape

```yaml
port: 8080
host: http://localhost:8080
health_path: /healthz
warmup_seconds: 3

resource_budget:
  cpus: 1.0
  memory_mb: 1024

slo:
  p99_ms: 25
  error_rate_pct: 1.0

load_stages:
  - { name: "warmup",     users: 20,  spawn_rate: 20,  duration_s: 15, scored: false }
  - { name: "light",      users: 50,  spawn_rate: 30,  duration_s: 20, scored: true  }
  - { name: "target",     users: 150, spawn_rate: 100, duration_s: 20, scored: true  }
  - { name: "heavy",      users: 400, spawn_rate: 250, duration_s: 20, scored: true  }
  - { name: "saturation", users: 800, spawn_rate: 400, duration_s: 20, scored: true  }
```

You can tune `resource_budget`, `slo`, and the stage sizes per
challenge. The grader reads these directly — no other code change is
needed when you add a new challenge.

### Step-by-step checklist for a new challenge

1. Copy `challenge-2/` to `challenge-<n>/` as a starting point. Rename
   internal references.
2. Replace `README.md` with the new spec — endpoints, status codes,
   error bodies, durability requirements, definition of done.
3. Write `benchmark/tests.py` as the executable spec. Every assertion
   should be traceable back to a line in `README.md`.
4. Adjust `benchmark/config.yml`: pick an SLO (`p99_ms`,
   `error_rate_pct`) and a load profile (5 stages is the convention).
   Keep `resource_budget` at `1.0 CPU / 1024 MB` unless you have a
   reason to differ.
5. Write `benchmark/locustfile.py` with a task mix that exercises the
   interesting endpoints. Use the `StagedLoad` shape that reads
   `config.yml` — copy it from `challenge-1/benchmark/locustfile.py`.
6. Update `benchmark/README.md`, `learning.md`, `hints.md` so a fresh
   contributor can solve the challenge without reading source.
7. Make `template/` boot. It must answer `GET /healthz` with `200` and
   declare `cpus:` + `mem_limit:` on every service so it passes the
   budget validator. It is expected to fail most functional tests.
8. Add the new challenge to the **The challenges** table in the root
   `README.md`. Bump the `Challenges-<N>` badge.
9. Smoke-test locally with the bundled template:

   ```bash
   ./create_submission.sh <n>
   ./grade.sh <n> <your-username>
   ```

   The template alone should produce a low-coverage result without
   crashing.

### Guidance for writing a good challenge

- **The API contract goes in `README.md`** — every endpoint, every status
  code, every error case. The functional tests should be a direct
  translation of that contract.
- **Functional tests should be readable.** They serve as the spec.
- **The load test should be aggressive but realistic.** Aim for a
  configuration that distinguishes a good implementation from a sloppy
  one, not one where every submission times out.
- **The template should boot.** It must answer `GET /healthz` so the
  grader can run end-to-end against it, even if it fails most functional
  tests. This lets contributors iterate.
- **Keep the language out of the spec.** The grader is language-agnostic.
  Anything that requires a specific stack should be optional.

## Style guidelines

- Markdown: wrap lines around 80 columns where reasonable, use code fences
  with language tags, and prefer tables over bullet soup for structured
  data.
- Python (grader / tests): format with `ruff format`, lint with
  `ruff check`, type-hint where it adds clarity.
- Shell scripts: `set -euo pipefail` at the top, quote your variables.
- Avoid emoji-heavy commits and PR descriptions. Plain language.

## Reporting issues

Open an issue with a clear title and a reproducible description. Include:

- Which challenge.
- What you ran.
- What you expected.
- What happened instead, including the CI run URL if applicable.

## Contact

- Email: [rezashiri88@gmail.com](mailto:rezashiri88@gmail.com)
- GitHub: [@RezaSi](https://github.com/RezaSi)

Thanks again for contributing.
