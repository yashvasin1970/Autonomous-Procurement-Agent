"""Streamlit front end for the Autonomous Procurement Agent.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from procurement_agent.agent import STATUS_LABELS, run_procurement
from procurement_agent.approval import record_decision
from procurement_agent.constraints import fmt
from procurement_agent.extractor import DEFAULT_MODEL
from procurement_agent.report import render_markdown, run_to_json

ROOT = Path(__file__).parent
SAMPLE = ROOT / "sample_data"
STEP_ICON = {"ok": "✅", "warn": "⚠️", "error": "❌"}
CHECK_ICON = {"PASS": "✅", "FAIL": "❌", "UNKNOWN": "❓"}


def sample_documents() -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted((SAMPLE / "quotes").iterdir()) if p.is_file()]


st.set_page_config(page_title="Procurement Agent", page_icon="🧾", layout="wide")
st.title("Autonomous Procurement Agent")
st.caption("Requirement → quotations → compliant, evidence-backed recommendation → human approval")

with st.sidebar:
    st.subheader("Engine")
    llm_configured = bool(os.environ.get("ANTHROPIC_API_KEY"))
    use_llm = st.checkbox("Use Claude for extraction", value=llm_configured, disabled=not llm_configured)
    if llm_configured:
        st.caption(f"Model: {os.environ.get('PROCUREMENT_MODEL', DEFAULT_MODEL)}")
    else:
        st.caption("No ANTHROPIC_API_KEY set, so rule-based extraction is used.")
    st.caption("Consequential actions always wait for a named human approver.")

if "use_sample" not in st.session_state:
    st.session_state.use_sample = False

left, right = st.columns(2)
with left:
    st.subheader("1 · Purchase request")
    if st.button("Load sample scenario (20 laptops, 5 vendors)"):
        st.session_state.req_text = (SAMPLE / "requirement.txt").read_text(encoding="utf-8")
        st.session_state.use_sample = True
    requirement_text = st.text_area(
        "Plain-language requirement", key="req_text", height=220,
        placeholder="Describe what you need, with measurable limits: processor, RAM, storage, budget, warranty, delivery.")

with right:
    st.subheader("2 · Vendor quotations")
    uploads = st.file_uploader(
        "Upload quotations", type=["pdf", "xlsx", "xlsm", "txt", "md", "csv", "eml"], accept_multiple_files=True)
    if uploads:
        st.caption(f"Using {len(uploads)} uploaded file(s).")
    elif st.session_state.use_sample:
        st.caption("Using the sample quotations (5 files).")
    else:
        st.caption("No quotations yet. Upload files or load the sample scenario.")

if st.button("Run agent", type="primary"):
    docs = [(f.name, f.getvalue()) for f in uploads] if uploads else (
        sample_documents() if st.session_state.use_sample else [])
    if not requirement_text.strip():
        st.error("Add a requirement first.")
    elif not docs:
        st.error("Upload quotations or load the sample scenario.")
    else:
        with st.spinner("Agent working: reading, checking, verifying..."):
            st.session_state.run = run_procurement(requirement_text, docs, use_llm=use_llm)
        st.session_state.decision = None

run = st.session_state.get("run")
if run is None:
    st.info("Load the sample scenario, or enter your own request and quotations, then run the agent.")
    st.stop()

banner = {"AWAITING_APPROVAL": st.success, "NEEDS_CLARIFICATION": st.warning, "NO_COMPLIANT_QUOTE": st.error}[run.status]
banner(f"**{STATUS_LABELS[run.status]}**  ·  run `{run.run_id}`")

passed = sum(a.verdict.value == "PASS" for a in run.assessments)
review = sum(a.verdict.value == "NEEDS_REVIEW" for a in run.assessments)
failed = sum(a.verdict.value == "FAIL" for a in run.assessments)
m1, m2, m3, m4 = st.columns(4)
m1.metric("Quotations read", len(run.assessments))
m2.metric("Pass every mandatory check", passed)
m3.metric("Need clarification", review)
m4.metric("Fail a requirement", failed)

rows = []
for i, a in enumerate(run.assessments, 1):
    v = a.quote.values
    rows.append({
        "Rank": i,
        "Vendor": a.vendor,
        "Verdict": a.verdict.value,
        "Score": a.score,
        "Unit price": fmt("unit_price", v.get("unit_price")),
        "Processor": fmt("cpu", v.get("cpu")),
        "RAM": fmt("ram_gb", v.get("ram_gb")),
        "Storage": fmt("storage_gb", v.get("storage_gb")),
        "Warranty": fmt("warranty_months", v.get("warranty_months")),
        "Delivery": fmt("delivery_days", v.get("delivery_days")),
        "Flags": "Yes" if a.flags else "",
    })
st.dataframe(pd.DataFrame(rows), hide_index=True)

if run.recommendation is not None:
    r = run.recommendation
    st.info(f"**Recommended: {r.vendor}**, score {r.score}/100. " + " ".join(r.notes))

tab_trace, tab_evidence, tab_verify, tab_open, tab_approval = st.tabs(
    ["Agent trace", "Evidence", "Verification", "Open items", "Approval"])

with tab_trace:
    for s in run.trace:
        st.markdown(f"{STEP_ICON[s.status]} **{s.stage}** · {s.title}")
        if s.detail:
            st.caption(s.detail)

with tab_evidence:
    for a in run.assessments:
        with st.expander(f"{a.vendor} · {a.verdict.value}", expanded=a is run.recommendation):
            for c in a.checks:
                st.markdown(f"{CHECK_ICON[c.status]} {c.reason}")
                snippet = a.quote.evidence.get(c.key)
                if snippet:
                    st.caption(f"Source: \"{snippet}\"")
            for flag in a.flags:
                st.warning(f"Anomaly: {flag}")
            st.caption(f"Extraction method: {a.quote.method}")
            for note in a.quote.notes:
                st.caption(note)

with tab_verify:
    for v in run.verification:
        st.markdown(f"{'✅' if v.passed else '❌'} **{v.name}**")
        st.caption(v.detail)

with tab_open:
    if run.clarifications:
        for item in run.clarifications:
            st.markdown(f"- {item}")
    else:
        st.write("Nothing open. Every quotation was fully resolved.")

with tab_approval:
    if run.status != "AWAITING_APPROVAL":
        st.info("No approval is needed yet. Resolve the open items and run the agent again.")
    else:
        st.text_area("Drafted vendor message (held, not sent)", run.draft_message, disabled=True, height=170)
        approver = st.text_input("Approver name", key="approver")
        comment = st.text_input("Comment (optional)", key="approval_comment")
        b1, b2 = st.columns(2)
        if b1.button("Approve recommendation", type="primary"):
            try:
                st.session_state.decision = record_decision(run, "APPROVED", approver, comment)
            except ValueError as exc:
                st.error(str(exc))
        if b2.button("Reject"):
            try:
                st.session_state.decision = record_decision(run, "REJECTED", approver, comment)
            except ValueError as exc:
                st.error(str(exc))
    if st.session_state.get("decision"):
        d = st.session_state.decision
        st.success(f"Recorded: {d['decision']} by {d['approver']} at {d['timestamp']} (audit log updated).")

st.divider()
d1, d2 = st.columns(2)
d1.download_button("Download evidence report (.md)", render_markdown(run),
                   file_name=f"procurement_{run.run_id}.md", mime="text/markdown")
d2.download_button("Download run record (.json)", run_to_json(run),
                   file_name=f"procurement_{run.run_id}.json", mime="application/json")
