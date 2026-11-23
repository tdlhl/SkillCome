"""Safety handling for edits that accidentally target slow guidance."""
from __future__ import annotations

import json

from skillopt.model import chat_optimizer
from skillopt.optimizer.slow_update import strip_slow_update_field
from skillopt.prompts import load_prompt
from skillopt.utils import extract_json


def normalize_protected_edits(
    skill_content: str,
    ranked_patch: dict,
    apply_report: list[dict],
) -> tuple[dict, dict]:
    """Ask the optimizer to relocate protected-region edits into the main skill."""
    edits = ranked_patch.get("edits", [])
    skipped: list[tuple[dict, dict]] = []
    for report in apply_report:
        if report.get("status") != "skipped_protected_slow_update_region":
            continue
        idx = int(report.get("index", 0)) - 1
        if 0 <= idx < len(edits) and isinstance(edits[idx], dict):
            skipped.append((edits[idx], report))

    if not skipped:
        return {"reasoning": "", "edits": []}, {
            "attempted": False,
            "skipped_edit_count": 0,
        }

    main_skill = strip_slow_update_field(skill_content)
    skipped_edits = [edit for edit, _ in skipped]
    user = (
        f"## Main Skill (the only editable region)\n{main_skill}\n\n"
        f"## Edits that targeted protected slow guidance\n"
        f"{json.dumps(skipped_edits, ensure_ascii=False, indent=2)}\n\n"
        f"Return exactly {len(skipped_edits)} repaired edit(s)."
    )
    response, _ = chat_optimizer(
        system=load_prompt("normalize_protected_edits"),
        user=user,
        max_completion_tokens=4096,
        retries=3,
        stage="normalize_protected_edits",
    )
    result = extract_json(response) or {}
    raw_repaired = result.get("edits", [])
    repaired: list[dict] = []
    if isinstance(raw_repaired, list):
        for pos, item in enumerate(raw_repaired[:len(skipped)]):
            if not isinstance(item, dict):
                continue
            original = skipped[pos][0]
            clean = dict(item)
            for key in (
                "source_type",
                "support_count",
                "group_support",
                "trajectory_support",
            ):
                if key in original:
                    clean[key] = original[key]
            clean["relocated_from_protected_region"] = True
            clean["original_target"] = original.get("target", "")
            repaired.append(clean)

    return {
        "reasoning": str(result.get("reasoning", "")).strip(),
        "edits": repaired,
    }, {
        "attempted": True,
        "skipped_edit_count": len(skipped),
        "repaired_edit_count": len(repaired),
        "raw_response": result,
    }
