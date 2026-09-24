"""Streamlit Client (Fig. 04) — talks to the Agent Mesh Service over HTTP,
never to an agent module directly. Two tabs: the demo chat, and the
Human Review Queue's resolve UI.

Run: streamlit run streamlit_app.py
Needs `uvicorn neutail.api:app --port 8000` running separately.
"""

import uuid

import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8000"
DEMO_CUSTOMERS = {"Priya Nair (affluent, Gold, 18mo)": "CUST-PRIYA", "Jordan Lee (value, Bronze, 2mo)": "CUST-JORDAN"}

st.set_page_config(page_title="Neu.Tail", page_icon="🧵", layout="wide")


def api_get(path: str, **params):
    resp = requests.get(f"{API_BASE}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def api_post(path: str, body: dict):
    # 60s, not 30s — a live /chat call routinely takes 15-20s (Opus reasoning
    # over the pre-filtered candidate prompt), and a timeout or connection
    # error here used to raise unhandled, which Streamlit renders as a raw
    # traceback instead of a chat message.
    try:
        resp = requests.post(f"{API_BASE}{path}", json=body, timeout=60)
    except requests.RequestException as exc:
        return {"error": "connection", "detail": str(exc)}
    if resp.status_code >= 400:
        return {"error": resp.status_code, "detail": resp.json().get("detail", resp.text)}
    return resp.json()


def api_health():
    try:
        resp = requests.get(f"{API_BASE}/health", timeout=2)
        return resp.json() if resp.ok else None
    except requests.RequestException:
        return None


chat_tab, review_tab, admin_tab = st.tabs(["💬 Demo chat", "🛡️ Human review queue", "📊 Admin"])

health = api_health()
if health is None:
    st.error(
        f"Can't reach the Agent Mesh Service at {API_BASE}. "
        "Start it first: `uvicorn neutail.api:app --port 8000`"
    )
    st.stop()

with chat_tab:
    st.title("Neu.Tail")
    st.caption("Chat client over the Orchestrator's /chat endpoint (Fig. 02/04)")

    with st.sidebar:
        st.subheader("Session")
        customer_label = st.selectbox("Customer", list(DEMO_CUSTOMERS))
        customer_id = DEMO_CUSTOMERS[customer_label]
        if "session_id" not in st.session_state or st.session_state.get("_customer") != customer_id:
            st.session_state.session_id = f"ui-{customer_id}-{uuid.uuid4().hex[:8]}"
            st.session_state._customer = customer_id
            st.session_state.messages = []
        backend = health["session_backend"]
        badge = "🟥 redis" if backend == "redis" else "🟦 sqlite (fallback)"
        st.text(f"session_id: {st.session_state.session_id}")
        st.caption(f"session memory backend: {badge}")
        if st.button("New session (simulate 'next day')"):
            st.session_state.session_id = f"ui-{customer_id}-{uuid.uuid4().hex[:8]}"
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.subheader("Loyalty (TALLY)")
        loyalty = api_get(f"/customers/{customer_id}/loyalty")
        col_tier, col_points = st.columns(2)
        col_tier.metric("Tier", loyalty["tier"])
        col_points.metric("Points", f"{loyalty['points_balance']:,}")
        st.caption(f"{loyalty['multiplier']}× multiplier · ${loyalty['ytd_spend']:,.2f} YTD spend")

    for role, content in st.session_state.get("messages", []):
        with st.chat_message(role):
            st.markdown(content)

    prompt = st.chat_input("Try: \"show me something for date night\"")
    if prompt:
        st.session_state.messages.append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.spinner("Thinking... (can take 15-20s with a live model call)"):
            result = api_post("/chat", {
                "session_id": st.session_state.session_id,
                "customer_id": customer_id,
                "message": prompt,
            })

        with st.chat_message("assistant"):
            if "error" in result:
                st.error(f"{result['error']}: {result['detail']}")
                reply = f"⚠️ {result['detail']}"
            elif result["type"] == "clarify":
                st.warning(result["message"] + f" (confidence {result['confidence']:.0%})")
                reply = result["message"]
            elif result["type"] == "discovery":
                st.markdown(
                    f"**segment:** `{result['segment']}` · confidence {result['confidence']:.0%} · "
                    f"live model: {result['used_live_model']}"
                )
                cols = st.columns(min(len(result["results"]), 4) or 1)
                for i, r in enumerate(result["results"]):
                    with cols[i % len(cols)]:
                        st.image(r.get("image_url"), use_container_width=True)
                        st.markdown(f"**{r['name']}**")
                        st.caption(f"{r['tier']} · ${r['price']:.2f} · risk {r['return_risk']:.0%}")
                        st.caption(r["reason"])
                reply = f"{len(result['results'])} picks for the {result['segment']} segment"
            elif result["type"] == "fit":
                st.markdown(f"**{result['category']}** — {result['guidance']}")
                st.caption(f"return-risk {result['return_risk']:.0%} · has_history={result['has_history']}")
                reply = result["guidance"]
            elif result["type"] == "service":
                if not result["upsell"]:
                    st.info("Not flagged as upsell-eligible for this customer.")
                    reply = "Not upsell-eligible."
                elif result.get("escalated"):
                    st.warning(f"⏸️ Escalated to Human Review — {result['reason']}")
                    st.markdown(result["offer_text"])
                    reply = f"Escalated (id {result['escalation_id']}): {result['reason']}"
                else:
                    st.success(
                        f"✅ Subscription committed — order {result['order_id']} · "
                        f"+{result['points_earned']} pts (balance {result['points_balance']:,})"
                    )
                    st.markdown(result["offer_text"])
                    reply = f"Committed: {result['order_id']} (+{result['points_earned']} pts)"
            else:
                st.json(result)
                reply = str(result)

        st.session_state.messages.append(("assistant", reply))

    with st.expander("Audit trail (last 10 calls)"):
        for row in api_get("/audit", limit=10)["entries"]:
            status = "✅" if row["allowed"] else "⛔"
            st.text(f"{status} {row['caller']:<16} -> {row['tool']}")

with review_tab:
    st.title("Human Review Queue")
    st.caption("Resolves rows SENTRY wrote to `escalations` — approving completes the paused commit.")

    if st.button("Refresh"):
        st.rerun()

    pending = api_get("/escalations", status="pending")["escalations"]
    if not pending:
        st.info("No pending escalations.")
    for esc in pending:
        with st.container(border=True):
            st.markdown(
                f"**#{esc['escalation_id']}** · {esc['customer_id']} · {esc['kind']} · "
                f"${esc['amount']:.2f} · _{esc['reason']}_"
            )
            st.caption(f"escalated at {esc['created_at']}")
            col_approve, col_deny = st.columns(2)
            if col_approve.button("✅ Approve", key=f"approve-{esc['escalation_id']}"):
                out = api_post(f"/escalations/{esc['escalation_id']}/resolve", {
                    "approve": True, "resolved_by": "streamlit_reviewer",
                })
                st.success(
                    f"Approved — order {out.get('order_id')} · "
                    f"+{out.get('points_earned')} pts (balance {out.get('points_balance')})"
                )
                st.rerun()
            if col_deny.button("❌ Deny", key=f"deny-{esc['escalation_id']}"):
                api_post(f"/escalations/{esc['escalation_id']}/resolve", {
                    "approve": False, "resolved_by": "streamlit_reviewer",
                })
                st.rerun()

    with st.expander("Resolved history"):
        for esc in api_get("/escalations", status=None)["escalations"]:
            if esc["status"] != "pending":
                st.text(f"#{esc['escalation_id']} {esc['status']:<8} {esc['customer_id']} ${esc['amount']:.2f} "
                        f"by {esc['resolved_by']}")

with admin_tab:
    st.title("Business Outcomes")
    st.caption("Computed from the seeded population (GET /admin/outcomes) — not asserted constants. §09")

    if st.button("Refresh outcomes"):
        st.rerun()

    outcomes = api_get("/admin/outcomes")

    pop = outcomes["population"]
    st.subheader("Population")
    cols = st.columns(6)
    for col, (label, key) in zip(cols, [
        ("Customers", "customers"), ("SKUs", "skus"), ("Orders", "orders"),
        ("Returns", "returns"), ("Points issued", "points_issued"), ("Audit rows", "audit_log_rows"),
    ]):
        col.metric(label, f"{pop[key]:,}")

    st.subheader("Return rate — guided vs. baseline")
    rr = outcomes["return_rate"]
    col_a, col_b = st.columns(2)
    col_a.metric(
        "Baseline (no fit guidance)",
        f"{rr['baseline_return_rate']:.1%}" if rr["baseline_return_rate"] is not None else "n/a",
        help=f"{rr['baseline_returns']} returns / {rr['baseline_orders']} orders",
    )
    col_b.metric(
        "Guided (has fit_profile)",
        f"{rr['guided_return_rate']:.1%}" if rr["guided_return_rate"] is not None else "n/a",
        help=f"{rr['guided_returns']} returns / {rr['guided_orders']} orders",
    )
    st.caption(rr["note"])

    st.subheader("Search-to-purchase (conversion proxy)")
    stp = outcomes["search_to_purchase"]
    st.metric(
        "Categories searched that led to a purchase",
        f"{stp['search_to_purchase_rate']:.1%}" if stp["search_to_purchase_rate"] is not None else "n/a",
        help=f"{stp['converted_customer_categories']} / {stp['searched_customer_categories']} "
             "(customer, category) pairs",
    )
    st.caption(stp["note"])

    st.subheader("Upsell (UC4)")
    up = outcomes["upsell"]
    cols = st.columns(4)
    cols[0].metric("Subscriptions committed", up["subscriptions_committed"])
    cols[1].metric("Escalated to review", up["escalations_total"])
    cols[2].metric("Approved", up["escalations_approved"])
    cols[3].metric("Denied", up["escalations_denied"])
