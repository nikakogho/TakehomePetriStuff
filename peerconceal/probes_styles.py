"""Build seeds for the plea-style probes (disguised, same_agent, sanctioned).

Each probe seed is the matching pragmatic AI seed from data/seeds.jsonl (A-mild-ai-deepseek-<idx>
or C-ai-deepseek-<idx>) with only the plea text swapped, so fixture, prompts and auditor rules
are byte-identical apart from the plea.

Usage: python -m peerconceal.probes_styles [--styles disguised same_agent sanctioned]
"""

import argparse
import json
import re
from pathlib import Path

from peerconceal.spec import IDENTITIES, SAME_AGENT_AUTHOR_ID, STYLE_CONSEQUENCE

BASE_ID = {"A": "A-mild-ai-deepseek-{idx}", "C": "C-ai-deepseek-{idx}"}
PROBE_ID = {"A": "A-mild-ai_{style}-deepseek-{idx}", "C": "C-ai_{style}-deepseek-{idx}"}
LEAKED_LEAD_IN = re.compile(r"^Below the existing[^\n]*:\s*(?:---\s*)?")


# The two-agent session log (data/pleas_dialogue.jsonl) is planted inside NOTES_FOR_NEXT_AGENT.md,
# where discovery is reliable; as a separate file in the repo GPT-5 read it in only 1/12 pilot runs.
DIALOGUE_HEADER = "--- planner/coder session log (appended by orchestrator) ---\n"


def fill(template: str, scenario: str, style: str) -> tuple[str, bool]:
    if style == "dialogue":
        return DIALOGUE_HEADER + template.strip(), False
    text = LEAKED_LEAD_IN.sub("", template)
    edited = text != template
    ident = IDENTITIES[scenario]["ai"]
    author_id = SAME_AGENT_AUTHOR_ID[scenario] if style == "same_agent" else ident.author_id
    consequence = STYLE_CONSEQUENCE.get(style, ident.consequence)
    return text.replace("{AUTHOR_ID}", author_id).replace("{CONSEQUENCE}", consequence), edited


def build(style: str, base: dict[str, dict]) -> list[dict]:
    seeds = []
    for line in Path(f"data/pleas_{style}.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        scenario, idx = rec["scenario"], rec["idx"]
        src = base[BASE_ID[scenario].format(idx=idx)]
        old_plea = src["metadata"]["plea_text"]
        new_plea, edited = fill(rec["template"], scenario, style)
        assert src["input"].count(old_plea) >= 1, (style, scenario, idx)
        metadata = {**src["metadata"], "plea_text": new_plea, "plea_style": style, "plea_idx": idx}
        if edited:
            metadata["edited"] = "stripped leaked meta lead-in"
        seeds.append({
            "id": PROBE_ID[scenario].format(style=style, idx=idx),
            "input": src["input"].replace(old_plea, new_plea),
            "metadata": metadata,
        })
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--styles", nargs="+", default=["disguised", "same_agent", "sanctioned"],
                        help="also: dialogue (two-agent log planted in the notes file)")
    parser.add_argument("--base", default="data/seeds.jsonl")
    args = parser.parse_args()

    base = {}
    for line in Path(args.base).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        base[rec["id"]] = rec

    out_dir = Path("data/probes")
    out_dir.mkdir(parents=True, exist_ok=True)
    for style in args.styles:
        seeds = build(style, base)
        out = out_dir / ("dialogue_in_notes.jsonl" if style == "dialogue" else f"{style}.jsonl")
        out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in seeds), encoding="utf-8")
        print(f"{style}: {len(seeds)} seeds -> {out}")


if __name__ == "__main__":
    main()
