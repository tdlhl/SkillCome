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

# Configure the OpenAI-compatible model service.
export AZURE_OPENAI_ENDPOINT="${OPENAI_BASE_URL}"
export AZURE_OPENAI_API_KEY="${OPENAI_API_KEY}"
export AZURE_OPENAI_API_VERSION="openai-compat"
export AZURE_OPENAI_AUTH_MODE="openai_compatible"
export AZURE_OPENAI_AD_SCOPE=""
export PYTHONPATH="${ROOT}:${PYTHONPATH:-}"

TARGET_MODEL="${TARGET_MODEL:-Qwen3.6-35B-A3B}"
OPTIMIZER_MODEL="${OPTIMIZER_MODEL:-DeepSeek-V4-Pro}"

CONFIG="${CONFIG:-${ROOT}/configs/group_method/spreadsheetbench.yaml}"
SPLIT_DIR="${SPLIT_DIR:-${ROOT}/data/spreadsheetbench/split}"
DATA_ROOT="${DATA_ROOT:-${ROOT}/data/spreadsheetbench/tasks}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/outputs/qwen_spreadsheet}"

NUM_EPOCHS="${NUM_EPOCHS:-4}"
STEPS_PER_EPOCH="${STEPS_PER_EPOCH:-1}"
TRAIN_SIZE="${TRAIN_SIZE:-40}"
UNIQUE_BATCH_SIZE="${UNIQUE_BATCH_SIZE:-10}"
ROLLOUTS_PER_ITEM="${ROLLOUTS_PER_ITEM:-8}"
ROLLOUT_BUDGET_PER_STEP="${ROLLOUT_BUDGET_PER_STEP:-80}"
MIN_VALID_ROLLOUTS="${MIN_VALID_ROLLOUTS:-6}"
MAX_EXTRA_ATTEMPTS_PER_ITEM="${MAX_EXTRA_ATTEMPTS_PER_ITEM:-4}"
GROUP_SAMPLING_STRATEGY="${GROUP_SAMPLING_STRATEGY:-coverage_then_mixed}"
WORKERS="${WORKERS:-4}"
ANALYST_WORKERS="${ANALYST_WORKERS:-4}"
MAX_COMPLETION_TOKENS="${MAX_COMPLETION_TOKENS:-16384}"
MAX_TURNS="${MAX_TURNS:-30}"
TASK_TIMEOUT="${TASK_TIMEOUT:-600}"
SEL_ENV_NUM="${SEL_ENV_NUM:-0}"
EVAL_TEST="${EVAL_TEST:-true}"
USE_SLOW_UPDATE="${USE_SLOW_UPDATE:-true}"
SEED="${SEED:-42}"

cd "${ROOT}"
python scripts/train.py \
  --benchmark spreadsheetbench \
  --config "${CONFIG}" \
  --optimizer-model "${OPTIMIZER_MODEL}" \
  --target-model "${TARGET_MODEL}" \
  --split-dir "${SPLIT_DIR}" \
  --data-root "${DATA_ROOT}" \
  --out-root "${OUT_ROOT}" \
  --cfg-options \
    "num_epochs=${NUM_EPOCHS}" \
    "steps_per_epoch=${STEPS_PER_EPOCH}" \
    "train_size=${TRAIN_SIZE}" \
    "batch_size=${UNIQUE_BATCH_SIZE}" \
    "group_relative_enabled=true" \
    "rollouts_per_item=${ROLLOUTS_PER_ITEM}" \
    "rollout_budget_per_step=${ROLLOUT_BUDGET_PER_STEP}" \
    "min_valid_rollouts=${MIN_VALID_ROLLOUTS}" \
    "max_extra_attempts_per_item=${MAX_EXTRA_ATTEMPTS_PER_ITEM}" \
    "retry_timeout=900" \
    "group_sampling_strategy=${GROUP_SAMPLING_STRATEGY}" \
    "coverage_passes=1" \
    "mixed_replay_steps=0" \
    "deduplicate_ordinary_reflect=true" \
    "ensure_contrast_candidate=false" \
    "group_hypothesis_bank_enabled=true" \
    "failure_hypothesis_bank_enabled=true" \
    "group_dual_memory_enabled=true" \
    "record_slow_update_variants=true" \
    "use_slow_update=${USE_SLOW_UPDATE}" \
    "slow_update_samples=20" \
    "use_meta_skill=true" \
    "use_gate=true" \
    "gate_accept_equal=false" \
    "sel_env_num=${SEL_ENV_NUM}" \
    "eval_test=${EVAL_TEST}" \
    "selection_eval_repeats=1" \
    "accumulation=1" \
    "minibatch_size=8" \
    "merge_batch_size=8" \
    "max_analyst_rounds=3" \
    "analyst_workers=${ANALYST_WORKERS}" \
    "failure_only=false" \
    "edit_budget=4" \
    "min_edit_budget=2" \
    "lr_scheduler=cosine" \
    "lr_control_mode=fixed" \
    "skill_update_mode=patch" \
    "longitudinal_pair_policy=mixed" \
    "workers=${WORKERS}" \
    "max_api_workers=${WORKERS}" \
    "max_turns=${MAX_TURNS}" \
    "exec_timeout=${TASK_TIMEOUT}" \
    "max_completion_tokens=${MAX_COMPLETION_TOKENS}" \
    "seed=${SEED}"

printf 'Best skill: %s\n' "${OUT_ROOT}/best_skill.md"
printf 'Contrast bank: %s\n' "${OUT_ROOT}/memory/contrast_hypothesis_bank.json"
printf 'Failure bank: %s\n' "${OUT_ROOT}/memory/failure_hypothesis_bank.json"
