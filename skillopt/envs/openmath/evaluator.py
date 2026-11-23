"""Answer extraction and reference-guided judging for open-ended mathematics."""
from __future__ import annotations

import json
import re
from typing import Any

from skillopt.model import chat_optimizer


def extract_answer(text: str) -> str:
    matches = re.findall(r"<answer>(.*?)</answer>", str(text), re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[-1].strip()
    boxed = re.findall(r"\\boxed\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", str(text), re.DOTALL)
    if boxed:
        return boxed[-1].strip()
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return lines[-1] if lines else str(text).strip()


def normalize_answer(text: str) -> str:
    value = extract_answer(text)
    value = value.replace("\\left", "").replace("\\right", "")
    value = value.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
    value = value.replace("−", "-").replace("–", "-")
    value = re.sub(r"\\(?:,|;|!|quad|qquad)", "", value)
    value = re.sub(r"\s+", "", value)
    value = value.strip("$.")
    return value.casefold()


def _parse_judge(text: str) -> tuple[bool | None, str]:
    raw = str(text or "").strip()
    candidates = [raw]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict) and isinstance(value.get("equivalent"), bool):
            return value["equivalent"], str(value.get("reason") or "").strip()
    match = re.search(r'"equivalent"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if match:
        return match.group(1).lower() == "true", raw[:500]
    return None, raw[:500]


def evaluate(
    *,
    question: str,
    response: str,
    reference_answers: list[str],
    reference_solutions: list[str],
    use_llm_judge: bool,
    judge_max_completion_tokens: int,
    judge_timeout: int,
) -> dict[str, Any]:
    predicted = extract_answer(response)
    normalized_predicted = normalize_answer(predicted)
    for reference in reference_answers:
        if normalized_predicted and normalized_predicted == normalize_answer(reference):
            return {
                "hard": 1,
                "soft": 1.0,
                "predicted_answer": predicted,
                "judge_mode": "normalized_exact",
                "judge_ok": True,
                "judge_reason": "Normalized final answer matches an official reference answer.",
                "judge_response": "",
                "judge_call_info": {},
            }

    if not use_llm_judge:
        return {
            "hard": 0,
            "soft": 0.0,
            "predicted_answer": predicted,
            "judge_mode": "normalized_exact",
            "judge_ok": True,
            "judge_reason": "Normalized final answer does not exactly match the reference.",
            "judge_response": "",
            "judge_call_info": {},
        }

    system = (
        "You are a strict mathematical answer-equivalence judge. Determine whether the "
        "student response gives the same final answer as the reference. Allow algebraically "
        "equivalent forms, reordered tuples/sets only when order is mathematically irrelevant, "
        "and harmless formatting differences. Do not reward merely similar reasoning. Return "
        "only JSON: {\"equivalent\": true|false, \"reason\": \"short reason\"}."
    )
    reference_solution = "\n\n".join(reference_solutions[:1])
    user = (
        f"## Problem\n{question}\n\n"
        f"## Official final answer\n{json.dumps(reference_answers, ensure_ascii=False)}\n\n"
        f"## Official solution (judge-only evidence)\n{reference_solution}\n\n"
        f"## Student response\n{response}"
    )
    try:
        judge_response, call_info = chat_optimizer(
            system=system,
            user=user,
            max_completion_tokens=judge_max_completion_tokens,
            retries=5,
            stage="math_answer_judge",
            reasoning_effort="low",
            timeout=judge_timeout,
        )
        compact_call_info = dict(call_info or {})
        compact_call_info.pop("reasoning_content", None)
        equivalent, reason = _parse_judge(judge_response)
        if equivalent is None:
            return {
                "hard": 0,
                "soft": 0.0,
                "predicted_answer": predicted,
                "judge_mode": "llm",
                "judge_ok": False,
                "judge_reason": "Judge returned an unparseable response.",
                "judge_response": judge_response,
                "judge_call_info": compact_call_info,
            }
        return {
            "hard": int(equivalent),
            "soft": float(equivalent),
            "predicted_answer": predicted,
            "judge_mode": "llm",
            "judge_ok": True,
            "judge_reason": reason,
            "judge_response": judge_response,
            "judge_call_info": compact_call_info,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "hard": 0,
            "soft": 0.0,
            "predicted_answer": predicted,
            "judge_mode": "llm",
            "judge_ok": False,
            "judge_reason": f"Judge error: {type(exc).__name__}: {exc}",
            "judge_response": "",
            "judge_call_info": {},
        }
