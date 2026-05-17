"""Functional tests for challenge-2 (Rate-Limited API).

The TARGET_HOST environment variable points at the running submission's
HTTP base URL (default: http://localhost:8080). Each test maps directly
to a clause of the API contract spelled out in challenge-2/README.md.

The rate limit policy is hard-coded for the grader: 60 requests per
60 seconds per API key.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import requests


HOST = os.environ.get("TARGET_HOST", "http://localhost:8080").rstrip("/")
LIMIT = 60
WINDOW = 60


def _key() -> str:
    return f"k_{uuid.uuid4().hex}"


def _work(api_key: str | None) -> requests.Response:
    headers = {"X-API-Key": api_key} if api_key is not None else {}
    return requests.post(f"{HOST}/api/work", headers=headers, timeout=5)


def _quota(api_key: str | None) -> requests.Response:
    headers = {"X-API-Key": api_key} if api_key is not None else {}
    return requests.get(f"{HOST}/api/quota", headers=headers, timeout=5)


def _assert_error_body(resp: requests.Response) -> None:
    body = resp.json()
    assert isinstance(body, dict), f"expected JSON object, got {body!r}"
    assert "error" in body, f"expected 'error' key, got {body!r}"
    assert isinstance(body["error"], str) and body["error"], (
        f"'error' must be a non-empty string, got {body['error']!r}"
    )


def _assert_rate_headers(resp: requests.Response) -> None:
    """Limit + Remaining + Reset must be present and sane on every response."""
    assert resp.headers.get("X-RateLimit-Limit") == str(LIMIT), resp.headers
    rem = int(resp.headers["X-RateLimit-Remaining"])
    assert 0 <= rem <= LIMIT, f"Remaining={rem} outside [0, {LIMIT}]"
    reset_at = int(resp.headers["X-RateLimit-Reset"])
    now = int(time.time())
    # reset_at can equal `now` exactly at the window boundary; allow it.
    # Upper bound: window length + 2s slack for clock skew.
    assert now - 1 <= reset_at <= now + WINDOW + 2, (
        f"X-RateLimit-Reset={reset_at} now={now} not within [now-1, now+{WINDOW}+2]"
    )


# ---------------------------------------------------------------------------
# GET /healthz
# ---------------------------------------------------------------------------


def test_healthz_returns_200():
    resp = requests.get(f"{HOST}/healthz", timeout=5)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Auth header validation
# ---------------------------------------------------------------------------


def test_work_rejects_missing_api_key():
    resp = _work(None)
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


def test_quota_rejects_missing_api_key():
    resp = _quota(None)
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


def test_work_rejects_empty_api_key():
    """An X-API-Key with an empty value should not authenticate the request."""
    resp = _work("")
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


# ---------------------------------------------------------------------------
# POST /api/work — happy path
# ---------------------------------------------------------------------------


def test_work_returns_200_and_rate_headers():
    resp = _work(_key())
    assert resp.status_code == 200, resp.text
    _assert_rate_headers(resp)
    assert resp.json() == {"ok": True}


def test_work_first_call_remaining_is_limit_minus_one():
    """The very first call against a fresh key consumes exactly one slot.

    Spec allows 1-request slack, so accept Remaining == LIMIT-1 (strict)
    or LIMIT-2 (pre-deducting implementations).
    """
    resp = _work(_key())
    rem = int(resp.headers["X-RateLimit-Remaining"])
    assert rem in (LIMIT - 1, LIMIT - 2), f"Remaining={rem} after one call"


# ---------------------------------------------------------------------------
# GET /api/quota
# ---------------------------------------------------------------------------


def test_quota_returns_initial_state_for_fresh_key():
    resp = _quota(_key())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["limit"] == LIMIT, body
    assert body["remaining"] == LIMIT, body
    now = int(time.time())
    assert now - 1 <= body["reset_at"] <= now + WINDOW + 2, body


def test_quota_does_not_consume_requests():
    """Spec: GET /api/quota does not count against the limit."""
    api_key = _key()
    for _ in range(5):
        _quota(api_key)
    resp = _work(api_key)
    rem = int(resp.headers["X-RateLimit-Remaining"])
    assert rem in (LIMIT - 1, LIMIT - 2), (
        f"After 5 quota pings, Remaining={rem} (expected {LIMIT - 1} or {LIMIT - 2})"
    )


def test_quota_reflects_consumed_requests():
    api_key = _key()
    for _ in range(10):
        _work(api_key)
    time.sleep(0.1)
    body = _quota(api_key).json()
    # 10 consumed + at most 1 of slack = remaining in [LIMIT-11, LIMIT-9]
    assert LIMIT - 11 <= body["remaining"] <= LIMIT - 9, body


# ---------------------------------------------------------------------------
# Counting behaviour
# ---------------------------------------------------------------------------


def test_remaining_is_monotonically_non_increasing_within_window():
    api_key = _key()
    seen = []
    for _ in range(8):
        resp = _work(api_key)
        assert resp.status_code == 200, resp.text
        seen.append(int(resp.headers["X-RateLimit-Remaining"]))
    # No request should ever report a HIGHER remaining than the previous
    # one (we don't refill in <1s for a 60s window).
    for prev, cur in zip(seen, seen[1:]):
        assert cur <= prev, f"remaining went UP: {seen}"
    # And we should be moving — not stuck at the same value forever.
    assert seen[-1] < seen[0], f"remaining never moved: {seen}"


def test_remaining_never_negative():
    api_key = _key()
    for _ in range(LIMIT + 5):
        resp = _work(api_key)
        rem = int(resp.headers["X-RateLimit-Remaining"])
        assert rem >= 0


def test_limit_is_enforced():
    api_key = _key()
    statuses = [_work(api_key).status_code for _ in range(LIMIT + 10)]
    # The tail of the burst must produce 429s.
    assert statuses.count(429) >= 5, statuses


# ---------------------------------------------------------------------------
# 429 response shape
# ---------------------------------------------------------------------------


def test_429_response_shape_and_headers():
    api_key = _key()
    # Burn through the quota
    for _ in range(LIMIT + 2):
        _work(api_key)
    resp = _work(api_key)
    assert resp.status_code == 429, resp.text
    _assert_error_body(resp)
    assert resp.json().get("error") == "rate limit exceeded"
    _assert_rate_headers(resp)
    # When throttled the Remaining must be exactly 0.
    assert int(resp.headers["X-RateLimit-Remaining"]) == 0
    retry_after = int(resp.headers.get("Retry-After", "0"))
    assert 1 <= retry_after <= WINDOW + 5, (
        f"Retry-After={retry_after} outside [1, {WINDOW + 5}]"
    )


def test_non_throttled_responses_do_not_include_retry_after():
    """Retry-After is meaningful only on a 429.

    Allowing it on a 200 confuses well-behaved clients.
    """
    resp = _work(_key())
    assert resp.status_code == 200, resp.text
    assert "Retry-After" not in resp.headers, dict(resp.headers)


# ---------------------------------------------------------------------------
# Isolation between API keys
# ---------------------------------------------------------------------------


def test_keys_are_isolated():
    a, b = _key(), _key()
    for _ in range(LIMIT + 5):
        _work(a)
    resp = _work(b)
    assert resp.status_code == 200, resp.text
    assert int(resp.headers["X-RateLimit-Remaining"]) >= LIMIT - 2


def test_one_keys_429s_do_not_leak_into_another_keys_quota():
    a, b = _key(), _key()
    for _ in range(LIMIT + 5):
        _work(a)
    # b should still see a fresh quota
    body_b = _quota(b).json()
    assert body_b["remaining"] == LIMIT, body_b


# ---------------------------------------------------------------------------
# Sliding-from-the-clients-perspective — fixed-window counters fail this
# ---------------------------------------------------------------------------


def test_no_double_burst_across_pseudo_window_boundary():
    """A correct sliding limiter must not allow 2x the quota in 2 seconds.

    We burn the quota, sleep briefly (much less than the window), then
    immediately try a fresh burst. A naive fixed-window-counter that
    happens to roll over during our sleep would let the second burst
    succeed. A sliding window must reject most of it.
    """
    api_key = _key()
    for _ in range(LIMIT):
        _work(api_key)
    time.sleep(1.0)
    rejected = sum(1 for _ in range(LIMIT) if _work(api_key).status_code == 429)
    # In 1 second of a 60-second window we get at most ~1/60th of the
    # quota back. Demand the limiter reject the vast majority.
    assert rejected >= LIMIT - 5, f"only {rejected} requests were rejected"
