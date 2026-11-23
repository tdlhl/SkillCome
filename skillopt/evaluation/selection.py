"""Opt-in selection-evaluation helpers.

The default remains one evaluation pass.  Repeated selection evaluation and
external baseline reuse are explicit experiment controls used by the trainer.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from skillopt.utils import compute_score


def read_results_jsonl(path: str) -> list[dict]:
    """Read a non-empty evaluation results JSONL file."""
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"Selection results not found: {resolved}")
    rows: list[dict] = []
    with open(resolved, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in {resolved}:{line_no}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"Expected an object in {resolved}:{line_no}"
                )
            rows.append(row)
    if not rows:
        raise ValueError(f"Selection results are empty: {resolved}")
    return rows


def load_reused_selection_results(
    path: str,
    *,
    expected_items: int,
) -> tuple[list[dict], float, float]:
    """Load and validate a previously materialized selection evaluation."""
    rows = read_results_jsonl(path)
    if len(rows) != expected_items:
        raise ValueError(
            "Reused selection baseline has the wrong number of rows: "
            f"expected {expected_items}, found {len(rows)} in {path}"
        )
    hard, soft = compute_score(rows)
    return rows, hard, soft


def evaluate_selection_repeats(
    *,
    adapter: Any,
    build_env: Callable[[], tuple[Any, int]],
    skill_content: str,
    out_dir: str,
    repeats: int = 1,
) -> tuple[float, float, dict]:
    """Evaluate a skill one or more times and score all rows jointly.

    For ``repeats=1`` the historical output layout is preserved.  Opt-in
    repeated runs are stored below ``repeat_01``, ``repeat_02``, ... so every
    pass remains independently resumable and auditable.
    """
    repeats = int(repeats)
    if repeats < 1:
        raise ValueError("selection_eval_repeats must be >= 1")

    os.makedirs(out_dir, exist_ok=True)
    all_results: list[dict] = []
    repeat_summaries: list[dict] = []
    expected_items: int | None = None

    for repeat_index in range(1, repeats + 1):
        env, item_count = build_env()
        if expected_items is None:
            expected_items = int(item_count)
        elif int(item_count) != expected_items:
            raise ValueError(
                "Selection environment size changed between repeats: "
                f"{expected_items} -> {item_count}"
            )
        repeat_dir = (
            out_dir
            if repeats == 1
            else os.path.join(out_dir, f"repeat_{repeat_index:02d}")
        )
        results = adapter.rollout(env, skill_content, repeat_dir)
        if len(results) != int(item_count):
            raise ValueError(
                "Selection evaluation returned the wrong number of rows: "
                f"repeat={repeat_index}, expected={item_count}, "
                f"found={len(results)}, out_dir={repeat_dir}"
            )
        hard, soft = compute_score(results)
        all_results.extend(results)
        repeat_summaries.append({
            "repeat": repeat_index,
            "expected_items": int(item_count),
            "result_rows": len(results),
            "hard": hard,
            "soft": soft,
            "out_dir": repeat_dir,
        })

    aggregate_hard, aggregate_soft = compute_score(all_results)
    summary = {
        "selection_eval_repeats": repeats,
        "expected_items_per_repeat": expected_items,
        "total_result_rows": len(all_results),
        "aggregate_hard": aggregate_hard,
        "aggregate_soft": aggregate_soft,
        "repeats": repeat_summaries,
    }
    with open(
        os.path.join(out_dir, "selection_summary.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return aggregate_hard, aggregate_soft, summary


def record_reused_selection_baseline(
    *,
    out_dir: str,
    source_path: str,
    result_rows: int,
    hard: float,
    soft: float,
) -> None:
    """Persist provenance without copying or modifying the source results."""
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "mode": "reused_external_results",
        "source_results": os.path.abspath(os.path.expanduser(source_path)),
        "result_rows": int(result_rows),
        "hard": float(hard),
        "soft": float(soft),
    }
    with open(
        os.path.join(out_dir, "selection_summary.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
