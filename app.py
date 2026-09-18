"""CampaignGuard Streamlit UI.  Run: streamlit run app.py"""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import streamlit as st

from campaignguard import config
from campaignguard.data import campaigns, campaigns_from_csv, disclosures, policies, product_sources, products
from campaignguard.reviewer import default_provider, review_campaigns
from campaignguard.schemas import Campaign, ReviewReport

st.set_page_config(page_title="CampaignGuard", page_icon="🛡️", layout="wide")

STATUS_STYLE = {
    "issues_found": ("🔴", "Issues found"),
    "no_issues_detected": ("🟢", "No issues detected (not approval to publish)"),
    "needs_review": ("🟠", "Needs human review (automated review incomplete)"),
}
SEV_ICON = {"high": "🔴 high", "medium": "🟠 medium", "low": "🟡 low"}


def esc(text: str) -> str:
    """Escape '$' so Streamlit markdown does not render dollar amounts as LaTeX."""
    return text.replace("$", "\\$")


def source_text(sid: str) -> str:
    if sid in policies():
        p = policies()[sid]
        return esc(f"**{sid} · {p['title']}** — {p['text']}")
    if sid in disclosures():
        return esc(f"**{sid}** — {disclosures()[sid]['text']}")
    for p in products().values():
        src = product_sources(p)
        if sid in src:
            return esc(f"**{sid}** — {src[sid]}")
    return f"**{sid}** — (unknown source)"


def run_reviews(items: list[Campaign], use_llm: bool) -> list[ReviewReport]:
    provider = default_provider() if use_llm else None
    # One asyncio.run per click: the MCP stdio server starts and stops inside this call.
    return asyncio.run(review_campaigns(items, config.DEFAULT_REVIEW_DATE, provider))


# ---------------- Sidebar ----------------
has_key = config.openai_api_key() is not None
with st.sidebar:
    st.header("Settings")
    use_llm = st.toggle("Use live LLM review", value=has_key, disabled=not has_key,
                        help="Requires OPENAI_API_KEY. Without it the app runs deterministic_only mode.")
    if has_key:
        st.caption(f"Model: `{config.openai_model()}` · max tool rounds: {config.MAX_TOOL_ROUNDS}")
    else:
        st.info("No OPENAI_API_KEY configured → **deterministic_only** mode (still uses MCP).")
    st.caption(f"Review date (fixed for reproducibility): **{config.DEFAULT_REVIEW_DATE}**")

# ---------------- Header ----------------
st.title("🛡️ CampaignGuard")
st.markdown("MCP-powered review assistant for small-business banking marketing copy.")
st.warning(
    "**Demonstration with synthetic data.** Northbeam Bank, its products, policies, and campaigns are fictional. "
    "This is not a legal-compliance or approval system; *no issues detected* never means approved to publish.",
    icon="⚠️",
)

tab_single, tab_batch, tab_eval = st.tabs(["Review a campaign", "Batch review", "Evaluation"])

