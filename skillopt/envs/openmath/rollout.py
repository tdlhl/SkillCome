"""Direct-API rollout for open-ended Olympiad mathematics."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from skillopt.envs.openmath.evaluator import evaluate
from skillopt.model import chat_target
from skillopt.prompts import load_prompt


def _build_system(skill_content: str) -> str:
    skill_section = f"## Skill\n{skill_content.strip()}\n\n" if skill_content.strip() else ""
    return load_prompt("rollout_system", env="openmath").format(skill_section=skill_section)


def process_one(
    item: dict,
    out_root: str,
    skill_content: str,
    *,
    exec_timeout: int,
    max_completion_tokens: int,
    use_llm_judge: bool,
    judge_max_completion_tokens: int,
    judge_timeout: int,
) -> dict:
    item_id = str(item["id"])
    result = {
        "id": item_id,
        "question": item["question"],
        "task_type": str(item.get("task_type") or "open_math"),
        "hard": 0,
        "soft": 0.0,
        "predicted_answer": "",
        "correct_text": " ; ".join(item.get("reference_answer") or []),
        "response": "",
        "fail_reason": "",
        "agent_ok": False,
        "llm_ok": False,
        "judge_ok": False,
        "n_turns": 0,
    }
    pred_dir = os.path.join(out_root, "predictions", item_id.replace(":", "_"))
    os.makedirs(pred_dir, exist_ok=True)
    system = _build_system(skill_content)
    user = f"## Problem\n{item['question']}"
    try:
        response, call_info = chat_target(
            system=system,
            user=user,
            max_completion_tokens=max_completion_tokens,
            retries=5,
            stage="rollout",
            timeout=exec_timeout,
        )
        result.update(
            response=response,
            agent_ok=True,
            llm_ok=True,
            n_turns=1,
            finish_reason=(call_info or {}).get("finish_reason"),
            prompt_tokens=int((call_info or {}).get("prompt_tokens", 0) or 0),
            completion_tokens=int((call_info or {}).get("completion_tokens", 0) or 0),
            reasoning_tokens=int((call_info or {}).get("reasoning_tokens", 0) or 0),
        )
        evaluation = evaluate(
            question=item["question"],
            response=response,
            reference_answers=list(item.get("reference_answer") or []),
            reference_solutions=list(item.get("reference_solution") or []),
            use_llm_judge=use_llm_judge,
            judge_max_completion_tokens=judge_max_completion_tokens,
            judge_timeout=judge_timeout,
        )
        result.update(evaluation)
        if not evaluation["judge_ok"]:
            result["fail_reason"] = evaluation["judge_reason"]
        elif not evaluation["hard"]:
            result["fail_reason"] = f"Math answer judged incorrect: {evaluation['judge_reason']}"
        with open(os.path.join(pred_dir, "target_system_prompt.txt"), "w", encoding="utf-8") as handle:
            handle.write(system)
        with open(os.path.join(pred_dir, "target_user_prompt.txt"), "w", encoding="utf-8") as handle:
            handle.write(user)
        with open(os.path.join(pred_dir, "conversation.json"), "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": response},
                    {
                        "role": "system",
                        "content": (
                            "[EVALUATION RESULT]\n"
                            f"Official answer: {result['correct_text']}\n"
                            f"Predicted answer: {result['predicted_answer']}\n"
                            f"Correct: {result['hard']}\n"
                            f"Judge: {result['judge_reason']}"
                        ),
                    },
                ],
                handle,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:  # noqa: BLE001
        result["fail_reason"] = f"error: {type(exc).__name__}: {exc}"
    return result


def run_batch(
    *,
    items: list[dict],
    out_root: str,
    skill_content: str,
    exec_timeout: int,
    workers: int,
    max_completion_tokens: int,
    use_llm_judge: bool,
    judge_max_completion_tokens: int,
    judge_timeout: int,
) -> list[dict]:
    os.makedirs(out_root, exist_ok=True)
    results_path = os.path.join(out_root, "results.jsonl")
    existing: list[dict] = []
    done_ids: set[str] = set()
    dropped_invalid = 0
    if os.path.isfile(results_path):
        with open(results_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    dropped_invalid += 1
                    continue
                if not row.get("agent_ok") or not row.get("judge_ok"):
                    dropped_invalid += 1
                    continue
                existing.append(row)
                done_ids.add(str(row.get("id")))
        if dropped_invalid:
            with open(results_path, "w", encoding="utf-8") as handle:
                for row in existing:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"    [openmath] removed {dropped_invalid} invalid target/judge rows before resume",
                flush=True,
            )
    pending = [item for item in items if str(item["id"]) not in done_ids]
    if not pending:
        return existing
    total = len(existing) + len(pending)
    completed = len(existing)
    correct = sum(int(row.get("hard", 0) or 0) for row in existing)
    if existing:
        print(f"    [openmath] resuming: {completed}/{total} already done", flush=True)
    results = list(existing)
    with ThreadPoolExecutor(max_workers=workers) as pool, open(results_path, "a", encoding="utf-8") as out:
        futures = {
            pool.submit(
                process_one,
                item,
                out_root,
                skill_content,
                exec_timeout=exec_timeout,
                max_completion_tokens=max_completion_tokens,
                use_llm_judge=use_llm_judge,
                judge_max_completion_tokens=judge_max_completion_tokens,
                judge_timeout=judge_timeout,
            ): item
            for item in pending
        }
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            completed += 1
            correct += int(row.get("hard", 0) or 0)
            print(
                f"    [openmath] {completed}/{total} acc={correct / completed:.3f} "
                f"id={row['id']} hard={row['hard']} judge_ok={row.get('judge_ok')}",
                flush=True,
            )
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
    return results
