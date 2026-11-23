from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skillopt.config import flatten_config, load_config
from skillopt.engine.trainer import (
    DEFAULT_BOUNDED_SLOW_UPDATE,
    DEFAULT_PROTECTED_EDIT_RELOCATION,
)
from skillopt.group_relative.reflect import run_group_contrast_reflect
from skillopt.group_relative.rollout import (
    classify_rollout_validity,
    run_grouped_rollout,
    select_ordinary_reflect_results,
)
from skillopt.group_relative.sampler import CoverageThenMixedSampler
from skillopt.group_relative.stats import write_global_group_summary
from skillopt.gradient.aggregate import merge_patches
from skillopt.optimizer.clip import rank_and_select


class _FakeAdapter:
    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        repeat_idx = int(Path(out_dir).name.split("_")[-1])
        rows = []
        for item in env_manager:
            task_id = str(item["id"])
            prediction_dir = Path(out_dir) / "predictions" / task_id
            prediction_dir.mkdir(parents=True, exist_ok=True)
            (prediction_dir / "conversation.json").write_text(
                json.dumps(
                    [
                        {
                            "role": "assistant",
                            "content": f"task={task_id} repeat={repeat_idx}",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            if task_id == "mixed":
                hard = int(repeat_idx % 2 == 0)
                rows.append(
                    {
                        "id": task_id,
                        "hard": hard,
                        "soft": float(hard),
                        "task_description": "same task",
                        "agent_ok": True,
                    }
                )
            elif task_id == "invalid" and repeat_idx == 0:
                rows.append(
                    {
                        "id": task_id,
                        "hard": 0,
                        "soft": 0.0,
                        "agent_ok": False,
                        "fail_reason": "429 Too Many Requests",
                    }
                )
            else:
                rows.append(
                    {
                        "id": task_id,
                        "hard": 1,
                        "soft": 1.0,
                        "agent_ok": True,
                    }
                )
        return rows


class _RetryAdapter:
    def __init__(self) -> None:
        self.exec_timeout = 600
        self.calls: list[tuple[str, list[str], int]] = []

    def rollout(
        self,
        env_manager,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        kind = Path(out_dir).name
        task_ids = [str(item["id"]) for item in env_manager]
        self.calls.append((kind, task_ids, self.exec_timeout))
        rows = []
        for task_id in task_ids:
            prediction_dir = Path(out_dir) / "predictions" / task_id
            prediction_dir.mkdir(parents=True, exist_ok=True)
            (prediction_dir / "conversation.json").write_text(
                "[]",
                encoding="utf-8",
            )
            if task_id == "needs_retry" and kind.startswith("repeat_"):
                rows.append(
                    {
                        "id": task_id,
                        "hard": 0,
                        "agent_ok": False,
                        "fail_reason": "task-timeout-600s",
                    }
                )
            else:
                rows.append(
                    {
                        "id": task_id,
                        "hard": 1,
                        "agent_ok": True,
                    }
                )
        return rows


class GroupRelativeRolloutTests(unittest.TestCase):
    def test_ordinary_reflect_selects_one_per_group_and_mixed_failure(self) -> None:
        rows = []
        for group_id, outcomes in {
            "mixed": [1, 1, 0, 0],
            "success": [1, 1, 1, 1],
            "failure": [0, 0, 0, 0],
        }.items():
            for repeat_idx, hard in enumerate(outcomes):
                rows.append(
                    {
                        "id": group_id,
                        "group_id": group_id,
                        "rollout_id": f"{group_id}::r{repeat_idx:02d}",
                        "repeat_idx": repeat_idx,
                        "valid_for_learning": True,
                        "hard": hard,
                    }
                )
        groups = [
            {"group_id": "mixed", "group_type": "mixed"},
            {"group_id": "success", "group_type": "all_success"},
            {"group_id": "failure", "group_type": "all_failure"},
            {"group_id": "invalid", "group_type": "invalid"},
        ]

        selected, manifest = select_ordinary_reflect_results(
            valid_results=rows,
            groups=groups,
            seed=42,
        )
        selected_again, manifest_again = select_ordinary_reflect_results(
            valid_results=rows,
            groups=groups,
            seed=42,
        )

        self.assertEqual(3, len(selected))
        self.assertEqual(
            {"mixed", "success", "failure"},
            {row["group_id"] for row in selected},
        )
        mixed = next(row for row in selected if row["group_id"] == "mixed")
        self.assertEqual(0, mixed["hard"])
        self.assertEqual(
            "mixed_random_failure",
            mixed["ordinary_reflect_selection_policy"],
        )
        self.assertEqual(
            [row["rollout_id"] for row in selected],
            [row["rollout_id"] for row in selected_again],
        )
        self.assertEqual(manifest, manifest_again)
        invalid = next(row for row in manifest if row["group_id"] == "invalid")
        self.assertIsNone(invalid["selected_rollout_id"])

    def test_infrastructure_failures_are_invalid(self) -> None:
        self.assertFalse(
            classify_rollout_validity(
                {"hard": 0, "agent_ok": False, "fail_reason": "429"}
            )[0]
        )
        self.assertFalse(
            classify_rollout_validity(
                {"hard": 0, "llm_ok": False, "fail_reason": ""}
            )[0]
        )
        self.assertFalse(
            classify_rollout_validity(
                {"hard": 0, "phase": "timeout", "fail_reason": ""}
            )[0]
        )
        self.assertTrue(
            classify_rollout_validity(
                {
                    "hard": 0,
                    "agent_ok": True,
                    "fail_reason": "answer mismatch",
                }
            )[0]
        )

    def test_agent_behavior_failures_can_be_valid_learning_evidence(self) -> None:
        behavior_failure = {
            "hard": 0,
            "agent_ok": False,
            "fail_reason": "Model neither produced a tool request nor a final answer",
        }
        self.assertFalse(classify_rollout_validity(behavior_failure)[0])
        self.assertTrue(
            classify_rollout_validity(
                behavior_failure,
                agent_failures_are_valid_evidence=True,
            )[0]
        )

        infrastructure_failure = {
            "hard": 0,
            "agent_ok": False,
            "fail_reason": (
                "error: LLM message call failed after 5 retries: "
                "Connection error."
            ),
        }
        self.assertFalse(
            classify_rollout_validity(
                infrastructure_failure,
                agent_failures_are_valid_evidence=True,
            )[0]
        )

    def test_grouped_rollout_threads_agent_failure_evidence_switch(self) -> None:
        class _BehaviorFailureAdapter:
            def rollout(self, env_manager, skill_content, out_dir, **kwargs):
                return [
                    {
                        "id": str(item["id"]),
                        "hard": 0,
                        "agent_ok": False,
                        "fail_reason": (
                            "Model neither produced a tool request nor a "
                            "final answer"
                        ),
                    }
                    for item in env_manager
                ]

        with tempfile.TemporaryDirectory() as tmp:
            grouped = run_grouped_rollout(
                adapter=_BehaviorFailureAdapter(),
                env_manager=[{"id": "behavior_failure"}],
                skill_content="skill",
                out_dir=tmp,
                rollouts_per_item=2,
                min_valid_rollouts=1,
                max_extra_attempts_per_item=0,
                agent_failures_are_valid_evidence=True,
            )

        self.assertEqual(2, len(grouped.valid_results))
        self.assertEqual(0, grouped.stats["invalid_rollouts"])
        self.assertTrue(
            grouped.stats["agent_failures_are_valid_evidence"]
        )
        self.assertEqual("all_failure", grouped.groups[0]["group_type"])

    def test_global_group_summary_tracks_coverage_and_group_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for step, groups in {
                "step_0001": [
                    {
                        "group_id": "a",
                        "requested_rollouts": 8,
                        "valid_rollouts": 7,
                        "invalid_rollouts": 1,
                        "group_type": "mixed",
                    },
                    {
                        "group_id": "b",
                        "requested_rollouts": 8,
                        "valid_rollouts": 8,
                        "invalid_rollouts": 0,
                        "group_type": "all_success",
                    },
                ],
                "step_0002": [
                    {
                        "group_id": "a",
                        "requested_rollouts": 8,
                        "valid_rollouts": 8,
                        "invalid_rollouts": 0,
                        "group_type": "all_failure",
                    }
                ],
            }.items():
                rollout_dir = os.path.join(tmp, "steps", step, "rollout")
                os.makedirs(rollout_dir)
                with open(
                    os.path.join(rollout_dir, "groups.json"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    json.dump(groups, f)

            summary = write_global_group_summary(tmp, train_pool_size=80)

            self.assertEqual(summary["unique_items"], 2)
            self.assertEqual(summary["covered_train_items"], 2)
            self.assertEqual(summary["group_instances"], 3)
            self.assertEqual(summary["requested_rollouts"], 24)
            self.assertEqual(summary["valid_rollouts"], 23)
            self.assertEqual(summary["invalid_rollouts"], 1)
            self.assertEqual(summary["materialized_rollouts"], 24)
            self.assertEqual(summary["mixed_groups"], 1)
            self.assertEqual(summary["all_success_groups"], 1)
            self.assertEqual(summary["all_failure_groups"], 1)
            self.assertEqual(summary["mixed_group_rate"], 1 / 3)
            self.assertEqual(summary["train_coverage_rate"], 2 / 80)

    def test_repeats_are_isolated_and_grouped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            grouped = run_grouped_rollout(
                adapter=_FakeAdapter(),
                env_manager=[
                    {"id": "mixed"},
                    {"id": "success"},
                    {"id": "invalid"},
                ],
                skill_content="skill",
                out_dir=tmp,
                rollouts_per_item=4,
                min_valid_rollouts=1,
                max_extra_attempts_per_item=0,
            )
            self.assertEqual(12, len(grouped.all_results))
            self.assertEqual(11, len(grouped.valid_results))
            by_id = {group["group_id"]: group for group in grouped.groups}
            self.assertEqual("mixed", by_id["mixed"]["group_type"])
            self.assertEqual("all_success", by_id["success"]["group_type"])
            self.assertEqual(3, by_id["invalid"]["valid_rollouts"])
            self.assertEqual("all_success", by_id["invalid"]["group_type"])
            self.assertTrue(
                Path(
                    next(
                        row["trajectory_dir"]
                        for row in grouped.all_results
                        if row["rollout_id"] == "mixed::r03"
                    )
                ).joinpath("conversation.json").exists()
            )
            self.assertTrue(Path(tmp, "groups.json").exists())
            self.assertTrue(Path(tmp, "group_stats.json").exists())

    def test_invalid_rollouts_are_retried_with_retry_only_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = _RetryAdapter()
            grouped = run_grouped_rollout(
                adapter=adapter,
                env_manager=[
                    {"id": "needs_retry"},
                    {"id": "already_valid"},
                ],
                skill_content="skill",
                out_dir=tmp,
                rollouts_per_item=2,
                min_valid_rollouts=2,
                max_extra_attempts_per_item=2,
                retry_timeout=900,
            )

            retry_calls = [call for call in adapter.calls if call[0].startswith("retry_")]
            self.assertEqual(2, len(retry_calls))
            self.assertTrue(
                all(ids == ["needs_retry"] for _, ids, _ in retry_calls)
            )
            self.assertTrue(all(timeout == 900 for _, _, timeout in retry_calls))
            self.assertEqual(600, adapter.exec_timeout)

            by_id = {group["group_id"]: group for group in grouped.groups}
            self.assertEqual(2, by_id["needs_retry"]["extra_attempts"])
            self.assertEqual(2, by_id["needs_retry"]["valid_rollouts"])
            self.assertTrue(by_id["needs_retry"]["meets_min_valid_rollouts"])
            self.assertEqual(0, by_id["already_valid"]["extra_attempts"])
            self.assertEqual(2, grouped.stats["extra_attempts"])
            self.assertEqual(6, grouped.stats["requested_rollouts"])


class GroupRelativeReflectTests(unittest.TestCase):
    def test_only_mixed_groups_receive_contrast_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = []
            for repeat_idx, hard in enumerate((1, 0, 1, 0)):
                attempt_dir = (
                    "retry_00" if repeat_idx == 3 else f"repeat_{repeat_idx:02d}"
                )
                trajectory_dir = root / attempt_dir / "predictions" / "x"
                trajectory_dir.mkdir(parents=True)
                (trajectory_dir / "conversation.json").write_text(
                    json.dumps(
                        [{"role": "assistant", "content": f"attempt {repeat_idx}"}]
                    ),
                    encoding="utf-8",
                )
                results.append(
                    {
                        "id": "x",
                        "group_id": "x",
                        "rollout_id": f"x::r{repeat_idx:02d}",
                        "repeat_idx": repeat_idx,
                        "hard": hard,
                        "valid_for_learning": True,
                        "trajectory_dir": str(trajectory_dir),
                    }
                )
            groups = [
                {"group_id": "x", "group_type": "mixed"},
                {"group_id": "y", "group_type": "all_failure"},
            ]

            response = json.dumps(
                {
                    "contrast_summary": {
                        "first_divergence": "step 1",
                        "success_behavior": "checked",
                        "failure_behavior": "guessed",
                        "causal_hypothesis": "missing check",
                        "generalizable": True,
                        "confidence": 0.9,
                    },
                    "patch": {
                        "reasoning": "contrast",
                        "edits": [{"op": "append", "content": "Verify first."}],
                    },
                }
            )
            with patch(
                "skillopt.group_relative.reflect.chat_optimizer",
                return_value=(response, {}),
            ) as mocked:
                patches = run_group_contrast_reflect(
                    results=results,
                    groups=groups,
                    skill_content="skill",
                    patches_dir=str(root / "patches"),
                    edit_budget=4,
                )
            self.assertEqual(1, mocked.call_count)
            self.assertEqual(1, len(patches))
            self.assertEqual("contrast", patches[0]["source_type"])
            edit = patches[0]["patch"]["edits"][0]
            self.assertEqual(1, edit["group_support"])
            self.assertEqual(4, edit["trajectory_support"])


class GroupRelativeConfigTests(unittest.TestCase):
    def test_group_method_config_is_self_contained(self) -> None:
        config = flatten_config(
            load_config(
                "configs/group_method/"
                "spreadsheetbench.yaml"
            )
        )
        self.assertTrue(config["group_relative_enabled"])
        self.assertTrue(config["group_dual_memory_enabled"])
        self.assertNotIn("repair_slow_target_edits", config)
        self.assertNotIn("bounded_slow_update", config)
        self.assertTrue(DEFAULT_PROTECTED_EDIT_RELOCATION)
        self.assertFalse(DEFAULT_BOUNDED_SLOW_UPDATE)
        self.assertEqual(40, config["train_size"])
        self.assertEqual(10, config["batch_size"])
        self.assertEqual(8, config["rollouts_per_item"])
        self.assertEqual(80, config["rollout_budget_per_step"])
        self.assertEqual(4, config["num_epochs"])
        self.assertEqual(0, config["mixed_replay_steps"])
        self.assertTrue(config["deduplicate_ordinary_reflect"])
        self.assertEqual("data/spreadsheetbench/split", config["split_dir"])


class GroupRelativeSamplerTests(unittest.TestCase):
    def test_partial_coverage_batch_prefers_observed_mixed_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            items = [{"id": f"task-{idx}"} for idx in range(6)]
            sampler = CoverageThenMixedSampler(
                items=items,
                batch_size=5,
                seed=42,
                state_path=os.path.join(tmp, "sampler.json"),
                coverage_passes=1,
                coverage_fill_strategy="mixed_first",
                coverage_fill_priority_ids=["task-0"],
            )
            first, _ = sampler.select("step_0001:batch_00")
            mixed_id = first[0]["id"]
            sampler.record_groups(
                "step_0001:batch_00",
                [
                    {
                        "group_id": row["id"],
                        "group_type": (
                            "mixed" if row["id"] == mixed_id else "all_success"
                        ),
                        "success_count": 4,
                        "failure_count": 4 if row["id"] == mixed_id else 0,
                        "valid_rollouts": 8,
                        "invalid_rollouts": 0,
                    }
                    for row in first
                ],
            )

            second, assignment = sampler.select("step_0002:batch_00")
            self.assertEqual(1, assignment["coverage_new_items"])
            self.assertEqual(4, assignment["coverage_fill_items"])
            self.assertEqual("mixed_first", assignment["coverage_fill_strategy"])
            self.assertIn(mixed_id, assignment["fill_item_ids"])
            self.assertEqual(1, assignment["selected_mixed_items"])
            self.assertEqual(5, len(second))

    def test_global_coverage_then_mixed_replay_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            items = [{"id": f"task-{idx}"} for idx in range(12)]
            state_path = os.path.join(tmp, "sampler.json")
            sampler = CoverageThenMixedSampler(
                items=items,
                batch_size=5,
                seed=42,
                state_path=state_path,
                coverage_passes=1,
            )
            covered: list[str] = []
            mixed_id = ""
            for round_idx in range(1, 4):
                selected, assignment = sampler.select(
                    f"round_{round_idx:04d}"
                )
                ids = [row["id"] for row in selected]
                self.assertEqual("coverage", assignment["phase"])
                self.assertEqual(5, len(ids))
                coverage_ids = assignment["coverage_item_ids"]
                self.assertFalse(set(covered).intersection(coverage_ids))
                covered.extend(coverage_ids)
                self.assertEqual(
                    len(coverage_ids), assignment["coverage_new_items"]
                )
                self.assertEqual(
                    len(assignment["fill_item_ids"]),
                    assignment["coverage_fill_items"],
                )
                if round_idx == 1:
                    mixed_id = ids[0]
                groups = []
                for task_id in ids:
                    is_mixed = task_id == mixed_id
                    groups.append(
                        {
                            "group_id": task_id,
                            "group_type": (
                                "mixed" if is_mixed else "all_success"
                            ),
                            "success_count": 4,
                            "failure_count": 4 if is_mixed else 0,
                            "valid_rollouts": 8,
                            "invalid_rollouts": 0,
                        }
                    )
                sampler.record_groups(
                    f"round_{round_idx:04d}", groups
                )

            self.assertEqual(12, len(set(covered)))
            self.assertEqual(2, assignment["coverage_new_items"])
            self.assertEqual(3, assignment["coverage_fill_items"])
            self.assertTrue(sampler.coverage_complete)
            replay, replay_assignment = sampler.select("round_0004")
            self.assertEqual("mixed_replay", replay_assignment["phase"])
            self.assertIn(mixed_id, [row["id"] for row in replay])

            resumed = CoverageThenMixedSampler(
                items=items,
                batch_size=5,
                seed=42,
                state_path=state_path,
                coverage_passes=1,
            )
            replay_again, assignment_again = resumed.select("round_0004")
            self.assertEqual(
                [row["id"] for row in replay],
                [row["id"] for row in replay_again],
            )
            self.assertEqual(replay_assignment, assignment_again)


class GroupRelativeAggregateTests(unittest.TestCase):
    def test_group_relative_merge_includes_all_three_sources(self) -> None:
        failure = {
            "reasoning": "failure",
            "edits": [
                {
                    "op": "append",
                    "content": "failure",
                    "source_type": "failure",
                    "group_support": 2,
                }
            ],
        }
        contrast = {
            "reasoning": "contrast",
            "edits": [
                {
                    "op": "append",
                    "content": "contrast",
                    "source_type": "contrast",
                    "group_support": 1,
                }
            ],
        }
        success = {
            "reasoning": "success",
            "edits": [
                {
                    "op": "append",
                    "content": "success",
                    "source_type": "success",
                    "group_support": 1,
                }
            ],
        }
        merged_response = json.dumps(
            {
                "reasoning": "failure then contrast then success",
                "edits": failure["edits"] + contrast["edits"] + success["edits"],
            }
        )
        with patch(
            "skillopt.gradient.aggregate.chat_optimizer",
            return_value=(merged_response, {}),
        ) as mocked:
            merged = merge_patches(
                "skill",
                [failure],
                [success],
                contrast_patches=[contrast],
                group_relative=True,
                verbose=False,
            )
        self.assertEqual(1, mocked.call_count)
        self.assertEqual(
            ["failure", "contrast", "success"],
            [item["source_type"] for item in merged["edits"]],
        )

    def test_original_failure_only_merge_is_unchanged(self) -> None:
        failure = {
            "reasoning": "failure",
            "edits": [{"op": "append", "content": "failure"}],
        }
        with patch("skillopt.gradient.aggregate.chat_optimizer") as mocked:
            merged = merge_patches("skill", [failure], [], verbose=False)
        mocked.assert_not_called()
        self.assertIs(merged, failure)

    def test_final_merge_preserves_contrast_when_optimizer_omits_it(self) -> None:
        failure = {
            "reasoning": "failure",
            "edits": [
                {
                    "op": "append",
                    "content": "failure",
                    "source_type": "failure",
                }
            ],
        }
        contrast = {
            "reasoning": "contrast",
            "edits": [
                {
                    "op": "append",
                    "content": "contrast",
                    "source_type": "contrast",
                    "group_support": 1,
                    "trajectory_support": 8,
                }
            ],
        }
        success = {
            "reasoning": "success",
            "edits": [
                {
                    "op": "append",
                    "content": "success",
                    "source_type": "success",
                }
            ],
        }
        responses = [
            (
                json.dumps(
                    {
                        "reasoning": "drop contrast",
                        "edits": failure["edits"] + success["edits"],
                    }
                ),
                {},
            )
        ]
        with patch(
            "skillopt.gradient.aggregate.chat_optimizer",
            side_effect=responses,
        ):
            merged = merge_patches(
                "skill",
                [failure],
                [success],
                contrast_patches=[contrast],
                group_relative=True,
                ensure_contrast_candidate=True,
                verbose=False,
            )
        self.assertIn(
            "contrast",
            [item.get("source_type") for item in merged["edits"]],
        )
        self.assertTrue(merged["contrast_candidate_preserved"])

    def test_ranking_reserves_one_contrast_slot(self) -> None:
        pool = {
            "reasoning": "pool",
            "edits": [
                {"op": "append", "content": "failure", "source_type": "failure"},
                {
                    "op": "append",
                    "content": "contrast",
                    "source_type": "contrast",
                    "group_support": 1,
                    "trajectory_support": 8,
                },
                {"op": "append", "content": "success", "source_type": "success"},
            ],
        }
        response = json.dumps(
            {"reasoning": "ordinary ranking", "selected_indices": [0, 2]}
        )
        with patch(
            "skillopt.optimizer.clip.chat_optimizer",
            return_value=(response, {}),
        ):
            ranked = rank_and_select(
                "skill",
                pool,
                max_edits=2,
                group_relative=True,
                ensure_contrast_candidate=True,
            )
        self.assertEqual(
            ["failure", "contrast"],
            [item["source_type"] for item in ranked["edits"]],
        )
        self.assertTrue(
            ranked["ranking_details"]["contrast_candidate_forced"]
        )


if __name__ == "__main__":
    unittest.main()
