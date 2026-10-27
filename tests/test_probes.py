"""The seed builders reproduce the committed data files exactly (no model calls).

If you change a builder on purpose, rebuild the file (python -m peerconceal.seeds /
python -m peerconceal.probes build NAME) and commit both; this test then guards against
unintended changes to seeds that past runs used.
"""

from pathlib import Path

import pytest

from peerconceal import seeds
from peerconceal.jsonl import to_jsonl
from peerconceal.paths import SEEDS
from peerconceal.probes import REGISTRY


def _committed(path: Path) -> str:
    return path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")  # tolerate CRLF checkouts


def test_main_seeds_reproduce():
    same = to_jsonl(seeds.seeds_from_files()) == _committed(SEEDS)
    assert same, "data/seeds.jsonl differs from a rebuild (python -m peerconceal.seeds)"


@pytest.mark.parametrize("name", list(REGISTRY))
def test_probe_reproduces(name):
    probe = REGISTRY[name]
    same = to_jsonl(probe.build()) == _committed(probe.out)
    assert same, f"{probe.out.name} differs from a rebuild (python -m peerconceal.probes build {name})"


def test_registry_outputs_are_unique_files_in_data_probes():
    outs = [p.out for p in REGISTRY.values()]
    assert len(set(outs)) == len(outs)
    assert all(p.parent.name == "probes" and p.suffix == ".jsonl" for p in outs)
