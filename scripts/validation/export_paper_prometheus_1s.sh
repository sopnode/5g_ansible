#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/validation/export_paper_prometheus_1s.sh \
    --prometheus-url URL \
    --start EPOCH_SECONDS \
    --end EPOCH_SECONDS \
    [--out-dir DIR]

Example:
  scripts/validation/export_paper_prometheus_1s.sh \
    --prometheus-url http://172.28.2.76:30090 \
    --start 1777642800 \
    --end 1777646880 \
    --out-dir paper_artifacts/prometheus_1s_2026-05-01_1540_1648

The query set is scripts/validation/paper_prometheus_queries.json.
The exporter evaluates those PromQL queries every 1 second and also discovers
radio metrics whose names contain mcs, bler, harq, cqi, rsrp, rsrq, sinr, or snr.
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROM_URL=""
START_EPOCH=""
END_EPOCH=""
OUT_DIR="$ROOT_DIR/paper_artifacts/prometheus_1s"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prometheus-url)
      PROM_URL="${2:-}"
      shift 2
      ;;
    --start)
      START_EPOCH="${2:-}"
      shift 2
      ;;
    --end)
      END_EPOCH="${2:-}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "$PROM_URL" ]] || { usage >&2; echo "error: --prometheus-url is required" >&2; exit 1; }
[[ -n "$START_EPOCH" ]] || { usage >&2; echo "error: --start is required" >&2; exit 1; }
[[ -n "$END_EPOCH" ]] || { usage >&2; echo "error: --end is required" >&2; exit 1; }

mkdir -p "$OUT_DIR"

python3 "$ROOT_DIR/scripts/validation/prometheus_range_export.py" \
  --prometheus-url "$PROM_URL" \
  --start "$START_EPOCH" \
  --end "$END_EPOCH" \
  --step 1s \
  --queries-json "$ROOT_DIR/scripts/validation/paper_prometheus_queries.json" \
  --discover-metrics-regex '(?i)(mcs|bler|harq|cqi|rsrp|rsrq|sinr|snr|mac_throughput|prach)' \
  --discover-metrics-limit 80 \
  --out "$OUT_DIR/prometheus_timeseries_1s.csv" \
  --summary-json "$OUT_DIR/prometheus_export_summary.json"

python3 "$ROOT_DIR/scripts/validation/create_experiment_analysis_notebook.py" \
  --results-dir "$OUT_DIR" \
  --kind experiment \
  --title "Prometheus 1s Experiment Analysis"

echo "CSV: $OUT_DIR/prometheus_timeseries_1s.csv"
echo "Summary: $OUT_DIR/prometheus_export_summary.json"
echo "Notebook: $OUT_DIR/experiment_analysis.ipynb"
