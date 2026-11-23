"""Compact cross-step failure hypotheses for Group-Relative SkillOpt."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from skillopt.optimizer.update_modes import get_payload_items


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _fallback_mechanism(result: dict) -> str:
    summaries = result.get("failure_summary", [])
    if isinstance(summaries, list):
        ranked = sorted(
            (item for item in summaries if isinstance(item, dict)),
            key=lambda item: int(item.get("count", 0) or 0),
            reverse=True,
        )
        if ranked:
            text = str(
                ranked[0].get("description")
                or ranked[0].get("failure_type")
                or ""
            ).strip()
            if text:
                return text
    patch = result.get("patch", {})
    if isinstance(patch, dict):
        return str(patch.get("reasoning") or "").strip()
    return ""


class FailureHypothesisBank:
    """Accumulate recurring failure mechanisms across distinct task groups.

    The persisted state deliberately excludes trajectories and skill text.  A
    compact top-k view is sent back to the analyst on the next step.
    """

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "hypotheses": [],
        }
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        if not isinstance(self.data.get("hypotheses"), list):
            self.data["hypotheses"] = []

    def _find(self, hypothesis_id: str) -> dict | None:
        for item in self.data["hypotheses"]:
            if item.get("hypothesis_id") == hypothesis_id:
                return item
        return None

    def render_context(
        self,
        *,
        max_entries: int = 8,
        max_mechanism_chars: int = 240,
    ) -> str:
        ranked = sorted(
            self.data["hypotheses"],
            key=lambda item: (
                len(item.get("support_group_ids", [])),
                int(item.get("last_step", 0) or 0),
            ),
            reverse=True,
        )[:max_entries]
        if not ranked:
            return "No earlier cross-step failure hypotheses."
        rows = [
            {
                "hypothesis_id": item.get("hypothesis_id"),
                "mechanism": str(item.get("mechanism") or "")[
                    :max_mechanism_chars
                ],
                "failure_group_support": len(
                    item.get("support_group_ids", [])
                ),
            }
            for item in ranked
        ]
        return json.dumps(rows, ensure_ascii=False, indent=2)

    def integrate(
        self,
        result: dict,
        *,
        group_ids: list[str],
        step: int,
        update_mode: str = "patch",
    ) -> dict:
        """Integrate one ordinary failure analyst result in place."""
        hypothesis = result.get("failure_hypothesis")
        if not isinstance(hypothesis, dict):
            hypothesis = {}
        related = str(
            hypothesis.get("related_hypothesis_id") or ""
        ).strip()
        mechanism = str(hypothesis.get("mechanism") or "").strip()
        if not mechanism:
            mechanism = _fallback_mechanism(result)
        if not mechanism:
            return result

        entry = self._find(related) if related else None
        if entry is None:
            digest = hashlib.sha256(
                _normalise(mechanism).encode("utf-8")
            ).hexdigest()[:12]
            hypothesis_id = f"failure_{digest}"
            entry = self._find(hypothesis_id)
            if entry is None:
                entry = {
                    "hypothesis_id": hypothesis_id,
                    "mechanism": mechanism[:500],
                    "support_group_ids": [],
                    "last_step": int(step),
                }
                self.data["hypotheses"].append(entry)
        else:
            hypothesis_id = str(entry["hypothesis_id"])

        support = {
            str(item)
            for item in entry.get("support_group_ids", [])
            if str(item)
        }
        support.update(str(item) for item in group_ids if str(item))
        entry["support_group_ids"] = sorted(support)
        entry["last_step"] = int(step)

        support_count = len(entry["support_group_ids"])
        result["failure_hypothesis_id"] = hypothesis_id
        result["failure_group_support"] = support_count
        patch = result.get("patch", {})
        if isinstance(patch, dict):
            items = get_payload_items(patch, update_mode)
            raw_indices = hypothesis.get("edit_indices", [])
            indices = {
                int(index)
                for index in raw_indices
                if isinstance(index, int) and 0 <= index < len(items)
            }
            if not indices and items:
                indices = {0}
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if index not in indices:
                    continue
                item["failure_hypothesis_id"] = hypothesis_id
                item["failure_group_support"] = support_count
                item["group_support"] = support_count
                item["group_ids"] = list(entry["support_group_ids"])
                item.setdefault("source_type", "failure")
        self.save()
        return result

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
