"""ReflACT gradient clipping — LLM-driven edit ranking and selection.

Analogous to gradient clipping in neural network training: ranks candidate
edits by importance and selects the top-L to apply, controlling the
effective step size. Previously core/select.py.
"""
from __future__ import annotations

from skillopt.model import chat_optimizer
from skillopt.optimizer.meta_skill import format_meta_skill_context
from skillopt.optimizer.update_modes import (
    describe_item,
    get_payload_items,
    is_rewrite_mode,
    normalize_update_mode,
    payload_key,
    payload_label,
)
from skillopt.prompts import load_prompt
from skillopt.utils import extract_json


# ── Public API ────────────────────────────────────────────────────────────────

def rank_and_select(
    skill_content: str,
    patch: dict,
    max_edits: int,
    meta_skill_context: str = "",
    update_mode: str = "patch",
    group_relative: bool = False,
    ensure_contrast_candidate: bool = False,
) -> dict:
    """Use a optimizer LLM to rank edits by importance, then keep top-L.

    If the edit pool is within budget, returns the patch unchanged.
    Otherwise, calls the optimizer to rank and select the most impactful edits.

    Parameters
    ----------
    skill_content : str
        Current skill document.
    patch : dict
        Merged :class:`~skillopt.types.Patch` dict with ``edits`` list.
    max_edits : int
        Maximum number of edits to keep (the "edit budget").

    Returns
    -------
    dict
        :class:`~skillopt.types.Patch` dict with selected edits and
        optional ``ranking_details``.
    """
    update_mode = normalize_update_mode(update_mode)
    edits = get_payload_items(patch, update_mode)
    if len(edits) <= max_edits:
        return patch

    contrast_indices = [
        idx
        for idx, edit in enumerate(edits)
        if isinstance(edit, dict) and edit.get("source_type") == "contrast"
    ]

    def preserve_contrast(selected_indices: list[int]) -> tuple[list[int], bool]:
        """Reserve one slot for contrast evidence when configured."""
        if (
            not group_relative
            or not ensure_contrast_candidate
            or not contrast_indices
            or max_edits <= 0
            or any(idx in contrast_indices for idx in selected_indices)
        ):
            return selected_indices, False
        best_contrast_idx = max(
            contrast_indices,
            key=lambda idx: (
                int(edits[idx].get("group_support", 0) or 0),
                int(edits[idx].get("trajectory_support", 0) or 0),
                int(edits[idx].get("support_count", 0) or 0),
            ),
        )
        selected_indices = list(selected_indices[:max_edits])
        if len(selected_indices) < max_edits:
            selected_indices.append(best_contrast_idx)
        else:
            replace_at = next(
                (
                    pos
                    for pos in range(len(selected_indices) - 1, -1, -1)
                    if edits[selected_indices[pos]].get("source_type")
                    == "success"
                ),
                len(selected_indices) - 1,
            )
            selected_indices[replace_at] = best_contrast_idx
        return selected_indices, True

    # Build the edit pool description for the optimizer
    edits_desc = []
    for i, edit in enumerate(edits):
        description = describe_item(edit, update_mode, max_chars=500)
        if group_relative and isinstance(edit, dict):
            provenance = (
                f"source_type={edit.get('source_type', '?')} "
                f"group_support={edit.get('group_support', edit.get('support_count', 0))} "
                f"trajectory_support={edit.get('trajectory_support', 0)}"
            )
            description = f"{description}  {provenance}"
        edits_desc.append(f"[{i}] {description}")

    user = (
        f"## Current Skill\n{skill_content}\n\n"
        f"## {payload_label(update_mode, title=True)} Pool ({len(edits)} {payload_label(update_mode)}, budget={max_edits})\n"
        + "\n".join(edits_desc)
        + f"\n\nSelect the {max_edits} most important {payload_label(update_mode)}. "
        f"Return their 0-based indices in priority order."
    )
    optimizer_ctx = format_meta_skill_context(meta_skill_context)
    if optimizer_ctx:
        user = f"{optimizer_ctx}\n\n{user}"
    if group_relative:
        prompt_name = (
            "ranking_group_relative_rewrite"
            if is_rewrite_mode(update_mode)
            else "ranking_group_relative"
        )
    else:
        prompt_name = "ranking_rewrite" if is_rewrite_mode(update_mode) else "ranking"

    try:
        response, _ = chat_optimizer(
            system=load_prompt(prompt_name), user=user,
            max_completion_tokens=2048, retries=3, stage="ranking",
        )
        result = extract_json(response)
        if result and "selected_indices" in result:
            indices = result["selected_indices"]
            valid_indices = []
            for idx in indices:
                if (
                    isinstance(idx, int)
                    and 0 <= idx < len(edits)
                    and idx not in valid_indices
                ):
                    valid_indices.append(idx)
                if len(valid_indices) >= max_edits:
                    break
            valid_indices, forced = preserve_contrast(valid_indices)
            selected = []
            seen: set[int] = set()
            for idx in valid_indices:
                if (
                    isinstance(idx, int)
                    and 0 <= idx < len(edits)
                    and idx not in seen
                ):
                    selected.append(edits[idx])
                    seen.add(idx)
                if len(selected) >= max_edits:
                    break
            if selected:
                if forced:
                    result["contrast_candidate_forced"] = True
                    result["selected_indices"] = valid_indices
                return {
                    "reasoning": patch.get("reasoning", "")
                    + f" [optimizer-ranked: selected {len(selected)}/{len(edits)} {payload_label(update_mode)}]",
                    payload_key(update_mode): selected,
                    "ranking_details": result,
                }
    except Exception:  # noqa: BLE001
        pass

    # Fallback: simple truncation, with the same Group-Relative guarantee.
    fallback_indices, forced = preserve_contrast(
        list(range(min(max_edits, len(edits))))
    )
    return {
        "reasoning": patch.get("reasoning", "")
        + f" [fallback truncated {len(edits)}->{max_edits} {payload_label(update_mode)}]",
        payload_key(update_mode): [edits[idx] for idx in fallback_indices],
        "ranking_details": {
            "selected_indices": fallback_indices,
            "contrast_candidate_forced": forced,
        },
    }
