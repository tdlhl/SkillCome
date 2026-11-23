"""Compact dual-memory banks for group-relative skill optimization."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from skillopt.optimizer.update_modes import get_payload_items


MAX_ENTRIES_PER_BANK = 8
MAX_DISPLAY_MECHANISM_CHARS = 500
TOTAL_CONTEXT_CHAR_BUDGET = 12_000
PER_BANK_CONTEXT_CHAR_BUDGET = TOTAL_CONTEXT_CHAR_BUDGET // 2


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _fallback_failure_mechanism(result: dict) -> str:
    summaries = result.get("failure_summary", [])
    if isinstance(summaries, list):
        ranked = sorted(
            (item for item in summaries if isinstance(item, dict)),
            key=lambda item: int(item.get("count", 0) or 0),
            reverse=True,
        )
        if ranked:
            value = str(
                ranked[0].get("description")
                or ranked[0].get("failure_type")
                or ""
            ).strip()
            if value:
                return value
    patch = result.get("patch", {})
    if isinstance(patch, dict):
        return str(patch.get("reasoning") or "").strip()
    return ""


class _CompactMemoryBank:
    bank_type = ""
    id_prefix = ""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.data: dict[str, Any] = {
            "schema_version": 2,
            "bank_type": self.bank_type,
            "hypotheses": [],
        }
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        self.data["schema_version"] = 2
        self.data["bank_type"] = self.bank_type
        if not isinstance(self.data.get("hypotheses"), list):
            self.data["hypotheses"] = []

    def _find(self, hypothesis_id: str) -> dict | None:
        for item in self.data["hypotheses"]:
            if item.get("hypothesis_id") == hypothesis_id:
                return item
        return None

    def _new_entry(self, mechanism: str, step: int) -> dict:
        digest = hashlib.sha256(
            _normalise(mechanism).encode("utf-8")
        ).hexdigest()[:12]
        hypothesis_id = f"{self.id_prefix}{digest}"
        entry = self._find(hypothesis_id)
        if entry is None:
            entry = {
                "hypothesis_id": hypothesis_id,
                # Keep the complete mechanism on disk for auditing.
                "mechanism": mechanism,
                "mechanism_history": [],
                "support_group_ids": [],
                "occurrences": [],
                "last_step": int(step),
                "status": "active",
            }
            self.data["hypotheses"].append(entry)
        return entry

    def _refine_mechanism(
        self,
        entry: dict,
        mechanism: str,
        *,
        step: int,
    ) -> None:
        """Retain an auditable history while accepting an explicit refinement."""
        mechanism = str(mechanism or "").strip()
        previous = str(entry.get("mechanism") or "").strip()
        if not mechanism or _normalise(mechanism) == _normalise(previous):
            return
        history = entry.setdefault("mechanism_history", [])
        history.append({"step": int(step), "mechanism": previous})
        entry["mechanism"] = mechanism

    def render_context(self) -> str:
        ranked = sorted(
            (
                item
                for item in self.data["hypotheses"]
                if item.get("status", "active") == "active"
            ),
            key=lambda item: (
                len(item.get("support_group_ids", [])),
                int(item.get("last_step", 0) or 0),
                str(item.get("hypothesis_id") or ""),
            ),
            reverse=True,
        )[:MAX_ENTRIES_PER_BANK]
        if not ranked:
            return f"No earlier cross-step {self.bank_type} hypotheses."
        kept: list[dict[str, Any]] = []
        for item in ranked:
            row = {
                "hypothesis_id": item.get("hypothesis_id"),
                "mechanism": str(item.get("mechanism") or "")[
                    :MAX_DISPLAY_MECHANISM_CHARS
                ],
                # Full support IDs remain only in the JSON bank.
                "support_count": len(item.get("support_group_ids", [])),
                "last_step": int(item.get("last_step", 0) or 0),
            }
            candidate = kept + [row]
            rendered = json.dumps(candidate, ensure_ascii=False, indent=2)
            if len(rendered) > PER_BANK_CONTEXT_CHAR_BUDGET:
                break
            kept = candidate
        return json.dumps(kept, ensure_ascii=False, indent=2)

    def support_ids(self, hypothesis_id: str) -> set[str]:
        entry = self._find(str(hypothesis_id))
        if entry is None:
            return set()
        return {
            str(value)
            for value in entry.get("support_group_ids", [])
            if str(value)
        }

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


class ContrastMemoryBank(_CompactMemoryBank):
    bank_type = "contrast"
    id_prefix = "contrast_"

    def integrate(
        self,
        result: dict,
        *,
        group_id: str,
        step: int,
        skill_hash_value: str,
    ) -> dict:
        hypothesis = result.get("hypothesis")
        if not isinstance(hypothesis, dict):
            hypothesis = {}
        related = str(hypothesis.get("related_hypothesis_id") or "").strip()
        mechanism = str(hypothesis.get("mechanism") or "").strip()
        if not mechanism:
            summary = result.get("contrast_summary", {})
            if isinstance(summary, dict):
                mechanism = str(
                    summary.get("causal_hypothesis")
                    or summary.get("first_divergence")
                    or ""
                ).strip()
            else:
                mechanism = str(summary or "").strip()
        if not mechanism:
            return result

        entry = self._find(related) if related else None
        if entry is None:
            entry = self._new_entry(mechanism, step)
        elif mechanism:
            self._refine_mechanism(entry, mechanism, step=step)
        hypothesis_id = str(entry["hypothesis_id"])

        support = {
            str(value)
            for value in entry.get("support_group_ids", [])
            if str(value)
        }
        support.add(str(group_id))
        entry["support_group_ids"] = sorted(support)
        entry["last_step"] = int(step)
        occurrences = entry.setdefault("occurrences", [])
        key = (int(step), str(group_id))
        existing = {
            (int(row.get("step", -1)), str(row.get("group_id", "")))
            for row in occurrences
            if isinstance(row, dict)
        }
        if key not in existing:
            occurrences.append({
                "step": int(step),
                "group_id": str(group_id),
                "skill_hash": str(skill_hash_value),
            })

        count = len(entry["support_group_ids"])
        result["hypothesis_id"] = hypothesis_id
        result["group_ids"] = list(entry["support_group_ids"])
        result["group_support"] = count
        patch = result.get("patch", {})
        if isinstance(patch, dict):
            for item in get_payload_items(patch, "patch"):
                if isinstance(item, dict):
                    item["memory_refs"] = [{
                        "bank_type": "contrast",
                        "hypothesis_id": hypothesis_id,
                    }]
                    item["hypothesis_id"] = hypothesis_id
                    item["group_support"] = count
                    item["support_count"] = count
                    item.setdefault("source_type", "contrast")
        self.save()
        return result


class FailureMemoryBank(_CompactMemoryBank):
    bank_type = "failure"
    id_prefix = "failure_"

    def integrate(
        self,
        result: dict,
        *,
        group_ids: list[str],
        step: int,
        update_mode: str = "patch",
    ) -> dict:
        hypothesis = result.get("failure_hypothesis")
        if not isinstance(hypothesis, dict):
            hypothesis = {}
        related = str(hypothesis.get("related_hypothesis_id") or "").strip()
        mechanism = str(hypothesis.get("mechanism") or "").strip()
        if not mechanism:
            mechanism = _fallback_failure_mechanism(result)
        if not mechanism:
            return result

        entry = self._find(related) if related else None
        if entry is None:
            entry = self._new_entry(mechanism, step)
        elif mechanism:
            self._refine_mechanism(entry, mechanism, step=step)
        hypothesis_id = str(entry["hypothesis_id"])

        support = {
            str(value)
            for value in entry.get("support_group_ids", [])
            if str(value)
        }
        support.update(str(value) for value in group_ids if str(value))
        entry["support_group_ids"] = sorted(support)
        entry["last_step"] = int(step)
        occurrences = entry.setdefault("occurrences", [])
        for group_id in sorted({str(value) for value in group_ids if str(value)}):
            key = (int(step), group_id)
            existing = {
                (int(row.get("step", -1)), str(row.get("group_id", "")))
                for row in occurrences
                if isinstance(row, dict)
            }
            if key not in existing:
                occurrences.append({"step": int(step), "group_id": group_id})

        count = len(entry["support_group_ids"])
        result["failure_hypothesis_id"] = hypothesis_id
        result["failure_group_support"] = count
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
                if not isinstance(item, dict) or index not in indices:
                    continue
                item["memory_refs"] = [{
                    "bank_type": "failure",
                    "hypothesis_id": hypothesis_id,
                }]
                item["failure_hypothesis_id"] = hypothesis_id
                item["group_support"] = count
                item["support_count"] = count
                item.setdefault("source_type", "failure")
        self.save()
        return result


def reconcile_memory_metadata(
    patch: dict,
    *,
    failure_bank: FailureMemoryBank | None,
    contrast_bank: ContrastMemoryBank | None,
    update_mode: str = "patch",
) -> dict:
    """Validate memory references and recompute support from bank JSON.

    The optimizer may choose and combine edits, but it cannot invent support
    counts.  Unrecognised references are discarded.  Edits without references
    remain ordinary candidates and follow the unchanged ranking/gate path.
    """
    banks = {"failure": failure_bank, "contrast": contrast_bank}
    for item in get_payload_items(patch, update_mode):
        if not isinstance(item, dict):
            continue
        raw_refs = item.get("memory_refs", [])
        if not isinstance(raw_refs, list):
            raw_refs = []
        if not raw_refs:
            # Backward-compatible recovery when a merge kept the stable ID
            # but omitted the structured reference wrapper.
            failure_id = str(
                item.get("failure_hypothesis_id") or ""
            ).strip()
            generic_id = str(item.get("hypothesis_id") or "").strip()
            if failure_id:
                raw_refs.append({
                    "bank_type": "failure",
                    "hypothesis_id": failure_id,
                })
            elif generic_id.startswith("contrast_"):
                raw_refs.append({
                    "bank_type": "contrast",
                    "hypothesis_id": generic_id,
                })
        refs: list[dict[str, str]] = []
        support: set[str] = set()
        seen: set[tuple[str, str]] = set()
        for raw in raw_refs:
            if not isinstance(raw, dict):
                continue
            bank_type = str(raw.get("bank_type") or "").strip()
            hypothesis_id = str(raw.get("hypothesis_id") or "").strip()
            bank = banks.get(bank_type)
            key = (bank_type, hypothesis_id)
            if bank is None or not hypothesis_id or key in seen:
                continue
            ids = bank.support_ids(hypothesis_id)
            if not ids:
                continue
            seen.add(key)
            refs.append({
                "bank_type": bank_type,
                "hypothesis_id": hypothesis_id,
            })
            support.update(ids)
        if not refs:
            item.pop("memory_refs", None)
            if str(item.get("hypothesis_id") or "").startswith(
                ("failure_", "contrast_")
            ):
                item.pop("hypothesis_id", None)
            if str(item.get("failure_hypothesis_id") or "").startswith(
                "failure_"
            ):
                item.pop("failure_hypothesis_id", None)
            continue
        item["memory_refs"] = refs
        item["group_support"] = len(support)
        item["support_count"] = len(support)
        if len(refs) == 1:
            hypothesis_id = refs[0]["hypothesis_id"]
            item["hypothesis_id"] = hypothesis_id
            if refs[0]["bank_type"] == "failure":
                item["failure_hypothesis_id"] = hypothesis_id
        else:
            item["hypothesis_ids"] = [
                ref["hypothesis_id"] for ref in refs
            ]
    return patch
