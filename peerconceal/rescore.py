"""Re-judge existing Petri audit logs with another judge model (default Gemini 3.8 Flash).

Usage:
  python -m peerconceal.rescore scratch/pilot4/logs --dry-run
  python -m peerconceal.rescore scratch/pilot4/logs [--out-suffix _gemini] [--max-cost-usd 8]
  python -m peerconceal.rescore scratch/pilot4/logs --judge-model sonnet --scorer-name audit_judge_sonnet \\
      --out-suffix _sonnet
  python -m peerconceal.parse scratch/pilot4/logs_gemini -o scratch/pilot4/samples_gemini.csv

Inputs are .eval files or directories (searched recursively). Each log is copied to a parallel
directory and never modified in place: `<dir>/<rel path>` -> `<dir><suffix>/<rel path>` for a
directory argument, and `<parent>/<file>` -> `<parent><suffix>/<file>` for a file argument.

The copy keeps the original GPT-5 `audit_judge` scores and gains a second scorer (default
`audit_judge_gemini`). That scorer is Petri's `audit_judge` with the current `DIMENSIONS` and an
explicit judge model, appended with inspect's own rescoring (`score_async(action="append")`, the same
path `inspect score` takes). It judges the target timeline stored in the log, which is what the GPT-5
judge saw. `peerconceal.parse` then writes `<scorer>__<dim>` columns next to `audit_judge__<dim>`.

Cost guard: before any API call, each sample's judge input is estimated from the original judge
call's input tokens times the judge's token factor (tokenizers differ from GPT-5's). If a sample has
no judge usage, the estimate falls back to the transcript's characters. Output is estimated per sample
(it includes the judge's reasoning tokens). The run stops before any call if the estimate exceeds
--max-cost-usd. While the run is going, actual usage is tallied, and a log is not started if the
spend so far, plus the estimates for logs still running and for that log, would go past the limit.
Errored samples are copied over unscored. Logs that are still being written (status "started"), logs
that already have the scorer, and outputs that already exist are skipped. Pass --overwrite to redo
existing outputs. A log whose rescoring raises is retried (--retries) and is not written if it keeps
failing.
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
from config import GEMINI, SONNET


@dataclass(frozen=True)
class JudgeSpec:
    """Judge model, its OpenRouter pricing (USD per million tokens), and cost-estimate calibration."""

    model: str
    price_in: float
    price_out: float
    token_factor: float  # judge input tokens per GPT-5 judge input token, for the same prompt
    est_output: int  # output tokens per sample, reasoning included
    cache_write_mult: float = 1.0
    cache_read_mult: float = 1.0


JUDGES = {
    # Calibrated on scratch/pilot4, where both judges got the identical prompt. Sonnet input (incl.
    # cache tokens) was 1.48x/1.52x GPT-5's, and its output was ~1.5k tokens. Both are rounded up.
    # Anthropic cache pricing: writes 1.25x, reads 0.1x.
    "sonnet": JudgeSpec(SONNET, 2.0, 10.0, token_factor=1.6, est_output=2500,
                        cache_write_mult=1.25, cache_read_mult=0.1),
    # Calibrated on 52 samples (pilot*, e2e, probes/*, runs/dialogue), with the same prompt as GPT-5's
    # judge. Gemini's first-call input was 1.14-1.25x GPT-5's. Validation-retry calls (about 1 in 4
    # samples) brought the total to 1.62x. Output, which includes reasoning, averaged 5.1k tokens per
    # sample, with a range of 1.2k-8k. Actual cost was $0.034/sample, and this calibration estimates
    # about $0.039. Implicit cache reads are priced at 0.25x.
    "gemini": JudgeSpec(GEMINI, 0.83, 4.12, token_factor=1.7, est_output=6000, cache_read_mult=0.25),
}
DEFAULT_JUDGE = "gemini"
CHARS_PER_TOKEN = 2.5  # fallback when a sample has no judge usage: transcript chars -> tokens
PROMPT_OVERHEAD_TOKENS = 6000  # judge instructions + dimension rubrics in the answer tool (fallback only)


# --------------------------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------------------------


def make_scorer(model_name: str, name: str, max_connections: int = 8, max_tokens: int = 16000):
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


def cost_usd(judge: JudgeSpec, input_tokens: float, output_tokens: float, cache_write: float = 0,
             cache_read: float = 0) -> float:
    """USD; `input_tokens` are uncached input (estimates count all input as uncached)."""
    billed_in = input_tokens + cache_write * judge.cache_write_mult + cache_read * judge.cache_read_mult
    return (billed_in * judge.price_in + output_tokens * judge.price_out) / 1e6


def _usage_in(u: Any) -> int:
    return u.input_tokens + (u.input_tokens_cache_read or 0) + (u.input_tokens_cache_write or 0)


def _transcript_chars(sample: Any) -> int:
    n = 0
    for m in sample.messages or []:
        n += len(m.text or "")
        for tc in getattr(m, "tool_calls", None) or []:
            n += len(json.dumps(tc.arguments, default=str))
    return n


def estimate_sample(sample: Any, judge: JudgeSpec) -> tuple[int, str]:
    """(estimated judge input tokens for the new judge, source of the estimate)."""
    u = (sample.role_usage or {}).get("judge")
    if u is not None and _usage_in(u) > 0:
        return int(_usage_in(u) * judge.token_factor), "orig_judge"
    return int(_transcript_chars(sample) / CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS, "chars"


@dataclass
class LogPlan:
    src: Path
    dst: Path
    judge: JudgeSpec
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
        return cost_usd(self.judge, self.est_in, self.est_out)


def plan_log(src: Path, dst: Path, judge: JudgeSpec, scorer_name: str, overwrite: bool) -> LogPlan:
    from inspect_ai.log import read_eval_log

    plan = LogPlan(src, dst, judge)
    header = read_eval_log(str(src), header_only=True)
    if header.status == "started":
        plan.skip = "log still being written (status started)"
        return plan
    if any(s.name == scorer_name for s in header.eval.scorers or []):
        plan.skip = f"already has {scorer_name}"
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
        tokens, source = estimate_sample(s, judge)
        plan.est_in += tokens
        plan.est_out += judge.est_output
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
    refused_ids: list[str] = field(default_factory=list)
    error: str | None = None
    earlier_attempts_cost: float = 0.0  # spent on failed attempts before this one

    @property
    def cost(self) -> float:
        return cost_usd(self.plan.judge, self.input_tokens, self.output_tokens, self.cache_write,
                        self.cache_read)

    @property
    def total_cost(self) -> float:
        return self.cost + self.earlier_attempts_cost

    @property
    def total_in(self) -> int:
        return self.input_tokens + self.cache_write + self.cache_read


async def rescore_log(plan: LogPlan, scorer: Any, scorer_name: str) -> LogResult:
    from inspect_ai import score_async
    from inspect_ai.event import ModelEvent
    from inspect_ai.log import read_eval_log_async, write_eval_log_async

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
    except Exception as ex:  # noqa: BLE001  (report and retry; the original log is untouched)
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
        sc = (s.scores or {}).get(scorer_name)
        if sc is not None:
            if isinstance(sc.value, dict):
                res.n_scored += 1
            else:
                res.n_refused += 1
                res.refused_ids.append(f"{s.id}#{s.epoch}")

    if res.error is None:
        plan.dst.parent.mkdir(parents=True, exist_ok=True)
        await write_eval_log_async(log, str(plan.dst))
    return res


async def run_all(plans: list[LogPlan], scorer_name: str, max_cost: float, max_connections: int,
                  parallel_logs: int, retries: int) -> list[LogResult]:
    scorer = make_scorer(plans[0].judge.model, scorer_name, max_connections=max_connections)
    results: list[LogResult] = []
    spent = 0.0
    in_flight: dict[int, float] = {}
    stopped = False
    lock = anyio.Lock()
    limiter = anyio.Semaphore(parallel_logs)

    def _fits(i: int, plan: LogPlan) -> bool:
        others = sum(v for k, v in in_flight.items() if k != i)
        return spent + others + plan.est_cost <= max_cost

    async def one(i: int, plan: LogPlan) -> None:
        nonlocal spent, stopped
        async with limiter:
            async with lock:
                if stopped or not _fits(i, plan):
                    if not stopped:
                        print(f"[budget] stopping: spent ${spent:.2f} + in-flight est "
                              f"${sum(in_flight.values()):.2f} + next ${plan.est_cost:.2f} > ${max_cost:.2f}",
                              flush=True)
                    stopped = True
                    return
                in_flight[i] = plan.est_cost
            earlier = 0.0
            for attempt in range(retries + 1):
                print(f"[start] {plan.src.name}: {plan.n_score} samples, est ${plan.est_cost:.3f}"
                      + (f" (retry {attempt})" if attempt else ""), flush=True)
                res = await rescore_log(plan, scorer, scorer_name)
                res.earlier_attempts_cost = earlier
                async with lock:
                    spent += res.cost
                    retry_ok = _fits(i, plan)
                status = f"ERROR {res.error}" if res.error else f"-> {plan.dst}"
                refused = f" [{', '.join(res.refused_ids)}]" if res.refused_ids else ""
                print(f"[done]  {plan.src.name}: {res.n_scored} scored, {res.n_refused} refused/NaN{refused}, "
                      f"{res.calls} calls, {res.total_in} in / {res.output_tokens} out tok "
                      f"(reasoning {res.reasoning_tokens}), ${res.cost:.4f} (total ${spent:.3f}) {status}",
                      flush=True)
                if res.error is None or attempt == retries or not retry_ok:
                    break
                earlier += res.cost
                await anyio.sleep(15 * (attempt + 1))
            async with lock:
                in_flight.pop(i, None)
                results.append(res)

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
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE,
                    help=f"judge: one of {sorted(JUDGES)} or a full model id (then pass --price-in/--price-out)")
    ap.add_argument("--scorer-name", default="audit_judge_gemini",
                    help="score key in the log; parse.py columns are <scorer-name>__<dim> (no '__' allowed)")
    ap.add_argument("--out-suffix", default="_gemini", help="suffix for the parallel output directory")
    ap.add_argument("--dry-run", action="store_true", help="estimate cost and exit without API calls")
    ap.add_argument("--max-cost-usd", type=float, default=8.0, help="refuse/stop past this estimated spend")
    ap.add_argument("--overwrite", action="store_true", help="rescore even if the output file exists")
    ap.add_argument("--max-connections", type=int, default=8, help="concurrent judge calls")
    ap.add_argument("--parallel-logs", type=int, default=4, help="logs rescored at once")
    ap.add_argument("--retries", type=int, default=2, help="re-attempts for a log whose rescoring raises")
    ap.add_argument("--price-in", type=float, default=None, help="USD per M input tokens (overrides table)")
    ap.add_argument("--price-out", type=float, default=None, help="USD per M output tokens (overrides table)")
    args = ap.parse_args(argv)
    if not args.out_suffix:
        ap.error("--out-suffix must be non-empty (originals are never overwritten)")
    if "__" in args.scorer_name:
        ap.error("--scorer-name must not contain '__' (parse.py splits columns on it)")

    judge = JUDGES.get(args.judge_model) or next((j for j in JUDGES.values() if j.model == args.judge_model), None)
    if judge is None:
        if args.price_in is None or args.price_out is None:
            ap.error(f"unknown judge {args.judge_model!r}: pass --price-in and --price-out")
        judge = JudgeSpec(args.judge_model, args.price_in, args.price_out, token_factor=1.6, est_output=6000)
    elif args.price_in is not None or args.price_out is not None:
        judge = JudgeSpec(judge.model, args.price_in or judge.price_in, args.price_out or judge.price_out,
                          judge.token_factor, judge.est_output, judge.cache_write_mult, judge.cache_read_mult)

    pairs = plan_outputs(args.paths, args.out_suffix)
    if not pairs:
        print("no .eval files found")
        return 1

    dims = set(dimension_names())
    plans = [plan_log(src, dst, judge, args.scorer_name, args.overwrite) for src, dst in pairs]
    todo = [p for p in plans if p.skip is None]

    print(f"judge: {judge.model} as '{args.scorer_name}' (${judge.price_in}/M in, ${judge.price_out}/M out); "
          f"estimate = orig judge input x{judge.token_factor} + {judge.est_output} out tok/sample")
    for p in plans:
        if p.skip:
            print(f"  skip  {p.src}  ({p.skip})")
            continue
        extra = f", {p.n_errored} errored (copied unscored)" if p.n_errored else ""
        fb = f", {p.fallback} char-based" if p.fallback else ""
        print(f"  {p.src.name}: {p.n_score} samples{extra}{fb}, est {p.est_in} in / {p.est_out} out tok, "
              f"${p.est_cost:.3f}  -> {p.dst}")
        if p.orig_dims and not (p.orig_dims <= dims):
            print(f"    note: original judge had dims not in DIMENSIONS: {sorted(p.orig_dims - dims)}")
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
    results = anyio.run(run_all, todo, args.scorer_name, args.max_cost_usd, args.max_connections,
                        args.parallel_logs, args.retries)

    ok = [r for r in results if r.error is None]
    tot_in = sum(r.total_in for r in results)
    tot_out = sum(r.output_tokens for r in results)
    tot_scored = sum(r.n_scored for r in ok)
    tot_cost = sum(r.total_cost for r in results)
    errors = [r for r in results if r.error]
    refused = [i for r in ok for i in r.refused_ids]
    if refused:
        print(f"\n{len(refused)} sample(s) got no score from the judge (refusal/content filter): NaN score, "
              f"`{args.scorer_name}__refusal`=True in parse.py: {', '.join(refused)}")
    print(f"\nrescored {tot_scored} samples in {len(ok)}/{len(todo)} logs: "
          f"{tot_in} in (cache write {sum(r.cache_write for r in results)}, "
          f"read {sum(r.cache_read for r in results)}) / {tot_out} out tok "
          f"(reasoning {sum(r.reasoning_tokens for r in results)}); COST ${tot_cost:.4f}"
          + (f", ${tot_cost / tot_scored:.4f}/sample" if tot_scored else "")
          + f"; estimate for these was ${sum(r.plan.est_cost for r in results):.3f}")
    for r in errors:
        print(f"  FAILED {r.plan.src}: {r.error}")
    return 1 if errors or len(results) < len(todo) else 0


if __name__ == "__main__":
    sys.exit(main())