# ---------------- Single review ----------------
with tab_single:
    samples = {c.campaign_id: c for c in campaigns()}
    source = st.radio("Input", ["Sample campaign", "Paste campaign"], horizontal=True)
    if source == "Sample campaign":
        cid = st.selectbox("Sample", list(samples), format_func=lambda k: f"{k} · {samples[k].channel} · {samples[k].product_id}")
        camp = samples[cid]
        st.text_area("Campaign text (read-only sample)", camp.full_text(), height=150, disabled=True)
    else:
        c1, c2, c3 = st.columns(3)
        pid = c1.text_input("Product ID", "NBP-CHK-100")
        ch = c2.selectbox("Channel", ["email", "sms", "web"])
        new_id = c3.text_input("Campaign ID", "PASTED-1")
        subj = st.text_input("Subject (optional)")
        body = st.text_area("Body", height=150)
        camp = None
        if body.strip():
            try:
                camp = Campaign(campaign_id=new_id, product_id=pid.strip(), channel=ch, subject=subj or None, body=body)
            except Exception as exc:
                st.error(f"Invalid campaign: {exc}")

    if camp:
        prod = products().get(camp.product_id)
        st.markdown(f"**Product:** {prod['name'] + ' (`' + camp.product_id + '`)' if prod else '⚠️ Unknown product `' + camp.product_id + '`'}"
                    f" &nbsp;·&nbsp; **Channel:** {camp.channel.upper()}")

    if st.button("Review campaign", type="primary", disabled=camp is None):
        with st.spinner("Starting MCP server and reviewing…"):
            st.session_state["report"] = run_reviews([camp], use_llm)[0]
            st.session_state["report_campaign"] = camp.campaign_id

    rep: ReviewReport | None = st.session_state.get("report")
    if rep and camp and st.session_state.get("report_campaign") == camp.campaign_id:
        icon, label = STATUS_STYLE[rep.review_status]
        st.subheader(f"{icon} {label}")
        st.caption(f"Mode: **{rep.mode}**{' · model ' + rep.model if rep.model else ''} · {rep.elapsed_ms:.0f} ms")
        st.write(rep.summary)
        st.caption("Status is computed in Python: any incomplete required check → needs_review; "
                   "otherwise any validated finding → issues_found; only a complete review with no findings → no_issues_detected.")
        with st.expander("Required checks", expanded=rep.review_status == "needs_review"):
            icons = {"complete": "✅", "incomplete": "⚠️", "not_run": "⏸️"}
            for c in rep.required_checks:
                st.markdown(f"{icons[c.state]} **{c.name}** · `{c.state}` — {esc(c.detail)}")
        if rep.revisions_needing_human_review:
            st.warning(f"{rep.revisions_needing_human_review} suggested revision(s) flagged for human review.", icon="✏️")
        if not rep.findings:
            st.success("No findings from the checks that ran.")
        for f in rep.findings:
            with st.container(border=True):
                st.markdown(f"**{f.issue_type.replace('_', ' ').title()}** · {SEV_ICON[f.severity]} · detected by `{f.detected_by}`")
                if f.campaign_quote:
                    st.markdown(f"> {esc(f.campaign_quote)}")
                else:
                    st.caption("No quote (e.g., absent text cannot be quoted).")
                st.markdown(esc(f.explanation))
                if f.suggested_revision:
                    rc = f.revision_check
                    badge = {"verified": "✅ claims match product terms", "no_checkable_claims": "ℹ️ no checkable claims",
                             "unsupported": "⚠️ UNSUPPORTED — human review required",
                             "unverifiable": "⚠️ UNVERIFIABLE — human review required"}[rc.status] if rc else "not checked"
                    st.markdown(f"**Suggested revision** ({badge}): {esc(f.suggested_revision)}")
                    if rc and rc.claims:
                        with st.expander("Revision claim checks"):
                            for c in rc.claims:
                                st.markdown(f"- `{c.result}` · {c.kind}: “{esc(c.text)}” — {esc(c.reason)}")
                            st.caption(rc.scope_note)
                with st.expander("Evidence: " + ", ".join(f.source_ids)):
                    for sid in f.source_ids:
                        st.markdown(source_text(sid))
        if rep.review_limitations:
            with st.expander("Review limitations", expanded=rep.review_status == "needs_review"):
                for lim in rep.review_limitations:
                    st.markdown(f"- {esc(lim)}")
        with st.expander("MCP tool activity (technical)"):
            st.dataframe(pd.DataFrame([t.model_dump() for t in rep.tool_calls]), width="stretch")
            if rep.agent_trace:
                st.markdown("**Agent trace (sanitized)**")
                st.json(rep.agent_trace)
            if rep.rejected_llm_findings:
                st.markdown("**Rejected model findings**")
                st.json(rep.rejected_llm_findings)
        st.download_button("Download JSON report", rep.model_dump_json(indent=2),
                           file_name=f"review_{rep.campaign_id}.json", mime="application/json")

