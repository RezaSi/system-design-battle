"""Functional tests for challenge-1 (URL Shortener).

The TARGET_HOST environment variable points at the running submission's
HTTP base URL (default: http://localhost:8080). Each test corresponds to
a single clause of the API contract spelled out in challenge-1/README.md.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid

import pytest
import requests


HOST = os.environ.get("TARGET_HOST", "http://localhost:8080").rstrip("/")
CODE_REGEX = re.compile(r"^[A-Za-z0-9]{4,16}$")
# Set by scripts/run_benchmark.sh so the persistence test can drive
# `docker compose restart`. When not set (e.g. someone runs pytest on a
# bare service), the persistence test is skipped.
COMPOSE_FILE = os.environ.get("SDB_COMPOSE_FILE")
HEALTH_PATH = os.environ.get("SDB_HEALTH_PATH", "/healthz")


def _unique_url() -> str:
    return f"https://example.com/{uuid.uuid4().hex}"


def _post_shorten(url: str | None, **kwargs) -> requests.Response:
    return requests.post(
        f"{HOST}/shorten",
        json={"url": url} if url is not None else {},
        timeout=5,
        **kwargs,
    )


def _assert_error_body(resp: requests.Response) -> None:
    """Every 400/404 response must carry {"error": "..."}.

    The grader is strict on the key name but tolerant of the message.
    """
    body = resp.json()
    assert isinstance(body, dict), f"expected JSON object, got {body!r}"
    assert "error" in body, f"expected 'error' key, got {body!r}"
    assert isinstance(body["error"], str) and body["error"], (
        f"'error' must be a non-empty string, got {body['error']!r}"
    )


# ---------------------------------------------------------------------------
# GET /healthz
# ---------------------------------------------------------------------------


def test_healthz_returns_200():
    resp = requests.get(f"{HOST}/healthz", timeout=5)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# POST /shorten — happy path
# ---------------------------------------------------------------------------


def test_shorten_returns_valid_code():
    resp = _post_shorten(_unique_url())
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert "code" in body, body
    assert CODE_REGEX.match(body["code"]), f"code {body['code']!r} fails regex"


def test_shorten_short_url_is_absolute_and_ends_in_code():
    resp = _post_shorten(_unique_url())
    body = resp.json()
    assert "short_url" in body, body
    short_url = body["short_url"]
    assert short_url.startswith("http://") or short_url.startswith("https://"), short_url
    assert short_url.endswith(body["code"]), short_url


def test_shorten_first_creation_returns_201():
    """Spec: 201 Created when a new code is minted."""
    resp = _post_shorten(_unique_url())
    assert resp.status_code == 201, resp.text


def test_shorten_repeat_for_same_url_returns_200():
    """Spec: 200 OK when returning an existing code for the same URL."""
    url = _unique_url()
    first = _post_shorten(url)
    assert first.status_code == 201, first.text
    second = _post_shorten(url)
    assert second.status_code == 200, second.text


def test_shorten_is_idempotent_for_same_url():
    url = _unique_url()
    a = _post_shorten(url).json()
    b = _post_shorten(url).json()
    assert a["code"] == b["code"], (
        f"same URL must yield same code; got {a['code']} and {b['code']}"
    )


def test_shorten_different_urls_get_different_codes():
    a = _post_shorten(_unique_url()).json()
    b = _post_shorten(_unique_url()).json()
    assert a["code"] != b["code"]


def test_shorten_accepts_https_and_http():
    https = _post_shorten("https://example.com/" + uuid.uuid4().hex)
    http_ = _post_shorten("http://example.com/" + uuid.uuid4().hex)
    assert https.status_code in (200, 201), https.text
    assert http_.status_code in (200, 201), http_.text
    assert CODE_REGEX.match(https.json()["code"])
    assert CODE_REGEX.match(http_.json()["code"])


# ---------------------------------------------------------------------------
# POST /shorten — validation (every rejection must return {"error": "..."})
# ---------------------------------------------------------------------------


def test_shorten_rejects_missing_url():
    resp = requests.post(f"{HOST}/shorten", json={}, timeout=5)
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


def test_shorten_rejects_empty_url():
    resp = requests.post(f"{HOST}/shorten", json={"url": ""}, timeout=5)
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


def test_shorten_rejects_non_http_scheme():
    resp = requests.post(
        f"{HOST}/shorten",
        json={"url": "ftp://example.com/foo"},
        timeout=5,
    )
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


def test_shorten_rejects_malformed_json():
    resp = requests.post(
        f"{HOST}/shorten",
        data="not json",
        headers={"content-type": "application/json"},
        timeout=5,
    )
    assert resp.status_code == 400, resp.text
    _assert_error_body(resp)


# ---------------------------------------------------------------------------
# GET /:code — redirect resolution
# ---------------------------------------------------------------------------


def test_get_code_returns_302_with_location():
    url = _unique_url()
    code = _post_shorten(url).json()["code"]
    resp = requests.get(f"{HOST}/{code}", allow_redirects=False, timeout=5)
    assert resp.status_code in (301, 302, 307, 308), resp.status_code
    assert resp.headers.get("Location") == url, resp.headers


def test_get_unknown_code_returns_404_with_error_body():
    resp = requests.get(f"{HOST}/zzzz9999", allow_redirects=False, timeout=5)
    assert resp.status_code == 404, resp.text
    _assert_error_body(resp)


def test_get_code_is_case_sensitive():
    """Codes are alphanumeric; lookup must distinguish case.

    'ABc123' and 'abc123' are two different codes.
    """
    url = _unique_url()
    code = _post_shorten(url).json()["code"]
    swapped = code.swapcase()
    if swapped == code:  # rare: all-digit code; skip rather than make a false claim
        pytest.skip("code happened to be case-insensitive (all digits)")
    resp = requests.get(f"{HOST}/{swapped}", allow_redirects=False, timeout=5)
    # Either it's a different (existing) code, or a miss. Both are fine, as
    # long as it does not resolve back to our URL.
    if resp.status_code in (301, 302, 307, 308):
        assert resp.headers.get("Location") != url, (
            "case-insensitive resolution: swapped-case code returned the same URL"
        )


# ---------------------------------------------------------------------------
# GET /api/codes/:code — metadata
# ---------------------------------------------------------------------------


def test_metadata_returns_url_and_initial_hit_count():
    url = _unique_url()
    code = _post_shorten(url).json()["code"]
    resp = requests.get(f"{HOST}/api/codes/{code}", timeout=5)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("code") == code, body
    assert body.get("url") == url, body
    assert "hits" in body, body
    assert isinstance(body["hits"], int), body
    assert body["hits"] >= 0


def test_metadata_unknown_code_returns_404_with_error_body():
    resp = requests.get(f"{HOST}/api/codes/zzzz9999", timeout=5)
    assert resp.status_code == 404, resp.text
    _assert_error_body(resp)


def test_metadata_hits_increase_after_resolves():
    url = _unique_url()
    code = _post_shorten(url).json()["code"]
    before = requests.get(f"{HOST}/api/codes/{code}", timeout=5).json()["hits"]
    for _ in range(5):
        requests.get(f"{HOST}/{code}", allow_redirects=False, timeout=5)
    # Allow eventual consistency (spec allows ~1s slack).
    time.sleep(1.2)
    after = requests.get(f"{HOST}/api/codes/{code}", timeout=5).json()["hits"]
    assert after >= before + 5, (
        f"hits did not grow enough: before={before} after={after}"
    )


def test_metadata_lookups_do_not_count_as_hits():
    """The spec says `hits` is the count of GET /:code resolutions.

    Pinging /api/codes/:code must not increment it.
    """
    url = _unique_url()
    code = _post_shorten(url).json()["code"]
    initial = requests.get(f"{HOST}/api/codes/{code}", timeout=5).json()["hits"]
    for _ in range(5):
        requests.get(f"{HOST}/api/codes/{code}", timeout=5)
    time.sleep(0.3)
    after = requests.get(f"{HOST}/api/codes/{code}", timeout=5).json()["hits"]
    assert after == initial, (
        f"metadata lookups bumped hits: initial={initial} after={after}"
    )


# ---------------------------------------------------------------------------
# Durability — data must survive a `docker compose restart` of the stack.
#
# This is the test that distinguishes a real submission from a toy. An
# in-memory implementation passes everything above and fails this. To pass,
# the submission must use a backing store (Postgres, MongoDB, Redis with
# persistence, SQLite on a named volume, etc.) declared in its compose file.
# ---------------------------------------------------------------------------


def _wait_for_healthz(timeout_seconds: float = 90.0) -> None:
    deadline = time.time() + timeout_seconds
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{HOST}{HEALTH_PATH}", timeout=2)
            if r.status_code == 200:
                return
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(1)
    raise AssertionError(
        f"service did not become healthy within {timeout_seconds:.0f}s "
        f"after restart (last error: {last_err})"
    )


@pytest.mark.skipif(
    not COMPOSE_FILE,
    reason="persistence test requires SDB_COMPOSE_FILE (set by the grader)",
)
def test_data_persists_across_docker_compose_restart():
    """Write data, restart the user's whole stack, verify the data is still there.

    Note for graders: this calls `docker compose -f <compose> restart` and
    waits for the service's `/healthz` to come back. The submission must
    declare a backing store with a volume so its data survives the restart
    of its app container.
    """
    # 1. Write data.
    url_a = _unique_url()
    url_b = _unique_url()
    code_a = _post_shorten(url_a).json()["code"]
    code_b = _post_shorten(url_b).json()["code"]

    # 2. Generate some hits to verify (best-effort) hit count persistence.
    for _ in range(3):
        requests.get(f"{HOST}/{code_a}", allow_redirects=False, timeout=5)
    time.sleep(1.5)
    hits_a_before = requests.get(f"{HOST}/api/codes/{code_a}", timeout=5).json()["hits"]

    # 3. Restart the entire compose stack. Resource caps come from the
    # submission's own docker-compose.yml so there's no override to pass.
    result = subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "restart"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"docker compose restart failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # 4. Wait for the service to be healthy again.
    _wait_for_healthz(timeout_seconds=90)
    # Small additional warmup for connection pools, indices, etc.
    time.sleep(2)

    # 5. URL mappings must survive the restart.
    for code, expected_url in ((code_a, url_a), (code_b, url_b)):
        resp = requests.get(f"{HOST}/{code}", allow_redirects=False, timeout=5)
        assert resp.status_code in (301, 302, 307, 308), (
            f"after restart, GET /{code} returned {resp.status_code}, "
            f"expected a 3xx redirect — data appears to be in-memory only"
        )
        assert resp.headers.get("Location") == expected_url, (
            f"after restart, GET /{code} pointed at {resp.headers.get('Location')!r}, "
            f"expected {expected_url!r}"
        )

    # 6. Metadata lookups must still resolve to the right URL.
    meta_a = requests.get(f"{HOST}/api/codes/{code_a}", timeout=5).json()
    assert meta_a["code"] == code_a
    assert meta_a["url"] == url_a

    # 7. Hit counter persistence is nice-to-have; the spec doesn't strictly
    # demand it. We accept either preserved counts (durable counter) or a
    # reset to 0 (volatile counter on top of durable URLs).
    hits_a_after = meta_a["hits"]
    assert hits_a_after >= 0
    if hits_a_before > 0 and hits_a_after == 0:
        # Implementation kept URLs durable but the hit counter is volatile.
        # That's allowed by the spec — log a hint but pass.
        pass
