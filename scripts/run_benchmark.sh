#!/usr/bin/env bash
#
# Run the full grader for a single submission.
#
# Usage:
#   scripts/run_benchmark.sh <challenge-dir> <submission-dir> <output-dir>
#
# Example:
#   scripts/run_benchmark.sh challenge-1 challenge-1/submissions/RezaSi results/
#
# What it does:
#   1. Validate the submission's docker-compose.yml: every service must
#      declare `cpus:` and `mem_limit:`, and the sum must fit in the
#      challenge's resource_budget. The contestant chooses how to slice
#      the budget across services — that allocation is part of the design.
#   2. docker compose up -d --build using the user's compose file.
#   3. Wait for the service's /healthz to return 200.
#   4. Run pytest against challenge/benchmark/tests.py for coverage.
#   5. Run Locust headless with the LoadTestShape declared in the
#      challenge's locustfile. Locust writes per-second stats to
#      <out>/locust_stats_history.csv which the parser groups by stage.
#   6. parse_results.py produces coverage.json, benchmark.json, and
#      benchmark-report.md (the PR comment / scoreboard payload).
#   7. docker compose down -v.
#
set -euo pipefail

if [ $# -lt 3 ]; then
    echo "Usage: $0 <challenge-dir> <submission-dir> <output-dir>"
    exit 1
fi

CHALLENGE_DIR=$1
SUBMISSION_DIR=$2
OUTPUT_DIR=$3
SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)

if [ ! -d "$CHALLENGE_DIR/benchmark" ]; then
    echo "Error: '$CHALLENGE_DIR/benchmark' not found."
    exit 1
fi

if [ ! -f "$SUBMISSION_DIR/docker-compose.yml" ]; then
    echo "Error: '$SUBMISSION_DIR/docker-compose.yml' not found."
    exit 1
fi

CONFIG_FILE="$CHALLENGE_DIR/benchmark/config.yml"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: '$CONFIG_FILE' not found."
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

read_config_value() {
    local key=$1
    local default=$2
    python3 - "$CONFIG_FILE" "$key" "$default" <<'PY'
import sys
import yaml
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    cfg = yaml.safe_load(f) or {}
# Support dotted keys for nested values like resource_budget.cpus
node = cfg
for part in key.split("."):
    if isinstance(node, dict) and part in node:
        node = node[part]
    else:
        node = default
        break
print("" if node is None else node)
PY
}

PORT=$(read_config_value "port" "8080")
HOST_URL=$(read_config_value "host" "http://localhost:${PORT}")
HEALTH_PATH=$(read_config_value "health_path" "/healthz")
WARMUP_SECONDS=$(read_config_value "warmup_seconds" "3")
BUDGET_CPUS=$(read_config_value "resource_budget.cpus" "1.0")
BUDGET_MEM=$(read_config_value "resource_budget.memory_mb" "1024")
SLO_P99=$(read_config_value "slo.p99_ms" "50")
SLO_ERR=$(read_config_value "slo.error_rate_pct" "1.0")

# Total Locust run-time = sum of stage durations from config.
TOTAL_DURATION_S=$(python3 - "$CONFIG_FILE" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f) or {}
stages = cfg.get("load_stages") or []
print(sum(int(s.get("duration_s", 0)) for s in stages) or 60)
PY
)

ABS_SUBMISSION_DIR=$(cd "$SUBMISSION_DIR" && pwd)

echo "=== Benchmark configuration ==="
echo "  Challenge:        $CHALLENGE_DIR"
echo "  Submission:       $SUBMISSION_DIR"
echo "  Host:             $HOST_URL"
echo "  Resource budget:  ${BUDGET_CPUS} CPU / ${BUDGET_MEM} MB (stack total)"
echo "  SLO:              p99 <= ${SLO_P99} ms, errors < ${SLO_ERR}%"
echo "  Load duration:    ${TOTAL_DURATION_S}s (sum of staged profile)"
echo "  Health path:      $HEALTH_PATH"
echo

COMPOSE_PROJECT="sdb-$(basename "$CHALLENGE_DIR")-$(basename "$SUBMISSION_DIR" | tr '[:upper:]' '[:lower:]')"
export COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT"

cleanup() {
    echo
    echo "=== Tearing down stack ==="
    (cd "$ABS_SUBMISSION_DIR" && docker compose down -v --remove-orphans) || true
}
trap cleanup EXIT

