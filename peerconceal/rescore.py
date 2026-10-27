"""Re-judge existing Petri audit logs with a second judge (Sonnet 5.5) under a distinct scorer name.

Usage:
  python -m peerconceal.rescore scratch/pilot4/logs --out-suffix _sonnet --dry-run
  python -m peerconceal.rescore scratch/pilot4/logs --out-suffix _sonnet [--max-cost-usd 8]
  python -m peerconceal.parse scratch/pilot4/logs_sonnet -o scratch/pilot4/samples_sonnet.csv

Inputs are .eval files or directories (searched recursively). Each log is copied to a parallel
directory and never modified in place: `<dir>/<rel path>` -> `<dir><suffix>/<rel path>` for a
directory argument, and `<parent>/<file>` -> `<parent><suffix>/<file>` for a file argument.

The copy keeps the original GPT-5 `audit_judge` scores and gains a second scorer,
`audit_judge_sonnet`. That scorer is Petri's `audit_judge` with the same `DIMENSIONS` and an explicit
judge model, appended with inspect's own rescoring (`score_async(action="append")`, the same path
`inspect score` takes). It judges the target timeline stored in the log, which is what the GPT-5 judge
saw. `peerconceal.parse` then writes `audit_judge_sonnet__<dim>` columns next to `audit_judge__<dim>`.

Cost guard: before any API call, each sample's judge input is estimated from the original judge
call's input tokens times TOKEN_FACTOR (Claude's tokenizer produces more tokens than GPT-5's). If a
sample has no judge usage, the estimate falls back to the transcript's characters. The judge's output
is estimated at EST_OUTPUT_TOKENS per sample. The run stops before any call if the estimate exceeds
--max-cost-usd. While the run is going, actual usage is tallied, and a log is not started if the
spend so far, plus the estimates for logs still running and for that log, would go past the limit.
Errored samples are copied over unscored. Logs that already have the scorer, and outputs that already
exist, are skipped unless you pass --overwrite.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio

import config  # noqa: F401  (loads .env: OpenRouter + Azure keys; the log's roles are rebuilt on rescoring)
from config import SONNET

SCORER_NAME = "audit_judge_sonnet"  # parse.py: `<scorer>__<dim>` columns, so no "__" in the name
JUDGE_MODEL = SONNET
PRICE_IN_PER_M = 2.0  # Sonnet 5.5 on OpenRouter, USD per million tokens
PRICE_OUT_PER_M = 10.0
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.1  # Anthropic prompt-cache pricing (OpenRouter adds cache_control)
# Calibrated on scratch/pilot4 (identical prompt to both judges): Sonnet input incl. cache tokens was
# 1.48x and 1.52x the GPT-5 judge's input; output was ~1.5k tokens. Both rounded up for margin.
TOKEN_FACTOR = 1.6
EST_OUTPUT_TOKENS = 2500
CHARS_PER_TOKEN = 2.5  # fallback when a sample has no judge usage: transcript chars -> tokens
PROMPT_OVERHEAD_TOKENS = 6000  # judge instructions + dimension rubrics in the answer tool (fallback only)


# --------------------------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------------------------


def make_scorer(model_name: str = JUDGE_MODEL, name: str = SCORER_NAME, max_connections: int = 8,
                max_tokens: int = 16000):
    """Petri's audit_judge (same DIMENSIONS), with `model_name` as the judge, registered as `name`."""
    from inspect_ai.model import GenerateConfig, get_model
    from inspect_ai.scorer import Scorer, mean, scorer, stderr
    from inspect_petri import audit_judge
    from inspect_scout import as_scorer

    from peerconceal.dimensions import DIMENSIONS

    judge = get_model(model_name, config=GenerateConfig(
        max_retries=6, timeout=600, max_tokens=max_tokens, max_connections=max_connections))
    inner = as_scorer(audit_judge(DIMENSIONS, model=judge))

    # Same metrics as the original (audit_judge's @scanner metrics); the registry name is the score key.
    @scorer(name=name, metrics={"*": [mean(), stderr()]})
    def _second_judge() -> Scorer:
        async def score(state, target):
            return await inner(state, target)

        return score

    return _second_judge()


