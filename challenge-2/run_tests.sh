#!/usr/bin/env bash
#
# Local wrapper around the grader. Same script CI runs.
#
#   ./run_tests.sh              # prompts for a username
#   ./run_tests.sh --all        # runs every submission in this challenge
#   ./run_tests.sh <username>   # runs only that submission
#
set -euo pipefail

CHALLENGE_ABS=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(cd "$CHALLENGE_ABS/.." && pwd)
CHALLENGE_REL=$(basename "$CHALLENGE_ABS")

cd "$ROOT_DIR"

if [ "${1-}" = "--all" ]; then
    mapfile -t SUBS < <(ls -1d "$CHALLENGE_REL/submissions/"*/ 2>/dev/null || true)
elif [ -n "${1-}" ]; then
    SUBS=( "$CHALLENGE_REL/submissions/$1/" )
else
    read -r -p "Enter the submission folder username (under $CHALLENGE_REL/submissions/): " USERNAME
    if [ -z "$USERNAME" ]; then
        echo "Username cannot be empty."
        exit 1
    fi
    SUBS=( "$CHALLENGE_REL/submissions/$USERNAME/" )
fi

if [ ${#SUBS[@]} -eq 0 ]; then
    echo "No submissions found under $CHALLENGE_REL/submissions/."
    exit 0
fi

for sub in "${SUBS[@]}"; do
    sub=${sub%/}
    if [ ! -d "$sub" ]; then
        echo "Submission directory '$sub' not found."
        continue
    fi
    if [ ! -f "$sub/docker-compose.yml" ]; then
        echo "Submission '$sub' is missing docker-compose.yml — skipping."
        continue
    fi
    USERNAME=$(basename "$sub")
    OUT_DIR="$CHALLENGE_ABS/results/$USERNAME"
    mkdir -p "$OUT_DIR"
    echo
    echo "=== Running grader for $USERNAME ==="
    "$ROOT_DIR/scripts/run_benchmark.sh" "$CHALLENGE_REL" "$sub" "$OUT_DIR"
done
