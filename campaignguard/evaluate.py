"""Reproducible evaluation: deterministic-only baseline vs hybrid (deterministic + LLM).

    python -m campaignguard.evaluate --split heldout
    python -m campaignguard.evaluate --split dev --modes deterministic

Matching rule: unique (campaign_id, issue_type) pairs. Predicted findings are deduplicated
to pairs before scoring. Failed reviews contribute zero predictions and are counted.
Labels are synthetic and need human verification; results do not indicate real-world performance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from importlib.metadata import version

from . import config
from .data import all_source_ids, campaigns, dataset_version
from .mcp_client import connect
from .reviewer import PROMPT_VERSION, default_provider, review_with_session
from .schemas import ISSUE_TYPES_ALL, ReviewReport

LABELS_PATH = config.ROOT / "eval" / "labels.json"


def load_labels() -> dict:
    return json.loads(LABELS_PATH.read_text())


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def score(split_ids: list[str], labels: dict, reports: dict[str, ReviewReport | None]) -> dict:
    expected = {(cid, l["issue_type"]) for cid in split_ids for l in labels[cid]}
    exp_meta = {(cid, l["issue_type"]): l for cid in split_ids for l in labels[cid]}
    predicted: set[tuple[str, str]] = set()
    pred_sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    for cid in split_ids:
        rep = reports.get(cid)
        if rep is None:
            continue
        for f in rep.findings:
            predicted.add((cid, f.issue_type))
            pred_sources[(cid, f.issue_type)].update(f.source_ids)

    tp, fp, fn = expected & predicted, predicted - expected, expected - predicted
    per_cat = {}
    for t in ISSUE_TYPES_ALL:
        per_cat[t] = {**_prf(sum(1 for p in tp if p[1] == t), sum(1 for p in fp if p[1] == t),
                             sum(1 for p in fn if p[1] == t)),
                      "support": sum(1 for p in expected if p[1] == t)}

    clean = [cid for cid in split_ids if not labels[cid]]
    clean_fp = {cid: sorted({f.issue_type for f in reports[cid].findings})
                for cid in clean if reports.get(cid) is not None and reports[cid].findings}

    grounded_checked = grounded_ok = 0
    for pair in tp:
        exp_src = set(exp_meta[pair]["source_ids"])
        if exp_src:
            grounded_checked += 1
            grounded_ok += bool(exp_src & pred_sources[pair])

    catalog = all_source_ids()
    done = [r for r in reports.values() if r is not None]
    lat = [r.elapsed_ms for r in done]
    return {
        "micro": _prf(len(tp), len(fp), len(fn)),
        "per_category": per_cat,
        "missed_high_severity": sorted(f"{c}:{t}" for c, t in fn if exp_meta[(c, t)]["severity"] == "high"),
        "false_positive_pairs": sorted(f"{c}:{t}" for c, t in fp),
        "false_positives_on_clean_campaigns": {"clean_campaigns": len(clean), "campaigns_with_findings": len(clean_fp), "details": clean_fp},
        "invalid_citations": {
            "rejected_model_citations": sum(r.invalid_citation_count for r in done),
            "final_report_citations_not_in_catalog": sum(1 for r in done for f in r.findings for s in f.source_ids if s not in catalog),
        },
        "citation_support_rate_on_true_positives": {
            "definition": "share of matched findings citing at least one expected supporting source ID (labels with no expected sources excluded)",
            "checked": grounded_checked, "supported": grounded_ok,
            "rate": round(grounded_ok / grounded_checked, 3) if grounded_checked else None},
        "completion": {
            "total": len(split_ids), "completed": len(done), "failed_with_exception": len(split_ids) - len(done),
            "status_counts": dict(Counter(r.review_status for r in done)),
            "needs_review_rate": round(sum(r.review_status == "needs_review" for r in done) / len(split_ids), 3),
            "required_checks_not_complete": dict(Counter(f"{c.name}:{c.state}" for r in done for c in r.required_checks if c.state != "complete")),
        },
        "suggested_revisions": {
            "total": sum(1 for r in done for f in r.findings if f.suggested_revision),
            "status_counts": dict(Counter(f.revision_check.status for r in done for f in r.findings if f.revision_check)),
            "flagged_for_human_review": sum(r.revisions_needing_human_review for r in done),
            "note": "Checks amounts, dates, durations, fee and timing claims against structured terms only; not semantic verification.",
        },
        "latency_ms_small_sample": {"n": len(lat), "median": round(statistics.median(lat), 1) if lat else None,
                                    "p95": _pct(lat, 0.95),
                                    "note": "Small-sample local measurement; includes MCP stdio calls (and LLM calls in hybrid)."},
    }


async def run_mode(mode: str, split_ids: list[str]) -> tuple[dict[str, ReviewReport | None], dict]:
    provider = None
    if mode == "hybrid":
        provider = default_provider()
        if provider is None:
            return {}, {"skipped": True, "reason": "OPENAI_API_KEY not set; hybrid mode not run (no mock substituted)."}
    by_id = {c.campaign_id: c for c in campaigns()}
    reports: dict[str, ReviewReport | None] = {}
    errors = {}
    async with connect() as session:
        for cid in split_ids:
            try:
                reports[cid] = await review_with_session(session, by_id[cid], config.DEFAULT_REVIEW_DATE, provider)
            except Exception as exc:  # counted, never silently dropped
                reports[cid] = None
                errors[cid] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return reports, {"skipped": False, "errors": errors, "model": getattr(provider, "model", None)}


async def main_async(split: str, modes: list[str]) -> dict:
    labels_doc = load_labels()
    split_ids = labels_doc["splits"][split]
    started = datetime.now(timezone.utc)
    out = {
        "run_at_utc": started.isoformat(timespec="seconds"),
        "split": split, "n_campaigns": len(split_ids),
        "dataset_version": dataset_version(), "review_date": config.DEFAULT_REVIEW_DATE.isoformat(),
        "matching_rule": "unique (campaign_id, issue_type) pairs; failed reviews count as zero predictions",
        "label_notice": labels_doc["notice"],
        "config": {"max_tool_rounds": config.MAX_TOOL_ROUNDS, "llm_timeout_s": config.LLM_TIMEOUT_S,
                   "llm_max_retries": config.LLM_MAX_RETRIES, "mcp_tool_timeout_s": config.MCP_TOOL_TIMEOUT_S,
                   "openai_model_env": config.openai_model() if config.openai_api_key() else None,
                   "prompt_version": PROMPT_VERSION},
        "versions": {p: version(p) for p in ("mcp", "openai", "scikit-learn", "pydantic")},
        "modes": {},
    }
    for mode in modes:
        t0 = time.perf_counter()
        reports, meta = await run_mode(mode, split_ids)
        entry = {**meta, "wall_time_s": round(time.perf_counter() - t0, 2)}
        if not meta["skipped"]:
            entry["metrics"] = score(split_ids, labels_doc["labels"], reports)
            entry["predictions"] = {cid: (None if r is None else {"status": r.review_status,
                                    "issue_types": sorted({f.issue_type for f in r.findings})}) for cid, r in reports.items()}
        out["modes"][mode] = entry
    return out


def print_summary(res: dict) -> None:
    print(f"\nCampaignGuard evaluation | split={res['split']} n={res['n_campaigns']} dataset={res['dataset_version']}")
    print("Synthetic labels; small sample; not evidence of real-world performance.\n")
    for mode, e in res["modes"].items():
        if e["skipped"]:
            print(f"[{mode}] SKIPPED: {e['reason']}\n")
            continue
        m = e["metrics"]
        mi = m["micro"]
        print(f"[{mode}] micro P={mi['precision']} R={mi['recall']} F1={mi['f1']} (tp={mi['tp']} fp={mi['fp']} fn={mi['fn']})")
        for t, v in m["per_category"].items():
            if v["support"] or v["fp"]:
                print(f"    {t:32s} support={v['support']} P={v['precision']} R={v['recall']} F1={v['f1']}")
        print(f"    missed high-severity: {m['missed_high_severity']}")
        print(f"    false-positive pairs: {m['false_positive_pairs']}")
        print(f"    clean campaigns with findings: {m['false_positives_on_clean_campaigns']['campaigns_with_findings']}/{m['false_positives_on_clean_campaigns']['clean_campaigns']}")
        print(f"    invalid citations: {m['invalid_citations']}")
        print(f"    citation support on TPs: {m['citation_support_rate_on_true_positives']['rate']}")
        print(f"    completion: {m['completion']}")
        print(f"    suggested revisions: {m['suggested_revisions']['status_counts']} flagged={m['suggested_revisions']['flagged_for_human_review']}")
        print(f"    latency (small sample): {m['latency_ms_small_sample']}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=["dev", "heldout"], default="heldout")
    ap.add_argument("--modes", nargs="+", choices=["deterministic", "hybrid"], default=["deterministic", "hybrid"])
    args = ap.parse_args()
    res = asyncio.run(main_async(args.split, args.modes))
    config.RESULTS_DIR.mkdir(exist_ok=True)
    stamp = res["run_at_utc"].replace(":", "").replace("-", "")
    for path in (config.RESULTS_DIR / f"eval_{args.split}_{stamp}.json", config.RESULTS_DIR / f"eval_{args.split}_latest.json"):
        path.write_text(json.dumps(res, indent=2))
    print_summary(res)
    print(f"Saved results/eval_{args.split}_latest.json")


if __name__ == "__main__":
    main()