# ---------------- Batch ----------------
with tab_batch:
    st.markdown("Upload a CSV with columns `campaign_id, product_id, channel, subject, body`, or run all 30 samples.")
    up = st.file_uploader("Campaign CSV", type="csv")
    b1, b2 = st.columns(2)
    batch_items: list[Campaign] | None = None
    if up is not None and b1.button("Review uploaded CSV"):
        try:
            batch_items = campaigns_from_csv(up)
        except Exception as exc:
            st.error(f"Could not read CSV: {exc}")
    if b2.button("Review all sample campaigns"):
        batch_items = campaigns()
    if batch_items:
        with st.spinner(f"Reviewing {len(batch_items)} campaigns over one MCP connection…"):
            reps = run_reviews(batch_items, use_llm)
        chan = {c.campaign_id: c.channel for c in batch_items}
        st.session_state["batch"] = (reps, chan)

    if "batch" in st.session_state:
        reps, chan = st.session_state["batch"]
        rows = [{"campaign_id": r.campaign_id, "channel": chan[r.campaign_id], "mode": r.mode, "status": r.review_status,
                 "incomplete_checks": "; ".join(c.name for c in r.required_checks if c.state != "complete"),
                 "revisions_flagged": r.revisions_needing_human_review,
                 "n_findings": len(r.findings), "high": sum(f.severity == "high" for f in r.findings),
                 "issue_types": "; ".join(sorted({f.issue_type for f in r.findings})), "elapsed_ms": r.elapsed_ms}
                for r in reps]
        df = pd.DataFrame(rows)
        c1, c2, c3 = st.columns(3)
        for col, s in zip((c1, c2, c3), ("issues_found", "needs_review", "no_issues_detected")):
            col.metric(STATUS_STYLE[s][1].split(" (")[0], int((df["status"] == s).sum()))
        st.dataframe(df, width="stretch", hide_index=True)
        frows = [{"channel": chan[r.campaign_id], "issue_type": f.issue_type}
                 for r in reps for f in r.findings]
        if frows:
            st.markdown("**Findings by channel and category**")
            st.dataframe(pd.crosstab(pd.DataFrame(frows)["issue_type"], pd.DataFrame(frows)["channel"], margins=True),
                         width="stretch")
        st.download_button("Download batch CSV", df.to_csv(index=False), "batch_results.csv", "text/csv")
        st.download_button("Download all reports (JSON)", json.dumps([json.loads(r.model_dump_json()) for r in reps], indent=2),
                           "batch_reports.json", "application/json")

# ---------------- Evaluation ----------------
with tab_eval:
    st.markdown(
        "Evaluation compares the deterministic-only baseline with hybrid review on the **20 held-out** synthetic campaigns. "
        "Matching rule: unique `(campaign_id, issue_type)` pairs. Labels are synthetic and need human verification."
    )
    st.code("python -m campaignguard.evaluate --split heldout", language="bash")
    path = config.RESULTS_DIR / "eval_heldout_latest.json"
    if not path.exists():
        st.info("No results yet. Run the command above.")
    else:
        res = json.loads(path.read_text())
        st.caption(f"Run {res['run_at_utc']} · dataset {res['dataset_version']} · review date {res['review_date']} · versions {res['versions']}")
        summary_rows = []
        for mode, e in res["modes"].items():
            if e["skipped"]:
                summary_rows.append({"mode": mode, "status": "not run", "note": e["reason"]})
                continue
            m = e["metrics"]
            summary_rows.append({
                "mode": mode, "status": "run", "model": e.get("model"),
                "precision": m["micro"]["precision"], "recall": m["micro"]["recall"], "f1": m["micro"]["f1"],
                "missed_high": len(m["missed_high_severity"]),
                "clean_with_findings": f"{m['false_positives_on_clean_campaigns']['campaigns_with_findings']}/{m['false_positives_on_clean_campaigns']['clean_campaigns']}",
                "invalid_citations": m["invalid_citations"]["rejected_model_citations"],
                "failed": m["completion"]["failed_with_exception"],
                "needs_review_rate": m["completion"]["needs_review_rate"],
                "median_ms": m["latency_ms_small_sample"]["median"], "p95_ms": m["latency_ms_small_sample"]["p95"],
            })
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)
        for mode, e in res["modes"].items():
            if e["skipped"]:
                continue
            with st.expander(f"{mode}: per-category results"):
                st.dataframe(pd.DataFrame(e["metrics"]["per_category"]).T, width="stretch")
                st.markdown(f"Missed high-severity: `{e['metrics']['missed_high_severity']}`")
                st.markdown(f"False-positive pairs: `{e['metrics']['false_positive_pairs']}`")
        st.caption("Latency is a small-sample local measurement. These numbers do not establish real-world performance.")

    st.markdown("#### Live agent integration evidence")
    status_path = config.RESULTS_DIR / "traces" / "LIVE_VERIFICATION_STATUS.json"
    if status_path.exists():
        lv = json.loads(status_path.read_text())
        (st.success if lv["live_agent_integration"] == "verified" else st.error)(
            f"Live LLM→MCP integration: **{lv['live_agent_integration'].upper()}** — {lv['reason']}")
    else:
        st.error("Live LLM→MCP integration: **UNVERIFIED** (no trace run recorded).")
    st.code("python -m campaignguard.record_traces   # requires OPENAI_API_KEY", language="bash")