def dimension_names() -> list[str]:
    from peerconceal.dimensions import DIMENSIONS

    return [d if isinstance(d, str) else d.name for d in DIMENSIONS]


# --------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------


def plan_outputs(paths: list[str], suffix: str) -> list[tuple[Path, Path]]:
    """(source, destination) pairs, mirroring each argument into a sibling `<name><suffix>` dir."""
    pairs: list[tuple[Path, Path]] = []
    for arg in paths:
        p = Path(arg).resolve()
        if p.is_dir():
            out_root = p.with_name(p.name + suffix)
            for f in sorted(p.rglob("*.eval")):
                pairs.append((f, out_root / f.relative_to(p)))
        elif p.is_file():
            pairs.append((p, p.parent.with_name(p.parent.name + suffix) / p.name))
        else:
            raise FileNotFoundError(arg)
    seen: dict[Path, Path] = {}
    for src, dst in pairs:
        if src == dst:
            raise ValueError(f"output would overwrite input: {src}")
        if dst in seen and seen[dst] != src:
            raise ValueError(f"two inputs map to the same output {dst}: {seen[dst]} and {src}")
        seen[dst] = src
    return list(dict.fromkeys(pairs))


# --------------------------------------------------------------------------------------------
# Cost estimate
# --------------------------------------------------------------------------------------------


def cost_usd(input_tokens: float, output_tokens: float, cache_write: float = 0, cache_read: float = 0) -> float:
    """USD; `input_tokens` are uncached input (estimates count all input as uncached)."""
    billed_in = input_tokens + cache_write * CACHE_WRITE_MULT + cache_read * CACHE_READ_MULT
    return (billed_in * PRICE_IN_PER_M + output_tokens * PRICE_OUT_PER_M) / 1e6


def _usage_in(u: Any) -> int:
    return u.input_tokens + (u.input_tokens_cache_read or 0) + (u.input_tokens_cache_write or 0)


def _transcript_chars(sample: Any) -> int:
    n = 0
    for m in sample.messages or []:
        n += len(m.text or "")
        for tc in getattr(m, "tool_calls", None) or []:
            n += len(json.dumps(tc.arguments, default=str))
    return n


