# Hints — Rate-Limited API

## 1. Where to start

Start with a fixed-window counter and the headers. It is the easiest
algorithm and gets you 80% of the functional tests. Iterate from there.

## 2. The "no double-burst" test

A fixed-window counter will fail the test that fires a burst of requests
right around the window boundary. If you see that test fail and others
pass, switch to a sliding-window-counter or a token bucket.

## 3. State storage

You don't need Redis. A `dict[str, State]` is fine for this challenge. The
load test uses around 200 distinct API keys, so the memory footprint is
negligible.

## 4. The lock

If you put a single `threading.Lock` around your state, the load test will
serialize every request and your p99 will be terrible. Either:

- Use `defaultdict` + per-key dict ops (atomic under CPython's GIL for
  simple types).
- Shard the map: `shards = [dict() for _ in range(64)]; shards[hash(key) % 64]`.
- Use an async runtime (no real OS threads = no real lock contention).

## 5. The `Retry-After` header

It is only on the 429 response. The other endpoints don't include it.
The minimum value is 1 second (some clients reject `0`).

## 6. Negative remaining

Clamp `X-RateLimit-Remaining` to a minimum of `0`. The tests check that
the value is `>= 0` on every successful response.

## 7. Performance tuning

The load test in `config.yml` walks five stages, peaking at 1500
concurrent users at saturation, each holding a distinct API key from a
shared pool of 200. The SLO is **p99 ≤ 25 ms, errors < 1 %** (429s count
as success). The fastest implementations don't lock globally and have
zero allocations on the hot path. In Python this means:

- Use `time.monotonic_ns()` instead of `datetime.now()` — it's an order
  of magnitude faster.
- Avoid logging on every request.
- Use FastAPI's `Response` directly with the headers set, not a
  middleware that wraps every response.

## 8. Budget allocation

The whole stack must fit in **1.0 vCPU / 1024 MB**. A single-container
in-process limiter (just a Python `dict` plus a counter) gets the full
1.0 / 1024 and has no inter-process hops to pay for. The moment you add
Redis to share state across replicas, you're trading half a CPU and a
hop's worth of latency for horizontal-scale-out — only worth it if you
need it. For this challenge you don't.
