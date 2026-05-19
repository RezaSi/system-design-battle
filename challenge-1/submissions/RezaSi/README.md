# RezaSi — challenge-1 reference submission

Two containers: a Go service and Redis with AOF persistence. Redis is the
**only** store — URL mappings and the hit counter both live there, and
the named volume on `/data` carries the AOF file across `docker compose
restart`.

| Service | CPU | Memory | Why |
|---------|----:|-------:|-----|
| `app`   | 0.7 | 256 MB | Go is tiny; CPU is the request bottleneck under load |
| `cache` | 0.3 | 768 MB | Holds the entire dataset in RAM, plus AOF buffers |
| **Total** | **1.0** | **1024 MB** | Fits the 1.0 / 1024 budget exactly |

## What's in here

```
main.go             Go service (net/http + go-redis)
go.mod / go.sum     Dependencies (just go-redis/v9)
Dockerfile          Multi-stage build → static binary on alpine:3.20
docker-compose.yml  app + cache (durable Redis with named volume)
```

## How it works

- **Codes** are 7 random base62 characters (~41 bits of entropy). Per
  POST we mint a fresh code and persist it with `SETNX`; on the
  vanishingly rare collision we re-roll up to 8 times. The spec no
  longer requires same-URL-same-code, so there's no reverse
  `url -> code` index and no read-before-write on the POST path.
- **Reads** are a single `GET u:<code>` on Redis → `302` with the
  `Location` header. The hit counter is incremented in a fire-and-forget
  goroutine so the redirect never blocks on it.
- **Metadata** (`GET /api/codes/:code`) does one `MGET u:<code>
  h:<code>` round-trip and returns the result. Metadata lookups never
  touch the hit counter.
- **Durability** comes from Redis with `appendonly yes` /
  `appendfsync everysec`. AOF survives container restarts; worst-case
  data loss is the last ~1 second of writes. The `redisdata` named
  volume on `/data` is what carries that file across
  `docker compose restart`.
- **Health.** `/healthz` returns `503` until the app has successfully
  pinged Redis at least once, then `200` thereafter. This is what the
  grader's 60-second wait loop is designed to tolerate.

## Run locally

```bash
docker compose up --build
# In another terminal:
curl -X POST localhost:8080/shorten -H content-type:application/json \
  -d '{"url":"https://example.com"}'
# {"code":"a1b2c3d","short_url":"http://localhost:8080/a1b2c3d"}

curl -v localhost:8080/a1b2c3d
# 302 Found, Location: https://example.com
```

To grade it against the official tests:

```bash
cd ../../..        # back to repo root
./grade.sh 1 RezaSi
```

## Performance notes

Go on one container with Redis on another should comfortably outpace
the previous Python + MongoDB + Redis reference at the same budget. The
hot read path is one in-memory Redis lookup over a Unix DNS-resolved
TCP connection in the Docker network — sub-millisecond on the runner.

What this submission deliberately *isn't* optimised for:

- **Same-URL deduplication.** Random codes mean each POST minted under
  the same URL gets its own entry; storage grows linearly with POSTs.
  Fine at benchmark scale, not how you'd build a real shortener.
- **Hit-counter accuracy under heavy load.** `INCR` is fire-and-forget
  on the redirect path; if Redis is unreachable for the ~1s of the
  request goroutine, the increment is dropped. The spec allows it.

If you want to beat this on the leaderboard, plausible angles:

- **Collapse to a single container.** Drop the `app -> cache` Docker
  network hop entirely by embedding storage in-process (SQLite on a
  volume, BoltDB, BadgerDB). All 1.0 / 1024 on one process; no
  inter-container TCP.
- **In-process LRU on top of Redis.** Most hot codes will be redirected
  many times. A tiny `lru.Cache` in front of `u:<code>` saves a TCP
  round-trip on the read path.
- **Pipeline the hit counter.** Coalesce `INCR h:<code>` into a Redis
  `MULTI/EXEC` batch flushed every ~50 ms or 256 entries, whichever
  comes first. Drops Redis round-trips per hit by an order of
  magnitude on the saturation stage.
