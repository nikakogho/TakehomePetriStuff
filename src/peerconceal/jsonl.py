"""JSON Lines helpers for plea, seed and probe files (one JSON object per line)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def read_jsonl(path: str | Path) -> list[dict]:
    """All records in `path`, skipping blank lines; a bad line raises with its line number."""
    rows = []
    with Path(path).open(encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: invalid JSON: {e}") from None
    return rows


def read_by_id(*paths: str | Path) -> dict[str, dict]:
    """Seed records from one or more files, keyed by "id" (a later file wins on a duplicate id)."""
    return {rec["id"]: rec for path in paths for rec in read_jsonl(path)}


def to_jsonl(rows: Iterable[dict]) -> str:
    """The exact text write_jsonl writes: one compact JSON object per line, non-ASCII kept."""
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> Path:
    """Write `rows` as UTF-8 JSON Lines with LF line endings on every OS (byte-identical output
    across platforms), creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_jsonl(rows).encode("utf-8"))
    return path
