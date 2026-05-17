# RezaSi — challenge-1 reference submission

Three containers: FastAPI app, MongoDB (source of truth), Redis (cache +
hit-counter buffer). Resource budget allocation:

| Service | CPU | Memory | Why |
|---------|----:|-------:|-----|
| `app`   | 0.55 | 512 MB | Request-bound; async Python wants cycles per concurrent connection |
| `mongo` | 0.35 | 416 MB | WiredTiger cache floor (256 MB) plus working set |
| `cache` | 0.10 |  96 MB | Redis is small; only hot lookups + the hit counter |
| **Total** | **1.00** | **1024 MB** | Fits the 1.0 / 1024 budget exactly |

## What's in here

```
app/main.py             FastAPI service
docker-compose.yml      app + mongo (with named volume) + redis
Dockerfile              python:3.12-slim + dependencies
requirements.txt        fastapi, uvicorn, motor, redis
```

## How it works

- **Codes** are deterministic: `base62(sha256(url)[:8])`, 7 chars. Same
  URL always yields the same code, which makes `POST /shorten` idempotent
  without a read-before-write.
- **Reads** check Redis first (`u:<code>` keys, 1h TTL). Cache miss falls
  back to MongoDB and fills the cache.
- **Hits** increment a Redis counter `h:<code>`. A background task moves
  them to Mongo via `$inc` every ~1 second (`GETDEL` to avoid losing
  in-flight increments). The redirect path never blocks on a Mongo write.
- **Durability**: Mongo writes go to the `mongodata` named volume.
  `docker compose restart` brings the app back up and previously
  shortened URLs still resolve.

## Run locally

```bash
docker compose up --build
# In another terminal:
curl -X POST localhost:8080/shorten -H content-type:application/json \
  -d '{"url":"https://example.com"}'
# {"code":"a1b2c3d","short_url":"http://localhost:8080/a1b2c3d"}
```

To grade it against the official tests:

```bash
cd ../../..        # back to repo root
./grade.sh 1 RezaSi
```

## Performance notes

This reference reliably hits 100% coverage and is fully durable, but
it pays an inter-container hop on every Mongo round-trip. On a typical
run it lands at a few hundred RPS aggregated, with p99 in the
hundreds-of-ms range under the heavier load stages — the cost of a
3-service stack with a real document store on a 1-CPU budget.

If you want to beat this on the leaderboard, plausible angles:

- **Collapse to a single container with SQLite on a volume.** No
  inter-process hops, the whole 1.0 / 1024 budget on one process,
  durability via the named volume. Lowest tail latency by a wide
  margin.
- Drop the async overhead — Go or Rust will easily out-throughput
  Python at the same caps.
- Skip Redis entirely and use an in-process LRU layered on the durable
  store. Saves a hop per resolve and frees up its share of the budget.
- Pipeline writes with `bulkWrite` and tune `writeConcern` if you keep
  Mongo. Batched commits reduce per-request tail latency.
