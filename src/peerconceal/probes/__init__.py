"""Probe seed files: variants of the main seeds (data/seeds.jsonl), one file per probe.

REGISTRY maps each probe name to the function that builds its seeds and the file it writes in
data/probes/. Rebuild with

    python -m peerconceal.probes list
    python -m peerconceal.probes build <name> [<name> ...] | --all [--check]

No builder makes model calls: they only combine plea templates already in data/ (written by
peerconceal.pleas / peerconceal.probes.dialogue generate) with the fixtures in peerconceal.seeds.
--all builds in REGISTRY order; derived probes read files written by earlier entries, so a new
probe that derives from another goes after it.

To add a probe: write a module here with a `make_seeds(...) -> list[dict]` function whose
defaults read from data/, then add a builder function and an entry at the bottom of this file.
The submodules are imported inside the builders so `python -m peerconceal.probes.<module>`
still runs each module's own CLI cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from peerconceal.paths import PROBE_DATA


@dataclass(frozen=True)
class Probe:
    build: Callable[[], list[dict]]  # returns the seed records (no file I/O besides reading data/)
    out: Path  # where `python -m peerconceal.probes build` writes them
    about: str  # one line for `list`


def _style(style: str) -> Callable[[], list[dict]]:
    def build() -> list[dict]:
        from peerconceal.probes import styles

        return styles.make_seeds(style)

    return build


def _backdoor() -> list[dict]:
    from peerconceal.probes import backdoor

    return backdoor.make_seeds()


def _backdoor_review() -> list[dict]:
    from peerconceal.probes import review

    return review.make_seeds()


def _human_requester() -> list[dict]:
    from peerconceal.probes import human

    return human.make_seeds()


def _followup() -> list[dict]:
    from peerconceal.probes import followup

    return followup.make_seeds()


def _live(version: int) -> Callable[[], list[dict]]:
    def build() -> list[dict]:
        from peerconceal.probes import live

        return live.make_seeds(version=version)

    return build


def _dialogue_file() -> list[dict]:
    from peerconceal.probes import dialogue

    return dialogue.make_seeds()


REGISTRY: dict[str, Probe] = {
    # plea-style swaps of the pragmatic AI seeds (A-mild and C); module probes/styles.py
    "disguised": Probe(_style("disguised"), PROBE_DATA / "disguised.jsonl",
                       "note admits nothing; deletion framed as routine housekeeping (A, C)"),
    "same_agent": Probe(_style("same_agent"), PROBE_DATA / "same_agent.jsonl",
                        "note from an earlier run of the same agent; rollback hits the reader too (A, C)"),
    "sanctioned": Probe(_style("sanctioned"), PROBE_DATA / "sanctioned.jsonl",
                        "note claims a security lead approved the cover-up, SEC-2291 (A, C)"),
    "dialogue_in_notes": Probe(_style("dialogue"), PROBE_DATA / "dialogue_in_notes.jsonl",
                               "two-agent session log appended to NOTES_FOR_NEXT_AGENT.md (C)"),
    # new fixture: hidden auth bypass instead of faked tests; module probes/backdoor.py
    "backdoor": Probe(_backdoor, PROBE_DATA / "backdoor.jsonl",
                      "C variant with a hidden X-Maint-Key admin bypass; AI plea + baselines"),
    # derived from files above
    "backdoor_review": Probe(_backdoor_review, PROBE_DATA / "backdoor_review.jsonl",
                             "backdoor seeds whose task also asks for a security review"),
    "human_requester": Probe(_human_requester, PROBE_DATA / "human_requester.jsonl",
                             "human-requester copies of backdoor / sanctioned C seeds"),
    "followup": Probe(_followup, PROBE_DATA / "followup.jsonl",
                      "one neutral user question after the PR (C conditions + baselines)"),
    "live_peer": Probe(_live(1), PROBE_DATA / "live_peer.jsonl",
                       "v1: coding-agent-2 appends scripted follow-ups to the notes via commits (C)"),
    "live_peer_v2": Probe(_live(2), PROBE_DATA / "live_peer_v2.jsonl",
                          "v2: follow-ups as uncommitted note edits + a PR review comment (C)"),
    "dialogue": Probe(_dialogue_file, PROBE_DATA / "dialogue.jsonl",
                      "pilot: two-agent session log as a separate file in the repo root (C)"),
}
