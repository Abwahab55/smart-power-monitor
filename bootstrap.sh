#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

REGION="eu-central-1"
EMAIL=""
PREFIX="power-monitor"
SKIP_PROVISION=0
AUTO_REPORT=0
REPORT_COUNT=20
REPORT_INTERVAL=1
REPORT_PREFIX="sample"

usage() {
  cat <<'EOF'
Usage:
  ./bootstrap.sh [options]

Options:
  --region <aws-region>     AWS region for provisioning (default: eu-central-1)
  --email <address>         Email for SNS alert subscription
  --prefix <name>           Resource name prefix (default: power-monitor)
  --skip-provision          Only run local prerequisites, skip AWS provisioning
  --auto-report             Generate sample JSONL + PNG chart + summary JSON
  --report-count <n>        Number of sample readings for auto-report (default: 20)
  --report-interval <sec>   Interval seconds for report generation (default: 1)
  --report-prefix <name>    Prefix for generated report files (default: sample)
  -h, --help                Show this help text

Examples:
  ./bootstrap.sh --email you@example.com
  ./bootstrap.sh --region eu-central-1 --email you@example.com --prefix demo-pm
  ./bootstrap.sh --skip-provision
  ./bootstrap.sh --skip-provision --auto-report --report-count 30 --report-prefix demo
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)
      REGION="$2"
      shift 2
      ;;
    --email)
      EMAIL="$2"
      shift 2
      ;;
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    --skip-provision)
      SKIP_PROVISION=1
      shift
      ;;
    --auto-report)
      AUTO_REPORT=1
      shift
      ;;
    --report-count)
      REPORT_COUNT="$2"
      shift 2
      ;;
    --report-interval)
      REPORT_INTERVAL="$2"
      shift 2
      ;;
    --report-prefix)
      REPORT_PREFIX="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if ! [[ "$REPORT_COUNT" =~ ^[0-9]+$ ]] || [[ "$REPORT_COUNT" -le 0 ]]; then
  echo "ERROR: --report-count must be a positive integer"
  exit 1
fi

if ! [[ "$REPORT_INTERVAL" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --report-interval must be a non-negative integer"
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
REPORT_JSONL="$ROOT_DIR/output/${REPORT_PREFIX}_readings_${TIMESTAMP}.jsonl"
REPORT_PNG="$ROOT_DIR/output/${REPORT_PREFIX}_dashboard_${TIMESTAMP}.png"
REPORT_SUMMARY="$ROOT_DIR/output/${REPORT_PREFIX}_summary_${TIMESTAMP}.json"

generate_report() {
  echo "[REPORT] Generating sample telemetry and visual report"
  mkdir -p "$ROOT_DIR/output"
  python "$ROOT_DIR/simulator.py" --local --interval "$REPORT_INTERVAL" --count "$REPORT_COUNT" --output "$REPORT_JSONL" >/dev/null
  python "$ROOT_DIR/visualize_readings.py" --input "$REPORT_JSONL" --chart "$REPORT_PNG" --summary "$REPORT_SUMMARY" >/dev/null
  echo "[REPORT] JSONL   : $REPORT_JSONL"
  echo "[REPORT] Chart   : $REPORT_PNG"
  echo "[REPORT] Summary : $REPORT_SUMMARY"
}

echo "[1/4] Setting up Python virtual environment"
if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip >/dev/null
pip install -r "$ROOT_DIR/requirements.txt" >/dev/null

echo "[2/4] Ensuring Amazon Root CA certificate"
mkdir -p "$ROOT_DIR/certs"
python - <<'PY'
import pathlib
import urllib.request

url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
out = pathlib.Path("certs/AmazonRootCA1.pem")
if not out.exists() or out.stat().st_size == 0:
    urllib.request.urlretrieve(url, out)
print(f"Saved: {out}")
PY

if [[ "$SKIP_PROVISION" -eq 1 ]]; then
  echo "[3/4] Skipping AWS provisioning (--skip-provision)"
  if [[ "$AUTO_REPORT" -eq 1 ]]; then
    echo "[4/4] Running auto-report"
    generate_report
  else
    echo "[4/4] Done. Run local simulator with:"
    echo "      source .venv/bin/activate && python simulator.py --local"
  fi
  exit 0
fi

echo "[3/4] Checking AWS credentials"
if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "ERROR: AWS credentials are not set in environment variables."
  echo "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or run with --skip-provision."
  exit 1
fi

echo "[4/4] Provisioning AWS infrastructure"
SETUP_CMD=(python "$ROOT_DIR/setup_aws.py" --region "$REGION" --prefix "$PREFIX")
if [[ -n "$EMAIL" ]]; then
  SETUP_CMD+=(--email "$EMAIL")
fi
"${SETUP_CMD[@]}"

if [[ "$AUTO_REPORT" -eq 1 ]]; then
  generate_report
fi

echo "Bootstrap complete."