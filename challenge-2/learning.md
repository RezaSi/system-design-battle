# Learning notes — Rate-Limited API

Rate limiting looks like a one-line problem. It is not. Every choice — the
algorithm, the storage layer, the clock source, where the limit is
checked — shows up in production behaviour.

## The four classic algorithms

### Fixed window counter

Bucket time into fixed windows (e.g. minute-aligned). Increment a counter
on each request. Reject when the counter exceeds the limit. Reset on
window boundary.

- Pro: trivial to implement (`INCR` in Redis or a `dict[key, (count, window_start)]` in memory).
- Con: bursty. A client doing 100 requests at second 59 and another 100 at
  second 61 is doing 200 requests in 2 seconds, but each window saw only 100.

This algorithm fails the "no double-burst across boundaries" test in this
challenge.

### Sliding window log

Store the timestamp of every request in a list. To check, count how many
timestamps fall within the last `window` seconds.

- Pro: exact.
- Con: O(N) memory per key, where N is the limit. For 1M users with a 60
  RPS limit, that is 60M timestamps in memory. Expensive.

### Sliding window counter

Approximate the sliding window by combining the current and previous fixed
window counts, weighted by where we are in the current window.

```text
estimated = count_current + count_previous * (1 - elapsed_in_current / window)
```

- Pro: O(1) per key. Two counters and a window timestamp.
- Con: approximate. The approximation error is bounded and usually fine.

This is what the reference solution uses.

### Token bucket / leaky bucket

Each key has a "bucket" of tokens. Tokens refill at a constant rate up to
a maximum (the burst size). Each allowed request consumes a token. When
the bucket is empty, reject.

- Pro: allows controlled bursts.
- Con: more state per key.

Variants you'll see in real systems: AWS uses token buckets in their SDK
retries. NGINX uses leaky buckets in `limit_req`.

## Where do you check the limit?

Three common places:

- **In the application.** Easy, but you pay a context switch for every
  rejected request. Fine at moderate scale.
- **In a middleware / sidecar.** Envoy and NGINX can rate limit before the
  request hits your app, freeing CPU. They typically push the count to
  Redis.
- **At the edge / CDN.** Cloudflare, Fastly. The cheapest place to reject
  a request is before it leaves the client's TCP socket.

For this challenge, in-process is fine. A single container, no external
dependencies, an in-memory map keyed by API key.

## Where do the response headers come from?

The de-facto standard, also covered by [draft-ietf-httpapi-ratelimit-headers](https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/):

- `X-RateLimit-Limit` — the policy ceiling.
- `X-RateLimit-Remaining` — how many requests are left in the current window.
- `X-RateLimit-Reset` — when the limit resets, as a Unix timestamp.
- `Retry-After` — only on a 429. Seconds the client should wait.

The tests in this challenge check the exact headers.

## Fairness under load

The load test in this challenge fires many distinct API keys in parallel.
If your limiter takes a global lock per request, the p99 will explode
because keys that don't share a bucket are still queueing on the same
lock.

Some ways to avoid this:

- Shard your state across N maps, one lock per shard. A 64-shard map gives
  you 64× less contention with one extra modulo operation.
- Use a lock-free / CAS-based counter (e.g. Python's `multiprocessing.Value`
  is not lock-free; a Go `sync/atomic` integer is; in Python, simple `dict`
  ops under the GIL are atomic at the bytecode level which is usually
  enough).
- Don't lock at all. Pre-allocate the per-key state and use `compare_exchange`
  to update the counter. For the sliding window counter that is two `int`s
  and a comparison — pretty doable.

## Things people get wrong

- Forgetting that `GET /api/quota` should not consume a request itself.
- Returning `Retry-After: 0`. The minimum is `1`.
- Resetting the window on every request (that's not a window, that's a
  cooldown).
- Letting `X-RateLimit-Remaining` go negative. Clamp to 0.

## Further reading

- Stripe's [Scaling your API with rate limiters](https://stripe.com/blog/rate-limiters).
- [System Design Primer: rate limiting](https://github.com/donnemartin/system-design-primer#api-rate-limiting).
- [draft-ietf-httpapi-ratelimit-headers](https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/) for the standard header semantics.
