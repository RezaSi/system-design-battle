# Hints — URL Shortener

Try to solve the challenge before reading these. Each hint progressively
gives away more of the solution.

## 1. Where do I start?

Copy the template into your submission folder, run `docker compose up`, and
hit `/healthz`. The template boots and answers that one endpoint and
nothing else. Read `benchmark/tests.py` end-to-end — it is the spec.

You'll quickly notice **two** things you must build:

1. The HTTP endpoints described in the README.
2. A backing store with a Docker volume. The grader restarts the stack
   mid-run; in-memory storage will fail
   `test_data_persists_across_docker_compose_restart`.

## 2. The shortening algorithm

You don't need anything fancy. Pick one of these, in order from easiest to
hardest:

- A SHA-256 of the URL, base62-encoded, take the first 7 characters.
- A monotonic counter, base62-encoded.
- 6 random base62 characters from a CSPRNG.

The first one is what the reference submission uses because it gives
idempotency for free.

## 3. Idempotency

If the same URL comes in twice, the second response must include the same
code. With a hash-based code that's automatic. With a random code, store
`url -> code` in a map and look it up before generating a new one.

## 4. The hit counter

Don't make `GET /:code` write to the database synchronously. The load test
will hammer that endpoint and the write contention will tank your RPS.
Instead, increment in memory (or in Redis) and let it drift; the tests
allow a 1-second slack window.

## 5. Health check

`GET /healthz` must return 200 within 60 seconds of `docker compose up`.
If you depend on Postgres or Redis, either:

- Use a Compose `healthcheck:` and a `depends_on: service: condition:
  service_healthy`, or
- Have your app return 503 from `/healthz` until the database connection
  is ready. The grader only cares about the *first* 200 it sees.

## 6. Performance

The load test in `benchmark/config.yml` walks five stages: a 20-user
warmup, then 50, 150, 400, and 800 concurrent users. Roughly 70% of the
traffic is reads (resolves), 25% writes (shortens), 5% metadata lookups.
The scoreboard reports your **aggregated RPS** and **aggregated p99**
across the whole run, plus the per-stage breakdown for diagnostics.

The fastest submissions will:

- Pick storage that does not require an extra network hop on the hot
  path. SQLite or an in-process embedded DB with a named volume keeps
  the read path sub-millisecond and saves you a whole container's
  worth of budget.
- If you do use a separate DB, put a cache on the resolve path (Redis,
  Memcached, or in-process LRU). Reading from disk on every redirect
  is a guaranteed loss.
- Avoid `print` / verbose logging on the hot path.
- Pick an async runtime instead of a process-per-request server, given
  the tight CPU budget. One async event loop on 0.6 vCPU handles
  thousands of redirects per second; four `gunicorn` workers on the
  same budget will starve each other.
- Batch the hits counter rather than incrementing on every read.

## 7. Resource budget — choose carefully

Every service you add must declare `cpus:` and `mem_limit:`, and the sum
across services must fit in **1.0 vCPU / 1024 MB**. The grader rejects
your submission with a clear error if you go over.

Typical splits you'll see in real submissions:

| Architecture | app | db | cache | When it wins |
|--------------|----:|---:|------:|--------------|
| Monolith + SQLite on a volume | 1.0 / 1024 | (in-process) | (in-process) | Low latency, small data |
| App + Redis with persistence | 0.7 / 768 | (n/a) | 0.3 / 256 | Hot read path, no SQL needs |
| App + DB | 0.6 / 640 | 0.4 / 384 | (in-process LRU) | Relational queries |
| App + DB + cache | 0.55 / 512 | 0.35 / 416 | 0.10 / 96 | Heavy traffic, mostly reads |

The reference submission in `submissions/RezaSi/` uses the last split.

## 8. If something feels wrong

The grader runs your stack in a fresh container, so any "works on my
machine" issue usually comes from:

- A hard-coded path inside the Docker image that doesn't exist on Linux.
- Listening on `127.0.0.1` instead of `0.0.0.0` — the port won't be
  reachable from outside the container.
- Not mapping port `8080:8080` in `docker-compose.yml`.
- Forgetting `cpus:` or `mem_limit:` on a service. The grader prints
  the per-service budget table at the top of the run; if it says
  `missing` next to any service, fix that first.
