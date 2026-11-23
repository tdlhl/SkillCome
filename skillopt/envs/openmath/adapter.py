"""SkillOpt adapter for open-ended mathematics benchmarks."""
from __future__ import annotations

import os

from skillopt.datasets.base import BatchSpec
from skillopt.envs.base import EnvAdapter
from skillopt.envs.openmath.dataloader import OpenMathDataLoader
from skillopt.envs.openmath.rollout import run_batch
from skillopt.gradient.reflect import run_minibatch_reflect


class OpenMathAdapter(EnvAdapter):
    def __init__(
        self,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_ratio: str = "2:1:7",
        split_seed: int = 42,
        split_output_dir: str = "",
        exec_timeout: int = 600,
        workers: int = 4,
        analyst_workers: int = 8,
        failure_only: bool = False,
        minibatch_size: int = 8,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        max_completion_tokens: int = 16384,
        use_llm_judge: bool = True,
        judge_max_completion_tokens: int = 1024,
        judge_timeout: int = 180,
    ) -> None:
        self.exec_timeout = int(exec_timeout)
        self.workers = int(workers)
        self.analyst_workers = int(analyst_workers)
        self.failure_only = bool(failure_only)
        self.minibatch_size = int(minibatch_size)
        self.edit_budget = int(edit_budget)
        self.max_completion_tokens = int(max_completion_tokens)
        self.use_llm_judge = bool(use_llm_judge)
        self.judge_max_completion_tokens = int(judge_max_completion_tokens)
        self.judge_timeout = int(judge_timeout)
        self.dataloader = OpenMathDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_ratio=split_ratio,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )

    def setup(self, cfg: dict) -> None:
        super().setup(cfg)
        self.dataloader.setup(cfg)

    def get_dataloader(self):
        return self.dataloader

    def build_reference_text(self, item: dict) -> str:
        answers = "\n".join(str(x) for x in item.get("reference_answer") or [])
        solutions = "\n\n".join(str(x) for x in item.get("reference_solution") or [])
        parts = []
        if answers:
            parts.append(f"## Reference Answer\n{answers}")
        if solutions:
            parts.append(f"## Reference Solution\n{solutions}")
        return "\n\n".join(parts)

    def get_reference_metadata(self, item: dict) -> dict:
        answers = list(item.get("reference_answer") or [])
        solutions = list(item.get("reference_solution") or [])
        fields = []
        if answers:
            fields.append("reference_answer")
        if solutions:
            fields.append("reference_solution")
        return {"fields": fields, "preview": self.build_reference_text(item)[:500]}

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        return list(batch.payload or [])

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        return list(self.dataloader.build_train_batch(batch_size=batch_size, seed=seed).payload or [])

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        return list(self.dataloader.build_eval_batch(env_num=env_num, split=split, seed=seed).payload or [])

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        return run_batch(
            items=list(env_manager),
            out_root=out_dir,
            skill_content=skill_content,
            exec_timeout=self.exec_timeout,
            workers=self.workers,
            max_completion_tokens=self.max_completion_tokens,
            use_llm_judge=self.use_llm_judge,
            judge_max_completion_tokens=self.judge_max_completion_tokens,
            judge_timeout=self.judge_timeout,
        )

    def reflect(self, results: list[dict], skill_content: str, out_dir: str, **kwargs):
        return run_minibatch_reflect(
            results=results,
            skill_content=skill_content,
            prediction_dir=kwargs.get("prediction_dir", os.path.join(out_dir, "predictions")),
            patches_dir=kwargs.get("patches_dir", os.path.join(out_dir, "patches")),
            workers=self.analyst_workers,
            failure_only=self.failure_only,
            minibatch_size=self.minibatch_size,
            edit_budget=self.edit_budget,
            random_seed=kwargs.get("random_seed"),
            error_system=self.get_error_minibatch_prompt(),
            success_system=self.get_success_minibatch_prompt(),
            step_buffer_context=kwargs.get("step_buffer_context", ""),
            meta_skill_context=kwargs.get("meta_skill_context", ""),
            failure_hypothesis_bank_context=kwargs.get("failure_hypothesis_bank_context", ""),
            skillopt_failure_memory_context=kwargs.get("skillopt_failure_memory_context", ""),
            attach_failure_task_provenance=bool(kwargs.get("attach_failure_task_provenance", False)),
            update_mode=getattr(self, "_cfg", {}).get("skill_update_mode", "patch"),
        )

    def get_task_types(self) -> list[str]:
        domains = {
            str(item.get("task_type") or "open_math")
            for item in self.dataloader.train_items + self.dataloader.val_items + self.dataloader.test_items
        }
        return sorted(domains)