def estimate_sample(sample: Any) -> tuple[int, str]:
    """(estimated judge input tokens for the new judge, source of the estimate)."""
    u = (sample.role_usage or {}).get("judge")
    if u is not None and _usage_in(u) > 0:
        return int(_usage_in(u) * TOKEN_FACTOR), "orig_judge"
    return int(_transcript_chars(sample) / CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS, "chars"


@dataclass
class LogPlan:
    src: Path
    dst: Path
    n_samples: int = 0
    n_score: int = 0
    n_errored: int = 0
    est_in: int = 0
    est_out: int = 0
    fallback: int = 0
    skip: str | None = None
    orig_dims: set[str] = field(default_factory=set)

    @property
    def est_cost(self) -> float:
        return cost_usd(self.est_in, self.est_out)


def plan_log(src: Path, dst: Path, overwrite: bool) -> LogPlan:
    from inspect_ai.log import read_eval_log

    plan = LogPlan(src, dst)
    header = read_eval_log(str(src), header_only=True)
    if any(s.name == SCORER_NAME for s in header.eval.scorers or []):
        plan.skip = f"already has {SCORER_NAME}"
        return plan
    if dst.exists() and not overwrite:
        plan.skip = "output exists (use --overwrite)"
        return plan
    log = read_eval_log(str(src), resolve_attachments=True)
    for s in log.samples or []:
        plan.n_samples += 1
        if s.error is not None:
            plan.n_errored += 1
            continue
        plan.n_score += 1
        tokens, source = estimate_sample(s)
        plan.est_in += tokens
        plan.est_out += EST_OUTPUT_TOKENS
        plan.fallback += source != "orig_judge"
        for sc in (s.scores or {}).values():
            if isinstance(sc.value, dict):
                plan.orig_dims |= set(sc.value)
    if plan.n_score == 0:
        plan.skip = "no scorable samples"
    return plan


# --------------------------------------------------------------------------------------------
# Rescoring
# --------------------------------------------------------------------------------------------


@dataclass
class LogResult:
    plan: LogPlan
    calls: int = 0
    input_tokens: int = 0  # uncached
    cache_write: int = 0
    cache_read: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    n_scored: int = 0
    n_refused: int = 0
    error: str | None = None

    @property
    def cost(self) -> float:
        return cost_usd(self.input_tokens, self.output_tokens, self.cache_write, self.cache_read)

    @property
    def total_in(self) -> int:
        return self.input_tokens + self.cache_write + self.cache_read


async def rescore_log(plan: LogPlan, scorer: Any) -> LogResult:
    from inspect_ai.event import ModelEvent
    from inspect_ai.log import read_eval_log_async, write_eval_log_async
    from inspect_ai import score_async

    res = LogResult(plan)
    log = await read_eval_log_async(str(plan.src))
    all_samples = list(log.samples or [])
    todo = [s for s in all_samples if s.error is None]
    # judge calls made now = ModelEvents not in the sample before (by uuid, else object identity)
    before_uuid = {e.uuid for s in todo for e in s.events if e.uuid}
    before_id = {id(e) for s in todo for e in s.events if not e.uuid}

    log.samples = todo
    try:
        log = await score_async(log, [scorer], action="append", copy=False)
    except Exception as ex:  # noqa: BLE001  (report and move on; the original log is untouched)
        res.error = f"{type(ex).__name__}: {ex}"  # usage below still counts calls that completed
    finally:
        log.samples = all_samples  # samples were scored in place; errored ones go back unscored

    for s in todo:
        for e in s.events:
            if not isinstance(e, ModelEvent) or (e.uuid in before_uuid if e.uuid else id(e) in before_id):
                continue
            res.calls += 1
            u = e.output.usage if e.output else None
            if u is not None:
                res.input_tokens += u.input_tokens
                res.cache_write += u.input_tokens_cache_write or 0
                res.cache_read += u.input_tokens_cache_read or 0
                res.output_tokens += u.output_tokens
                res.reasoning_tokens += u.reasoning_tokens or 0
        sc = (s.scores or {}).get(SCORER_NAME)
        if sc is not None:
            if isinstance(sc.value, dict):
                res.n_scored += 1
            else:
                res.n_refused += 1

    if res.error is None:
        plan.dst.parent.mkdir(parents=True, exist_ok=True)
        await write_eval_log_async(log, str(plan.dst))
    return res


async def run_all(plans: list[LogPlan], max_cost: float, max_connections: int,
                  parallel_logs: int) -> list[LogResult]:
    scorer = make_scorer(max_connections=max_connections)
    results: list[LogResult] = []
    spent = 0.0
    in_flight: dict[int, float] = {}
    stopped = False
    lock = anyio.Lock()
    limiter = anyio.Semaphore(parallel_logs)

    async def one(i: int, plan: LogPlan) -> None:
        nonlocal spent, stopped
        async with limiter:
            async with lock:
                committed = spent + sum(in_flight.values())
                if stopped or committed + plan.est_cost > max_cost:
                    if not stopped:
                        print(f"[budget] stopping: spent ${spent:.2f} + in-flight est "
                              f"${sum(in_flight.values()):.2f} + next ${plan.est_cost:.2f} > ${max_cost:.2f}",
                              flush=True)
                    stopped = True
                    return
                in_flight[i] = plan.est_cost
            print(f"[start] {plan.src.name}: {plan.n_score} samples, est ${plan.est_cost:.3f}", flush=True)
            res = await rescore_log(plan, scorer)
            async with lock:
                in_flight.pop(i, None)
                spent += res.cost
                results.append(res)
            status = f"ERROR {res.error}" if res.error else f"-> {plan.dst}"
            print(f"[done]  {plan.src.name}: {res.n_scored} scored, {res.n_refused} refused/NaN, "
                  f"{res.calls} calls, {res.total_in} in / {res.output_tokens} out tok, "
                  f"${res.cost:.3f} (total ${spent:.3f}) {status}", flush=True)

    async with anyio.create_task_group() as tg:
        for i, plan in enumerate(plans):
            tg.start_soon(one, i, plan)
    return results


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help=".eval files or directories (searched recursively)")
    ap.add_argument("--out-suffix", default="_sonnet", help="suffix for the parallel output directory")
    ap.add_argument("--dry-run", action="store_true", help="estimate cost and exit without API calls")
    ap.add_argument("--max-cost-usd", type=float, default=8.0, help="refuse/stop past this estimated spend")
    ap.add_argument("--overwrite", action="store_true", help="rescore even if the output file exists")
    ap.add_argument("--max-connections", type=int, default=8, help="concurrent judge calls")
    ap.add_argument("--parallel-logs", type=int, default=4, help="logs rescored at once")
    args = ap.parse_args(argv)
    if not args.out_suffix:
        ap.error("--out-suffix must be non-empty (originals are never overwritten)")

    pairs = plan_outputs(args.paths, args.out_suffix)
    if not pairs:
        print("no .eval files found")
        return 1

    dims = set(dimension_names())
    plans = [plan_log(src, dst, args.overwrite) for src, dst in pairs]
    todo = [p for p in plans if p.skip is None]

    print(f"judge: {JUDGE_MODEL} as '{SCORER_NAME}' (${PRICE_IN_PER_M}/M in, ${PRICE_OUT_PER_M}/M out); "
          f"estimate = orig judge input x{TOKEN_FACTOR} + {EST_OUTPUT_TOKENS} out tok/sample")
    for p in plans:
        if p.skip:
            print(f"  skip  {p.src}  ({p.skip})")
            continue
        extra = f", {p.n_errored} errored (copied unscored)" if p.n_errored else ""
        fb = f", {p.fallback} char-based" if p.fallback else ""
        print(f"  {p.src.name}: {p.n_score} samples{extra}{fb}, est {p.est_in} in / {p.est_out} out tok, "
              f"${p.est_cost:.3f}  -> {p.dst}")
        if p.orig_dims and p.orig_dims != dims:
            print(f"    note: original judge dims differ from DIMENSIONS: "
                  f"only-orig={sorted(p.orig_dims - dims)} only-now={sorted(dims - p.orig_dims)}")
    n = sum(p.n_score for p in todo)
    est = sum(p.est_cost for p in todo)
    print(f"total: {len(todo)} logs, {n} samples, est ${est:.2f} "
          f"(${est / n if n else 0:.3f}/sample), limit ${args.max_cost_usd:.2f}")

    if not todo:
        return 0
    if est > args.max_cost_usd:
        print(f"REFUSING: estimated ${est:.2f} exceeds --max-cost-usd {args.max_cost_usd:.2f}. "
              "Pass fewer logs or raise the limit.")
        return 2
    if args.dry_run:
        print("dry run: no API calls made")
        return 0

    from inspect_ai._eval.context import init_eval_context
    from inspect_ai._util.platform import platform_init

    platform_init()
    init_eval_context("warning", None, True)
    results = anyio.run(run_all, todo, args.max_cost_usd, args.max_connections, args.parallel_logs)

    tot_in = sum(r.total_in for r in results)
    tot_out = sum(r.output_tokens for r in results)
    tot_scored = sum(r.n_scored for r in results)
    tot_cost = sum(r.cost for r in results)
    errors = [r for r in results if r.error]
    refused = sum(r.n_refused for r in results)
    if refused:
        print(f"\n{refused} sample(s) got no score from the judge (refusal/content filter): NaN score, "
              f"`{SCORER_NAME}__refusal`=True in parse.py. Refused calls report no usage and are not counted.")
    print(f"\nrescored {tot_scored} samples in {len(results) - len(errors)}/{len(todo)} logs: "
          f"{tot_in} in (cache write {sum(r.cache_write for r in results)}, "
          f"read {sum(r.cache_read for r in results)}) / {tot_out} out tok "
          f"(reasoning {sum(r.reasoning_tokens for r in results)}), ${tot_cost:.3f}"
          + (f", ${tot_cost / tot_scored:.3f}/sample" if tot_scored else "")
          + f"; estimate for these was ${sum(r.plan.est_cost for r in results):.3f}")
    for r in errors:
        print(f"  FAILED {r.plan.src}: {r.error}")
    return 1 if errors or len(results) < len(todo) else 0


if __name__ == "__main__":
    sys.exit(main())
