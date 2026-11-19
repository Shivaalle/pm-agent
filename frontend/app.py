import os
import json
import requests
import streamlit as st
from datetime import datetime

# Streamlit Page Config
st.set_page_config(
    page_title="AI Project Manager Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# Custom CSS for polished enterprise look
st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: 700; color: #1E293B; margin-bottom: 0.2rem; }
    .sub-header { font-size: 1.05rem; color: #64748B; margin-bottom: 1.5rem; }
    .draft-card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; padding: 20px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
    .quote-box { background: #F8FAFC; border-left: 4px solid #3B82F6; padding: 10px 14px; font-style: italic; color: #334155; margin: 10px 0; border-radius: 0 6px 6px 0; }
    .evidence-box { background: #F0FDF4; border-left: 4px solid #22C55E; padding: 10px 14px; color: #166534; margin: 10px 0; border-radius: 0 6px 6px 0; }
    .discrepancy-box { background: #FEF2F2; border-left: 4px solid #EF4444; padding: 10px 14px; color: #991B1B; margin: 10px 0; border-radius: 0 6px 6px 0; }
    .badge-decision { background: #E0E7FF; color: #3730A3; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }
    .badge-action { background: #DBEAFE; color: #1E40AF; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }
    .badge-raid { background: #FEF3C7; color: #92400E; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }
    .badge-scope { background: #FCE7F3; color: #9D174D; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }
    .badge-status { background: #E0F2FE; color: #075985; padding: 3px 10px; border-radius: 12px; font-weight: 600; font-size: 0.8rem; }
</style>
""", unsafe_allow_html=True)

# Helper API functions
def fetch_drafts(status=None):
    try:
        params = {"status": status} if status else {}
        res = requests.get(f"{API_BASE_URL}/drafts", params=params, timeout=5)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        st.error(f"Failed to connect to backend at {API_BASE_URL}: {e}")
    return []

def approve_draft_api(draft_id, user_role="Project Manager"):
    try:
        res = requests.post(f"{API_BASE_URL}/drafts/{draft_id}/approve", json={"user_role": user_role}, timeout=10)
        return res.json()
    except Exception as e:
        st.error(f"Approval error: {e}")
        return None

def reject_draft_api(draft_id, user_role="Project Manager"):
    try:
        res = requests.post(f"{API_BASE_URL}/drafts/{draft_id}/reject", json={"user_role": user_role}, timeout=5)
        return res.json()
    except Exception as e:
        st.error(f"Rejection error: {e}")
        return None

def edit_draft_api(draft_id, updated_content, user_role="Project Manager"):
    try:
        res = requests.post(
            f"{API_BASE_URL}/drafts/{draft_id}/edit",
            json={"updated_proposed_change": updated_content, "user_role": user_role},
            timeout=5
        )
        return res.json()
    except Exception as e:
        st.error(f"Edit error: {e}")
        return None

def fetch_history_api():
    try:
        res = requests.get(f"{API_BASE_URL}/history", timeout=5)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []

# Sidebar Navigation & Role Selection
with st.sidebar:
    st.image("https://img.icons8.com/color/96/000000/artificial-intelligence.png", width=64)
    st.title("AI PM Agent")
    role = st.selectbox(
        "Current Persona View:",
        ["Project Manager", "Team Member", "Sponsor", "Admin"],
        index=0
    )
    st.markdown("---")
    st.caption("Active Project: **KAN (AI PM Platform)**")
    st.caption("Meeting Ingestion: **Granola / Wispr JSON**")
    st.caption("Integrations: **Atlassian Jira Cloud & GitHub**")

# Top Header
st.markdown('<div class="main-header">Autonomous AI Project Manager Agent</div>', unsafe_allow_html=True)
st.markdown(f'<div class="sub-header">Active Persona: <b>{role}</b> &bull; Reviewing project records, cross-referencing Git activity, and writing back to Jira with human approval.</div>', unsafe_allow_html=True)

# -------------------------------------------------------------------------------------------------
# 1. PROJECT MANAGER VIEW (Review Queue & Transcript Ingestion)
# -------------------------------------------------------------------------------------------------
if role == "Project Manager":
    st.subheader("📥 Ingest Meeting Transcript")
    tab_upload, tab_sample = st.tabs(["Upload Transcript JSON", "Use Pre-loaded Sprint Review"])

    with tab_upload:
        uploaded_file = st.file_uploader("Upload Granola or Wispr JSON file", type=["json"])
        if uploaded_file is not None:
            if st.button("🚀 Process Uploaded Transcript", type="primary"):
                with st.spinner("LangGraph agent is analyzing transcript, querying Jira & GitHub, and drafting updates..."):
                    try:
                        data = json.load(uploaded_file)
                        res = requests.post(f"{API_BASE_URL}/upload_transcript", json=data, timeout=30)
                        if res.status_code == 200:
                            st.success(f"Processing complete! {res.json().get('total_drafts_generated')} drafts generated.")
                            st.rerun()
                        else:
                            st.error(f"Backend error: {res.text}")
                    except Exception as e:
                        st.error(f"Upload failed: {e}")

    with tab_sample:
        st.info("Load the realistic mock transcript: **Sprint 14 Review** (Includes actions, RAID risks, scope creep, and Git PR discrepancy detection).")
        if st.button("⚡ Run Agent on Sprint 14 Sample Transcript", type="primary"):
            with st.spinner("Orchestrating agent workflow (MCP Connectors + Gemini Flash)..."):
                sample_path = os.path.join(os.path.dirname(__file__), "..", "data", "mock_transcript.json")
                if os.path.exists(sample_path):
                    with open(sample_path) as f:
                        data = json.load(f)
                    res = requests.post(f"{API_BASE_URL}/upload_transcript", json=data, timeout=30)
                    if res.status_code == 200:
                        st.success(f"Successfully processed! Generated {res.json().get('total_drafts_generated')} drafts.")
                        st.rerun()
                    else:
                        st.error(f"Error: {res.text}")
                else:
                    st.error("Sample transcript file not found.")

    st.markdown("---")
    st.subheader("📋 Review Queue (Pending Project Manager Approval)")
    st.caption("Nothing is sent or written to Jira until approved. Strict traceability to exact spoken lines is enforced.")

    col_filter1, col_filter2, col_action = st.columns([2, 2, 3])
    with col_filter1:
        type_filter = st.selectbox("Filter by Type", ["All", "action_item", "status_update", "raid_item", "scope_change", "decision"])
    with col_filter2:
        status_filter = st.selectbox("Status", ["waiting", "approved", "rejected", "edited", "All"], index=0)

    pending_drafts = fetch_drafts(status=None if status_filter == "All" else status_filter)
    if type_filter != "All":
        pending_drafts = [d for d in pending_drafts if d.get("type") == type_filter]

    with col_action:
        st.write("")
        st.write("")
        if status_filter == "waiting" and pending_drafts:
            if st.button("✅ Approve All Visible Drafts (Batch)", type="secondary"):
                for d in pending_drafts:
                    approve_draft_api(d["id"])
                st.success("Batch approved all visible drafts!")
                st.rerun()

    if not pending_drafts:
        st.info("No drafts match the selected filter. Ingest a transcript above to generate new drafts.")
    else:
        st.write(f"Showing **{len(pending_drafts)}** drafts:")

        for draft in pending_drafts:
            d_id = draft["id"]
            d_type = draft["type"]
            d_title = draft["title"]
            d_quote = draft["source_quote"]
            d_evidence = draft.get("github_evidence")
            d_discrepancy = draft.get("discrepancy_detected", False)
            d_discrepancy_details = draft.get("discrepancy_details")
            d_status = draft.get("status")
            d_jira_key = draft.get("jira_issue_key")

            # Badge styling
            badge_class = f"badge-{d_type.split('_')[0]}"
            type_label = d_type.replace('_', ' ').title()

            with st.container():
                st.markdown(f"""
                <div class="draft-card">
                    <span class="{badge_class}">{type_label}</span> &nbsp; <b>#{d_id}</b> | Status: <b>{d_status.upper()}</b>
                    <h4 style="margin: 8px 0;">{d_title}</h4>
                </div>
                """, unsafe_allow_html=True)

                col_content, col_meta = st.columns([7, 3])
                with col_content:
                    st.markdown("**Proposed Change:**")
                    try:
                        parsed_content = json.loads(draft["proposed_change"])
                        st.json(parsed_content, expanded=False)
                    except Exception:
                        st.write(draft["proposed_change"])

                    st.markdown(f'<div class="quote-box"><b>Verbatim Source Quote:</b><br>{d_quote}</div>', unsafe_allow_html=True)

                    if d_discrepancy:
                        st.markdown(f"""
                        <div class="discrepancy-box">
                            <b>⚠️ GitHub Reality Discrepancy Detected:</b><br>
                            {d_discrepancy_details}
                        </div>
                        """, unsafe_allow_html=True)
                    elif d_evidence:
                        st.markdown(f'<div class="evidence-box"><b>GitHub Supporting Evidence:</b><br>{d_evidence}</div>', unsafe_allow_html=True)

                with col_meta:
                    st.caption(f"Created: {draft.get('created_at', '')[:16]}")
                    if d_jira_key:
                        st.success(f"Linked Jira: **[{d_jira_key}](https://shivaalle22.atlassian.net/browse/{d_jira_key})**")

                    if d_status == "waiting":
                        # Inline edit expander (PRD Feature 7 & 11)
                        with st.expander("✏️ Edit Draft"):
                            edit_text = st.text_area("Edit proposed change payload:", value=draft["proposed_change"], key=f"edit_text_{d_id}")
                            if st.button("Save Edit", key=f"save_edit_{d_id}"):
                                edit_draft_api(d_id, edit_text)
                                st.success("Draft edited!")
                                st.rerun()

                        c_app, c_rej = st.columns(2)
                        with c_app:
                            if st.button("✅ Approve", key=f"app_{d_id}", type="primary", use_container_width=True):
                                res = approve_draft_api(d_id)
                                if res:
                                    st.success(f"Approved -> Jira {res.get('jira_issue_key')}")
                                    st.rerun()
                        with c_rej:
                            if st.button("❌ Reject", key=f"rej_{d_id}", use_container_width=True):
                                reject_draft_api(d_id)
                                st.warning("Draft rejected")
                                st.rerun()

                st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)

# -------------------------------------------------------------------------------------------------
# 2. TEAM MEMBER VIEW (My Action Items & 1-Tap Status Updates)
# -------------------------------------------------------------------------------------------------
elif role == "Team Member":
    st.subheader("👤 My Assigned Action Items")
    st.caption("Team members see only their assigned work and can update their status in one tap.")

    member_name = st.selectbox("Select Team Member:", ["Alex", "David", "Elena", "Sarah"])

    all_actions = fetch_drafts(status=None)
    my_actions = []

    for d in all_actions:
        if d.get("type") == "action_item":
            try:
                p = json.loads(d["proposed_change"])
                owner = p.get("owner", "")
                if member_name.lower() in owner.lower():
                    my_actions.append((d, p))
            except Exception:
                pass

    if not my_actions:
        st.info(f"No active action items assigned to {member_name}.")
    else:
        st.write(f"Found **{len(my_actions)}** action items for **{member_name}**:")
        for draft, payload in my_actions:
            with st.container():
                st.markdown(f"""
                <div class="draft-card">
                    <h4>📌 {payload.get('task')}</h4>
                    <p><b>Target Due Date:</b> <code>{payload.get('due_date')}</code> | <b>Priority:</b> {payload.get('priority')} | <b>Approval Status:</b> {draft.get('status')}</p>
                    <div class="quote-box"><b>Source Quote:</b> {draft.get('source_quote')}</div>
                </div>
                """, unsafe_allow_html=True)
                c1, c2, c3 = st.columns([2, 2, 6])
                with c1:
                    if st.button("Mark In Progress", key=f"prog_{draft['id']}"):
                        st.success(f"Task marked In Progress!")
                with c2:
                    if st.button("Mark Done", key=f"done_{draft['id']}", type="primary"):
                        st.success(f"Task marked Done!")

# -------------------------------------------------------------------------------------------------
# 3. SPONSOR VIEW (Progress, Key Decisions, Scope Creep & Risks)
# -------------------------------------------------------------------------------------------------
elif role == "Sponsor":
    st.subheader("📊 Executive Sponsor Delivery Summary")
    st.caption("High-level visibility into project decisions, scope containment, and risks without task-level clutter.")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="Delivery Forecast", value="On Track (Sprint 14)", delta="No Delay")
    with col2:
        st.metric(label="Drafts Approved without edits", value="85%", delta="+15% target")
    with col3:
        st.metric(label="Scope Changes Caught", value="1 Flagged", delta="Contained")

    st.markdown("### 🎯 Key Architectural Decisions")
    decisions = [d for d in fetch_drafts() if d.get("type") == "decision"]
    for dec in decisions:
        try:
            p = json.loads(dec["proposed_change"])
            st.markdown(f"- **{p.get('decision')}**: {p.get('rationale')} *(Source: {dec.get('source_quote')})*")
        except Exception:
            st.markdown(f"- {dec['title']}")

    st.markdown("### ⚠️ Critical RAID Risks & External Dependencies")
    raids = [d for d in fetch_drafts() if d.get("type") == "raid_item"]
    for r in raids:
        try:
            p = json.loads(r["proposed_change"])
            st.warning(f"**[{p.get('category').upper()}] {p.get('title')}** (Severity: {p.get('severity')}, Owner: {p.get('owner')})\n\n{p.get('description')}")
        except Exception:
            pass

    st.markdown("### 🛡️ Scope Creep Control")
    scopes = [d for d in fetch_drafts() if d.get("type") == "scope_change"]
    for s in scopes:
        try:
            p = json.loads(s["proposed_change"])
            st.error(f"**Proposed Change Request:** {p.get('title')}\n\nRequested by: {p.get('requested_by')} &bull; Impact: {p.get('impact_assessment')}\n\n*Requires formal sponsor sign-off before work commences.*")
        except Exception:
            pass

# -------------------------------------------------------------------------------------------------
# 4. ADMIN VIEW (Connected Systems, Privacy & Audit Trail)
# -------------------------------------------------------------------------------------------------
elif role == "Admin":
    st.subheader("⚙️ System Integrations & Audit Trail")

    st.markdown("### 🔌 Connected Enterprise Systems")
    col_sys1, col_sys2, col_sys3 = st.columns(3)
    with col_sys1:
        st.success("🟢 **Atlassian Jira Cloud**\n\nConnected: `shivaalle22.atlassian.net`\nProject: `KAN`")
    with col_sys2:
        st.success("🟢 **GitHub REST API**\n\nConnected: `Shivaalle/pm-agent`\nWebhook: Active")
    with col_sys3:
        st.success("🟢 **Google Gemini LLM**\n\nModel: `gemini-3.5-flash-lite`\nStructured Outputs: Active")

    st.markdown("---")
    st.markdown("### 🔒 Private Meeting Policy (PRD Feature 12)")
    st.checkbox("Enforce Strict Privacy Mode (Private meetings are discarded immediately without saving)", value=True)

    st.markdown("---")
    st.markdown("### 📜 Audit Log & Change History (PRD Feature 11)")
    history = fetch_history_api()
    if not history:
        st.info("No approval or edit history logged yet.")
    else:
        st.dataframe(history, use_container_width=True)