echo "=== Validating resource budget ==="
if ! python3 "$SCRIPTS_DIR/validate_budget.py" \
    --compose "$ABS_SUBMISSION_DIR/docker-compose.yml" \
    --budget-cpus "$BUDGET_CPUS" \
    --budget-memory-mb "$BUDGET_MEM"; then
    echo
    echo "Resource budget validation failed; not running the bench."
    exit 1
fi

wait_for_healthz() {
    local label=$1
    local attempts=0
    local max_attempts=${2:-60}
    echo "=== ${label} at ${HOST_URL}${HEALTH_PATH} ==="
    until curl -fsS -o /dev/null -m 3 "${HOST_URL}${HEALTH_PATH}"; do
        attempts=$((attempts + 1))
        if [ $attempts -ge $max_attempts ]; then
            echo "Health check did not pass within ${max_attempts} seconds."
            echo "=== docker compose logs ==="
            (cd "$ABS_SUBMISSION_DIR" && docker compose logs --tail=200) || true
            return 1
        fi
        sleep 1
    done
    echo "Service is healthy after ${attempts}s."
    return 0
}

echo "=== Building and starting submission stack ==="
(cd "$ABS_SUBMISSION_DIR" && docker compose up -d --build)

wait_for_healthz "Waiting for initial health" 60

echo "=== Warmup (${WARMUP_SECONDS}s) ==="
sleep "$WARMUP_SECONDS"

ABS_COMPOSE_FILE="$ABS_SUBMISSION_DIR/docker-compose.yml"
export SDB_COMPOSE_FILE="$ABS_COMPOSE_FILE"
export SDB_SUBMISSION_DIR="$ABS_SUBMISSION_DIR"

echo "=== Running functional tests (coverage) ==="
COVERAGE_JSON="$OUTPUT_DIR/coverage.json"
PYTEST_OUT="$OUTPUT_DIR/pytest_output.txt"
set +e
TARGET_HOST="$HOST_URL" \
SDB_COMPOSE_FILE="$ABS_COMPOSE_FILE" \
SDB_SUBMISSION_DIR="$ABS_SUBMISSION_DIR" \
SDB_HEALTH_PATH="$HEALTH_PATH" \
python3 -m pytest "$CHALLENGE_DIR/benchmark/tests.py" \
    -v --tb=short --no-header -p no:cacheprovider \
    --junitxml="$OUTPUT_DIR/junit.xml" \
    | tee "$PYTEST_OUT"
PYTEST_EXIT=${PIPESTATUS[0]}
set -e
echo "Functional tests exit code: $PYTEST_EXIT"

python3 "$SCRIPTS_DIR/parse_results.py" \
    --pytest-junit "$OUTPUT_DIR/junit.xml" \
    --coverage-out "$COVERAGE_JSON"

# Persistence tests can restart the user's stack — make sure it's back
# up before pummelling it with Locust.
if ! wait_for_healthz "Re-checking health before load test" 90; then
    echo "Service was not healthy after functional tests; skipping load test."
    SKIP_LOAD=1
fi

echo "=== Running staged load test (Locust) ==="
LOCUST_CSV_PREFIX="$OUTPUT_DIR/locust"
LOCUST_EXIT=0
if [ "${SKIP_LOAD:-0}" != "1" ]; then
    set +e
    python3 -m locust \
        -f "$CHALLENGE_DIR/benchmark/locustfile.py" \
        --headless \
        --host "$HOST_URL" \
        --users 10 --spawn-rate 10 \
        --run-time "${TOTAL_DURATION_S}s" \
        --csv "$LOCUST_CSV_PREFIX" \
        --csv-full-history \
        --only-summary \
        --exit-code-on-error 0
    LOCUST_EXIT=$?
    set -e
fi
echo "Locust exit code: $LOCUST_EXIT"

python3 "$SCRIPTS_DIR/parse_results.py" \
    --locust-csv "${LOCUST_CSV_PREFIX}_stats.csv" \
    --locust-history "${LOCUST_CSV_PREFIX}_stats_history.csv" \
    --config "$CONFIG_FILE" \
    --benchmark-out "$OUTPUT_DIR/benchmark.json"

echo "=== Generating report ==="
python3 "$SCRIPTS_DIR/parse_results.py" \
    --coverage "$COVERAGE_JSON" \
    --benchmark "$OUTPUT_DIR/benchmark.json" \
    --challenge "$CHALLENGE_DIR" \
    --submission "$SUBMISSION_DIR" \
    --report-out "$OUTPUT_DIR/benchmark-report.md"

cat "$OUTPUT_DIR/benchmark-report.md"

echo "=== Done. Artifacts in $OUTPUT_DIR ==="
