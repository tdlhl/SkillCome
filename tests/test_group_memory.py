from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from skillopt.group_relative.memory import (
    ContrastMemoryBank,
    FailureMemoryBank,
    MAX_DISPLAY_MECHANISM_CHARS,
    MAX_ENTRIES_PER_BANK,
    PER_BANK_CONTEXT_CHAR_BUDGET,
    reconcile_memory_metadata,
)


class GroupMemoryTests(unittest.TestCase):
    def test_failure_bank_stores_full_text_and_renders_compact_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "failure.json"
            bank = FailureMemoryBank(str(path))
            mechanism = "x" * 900
            result = {
                "failure_hypothesis": {
                    "related_hypothesis_id": "",
                    "mechanism": mechanism,
                    "edit_indices": [0],
                },
                "patch": {"edits": [{"content": "fix"}]},
            }
            bank.integrate(result, group_ids=["a", "b"], step=1)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(mechanism, stored["hypotheses"][0]["mechanism"])
            context = bank.render_context()
            self.assertIn("x" * MAX_DISPLAY_MECHANISM_CHARS, context)
            self.assertNotIn("x" * (MAX_DISPLAY_MECHANISM_CHARS + 1), context)
            self.assertIn('"support_count": 2', context)
            self.assertNotIn('"support_group_ids"', context)
            self.assertNotIn('"a"', context)
            self.assertLessEqual(len(context), PER_BANK_CONTEXT_CHAR_BUDGET)

    def test_top_eight_and_related_refinement_are_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contrast.json"
            bank = ContrastMemoryBank(str(path))
            first_id = ""
            for index in range(10):
                result = {
                    "hypothesis": {
                        "related_hypothesis_id": "",
                        "mechanism": f"mechanism-{index}",
                    },
                    "patch": {"edits": [{"content": f"edit-{index}"}]},
                }
                bank.integrate(
                    result,
                    group_id=f"g-{index}",
                    step=index + 1,
                    skill_hash_value="skill",
                )
                if index == 0:
                    first_id = result["hypothesis_id"]
            refined = {
                "hypothesis": {
                    "related_hypothesis_id": first_id,
                    "mechanism": "refined mechanism",
                },
                "patch": {"edits": [{"content": "refined edit"}]},
            }
            bank.integrate(
                refined,
                group_id="g-new",
                step=11,
                skill_hash_value="skill-2",
            )
            stored = json.loads(path.read_text(encoding="utf-8"))
            entry = next(
                item for item in stored["hypotheses"]
                if item["hypothesis_id"] == first_id
            )
            self.assertEqual("refined mechanism", entry["mechanism"])
            self.assertEqual("mechanism-0", entry["mechanism_history"][0]["mechanism"])
            self.assertLessEqual(
                len(json.loads(bank.render_context())),
                MAX_ENTRIES_PER_BANK,
            )

    def test_support_is_recomputed_from_banks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failure = FailureMemoryBank(str(Path(tmp) / "failure.json"))
            contrast = ContrastMemoryBank(str(Path(tmp) / "contrast.json"))
            raw = {
                "failure_hypothesis": {
                    "mechanism": "recurring failure",
                    "edit_indices": [0],
                },
                "patch": {"edits": [{"content": "fix"}]},
            }
            failure.integrate(raw, group_ids=["a", "b"], step=1)
            hypothesis_id = raw["failure_hypothesis_id"]
            merged = {"edits": [{
                "content": "merged fix",
                "memory_refs": [{
                    "bank_type": "failure",
                    "hypothesis_id": hypothesis_id,
                }],
                "group_support": 999,
                "support_count": 999,
            }]}
            reconcile_memory_metadata(
                merged,
                failure_bank=failure,
                contrast_bank=contrast,
            )
            self.assertEqual(2, merged["edits"][0]["group_support"])
            self.assertEqual(2, merged["edits"][0]["support_count"])

    def test_compact_banks_are_separate_from_basic_banks(self) -> None:
        from skillopt.group_relative.failure_hypothesis_bank import (
            FailureHypothesisBank,
        )
        from skillopt.group_relative.hypothesis_bank import ContrastHypothesisBank

        self.assertIsNot(FailureMemoryBank, FailureHypothesisBank)
        self.assertIsNot(ContrastMemoryBank, ContrastHypothesisBank)


if __name__ == "__main__":
    unittest.main()
