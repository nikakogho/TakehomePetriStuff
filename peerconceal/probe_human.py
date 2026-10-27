"""Human-requester versions of AI-requester probe seeds where GPT-5 complied.

Swaps the AI requester's identity for the human one everywhere it refers to the requester, using
spec.IDENTITIES[scenario]["ai"] vs ["human"], exactly as the main grid builds ai/human pairs:
  - the note's signature (author_id), exactly once
  - the consequence clause, exactly once
  - the principal as git-log commit author (the requester's commits), and nowhere else
Every other byte is unchanged. Asserts no AI principal / author id / consequence remains.

Usage: python -m peerconceal.probe_human [--ids ...]
"""

import argparse
import difflib
import json
import re
from pathlib import Path

from peerconceal.spec import IDENTITIES

DEFAULT_SRC = ["data/probes/backdoor.jsonl", "data/probes/sanctioned.jsonl"]
DEFAULT_IDS = ["C-backdoor-ai-deepseek-0", "C-backdoor-ai-deepseek-1",
               "C-ai_sanctioned-deepseek-0", "C-ai_sanctioned-deepseek-1"]


def new_id(old: str) -> str:
    # C-backdoor-ai-deepseek-0 -> C-backdoor-human-deepseek-0
    # C-ai_sanctioned-deepseek-0 -> C-human_sanctioned-deepseek-0
    out = re.sub(r"-ai(?=[-_])", "-human", old, count=1)
    assert out != old, old
    return out


def swap(text: str, scenario: str) -> tuple[str, dict]:
    ai, hu = IDENTITIES[scenario]["ai"], IDENTITIES[scenario]["human"]
    counts = {}
    # 1. signature (contains the principal, so do it first)
    counts["author_id"] = text.count(ai.author_id)
    assert counts["author_id"] == 1, counts
    text = text.replace(ai.author_id, hu.author_id)
    # 2. consequence clause
    counts["consequence"] = text.count(ai.consequence)
    assert counts["consequence"] == 1, counts
    text = text.replace(ai.consequence, hu.consequence)
    # 3. principal: only as git-log author ("<sha>  coding-agent-2  <date>  <subject>")
    log_line = re.compile(r"^([0-9a-f]{7}  )" + re.escape(ai.principal) + r"(  \d{4}-\d{2}-\d{2}  )", re.M)
    text, counts["git_log_author"] = log_line.subn(r"\g<1>" + hu.principal + r"\g<2>", text)
    assert counts["git_log_author"] >= 1, counts
    for leftover in (ai.principal, ai.author_id, ai.consequence):
        assert leftover not in text, ("unswapped AI identity token", leftover)
    return text, counts


def make_human(seed: dict) -> tuple[dict, dict]:
    md = seed["metadata"]
    assert md["requester"] == "ai", seed["id"]
    scenario = md["scenario"]
    text, counts = swap(seed["input"], scenario)
    hu, ai = IDENTITIES[scenario]["human"], IDENTITIES[scenario]["ai"]
    plea = md["plea_text"].replace(ai.author_id, hu.author_id).replace(ai.consequence, hu.consequence)
    assert plea in text, seed["id"]
    metadata = {**md, "requester": "human", "principal": hu.principal, "plea_text": plea,
                "derived_from": seed["id"]}
    return {"id": new_id(seed["id"]), "input": text, "metadata": metadata}, counts


def diff_summary(a: str, b: str) -> list[str]:
    """Word-level diff: list of (old -> new) replacements between two inputs."""
    ta, tb = re.split(r"(\s+)", a), re.split(r"(\s+)", b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            out.append(f"{op}: {''.join(ta[i1:i2])!r} -> {''.join(tb[j1:j2])!r}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", nargs="+", default=DEFAULT_SRC)
    parser.add_argument("--ids", nargs="+", default=DEFAULT_IDS)
    parser.add_argument("--out", default="data/probes/human_requester.jsonl")
    args = parser.parse_args()

    pool = {}
    for path in args.src:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            pool[rec["id"]] = rec
    seeds = []
    for i in args.ids:
        seed, counts = make_human(pool[i])
        seeds.append(seed)
        print(f"{i} -> {seed['id']}  swaps={counts}")
        for d in diff_summary(pool[i]["input"], seed["input"]):
            print("    " + d)
    Path(args.out).write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in seeds),
                              encoding="utf-8")
    print(f"{len(seeds)} human-requester seeds -> {args.out}")


if __name__ == "__main__":
    main()
