#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

REGION="eu-central-1"
EMAIL=""
PREFIX="power-monitor"
SKIP_PROVISION=0

usage() {
  cat <<'EOF'
Usage:
  ./bootstrap.sh [options]

Options:
  --region <aws-region>     AWS region for provisioning (default: eu-central-1)
  --email <address>         Email for SNS alert subscription
  --prefix <name>           Resource name prefix (default: power-monitor)
  --skip-provision          Only run local prerequisites, skip AWS provisioning
  -h, --help                Show this help text

Examples:
  ./bootstrap.sh --email you@example.com
  ./bootstrap.sh --region eu-central-1 --email you@example.com --prefix demo-pm
  ./bootstrap.sh --skip-provision
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
  echo "[4/4] Done. Run local simulator with:"
  echo "      source .venv/bin/activate && python simulator.py --local"
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

echo "Bootstrap complete."