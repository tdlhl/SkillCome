"""Opt-in recording of pre/post slow-update skill variants."""
from __future__ import annotations

import json
import os
import shutil

from skillopt.utils import skill_hash


def record_slow_update_variant(
    *,
    out_root: str,
    epoch: int,
    phase: str,
    current_skill: str,
    best_skill: str,
    action: str = "pending",
) -> None:
    """Persist exact variants without changing trainer decisions."""
    if phase not in {"before", "after"}:
        raise ValueError(f"invalid slow-update phase: {phase}")
    root = os.path.join(out_root, "slow_update_variants")
    epoch_dir = os.path.join(root, f"epoch_{epoch:02d}")
    os.makedirs(epoch_dir, exist_ok=True)
    paths = {
        "current": os.path.join(epoch_dir, f"current_{phase}.md"),
        "best": os.path.join(epoch_dir, f"best_{phase}.md"),
    }
    # A resumed epoch may already have advanced past the slow update.  Never
    # overwrite the original pre-update evidence with resumed post-update
    # state.
    if phase == "before" and all(os.path.exists(path) for path in paths.values()):
        return
    with open(paths["current"], "w", encoding="utf-8") as f:
        f.write(current_skill)
    with open(paths["best"], "w", encoding="utf-8") as f:
        f.write(best_skill)

    manifest_path = os.path.join(epoch_dir, "variant_manifest.json")
    manifest = {"epoch": epoch}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            manifest.update(loaded)
    manifest[phase] = {
        "action": action,
        "current_path": os.path.relpath(paths["current"], out_root),
        "best_path": os.path.relpath(paths["best"], out_root),
        "current_hash": skill_hash(current_skill),
        "best_hash": skill_hash(best_skill),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    shutil.copyfile(paths["current"], os.path.join(root, f"latest_current_{phase}.md"))
    shutil.copyfile(paths["best"], os.path.join(root, f"latest_best_{phase}.md"))
