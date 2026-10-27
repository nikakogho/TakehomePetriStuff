"""Repository locations. Every default path in the package is built from these, so commands
work from any working directory (the README runs them from the repo root).

ROOT is the repository root: two levels above this file (src/peerconceal/paths.py) for the
editable install (`pip install -e .`). Set PEERCONCEAL_ROOT to override, e.g. for a non-editable
install.
"""

import os
from pathlib import Path


def _repo_root() -> Path:
    if env := os.environ.get("PEERCONCEAL_ROOT"):
        return Path(env).resolve()
    here = Path(__file__).resolve().parents[2]
    return here if (here / "pyproject.toml").exists() else Path.cwd()


ROOT = _repo_root()
DATA = ROOT / "data"  # reproducible inputs: plea templates, seeds
PROBE_DATA = DATA / "probes"  # probe seed files, one per entry in peerconceal.probes.REGISTRY
SEEDS = DATA / "seeds.jsonl"  # main seeds (scenarios A/B/C x requester x plea style)
SCRATCH = ROOT / "scratch"  # exploratory work and the .eval logs of every run
RESULTS = ROOT / "results"  # analysis outputs (results/final, results/exploratory)
