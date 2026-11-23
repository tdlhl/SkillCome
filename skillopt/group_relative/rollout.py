"""Repeated, same-task rollout orchestration for Group-Relative SkillOpt."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import is_dataclass, replace
import hashlib
import json
import os
import random
from typing import Any


_INFRA_ERROR_PATTERNS = (
    "rate limit",
    "ratelimit",
    "too many requests",
    "task-timeout",
    "timed out",
    "timeout",
    "connection error",
    "connection reset",
    "connection aborted",
    "ssl",
    "handshake",
    "empty response",
    "empty_response",
    "api error",
    "api_error",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
)


def classify_rollout_validity(
    row: dict,
    *,
    agent_failures_are_valid_evidence: bool = False,
) -> tuple[bool, str]:
    """Return whether a rollout is valid evidence for skill learning.

    Benchmark failures remain valid. API, process, and timeout failures are
    retained on disk but excluded from learning. By default, ``agent_ok=false``
    keeps the legacy invalid behavior; an isolated experiment may opt in to
    treating non-infrastructure agent failures as valid failure evidence.
    """
    phase = str(row.get("phase") or "").strip().lower()
    if phase in {"timeout", "api_error", "infrastructure_error"}:
        return False, str(row.get("fail_reason") or phase)

    combined = " ".join(
        str(row.get(key) or "")
        for key in ("fail_reason", "error", "exception")
    ).strip().lower()
    if any(pattern in combined for pattern in _INFRA_ERROR_PATTERNS):
        return False, combined[:500] or "infrastructure failure"

    if row.get("llm_ok") is False:
        return False, str(row.get("fail_reason") or "llm_ok=false")
    if (
        row.get("agent_ok") is False
        and not agent_failures_are_valid_evidence
    ):
        return False, str(row.get("fail_reason") or "agent_ok=false")

    # Several rollout implementations use phase=error for evaluator/code
    # errors as well as API errors.  Only exclude it when no benchmark case
    # was actually evaluated.
    if phase == "error":
        n_cases = int(row.get("n_cases", 0) or 0)
        if n_cases == 0:
            return False, combined[:500] or "phase=error with no evaluated cases"

    return True, ""


@dataclass
class GroupedRollout:
    """Materialized repeated rollouts and their group-level statistics."""

    valid_results: list[dict]
    all_results: list[dict]
    groups: list[dict]
    stats: dict


def select_ordinary_reflect_results(
    *,
    valid_results: list[dict],
    groups: list[dict],
    seed: int,
) -> tuple[list[dict], list[dict]]:
    """Select one reproducible representative per valid task group.

    Repeated trajectories remain available to the same-task contrast branch.
    The ordinary failure/success reflection receives exactly one trajectory
    from every group that has valid evidence.  Mixed groups deliberately
    contribute a failed trajectory so ordinary reflection still covers the
    full unique-item batch without counting repeated attempts as independent
    support.

    Selection is pseudo-random but stable per ``(seed, group_id, group_type)``;
    adding or removing another group therefore does not perturb existing
    representatives, and resumed runs produce the same inputs.
    """
    rows_by_group: dict[str, list[dict]] = {}
    for row in valid_results:
        rows_by_group.setdefault(str(row.get("group_id")), []).append(row)

    selected: list[dict] = []
    manifest: list[dict] = []
    for group in groups:
        group_id = str(group.get("group_id"))
        group_type = str(group.get("group_type") or "invalid")
        valid_rows = rows_by_group.get(group_id, [])
        if not valid_rows:
            manifest.append(
                {
                    "group_id": group_id,
                    "group_type": group_type,
                    "selected_rollout_id": None,
                    "selected_outcome": None,
                    "selection_policy": "skip_no_valid_trajectory",
                }
            )
            continue

        if group_type == "mixed":
            candidates = [
                row
                for row in valid_rows
                if float(row.get("hard", 0) or 0) <= 0
            ]
            policy = "mixed_random_failure"
        elif group_type == "all_failure":
            candidates = [
                row
                for row in valid_rows
                if float(row.get("hard", 0) or 0) <= 0
            ]
            policy = "all_failure_random_representative"
        elif group_type == "all_success":
            candidates = [
                row
                for row in valid_rows
                if float(row.get("hard", 0) or 0) > 0
            ]
            policy = "all_success_random_representative"
        else:
            candidates = list(valid_rows)
            policy = "fallback_random_valid_representative"

        # A declared group type should always have a matching candidate, but
        # retain full-batch coverage if an externally produced manifest is
        # inconsistent with its rows.
        if not candidates:
            candidates = list(valid_rows)
            policy += "_fallback_any_valid"
        candidates = sorted(
            candidates,
            key=lambda row: str(row.get("rollout_id") or ""),
        )
        seed_material = f"{seed}\0{group_id}\0{group_type}".encode("utf-8")
        group_seed = int.from_bytes(
            hashlib.sha256(seed_material).digest()[:8],
            "big",
        )
        chosen = dict(random.Random(group_seed).choice(candidates))
        chosen["ordinary_reflect_representative"] = True
        chosen["ordinary_reflect_selection_policy"] = policy
        selected.append(chosen)
        manifest.append(
            {
                "group_id": group_id,
                "group_type": group_type,
                "valid_candidates": len(valid_rows),
                "eligible_candidates": len(candidates),
                "selected_rollout_id": chosen.get("rollout_id"),
                "selected_outcome": (
                    "success"
                    if float(chosen.get("hard", 0) or 0) > 0
                    else "failure"
                ),
                "selection_policy": policy,
                "selection_seed": group_seed,
            }
        )
    return selected, manifest


def _write_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def _write_jsonl(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_groups(
    rows: list[dict],
    requested_repeats: int,
    retry_attempts: dict[str, int] | None = None,
    min_valid_rollouts: int = 0,
) -> list[dict]:
    retry_attempts = retry_attempts or {}
    by_group: dict[str, list[dict]] = {}
    for row in rows:
        by_group.setdefault(str(row["group_id"]), []).append(row)

    groups: list[dict] = []
    for group_id, group_rows in by_group.items():
        ordered = sorted(group_rows, key=lambda x: int(x["repeat_idx"]))
        valid = [row for row in ordered if row.get("valid_for_learning")]
        successes = [row for row in valid if float(row.get("hard", 0) or 0) > 0]
        failures = [row for row in valid if float(row.get("hard", 0) or 0) <= 0]
        if successes and failures:
            group_type = "mixed"
        elif successes:
            group_type = "all_success"
        elif failures:
            group_type = "all_failure"
        else:
            group_type = "invalid"
        groups.append(
            {
                "group_id": group_id,
                "base_requested_rollouts": requested_repeats,
                "extra_attempts": int(retry_attempts.get(group_id, 0)),
                "requested_rollouts": (
                    requested_repeats + int(retry_attempts.get(group_id, 0))
                ),
                "valid_rollouts": len(valid),
                "invalid_rollouts": len(ordered) - len(valid),
                "meets_min_valid_rollouts": (
                    len(valid) >= min_valid_rollouts
                    if min_valid_rollouts > 0
                    else True
                ),
                "success_count": len(successes),
                "failure_count": len(failures),
                "group_type": group_type,
                "rollout_ids": [row["rollout_id"] for row in ordered],
                "valid_rollout_ids": [row["rollout_id"] for row in valid],
            }
        )
    return groups


def _item_id(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("id", "task_id", "question_id", "instance_id"):
            if item.get(key) is not None:
                return str(item[key])
    return str(getattr(item, "id", ""))


def _subset_env(env_manager: Any, wanted_ids: set[str]) -> Any:
    """Return the pending task subset while preserving adapter-specific metadata."""
    if isinstance(env_manager, list):
        return [item for item in env_manager if _item_id(item) in wanted_ids]
    if isinstance(env_manager, tuple):
        return tuple(item for item in env_manager if _item_id(item) in wanted_ids)

    items = list(getattr(env_manager, "items", None) or [])
    result_ids = list(getattr(env_manager, "result_ids", None) or [])
    if is_dataclass(env_manager) and items:
        ids = (
            [str(value) for value in result_ids]
            if len(result_ids) == len(items)
            else [_item_id(item) for item in items]
        )
        indices = [idx for idx, task_id in enumerate(ids) if task_id in wanted_ids]
        changes: dict[str, Any] = {
            "env_num": len(indices),
            "items": [items[idx] for idx in indices],
        }
        if result_ids:
            changes["result_ids"] = [result_ids[idx] for idx in indices]
        gamefiles = list(getattr(env_manager, "specific_gamefiles", None) or [])
        if gamefiles and len(gamefiles) == len(items):
            changes["specific_gamefiles"] = [gamefiles[idx] for idx in indices]
        return replace(env_manager, **changes)

    raise TypeError(
        "Group-relative retry requires a list-like or dataclass batch "
        f"environment, got {type(env_manager).__name__}"
    )


@contextmanager
def _retry_timeout(adapter: Any, timeout: int):
    """Temporarily override only retry rollouts' per-task timeout."""
    has_timeout = hasattr(adapter, "exec_timeout")
    original = getattr(adapter, "exec_timeout", None)
    if has_timeout:
        adapter.exec_timeout = timeout
    try:
        yield
    finally:
        if has_timeout:
            adapter.exec_timeout = original


