"""ReflACT Aggregate stage — hierarchical patch merging.

The Aggregate stage takes independently-generated patches from the Reflect
stage and merges them into a single coherent patch via hierarchical LLM calls.
Failure-driven patches take priority over success-driven ones.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from skillopt.model import chat_optimizer
from skillopt.optimizer.meta_skill import format_meta_skill_context
from skillopt.optimizer.update_modes import (
    get_payload_items,
    is_full_rewrite_minibatch_mode,
    is_rewrite_mode,
    normalize_update_mode,
    payload_key,
    payload_label,
)
from skillopt.prompts import load_prompt
from skillopt.utils import extract_json


# ── Internal helpers ──────────────────────────────────────────────────────────

def _merge_batch(
    skill_content: str,
    patches: list[dict],
    system_prompt: str,
    update_mode: str,
    meta_skill_context: str = "",
    level: int = 1,
) -> dict:
    """Call optimizer LLM to merge a batch of patches into one."""
    patches_text = json.dumps(patches, ensure_ascii=False, indent=2)
    user = (
        f"## Current Skill\n{skill_content}\n\n"
        f"## Patches to merge ({len(patches)} total, merge level {level})\n{patches_text}"
    )
    optimizer_ctx = format_meta_skill_context(meta_skill_context)
    if optimizer_ctx:
        user = f"{optimizer_ctx}\n\n{user}"
    try:
        response, _ = chat_optimizer(
            system=system_prompt,
            user=user,
            max_completion_tokens=64000 if is_full_rewrite_minibatch_mode(update_mode) else 4096,
            retries=3,
            stage="merge",
        )
        merged = extract_json(response)
        key = payload_key(update_mode)
        if merged and key in merged:
            for e in merged.get(key, []):
                e["merge_level"] = level
            return merged
    except Exception:  # noqa: BLE001
        pass
    # Fallback: concatenate all edits
    all_edits = []
    for p in patches:
        for e in get_payload_items(p, update_mode):
            e.setdefault("merge_level", level)
            all_edits.append(e)
    return {"reasoning": "fallback concatenation", payload_key(update_mode): all_edits}


def _hierarchical_merge(
    skill_content: str,
    patches: list[dict],
    system_prompt: str,
    update_mode: str,
    batch_size: int,
    verbose: bool,
    label: str = "",
    workers: int = 16,
    meta_skill_context: str = "",
) -> dict:
    """Hierarchically merge N patches using the given system prompt.

    Same-level batches are executed in PARALLEL via ThreadPoolExecutor.
    """
    if not patches:
        return {"reasoning": "no patches", payload_key(update_mode): []}
    if len(patches) == 1:
        return patches[0]

    current = list(patches)
    level = 0
    while len(current) > 1:
        level += 1
        batches: list[tuple[int, list[dict]]] = []
        for i in range(0, len(current), batch_size):
            batch = current[i : i + batch_size]
            batches.append((i, batch))

        if verbose:
            print(
                f"    [aggregate {label}] level={level}  "
                f"{len(current)} patches → {len(batches)} batches "
                f"(parallel, batch_size={batch_size})"
            )

        next_level: list[dict | None] = [None] * len(batches)

        to_merge: list[tuple[int, list[dict]]] = []
        for idx, (i, batch) in enumerate(batches):
            if len(batch) == 1:
                next_level[idx] = batch[0]
            else:
                to_merge.append((idx, batch))

        if to_merge:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(
                        _merge_batch, skill_content, batch, system_prompt, update_mode,
                        meta_skill_context, level,
                    ): idx
                    for idx, batch in to_merge
                }
                for fut in as_completed(futs):
                    idx = futs[fut]
                    next_level[idx] = fut.result()
                    if verbose:
                        batch_i, batch_data = batches[idx]
                        n_edits = len(get_payload_items(next_level[idx], update_mode))
                        print(
                            f"      [aggregate {label}] level={level} "
                            f"batch [{batch_i}:{batch_i+len(batch_data)}] "
                            f"→ 1 patch ({n_edits} {payload_label(update_mode)})"
                        )

        current = [x for x in next_level if x is not None]

    return current[0]


# ── Public API ────────────────────────────────────────────────────────────────

def merge_patches(
    skill_content: str,
    failure_patches: list[dict],
    success_patches: list[dict],
    batch_size: int = 8,
    verbose: bool = True,
    workers: int = 16,
    update_mode: str = "patch",
    meta_skill_context: str = "",
    contrast_patches: list[dict] | None = None,
    group_relative: bool = False,
    ensure_contrast_candidate: bool = False,
    preserve_memory_refs: bool = False,
) -> dict:
    """Hierarchical merge with optional same-task contrast evidence.

    1. Merge failure patches independently (parallel)
    2. Merge success patches independently (parallel)
    3. For ordinary SkillOpt, combine both groups with failure priority
    4. For Group-Relative SkillOpt, independently merge contrast patches,
       then combine failure / contrast / success with group-aware priority

    Returns a merged :class:`~skillopt.types.Patch` dict (``edits`` + ``reasoning``).
    """
    contrast_patches = list(contrast_patches or [])
    if verbose and group_relative:
        print(
            f"    [3/6 AGGREGATE] "
            f"failure={len(failure_patches)} success={len(success_patches)} "
            f"contrast={len(contrast_patches)} "
            f"(parallel, workers={workers})"
        )
    elif verbose:
        print(
            f"    [3/6 AGGREGATE] "
            f"failure={len(failure_patches)} success={len(success_patches)} "
            f"(parallel, workers={workers})"
        )

    update_mode = normalize_update_mode(update_mode)
    if is_full_rewrite_minibatch_mode(update_mode):
        merge_failure_prompt = load_prompt("merge_failure_full_rewrite")
        merge_success_prompt = load_prompt("merge_success_full_rewrite")
        merge_final_prompt = load_prompt("merge_final_full_rewrite")
    elif is_rewrite_mode(update_mode):
        merge_failure_prompt = load_prompt("merge_failure_rewrite")
        merge_success_prompt = load_prompt("merge_success_rewrite")
        merge_final_prompt = load_prompt("merge_final_rewrite")
    else:
        merge_failure_prompt = load_prompt("merge_failure")
        merge_success_prompt = load_prompt("merge_success")
        merge_final_prompt = load_prompt("merge_final")

    if group_relative:
        group_support_instruction = (
            "\n\nGROUP-RELATIVE SUPPORT RULE:\n"
            "- group_support is the number of DISTINCT original tasks "
            "supporting an item.\n"
            "- trajectory_support may contain repeated rollouts of the same "
            "task and is secondary.\n"
            "- Prefer patterns with broader group_support; never treat "
            "repeats of one task as independent task support.\n"
            "- Carry forward group_support, trajectory_support, "
            "support_count, and source_type.\n"
        )
        merge_failure_prompt += group_support_instruction
        merge_success_prompt += group_support_instruction
    if preserve_memory_refs:
        memory_provenance_instruction = (
            "\n\nDUAL-MEMORY PROVENANCE RULE:\n"
            "- Every input edit may contain memory_refs. Preserve those "
            "references verbatim in the corresponding merged edit.\n"
            "- When combining multiple edits, use the deduplicated union of "
            "their memory_refs.\n"
            "- Never invent or alter a bank_type or hypothesis_id.\n"
            "- support_count and group_support are audited and recomputed "
            "from the persistent banks after this merge.\n"
        )
        merge_failure_prompt += memory_provenance_instruction
        merge_success_prompt += memory_provenance_instruction

    failure_merged = _hierarchical_merge(
        skill_content, failure_patches, merge_failure_prompt, update_mode,
        batch_size, verbose, label="failure", workers=workers,
        meta_skill_context=meta_skill_context,
    )

    success_merged = _hierarchical_merge(
        skill_content, success_patches, merge_success_prompt, update_mode,
        batch_size, verbose, label="success", workers=workers,
        meta_skill_context=meta_skill_context,
    )

    f_edits = get_payload_items(failure_merged, update_mode)
    s_edits = get_payload_items(success_merged, update_mode)

    # Preserve the original two-source behavior byte-for-byte when the
    # Group-Relative method is not active.
    if not group_relative:
        if not f_edits and not s_edits:
            return {"reasoning": "no updates from either group", payload_key(update_mode): []}
        if not s_edits:
            return failure_merged
        if not f_edits:
            return success_merged

        combined_patches = [failure_merged, success_merged]
        combined_text = json.dumps(combined_patches, ensure_ascii=False, indent=2)
        if is_full_rewrite_minibatch_mode(update_mode):
            item_label = payload_label(update_mode)
            user = (
                f"## Current Skill\n{skill_content}\n\n"
                f"## Two pre-merged candidate groups to combine\n"
                f"Group 1 (from failed trajectories): "
                f"{len(f_edits)} {item_label}\n"
                f"Group 2 (from successful trajectories): "
                f"{len(s_edits)} {item_label}\n\n"
                f"{combined_text}"
            )
        else:
            user = (
                f"## Current Skill\n{skill_content}\n\n"
                f"## Two pre-merged patch groups to combine\n"
                f"Group 1 (failure-driven, HIGH priority): "
                f"{len(f_edits)} edits\n"
                f"Group 2 (success-driven, lower priority): "
                f"{len(s_edits)} edits\n\n"
                f"{combined_text}"
            )
        optimizer_ctx = format_meta_skill_context(meta_skill_context)
        if optimizer_ctx:
            user = f"{optimizer_ctx}\n\n{user}"
        try:
            response, _ = chat_optimizer(
                system=merge_final_prompt,
                user=user,
                max_completion_tokens=64000 if is_full_rewrite_minibatch_mode(update_mode) else 4096,
                retries=3,
                stage="merge",
            )
            final = extract_json(response)
            key = payload_key(update_mode)
            if final and key in final:
                if verbose:
                    print(
                        f"    [aggregate final] "
                        f"{len(f_edits)}+{len(s_edits)} → "
                        f"{len(final[key])} {payload_label(update_mode)}"
                    )
                return final
        except Exception:  # noqa: BLE001
            pass

        return {
            "reasoning": "fallback: failure first, then success",
            payload_key(update_mode): f_edits + s_edits,
        }

    if is_full_rewrite_minibatch_mode(update_mode):
        merge_contrast_prompt = load_prompt("merge_contrast_full_rewrite")
        merge_group_final_prompt = load_prompt(
            "merge_group_relative_final_full_rewrite"
        )
    elif is_rewrite_mode(update_mode):
        merge_contrast_prompt = load_prompt("merge_contrast_rewrite")
        merge_group_final_prompt = load_prompt(
            "merge_group_relative_final_rewrite"
        )
    else:
        merge_contrast_prompt = load_prompt("merge_contrast")
        merge_group_final_prompt = load_prompt("merge_group_relative_final")

    if preserve_memory_refs:
        merge_contrast_prompt += memory_provenance_instruction
        merge_group_final_prompt += memory_provenance_instruction

    contrast_merged = _hierarchical_merge(
        skill_content,
        contrast_patches,
        merge_contrast_prompt,
        update_mode,
        batch_size,
        verbose,
        label="contrast",
        workers=workers,
        meta_skill_context=meta_skill_context,
    )
    c_edits = get_payload_items(contrast_merged, update_mode)

    nonempty_groups = [
        (label, patch, len(items))
        for label, patch, items in (
            ("failure", failure_merged, f_edits),
            ("contrast", contrast_merged, c_edits),
            ("success", success_merged, s_edits),
        )
        if items
    ]
    if not nonempty_groups:
        return {
            "reasoning": "no updates from failure, contrast, or success analysis",
            payload_key(update_mode): [],
        }
    if len(nonempty_groups) == 1:
        return nonempty_groups[0][1]

    def preserve_contrast_candidate(container: dict) -> dict:
        """Keep one causal contrast item if the final merge dropped them all."""
        if not ensure_contrast_candidate or not c_edits:
            return container
        final_items = get_payload_items(container, update_mode)
        if any(
            isinstance(item, dict) and item.get("source_type") == "contrast"
            for item in final_items
        ):
            return container
        best_contrast = max(
            c_edits,
            key=lambda item: (
                int(item.get("group_support", 0) or 0),
                int(item.get("trajectory_support", 0) or 0),
                int(item.get("support_count", 0) or 0),
            ),
        )
        final_items.append(best_contrast)
        container[payload_key(update_mode)] = final_items
        container["reasoning"] = (
            str(container.get("reasoning") or "")
            + " [preserved one contrast candidate omitted by final merge]"
        ).strip()
        container["contrast_candidate_preserved"] = True
        return container

    combined = [patch for _, patch, _ in nonempty_groups]
    group_summary = "\n".join(
        f"- {label}: {count} {payload_label(update_mode)}"
        for label, _, count in nonempty_groups
    )
    user = (
        f"## Current Skill\n{skill_content}\n\n"
        "## Pre-merged evidence groups\n"
        f"{group_summary}\n\n"
        "Priority rule: recurring failure patterns supported by more distinct "
        "task groups come first; same-task contrast evidence comes before "
        "success-only reinforcement when support is otherwise comparable.\n\n"
        + json.dumps(combined, ensure_ascii=False, indent=2)
    )
    optimizer_ctx = format_meta_skill_context(meta_skill_context)
    if optimizer_ctx:
        user = f"{optimizer_ctx}\n\n{user}"
    try:
        response, _ = chat_optimizer(
            system=merge_group_final_prompt,
            user=user,
            max_completion_tokens=(
                64000 if is_full_rewrite_minibatch_mode(update_mode) else 4096
            ),
            retries=3,
            stage="merge",
        )
        final = extract_json(response)
        key = payload_key(update_mode)
        if final and key in final:
            if verbose:
                print(
                    f"    [aggregate group-relative final] "
                    f"failure={len(f_edits)} contrast={len(c_edits)} "
                    f"success={len(s_edits)} → "
                    f"{len(final[key])} {payload_label(update_mode)}"
                )
            return preserve_contrast_candidate(final)
    except Exception:  # noqa: BLE001
        pass

    return preserve_contrast_candidate({
        "reasoning": "fallback: failure, then contrast, then success",
        payload_key(update_mode): f_edits + c_edits + s_edits,
    })
