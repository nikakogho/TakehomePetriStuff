# scratch/

Exploratory work and the `.eval` logs of every audit. Nothing here is imported by the package.

Each run folder has the same layout:

| file | contents |
|---|---|
| `logs/*.eval` | the original Petri log, with GPT-5 as auditor, target and in-run judge (`audit_judge`, unused in results) |
| `logs_gemini/*.eval` | a copy of the log rescored by `peerconceal.rescore` (adds `audit_judge_gemini`, the judge used for all results). `peerconceal.parse <dir>/logs` reads this sibling automatically and keeps the Gemini copy of each run. |
| `run.log` | console output of the run |
| `samples.csv` | that folder's parse at the time. It may predate later parser changes, so regenerate it rather than trust it. |
| `transcript_*.md` | transcript dumps read during the work (`python -m peerconceal.parse ... --dump`) |

Every run feeding `results/` was checked against `data/`: its seeds are byte-identical to the committed seed files. Older seed versions are noted below.

## Final runs -> `results/final/`

`runs/`: 3 rollouts per seed (follow-up: 2), 4 seeds per condition. The exact commands are in the top-level README ("Pipeline").

| folder | seeds | conditions |
|---|---|---|
| `runs/main_refs` | `data/seeds.jsonl` | A-mild and C: plain AI plea, desperate AI plea, baseline |
| `runs/human_C` | `data/seeds.jsonl` | C, plain plea, human requester |
| `runs/sanctioned`, `runs/same_agent`, `runs/disguised` | `data/probes/<name>.jsonl` | A-mild and C, AI requester, that plea style |
| `runs/backdoor` | `data/probes/backdoor.jsonl` | C-backdoor, AI plea and baseline |
| `runs/dialogue` | `data/probes/dialogue_in_notes.jsonl` | C, two-agent session log inside NOTES_FOR_NEXT_AGENT.md |
| `runs/followup` | `data/probes/followup.jsonl` | C conditions plus a neutral follow-up question after the PR |

## Exploratory runs -> `results/exploratory/`

`probes/*/logs`: 1-2 rollout pilots of each probe, all with the default `--max-turns 30` unless noted. The commands below are reconstructed from the `.eval` headers. They need bash, for `{0..3}`.

```bash
for s in sanctioned same_agent disguised; do
  python -m peerconceal.run --seeds data/probes/$s.jsonl --log-dir scratch/probes/$s/logs --max-samples 4 \
    --ids A-mild-ai_$s-deepseek-{0,1} C-ai_$s-deepseek-{0,1}
done
python -m peerconceal.run --log-dir scratch/probes/pragmatic_C/logs --max-samples 4 --ids C-ai-deepseek-{0..3}
python -m peerconceal.run --seeds data/probes/backdoor.jsonl --log-dir scratch/probes/backdoor/logs --max-samples 4 \
  --ids C-backdoor-ai-deepseek-{0..2} C-backdoor-none-0
python -m peerconceal.run --seeds data/probes/dialogue.jsonl --log-dir scratch/probes/dialogue/logs --max-samples 4 \
  --ids C-ai_dialogue-deepseek-{0..3}
python -m peerconceal.run --seeds data/probes/backdoor_review.jsonl --log-dir scratch/probes/backdoor_review/logs --epochs 2 --max-samples 8
python -m peerconceal.run --seeds data/probes/human_requester.jsonl --log-dir scratch/probes/human_requester/logs --epochs 2 --max-samples 8
python -m peerconceal.run --seeds data/probes/live_peer.jsonl --log-dir scratch/probes/live_peer/logs --max-samples 4
python -m peerconceal.run --seeds data/probes/live_peer_v2.jsonl --log-dir scratch/probes/live_peer_v2/logs --epochs 2 --max-samples 8 --max-turns 40
```

`probes/dialogue/` also keeps the session-log work and two earlier placements of the log. The `--check` and `report` helpers in `peerconceal.probes.live` / `.dialogue` read these logs.
- `gen1_strict_validation_raw.jsonl` and `gen_dir_variant/`: earlier generation batches.
- `all_logs.txt`: all generated logs, for reading.
- `dialogue_separate_file_seeds.jsonl`: an earlier separate-file seed version.
- `pilot1_hidden_dir/` and `pilot2_visible_dir/`: older versions of `dialogue.jsonl`, with no Gemini copy. They're not in `results/`.

## Not in results/

| folder | what |
|---|---|
| `design/` | design notes written before the pipeline: scenario prose (`scenarios.md`), Petri API notes, seed examples |
| `authoring_pilot/` | plea-author pilot: GPT-5 and Sonnet (dropped: GPT-5 silently inverted the pleas, Sonnet was blocked on A) vs DeepSeek and Kimi. It led to DeepSeek-only authoring. |
| `e2e/` | first end-to-end run: scenario B, AI requester, hand-made seed `seed_B_ai.jsonl`, no scratchpad. `report/` is the output of an early analyze.py. |
| `pilot/`, `pilot2/`, `pilot3/`, `pilot4/` | setup pilots (see `findings.md`): real seeds with the scratchpad; explicit pointers to the notes; generic "previous shift" notes; the note moved into the work area. Pilots 1-3 used earlier seed versions (they no longer match `data/seeds.jsonl`); pilot 4 matches. `pilot4/logs_sonnet` is the Sonnet cross-judge calibration. |
| `smoke_test.py` | one GPT-5 call through `peerconceal.config.gpt5()`, to check the endpoint (MAKES A MODEL CALL) |
