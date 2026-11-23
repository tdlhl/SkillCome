"""ReflACT Trainer — the main training loop.

Orchestrates the 6-stage ReflACT pipeline:
  1. Rollout   — execute episodes with current skill
  2. Reflect   — analyze trajectories, generate patches
  3. Aggregate — hierarchical merge of patches
  4. Select    — rank and select top edits
  5. Update    — apply edits to skill document
  6. Evaluate  — validate candidate skill, accept/reject

The trainer is environment-agnostic; all environment-specific logic is
delegated to an :class:`~skillopt.envs.base.EnvAdapter` instance.
"""
from __future__ import annotations

import glob
import json
import math
import os
import random
import re
import time
from collections import defaultdict

from skillopt.datasets.base import BatchSpec
from skillopt.envs.base import EnvAdapter
from skillopt.evaluation.gate import evaluate_gate, select_gate_score
from skillopt.evaluation.selection import (
    evaluate_selection_repeats,
    load_reused_selection_results,
    record_reused_selection_baseline,
)
from skillopt.gradient.aggregate import merge_patches
from skillopt.group_relative.reflect import run_group_contrast_reflect
from skillopt.group_relative.failure_hypothesis_bank import (
    FailureHypothesisBank,
)
from skillopt.group_relative.memory import (
    ContrastMemoryBank,
    FailureMemoryBank,
    reconcile_memory_metadata,
)
from skillopt.failure_memory import SkillOptFailureHypothesisBank
from skillopt.group_relative.edit_safety import normalize_protected_edits
from skillopt.group_relative.rollout import (
    run_grouped_rollout,
    select_ordinary_reflect_results,
)
from skillopt.group_relative.sampler import CoverageThenMixedSampler
from skillopt.group_relative.stats import write_global_group_summary
from skillopt.group_relative.slow_update_variants import (
    record_slow_update_variant,
)
from skillopt.optimizer.meta_skill import run_meta_skill
from skillopt.optimizer.clip import rank_and_select
from skillopt.optimizer.lr_autonomous import decide_autonomous_learning_rate
from skillopt.optimizer.rewrite import rewrite_skill_from_suggestions
from skillopt.optimizer.scheduler import build_scheduler
from skillopt.optimizer.skill import apply_patch_with_report
from skillopt.optimizer.slow_update import (
    build_comparison_pairs,
    extract_slow_update_field,
    inject_empty_slow_update_field,
    replace_slow_update_field,
    run_slow_update,
    save_comparison_pairs,
    strip_slow_update_field,
)
from skillopt.optimizer.update_modes import (
    get_payload_items,
    is_full_rewrite_minibatch_mode,
    normalize_update_mode,
    payload_label,
    short_item_summary,
)
from skillopt.model import (
    configure_azure_openai,
    configure_claude_code_exec,
    configure_codex_exec,
    configure_qwen_chat,
    get_token_summary,
    reset_token_tracker,
    set_reasoning_effort,
    set_target_backend,
    set_target_deployment,
    set_optimizer_backend,
    set_optimizer_deployment,
)
from skillopt.utils import compute_score, skill_hash


DEFAULT_PROTECTED_EDIT_RELOCATION = True
DEFAULT_BOUNDED_SLOW_UPDATE = False


# ── Training schedule normalization ─────────────────────────────────────────

