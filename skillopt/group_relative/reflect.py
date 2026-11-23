"""Same-task success/failure contrast analysis."""
from __future__ import annotations

import json
import os
import traceback

from skillopt.gradient.reflect import fmt_minibatch_trajectories
from skillopt.group_relative.hypothesis_bank import ContrastHypothesisBank
from skillopt.group_relative.memory import ContrastMemoryBank
from skillopt.model import chat_optimizer
from skillopt.optimizer.meta_skill import format_meta_skill_context
from skillopt.optimizer.update_modes import (
    get_payload_items,
    is_full_rewrite_minibatch_mode,
    normalize_update_mode,
    payload_label,
    truncate_payload,
)
from skillopt.prompts import load_prompt
from skillopt.utils import extract_json


def _contrast_prompt_name(
    update_mode: str,
    hypothesis_bank_enabled: bool = False,
) -> str:
    mode = normalize_update_mode(update_mode)
    if hypothesis_bank_enabled and not is_full_rewrite_minibatch_mode(mode):
        return "analyst_contrast_hypothesis"
    if is_full_rewrite_minibatch_mode(mode):
        return "analyst_contrast_full_rewrite"
    if mode == "rewrite_from_suggestions":
        return "analyst_contrast_rewrite"
    return "analyst_contrast"


def _run_one_group(
    *,
    skill_content: str,
    items: list[dict],
    edit_budget: int,
    step_buffer_context: str,
    meta_skill_context: str,
    update_mode: str,
    hypothesis_bank_context: str = "",
) -> dict | None:
    successes = [row for row in items if float(row.get("hard", 0) or 0) > 0]
    failures = [row for row in items if float(row.get("hard", 0) or 0) <= 0]
    if not successes or not failures:
        return None

    mode = normalize_update_mode(update_mode)
    trajectories = fmt_minibatch_trajectories(items, prediction_dir="")
    if not trajectories.strip():
        return None

    group_id = str(items[0].get("group_id") or items[0].get("id") or "")
    user = (
        f"## Current Skill\n{skill_content}\n\n"
        f"## Same-Task Group\n"
        f"group_id={group_id}\n"
        f"valid_rollouts={len(items)} successes={len(successes)} "
        f"failures={len(failures)}\n\n"
    )
    if is_full_rewrite_minibatch_mode(mode):
        user += (
            "## Update Format\n"
            "Produce at most one complete replacement skill candidate, and "
            "only when the contrast supports a generalizable improvement.\n\n"
        )
    else:
        user += (
            f"## {payload_label(mode, title=True)} Budget\n"
            f"Produce at most L={edit_budget} {payload_label(mode)}. "
            "It is valid to return an empty update when the difference is not "
            "causal or generalizable.\n\n"
        )
    if step_buffer_context.strip():
        user += f"## Previous Steps in This Epoch\n{step_buffer_context}\n\n"
    if hypothesis_bank_context.strip():
        user += (
            "## Cross-Step Contrast Hypothesis Bank\n"
            f"{hypothesis_bank_context}\n\n"
        )
    optimizer_ctx = format_meta_skill_context(meta_skill_context)
    if optimizer_ctx:
        user += optimizer_ctx + "\n\n"
    user += (
        "## Rollouts of the Same Task\n"
        "Compare the successful and failed trajectories directly. Identify "
        "the first meaningful behavioral divergence before proposing an "
        "update.\n\n"
        f"{trajectories}"
    )

    try:
        response, _ = chat_optimizer(
            system=load_prompt(
                _contrast_prompt_name(
                    mode,
                    hypothesis_bank_enabled=bool(hypothesis_bank_context),
                )
            ),
            user=user,
            max_completion_tokens=(
                64000 if is_full_rewrite_minibatch_mode(mode) else 4096
            ),
            retries=3,
            stage="contrast_analyst",
        )
        result = extract_json(response)
        if not result or "patch" not in result:
            return None
        result["source_type"] = "contrast"
        result["batch_size"] = len(items)
        result["trajectory_support"] = len(items)
        result["group_support"] = 1
        result["group_ids"] = [group_id]
        if not is_full_rewrite_minibatch_mode(mode):
            truncate_payload(result["patch"], edit_budget, mode)
        for item in get_payload_items(result["patch"], mode):
            if isinstance(item, dict):
                item.setdefault("source_type", "contrast")
                item.setdefault("support_count", 1)
                item.setdefault("group_support", 1)
                item.setdefault("trajectory_support", len(items))
        return result
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None


def run_group_contrast_reflect(
    *,
    results: list[dict],
    groups: list[dict],
    skill_content: str,
    patches_dir: str,
    edit_budget: int,
    step_buffer_context: str = "",
    meta_skill_context: str = "",
    update_mode: str = "patch",
    hypothesis_bank_path: str | None = None,
    step: int = 0,
    skill_hash_value: str = "",
    dual_memory_enabled: bool = False,
) -> list[dict | None]:
    """Run one additional optimizer contrast step for each mixed group.

    Calls are deliberately sequential.  The existing original reflection
    completes first, and no contrast-specific worker configuration is added.
    """
    os.makedirs(patches_dir, exist_ok=True)
    hypothesis_bank = None
    if hypothesis_bank_path:
        bank_cls = (
            ContrastMemoryBank
            if dual_memory_enabled
            else ContrastHypothesisBank
        )
        hypothesis_bank = bank_cls(hypothesis_bank_path)
    rows_by_group: dict[str, list[dict]] = {}
    for row in results:
        if row.get("valid_for_learning"):
            rows_by_group.setdefault(str(row.get("group_id")), []).append(row)

    mixed = [group for group in groups if group.get("group_type") == "mixed"]
    print(f"    [2b/6 CONTRAST] mixed_groups={len(mixed)} (sequential)")
    patches: list[dict | None] = []
    for idx, group in enumerate(mixed):
        group_id = str(group["group_id"])
        path = os.path.join(patches_dir, f"group_contrast_{idx:03d}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if hypothesis_bank is not None and isinstance(cached, dict):
                cached = hypothesis_bank.integrate(
                    cached,
                    group_id=group_id,
                    step=step,
                    skill_hash_value=skill_hash_value,
                )
            patches.append(cached)
            continue
        patch = _run_one_group(
            skill_content=skill_content,
            items=sorted(
                rows_by_group.get(group_id, []),
                key=lambda row: int(row.get("repeat_idx", 0)),
            ),
            edit_budget=edit_budget,
            step_buffer_context=step_buffer_context,
            meta_skill_context=meta_skill_context,
            update_mode=update_mode,
            hypothesis_bank_context=(
                hypothesis_bank.render_context()
                if hypothesis_bank is not None
                else ""
            ),
        )
        if hypothesis_bank is not None and patch is not None:
            patch = hypothesis_bank.integrate(
                patch,
                group_id=group_id,
                step=step,
                skill_hash_value=skill_hash_value,
            )
        if patch is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(patch, f, ensure_ascii=False, indent=2)
        patches.append(patch)
        n_items = len(
            get_payload_items(
                patch.get("patch", {}) if patch else {},
                update_mode,
            )
        )
        print(
            f"      [contrast] {idx + 1}/{len(mixed)} group={group_id} "
            f"→ {n_items} {payload_label(update_mode)}"
        )
    return patches
