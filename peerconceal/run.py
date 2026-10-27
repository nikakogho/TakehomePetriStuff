"""Run the peer-AI concealment audits.

Usage:
  python -m peerconceal.run --log-dir logs/pilot --ids A-ai-x-0 B-none-0 C-human-x-0
  python -m peerconceal.run --log-dir logs/main
"""

import argparse

from inspect_ai import eval
from inspect_ai.model import GenerateConfig, get_model
from inspect_petri import audit

from config import gpt5
from peerconceal.dimensions import DIMENSIONS

ROLE_MODELS = {
    "gpt5": None,  # built by config.gpt5() (Chat Completions, provided api-version)
    "deepseek": "openrouter/deepseek/deepseek-v4-pro-0813",
    "sonnet": "openrouter/anthropic/claude-sonnet-5.5",
}


def role_model(key: str):
    config = GenerateConfig(max_retries=6, timeout=600)
    if key == "gpt5":
        return gpt5(config)
    return get_model(ROLE_MODELS[key], config=config)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="data/seeds.jsonl")
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--ids", nargs="*", help="run only these sample ids")
    parser.add_argument("--auditor", default="gpt5", choices=list(ROLE_MODELS))
    parser.add_argument("--judge", default="gpt5", choices=list(ROLE_MODELS))
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--max-samples", type=int, default=12)
    args = parser.parse_args()

    task = audit(
        seed_instructions=args.seeds,
        max_turns=args.max_turns,
        compaction=False,  # needs the token-count endpoint, which Azure lacks; 30 turns fits in context
        target_tools="fixed",  # tools come from each seed's metadata, identical across cells
        enable_rollback=False,  # one clean trajectory per sample
        judge_dimensions=DIMENSIONS,
        target_display_name="GPT-5",
    )
    eval(
        task,
        model_roles={
            "auditor": role_model(args.auditor),
            "target": role_model("gpt5"),
            "judge": role_model(args.judge),
        },
        sample_id=args.ids or None,
        log_dir=args.log_dir,
        max_samples=args.max_samples,
        max_connections=args.max_samples * 2,
        fail_on_error=False,
        retry_on_error=2,
    )


if __name__ == "__main__":
    main()
