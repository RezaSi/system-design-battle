"""Reference rate-limited API.

Design:

- Sliding-window counter, per API key.
  estimated = current_count + previous_count * (1 - elapsed / window)
  Two int counters per key, plus a window-start timestamp.

- Sharded state: 64 dicts keyed by `hash(api_key) % 64`. Each shard has
  its own lock so concurrent keys rarely contend.

- Limit is hard-coded to 60 requests / 60 seconds, as the challenge spec
  requires.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Header, Response
from fastapi.responses import JSONResponse


LIMIT = 60
WINDOW = 60.0
NUM_SHARDS = 64


@dataclass
class Bucket:
    window_start: float = 0.0
    current: int = 0
    previous: int = 0


class Limiter:
    def __init__(self) -> None:
        self._shards: list[dict[str, Bucket]] = [dict() for _ in range(NUM_SHARDS)]
        self._locks: list[threading.Lock] = [threading.Lock() for _ in range(NUM_SHARDS)]

    def _shard(self, key: str) -> tuple[dict[str, Bucket], threading.Lock]:
        idx = hash(key) % NUM_SHARDS
        return self._shards[idx], self._locks[idx]

    def _estimate(self, bucket: Bucket, now: float) -> tuple[float, float]:
        """Return (estimated_count, window_start_used)."""
        elapsed = now - bucket.window_start
        if elapsed >= 2 * WINDOW:
            return 0.0, now
        if elapsed >= WINDOW:
            # Roll: current becomes previous, current resets.
            return bucket.current * (1 - (elapsed - WINDOW) / WINDOW), bucket.window_start + WINDOW
        # Estimate with both buckets.
        return (
            bucket.current + bucket.previous * (1 - elapsed / WINDOW),
            bucket.window_start,
        )

    def _maybe_roll(self, bucket: Bucket, now: float) -> None:
        elapsed = now - bucket.window_start
        if elapsed >= 2 * WINDOW:
            bucket.window_start = now
            bucket.current = 0
            bucket.previous = 0
            return
        if elapsed >= WINDOW:
            bucket.window_start = bucket.window_start + WINDOW
            bucket.previous = bucket.current
            bucket.current = 0

    def consume(self, key: str) -> tuple[bool, int, int, int]:
        """Try to consume one request.

        Returns (allowed, limit, remaining, reset_at_epoch_seconds).
        """
        now = time.time()
        shard, lock = self._shard(key)
        with lock:
            bucket = shard.get(key)
            if bucket is None:
                bucket = Bucket(window_start=now)
                shard[key] = bucket
            self._maybe_roll(bucket, now)
            est, _ = self._estimate(bucket, now)
            if est + 1 > LIMIT:
                remaining = max(0, LIMIT - int(est))
                reset_at = int(bucket.window_start + WINDOW)
                return False, LIMIT, remaining, reset_at
            bucket.current += 1
            est_after, _ = self._estimate(bucket, now)
            remaining = max(0, LIMIT - int(est_after))
            reset_at = int(bucket.window_start + WINDOW)
            return True, LIMIT, remaining, reset_at

    def peek(self, key: str) -> tuple[int, int, int]:
        now = time.time()
        shard, lock = self._shard(key)
        with lock:
            bucket = shard.get(key)
            if bucket is None:
                return LIMIT, LIMIT, int(now + WINDOW)
            self._maybe_roll(bucket, now)
            est, _ = self._estimate(bucket, now)
            remaining = max(0, LIMIT - int(est))
            reset_at = int(bucket.window_start + WINDOW)
            return LIMIT, remaining, reset_at


limiter = Limiter()
app = FastAPI()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/api/work")
def work(response: Response, x_api_key: str | None = Header(default=None)) -> JSONResponse:
    if not x_api_key:
        return JSONResponse({"error": "missing api key"}, status_code=400)
    allowed, limit, remaining, reset_at = limiter.consume(x_api_key)
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset_at),
    }
    if not allowed:
        retry_after = max(1, reset_at - int(time.time()))
        headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            {"error": "rate limit exceeded"},
            status_code=429,
            headers=headers,
        )
    return JSONResponse({"ok": True}, headers=headers)


@app.get("/api/quota")
def quota(x_api_key: str | None = Header(default=None)) -> JSONResponse:
    if not x_api_key:
        return JSONResponse({"error": "missing api key"}, status_code=400)
    limit, remaining, reset_at = limiter.peek(x_api_key)
    return JSONResponse(
        {"limit": limit, "remaining": remaining, "reset_at": reset_at}
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
