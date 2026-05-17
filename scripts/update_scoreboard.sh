#!/usr/bin/env bash
#
# Rebuild a per-challenge SCOREBOARD.md by running the grader against every
# submission in that challenge and recording the numbers.
#
# Usage:
#   scripts/update_scoreboard.sh <challenge-dir>
#
# Called from .github/workflows/update-scoreboards.yml for each challenge
# that had its submissions/ directory touched in the merge. Re-runs every
# submission on the same runner so the leaderboard is internally
# consistent (CI hardware drift is the same for all rows).
#
# Sort order:
#   1. Grade (S > A > B > C > D > F)
#   2. Capacity RPS at SLO (desc)
#   3. p99 at capacity (asc)
#
set -euo pipefail

CHALLENGE_DIR=${1:-}
if [ -z "$CHALLENGE_DIR" ]; then
    echo "Usage: $0 <challenge-dir>"
    exit 1
fi

SCOREBOARD="$CHALLENGE_DIR/SCOREBOARD.md"
CHALLENGE_NAME=$(basename "$CHALLENGE_DIR")
RESULTS_ROOT=$(mktemp -d)
ROWS_FILE="$RESULTS_ROOT/_rows.tsv"

trap 'rm -rf "$RESULTS_ROOT"' EXIT

cat > "$SCOREBOARD" <<EOF
# Scoreboard for $CHALLENGE_NAME

> Auto-generated on every merge that touches \`$CHALLENGE_NAME/submissions/\`.
> Every submission is re-benchmarked on the same CI runner so the
> numbers are internally consistent.
>
> - **Coverage** — percentage of functional tests passed.
> - **Capacity** — highest sustained RPS where the SLO held
>   (see \`benchmark/config.yml\` for the per-challenge SLO).
> - **p99** — tail latency at the capacity stage.
> - **Grade** — S held SLO at saturation, A at heavy, B at target,
>   C at light or below 100% coverage, D never met SLO, F coverage <95%.

| Rank | Username | Grade | Coverage | Capacity (req/s) | p99 (ms) | Errors |
|:----:|:---------|:-----:|---------:|-----------------:|---------:|-------:|
EOF

if [ ! -d "$CHALLENGE_DIR/submissions" ]; then
    echo "| — | _no submissions yet_ | — | — | — | — | — |" >> "$SCOREBOARD"
    echo "Wrote empty scoreboard to $SCOREBOARD"
    exit 0
fi

shopt -s nullglob
HAS_ANY=0
for submission_dir in "$CHALLENGE_DIR"/submissions/*/; do
    [ -d "$submission_dir" ] || continue
    USERNAME=$(basename "$submission_dir")
    if [ ! -f "$submission_dir/docker-compose.yml" ]; then
        echo "Skipping $USERNAME: no docker-compose.yml"
        continue
    fi

    echo
    echo "=========================================="
    echo "Benchmarking $CHALLENGE_NAME / $USERNAME"
    echo "=========================================="
    OUT_DIR="$RESULTS_ROOT/$USERNAME"
    mkdir -p "$OUT_DIR"

    if ! "$(dirname "$0")/run_benchmark.sh" "$CHALLENGE_DIR" "$submission_dir" "$OUT_DIR"; then
        echo "Benchmark failed for $USERNAME; recording zeros."
    fi

    COVERAGE_FILE="$OUT_DIR/coverage.json"
    BENCH_FILE="$OUT_DIR/benchmark.json"

    # One python invocation per submission to read every field at once,
    # printed tab-separated for `sort` and the markdown row writer below.
    ROW=$(python3 - "$COVERAGE_FILE" "$BENCH_FILE" "$USERNAME" <<'PY'
import json, sys, pathlib

cov_path = pathlib.Path(sys.argv[1])
bench_path = pathlib.Path(sys.argv[2])
username = sys.argv[3]

cov = json.loads(cov_path.read_text()) if cov_path.exists() else {}
bench = json.loads(bench_path.read_text()) if bench_path.exists() else {}

grade = bench.get("grade", "F")
coverage = float(cov.get("coverage_pct", 0.0))
capacity = float(bench.get("capacity_rps", 0.0))
p99 = int(bench.get("capacity_p99_ms", 0))
err = float(bench.get("capacity_error_rate_pct", 0.0))

# Numeric grade for sorting (higher is better).
grade_rank = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, "F": 0}.get(grade, 0)

print("\t".join([
    username, grade, str(grade_rank),
    f"{coverage:.1f}", f"{capacity:.1f}", str(p99), f"{err:.2f}"
]))
PY
)
    echo "$ROW" >> "$ROWS_FILE"
    HAS_ANY=1
done

if [ "$HAS_ANY" = "0" ]; then
    echo "| — | _no submissions yet_ | — | — | — | — | — |" >> "$SCOREBOARD"
    echo "Wrote empty scoreboard to $SCOREBOARD"
    exit 0
fi

# Sort: grade_rank desc (col 3), capacity desc (col 5), p99 asc (col 6).
# Per-key flags are required here; -n and -g are mutually exclusive when
# given as global flags, but each can be attached to its own key with
# the `KEYnr` / `KEYg` shorthand. `-g` handles both ints and floats so
# we use it everywhere.
sort -t "$(printf '\t')" -k3,3gr -k5,5gr -k6,6g "$ROWS_FILE" > "$ROWS_FILE.sorted"

RANK=0
while IFS=$'\t' read -r u grade _grank cov cap p99 err; do
    RANK=$((RANK + 1))
    printf "| %d | %s | %s | %s%% | %s | %s | %s%% |\n" \
        "$RANK" "$u" "$grade" "$cov" "$cap" "$p99" "$err" >> "$SCOREBOARD"
done < "$ROWS_FILE.sorted"

echo
echo "Wrote scoreboard to $SCOREBOARD"
