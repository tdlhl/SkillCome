"""Coverage-first sampling followed by mixed-outcome replay.

This sampler is intentionally isolated from the ordinary SkillOpt dataloader.
It stores every assignment before rollout so interrupted Group-Relative runs
resume with the same task IDs instead of silently drawing a different batch.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Any


def item_id(item: Any) -> str:
    """Return a stable task identifier used by group rollout and resume."""
    if isinstance(item, dict):
        for key in ("id", "task_id", "question_id", "instance_id"):
            if item.get(key) is not None:
                return str(item[key])
    for key in ("id", "task_id", "question_id", "instance_id"):
        value = getattr(item, key, None)
        if value is not None:
            return str(value)
    raise ValueError(f"Training item has no stable ID: {type(item).__name__}")


def _write_json_atomic(path: str, value: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


class CoverageThenMixedSampler:
    """Persistently cover every training item, then replay mixed groups.

    Coverage order is a deterministic global permutation and is *not* reset at
    epoch boundaries. After ``coverage_passes`` full passes, replay batches
    prioritize items that have ever produced both successful and failed valid
    trajectories. Replay counts rotate the mixed pool rather than repeatedly
    choosing the same first few IDs.
    """

    VERSION = 1

    def __init__(
        self,
        *,
        items: list[Any],
        batch_size: int,
        seed: int,
        state_path: str,
        coverage_passes: int = 1,
        coverage_fill_strategy: str = "deterministic",
        coverage_fill_priority_ids: list[str] | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if coverage_passes <= 0:
            raise ValueError("coverage_passes must be positive")

        self.items_by_id: dict[str, Any] = {}
        for item in items:
            task_id = item_id(item)
            if task_id in self.items_by_id:
                raise ValueError(f"Duplicate training item ID: {task_id}")
            self.items_by_id[task_id] = item
        if not self.items_by_id:
            raise ValueError("CoverageThenMixedSampler requires training items")

        self.batch_size = int(batch_size)
        self.seed = int(seed)
        self.state_path = os.path.abspath(state_path)
        self.coverage_passes = int(coverage_passes)
        self.coverage_fill_strategy = str(
            coverage_fill_strategy or "deterministic"
        ).strip().lower()
        if self.coverage_fill_strategy not in {
            "deterministic",
            "mixed_first",
        }:
            raise ValueError(
                "coverage_fill_strategy must be 'deterministic' or "
                f"'mixed_first', got {coverage_fill_strategy!r}"
            )
        self.coverage_fill_priority_ids = [
            str(task_id)
            for task_id in (coverage_fill_priority_ids or [])
            if str(task_id) in self.items_by_id
        ]
        self._pool_ids = list(self.items_by_id)
        self._pool_fingerprint = hashlib.sha256(
            "\n".join(sorted(self._pool_ids)).encode("utf-8")
        ).hexdigest()
        self.state = self._load_or_initialize()

    def _coverage_order(self, pass_index: int) -> list[str]:
        order = list(self._pool_ids)
        random.Random(self.seed + pass_index * 100_003).shuffle(order)
        return order

    def _initial_state(self) -> dict:
        return {
            "version": self.VERSION,
            "seed": self.seed,
            "batch_size": self.batch_size,
            "coverage_passes": self.coverage_passes,
            "coverage_fill_strategy": self.coverage_fill_strategy,
            "coverage_fill_priority_ids": self.coverage_fill_priority_ids,
            "pool_size": len(self._pool_ids),
            "pool_fingerprint": self._pool_fingerprint,
            "coverage_pass_index": 0,
            "coverage_cursor": 0,
            "coverage_order": self._coverage_order(0),
            "assignments": {},
            "completed_assignments": [],
            "item_stats": {},
            "replay_batches": 0,
        }

    def _load_or_initialize(self) -> dict:
        if not os.path.exists(self.state_path):
            state = self._initial_state()
            _write_json_atomic(self.state_path, state)
            return state
        with open(self.state_path, encoding="utf-8") as f:
            state = json.load(f)
        expected = {
            "version": self.VERSION,
            "seed": self.seed,
            "batch_size": self.batch_size,
            "coverage_passes": self.coverage_passes,
            "coverage_fill_strategy": self.coverage_fill_strategy,
            "coverage_fill_priority_ids": self.coverage_fill_priority_ids,
            "pool_size": len(self._pool_ids),
            "pool_fingerprint": self._pool_fingerprint,
        }
        # Sampler states created before mixed-aware coverage filling did not
        # contain these fields.  Treat those states as the original behavior
        # so old experiments remain resumable and reproducible.
        state.setdefault("coverage_fill_strategy", "deterministic")
        state.setdefault("coverage_fill_priority_ids", [])
        mismatches = {
            key: (state.get(key), value)
            for key, value in expected.items()
            if state.get(key) != value
        }
        if mismatches:
            raise ValueError(
                "Group sampler state does not match the current training "
                f"configuration: {mismatches}"
            )
        return state

    def _save(self) -> None:
        _write_json_atomic(self.state_path, self.state)

    @property
    def coverage_complete(self) -> bool:
        return int(self.state["coverage_pass_index"]) >= self.coverage_passes

    def _finish_coverage_pass_if_needed(self) -> None:
        if int(self.state["coverage_cursor"]) < len(self._pool_ids):
            return
        next_pass = int(self.state["coverage_pass_index"]) + 1
        self.state["coverage_pass_index"] = next_pass
        self.state["coverage_cursor"] = 0
        if next_pass < self.coverage_passes:
            self.state["coverage_order"] = self._coverage_order(next_pass)

    @staticmethod
    def _uncertainty(stats: dict) -> float:
        success = int(stats.get("success_rollouts", 0) or 0)
        failure = int(stats.get("failure_rollouts", 0) or 0)
        total = success + failure
        if total <= 0:
            return 0.0
        p = success / total
        return 4.0 * p * (1.0 - p)

    def _replay_ids(self, assignment_key: str) -> tuple[list[str], int]:
        stats_by_id = self.state["item_stats"]
        jitter_rng = random.Random(
            f"{self.seed}:{assignment_key}:{self.state['replay_batches']}"
        )
        jitter = {task_id: jitter_rng.random() for task_id in self._pool_ids}

        mixed_ids = [
            task_id
            for task_id in self._pool_ids
            if stats_by_id.get(task_id, {}).get("ever_mixed") is True
        ]
        mixed_ids.sort(
            key=lambda task_id: (
                int(stats_by_id.get(task_id, {}).get("replay_count", 0) or 0),
                -self._uncertainty(stats_by_id.get(task_id, {})),
                -int(stats_by_id.get(task_id, {}).get("mixed_groups", 0) or 0),
                jitter[task_id],
            )
        )
        selected = mixed_ids[: self.batch_size]
        selected_set = set(selected)
        selected_mixed = len(selected)

        if len(selected) < self.batch_size:
            fallback = [
                task_id for task_id in self._pool_ids if task_id not in selected_set
            ]
            fallback.sort(
                key=lambda task_id: (
                    -int(
                        stats_by_id.get(task_id, {}).get(
                            "all_failure_groups", 0
                        )
                        or 0
                    ),
                    int(
                        stats_by_id.get(task_id, {}).get("replay_count", 0)
                        or 0
                    ),
                    -self._uncertainty(stats_by_id.get(task_id, {})),
                    jitter[task_id],
                )
            )
            selected.extend(fallback[: self.batch_size - len(selected)])

        for task_id in selected:
            stats = stats_by_id.setdefault(task_id, {})
            stats["replay_count"] = int(stats.get("replay_count", 0) or 0) + 1
        self.state["replay_batches"] = int(self.state["replay_batches"]) + 1
        return selected, selected_mixed

    def _coverage_fill_ids(
        self,
        *,
        assignment_key: str,
        order: list[str],
        cursor: int,
        selected: list[str],
    ) -> list[str]:
        """Deterministically fill a partial final coverage batch.

        The new coverage items always come first. Fillers are sampled from
        items already visited in the current coverage pass, so they do not
        advance the coverage cursor or masquerade as newly covered items.
        Persisted assignments make the choice resume-stable.
        """
        missing = self.batch_size - len(selected)
        if missing <= 0:
            return []

        selected_set = set(selected)
        candidates = [
            task_id
            for task_id in order[:cursor]
            if task_id not in selected_set
        ]
        fill_rng = random.Random(
            f"{self.seed}:{assignment_key}:coverage_fill:"
            f"{self.state['coverage_pass_index']}"
        )
        fill_rng.shuffle(candidates)
        if self.coverage_fill_strategy == "deterministic":
            return candidates[:missing]

        stats_by_id = self.state["item_stats"]
        jitter = {task_id: fill_rng.random() for task_id in candidates}
        mixed = [
            task_id
            for task_id in candidates
            if stats_by_id.get(task_id, {}).get("ever_mixed") is True
        ]
        mixed.sort(
            key=lambda task_id: (
                -self._uncertainty(stats_by_id.get(task_id, {})),
                -int(
                    stats_by_id.get(task_id, {}).get("mixed_groups", 0) or 0
                ),
                jitter[task_id],
            )
        )

        selected_fill = mixed[:missing]
        selected_set.update(selected_fill)

        # Optional probe-derived IDs are only a fallback when the current run
        # has not observed enough online mixed groups.  They never displace a
        # mixed group observed under the current model and current skill.
        if len(selected_fill) < missing:
            for task_id in self.coverage_fill_priority_ids:
                if task_id in candidates and task_id not in selected_set:
                    selected_fill.append(task_id)
                    selected_set.add(task_id)
                    if len(selected_fill) >= missing:
                        break

        if len(selected_fill) < missing:
            selected_fill.extend(
                task_id
                for task_id in candidates
                if task_id not in selected_set
            )
        return selected_fill[:missing]

    def select(self, assignment_key: str) -> tuple[list[Any], dict]:
        """Return the stable batch assigned to ``assignment_key``."""
        assignments = self.state["assignments"]
        existing = assignments.get(assignment_key)
        if existing is not None:
            ids = list(existing["item_ids"])
            return [self.items_by_id[task_id] for task_id in ids], dict(existing)

        if not self.coverage_complete:
            cursor = int(self.state["coverage_cursor"])
            order = list(self.state["coverage_order"])
            coverage_ids = order[cursor: cursor + self.batch_size]
            fill_ids = self._coverage_fill_ids(
                assignment_key=assignment_key,
                order=order,
                cursor=cursor,
                selected=coverage_ids,
            )
            selected = coverage_ids + fill_ids
            self.state["coverage_cursor"] = cursor + len(coverage_ids)
            phase = "coverage"
            selected_mixed = sum(
                1
                for task_id in fill_ids
                if self.state["item_stats"].get(task_id, {}).get("ever_mixed")
                is True
            )
            self._finish_coverage_pass_if_needed()
        else:
            selected, selected_mixed = self._replay_ids(assignment_key)
            phase = "mixed_replay"
            coverage_ids = []
            fill_ids = []

        assignment = {
            "assignment_key": assignment_key,
            "phase": phase,
            "item_ids": selected,
            "coverage_item_ids": coverage_ids,
            "fill_item_ids": fill_ids,
            "coverage_new_items": len(coverage_ids),
            "coverage_fill_items": len(fill_ids),
            "coverage_fill_strategy": self.coverage_fill_strategy,
            "selected_mixed_items": selected_mixed,
            "coverage_pass_index": int(self.state["coverage_pass_index"]),
            "coverage_complete_after_selection": self.coverage_complete,
        }
        assignments[assignment_key] = assignment
        self._save()
        return [self.items_by_id[task_id] for task_id in selected], dict(assignment)

    def record_groups(self, assignment_key: str, groups: list[dict]) -> None:
        """Record outcomes once; repeated resume calls are idempotent."""
        completed = set(self.state["completed_assignments"])
        if assignment_key in completed:
            return
        expected = set(
            self.state["assignments"].get(assignment_key, {}).get("item_ids", [])
        )
        observed: set[str] = set()
        for group in groups:
            task_id = str(group.get("group_id"))
            if task_id not in self.items_by_id:
                continue
            observed.add(task_id)
            stats = self.state["item_stats"].setdefault(task_id, {})
            group_type = str(group.get("group_type") or "invalid")
            stats["group_instances"] = int(
                stats.get("group_instances", 0) or 0
            ) + 1
            stats["success_rollouts"] = int(
                stats.get("success_rollouts", 0) or 0
            ) + int(group.get("success_count", 0) or 0)
            stats["failure_rollouts"] = int(
                stats.get("failure_rollouts", 0) or 0
            ) + int(group.get("failure_count", 0) or 0)
            stats["valid_rollouts"] = int(
                stats.get("valid_rollouts", 0) or 0
            ) + int(group.get("valid_rollouts", 0) or 0)
            stats["invalid_rollouts"] = int(
                stats.get("invalid_rollouts", 0) or 0
            ) + int(group.get("invalid_rollouts", 0) or 0)
            count_key = f"{group_type}_groups"
            stats[count_key] = int(stats.get(count_key, 0) or 0) + 1
            if group_type == "mixed":
                stats["ever_mixed"] = True
            stats["last_group_type"] = group_type
            stats["last_assignment_key"] = assignment_key

        assignment = self.state["assignments"].get(assignment_key, {})
        assignment["observed_item_ids"] = sorted(observed)
        assignment["missing_item_ids"] = sorted(expected - observed)
        assignment["completed"] = True
        self.state["completed_assignments"].append(assignment_key)
        self._save()

    def summary(self) -> dict:
        stats = self.state["item_stats"]
        covered = {
            task_id
            for task_id, row in stats.items()
            if int(row.get("group_instances", 0) or 0) > 0
        }
        learned = {
            task_id
            for task_id, row in stats.items()
            if int(row.get("valid_rollouts", 0) or 0) > 0
        }
        mixed = {
            task_id
            for task_id, row in stats.items()
            if row.get("ever_mixed") is True
        }
        replayed = {
            task_id
            for task_id, row in stats.items()
            if int(row.get("replay_count", 0) or 0) > 0
        }
        pool_size = len(self._pool_ids)
        coverage_pass_index = int(self.state["coverage_pass_index"])
        coverage_cursor = int(self.state["coverage_cursor"])
        coverage_items_target = pool_size * self.coverage_passes
        coverage_items_selected = (
            min(coverage_pass_index, self.coverage_passes) * pool_size
        )
        if coverage_pass_index < self.coverage_passes:
            coverage_items_selected += coverage_cursor
        return {
            "sampling_strategy": "coverage_then_mixed",
            "sampling_phase": (
                "mixed_replay" if self.coverage_complete else "coverage"
            ),
            "coverage_passes_target": self.coverage_passes,
            "coverage_fill_strategy": self.coverage_fill_strategy,
            "coverage_passes_selected": coverage_pass_index,
            "coverage_cursor": coverage_cursor,
            "coverage_items_selected": coverage_items_selected,
            "coverage_items_target": coverage_items_target,
            "coverage_complete": self.coverage_complete,
            "attempted_coverage_items": len(covered),
            "learned_coverage_items": len(learned),
            "attempted_coverage_rate": len(covered) / pool_size,
            "learned_coverage_rate": len(learned) / pool_size,
            "ever_mixed_items": len(mixed),
            "mixed_replay_batches": int(self.state["replay_batches"]),
            "mixed_replay_unique_items": len(replayed),
            "assignments": len(self.state["assignments"]),
            "completed_assignments": len(
                self.state["completed_assignments"]
            ),
        }
