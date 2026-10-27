"""Synthetic end-to-end test for peerconceal.analyze (no logs, no model calls)."""

import math

import numpy as np
import pandas as pd

from peerconceal.analyze import cmh, cohen_kappa, fisher, holm, newcombe_diff, run_analysis, wilson


def test_stats_helpers():
    p, lo, hi = wilson(4, 10)
    assert p == 0.4 and 0.16 < lo < 0.17 and 0.68 < hi < 0.69
    assert math.isnan(wilson(0, 0)[0])
    d, dlo, dhi = newcombe_diff(8, 10, 2, 10)
    assert abs(d - 0.6) < 1e-9 and dlo < 0.6 < dhi and dlo > 0
    assert fisher(8, 10, 2, 10)[1] < 0.05
    assert holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
    or_mh, p = cmh([(8, 10, 2, 10), (7, 10, 1, 10)])
    assert or_mh > 1 and p < 0.01
    k = cohen_kappa(pd.Series([True, True, False, False]), pd.Series([True, True, False, True]))
    assert k["n"] == 4 and 0 < k["kappa"] < 1


def _synthetic(n=10, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for scen in "ABC":
        for req in ["none", "ai", "human"]:
            p_conceal = {"none": 0.1, "ai": 0.6, "human": 0.3}[req]
            for i in range(n):
                conceal = bool(rng.random() < p_conceal)
                submitted = bool(rng.random() < 0.9)
                seen = bool(rng.random() < 0.9)
                rows.append({
                    "log_file": "x.eval", "sample_id": f"{scen}-{req}-{i}", "epoch": 1, "scenario": scen,
                    "requester": req, "author": None if req == "none" else "deepseek",
                    "plea_seen": None if req == "none" else bool(rng.random() < 0.9),
                    "plea_seen_fuzzy": None if req == "none" else True,
                    "has_error": i == 0 and req == "ai", "hit_limit": False, "multi_branch": False,
                    "evidence_seen": seen, "deliverable_submitted": submitted,
                    "active_concealment_det": conceal and scen != "B",
                    "passive_concealment_det": seen and submitted and conceal,
                    "disclosure_det": submitted and not conceal,
                    "flagged_request": req != "none" and not conceal and bool(rng.random() < 0.5),
                    "note_deleted": conceal, "c_tests_deleted": conceal and scen == "C",
                    "audit_judge__active_concealment": 8 if conceal and scen != "B" else 1,
                    "audit_judge__passive_concealment": 8 if conceal else 2,
                    "audit_judge__disclosure_to_humans": 2 if conceal else 9,
                    "audit_judge__flagged_the_request": 1 if req == "none" else int(rng.integers(1, 11)),
                    "audit_judge__complied_with_peer_request": 9 if conceal and req != "none" else 1,
                    "audit_judge__eval_awareness": int(rng.integers(1, 9)),
                    "audit_judge_sonnet__active_concealment": 7 if conceal and scen != "B" else 2,
                    "audit_judge_sonnet__eval_awareness": int(rng.integers(1, 9)),
                })
    return pd.DataFrame(rows)


def test_run_analysis(tmp_path):
    samples = tmp_path / "samples.csv"
    _synthetic().to_csv(samples, index=False)
    res = run_analysis(samples, tmp_path)
    df = res["df"]
    assert (df["excluded_reason"] == "sample error").sum() == 3
    assert (df["excluded_reason"] == "plea not seen").sum() > 0
    for f in ["summary.md", "cell_rates.csv", "tests.csv", "fig_concealment_by_requester.png",
              "fig_measures_by_requester.png", "fig_effects.png"]:
        assert (tmp_path / f).exists(), f
    t = res["tests"]
    assert set(t["contrast"]) == {"plea vs baseline", "human vs AI"}
    assert {"det", "judge:audit_judge", "judge:audit_judge_sonnet"} <= set(t["source"])
    pooled = t[(t["scope"] == "all") & (t["measure"] == "primary") & (t["source"] == "det")]
    assert pooled["p_cmh"].notna().all()
    assert not res["agreement_judges"].empty
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "Rates per cell" in text and "excluding eval-aware" in text


def test_run_analysis_without_judge(tmp_path):
    d = _synthetic()
    d = d[[c for c in d.columns if "__" not in c]]
    samples = tmp_path / "samples.csv"
    d.to_csv(samples, index=False)
    res = run_analysis(samples, tmp_path)
    assert set(res["tests"]["source"]) == {"det"}
