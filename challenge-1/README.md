[View the scoreboard](SCOREBOARD.md)

# Challenge 1 — URL Shortener

**Difficulty:** Easy &nbsp;•&nbsp; **Theme:** HTTP service + storage

You'll build a Bitly-style URL shortener. Submit a `docker-compose.yml`
that brings up an HTTP service on port `8080`. The grader will check
correctness against the API contract below, then load-test it.

## Definition of done

The grader runs **18 functional tests** plus a 60-second Locust load test.
A submission is "done" when:

- [ ] `docker compose up` brings the service up and `GET /healthz` returns
      `200` within 60 seconds.
- [ ] `POST /shorten` returns **`201 Created`** with
      `{"code", "short_url"}` on success.
- [ ] Codes match `^[A-Za-z0-9]{4,16}$` and are case-sensitive when resolved.
- [ ] Invalid input (malformed JSON, missing `url`, empty `url`, non-http
      scheme) returns `400` with body `{"error": "..."}`.
- [ ] `GET /:code` returns `302` with the original URL in `Location`, or
      `404` with `{"error": "not found"}`.
- [ ] `GET /api/codes/:code` returns `{"code", "url", "hits"}` and does
      **not** itself bump `hits`. Only `GET /:code` does.
- [ ] `hits` monotonically increases (~1s eventual consistency allowed).
- [ ] **Data survives a `docker compose restart`** of the submission's
      stack. The URL→code mapping must be durable. In-memory storage will
      not pass this challenge.

The full executable spec lives in
[`benchmark/tests.py`](benchmark/tests.py).

## API contract

All requests and responses use JSON unless otherwise noted.

### `GET /healthz`

Return `200 OK` once the service is ready to serve traffic.

Response body (any JSON value or empty body, only the status code matters):

```json
{"status": "ok"}
```

### `POST /shorten`

Create or fetch a short code for a URL.

Request:

```json
{ "url": "https://example.com/some/long/path" }
```

Response — `201 Created` on success:

```json
{ "code": "abc123", "short_url": "http://localhost:8080/abc123" }
```

Rules:

- `code` is alphanumeric, 4–16 characters long. `^[A-Za-z0-9]{4,16}$` is the
  exact regex the tests will check.
- Whether the same input URL maps to the same code on a repeat POST is
  **up to you**. Deterministic-by-hash, random per-request, and
  `INSERT … ON CONFLICT` are all valid designs.
- Reject obviously invalid input with `400 Bad Request` and a JSON body
  `{"error": "..."}` if:
  - the body isn't valid JSON,
  - `url` is missing or empty,
  - `url` does not start with `http://` or `https://`.

### `GET /:code`

Resolve a short code.

- `302 Found` with the original URL in the `Location` header on a hit.
- `404 Not Found` with `{"error": "not found"}` on a miss.

The grader follows the redirect target's value (not the actual destination),
so you do not need to make outbound requests.

### `GET /api/codes/:code`

Return metadata for a code. This is the "stat lookup" endpoint.

- `200 OK` with:

  ```json
  { "code": "abc123", "url": "https://example.com/...", "hits": 17 }
  ```

  `hits` is the number of times `GET /:code` has been resolved successfully
  for this code, and must monotonically increase. Eventually consistent is
  fine; the tests allow a 1-second slack window.

- `404 Not Found` with `{"error": "not found"}` for unknown codes.

## Constraints

- The service must listen on **port 8080** on the host. Map it in your
  `docker-compose.yml` with `ports: ["8080:8080"]`.
- Health check on `GET /healthz` must return `200` within 60 seconds of
  `docker compose up`. Anything that takes longer is treated as a failure.
- **Resource budget — 1.0 vCPU / 1024 MB across the whole stack.**
  Every service in your compose file must declare `cpus:` and
  `mem_limit:`. The sum across services must fit inside the budget; the
  grader rejects the submission before starting it if it doesn't. How
  you split the budget is up to you. A single-container monolith gets
  it all; a 3-service stack has to make trade-offs.
- **Data must be durable.** The grader runs `docker compose restart`
  partway through and re-checks that previously shortened URLs still
  resolve. You may use any stack — Postgres, Redis with persistence,
  MongoDB, SQLite on a named volume, MySQL, FoundationDB, whatever — as
  long as it survives an app-container restart. An in-memory map alone
  will fail this challenge.
- The grader does **not** delete volumes between phases. After the full
  run finishes, it does call `docker compose down -v` to clean up.
- A read-heavy cache (Redis, Memcached, in-process LRU) on top of the
  durable store is encouraged and expected to help on the load test —
  but every service still eats into the same 1.0 / 1024 budget.

## How submissions are scored

Three numbers, all reported on the PR comment and in `SCOREBOARD.md`:

1. **Coverage** — percentage of the functional tests in
   [`benchmark/tests.py`](benchmark/tests.py) that passed.
2. **RPS** — aggregated requests-per-second across the whole staged
   load test (warmup + light → saturation; see `benchmark/config.yml`).
3. **p99 (ms)** — 99th-percentile latency across the whole run.

The scoreboard sorts by **coverage (desc) → RPS (desc) → p99 (asc)**.
Correctness wins ties first; throughput wins between correct
submissions; tail latency is the final tiebreaker.

## Quick start

```bash
./create_submission.sh 1
cd challenge-1/submissions/<your-github-username>
docker compose up --build
```

Hit it:

```bash
curl -X POST localhost:8080/shorten -H 'content-type: application/json' \
  -d '{"url":"https://example.com"}'
# {"code":"a1b2c3","short_url":"http://localhost:8080/a1b2c3"}

curl -v localhost:8080/a1b2c3
# 302 Found, Location: https://example.com
```

When you're ready, run the same grader CI will run:

```bash
cd challenge-1
./run_tests.sh
```

## Hints and learning

- [`learning.md`](learning.md) — design notes: short-code generation,
  collision handling, hit counters, durability shapes.
- [`hints.md`](hints.md) — gentle nudges if you're stuck.
