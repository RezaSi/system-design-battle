[View the scoreboard](SCOREBOARD.md)

# Challenge 2 — Rate-Limited API

**Difficulty:** Easy-Medium &nbsp;•&nbsp; **Theme:** Per-key rate limits, fairness

You'll ship a small HTTP service that enforces a per-API-key rate limit.
The interesting part is not the endpoint that does work — it's making the
limiter fair, accurate, and cheap to evaluate.

## Definition of done

The grader runs **17 functional tests** plus a 60-second Locust load test
with 200 concurrent distinct API keys. A submission is "done" when:

- [ ] `docker compose up` brings the service up and `GET /healthz` returns
      `200` within 60 seconds.
- [ ] Missing or empty `X-API-Key` on either endpoint returns `400` with
      body `{"error": "..."}`.
- [ ] `POST /api/work` returns `200` with body `{"ok": true}` and the
      three rate-limit headers (`X-RateLimit-Limit`, `-Remaining`, `-Reset`).
- [ ] `GET /api/quota` returns `{"limit", "remaining", "reset_at"}` and
      does **not** itself count against the limit.
- [ ] After ~60 requests inside one window, further requests get `429`
      with body `{"error": "rate limit exceeded"}` and a `Retry-After`
      header between 1 and 60.
- [ ] `Retry-After` is **only** emitted on 429 responses.
- [ ] `X-RateLimit-Remaining` is non-negative and never *rises* within a
      window. It hits 0 before any `429`.
- [ ] Two distinct API keys do not influence each other's quotas.
- [ ] The window is sliding from the client's point of view (a naive
      fixed-window counter fails the "no double burst across the
      window boundary" test).

The full executable spec lives in
[`benchmark/tests.py`](benchmark/tests.py).

## API contract

### `GET /healthz`

`200 OK` once ready. Body ignored.

### `POST /api/work`

The "work" endpoint. It must be subject to rate limiting based on the
`X-API-Key` header.

- Missing `X-API-Key` header → `400 Bad Request` with `{"error": "missing api key"}`.
- Allowed request → `200 OK` with `{"ok": true}` and these response headers:
  - `X-RateLimit-Limit: <integer requests per window>`
  - `X-RateLimit-Remaining: <integer>`
  - `X-RateLimit-Reset: <unix epoch seconds at which the window resets>`
- Rate-limited request → `429 Too Many Requests` with body
  `{"error": "rate limit exceeded"}` and the same three headers plus:
  - `Retry-After: <seconds until next allowed request, integer>`

The request body for `POST /api/work` is irrelevant — accept anything and
respond as above. The service is allowed to do nothing useful on a success.

### `GET /api/quota`

Inspect the current state for an API key without consuming a request.

- Missing `X-API-Key` → `400 Bad Request`.
- Otherwise `200 OK` with:

  ```json
  {
    "limit": 60,
    "remaining": 42,
    "reset_at": 1715797600
  }
  ```

`GET /api/quota` itself does **not** count against the limit.

## The rate limit policy

- The default policy is **60 requests per 60 seconds, per API key**.
  Use any algorithm — fixed window, sliding window, token bucket, leaky
  bucket. The tests don't care which, as long as the visible behaviour
  matches.
- The window must be **sliding from the point of view of a client**: a
  client that does 60 requests in second 1 must not be able to do 60 more
  in second 2. (A naive fixed-window-counter implementation will fail
  one of the tests for this reason.)
- A 1-request slack on either side is acceptable. The tests allow it.
- Different API keys are independent. One key burning its quota must not
  affect another.

## Constraints

- Service listens on **port 8080**.
- The limit must be configurable, but for the grader it is always 60/60s.
  The tests do not change it, so a hard-coded constant is fine.
- **Resource budget — 1.0 vCPU / 1024 MB across the whole stack.**
  Every service in your `docker-compose.yml` must declare both `cpus:`
  and `mem_limit:` and the sum must fit. The grader rejects the
  submission before starting it if it doesn't. A single-container
  in-process limiter gets the whole 1.0 / 1024; a distributed limiter
  that needs Redis to share state has to split it.
- The service can be a single container or a multi-service stack. Either
  is acceptable, but multi-service designs pay an inter-process hop
  that shows up immediately in p99.

## How submissions are scored

Three numbers, all reported on the PR comment and in `SCOREBOARD.md`:

1. **Coverage** — percentage of `benchmark/tests.py` that passed.
   Below 100% fails the correctness gate (grade **F**).
2. **Capacity** — highest sustained RPS that held the SLO. For this
   challenge the SLO is **p99 ≤ 25 ms, error rate < 1 %**, with **429
   responses counted as success** (they are the limiter doing its job
   correctly; only 5xx / connection errors are real failures).
3. **Grade** — S / A / B / C / D / F based on which scored stage was
   the deepest one to hold the SLO. The load test uses many distinct
   API keys at high concurrency, so a limiter behind a global lock
   shows up in the p99 immediately.

## Quick start

```bash
./create_submission.sh 2
cd challenge-2/submissions/<your-github-username>
docker compose up --build

curl -X POST -H 'X-API-Key: demo' localhost:8080/api/work -i
curl       -H 'X-API-Key: demo' localhost:8080/api/quota -i
```

When you're ready:

```bash
cd challenge-2
./run_tests.sh
```

See [`learning.md`](learning.md) for algorithm trade-offs and
[`hints.md`](hints.md) if you get stuck.
