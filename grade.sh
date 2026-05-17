#!/usr/bin/env bash
#
# One-command local grader.
#
#   ./grade.sh <challenge-number> <username>
#
# Examples:
#   ./grade.sh 1 RezaSi
#   ./grade.sh 2 alice
#
# What it does:
#   1. Creates .venv/ (once) and installs scripts/requirements.txt into it.
#   2. Builds and starts your submission with docker compose.
#   3. Waits for the service's /healthz to respond.
#   4. Runs the functional tests (pytest) — that's the coverage number.
#   5. Runs the Locust load test — that's RPS and p50/p90/p99.
#   6. Prints a benchmark report and tears the stack down.
#
# Requirements:
#   - Docker and Docker Compose
#   - Python 3.11+
#
set -euo pipefail

usage() {
    cat <<EOF
Usage: $0 <challenge-number> <username>

Examples:
  $0 1 RezaSi              # grade challenge-1 submission by RezaSi
  $0 2 alice               # grade challenge-2 submission by alice

This is the same grader that runs on every PR. Output goes to:
  challenge-<n>/results/<username>/
EOF
}

if [ $# -lt 2 ]; then
    usage
    exit 1
fi

CHALLENGE_NUM=$1
USERNAME=$2
ROOT_DIR=$(cd "$(dirname "$0")" && pwd)
CHALLENGE_DIR="$ROOT_DIR/challenge-$CHALLENGE_NUM"
SUBMISSION_DIR="$CHALLENGE_DIR/submissions/$USERNAME"
RESULTS_DIR="$CHALLENGE_DIR/results/$USERNAME"

if [ ! -d "$CHALLENGE_DIR" ]; then
    echo "Error: $CHALLENGE_DIR does not exist."
    echo "Available challenges:"
    ls -1d "$ROOT_DIR"/challenge-*/ 2>/dev/null | xargs -n1 basename
    exit 1
fi

if [ ! -d "$SUBMISSION_DIR" ]; then
    echo "Error: no submission folder at $SUBMISSION_DIR."
    echo "Bootstrap one with:  ./create_submission.sh $CHALLENGE_NUM"
    exit 1
fi

if [ ! -f "$SUBMISSION_DIR/docker-compose.yml" ]; then
    echo "Error: $SUBMISSION_DIR has no docker-compose.yml."
    exit 1
fi

# --- 1. Python venv ---------------------------------------------------------
VENV_DIR="$ROOT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo ">> Creating local Python virtualenv at .venv/"
    python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

REQ_FILE="$ROOT_DIR/scripts/requirements.txt"
REQ_STAMP="$VENV_DIR/.requirements.installed"
if [ ! -f "$REQ_STAMP" ] || [ "$REQ_FILE" -nt "$REQ_STAMP" ]; then
    echo ">> Installing grader dependencies into .venv/"
    pip install --quiet --upgrade pip
    pip install --quiet -r "$REQ_FILE"
    touch "$REQ_STAMP"
fi

# --- 2. Docker preflight ----------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed. Install Docker Desktop first."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Error: docker daemon is not running."
    exit 1
fi

# --- 3. Run the grader ------------------------------------------------------
mkdir -p "$RESULTS_DIR"

echo
echo "=============================================="
echo "  Grading challenge-$CHALLENGE_NUM / $USERNAME"
echo "=============================================="

"$ROOT_DIR/scripts/run_benchmark.sh" \
    "challenge-$CHALLENGE_NUM" \
    "challenge-$CHALLENGE_NUM/submissions/$USERNAME" \
    "$RESULTS_DIR"

echo
echo "=============================================="
echo "  Done. Artifacts in $RESULTS_DIR"
echo "=============================================="
echo "  - benchmark-report.md     (the PR-comment summary)"
echo "  - coverage.json           (functional test results)"
echo "  - benchmark.json          (load test results)"
echo "  - locust_stats.csv        (raw Locust output)"
echo "  - junit.xml               (raw pytest output)"
