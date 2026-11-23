"""Run-level statistics for Group-Relative SkillOpt."""
from __future__ import annotations

import glob
import json
import os


def write_global_group_summary(out_root: str, train_pool_size: int) -> dict:
    """Aggregate all materialized training groups and write one run summary.

    ``group_instances`` counts a task every time it appears in a training
    batch. ``unique_items`` / ``covered_train_items`` deduplicate task IDs
    across the whole run and are therefore the train-set coverage numerator.
    """
    group_paths = sorted(
        glob.glob(
            os.path.join(
                out_root,
                "steps",
                "step_*",
                "**",
                "rollout",
                "groups.json",
            ),
            recursive=True,
        )
    )

    groups: list[dict] = []
    for path in group_paths:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        if isinstance(value, list):
            groups.extend(row for row in value if isinstance(row, dict))

    covered_ids = {
        str(group.get("group_id"))
        for group in groups
        if group.get("group_id") is not None
    }
    group_instances = len(groups)
    valid_rollouts = sum(int(g.get("valid_rollouts", 0) or 0) for g in groups)
    invalid_rollouts = sum(int(g.get("invalid_rollouts", 0) or 0) for g in groups)
    requested_rollouts = sum(
        int(g.get("requested_rollouts", 0) or 0) for g in groups
    )
    base_requested_rollouts = sum(
        int(g.get("base_requested_rollouts", g.get("requested_rollouts", 0)) or 0)
        for g in groups
    )
    extra_attempts = sum(int(g.get("extra_attempts", 0) or 0) for g in groups)
    groups_meeting_min_valid = sum(
        g.get("meets_min_valid_rollouts") is True for g in groups
    )
    groups_below_min_valid = sum(
        g.get("meets_min_valid_rollouts") is False for g in groups
    )
    mixed_groups = sum(g.get("group_type") == "mixed" for g in groups)
    all_success_groups = sum(g.get("group_type") == "all_success" for g in groups)
    all_failure_groups = sum(g.get("group_type") == "all_failure" for g in groups)
    invalid_groups = sum(g.get("group_type") == "invalid" for g in groups)
    pool_size = max(int(train_pool_size or 0), 0)

    summary = {
        "train_pool_size": pool_size,
        "unique_items": len(covered_ids),
        "covered_train_items": len(covered_ids),
        "train_coverage_rate": (
            len(covered_ids) / pool_size if pool_size else 0.0
        ),
        "group_instances": group_instances,
        "completed_group_batches": len(group_paths),
        "base_requested_rollouts": base_requested_rollouts,
        "extra_attempts": extra_attempts,
        "requested_rollouts": requested_rollouts,
        "materialized_rollouts": valid_rollouts + invalid_rollouts,
        "missing_rollouts": max(
            requested_rollouts - valid_rollouts - invalid_rollouts,
            0,
        ),
        "valid_rollouts": valid_rollouts,
        "invalid_rollouts": invalid_rollouts,
        "groups_meeting_min_valid": groups_meeting_min_valid,
        "groups_below_min_valid": groups_below_min_valid,
        "mixed_groups": mixed_groups,
        "all_success_groups": all_success_groups,
        "all_failure_groups": all_failure_groups,
        "invalid_groups": invalid_groups,
        "mixed_group_rate": (
            mixed_groups / group_instances if group_instances else 0.0
        ),
    }

    sampler_path = os.path.join(out_root, "group_sampler_state.json")
    if os.path.exists(sampler_path):
        with open(sampler_path, encoding="utf-8") as f:
            sampler = json.load(f)
        item_stats = sampler.get("item_stats", {})
        replayed = {
            task_id
            for task_id, row in item_stats.items()
            if int(row.get("replay_count", 0) or 0) > 0
        }
        ever_mixed = {
            task_id
            for task_id, row in item_stats.items()
            if row.get("ever_mixed") is True
        }
        summary["sampler"] = {
            "sampling_strategy": "coverage_then_mixed",
            "coverage_passes_target": int(
                sampler.get("coverage_passes", 1) or 1
            ),
            "coverage_passes_selected": int(
                sampler.get("coverage_pass_index", 0) or 0
            ),
            "coverage_complete": int(
                sampler.get("coverage_pass_index", 0) or 0
            )
            >= int(sampler.get("coverage_passes", 1) or 1),
            "ever_mixed_items": len(ever_mixed),
            "mixed_replay_batches": int(
                sampler.get("replay_batches", 0) or 0
            ),
            "mixed_replay_unique_items": len(replayed),
            "assignments": len(sampler.get("assignments", {})),
            "completed_assignments": len(
                sampler.get("completed_assignments", [])
            ),
        }

    path = os.path.join(out_root, "group_summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary
