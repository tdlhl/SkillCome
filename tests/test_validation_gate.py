from __future__ import annotations

import unittest

from skillopt.config import flatten_config
from skillopt.evaluation.gate import evaluate_gate


class ValidationGateTests(unittest.TestCase):
    def _evaluate(self, candidate_score: float, *, accept_equal: bool):
        return evaluate_gate(
            candidate_skill="candidate",
            cand_hard=candidate_score,
            current_skill="current",
            current_score=0.5,
            best_skill="best",
            best_score=0.6,
            best_step=2,
            global_step=3,
            accept_equal=accept_equal,
        )

    def test_equal_rejects_by_default(self) -> None:
        result = self._evaluate(0.5, accept_equal=False)
        self.assertEqual(result.action, "reject")
        self.assertEqual(result.current_skill, "current")
        self.assertEqual(result.best_skill, "best")

    def test_equal_accepts_current_but_does_not_replace_best(self) -> None:
        result = self._evaluate(0.5, accept_equal=True)
        self.assertEqual(result.action, "accept_flat")
        self.assertEqual(result.current_skill, "candidate")
        self.assertEqual(result.current_score, 0.5)
        self.assertEqual(result.best_skill, "best")
        self.assertEqual(result.best_score, 0.6)
        self.assertEqual(result.best_step, 2)

    def test_strict_improvement_behavior_is_unchanged(self) -> None:
        result = self._evaluate(0.55, accept_equal=True)
        self.assertEqual(result.action, "accept")
        self.assertEqual(result.current_skill, "candidate")
        self.assertEqual(result.best_skill, "best")

    def test_structured_config_flattens_accept_equal(self) -> None:
        flat = flatten_config({
            "evaluation": {"gate_accept_equal": True},
        })
        self.assertIs(flat["gate_accept_equal"], True)
        self.assertNotIn("gate_accept_equal", flatten_config({}))


if __name__ == "__main__":
    unittest.main()
