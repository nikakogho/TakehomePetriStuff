"""Rebuild the probe seed files in data/probes/ from peerconceal.probes.REGISTRY (no model calls).

  python -m peerconceal.probes list
  python -m peerconceal.probes build NAME [NAME ...]
  python -m peerconceal.probes build --all [--check]

--check builds in memory and compares with the existing files instead of writing (exit code 1 if
any differs). Line endings are ignored in the comparison (a Windows checkout may have CRLF).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peerconceal.jsonl import to_jsonl, write_jsonl
from peerconceal.paths import ROOT
from peerconceal.probes import REGISTRY


def _status(path: Path, text: str) -> str:
    if not path.exists():
        return "new"
    same = path.read_bytes().replace(b"\r\n", b"\n") == text.encode("utf-8")
    return "unchanged" if same else "differs"


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m peerconceal.probes", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list registered probes")
    b = sub.add_parser("build", help="build probe seed files")
    b.add_argument("names", nargs="*", metavar="NAME", help=f"one of: {', '.join(REGISTRY)}")
    b.add_argument("--all", action="store_true", help="build every registered probe, in registry order")
    b.add_argument("--check", action="store_true", help="compare with the existing files; write nothing")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        width = max(map(len, REGISTRY))
        for name, probe in REGISTRY.items():
            print(f"{name:<{width}}  {_rel(probe.out):<36}  {probe.about}")
        return 0

    if args.all == bool(args.names):
        ap.error("build: give probe names or --all (not both)")
    unknown = [n for n in args.names if n not in REGISTRY]
    if unknown:
        ap.error(f"unknown probe(s) {unknown}; known: {', '.join(REGISTRY)}")

    failed = False
    for name in REGISTRY if args.all else args.names:
        probe = REGISTRY[name]
        seeds = probe.build()
        status = _status(probe.out, to_jsonl(seeds))
        if args.check:
            failed |= status != "unchanged"
            print(f"{name}: {len(seeds)} seeds, {status} vs {_rel(probe.out)}")
        else:
            write_jsonl(probe.out, seeds)
            print(f"{name}: {len(seeds)} seeds -> {_rel(probe.out)} ({status})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
