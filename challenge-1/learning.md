# Learning notes — URL Shortener

URL shorteners look trivial. They aren't. The problem is interesting because
the obvious solution (`SELECT MAX(id) + 1`) doesn't survive concurrent
writes, and the next obvious solution (a UUID base62-encoded) gives you
24-character "short" URLs.

## What problems does a URL shortener actually solve?

- Map a long URL to a short opaque code.
- Look up the original URL given the code, fast, billions of times per day.
- Optionally, track usage. Click counts, geolocation, referrer, whatever.

For this challenge we only care about the first two plus a hit counter.

## Generating short codes

Three classic approaches, each with trade-offs.

### 1. Hash and truncate

```text
code = base62(sha256(url))[:7]
```

- Pro: deterministic — the same URL always produces the same code, which
  makes idempotency free.
- Con: collisions. With 62^7 ≈ 3.5 trillion codes and a few billion URLs,
  the birthday paradox kicks in long before you exhaust the space. You
  need a "check, and if collision, try a longer prefix or salt" loop.

### 2. Random codes

```text
code = base62(random_64_bits())[:7]
```

- Pro: simple, no read before write.
- Con: not idempotent — same URL gets different codes on different POSTs.
  You can fix that with a unique index on `url` and an "on conflict
  return existing row" pattern.

### 3. Counter + base62

```text
code = base62(next_id)
```

- Pro: shortest codes. The first 56 billion URLs fit in 6 characters.
- Con: needs a coordinated counter. A single Postgres `SERIAL` works. So
  does Redis `INCR`. Distributing the counter (Twitter's Snowflake,
  ticket servers) is harder than it looks.

For this challenge, any of the three is fine. The functional tests verify
that the code matches `^[A-Za-z0-9]{4,16}$` and that the same URL produces
the same code on a repeat POST. The load test rewards a fast READ path more
than anything else.

## Idempotency

> "POST /shorten with the same URL twice must return the same code."

This is the most common bug. Two ways to handle it:

- **Read before write.** On every POST, look up the URL first; if it
  exists, return its code with a `200`. Otherwise create one. Two writers
  for the same URL can still race, so wrap the create in a unique index
  on `url` and handle the "already exists" error by reading again.

- **Hash and write.** If your code is `hash(url)`, two writers will write
  the same row twice. Most databases will let you do `INSERT ... ON
  CONFLICT DO NOTHING` and you can ignore the duplicate.

## The READ path is everything

For a real shortener, reads outnumber writes by 100:1 or more. Optimize
accordingly:

- A flat key → value lookup. SQL works. Redis works. An in-memory
  `dict` works for this challenge.
- An HTTP redirect is small. Don't try to render a page; emit the
  `Location` header and let the client follow it.
- Avoid touching disk on the hot path. Counters can be lazy — increment a
  cache, flush to durable storage in batches.

## Counting hits

The spec asks for a `hits` counter, but it does **not** ask for strong
consistency. So you have options:

- **Sync counter on the read path.** Easy, but every read becomes a write.
  Bad for throughput.
- **Async counter.** Queue the increment, return the redirect immediately,
  let a worker batch them. The tests allow a 1-second window before they
  check the counter, so this is the safest design under load.
- **Probabilistic counter.** HyperLogLog or sample-1-in-N. Out of scope here
  but worth knowing about.

## Durability — what the persistence test actually checks

The grader will `docker compose restart` your stack mid-run and verify
that previously shortened URLs still resolve. The realistic shapes are
below. Notice that **every service declares a CPU and memory cap that
sums to the challenge budget (1.0 CPU / 1024 MB)**. The grader rejects
submissions that go over — see the README's Methodology section.

### Single-service with a volume (SQLite, BoltDB, etc.)

```yaml
services:
  app:
    build: .
    cpus: 1.0
    mem_limit: 1024m
    ports: ["8080:8080"]
    volumes:
      - data:/var/lib/app          # SQLite file lives here
volumes:
  data:
```

Simplest. Fine for the easy challenge. Restart-safe because the data
lives on a named volume. Wins on the tight SLO because there is no
inter-container hop on the read path — every redirect is one in-process
SQLite lookup.

### App + a separate database

```yaml
services:
  app:
    build: .
    cpus: 0.6
    mem_limit: 640m
    ports: ["8080:8080"]
    depends_on:
      - db
    environment:
      DATABASE_URL: postgres://shortener:s@db:5432/shortener
  db:
    image: postgres:16-alpine
    cpus: 0.4
    mem_limit: 384m
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```

This is what most real shorteners look like. Slightly more startup time;
make sure your app returns `503` from `/healthz` while it waits for the
DB and `200` once it's reachable. The grader polls `/healthz` for up to
60 seconds and only cares about the first `200` it sees.

### App + cache + database (the reference)

```yaml
services:
  app:
    build: .
    cpus: 0.55
    mem_limit: 512m
    ports: ["8080:8080"]
    depends_on:
      - mongo
      - cache
    environment:
      MONGO_URL: mongodb://mongo:27017
      REDIS_URL: redis://cache:6379
  mongo:
    image: mongo:7
    cpus: 0.35
    mem_limit: 416m
    command: ["mongod", "--wiredTigerCacheSizeGB", "0.25"]
    volumes:
      - mongodata:/data/db
  cache:
    image: redis:7-alpine
    cpus: 0.10
    mem_limit: 96m
    command: ["redis-server", "--save", "", "--appendonly", "no",
              "--maxmemory", "64mb", "--maxmemory-policy", "allkeys-lru"]
volumes:
  mongodata:
```

Reads hit Redis first (microsecond latency), fall back to MongoDB on a
miss and fill the cache. Writes go straight to MongoDB and invalidate
the cache. Hits counter is batched in Redis and flushed to MongoDB
every second. This is roughly how real-world shorteners are built and
is what the reference submission ships. Under a tight SLO it will pay a
hop-latency tax compared to the single-service design — that's the
trade-off, and the leaderboard's job is to make it visible.

## Things people get wrong

- Generating codes longer than 16 characters (the regex rejects them).
- Returning `200` on first create instead of `201`, or vice-versa. Both are
  accepted; the tests don't distinguish — but pick one consistently.
- Forgetting that `/healthz` must answer **before** any data is loaded. The
  grader's wait loop only runs for 60 seconds.
- Using a fresh code per POST. The idempotency test will fail.

## Further reading

- ["URL Shortener System Design"](https://systeminterview.com/) (any
  reputable source covers this).
- High Scalability: the [Bit.ly architecture](http://highscalability.com/blog/2014/7/14/bitly-lessons-learned-building-a-distributed-system-that-han.html).
- [Stripe's idempotency keys post](https://stripe.com/blog/idempotency).