def _normalise_steps_per_epoch_schedule(
    raw_schedule: object,
    *,
    num_epochs: int,
) -> list[int]:
    """Normalize an optional explicit per-epoch step schedule.

    CLI launchers pass comma-separated strings while structured YAML configs
    naturally use integer lists.  An empty value preserves the historical
    scalar/automatic scheduling behavior.
    """
    if raw_schedule in (None, "", []):
        return []
    if isinstance(raw_schedule, str):
        values: list[object] = [
            value.strip()
            for value in raw_schedule.split(",")
            if value.strip()
        ]
    elif isinstance(raw_schedule, (list, tuple)):
        values = list(raw_schedule)
    else:
        raise ValueError(
            "steps_per_epoch_schedule must be a comma-separated string "
            "or a list of positive integers"
        )

    try:
        schedule = [int(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "steps_per_epoch_schedule must contain only integers"
        ) from exc
    if len(schedule) != num_epochs:
        raise ValueError(
            "steps_per_epoch_schedule length must equal num_epochs: "
            f"len={len(schedule)} num_epochs={num_epochs}"
        )
    if any(value <= 0 for value in schedule):
        raise ValueError(
            "steps_per_epoch_schedule values must all be positive"
        )
    return schedule


# ── Patch normalization ───────────────────────────────────────────────────────

def _normalise_patches(
    raw_patches: list[dict | None],
    update_mode: str = "patch",
) -> tuple[list[dict], list[dict]]:
    """Extract inner 'patch' sub-dict, split into failure/success lists.

    Each element is expected to conform to :class:`~skillopt.types.RawPatch`.
    """
    mode = normalize_update_mode(update_mode)
    failure: list[dict] = []
    success: list[dict] = []
    for p in raw_patches:
        if not isinstance(p, dict):
            continue
        inner = p.get("patch", p)
        if not isinstance(inner, dict):
            continue
        items = get_payload_items(inner, mode)
        if not items:
            continue
        support = max(
            int(p.get("group_support", p.get("batch_size", 0)) or 0),
            1,
        )
        for item in items:
            if isinstance(item, dict):
                item.setdefault("source_type", p.get("source_type", "failure"))
                item.setdefault("support_count", support)
                if p.get("group_support") is not None:
                    item.setdefault("group_support", int(p["group_support"]))
                if p.get("trajectory_support") is not None:
                    item.setdefault(
                        "trajectory_support",
                        int(p["trajectory_support"]),
                    )
        if p.get("source_type", "failure") == "success":
            success.append(inner)
        else:
            failure.append(inner)
    return failure, success


def _normalise_contrast_patches(
    raw_patches: list[dict | None],
    update_mode: str = "patch",
) -> list[dict]:
    """Extract contrast patches while preserving group-level provenance."""
    mode = normalize_update_mode(update_mode)
    contrast: list[dict] = []
    for raw in raw_patches:
        if not isinstance(raw, dict):
            continue
        inner = raw.get("patch", raw)
        if not isinstance(inner, dict):
            continue
        items = get_payload_items(inner, mode)
        if not items:
            continue
        group_support = max(int(raw.get("group_support", 1) or 1), 1)
        trajectory_support = max(
            int(raw.get("trajectory_support", raw.get("batch_size", 1)) or 1),
            1,
        )
        for item in items:
            if isinstance(item, dict):
                item.setdefault("source_type", "contrast")
                item.setdefault("support_count", group_support)
                item.setdefault("group_support", group_support)
                item.setdefault("trajectory_support", trajectory_support)
        contrast.append(inner)
    return contrast


def _normalise_longitudinal_pair_policy(policy: str | None) -> str:
    raw = str(policy or "mixed").strip().lower()
    aliases = {
        "mixed": "mixed",
        "default": "mixed",
        "random": "mixed",
        "all": "mixed",
        "changed": "changed",
        "change": "changed",
        "delta": "changed",
        "10_01": "changed",
        "01_10": "changed",
        "unchanged": "unchanged",
        "stable": "unchanged",
        "same": "unchanged",
        "00_11": "unchanged",
    }
    if raw not in aliases:
        raise ValueError(
            "optimizer.longitudinal_pair_policy must be one of "
            "mixed, changed, unchanged"
        )
    return aliases[raw]


def _normalise_lr_control_mode(mode: str | None) -> str:
    raw = str(mode or "fixed").strip().lower()
    aliases = {
        "fixed": "fixed",
        "manual": "fixed",
        "scheduler": "fixed",
        "scheduled": "fixed",
        "autonomous": "autonomous",
        "auto": "autonomous",
        "optimizer": "autonomous",
        "none": "none",
        "off": "none",
        "no_lr": "none",
    }
    if raw not in aliases:
        raise ValueError("optimizer.lr_control_mode must be one of fixed, autonomous, none")
    return aliases[raw]


def _filter_longitudinal_pairs(pairs: list[dict], policy: str) -> list[dict]:
    if policy == "mixed":
        return pairs
    if policy == "changed":
        keep = {"improved", "regressed"}
    elif policy == "unchanged":
        keep = {"persistent_fail", "stable_success"}
    else:
        raise ValueError(f"Unknown longitudinal pair policy: {policy}")
    return [p for p in pairs if p.get("category") in keep]


def _pair_category_counts(pairs: list[dict]) -> dict[str, int]:
    counts = {
        "improved": 0,
        "regressed": 0,
        "persistent_fail": 0,
        "stable_success": 0,
    }
    for pair in pairs:
        cat = str(pair.get("category", ""))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def _safe_pair_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return safe[:80] or "item"


def _build_longitudinal_pairs(
    *,
    adapter: EnvAdapter,
    dataloader,
    prev_skill: str,
    curr_skill: str,
    initial_items: list[dict],
    initial_prev_results: list[dict],
    initial_curr_results: list[dict],
    prev_rollout_dir: str,
    curr_rollout_dir: str,
    policy: str,
    target_n: int,
    seed: int,
    out_root: str,
) -> tuple[list[dict], list[dict]]:
    """Build longitudinal pairs, optionally filtering by change category.

    ``mixed`` preserves the legacy behavior exactly. ``changed`` keeps only
    10/01 pairs and attempts to top up to ``target_n`` by scanning the train
    split once. ``unchanged`` keeps only 00/11 pairs and does not top up.
    """
    all_pairs = build_comparison_pairs(
        initial_prev_results,
        initial_curr_results,
        initial_items,
        prev_rollout_dir=prev_rollout_dir,
        curr_rollout_dir=curr_rollout_dir,
    )
    selected_pairs = _filter_longitudinal_pairs(all_pairs, policy)
    if policy != "changed" or len(selected_pairs) >= target_n or dataloader is None:
        return selected_pairs, all_pairs

    train_items = list(getattr(dataloader, "train_items", []) or [])
    if not train_items:
        return selected_pairs, all_pairs

    seen_ids = {str(p.get("id", "")) for p in all_pairs}
    rng = random.Random(seed)
    candidates = list(train_items)
    rng.shuffle(candidates)
    candidates = [item for item in candidates if str(item.get("id", "")) not in seen_ids]

    for idx, item in enumerate(candidates):
        if len(selected_pairs) >= target_n:
            break
        item_id = _safe_pair_id(str(item.get("id", f"item_{idx}")))
        batch = BatchSpec(
            phase="train",
            split="train",
            seed=seed + idx + 1,
            batch_size=1,
            payload=[item],
        )
        env = adapter.build_env_from_batch(batch, out_root=out_root)
        prev_dir = os.path.join(prev_rollout_dir, "topup", item_id)
        curr_dir = os.path.join(curr_rollout_dir, "topup", item_id)
        prev_results = adapter.rollout(env, prev_skill, prev_dir)
        curr_results = adapter.rollout(env, curr_skill, curr_dir)
        pair = build_comparison_pairs(
            prev_results,
            curr_results,
            [item],
            prev_rollout_dir=prev_dir,
            curr_rollout_dir=curr_dir,
        )
        all_pairs.extend(pair)
        selected_pairs.extend(_filter_longitudinal_pairs(pair, policy))

    return selected_pairs[:target_n], all_pairs


# ── History / persistence helpers ─────────────────────────────────────────────

_SECRET_KEYS = {
    "azure_api_key",
    "api_key",
    "openai_api_key",
}


def _redact_value(val: str) -> str:
    if len(val) <= 8:
        return "*" * len(val)
    return f"{val[:4]}...{val[-4:]}"


def _redact_cfg(cfg: dict) -> dict:
    redacted = dict(cfg)
    for key in list(redacted):
        if key.lower() in _SECRET_KEYS and redacted.get(key):
            redacted[key] = _redact_value(str(redacted[key]))
    return redacted

def _load_history(out_root: str) -> list[dict]:
    path = os.path.join(out_root, "history.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def _save_history(out_root: str, history: list[dict]) -> None:
    path = os.path.join(out_root, "history.json")
    with open(path, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _save_skill(out_root: str, step: int, content: str) -> None:
    skills_dir = os.path.join(out_root, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    with open(os.path.join(skills_dir, f"skill_v{step:04d}.md"), "w") as f:
        f.write(content)


def _load_skill(out_root: str, step: int) -> str:
    path = os.path.join(out_root, "skills", f"skill_v{step:04d}.md")
    with open(path) as f:
        return f.read()


def _load_meta_skill_content(out_root: str, epoch: int) -> str:
    if epoch <= 0:
        return ""
    path = os.path.join(
        out_root, "meta_skill", f"epoch_{epoch:02d}", "meta_skill_result.json",
    )
    if not os.path.exists(path):
        return ""
    try:
        with open(path) as f:
            result = json.load(f)
        return str(result.get("meta_skill_content", "")).strip()
    except Exception:
        return ""


def _load_runtime_state(out_root: str) -> dict | None:
    path = os.path.join(out_root, "runtime_state.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else None
    except Exception:
        return None


def _save_runtime_state(out_root: str, state: dict) -> None:
    path = os.path.join(out_root, "runtime_state.json")
    with open(path, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _resolve_train_size(cfg: dict, dataloader) -> int:
    configured = int(cfg.get("train_size", 0) or 0)
    inferred: int | None = None

    if dataloader is not None:
        getter = getattr(dataloader, "get_train_size", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = None
            if value is not None:
                inferred = int(value)
        elif hasattr(dataloader, "train_items"):
            try:
                inferred = len(getattr(dataloader, "train_items"))
            except Exception:
                inferred = None

    if inferred is not None and inferred <= 0:
        inferred = None

    if configured > 0 and inferred is not None and configured != inferred:
        raise ValueError(
            f"Configured train_size={configured} does not match loaded train split "
            f"size={inferred}. Fix the config or the dataset split."
        )

    train_size = configured if configured > 0 else inferred
    if train_size is None or train_size <= 0:
        raise ValueError(
            "Unable to determine train_size automatically. "
            "Provide train.train_size in the config for this environment."
        )
    return int(train_size)


def _compute_task_type_buckets(results: list[dict], task_types: list[str]) -> dict[str, dict]:
    """Compute per-task-type success rates."""
    buckets: dict[str, dict] = {}
    for task in task_types + ["overall"]:
        buckets[task] = {"total": 0, "hard": 0, "soft": 0.0}
    for r in results:
        tt = r.get("task_type", "other")
        for key in [tt, "overall"]:
            if key not in buckets:
                buckets[key] = {"total": 0, "hard": 0, "soft": 0.0}
            buckets[key]["total"] += 1
            buckets[key]["hard"] += float(r.get("hard", 0))
            buckets[key]["soft"] += float(r.get("soft", 0.0))
    return buckets


def _format_rejection_buffer(buffer: list[dict]) -> str:
    """**DEPRECATED** — kept for backward compat; use _format_step_buffer."""
    return _format_step_buffer(buffer)


def _extract_failure_patterns(
    rollout_results: list[dict],
    step_dir: str,
) -> list[dict]:
    """Extract compact failure patterns from rollout results.

    Uses analyst ``failure_summary`` from minibatch patches when available,
    otherwise falls back to ``fail_reason`` prefix grouping.
    """
    failures = [r for r in rollout_results if not r.get("hard") or float(r.get("hard", 0)) < 1e-9]
    if not failures:
        return []

    # Group by fail_reason prefix
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in failures:
        reason = r.get("fail_reason", "unknown")
        prefix = reason.split(":")[0].strip() if ":" in reason else reason
        groups[prefix].append(r)

    # Try richer descriptions from analyst patches
    analyst_descs: list[str] = []
    patch_globs = [
        os.path.join(step_dir, "patches", "minibatch_fail_*.json"),
        os.path.join(step_dir, "batch_*", "patches", "minibatch_fail_*.json"),
    ]
    seen_patch_files: set[str] = set()
    for pattern in patch_globs:
        for fname in sorted(glob.glob(pattern)):
            if fname in seen_patch_files:
                continue
            seen_patch_files.add(fname)
            try:
                with open(fname) as f:
                    patch = json.load(f)
                for fs in patch.get("failure_summary", []):
                    ft = fs.get("failure_type", "")
                    sd = fs.get("description", "")
                    analyst_descs.append(f"{ft}: {sd}" if sd else ft)
            except Exception:
                pass

    patterns = []
    desc_iter = iter(analyst_descs)
    for prefix, items in groups.items():
        desc = next(desc_iter, None) or prefix
        patterns.append({
            "pattern": desc,
            "count": len(items),
            "task_ids": [str(r.get("id", "?")) for r in items],
        })
    return patterns


def _format_step_buffer(buffer: list[dict]) -> str:
    """Format the unified step buffer into a single context block.

    Each entry captures what happened at a previous step: failure patterns
    observed during rollout, and — when the step was rejected — the specific
    edits that were tried and the resulting score drop.

    Returns empty string when *buffer* is empty.
    """
    if not buffer:
        return ""

    parts = [
        "Below is a summary of previous steps in this epoch. "
        "Use it to avoid repeating ineffective edits and to prioritise "
        "failure patterns that remain unsolved.\n"
    ]

    for entry in buffer:
        step = entry["step"]
        action = entry["action"]
        n_fail = entry.get("n_fail", 0)
        n_total = entry.get("n_total", "?")

        parts.append(f"### Step {step} — {action.upper()} ({n_fail}/{n_total} failed)")

        # Failure patterns
        for p in entry.get("failure_patterns", []):
            ids = ", ".join(p["task_ids"][:3])
            parts.append(f'  - "{p["pattern"]}" (×{p["count"]}, tasks: {ids})')

        # Rejected edits (only present on reject)
        rejected = entry.get("rejected_edits", [])
        if rejected:
            score_before = entry.get("score_before", "?")
            score_after = entry.get("score_after", "?")
            parts.append(
                f"  Rejected edits (score {score_before} → {score_after}):"
            )
            for i, e in enumerate(rejected, 1):
                if e.get("op") is not None:
                    op = e.get("op", "?")
                    content = e.get("content", "")
                    target = e.get("target", "")
                    if target:
                        parts.append(f'    {i}. [{op}] target="{target[:80]}" → "{content}"')
                    else:
                        parts.append(f'    {i}. [{op}] "{content}"')
                else:
                    kind = e.get("type", "?")
                    title = e.get("title", "")
                    instruction = e.get("instruction", "")
                    parts.append(f'    {i}. [{kind}] "{title}" → "{instruction}"')

    return "\n".join(parts)


# ── Trainer ──────────────────────────────────────────────────────────────────

class ReflACTTrainer:
    """Main ReflACT training loop.

    Parameters
    ----------
    cfg : dict
        Configuration dictionary. See ``configs/alfworld_default.yaml``
        for the full list of keys.
    adapter : EnvAdapter
        Environment adapter instance.
    """

    def __init__(self, cfg: dict, adapter: EnvAdapter) -> None:
        self.cfg = cfg
        self.adapter = adapter

    def train(self) -> dict:
        """Execute the full ReflACT training loop. Returns summary dict."""
        cfg = self.cfg
        adapter = self.adapter
        out_root = cfg["out_root"]
        os.makedirs(out_root, exist_ok=True)

        # ── Adapter setup (one-time init) ────────────────────────────
        adapter.setup(cfg)
        dataloader = adapter.get_dataloader()

        def _build_train_env(batch: BatchSpec):
            env_manager = adapter.build_env_from_batch(batch, out_root=out_root)
            return env_manager, batch.batch_size, batch.seed

        def _build_eval_env(split: str, env_num: int, seed: int):
            if dataloader is None:
                env_manager = adapter.build_eval_env(
                    env_num=env_num,
                    split=split,
                    seed=seed,
                    out_root=out_root,
                )
                actual_n = len(env_manager) if hasattr(env_manager, "__len__") else env_num
                return env_manager, actual_n

            batch = dataloader.build_eval_batch(
                env_num=env_num,
                split=split,
                seed=seed,
                out_root=out_root,
            )
            env_manager = adapter.build_env_from_batch(batch, out_root=out_root)
            return env_manager, batch.batch_size

        # ── Configure models ─────────────────────────────────────────────
        backend = cfg.get("model_backend", "azure_openai")
        configure_azure_openai(
            endpoint=(
                cfg.get("azure_openai_endpoint")
                or cfg.get("azure_endpoint")
                or None
            ),
            api_version=(
                cfg.get("azure_openai_api_version")
                or cfg.get("azure_api_version")
                or None
            ),
            api_key=(
                cfg.get("azure_openai_api_key")
                or cfg.get("azure_api_key")
                or None
            ),
            auth_mode=cfg.get("azure_openai_auth_mode") or None,
            ad_scope=cfg.get("azure_openai_ad_scope") or None,
            managed_identity_client_id=cfg.get("azure_openai_managed_identity_client_id") or None,
            optimizer_endpoint=cfg.get("optimizer_azure_openai_endpoint") or None,
            optimizer_api_version=cfg.get("optimizer_azure_openai_api_version") or None,
            optimizer_api_key=cfg.get("optimizer_azure_openai_api_key") or None,
            optimizer_auth_mode=cfg.get("optimizer_azure_openai_auth_mode") or None,
            optimizer_ad_scope=cfg.get("optimizer_azure_openai_ad_scope") or None,
            optimizer_managed_identity_client_id=(
                cfg.get("optimizer_azure_openai_managed_identity_client_id") or None
            ),
            target_endpoint=cfg.get("target_azure_openai_endpoint") or None,
            target_api_version=cfg.get("target_azure_openai_api_version") or None,
            target_api_key=cfg.get("target_azure_openai_api_key") or None,
            target_auth_mode=cfg.get("target_azure_openai_auth_mode") or None,
            target_ad_scope=cfg.get("target_azure_openai_ad_scope") or None,
            target_managed_identity_client_id=(
                cfg.get("target_azure_openai_managed_identity_client_id") or None
            ),
        )
        optimizer_backend = cfg.get("optimizer_backend")
        target_backend = cfg.get("target_backend")
        if not optimizer_backend or not target_backend:
            if backend in {"claude", "claude_chat"}:
                optimizer_backend = optimizer_backend or "claude_chat"
                target_backend = target_backend or "claude_chat"
            elif backend in {"codex", "codex_exec"}:
                optimizer_backend = optimizer_backend or "openai_chat"
                target_backend = target_backend or "codex_exec"
            elif backend == "claude_code_exec":
                optimizer_backend = optimizer_backend or "openai_chat"
                target_backend = target_backend or "claude_code_exec"
            elif backend in {"qwen", "qwen_chat"}:
                optimizer_backend = optimizer_backend or "openai_chat"
                target_backend = target_backend or "qwen_chat"
            else:
                optimizer_backend = optimizer_backend or "openai_chat"
                target_backend = target_backend or "openai_chat"
            cfg["optimizer_backend"] = optimizer_backend
            cfg["target_backend"] = target_backend
        set_optimizer_backend(optimizer_backend)
        set_target_backend(target_backend)
        set_optimizer_deployment(cfg["optimizer_model"])
        set_target_deployment(cfg["target_model"])
        configure_codex_exec(
            path=cfg.get("codex_exec_path", "codex"),
            sandbox=cfg.get("codex_exec_sandbox", "workspace-write"),
            profile=cfg.get("codex_exec_profile", ""),
            full_auto=cfg.get("codex_exec_full_auto", False),
            reasoning_effort=cfg.get("codex_exec_reasoning_effort", "none"),
            use_sdk=cfg.get("codex_exec_use_sdk", None),
            network_access=cfg.get("codex_exec_network_access", False),
            web_search=cfg.get("codex_exec_web_search", False),
            approval_policy=cfg.get("codex_exec_approval_policy", "never"),
        )
        configure_claude_code_exec(
            path=cfg.get("claude_code_exec_path", "claude"),
            profile=cfg.get("claude_code_exec_profile", ""),
            use_sdk=cfg.get("claude_code_exec_use_sdk", None),
            effort=cfg.get("claude_code_exec_effort", cfg.get("reasoning_effort", "medium")),
            max_thinking_tokens=cfg.get("claude_code_exec_max_thinking_tokens", 16384),
        )
        configure_qwen_chat(
            base_url=cfg.get("qwen_chat_base_url") or None,
            api_key=cfg.get("qwen_chat_api_key") or None,
            temperature=cfg.get("qwen_chat_temperature"),
            timeout_seconds=cfg.get("qwen_chat_timeout_seconds"),
            max_tokens=cfg.get("qwen_chat_max_tokens"),
            enable_thinking=cfg.get("qwen_chat_enable_thinking"),
        )
        os.environ["REFLACT_CODEX_TRACE_TO_OPTIMIZER"] = (
            "1"
            if target_backend == "codex_exec" and cfg.get("codex_trace_to_optimizer", False)
            else "0"
        )
        reasoning = cfg.get("reasoning_effort", "") or None
        set_reasoning_effort(reasoning)
        print(
            f"  [model config] backend={backend}  "
            f"optimizer={cfg['optimizer_model']} ({optimizer_backend})  "
            f"target={cfg['target_model']} ({target_backend})  "
            f"reasoning={reasoning or 'off'}"
        )

        # ── Initialize Ray ───────────────────────────────────────────────
        if adapter.requires_ray():
            try:
                import ray
            except ImportError as e:
                raise ImportError(
                    "This environment requires ray, but ray is not installed."
                ) from e

            if not ray.is_initialized():
                ray.init(num_gpus=0)

        # ── Load initial skill ───────────────────────────────────────────
        skill_init_path = os.path.abspath(cfg["skill_init"])
        if os.path.exists(skill_init_path):
            with open(skill_init_path) as f:
                skill_init = f.read()
            print(f"  [initial skill] {skill_init_path} ({len(skill_init)} chars)")
        else:
            skill_init = ""
            print("  [initial skill] no initial skill file — starting from blank")

        # ── Training parameters ──────────────────────────────────────────
        batch_size = cfg["batch_size"]
        num_epochs = cfg["num_epochs"]
        accumulation = cfg["accumulation"]
        seed = cfg["seed"]
        merge_bs = cfg["merge_batch_size"]
        group_relative_enabled = bool(cfg.get("group_relative_enabled", False))
        rollouts_per_item = int(cfg.get("rollouts_per_item", 1) or 1)
        rollout_budget_per_step = int(
            cfg.get("rollout_budget_per_step", batch_size * rollouts_per_item)
            or batch_size * rollouts_per_item
        )
        min_valid_rollouts = int(cfg.get("min_valid_rollouts", 6) or 6)
        max_extra_attempts_per_item = int(
            cfg.get("max_extra_attempts_per_item", 4) or 0
        )
        retry_timeout = int(cfg.get("retry_timeout", 900) or 900)
        agent_failures_are_valid_evidence = bool(
            cfg.get("agent_failures_are_valid_evidence", False)
        )
        group_sampling_strategy = str(
            cfg.get("group_sampling_strategy", "legacy_epoch_shuffle")
            or "legacy_epoch_shuffle"
        ).strip().lower()
        coverage_passes = int(cfg.get("coverage_passes", 1) or 1)
        mixed_replay_steps = int(cfg.get("mixed_replay_steps", 0) or 0)
        coverage_fill_strategy = str(
            cfg.get("coverage_fill_strategy", "deterministic")
            or "deterministic"
        ).strip().lower()
        raw_fill_priority_ids = cfg.get("coverage_fill_priority_ids", [])
        if isinstance(raw_fill_priority_ids, str):
            coverage_fill_priority_ids = [
                task_id.strip()
                for task_id in raw_fill_priority_ids.split(",")
                if task_id.strip()
            ]
        elif isinstance(raw_fill_priority_ids, (list, tuple)):
            coverage_fill_priority_ids = [
                str(task_id).strip()
                for task_id in raw_fill_priority_ids
                if str(task_id).strip()
            ]
        else:
            raise ValueError(
                "coverage_fill_priority_ids must be a comma-separated "
                "string or a list"
            )
        configured_steps_per_epoch = int(
            cfg.get("steps_per_epoch", 0) or 0
        )
        steps_per_epoch_schedule = _normalise_steps_per_epoch_schedule(
            cfg.get("steps_per_epoch_schedule", []),
            num_epochs=num_epochs,
        )
        ensure_contrast_candidate = bool(
            cfg.get("ensure_contrast_candidate", False)
        )
        deduplicate_ordinary_reflect = bool(
            cfg.get("deduplicate_ordinary_reflect", False)
        )
        group_hypothesis_bank_enabled = bool(
            cfg.get("group_hypothesis_bank_enabled", False)
        )
        failure_hypothesis_bank_enabled = bool(
            cfg.get("failure_hypothesis_bank_enabled", False)
        )
        group_dual_memory_enabled = bool(
            cfg.get("group_dual_memory_enabled", False)
        )
        skillopt_failure_hypothesis_bank_enabled = bool(
            cfg.get("skillopt_failure_hypothesis_bank_enabled", False)
        )
        record_slow_update_variants = bool(
            cfg.get("record_slow_update_variants", False)
        )
        bounded_slow_update = bool(
            cfg.get("bounded_slow_update", DEFAULT_BOUNDED_SLOW_UPDATE)
        )
        repair_slow_target_edits_enabled = bool(
            cfg.get(
                "repair_slow_target_edits",
                DEFAULT_PROTECTED_EDIT_RELOCATION,
            )
        )
        selection_eval_repeats = int(
            cfg.get("selection_eval_repeats", 1) or 1
        )
        selection_baseline_results_path = str(
            cfg.get("selection_baseline_results_path", "") or ""
        ).strip()
        max_analyst_rounds = int(cfg.get("max_analyst_rounds", 3) or 3)
        update_mode = normalize_update_mode(cfg.get("skill_update_mode", "patch"))
        lr_control_mode = _normalise_lr_control_mode(cfg.get("lr_control_mode", "fixed"))
        if is_full_rewrite_minibatch_mode(update_mode):
            lr_control_mode = "none"
        longitudinal_pair_policy = _normalise_longitudinal_pair_policy(
            cfg.get("longitudinal_pair_policy", "mixed")
        )
        rewrite_reasoning_effort = cfg.get("rewrite_reasoning_effort", "high")
        if rewrite_reasoning_effort == "":
            rewrite_reasoning_effort = None
        rewrite_max_completion_tokens = int(cfg.get("rewrite_max_completion_tokens", 64000))
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        if accumulation <= 0:
            raise ValueError(f"accumulation must be positive, got {accumulation}")
        if configured_steps_per_epoch < 0:
            raise ValueError(
                "steps_per_epoch must be non-negative; use 0 or omit it "
                "for automatic scheduling"
            )
        if steps_per_epoch_schedule and configured_steps_per_epoch > 0:
            raise ValueError(
                "steps_per_epoch_schedule cannot be combined with a positive "
                "steps_per_epoch"
            )
        if steps_per_epoch_schedule and not (
            group_relative_enabled
            and group_sampling_strategy == "coverage_then_mixed"
        ):
            raise ValueError(
                "steps_per_epoch_schedule is isolated to Group-Relative "
                "coverage_then_mixed runs"
            )
        if group_relative_enabled:
            if rollouts_per_item <= 1:
                raise ValueError(
                    "Group-Relative SkillOpt requires rollouts_per_item > 1"
                )
            if min_valid_rollouts <= 0:
                raise ValueError("min_valid_rollouts must be positive")
            if max_extra_attempts_per_item < 0:
                raise ValueError(
                    "max_extra_attempts_per_item must be non-negative"
                )
            if min_valid_rollouts > (
                rollouts_per_item + max_extra_attempts_per_item
            ):
                raise ValueError(
                    "min_valid_rollouts cannot exceed rollouts_per_item + "
                    "max_extra_attempts_per_item"
                )
            if retry_timeout <= 0:
                raise ValueError("retry_timeout must be positive")
            if group_sampling_strategy not in {
                "legacy_epoch_shuffle",
                "coverage_then_mixed",
            }:
                raise ValueError(
                    "group_sampling_strategy must be "
                    "'legacy_epoch_shuffle' or 'coverage_then_mixed'"
                )
            if coverage_passes <= 0:
                raise ValueError("coverage_passes must be positive")
            if mixed_replay_steps < 0:
                raise ValueError("mixed_replay_steps must be non-negative")
            if coverage_fill_strategy not in {
                "deterministic",
                "mixed_first",
            }:
                raise ValueError(
                    "coverage_fill_strategy must be 'deterministic' or "
                    "'mixed_first'"
                )
            actual_budget = batch_size * rollouts_per_item
            if actual_budget != rollout_budget_per_step:
                raise ValueError(
                    "Group rollout budget mismatch: "
                    f"batch_size({batch_size}) * "
                    f"rollouts_per_item({rollouts_per_item}) = "
                    f"{actual_budget}, configured rollout_budget_per_step="
                    f"{rollout_budget_per_step}"
                )
        if repair_slow_target_edits_enabled and update_mode != "patch":
            raise ValueError(
                "repair_slow_target_edits currently requires "
                "skill_update_mode=patch"
            )
        if selection_eval_repeats < 1:
            raise ValueError("selection_eval_repeats must be >= 1")
        if failure_hypothesis_bank_enabled and not group_relative_enabled:
            raise ValueError(
                "failure_hypothesis_bank_enabled requires group rollouts"
            )
        if group_dual_memory_enabled and not group_relative_enabled:
            raise ValueError(
                "group_dual_memory_enabled requires group rollouts"
            )
        if group_dual_memory_enabled and not (
            group_hypothesis_bank_enabled
            and failure_hypothesis_bank_enabled
        ):
            raise ValueError(
                "Group Dual Memory requires both contrast and failure "
                "hypothesis banks"
            )
        if group_dual_memory_enabled and update_mode != "patch":
            raise ValueError(
                "Group Dual Memory currently requires "
                "skill_update_mode=patch"
            )
        if skillopt_failure_hypothesis_bank_enabled and group_relative_enabled:
            raise ValueError(
                "skillopt_failure_hypothesis_bank_enabled is an ordinary "
                "SkillOpt ablation and cannot be combined with group rollouts"
            )
        if (
            skillopt_failure_hypothesis_bank_enabled
            and failure_hypothesis_bank_enabled
        ):
            raise ValueError(
                "ordinary SkillOpt failure memory and Group failure bank "
                "cannot be enabled together"
            )

        group_memory_dir = (
            "memory"
            if group_dual_memory_enabled
            else "group_relative"
        )
        failure_hypothesis_bank = None
        if failure_hypothesis_bank_enabled:
            failure_bank_cls = (
                FailureMemoryBank
                if group_dual_memory_enabled
                else FailureHypothesisBank
            )
            failure_hypothesis_bank = failure_bank_cls(
                os.path.join(
                    out_root,
                    group_memory_dir,
                    "failure_hypothesis_bank.json",
                )
            )
        contrast_hypothesis_bank_path = (
            os.path.join(
                out_root,
                group_memory_dir,
                "contrast_hypothesis_bank.json",
            )
            if group_dual_memory_enabled
            else ""
        )
        skillopt_failure_hypothesis_bank = (
            SkillOptFailureHypothesisBank(
                os.path.join(
                    out_root,
                    "failure_memory",
                    "failure_hypothesis_bank.json",
                )
            )
            if skillopt_failure_hypothesis_bank_enabled
            else None
        )

        train_size = _resolve_train_size(cfg, dataloader)
        coverage_steps = 0
        if (
            group_relative_enabled
            and group_sampling_strategy == "coverage_then_mixed"
        ):
            coverage_steps = math.ceil(
                train_size * coverage_passes / (batch_size * accumulation)
            )
            total_steps = coverage_steps + mixed_replay_steps
            if steps_per_epoch_schedule:
                scheduled_total = sum(steps_per_epoch_schedule)
                if scheduled_total != total_steps:
                    raise ValueError(
                        "steps_per_epoch_schedule must sum to the coverage "
                        "schedule total: "
                        f"sum={scheduled_total} total_steps={total_steps}"
                    )
                steps_per_epoch = max(steps_per_epoch_schedule)
                steps_per_epoch_source = "explicit coverage schedule"
            else:
                steps_per_epoch = math.ceil(total_steps / num_epochs)
                steps_per_epoch_source = "coverage schedule"
        elif configured_steps_per_epoch > 0:
            # Explicit scheduling is useful when a new split changes the
            # number of items but the experiment must retain the original
            # optimizer/slow-update cadence.  Defaults remain auto-derived.
            steps_per_epoch = configured_steps_per_epoch
            total_steps = num_epochs * steps_per_epoch
            steps_per_epoch_source = "explicit"
        else:
            step_budget = (
                rollout_budget_per_step
                if group_relative_enabled
                else batch_size
            )
            steps_per_epoch = math.ceil(
                train_size / (step_budget * accumulation)
            )
            total_steps = num_epochs * steps_per_epoch
            steps_per_epoch_source = "auto"
        batches_per_epoch = steps_per_epoch * accumulation

        # Persist resolved derived fields so config.json / summary.json match
        # the actual runtime recipe.
        cfg["train_size"] = train_size
        cfg["steps_per_epoch"] = steps_per_epoch
        cfg["batches_per_epoch"] = batches_per_epoch
        if steps_per_epoch_schedule:
            cfg["steps_per_epoch_schedule"] = steps_per_epoch_schedule
            cfg["batches_per_epoch_schedule"] = [
                value * accumulation for value in steps_per_epoch_schedule
            ]
        cfg["samples_per_epoch"] = train_size
        cfg["skill_update_mode"] = update_mode
        cfg["lr_control_mode"] = lr_control_mode
        if group_relative_enabled:
            cfg["unique_batch_size"] = batch_size
            cfg["rollouts_per_item"] = rollouts_per_item
            cfg["rollout_budget_per_step"] = rollout_budget_per_step
            cfg["min_valid_rollouts"] = min_valid_rollouts
            cfg["max_extra_attempts_per_item"] = max_extra_attempts_per_item
            cfg["retry_timeout"] = retry_timeout
            cfg["agent_failures_are_valid_evidence"] = (
                agent_failures_are_valid_evidence
            )
            cfg["group_sampling_strategy"] = group_sampling_strategy
            cfg["coverage_passes"] = coverage_passes
            cfg["mixed_replay_steps"] = mixed_replay_steps
            cfg["coverage_fill_strategy"] = coverage_fill_strategy
            cfg["coverage_fill_priority_ids"] = coverage_fill_priority_ids
            cfg["ensure_contrast_candidate"] = ensure_contrast_candidate
            cfg["deduplicate_ordinary_reflect"] = (
                deduplicate_ordinary_reflect
            )
            cfg["coverage_steps"] = coverage_steps
            cfg["total_steps"] = total_steps

        # Save config after deriving runtime values.
        with open(os.path.join(out_root, "config.json"), "w") as f:
            json.dump(_redact_cfg(cfg), f, indent=2, ensure_ascii=False)

        train_pool_size = train_size
        group_sampler = None
        if (
            group_relative_enabled
            and group_sampling_strategy == "coverage_then_mixed"
        ):
            if dataloader is None or not hasattr(
                dataloader, "get_split_items"
            ):
                raise ValueError(
                    "coverage_then_mixed requires a split-backed dataloader "
                    "with stable training item IDs"
                )
            train_items = list(dataloader.get_split_items("train"))
            if train_size < len(train_items):
                train_items = train_items[:train_size]
            if len(train_items) != train_size:
                raise ValueError(
                    "Resolved train_size does not match available training "
                    f"items: train_size={train_size}, items={len(train_items)}"
                )
            group_sampler = CoverageThenMixedSampler(
                items=train_items,
                batch_size=batch_size,
                seed=seed,
                state_path=os.path.join(
                    out_root, "group_sampler_state.json"
                ),
                coverage_passes=coverage_passes,
                coverage_fill_strategy=coverage_fill_strategy,
                coverage_fill_priority_ids=coverage_fill_priority_ids,
            )

        scheduler = build_scheduler(
            mode=cfg.get("lr_scheduler", "constant"),
            max_lr=cfg["edit_budget"],
            min_lr=cfg.get("min_edit_budget", 2),
            total_steps=total_steps,
        )

        # Fixed training pool: base seeds (each seed = one deterministic batch)
        if dataloader is not None:
            base_seeds = dataloader.make_base_seeds(
                steps_per_epoch=steps_per_epoch,
                accumulation=accumulation,
                seed=seed,
            )
        else:
            base_seeds = [seed + i + 1 for i in range(batches_per_epoch)]

        schedule_text = (
            f" schedule={steps_per_epoch_schedule}"
            if steps_per_epoch_schedule
            else ""
        )
        print(f"\n  [config] epochs={num_epochs} steps/epoch={steps_per_epoch} "
              f"({steps_per_epoch_source}){schedule_text} "
              f"accum={accumulation} batch_size={batch_size}")
        if group_relative_enabled:
            print(
                f"  [config] method=group_relative_skillopt "
                f"unique_batch={batch_size} repeats={rollouts_per_item} "
                f"rollout_budget/step={rollout_budget_per_step} "
                f"min_valid={min_valid_rollouts} "
                f"max_extra/item={max_extra_attempts_per_item} "
                f"retry_timeout={retry_timeout}s "
                f"agent_failures_valid="
                f"{agent_failures_are_valid_evidence}"
            )
            print(
                f"  [config] sampling={group_sampling_strategy} "
                f"coverage_passes={coverage_passes} "
                f"coverage_steps={coverage_steps or 'legacy'} "
                f"mixed_replay_steps={mixed_replay_steps} "
                f"coverage_fill={coverage_fill_strategy} "
                f"ensure_contrast={ensure_contrast_candidate} "
                f"dedup_ordinary_reflect={deduplicate_ordinary_reflect}"
            )
        print(f"  [config] train_size={train_size}")
        print(f"  [config] batches/epoch={batches_per_epoch} "
              f"total_steps={total_steps} "
              f"games/epoch={train_pool_size}")
        print(f"  [config] lr_scheduler={cfg.get('lr_scheduler', 'constant')} "
              f"edit_budget={cfg['edit_budget']} "
              f"min_edit_budget={cfg.get('min_edit_budget', 2)}")
        print(f"  [config] skill_update_mode={update_mode} "
              f"lr_control_mode={lr_control_mode} "
              f"rewrite_reasoning_effort={rewrite_reasoning_effort or 'off'} "
              f"rewrite_max_completion_tokens={rewrite_max_completion_tokens} "
              f"max_analyst_rounds={max_analyst_rounds}")
        print(f"  [config] longitudinal_pair_policy={longitudinal_pair_policy}")
        print(
            f"  [config] selection_eval_repeats={selection_eval_repeats} "
            f"baseline_reuse="
            f"{selection_baseline_results_path or 'off'}"
        )
        print(f"  [config] base_seeds={base_seeds}")

        # ── Resume check ─────────────────────────────────────────────────
        history = _load_history(out_root)
        runtime_state = _load_runtime_state(out_root)
        if runtime_state:
            last_step = int(runtime_state.get("last_completed_step", 0) or 0)
            current_skill_path = runtime_state.get("current_skill_path") or os.path.join(
                out_root, "skills", f"skill_v{last_step:04d}.md",
            )
            with open(current_skill_path) as f:
                current_skill = f.read()
            best_skill_path = runtime_state.get("best_skill_path") or os.path.join(
                out_root, "best_skill.md",
            )
            if os.path.exists(best_skill_path):
                with open(best_skill_path) as f:
                    best_skill = f.read()
            else:
                best_skill = current_skill
            current_score = float(runtime_state.get("current_score", -1.0) or -1.0)
            best_score = float(runtime_state.get("best_score", current_score) or current_score)
            best_step = runtime_state.get("best_step", last_step)
            current_origin = str(
                runtime_state.get("current_origin")
                or (f"step_{last_step:04d}" if last_step > 0 else "initial_skill")
            )
            best_origin = str(runtime_state.get("best_origin") or current_origin)
            resume_from = last_step + 1
            scheduler.load_state_dict({"current_step": last_step})
            print(
                f"  [resume] from step {resume_from}  "
                f"current={current_score:.4f} best={best_score:.4f} "
                f"(origin={current_origin})"
            )
        elif history:
            last_step = history[-1]["step"]
            current_skill = _load_skill(out_root, last_step)
            best_rec = max(history, key=lambda h: h.get("best_score", 0.0))
            best_score = best_rec["best_score"]
            best_step = best_rec["best_step"]
            best_skill_path = os.path.join(out_root, "best_skill.md")
            if os.path.exists(best_skill_path):
                with open(best_skill_path) as f:
                    best_skill = f.read()
            else:
                best_skill = _load_skill(out_root, best_step)
            current_score = history[-1].get("current_score", best_score)
            current_origin = f"step_{last_step:04d}"
            best_origin = f"step_{int(best_step):04d}" if isinstance(best_step, int) else str(best_step)
            resume_from = last_step + 1
            scheduler.load_state_dict({"current_step": last_step})
            print(
                f"  [resume] from step {resume_from}  "
                f"current={current_score:.4f} best={best_score:.4f}"
            )
        else:
            current_skill = skill_init
            best_skill = skill_init
            best_score = -1.0
            current_score = -1.0
            best_step = 0
            current_origin = "initial_skill"
            best_origin = "initial_skill"
            resume_from = 1

        _save_skill(out_root, 0, skill_init)

        def _persist_runtime_state(last_completed_step: int) -> None:
            _save_runtime_state(
                out_root,
                {
                    "last_completed_step": last_completed_step,
                    "current_skill_path": os.path.join(
                        out_root, "skills", f"skill_v{last_completed_step:04d}.md",
                    ),
                    "current_score": current_score,
                    "current_origin": current_origin,
                    "best_skill_path": os.path.join(out_root, "best_skill.md"),
                    "best_score": best_score,
                    "best_step": best_step,
                    "best_origin": best_origin,
                },
            )

        # ── Selection cache ──────────────────────────────────────────────
        sel_cache: dict[str, tuple[float, float]] = {}
        for rec in history:
            sh = rec.get("candidate_hash", "")
            if sh and rec.get("selection_hard") is not None:
                sel_cache[sh] = (rec["selection_hard"], rec["selection_soft"])

        # ── Baseline evaluation on selection set ─────────────────────────
        if cfg.get("use_gate") is False:
            raise ValueError(
                "Gate validation is mandatory in this branch. Remove "
                "`evaluation.use_gate=false` from the config."
            )
        gate_metric = str(cfg.get("gate_metric", "hard")).strip().lower()
        if gate_metric not in {"hard", "soft", "mixed"}:
            raise ValueError(
                f"evaluation.gate_metric must be 'hard' | 'soft' | 'mixed', "
                f"got {gate_metric!r}"
            )
        gate_mixed_weight = float(cfg.get("gate_mixed_weight", 0.5))
        gate_accept_equal = bool(cfg.get("gate_accept_equal", False))
        if not 0.0 <= gate_mixed_weight <= 1.0:
            raise ValueError(
                f"evaluation.gate_mixed_weight must be in [0, 1], "
                f"got {gate_mixed_weight}"
            )
        print(
            f"  [gate] metric={gate_metric}"
            + (
                f" mixed_weight={gate_mixed_weight}"
                if gate_metric == "mixed"
                else ""
            )
            + f" accept_equal={gate_accept_equal}"
        )
        if current_score < 0:
            print(f"\n{'='*60}")
            print("  BASELINE — evaluate initial skill on Selection set (valid_seen)")
            print(f"{'='*60}")
            baseline_dir = os.path.join(out_root, "selection_eval_baseline")
            if selection_baseline_results_path:
                _, sel_n = _build_eval_env(
                    split="valid_seen",
                    env_num=cfg["sel_env_num"],
                    seed=seed,
                )
                print(f"  Selection items: {sel_n}")
                (
                    baseline_results,
                    baseline_hard,
                    baseline_soft,
                ) = load_reused_selection_results(
                    selection_baseline_results_path,
                    expected_items=sel_n,
                )
                record_reused_selection_baseline(
                    out_dir=baseline_dir,
                    source_path=selection_baseline_results_path,
                    result_rows=len(baseline_results),
                    hard=baseline_hard,
                    soft=baseline_soft,
                )
                print(
                    "  [baseline reuse] skipped target-model rollout; "
                    f"source={selection_baseline_results_path}"
                )
            elif selection_eval_repeats > 1:
                baseline_hard, baseline_soft, baseline_summary = (
                    evaluate_selection_repeats(
                        adapter=adapter,
                        build_env=lambda: _build_eval_env(
                            split="valid_seen",
                            env_num=cfg["sel_env_num"],
                            seed=seed,
                        ),
                        skill_content=skill_init,
                        out_dir=baseline_dir,
                        repeats=selection_eval_repeats,
                    )
                )
                print(
                    "  [baseline repeats] "
                    f"rows={baseline_summary['total_result_rows']} "
                    f"runs={selection_eval_repeats}"
                )
                print(
                    "  Selection items: "
                    f"{baseline_summary['expected_items_per_repeat']}"
                )
            else:
                sel_env, sel_n = _build_eval_env(
                    split="valid_seen",
                    env_num=cfg["sel_env_num"],
                    seed=seed,
                )
                print(f"  Selection items: {sel_n}")
                baseline_results = adapter.rollout(
                    sel_env, skill_init, baseline_dir,
                )
                baseline_hard, baseline_soft = compute_score(
                    baseline_results,
                )
            current_score = select_gate_score(
                baseline_hard, baseline_soft, gate_metric, gate_mixed_weight,
            )
            best_score = current_score
            sh = skill_hash(skill_init)
            sel_cache[sh] = (baseline_hard, baseline_soft)
            current_origin = "initial_skill"
            best_origin = "initial_skill"
            _persist_runtime_state(0)
            print(
                f"  [baseline result] selection hard={baseline_hard:.4f} "
                f"soft={baseline_soft:.4f} "
                f"gate[{gate_metric}]={current_score:.4f}"
            )

        # ── Training loop ────────────────────────────────────────────────
        t_loop_start = time.time()

        if resume_from > total_steps:
            print(f"\n  [skip] all {total_steps} steps complete — jumping to evaluation")

        global_step = 0
        for epoch in range(1, num_epochs + 1):
            if global_step >= total_steps:
                break
            if group_sampler is not None:
                epoch_batches = []
                sampling_status = group_sampler.summary()
            elif dataloader is not None:
                epoch_batches = dataloader.plan_train_epoch(
                    epoch=epoch,
                    steps_per_epoch=steps_per_epoch,
                    accumulation=accumulation,
                    batch_size=batch_size,
                    seed=seed,
                    out_root=out_root,
                )
                shuffled_seeds = [batch.seed for batch in epoch_batches]
            else:
                epoch_batches = []
                epoch_rng = random.Random(seed + epoch * 1000)
                shuffled_seeds = base_seeds.copy()
                epoch_rng.shuffle(shuffled_seeds)

            # Step buffer: accumulates per-step context (failure patterns +
            # rejected edits) within this epoch so optimizers see full history.
            step_buffer: list[dict] = []
            active_meta_skill = (
                _load_meta_skill_content(out_root, epoch - 1)
                if cfg.get("use_meta_skill", False)
                else ""
            )

            if group_sampler is not None:
                planned_steps = (
                    steps_per_epoch_schedule[epoch - 1]
                    if steps_per_epoch_schedule
                    else steps_per_epoch
                )
                steps_this_epoch = min(
                    planned_steps,
                    total_steps - global_step,
                )
                print(
                    f"\n  [EPOCH {epoch}/{num_epochs}] "
                    f"sampling={sampling_status['sampling_strategy']} "
                    f"phase={sampling_status['sampling_phase']} "
                    f"coverage="
                    f"{sampling_status['coverage_items_selected']}/"
                    f"{sampling_status['coverage_items_target']} "
                    f"replay_batches="
                    f"{sampling_status['mixed_replay_batches']} "
                    f"steps_this_epoch={steps_this_epoch}"
                )
            else:
                steps_this_epoch = steps_per_epoch
                print(
                    f"\n  [EPOCH {epoch}/{num_epochs}] "
                    f"shuffled_seeds={shuffled_seeds}"
                )
            if active_meta_skill:
                print(
                    f"  [meta skill] loaded from epoch {epoch - 1} "
                    f"({len(active_meta_skill)} chars)"
                )

            for step_in_epoch in range(steps_this_epoch):
                if global_step >= total_steps:
                    break
                global_step += 1
                if global_step < resume_from:
                    continue

                step_t0 = time.time()
                step_dir = os.path.join(out_root, "steps", f"step_{global_step:04d}")
                os.makedirs(step_dir, exist_ok=True)

                tokens_before = get_token_summary()

                print(
                    f"\n  [STEP {global_step}/{total_steps}] "
                    f"epoch={epoch} step_in_epoch={step_in_epoch} "
                    f"{'='*30}"
                )

                step_rec: dict = {
                    "step": global_step,
                    "epoch": epoch,
                    "step_in_epoch": step_in_epoch,
                    "timing": {},
                    "tokens": {},
                }

                # ── Accumulation: Rollout + Reflect ──────────────────────
                all_failure_patches: list[dict] = []
                all_success_patches: list[dict] = []
                all_contrast_patches: list[dict] = []
                all_raw_patches: list[dict | None] = []
                all_rollout_results: list[dict] = []
                accum_rollout_stats: list[dict] = []
                total_rollout_time = 0.0
                total_reflect_time = 0.0

                for a in range(accumulation):
                    batch_idx = step_in_epoch * accumulation + a
                    sampling_assignment = None
                    sampling_assignment_key = ""
                    if group_sampler is not None:
                        sampling_assignment_key = (
                            f"step_{global_step:04d}:batch_{a:02d}"
                        )
                        selected_items, sampling_assignment = (
                            group_sampler.select(sampling_assignment_key)
                        )
                        batch_seed = (
                            seed + global_step * 1000 + a + 1
                        )
                        batch_spec = dataloader.build_train_batch_from_items(
                            selected_items,
                            seed=batch_seed,
                            out_root=out_root,
                        )
                        train_env, train_n, batch_seed = _build_train_env(
                            batch_spec
                        )
                        step_rec.setdefault("sampling_batches", []).append(
                            sampling_assignment
                        )
                    elif dataloader is not None:
                        batch_spec = epoch_batches[batch_idx]
                        train_env, train_n, batch_seed = _build_train_env(batch_spec)
                    else:
                        batch_seed = shuffled_seeds[batch_idx]
                        train_env = adapter.build_train_env(
                            batch_size=batch_size,
                            seed=batch_seed,
                            out_root=out_root,
                        )
                        train_n = len(train_env) if hasattr(train_env, "__len__") else batch_size

                    # Directory routing
                    if accumulation > 1:
                        batch_dir = os.path.join(step_dir, f"batch_{a}")
                    else:
                        batch_dir = step_dir

                    rollout_dir = os.path.join(batch_dir, "rollout")
                    patches_dir = os.path.join(batch_dir, "patches")

                    # ① ROLLOUT ────────────────────────────────────────────
                    t_phase = time.time()
                    print(
                        f"    [1/6 ROLLOUT] train items={train_n} "
                        f"(from pool, batch_seed={batch_seed})"
                    )
                    grouped_rollout = None
                    ordinary_reflect_results = None
                    if group_relative_enabled:
                        grouped_rollout = run_grouped_rollout(
                            adapter=adapter,
                            env_manager=train_env,
                            skill_content=current_skill,
                            out_dir=rollout_dir,
                            rollouts_per_item=rollouts_per_item,
                            min_valid_rollouts=min_valid_rollouts,
                            max_extra_attempts_per_item=(
                                max_extra_attempts_per_item
                            ),
                            retry_timeout=retry_timeout,
                            use_eval_feedback=True,
                            agent_failures_are_valid_evidence=(
                                agent_failures_are_valid_evidence
                            ),
                        )
                        if group_sampler is not None:
                            group_sampler.record_groups(
                                sampling_assignment_key,
                                grouped_rollout.groups,
                            )
                            step_rec["sampling_summary"] = (
                                group_sampler.summary()
                            )
                        rollout_results = grouped_rollout.valid_results
                        if deduplicate_ordinary_reflect:
                            (
                                ordinary_reflect_results,
                                ordinary_reflect_manifest,
                            ) = select_ordinary_reflect_results(
                                valid_results=grouped_rollout.valid_results,
                                groups=grouped_rollout.groups,
                                seed=batch_seed,
                            )
                            with open(
                                os.path.join(
                                    rollout_dir,
                                    "ordinary_reflect_selection.json",
                                ),
                                "w",
                                encoding="utf-8",
                            ) as f:
                                json.dump(
                                    ordinary_reflect_manifest,
                                    f,
                                    ensure_ascii=False,
                                    indent=2,
                                )
                            print(
                                "    [group ordinary reflect] "
                                f"representatives={len(ordinary_reflect_results)}/"
                                f"{len(grouped_rollout.groups)} "
                                "(one per valid group; mixed -> failure)"
                            )
                        else:
                            ordinary_reflect_results = rollout_results
                        print(
                            "    [group stats] "
                            f"valid={grouped_rollout.stats['valid_rollouts']}/"
                            f"{grouped_rollout.stats['requested_rollouts']} "
                            f"mixed={grouped_rollout.stats['mixed_groups']}/"
                            f"{grouped_rollout.stats['unique_items']}"
                        )
                        write_global_group_summary(out_root, train_pool_size)
                    else:
                        rollout_results = adapter.rollout(
                            train_env, current_skill, rollout_dir,
                            use_eval_feedback=True,
                        )
                        ordinary_reflect_results = rollout_results
                    r_hard, r_soft = compute_score(rollout_results)
                    total_rollout_time += time.time() - t_phase
                    all_rollout_results.extend(rollout_results)
                    print(f"    [1/6 done] hard={r_hard:.4f} soft={r_soft:.4f}")

                    # ② REFLECT ────────────────────────────────────────────
                    t_phase = time.time()
                    pred_dir = os.path.join(rollout_dir, "predictions")

                    # Build step context from buffer
                    step_buffer_context = _format_step_buffer(step_buffer)

                    raw_patches = adapter.reflect(
                        ordinary_reflect_results, current_skill, batch_dir,
                        prediction_dir=pred_dir, patches_dir=patches_dir,
                        random_seed=batch_seed,
                        step_buffer_context=step_buffer_context,
                        meta_skill_context=active_meta_skill,
                        failure_hypothesis_bank_context=(
                            failure_hypothesis_bank.render_context()
                            if failure_hypothesis_bank is not None
                            else ""
                        ),
                        skillopt_failure_memory_context=(
                            skillopt_failure_hypothesis_bank.render_context()
                            if skillopt_failure_hypothesis_bank is not None
                            else ""
                        ),
                        attach_failure_task_provenance=(
                            skillopt_failure_hypothesis_bank is not None
                        ),
                    )
                    if failure_hypothesis_bank is not None:
                        for raw_patch in raw_patches:
                            if (
                                not isinstance(raw_patch, dict)
                                or raw_patch.get("source_type") != "failure"
                            ):
                                continue
                            failure_hypothesis_bank.integrate(
                                raw_patch,
                                group_ids=[
                                    str(item)
                                    for item in raw_patch.get("group_ids", [])
                                ],
                                step=global_step,
                                update_mode=update_mode,
                            )
                    if skillopt_failure_hypothesis_bank is not None:
                        for raw_patch in raw_patches:
                            if (
                                not isinstance(raw_patch, dict)
                                or raw_patch.get("source_type") != "failure"
                            ):
                                continue
                            skillopt_failure_hypothesis_bank.integrate(
                                raw_patch,
                                task_ids=[
                                    str(item)
                                    for item in raw_patch.get(
                                        "failure_task_ids", []
                                    )
                                ],
                                step=global_step,
                                update_mode=update_mode,
                            )
                    failure_patches, success_patches = _normalise_patches(
                        raw_patches,
                        update_mode=update_mode,
                    )
                    all_failure_patches.extend(failure_patches)
                    all_success_patches.extend(success_patches)
                    all_raw_patches.extend(raw_patches)

                    # Group-Relative extension: the original failure/success
                    # reflection above always completes first.  Then the same
                    # optimizer performs one additional, sequential contrast
                    # analysis for every valid mixed-outcome task group.
                    contrast_patches: list[dict] = []
                    if group_relative_enabled and grouped_rollout is not None:
                        raw_contrast_patches = run_group_contrast_reflect(
                            results=grouped_rollout.all_results,
                            groups=grouped_rollout.groups,
                            skill_content=current_skill,
                            patches_dir=os.path.join(
                                batch_dir,
                                "contrast_patches",
                            ),
                            edit_budget=cfg["edit_budget"],
                            step_buffer_context=step_buffer_context,
                            meta_skill_context=active_meta_skill,
                            update_mode=update_mode,
                            hypothesis_bank_path=(
                                os.path.join(
                                    out_root,
                                    group_memory_dir,
                                    "contrast_hypothesis_bank.json",
                                )
                                if group_hypothesis_bank_enabled
                                else None
                            ),
                            step=global_step,
                            skill_hash_value=skill_hash(current_skill),
                            dual_memory_enabled=(
                                group_dual_memory_enabled
                            ),
                        )
                        contrast_patches = _normalise_contrast_patches(
                            raw_contrast_patches,
                            update_mode=update_mode,
                        )
                        all_contrast_patches.extend(contrast_patches)
                        all_raw_patches.extend(raw_contrast_patches)
                    total_reflect_time += time.time() - t_phase

                    print(
                        f"    [2/6 done] failure_patches={len(failure_patches)} "
                        f"success_patches={len(success_patches)} "
                        f"contrast_patches={len(contrast_patches)}"
                    )

                    # Track per-batch stats
                    accum_rollout_stats.append({
                        "batch_idx": a,
                        "batch_seed": batch_seed,
                        "n_envs": len(rollout_results),
                        "n_ordinary_reflect_trajectories": len(
                            ordinary_reflect_results
                        ),
                        "hard": r_hard,
                        "soft": r_soft,
                        "n_failure_patches": len(failure_patches),
                        "n_success_patches": len(success_patches),
                        "n_contrast_patches": len(contrast_patches),
                        "group_stats": (
                            grouped_rollout.stats
                            if grouped_rollout is not None
                            else None
                        ),
                    })

                # ── End of accumulation loop ─────────────────────────────

                # Aggregate rollout stats across batches
                total_n = sum(b["n_envs"] for b in accum_rollout_stats)
                agg_hard = sum(b["hard"] * b["n_envs"] for b in accum_rollout_stats) / max(total_n, 1)
                agg_soft = sum(b["soft"] * b["n_envs"] for b in accum_rollout_stats) / max(total_n, 1)

                step_rec["rollout_hard"] = round(agg_hard, 6)
                step_rec["rollout_soft"] = round(agg_soft, 6)
                step_rec["rollout_n"] = total_n
                step_rec["accumulation_batches"] = accum_rollout_stats
                step_rec["timing"]["rollout_s"] = round(total_rollout_time, 1)
                step_rec["timing"]["reflect_s"] = round(total_reflect_time, 1)

                n_total_patches = (
                    len(all_failure_patches)
                    + len(all_success_patches)
                    + len(all_contrast_patches)
                )
                step_rec["n_patches"] = n_total_patches
                step_rec["n_failure_patches"] = len(all_failure_patches)
                step_rec["n_success_patches"] = len(all_success_patches)
                step_rec["n_contrast_patches"] = len(all_contrast_patches)

                if accumulation > 1:
                    print(
                        f"    [accum done] total: failure={len(all_failure_patches)} "
                        f"success={len(all_success_patches)} "
                        f"from {accumulation} batches"
                    )

                # ── No patches? Skip ─────────────────────────────────────
                if (
                    not all_failure_patches
                    and not all_success_patches
                    and not all_contrast_patches
                ):
                    step_rec["action"] = "skip_no_patches"
                    step_rec["current_score"] = current_score
                    step_rec["best_score"] = best_score
                    step_rec["best_step"] = best_step
                    step_rec["skill_len"] = len(current_skill)
                    step_rec["wall_time_s"] = round(time.time() - step_t0, 1)
                    history.append(step_rec)
                    _save_history(out_root, history)
                    _save_skill(out_root, global_step, current_skill)
                    _persist_runtime_state(global_step)
                    with open(os.path.join(step_dir, "step_record.json"), "w") as f:
                        json.dump(step_rec, f, indent=2, ensure_ascii=False)
                    print("    [skip] no usable patches — skill unchanged")
                    continue

                # ③ AGGREGATE ──────────────────────────────────────────────
                t_phase = time.time()
                merged_patch = merge_patches(
                    current_skill, all_failure_patches, all_success_patches,
                    batch_size=merge_bs, verbose=True,
                    workers=cfg["analyst_workers"],
                    update_mode=update_mode,
                    meta_skill_context=active_meta_skill,
                    contrast_patches=all_contrast_patches,
                    group_relative=group_relative_enabled,
                    ensure_contrast_candidate=(
                        group_relative_enabled
                        and ensure_contrast_candidate
                    ),
                    preserve_memory_refs=group_dual_memory_enabled,
                )
                if group_dual_memory_enabled:
                    merged_patch = reconcile_memory_metadata(
                        merged_patch,
                        failure_bank=failure_hypothesis_bank,
                        contrast_bank=ContrastMemoryBank(
                            contrast_hypothesis_bank_path
                        ),
                        update_mode=update_mode,
                    )
                with open(os.path.join(step_dir, "merged_patch.json"), "w") as f:
                    json.dump(merged_patch, f, ensure_ascii=False, indent=2)

                merged_items = get_payload_items(merged_patch, update_mode)
                n_edits_merged = len(merged_items)
                step_rec["n_edits_merged"] = n_edits_merged
                step_rec["timing"]["aggregate_s"] = round(time.time() - t_phase, 1)
                print(f"    [3/6 done] merged {n_edits_merged} {payload_label(update_mode)}")

                # ④ SELECT ─────────────────────────────────────────────────
                t_phase = time.time()
                lr_decision = None
                if is_full_rewrite_minibatch_mode(update_mode):
                    edit_budget = None
                    ranked_patch = merged_patch
                    ranked_items = merged_items
                    n_edits_ranked = len(ranked_items)
                    step_rec["n_edits_ranked"] = n_edits_ranked
                    step_rec["edit_budget"] = None
                    step_rec["lr_control_mode"] = "none"
                    with open(os.path.join(step_dir, "ranked_edits.json"), "w") as f:
                        json.dump(ranked_patch, f, ensure_ascii=False, indent=2)
                else:
                    if lr_control_mode == "autonomous":
                        lr_decision = decide_autonomous_learning_rate(
                            skill_content=current_skill,
                            merged_patch=merged_patch,
                            update_mode=update_mode,
                            rollout_hard=agg_hard,
                            rollout_soft=agg_soft,
                            rollout_n=total_n,
                            step_buffer_context=step_buffer_context,
                            meta_skill_context=active_meta_skill,
                        )
                        edit_budget = int(lr_decision["learning_rate"])
                        with open(os.path.join(step_dir, "lr_decision.json"), "w") as f:
                            json.dump(lr_decision, f, ensure_ascii=False, indent=2)
                        with open(os.path.join(out_root, "lr_history.jsonl"), "a") as f:
                            f.write(json.dumps({
                                "step": global_step,
                                "epoch": epoch,
                                **lr_decision,
                            }, ensure_ascii=False) + "\n")
                    else:
                        edit_budget = scheduler.step()
                    ranked_patch = rank_and_select(
                        current_skill, merged_patch,
                        max_edits=edit_budget,
                        update_mode=update_mode,
                        meta_skill_context=active_meta_skill,
                        group_relative=group_relative_enabled,
                        ensure_contrast_candidate=(
                            group_relative_enabled
                            and ensure_contrast_candidate
                        ),
                    )
                    if group_dual_memory_enabled:
                        ranked_patch = reconcile_memory_metadata(
                            ranked_patch,
                            failure_bank=failure_hypothesis_bank,
                            contrast_bank=ContrastMemoryBank(
                                contrast_hypothesis_bank_path
                            ),
                            update_mode=update_mode,
                        )
                    with open(os.path.join(step_dir, "ranked_edits.json"), "w") as f:
                        json.dump(ranked_patch, f, ensure_ascii=False, indent=2)

                    ranked_items = get_payload_items(ranked_patch, update_mode)
                    n_edits_ranked = len(ranked_items)
                    step_rec["n_edits_ranked"] = n_edits_ranked
                    step_rec["edit_budget"] = edit_budget
                    step_rec["lr_control_mode"] = lr_control_mode
                    if lr_decision is not None:
                        step_rec["lr_decision"] = lr_decision
                step_rec["timing"]["select_s"] = round(time.time() - t_phase, 1)
                step_rec["n_contrast_edits_merged"] = sum(
                    isinstance(item, dict)
                    and item.get("source_type") == "contrast"
                    for item in merged_items
                )
                step_rec["n_contrast_edits_ranked"] = sum(
                    isinstance(item, dict)
                    and item.get("source_type") == "contrast"
                    for item in ranked_items
                )
                if isinstance(ranked_patch.get("ranking_details"), dict):
                    step_rec["contrast_candidate_forced"] = bool(
                        ranked_patch["ranking_details"].get(
                            "contrast_candidate_forced", False
                        )
                    )

                support_counts = [
                    item.get("support_count", 0) for item in ranked_items if isinstance(item, dict)
                ]
                step_rec["support_counts"] = support_counts
                if is_full_rewrite_minibatch_mode(update_mode):
                    print(
                        f"    [4/6 SELECT] skipped LR/select; "
                        f"using {n_edits_ranked} merged {payload_label(update_mode)}"
                    )
                else:
                    print(
                        f"    [4/6 SELECT] "
                        f"{n_edits_merged} -> {n_edits_ranked} {payload_label(update_mode)} "
                        f"(budget={edit_budget}, lr_control={lr_control_mode})"
                    )

                # ⑤ UPDATE ─────────────────────────────────────────────────
                t_phase = time.time()
                rewrite_result = None
                if update_mode == "rewrite_from_suggestions":
                    rewrite_result = rewrite_skill_from_suggestions(
                        current_skill,
                        ranked_patch,
                        step_buffer_context=step_buffer_context,
                        env=cfg.get("env"),
                        reasoning_effort=rewrite_reasoning_effort,
                        max_completion_tokens=rewrite_max_completion_tokens,
                    )
                    if rewrite_result and rewrite_result.get("new_skill"):
                        candidate_skill = rewrite_result["new_skill"]
                        apply_report = []
                        with open(os.path.join(step_dir, "rewrite_result.json"), "w") as f:
                            json.dump(rewrite_result, f, ensure_ascii=False, indent=2)
                    else:
                        candidate_skill = current_skill
                        apply_report = []
                elif is_full_rewrite_minibatch_mode(update_mode):
                    skill_candidates = get_payload_items(ranked_patch, update_mode)
                    selected_candidate = next(
                        (
                            item for item in skill_candidates
                            if isinstance(item, dict) and str(item.get("new_skill", "")).strip()
                        ),
                        None,
                    )
                    if selected_candidate:
                        candidate_skill = str(selected_candidate["new_skill"]).rstrip() + "\n"
                        apply_report = []
                        rewrite_result = {
                            "reasoning": ranked_patch.get("reasoning", ""),
                            "change_summary": selected_candidate.get("change_summary", []),
                            "title": selected_candidate.get("title", ""),
                            "source_type": selected_candidate.get("source_type", ""),
                        }
                        with open(os.path.join(step_dir, "full_rewrite_result.json"), "w") as f:
                            json.dump(
                                {
                                    "selected_candidate": selected_candidate,
                                    "merged_patch": ranked_patch,
                                },
                                f,
                                ensure_ascii=False,
                                indent=2,
                            )
                    else:
                        candidate_skill = current_skill
                        apply_report = []
                else:
                    candidate_skill, apply_report = apply_patch_with_report(current_skill, ranked_patch)
                    if repair_slow_target_edits_enabled:
                        repaired_patch, repair_details = normalize_protected_edits(
                            candidate_skill,
                            ranked_patch,
                            apply_report,
                        )
                        if repair_details.get("attempted"):
                            repaired_skill, repaired_report = apply_patch_with_report(
                                candidate_skill,
                                repaired_patch,
                            )
                            for row in repaired_report:
                                row["repair_attempt"] = True
                            candidate_skill = repaired_skill
                            apply_report.extend(repaired_report)
                            repair_details["apply_report"] = repaired_report
                            with open(
                                os.path.join(step_dir, "slow_target_repair.json"),
                                "w",
                            ) as f:
                                json.dump(
                                    {
                                        "repair_details": repair_details,
                                        "repaired_patch": repaired_patch,
                                    },
                                    f,
                                    indent=2,
                                    ensure_ascii=False,
                                )
                with open(os.path.join(step_dir, "candidate_skill.md"), "w") as f:
                    f.write(candidate_skill)
                if apply_report:
                    with open(os.path.join(step_dir, "edit_apply_report.json"), "w") as f:
                        json.dump(apply_report, f, indent=2, ensure_ascii=False)

                cand_hash = skill_hash(candidate_skill)
                step_rec["candidate_hash"] = cand_hash
                step_rec["candidate_skill_len"] = len(candidate_skill)
                if rewrite_result:
                    step_rec["rewrite_change_summary"] = rewrite_result.get("change_summary", [])
                if apply_report:
                    step_rec["edit_apply_summary"] = {
                        "total": len(apply_report),
                        "applied": sum(
                            1 for row in apply_report if str(row.get("status", "")).startswith("applied")
                        ),
                        "skipped": sum(
                            1 for row in apply_report if str(row.get("status", "")).startswith("skipped")
                        ),
                        "errors": sum(
                            1 for row in apply_report if row.get("status") == "error"
                        ),
                    }
                step_rec["timing"]["update_s"] = round(time.time() - t_phase, 1)
                if (
                    update_mode == "rewrite_from_suggestions"
                    and rewrite_result is None
                ) or (
                    is_full_rewrite_minibatch_mode(update_mode)
                    and rewrite_result is None
                ):
                    step_rec["action"] = "skip_no_rewrite"
                    step_rec["current_score"] = current_score
                    step_rec["best_score"] = best_score
                    step_rec["best_step"] = best_step
                    step_rec["skill_len"] = len(current_skill)
                    step_rec["wall_time_s"] = round(time.time() - step_t0, 1)
                    history.append(step_rec)
                    _save_history(out_root, history)
                    _save_skill(out_root, global_step, current_skill)
                    _persist_runtime_state(global_step)
                    with open(os.path.join(step_dir, "step_record.json"), "w") as f:
                        json.dump(step_rec, f, indent=2, ensure_ascii=False)
                    print("    [skip] no usable rewrite generated — skill unchanged")
                    continue
                print(
                    f"    [5/6 UPDATE] "
                    f"skill_len {len(current_skill)} -> {len(candidate_skill)}"
                )

                # ⑥ EVALUATE ───────────────────────────────────────────────
                t_phase = time.time()
                if cand_hash in sel_cache:
                    cand_hard, cand_soft = sel_cache[cand_hash]
                    print(
                        f"    [6/6 EVALUATE] "
                        f"cache hit {cand_hash}: hard={cand_hard:.4f}"
                    )
                else:
                    sel_eval_dir = os.path.join(step_dir, "selection_eval")
                    if selection_eval_repeats > 1:
                        cand_hard, cand_soft, selection_summary = (
                            evaluate_selection_repeats(
                                adapter=adapter,
                                build_env=lambda: _build_eval_env(
                                    split="valid_seen",
                                    env_num=cfg["sel_env_num"],
                                    seed=seed,
                                ),
                                skill_content=candidate_skill,
                                out_dir=sel_eval_dir,
                                repeats=selection_eval_repeats,
                            )
                        )
                        print(
                            f"    [6/6 EVALUATE] selection "
                            f"items="
                            f"{selection_summary['expected_items_per_repeat']} "
                            f"repeats={selection_eval_repeats} "
                            f"rows={selection_summary['total_result_rows']}"
                        )
                    else:
                        sel_env, sel_n = _build_eval_env(
                            split="valid_seen",
                            env_num=cfg["sel_env_num"],
                            seed=seed,
                        )
                        print(
                            f"    [6/6 EVALUATE] selection items={sel_n}"
                        )
                        sel_results = adapter.rollout(
                            sel_env, candidate_skill, sel_eval_dir,
                        )
                        cand_hard, cand_soft = compute_score(sel_results)
                    sel_cache[cand_hash] = (cand_hard, cand_soft)

                step_rec["selection_hard"] = cand_hard
                step_rec["selection_soft"] = cand_soft
                step_rec["selection_eval_repeats"] = selection_eval_repeats

                gate = evaluate_gate(
                    candidate_skill=candidate_skill,
                    cand_hard=cand_hard,
                    current_skill=current_skill,
                    current_score=current_score,
                    best_skill=best_skill,
                    best_score=best_score,
                    best_step=best_step,
                    global_step=global_step,
                    cand_soft=cand_soft,
                    metric=gate_metric,
                    mixed_weight=gate_mixed_weight,
                    accept_equal=gate_accept_equal,
                )
                cand_gate_score = select_gate_score(
                    cand_hard, cand_soft, gate_metric, gate_mixed_weight,
                )
                step_rec["gate_metric"] = gate_metric
                step_rec["candidate_gate_score"] = cand_gate_score
                step_rec["action"] = gate.action
                prev_current = current_score
                prev_best = best_score
                current_skill = gate.current_skill
                current_score = gate.current_score
                best_skill = gate.best_skill
                best_score = gate.best_score
                best_step = gate.best_step
                if gate.action in {
                    "accept", "accept_flat", "accept_new_best",
                }:
                    current_origin = f"step_{global_step:04d}"
                if gate.action == "accept_new_best":
                    best_origin = current_origin

                if gate_metric == "hard":
                    score_label = f"hard={cand_hard:.4f}"
                elif gate_metric == "soft":
                    score_label = f"soft={cand_soft:.4f}"
                else:
                    score_label = (
                        f"mixed[w={gate_mixed_weight}]={cand_gate_score:.4f} "
                        f"(hard={cand_hard:.4f} soft={cand_soft:.4f})"
                    )
                if gate.action == "accept_new_best":
                    print(
                        f"    [6/6 EVALUATE] ACCEPT (new best) "
                        f"{score_label} > prev best {prev_best:.4f}"
                    )
                elif gate.action == "accept":
                    print(
                        f"    [6/6 EVALUATE] ACCEPT "
                        f"{score_label} > current={prev_current:.4f}"
                    )
                elif gate.action == "accept_flat":
                    print(
                        f"    [6/6 EVALUATE] ACCEPT_FLAT "
                        f"{score_label} == current={prev_current:.4f}; "
                        "updated current only, best unchanged"
                    )
                else:
                    print(
                        f"    [6/6 EVALUATE] REJECT "
                        f"{score_label} <= current={current_score:.4f}"
                    )

                step_rec["timing"]["evaluate_s"] = round(time.time() - t_phase, 1)

                # ── Step buffer: unified failure patterns + rejected edits ─
                action = step_rec.get("action", "unknown")
                n_total = len(all_rollout_results) or 1
                n_fail = sum(1 for r in all_rollout_results if not r.get("hard") or float(r.get("hard", 0)) < 1e-9)
                failure_patterns = _extract_failure_patterns(
                    all_rollout_results, step_dir,
                )

                buf_entry: dict = {
                    "step": global_step,
                    "action": action,
                    "n_total": n_total,
                    "n_fail": n_fail,
                    "failure_patterns": failure_patterns,
                }

                # Attach rejected edits when the step was rejected
                if "reject" in action and ranked_patch:
                    rejected_edits = [
                        short_item_summary(item, update_mode)
                        for item in ranked_items
                        if isinstance(item, dict)
                    ]
                    buf_entry["score_before"] = current_score
                    buf_entry["score_after"] = cand_gate_score
                    buf_entry["rejected_edits"] = rejected_edits

                step_buffer.append(buf_entry)

                # Persist step digest for step buffer context
                digest_path = os.path.join(step_dir, "trajectory_digest.json")
                with open(digest_path, "w") as f:
                    json.dump(buf_entry, f, indent=2, ensure_ascii=False)

                # ── Token snapshot ───────────────────────────────────────
                tokens_after = get_token_summary()
                step_tokens: dict = {}
                for stage in tokens_after:
                    if stage == "_total":
                        continue
                    after = tokens_after[stage]
                    before = tokens_before.get(stage, {})
                    step_tokens[stage] = {
                        "calls": after.get("calls", 0) - before.get("calls", 0),
                        "prompt_tokens": after.get("prompt_tokens", 0)
                        - before.get("prompt_tokens", 0),
                        "completion_tokens": after.get("completion_tokens", 0)
                        - before.get("completion_tokens", 0),
                    }
                step_rec["tokens"] = step_tokens

                # ── Save state ───────────────────────────────────────────
                step_rec["current_score"] = current_score
                step_rec["best_score"] = best_score
                step_rec["best_step"] = best_step
                step_rec["current_origin"] = current_origin
                step_rec["best_origin"] = best_origin
                step_rec["skill_len"] = len(current_skill)
                step_rec["wall_time_s"] = round(time.time() - step_t0, 1)

                _save_skill(out_root, global_step, current_skill)
                with open(os.path.join(out_root, "best_skill.md"), "w") as f:
                    f.write(best_skill)
                history.append(step_rec)
                _save_history(out_root, history)
                _persist_runtime_state(global_step)
                with open(os.path.join(step_dir, "step_record.json"), "w") as f:
                    json.dump(step_rec, f, indent=2, ensure_ascii=False)

                timing = step_rec["timing"]
                print(
                    f"\n  [STEP {global_step} done] "
                    f"epoch={epoch} action={step_rec['action']} "
                    f"current={current_score:.4f} best={best_score:.4f} "
                    f"dt={step_rec['wall_time_s']}s\n"
                    f"    timing: rollout={timing.get('rollout_s',0)}s "
                    f"reflect={timing.get('reflect_s',0)}s "
                    f"aggregate={timing.get('aggregate_s',0)}s "
                    f"select={timing.get('select_s',0)}s "
                    f"evaluate={timing.get('evaluate_s',0)}s"
                )

            epoch_last_step_skill = current_skill
            epoch_comparison_pairs: list[dict] | None = None

            # ── SLOW UPDATE (end of epoch) ──────────────────────────────
            use_slow = cfg.get("use_slow_update", False)
            slow_variant_action = "disabled"
            if use_slow and record_slow_update_variants:
                record_slow_update_variant(
                    out_root=out_root,
                    epoch=epoch,
                    phase="before",
                    current_skill=current_skill,
                    best_skill=best_skill,
                    action="pending",
                )
            if use_slow:
                slow_dir = os.path.join(out_root, "slow_update", f"epoch_{epoch:02d}")
                slow_done_path = os.path.join(slow_dir, "slow_result.json")

                if os.path.exists(slow_done_path):
                    # Resume support
                    print(
                        f"\n  [SLOW UPDATE epoch {epoch}] "
                        f"resumed — already done"
                    )
                    with open(slow_done_path) as f:
                        slow_saved = json.load(f)
                    slow_variant_action = str(
                        slow_saved.get("action", "resumed")
                    )
                    comparison_path = os.path.join(slow_dir, "comparison_pairs.json")
                    if os.path.exists(comparison_path):
                        try:
                            with open(comparison_path) as f:
                                epoch_comparison_pairs = json.load(f)
                        except Exception:
                            epoch_comparison_pairs = None
                    if (
                        slow_saved.get("slow_update_content")
                        and slow_saved.get("action") in {
                            "accept", "accept_new_best", "force_accept",
                        }
                        and epoch >= 2
                    ):
                        current_skill = replace_slow_update_field(
                            current_skill, slow_saved["slow_update_content"],
                        )
                        best_skill = replace_slow_update_field(
                            best_skill, slow_saved["slow_update_content"],
                        )
                elif epoch == 1:
                    # Epoch 1: inject empty placeholder
                    os.makedirs(slow_dir, exist_ok=True)
                    current_skill = inject_empty_slow_update_field(current_skill)
                    current_origin = f"slow_update_placeholder_epoch_{epoch:02d}"
                    _save_skill(out_root, global_step, current_skill)
                    with open(os.path.join(out_root, "best_skill.md"), "w") as f:
                        f.write(best_skill if best_score > current_score else current_skill)
                    with open(slow_done_path, "w") as f:
                        json.dump({"action": "inject_placeholder", "epoch": epoch}, f, indent=2)
                    slow_variant_action = "inject_placeholder"
                    _persist_runtime_state(global_step)
                    print(
                        f"\n  [SLOW UPDATE epoch {epoch}] "
                        f"injected empty placeholder"
                    )
                else:
                    # Epoch 2+: longitudinal comparison
                    os.makedirs(slow_dir, exist_ok=True)
                    print(
                        f"\n  {'='*60}\n"
                        f"  SLOW UPDATE — Epoch {epoch} "
                        f"(comparing epoch {epoch-1} vs {epoch})\n"
                        f"  {'='*60}"
                    )

                    # 1. Get skill from last step of previous epoch
                    prev_epoch_records = [
                        h for h in history if h.get("epoch") == epoch - 1
                    ]
                    prev_epoch_last_step = prev_epoch_records[-1]["step"]
                    prev_skill = _load_skill(out_root, prev_epoch_last_step)

                    # 2. Sample items from train set
                    slow_n = cfg.get("slow_update_samples", 20)
                    slow_seed = seed + epoch * 2000
                    if dataloader is not None:
                        slow_batch = dataloader.build_train_batch(
                            batch_size=slow_n,
                            seed=slow_seed,
                            out_root=out_root,
                        )
                        slow_env = adapter.build_env_from_batch(
                            slow_batch, out_root=out_root,
                        )
                    else:
                        slow_env = adapter.build_train_env(
                            batch_size=slow_n,
                            seed=slow_seed,
                            out_root=out_root,
                        )
                    slow_items = list(slow_env) if hasattr(slow_env, "__iter__") else slow_env
                    print(f"    [slow update] sampled {len(slow_items)} train items (seed={slow_seed})")

                    # 3. Rollout with both skills
                    t_slow = time.time()
                    prev_rollout_dir = os.path.join(slow_dir, "rollout_prev")
                    curr_rollout_dir = os.path.join(slow_dir, "rollout_curr")
                    results_prev = adapter.rollout(slow_env, prev_skill, prev_rollout_dir)
                    results_curr = adapter.rollout(slow_env, current_skill, curr_rollout_dir)

                    prev_hard, _ = compute_score(results_prev)
                    curr_hard, _ = compute_score(results_curr)
                    print(
                        f"    [slow update] prev epoch hard={prev_hard:.4f}  "
                        f"curr epoch hard={curr_hard:.4f}"
                    )

                    # 4. Build and save structured comparison pairs
                    comparison_pairs, all_comparison_pairs = _build_longitudinal_pairs(
                        adapter=adapter,
                        dataloader=dataloader,
                        prev_skill=prev_skill,
                        curr_skill=current_skill,
                        initial_items=slow_items,
                        initial_prev_results=results_prev,
                        initial_curr_results=results_curr,
                        prev_rollout_dir=prev_rollout_dir,
                        curr_rollout_dir=curr_rollout_dir,
                        policy=longitudinal_pair_policy,
                        target_n=slow_n,
                        seed=slow_seed,
                        out_root=out_root,
                    )
                    epoch_comparison_pairs = comparison_pairs
                    if all_comparison_pairs is not comparison_pairs:
                        save_comparison_pairs(
                            all_comparison_pairs,
                            os.path.join(slow_dir, "comparison_pairs_all.json"),
                        )
                    save_comparison_pairs(
                        comparison_pairs,
                        os.path.join(slow_dir, "comparison_pairs.json"),
                    )
                    n_regressed = sum(1 for p in comparison_pairs if p["category"] == "regressed")
                    n_improved = sum(1 for p in comparison_pairs if p["category"] == "improved")
                    n_persist = sum(1 for p in comparison_pairs if p["category"] == "persistent_fail")
                    n_stable = sum(1 for p in comparison_pairs if p["category"] == "stable_success")
                    print(
                        f"    [slow update] comparison: "
                        f"regressed={n_regressed} improved={n_improved} "
                        f"persistent_fail={n_persist} stable_success={n_stable} "
                        f"policy={longitudinal_pair_policy} "
                        f"kept={len(comparison_pairs)}/{len(all_comparison_pairs)}"
                    )

                    # 5. Extract previous slow update guidance for reflection.
                    existing_guidance = extract_slow_update_field(current_skill)

                    # 6. Optimizer analysis (with reflection on previous guidance)
                    slow_result = run_slow_update(
                        current_skill,
                        results_prev,
                        results_curr,
                        slow_items,
                        prev_skill=prev_skill,
                        prev_slow_update_content=existing_guidance,
                        prev_rollout_dir=prev_rollout_dir,
                        curr_rollout_dir=curr_rollout_dir,
                        comparison_pairs=comparison_pairs,
                        max_slow_chars=(
                            min(
                                len(strip_slow_update_field(current_skill)),
                                len(strip_slow_update_field(best_skill)),
                            )
                            if bounded_slow_update
                            else None
                        ),
                    )
                    slow_time = round(time.time() - t_slow, 1)

                    if slow_result and slow_result.get("slow_update_content"):
                        slow_candidate = replace_slow_update_field(
                            current_skill, slow_result["slow_update_content"],
                        )
                        slow_candidate_hash = skill_hash(slow_candidate)
                        with open(os.path.join(slow_dir, "candidate_skill.md"), "w") as f:
                            f.write(slow_candidate)
                        slow_result["time_s"] = slow_time
                        slow_result["prev_hard"] = prev_hard
                        slow_result["curr_hard"] = curr_hard
                        slow_result["candidate_hash"] = slow_candidate_hash
                        slow_result["update_origin"] = "slow_update_momentum"
                        slow_result["update_target"] = (
                            "Address longitudinal regressions and persistent failures "
                            "observed across adjacent epochs."
                        )

                        # Slow update field is force-updated into both
                        # current_skill and best_skill unconditionally.
                        # The epoch-level longitudinal guidance should always
                        # persist — it must not be gated by step-level
                        # selection scores.
                        slow_content = slow_result["slow_update_content"]
                        current_skill = replace_slow_update_field(
                            current_skill, slow_content,
                        )
                        best_skill = replace_slow_update_field(
                            best_skill, slow_content,
                        )
                        # Update caches so downstream steps use the
                        # slow-update-injected skill for hashing.
                        slow_candidate_hash = skill_hash(current_skill)
                        sel_cache[slow_candidate_hash] = (current_score, 0.0)

                        slow_result["action"] = "force_accept"
                        slow_variant_action = "force_accept"
                        current_origin = f"slow_update_epoch_{epoch:02d}"

                        print(
                            f"    [slow update] force-injected into current & best "
                            f"({len(slow_content)} chars), "
                            f"{slow_time}s"
                        )
                    else:
                        slow_result = slow_result or {}
                        slow_result["action"] = "no_content"
                        slow_variant_action = "no_content"
                        slow_result["time_s"] = slow_time
                        print(
                            f"    [slow update] no guidance produced, "
                            f"{slow_time}s"
                        )

                    # 5. Save
                    with open(slow_done_path, "w") as f:
                        json.dump(slow_result, f, indent=2, ensure_ascii=False)
                    _save_skill(out_root, global_step, current_skill)
                    with open(os.path.join(out_root, "best_skill.md"), "w") as f:
                        f.write(best_skill)
                    _persist_runtime_state(global_step)

                    print(
                        f"\n  [SLOW UPDATE epoch {epoch} done] "
                        f"current={current_score:.4f} best={best_score:.4f}"
                    )

            if use_slow and record_slow_update_variants:
                record_slow_update_variant(
                    out_root=out_root,
                    epoch=epoch,
                    phase="after",
                    current_skill=current_skill,
                    best_skill=best_skill,
                    action=slow_variant_action,
                )

            # ── META SKILL (end of epoch, optimizer-side memory) ─────────
            use_meta_skill = cfg.get("use_meta_skill", False)
            if use_meta_skill:
                meta_skill_dir = os.path.join(out_root, "meta_skill", f"epoch_{epoch:02d}")
                meta_skill_done_path = os.path.join(meta_skill_dir, "meta_skill_result.json")
                os.makedirs(meta_skill_dir, exist_ok=True)

                if os.path.exists(meta_skill_done_path):
                    print(f"\n  [META SKILL epoch {epoch}] resumed — already done")
                elif epoch == 1:
                    with open(meta_skill_done_path, "w") as f:
                        json.dump(
                            {"action": "skip_first_epoch", "epoch": epoch},
                            f, indent=2, ensure_ascii=False,
                        )
                    print(f"\n  [META SKILL epoch {epoch}] skipped — first epoch")
                else:
                    print(
                        f"\n  {'='*60}\n"
                        f"  META SKILL — Epoch {epoch} "
                        f"(optimizer memory from epoch {epoch-1} vs {epoch})\n"
                        f"  {'='*60}"
                    )

                    prev_epoch_records = [h for h in history if h.get("epoch") == epoch - 1]
                    prev_epoch_last_step = prev_epoch_records[-1]["step"]
                    prev_skill = _load_skill(out_root, prev_epoch_last_step)
                    prev_meta_skill = _load_meta_skill_content(out_root, epoch - 1)

                    if epoch_comparison_pairs is None:
                        meta_n = cfg.get("slow_update_samples", 20)
                        meta_seed = seed + epoch * 2000
                        if dataloader is not None:
                            meta_batch = dataloader.build_train_batch(
                                batch_size=meta_n,
                                seed=meta_seed,
                                out_root=out_root,
                            )
                            meta_env = adapter.build_env_from_batch(
                                meta_batch, out_root=out_root,
                            )
                        else:
                            meta_env = adapter.build_train_env(
                                batch_size=meta_n,
                                seed=meta_seed,
                                out_root=out_root,
                            )
                        meta_items = list(meta_env) if hasattr(meta_env, "__iter__") else meta_env
                        prev_rollout_dir = os.path.join(meta_skill_dir, "rollout_prev")
                        curr_rollout_dir = os.path.join(meta_skill_dir, "rollout_curr")
                        results_prev = adapter.rollout(meta_env, prev_skill, prev_rollout_dir)
                        results_curr = adapter.rollout(meta_env, epoch_last_step_skill, curr_rollout_dir)
                        epoch_comparison_pairs, all_meta_comparison_pairs = _build_longitudinal_pairs(
                            adapter=adapter,
                            dataloader=dataloader,
                            prev_skill=prev_skill,
                            curr_skill=epoch_last_step_skill,
                            initial_items=meta_items,
                            initial_prev_results=results_prev,
                            initial_curr_results=results_curr,
                            prev_rollout_dir=prev_rollout_dir,
                            curr_rollout_dir=curr_rollout_dir,
                            policy=longitudinal_pair_policy,
                            target_n=meta_n,
                            seed=meta_seed,
                            out_root=out_root,
                        )
                        if all_meta_comparison_pairs is not epoch_comparison_pairs:
                            save_comparison_pairs(
                                all_meta_comparison_pairs,
                                os.path.join(meta_skill_dir, "comparison_pairs_all.json"),
                            )
                        save_comparison_pairs(
                            epoch_comparison_pairs,
                            os.path.join(meta_skill_dir, "comparison_pairs.json"),
                        )
                        meta_counts = _pair_category_counts(epoch_comparison_pairs)
                        print(
                            f"    [meta skill] comparison: "
                            f"regressed={meta_counts.get('regressed', 0)} "
                            f"improved={meta_counts.get('improved', 0)} "
                            f"persistent_fail={meta_counts.get('persistent_fail', 0)} "
                            f"stable_success={meta_counts.get('stable_success', 0)} "
                            f"policy={longitudinal_pair_policy} "
                            f"kept={len(epoch_comparison_pairs)}/{len(all_meta_comparison_pairs)}"
                        )

                    t_meta_skill = time.time()
                    meta_skill_result = run_meta_skill(
                        prev_skill=prev_skill,
                        curr_skill=epoch_last_step_skill,
                        comparison_pairs=epoch_comparison_pairs or [],
                        prev_meta_skill_content=prev_meta_skill,
                    )
                    meta_skill_time = round(time.time() - t_meta_skill, 1)

                    if meta_skill_result and meta_skill_result.get("meta_skill_content"):
                        meta_skill_result["time_s"] = meta_skill_time
                        meta_skill_result["action"] = "write_meta_skill"
                        print(
                            f"    [meta skill] memory written "
                            f"({len(meta_skill_result['meta_skill_content'])} chars), "
                            f"{meta_skill_time}s"
                        )
                    else:
                        meta_skill_result = meta_skill_result or {}
                        meta_skill_result["time_s"] = meta_skill_time
                        meta_skill_result["action"] = "no_content"
                        print(f"    [meta skill] no memory produced, {meta_skill_time}s")

                    with open(meta_skill_done_path, "w") as f:
                        json.dump(meta_skill_result, f, indent=2, ensure_ascii=False)

        # ── Save best skill ──────────────────────────────────────────────
        with open(os.path.join(out_root, "best_skill.md"), "w") as f:
            f.write(best_skill)
        _persist_runtime_state(global_step)
        print(
            f"\n  [done] best skill from step {best_step}, "
            f"score={best_score:.4f}"
        )

        # ── Final test evaluation (valid_unseen) ─────────────────────────
        baseline_test_hard = None
        baseline_test_soft = None
        test_hard = None
        test_soft = None

        if cfg["eval_test"]:
            task_types = adapter.get_task_types()

            # Baseline: S_0 on test set (valid_unseen)
            print(f"\n{'='*60}")
            print("  BASELINE TEST — evaluate initial skill on Test set (valid_unseen)")
            print(f"{'='*60}")
            test_env, test_n = _build_eval_env(
                split="valid_unseen",
                env_num=cfg["test_env_num"],
                seed=seed,
            )
            print(f"  Test items: {test_n}")
            baseline_test_dir = os.path.join(out_root, "test_eval_baseline")
            baseline_test_results = adapter.rollout(test_env, skill_init, baseline_test_dir)
            baseline_test_hard, baseline_test_soft = compute_score(baseline_test_results)
            baseline_buckets = _compute_task_type_buckets(baseline_test_results, task_types)
            print("\n  === Baseline Test Results (S_0) ===")
            for task_type in task_types + ["overall"]:
                b = baseline_buckets.get(task_type, {"total": 0, "hard": 0})
                t = max(b["total"], 1)
                print(
                    f"    {task_type:<40s}: "
                    f"hard={b['hard']}/{b['total']}={b['hard']/t:.4f}"
                )
            with open(os.path.join(baseline_test_dir, "summary.json"), "w") as f:
                json.dump(
                    {
                        k: {
                            "total": b["total"],
                            "hard_acc": b["hard"] / max(b["total"], 1),
                        }
                        for k, b in baseline_buckets.items()
                    },
                    f, indent=2, ensure_ascii=False,
                )

            # Best skill on test set
            print(f"\n{'='*60}")
            print("  BEST SKILL TEST — evaluate best skill on Test set (valid_unseen)")
            print(f"{'='*60}")
            final_test_env, final_test_n = _build_eval_env(
                split="valid_unseen",
                env_num=cfg["test_env_num"],
                seed=seed,
            )
            print(f"  Test items: {final_test_n}")
            test_dir = os.path.join(out_root, "test_eval")
            test_results = adapter.rollout(final_test_env, best_skill, test_dir)
            test_hard, test_soft = compute_score(test_results)
            best_buckets = _compute_task_type_buckets(test_results, task_types)
            print("\n  === Best Skill Test Results ===")
            for task_type in task_types + ["overall"]:
                b = best_buckets.get(task_type, {"total": 0, "hard": 0})
                t = max(b["total"], 1)
                print(
                    f"    {task_type:<40s}: "
                    f"hard={b['hard']}/{b['total']}={b['hard']/t:.4f}"
                )
            with open(os.path.join(test_dir, "summary.json"), "w") as f:
                json.dump(
                    {
                        k: {
                            "total": b["total"],
                            "hard_acc": b["hard"] / max(b["total"], 1),
                        }
                        for k, b in best_buckets.items()
                    },
                    f, indent=2, ensure_ascii=False,
                )

            # Comparison
            delta_hard = (test_hard or 0) - (baseline_test_hard or 0)
            print(f"\n  === Improvement (best vs baseline) ===")
            print(
                f"    hard: {baseline_test_hard:.4f} -> {test_hard:.4f}  "
                f"(delta={delta_hard:+.4f})"
            )

        # ── Global summary ───────────────────────────────────────────────
        total_wall = time.time() - t_loop_start
        n_accept = sum(
            1 for h in history
            if h.get("action") in {"accept", "accept_new_best"}
        )
        n_flat_accept = sum(
            1 for h in history if h.get("action") == "accept_flat"
        )
        n_reject = sum(1 for h in history if h.get("action") == "reject")
        n_skip = sum(1 for h in history if h.get("action") == "skip_no_patches")

        token_summary = get_token_summary()

        # Epoch-level statistics
        epoch_stats = []
        for e in range(1, num_epochs + 1):
            epoch_records = [h for h in history if h.get("epoch") == e]
            if epoch_records:
                epoch_stats.append({
                    "epoch": e,
                    "steps": [h["step"] for h in epoch_records],
                    "accepts": sum(
                        1 for h in epoch_records
                        if h.get("action") in {"accept", "accept_new_best"}
                    ),
                    "flat_accepts": sum(
                        1 for h in epoch_records
                        if h.get("action") == "accept_flat"
                    ),
                    "rejects": sum(1 for h in epoch_records if h.get("action") == "reject"),
                    "skips": sum(1 for h in epoch_records if h.get("action") == "skip_no_patches"),
                    "best_score_at_epoch_end": epoch_records[-1].get("best_score", 0.0),
                    "current_score_at_epoch_end": epoch_records[-1].get("current_score", 0.0),
                })

        summary = {
            "version": "skillopt-0.1.0",
            "config": _redact_cfg(cfg),
            "baseline_selection_hard": sel_cache.get(
                skill_hash(skill_init), (None, None),
            )[0],
            "best_selection_hard": best_score,
            "best_step": best_step,
            "current_origin": current_origin,
            "best_origin": best_origin,
            "total_steps": len(history),
            "total_accepts": n_accept,
            "total_flat_accepts": n_flat_accept,
            "total_rejects": n_reject,
            "total_skips": n_skip,
            "epoch_stats": epoch_stats,
            "baseline_test_hard": baseline_test_hard,
            "baseline_test_soft": baseline_test_soft,
            "test_hard": test_hard,
            "test_soft": test_soft,
            "test_delta_hard": (
                (test_hard or 0) - (baseline_test_hard or 0)
                if test_hard is not None
                else None
            ),
            "total_wall_time_s": round(total_wall, 1),
            "token_summary": token_summary,
        }
        with open(os.path.join(out_root, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*60}")
        print("  Final Summary")
        print(f"{'='*60}")
        print(
            f"  steps={len(history)} accept={n_accept} "
            f"flat_accept={n_flat_accept} "
            f"reject={n_reject} skip={n_skip}"
        )
        print(f"  best_score={best_score:.4f} (step {best_step})  wall={total_wall:.0f}s")
        if epoch_stats:
            for es in epoch_stats:
                print(
                    f"    epoch {es['epoch']}: accept={es['accepts']} "
                    f"flat_accept={es['flat_accepts']} "
                    f"reject={es['rejects']} "
                    f"best={es['best_score_at_epoch_end']:.4f}"
                )
        if test_hard is not None:
            print(f"  test_hard={test_hard:.4f} test_soft={test_soft:.4f}")
        if token_summary.get("_total"):
            t = token_summary["_total"]
            print(
                f"  total tokens: {t['total_tokens']:,} "
                f"(prompt={t['prompt_tokens']:,} "
                f"completion={t['completion_tokens']:,} "
                f"calls={t['calls']})"
            )

        return summary
