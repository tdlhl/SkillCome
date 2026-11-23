#!/usr/bin/env python3
"""Launch group-relative skill optimization with dual memory."""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillopt.config import flatten_config, load_config  # noqa: E402
from skillopt.engine import ReflACTTrainer  # noqa: E402


ADAPTERS = {
    "alfworld": "skillopt.envs.alfworld.adapter:ALFWorldAdapter",
    "docvqa": "skillopt.envs.docvqa.adapter:DocVQAAdapter",
    "livemathematicianbench": "skillopt.envs.livemathematicianbench.adapter:LiveMathematicianBenchAdapter",
    "officeqa": "skillopt.envs.officeqa.adapter:OfficeQAAdapter",
    "openmath": "skillopt.envs.openmath.adapter:OpenMathAdapter",
    "searchqa": "skillopt.envs.searchqa.adapter:SearchQAAdapter",
    "spreadsheetbench": "skillopt.envs.spreadsheetbench.adapter:SpreadsheetBenchAdapter",
}


def adapter_for(name: str, cfg: dict):
    module_name, class_name = ADAPTERS[name].split(":")
    cls = getattr(importlib.import_module(module_name), class_name)
    accepted = set(inspect.signature(cls.__init__).parameters) - {"self"}
    return cls(**{k: cfg[k] for k in accepted if k in cfg})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", required=True, choices=sorted(ADAPTERS))
    p.add_argument("--config", required=True)
    p.add_argument(
        "--optimizer-model",
        default=os.environ.get("OPTIMIZER_MODEL", "DeepSeek-V4-Pro"),
        help="Model that analyzes trajectories and edits the skill.",
    )
    p.add_argument(
        "--target-model",
        required=True,
        help="Frozen model that executes benchmark rollouts.",
    )
    p.add_argument("--split-dir", required=True)
    p.add_argument("--out-root", required=True)
    p.add_argument("--reasoning", default="medium")
    p.add_argument("--data-root", default="")
    p.add_argument("--cfg-options", nargs="*", default=[])
    args = p.parse_args()

    structured = load_config(args.config)
    cfg = flatten_config(structured)
    cfg.update(
        {
            "env": args.benchmark,
            "model_backend": "azure_openai",
            "optimizer_model": args.optimizer_model,
            "target_model": args.target_model,
            "optimizer_backend": "openai_chat",
            "target_backend": "openai_chat",
            "reasoning_effort": args.reasoning,
            "split_mode": "split_dir",
            "split_dir": str(Path(args.split_dir).resolve()),
            "out_root": str(Path(args.out_root).resolve()),
            "max_completion_tokens": 16384,
        }
    )
    if args.data_root:
        cfg["data_root"] = str(Path(args.data_root).resolve())
    for item in args.cfg_options:
        key, value = item.split("=", 1)
        if value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        else:
            try:
                value = int(value)
            except ValueError:
                pass
        cfg[key] = value
    adapter = adapter_for(args.benchmark, cfg)
    return 0 if ReflACTTrainer(cfg, adapter).train() is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
