#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY in the environment or .env}"
: "${OPENAI_BASE_URL:?Set OPENAI_BASE_URL in the environment or .env}"

export AZURE_OPENAI_ENDPOINT="${OPENAI_BASE_URL}"
export AZURE_OPENAI_API_KEY="${OPENAI_API_KEY}"
export AZURE_OPENAI_API_VERSION="openai-compat"
export AZURE_OPENAI_AUTH_MODE="openai_compatible"
export AZURE_OPENAI_AD_SCOPE=""
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"

TARGET_MODEL="${TARGET_MODEL:-Qwen3.6-35B-A3B}"
CONFIG="${CONFIG:-${ROOT}/configs/group_method/spreadsheetbench.yaml}"
SKILL="${SKILL:-${ROOT}/checkpoints/Qwen3.6-35B-A3B/spreadsheetbench.md}"
SPLIT_DIR="${SPLIT_DIR:-${ROOT}/data/spreadsheetbench/split}"
DATA_ROOT="${DATA_ROOT:-${ROOT}/data/spreadsheetbench/tasks}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/outputs/qwen_spreadsheet_test}"
TEST_ENV_NUM="${TEST_ENV_NUM:-0}"
WORKERS="${WORKERS:-4}"

cd "${ROOT}"
python scripts/eval_only.py \
  --config "${CONFIG}" \
  --skill "${SKILL}" \
  --split valid_unseen \
  --target_model "${TARGET_MODEL}" \
  --target_backend openai_chat \
  --split_dir "${SPLIT_DIR}" \
  --data_root "${DATA_ROOT}" \
  --out_root "${OUT_ROOT}" \
  --test_env_num "${TEST_ENV_NUM}" \
  --mode multi \
  --workers "${WORKERS}" \
  --max_api_workers "${WORKERS}"

printf 'Summary: %s\n' "${OUT_ROOT}/eval_summary.json"
