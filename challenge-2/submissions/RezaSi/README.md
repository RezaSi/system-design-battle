# RezaSi — challenge-2 reference submission

Single container — a sharded sliding-window-counter rate limiter inside
the FastAPI process. No backing store, no inter-container hops.

| Service | CPU | Memory | Why |
|---------|----:|-------:|-----|
| `app`   | 1.0 | 1024 MB | Pure in-process limiter; nothing to share with |
| **Total** | **1.0** | **1024 MB** | Fits the 1.0 / 1024 budget |

## How it works

- 64 dict-with-lock shards keyed by `hash(api_key) % 64`. A request to
  one key only contends with the ≈1/64 of requests that fall in the
  same shard, so the limiter scales out almost linearly with API-key
  cardinality.
- Window is sliding-counter: each shard stores `[counter, window_start]`
  per key. When the window expires, it rolls forward atomically.
- `Retry-After` is the seconds-until-window-rolls-forward, clamped to
  `[1, 60]`. `X-RateLimit-Remaining` is clamped to `[0, limit]`.

## Run it locally

```bash
docker compose up --build

# In another terminal:
curl -X POST -H 'X-API-Key: demo' localhost:8080/api/work -i
curl       -H 'X-API-Key: demo' localhost:8080/api/quota
```

To grade it against the official tests:

```bash
./grade.sh 2 RezaSi
```

## Ways to beat this reference

- Lock-free per-shard counter using `atomic.Int64` (Go) or `AtomicLong`
  (Java) — Python's GIL makes the dict-with-lock approach near-optimal
  in this language, but a compiled language could push p99 lower.
- A token-bucket variant that returns more precise `Retry-After` values
  by computing time-to-next-token instead of time-to-window-roll.
- A distributed limiter on Redis with `EVAL`/`SCRIPT LOAD` — only worth
  it if you actually need to share state across replicas, which this
  benchmark doesn't (single-container submission). The hop tax will
  show up in p99.
