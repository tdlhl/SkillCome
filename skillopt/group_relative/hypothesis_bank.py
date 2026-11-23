"""Persistent cross-step hypotheses for Group-Relative SkillOpt.

This module is opt-in.  The existing group-relative path does not import or
write a bank unless the vNext config explicitly enables it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


class ContrastHypothesisBank:
    """Store general mechanisms and accumulate independent group support."""

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

    def render_context(self) -> str:
        rows = []
        for item in self.data["hypotheses"]:
            rows.append({
                "hypothesis_id": item.get("hypothesis_id"),
                "mechanism": item.get("mechanism"),
                "support_group_ids": item.get("support_group_ids", []),
                "support_count": len(item.get("support_group_ids", [])),
            })
        if not rows:
            return "No earlier contrast hypotheses have been recorded."
        return json.dumps(rows, ensure_ascii=False, indent=2)

    def _find(self, hypothesis_id: str) -> dict | None:
        for item in self.data["hypotheses"]:
            if item.get("hypothesis_id") == hypothesis_id:
                return item
        return None

    def integrate(
        self,
        result: dict,
        *,
        group_id: str,
        step: int,
        skill_hash_value: str,
    ) -> dict:
        """Update the bank and annotate a contrast result in place.

        A hypothesis supported by only one group remains a valid edit source.
        Additional groups merely increase its ranking evidence; they are not a
        prerequisite for editing the skill.
        """
        hypothesis = result.get("hypothesis")
        if not isinstance(hypothesis, dict):
            hypothesis = {}
        related = str(hypothesis.get("related_hypothesis_id") or "").strip()
        mechanism = str(hypothesis.get("mechanism") or "").strip()
        if not mechanism:
            mechanism = str(
                result.get("causal_hypothesis")
                or result.get("contrast_summary")
                or result.get("first_divergence")
                or ""
            ).strip()

        entry = self._find(related) if related else None
        if entry is None:
            digest_source = _normalise(mechanism) or f"group:{group_id}"
            hypothesis_id = "contrast_" + hashlib.sha256(
                digest_source.encode("utf-8")
            ).hexdigest()[:12]
            entry = self._find(hypothesis_id)
            if entry is None:
                entry = {
                    "hypothesis_id": hypothesis_id,
                    "mechanism": mechanism,
                    "support_group_ids": [],
                    "occurrences": [],
                    "status": "active",
                }
                self.data["hypotheses"].append(entry)
        else:
            hypothesis_id = str(entry["hypothesis_id"])
            if mechanism and not entry.get("mechanism"):
                entry["mechanism"] = mechanism

        support = {str(x) for x in entry.get("support_group_ids", [])}
        support.add(str(group_id))
        entry["support_group_ids"] = sorted(support)
        occurrences = entry.setdefault("occurrences", [])
        occurrence_key = (int(step), str(group_id))
        existing = {
            (int(x.get("step", -1)), str(x.get("group_id", "")))
            for x in occurrences
            if isinstance(x, dict)
        }
        if occurrence_key not in existing:
            occurrences.append({
                "step": int(step),
                "group_id": str(group_id),
                "skill_hash": str(skill_hash_value),
            })

        result["hypothesis_id"] = hypothesis_id
        result["group_ids"] = list(entry["support_group_ids"])
        result["group_support"] = len(entry["support_group_ids"])
        patch = result.get("patch", {})
        if isinstance(patch, dict):
            for values in patch.values():
                if not isinstance(values, list):
                    continue
                for item in values:
                    if isinstance(item, dict):
                        item["hypothesis_id"] = hypothesis_id
                        item["group_ids"] = list(entry["support_group_ids"])
                        item["group_support"] = len(entry["support_group_ids"])
        self.save()
        return result

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