def _annotate_rows(
    rows: list[dict],
    *,
    attempt_idx: int,
    attempt_dir: str,
    is_retry: bool,
    retry_idx: int | None = None,
    agent_failures_are_valid_evidence: bool = False,
) -> list[dict]:
    annotated: list[dict] = []
    for source_row in rows:
        row = dict(source_row)
        group_id = str(row.get("id", ""))
        valid, invalid_reason = classify_rollout_validity(
            row,
            agent_failures_are_valid_evidence=(
                agent_failures_are_valid_evidence
            ),
        )
        rollout_suffix = (
            f"retry{int(retry_idx or 0):02d}"
            if is_retry
            else f"r{attempt_idx:02d}"
        )
        row.update(
            {
                "group_id": group_id,
                "repeat_idx": attempt_idx,
                "rollout_id": f"{group_id}::{rollout_suffix}",
                "is_retry": is_retry,
                "valid_for_learning": valid,
                "invalid_reason": invalid_reason,
                "trajectory_dir": os.path.abspath(
                    os.path.join(attempt_dir, "predictions", group_id)
                ),
                "repeat_dir": os.path.abspath(attempt_dir),
            }
        )
        annotated.append(row)
    return annotated


def run_grouped_rollout(
    *,
    adapter,
    env_manager,
    skill_content: str,
    out_dir: str,
    rollouts_per_item: int,
    min_valid_rollouts: int = 6,
    max_extra_attempts_per_item: int = 4,
    retry_timeout: int = 900,
    use_eval_feedback: bool = True,
    agent_failures_are_valid_evidence: bool = False,
) -> GroupedRollout:
    """Run the same unique item batch repeatedly in isolated directories.

    Repeats are intentionally executed as repeat waves.  Each wave delegates
    concurrency to the unmodified environment adapter, so existing worker and
    rate-limit settings remain authoritative and no second worker knob is
    introduced.
    """
    if rollouts_per_item <= 0:
        raise ValueError("rollouts_per_item must be positive")
    if min_valid_rollouts <= 0:
        raise ValueError("min_valid_rollouts must be positive")
    if max_extra_attempts_per_item < 0:
        raise ValueError("max_extra_attempts_per_item must be non-negative")
    if retry_timeout <= 0:
        raise ValueError("retry_timeout must be positive")
    if min_valid_rollouts > rollouts_per_item + max_extra_attempts_per_item:
        raise ValueError(
            "min_valid_rollouts cannot exceed base rollouts plus maximum "
            "extra attempts"
        )

    os.makedirs(out_dir, exist_ok=True)
    all_rows: list[dict] = []
    retry_attempts: dict[str, int] = {}
    for repeat_idx in range(rollouts_per_item):
        repeat_dir = os.path.join(out_dir, f"repeat_{repeat_idx:02d}")
        print(
            f"      [group rollout] repeat={repeat_idx + 1}/{rollouts_per_item} "
            f"out={repeat_dir}"
        )
        repeat_rows = adapter.rollout(
            env_manager,
            skill_content,
            repeat_dir,
            use_eval_feedback=use_eval_feedback,
        )
        all_rows.extend(
            _annotate_rows(
                repeat_rows,
                attempt_idx=repeat_idx,
                attempt_dir=repeat_dir,
                is_retry=False,
                agent_failures_are_valid_evidence=(
                    agent_failures_are_valid_evidence
                ),
            )
        )

    for retry_idx in range(max_extra_attempts_per_item):
        valid_counts: dict[str, int] = {}
        for row in all_rows:
            if row.get("valid_for_learning"):
                group_id = str(row["group_id"])
                valid_counts[group_id] = valid_counts.get(group_id, 0) + 1
        known_ids = {str(row["group_id"]) for row in all_rows}
        pending_ids = {
            group_id
            for group_id in known_ids
            if valid_counts.get(group_id, 0) < min_valid_rollouts
            and retry_attempts.get(group_id, 0) < max_extra_attempts_per_item
        }
        if not pending_ids:
            break

        retry_dir = os.path.join(out_dir, f"retry_{retry_idx:02d}")
        retry_env = _subset_env(env_manager, pending_ids)
        for group_id in pending_ids:
            retry_attempts[group_id] = retry_attempts.get(group_id, 0) + 1
        print(
            f"      [group retry] attempt={retry_idx + 1}/"
            f"{max_extra_attempts_per_item} items={len(pending_ids)} "
            f"min_valid={min_valid_rollouts} timeout={retry_timeout}s "
            f"out={retry_dir}"
        )
        with _retry_timeout(adapter, retry_timeout):
            retry_rows = adapter.rollout(
                retry_env,
                skill_content,
                retry_dir,
                use_eval_feedback=use_eval_feedback,
                task_timeout=retry_timeout,
            )
        all_rows.extend(
            _annotate_rows(
                retry_rows,
                attempt_idx=rollouts_per_item + retry_idx,
                attempt_dir=retry_dir,
                is_retry=True,
                retry_idx=retry_idx,
                agent_failures_are_valid_evidence=(
                    agent_failures_are_valid_evidence
                ),
            )
        )

    groups = _build_groups(
        all_rows,
        rollouts_per_item,
        retry_attempts=retry_attempts,
        min_valid_rollouts=min_valid_rollouts,
    )
    valid_rows = [row for row in all_rows if row["valid_for_learning"]]
    requested_rollouts = sum(g["requested_rollouts"] for g in groups)
    stats = {
        "unique_items": len(groups),
        "base_requested_rollouts": len(groups) * rollouts_per_item,
        "extra_attempts": sum(retry_attempts.values()),
        "requested_rollouts": requested_rollouts,
        "materialized_rollouts": len(all_rows),
        "valid_rollouts": len(valid_rows),
        "invalid_rollouts": len(all_rows) - len(valid_rows),
        "agent_failures_are_valid_evidence": (
            agent_failures_are_valid_evidence
        ),
        "missing_rollouts": max(requested_rollouts - len(all_rows), 0),
        "min_valid_rollouts": min_valid_rollouts,
        "groups_meeting_min_valid": sum(
            g["meets_min_valid_rollouts"] for g in groups
        ),
        "groups_below_min_valid": sum(
            not g["meets_min_valid_rollouts"] for g in groups
        ),
        "mixed_groups": sum(g["group_type"] == "mixed" for g in groups),
        "all_success_groups": sum(g["group_type"] == "all_success" for g in groups),
        "all_failure_groups": sum(g["group_type"] == "all_failure" for g in groups),
        "invalid_groups": sum(g["group_type"] == "invalid" for g in groups),
    }
    stats["mixed_group_rate"] = (
        stats["mixed_groups"] / len(groups) if groups else 0.0
    )

    _write_jsonl(os.path.join(out_dir, "results.jsonl"), all_rows)
    _write_json(os.path.join(out_dir, "groups.json"), groups)
    _write_json(os.path.join(out_dir, "group_stats.json"), stats)
    return GroupedRollout(
        valid_results=valid_rows,
        all_results=all_rows,
        groups=groups,
        stats=stats,
    )
