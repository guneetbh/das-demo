"""Streamlit Client (Fig. 04) — a storefront in front of the Agent Mesh
Service, never an agent module directly. Search bar + result grid +
product detail page, in the shape of a retail site rather than a chat
transcript — the underlying call is still the same /chat request that
routes through the Orchestrator's full intent classification (§03), just
styled as product search instead of a conversation log. Two other tabs:
Human Review Queue, and Admin (business outcomes + the audit trail).

Run: streamlit run streamlit_app.py
Needs `uvicorn neutail.api:app --port 8000` running separately.
"""

import uuid

import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8000"
DEMO_CUSTOMERS = {"Priya Nair (affluent, Gold, 18mo)": "CUST-PRIYA", "Jordan Lee (value, Bronze, 2mo)": "CUST-JORDAN"}
SUGGESTED_SEARCHES = ["date night", "work outfit", "gym essentials", "everyday basics"]
MAX_RECENT_SEARCHES = 8

st.set_page_config(page_title="Neu.Tail", page_icon="🧵", layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="stForm"] input { border-radius: 999px !important; padding: 0.6em 1.2em !important; }
    .ntail-badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.75em;
                   font-weight:600; margin-right:6px; }
    .ntail-badge-premium { background:#f4e4c1; color:#8a6516; }
    .ntail-badge-private_label { background:#dbe9f5; color:#265a82; }
    .ntail-badge-risk-low { background:#dcf3e4; color:#1e7a42; }
    .ntail-badge-risk-mid { background:#fdf0d5; color:#96660e; }
    .ntail-badge-risk-high { background:#fbe1e1; color:#a3312f; }
    .ntail-price { font-size:1.15em; font-weight:700; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- API
def api_get(path: str, **params):
    resp = requests.get(f"{API_BASE}{path}", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def api_post(path: str, body: dict):
    # 60s, not 30s — a live /chat call routinely takes 15-20s (Opus reasoning
    # over the pre-filtered candidate prompt), and a timeout or connection
    # error here used to raise unhandled, which Streamlit renders as a raw
    # traceback instead of an in-page message.
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


# --------------------------------------------------------------------------- small render helpers
def tier_badge(tier: str) -> str:
    label = "Premium" if tier == "premium" else "Value"
    return f'<span class="ntail-badge ntail-badge-{tier}">{label}</span>'


def risk_badge(return_risk: float) -> str:
    level = "low" if return_risk < 0.20 else "mid" if return_risk < 0.35 else "high"
    return f'<span class="ntail-badge ntail-badge-risk-{level}">Return risk {return_risk:.0%}</span>'


def open_product(sku: str) -> None:
    st.query_params["sku"] = sku
    st.rerun()


def close_product() -> None:
    st.query_params.clear()
    st.rerun()


def find_in_last_results(sku: str) -> dict | None:
    """Enrichment (why it was recommended, its return-risk) is only known
    for products from the most recent search — the catalogue lookup below
    always has name/price/tier/image, but not MUSE's reasoning."""
    last = st.session_state.get("last_result")
    if last and last.get("type") == "discovery":
        for r in last["results"]:
            if r["sku"] == sku:
                return r
    return None


# --------------------------------------------------------------------------- session state
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "_customer" not in st.session_state:
    st.session_state._customer = None
if "recent_searches" not in st.session_state:
    st.session_state.recent_searches = []
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "fit_checks" not in st.session_state:
    st.session_state.fit_checks = {}
if "stylist_offer" not in st.session_state:
    st.session_state.stylist_offer = None
if "purchases" not in st.session_state:
    st.session_state.purchases = {}
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None


def run_search(customer_id: str, query: str) -> None:
    with st.spinner("Searching... (can take 15-20s with a live model call)"):
        result = api_post("/chat", {
            "session_id": st.session_state.session_id,
            "customer_id": customer_id,
            "message": query,
        })
    st.session_state.last_result = result
    st.session_state.last_query = query  # the /chat response has no query field of its own
    if query in st.session_state.recent_searches:
        st.session_state.recent_searches.remove(query)
    st.session_state.recent_searches.insert(0, query)
    st.session_state.recent_searches = st.session_state.recent_searches[:MAX_RECENT_SEARCHES]


# --------------------------------------------------------------------------- boot
health = api_health()
if health is None:
    st.error(
        f"Can't reach the Agent Mesh Service at {API_BASE}. "
        "Start it first: `uvicorn neutail.api:app --port 8000`"
    )
    st.stop()

shop_tab, review_tab, admin_tab = st.tabs(["🛍️ Shop", "🛡️ Human Review", "📊 Admin"])

with shop_tab:
    with st.sidebar:
        st.subheader("Account")
        customer_label = st.selectbox("Shopping as", list(DEMO_CUSTOMERS))
        customer_id = DEMO_CUSTOMERS[customer_label]
        if st.session_state._customer != customer_id:
            st.session_state.session_id = f"ui-{customer_id}-{uuid.uuid4().hex[:8]}"
            st.session_state._customer = customer_id
            st.session_state.recent_searches = []
            st.session_state.last_result = None
            st.session_state.fit_checks = {}
            st.session_state.stylist_offer = None
            st.session_state.purchases = {}
            st.query_params.clear()

        backend = health["session_backend"]
        badge = "🟥 redis" if backend == "redis" else "🟦 sqlite (fallback)"
        st.caption(f"session: `{st.session_state.session_id}` · memory: {badge}")
        if st.button("New session (simulate 'next day')"):
            st.session_state.session_id = f"ui-{customer_id}-{uuid.uuid4().hex[:8]}"
            st.session_state.last_result = None
            st.query_params.clear()
            st.rerun()

        st.divider()
        st.subheader("Loyalty")
        loyalty = api_get(f"/customers/{customer_id}/loyalty")
        col_tier, col_points = st.columns(2)
        col_tier.metric("Tier", loyalty["tier"])
        col_points.metric("Points", f"{loyalty['points_balance']:,}")
        st.caption(f"{loyalty['multiplier']}× multiplier · ${loyalty['ytd_spend']:,.2f} YTD spend")

    st.title("🧵 Neu.Tail")

    if st.session_state.pending_query:
        run_search(customer_id, st.session_state.pending_query)
        st.session_state.pending_query = None

    with st.form("search_form", clear_on_submit=False):
        col_input, col_button = st.columns([5, 1])
        query = col_input.text_input(
            "Search", placeholder="Search for date night looks, ask about fit, or request styling help...",
            label_visibility="collapsed",
        )
        submitted = col_button.form_submit_button("🔍 Search", use_container_width=True)
    if submitted and query:
        run_search(customer_id, query)

    st.divider()

    selected_sku = st.query_params.get("sku")
    product = None
    if selected_sku:
        try:
            product = api_get(f"/catalogue/{selected_sku}")
        except requests.HTTPError:
            st.error(f"No such product: {selected_sku}")

    # Deliberately if/elif, never st.stop() — this all lives inside the shop
    # tab's `with` block, and st.stop() halts the *entire* script in one
    # Streamlit run, which would have silently broken the Human Review and
    # Admin tabs any time a product page or an error was showing.
    if product:
        # ---------------------------------------------------------- product detail
        if st.button("← Back to results"):
            close_product()

        enrichment = find_in_last_results(selected_sku)
        category = product["category"]

        col_img, col_info = st.columns([2, 3])
        with col_img:
            st.image(product.get("image_url"), use_container_width=True)
        with col_info:
            st.markdown(f"## {product['name']}")
            st.markdown(f'<span class="ntail-price">${product["price"]:.2f}</span>', unsafe_allow_html=True)
            badges = tier_badge(product["tier"])
            if enrichment:
                badges += " " + risk_badge(enrichment["return_risk"])
            st.markdown(badges, unsafe_allow_html=True)
            st.caption(f"Category: {category}")
            if enrichment:
                st.info(f"**Why we picked this for you:** {enrichment['reason']}")

            st.divider()

            if selected_sku in st.session_state.purchases:
                order = st.session_state.purchases[selected_sku]
                if "error" in order:
                    st.error(order["detail"])
                elif order.get("escalated"):
                    st.warning(f"⏸️ Order needs a quick review before it ships — {order['reason']}")
                else:
                    st.success(
                        f"✅ Order placed — {order['order_id']} · "
                        f"+{order['points_earned']} pts (balance {order['points_balance']:,})"
                    )
            elif st.button(f"🛒 Buy now — ${product['price']:.2f}", type="primary", use_container_width=True):
                with st.spinner("Placing order..."):
                    # commit_order (CONCIERGE) -> check_payment_policy (SENTRY) -> earn_points (TALLY),
                    # the same chokepoints as every other money-moving action, not a side door.
                    st.session_state.purchases[selected_sku] = api_post(
                        "/tools/commit_order/invoke",
                        {"caller": "concierge_agent", "args": {"customer_id": customer_id, "sku": selected_sku, "quantity": 1}},
                    )
                # Rerun so the sidebar's loyalty panel (rendered earlier in this same
                # script) re-fetches and shows the new balance immediately — without
                # this, the points update wouldn't be visible until some other
                # interaction happened to trigger the next rerun.
                st.rerun()

            col_fit, col_stylist = st.columns(2)

            if col_fit.button("📏 Check my fit", use_container_width=True):
                with st.spinner("Checking..."):
                    st.session_state.fit_checks[category] = api_post(
                        "/tools/get_fit_profile/invoke",
                        {"caller": "tailor_agent", "args": {"customer_id": customer_id, "category": category}},
                    )

            if col_stylist.button("💬 Talk to a stylist", use_container_width=True):
                with st.spinner("Connecting..."):
                    st.session_state.stylist_offer = api_post("/chat", {
                        "session_id": st.session_state.session_id,
                        "customer_id": customer_id,
                        "message": "I have a styling question",
                    })

            if category in st.session_state.fit_checks:
                fit = st.session_state.fit_checks[category]
                if "error" in fit:
                    st.error(fit["detail"])
                else:
                    st.markdown(risk_badge(fit["return_risk"]), unsafe_allow_html=True)
                    st.write(fit["guidance"])

            if st.session_state.stylist_offer:
                offer = st.session_state.stylist_offer
                if "error" in offer:
                    st.error(offer["detail"])
                elif not offer.get("upsell", True):
                    st.info("Not currently eligible for a styling subscription offer.")
                elif offer.get("escalated"):
                    st.warning(f"⏸️ Needs a quick review before it's confirmed — {offer['reason']}")
                    st.write(offer["offer_text"])
                else:
                    st.success(f"✅ Subscription confirmed — +{offer['points_earned']} pts")
                    st.write(offer["offer_text"])

    else:
        # ---------------------------------------------------------------- main / results view
        result = st.session_state.last_result

        if result is None:
            st.subheader("Popular searches")
            cols = st.columns(len(SUGGESTED_SEARCHES))
            for col, suggestion in zip(cols, SUGGESTED_SEARCHES):
                if col.button(suggestion, key=f"suggested-{suggestion}", use_container_width=True):
                    st.session_state.pending_query = suggestion
                    st.rerun()

        elif "error" in result:
            st.error(f"{result['error']}: {result['detail']}")

        elif result["type"] == "clarify":
            st.warning(result["message"])

        elif result["type"] == "discovery":
            last_query = st.session_state.get("last_query", "")
            st.subheader(f"Results for “{last_query}”" if last_query else "Results")
            cols = st.columns(min(len(result["results"]), 4) or 1)
            for i, r in enumerate(result["results"]):
                with cols[i % len(cols)]:
                    with st.container(border=True):
                        st.image(r.get("image_url"), use_container_width=True)
                        st.markdown(f"**{r['name']}**")
                        st.markdown(
                            f'<span class="ntail-price">${r["price"]:.2f}</span> {tier_badge(r["tier"])}',
                            unsafe_allow_html=True,
                        )
                        st.markdown(risk_badge(r["return_risk"]), unsafe_allow_html=True)
                        if st.button("View details →", key=f"view-{r['sku']}", use_container_width=True):
                            open_product(r["sku"])
            with st.expander("🔧 Behind the scenes"):
                st.caption(
                    f"segment: `{result['segment']}` · routing confidence {result['confidence']:.0%} "
                    f"(live: {result.get('intent_live_model')}) · ranking live: {result['used_live_model']}"
                )

        elif result["type"] == "fit":
            st.markdown(f"### {result['category'].title()} fit guidance")
            st.markdown(risk_badge(result["return_risk"]), unsafe_allow_html=True)
            st.write(result["guidance"])

        elif result["type"] == "service":
            if not result["upsell"]:
                st.info("Not currently eligible for a styling subscription offer.")
            elif result.get("escalated"):
                st.warning(f"⏸️ Your offer needs a quick review before it's confirmed — {result['reason']}")
                st.write(result["offer_text"])
            else:
                st.success(f"✅ Subscription confirmed — +{result['points_earned']} pts")
                st.write(result["offer_text"])

        else:
            st.json(result)

        # ------------------------------------------------------------ recent searches footer
        if st.session_state.recent_searches:
            st.divider()
            st.caption("Recent searches")
            chip_cols = st.columns(min(len(st.session_state.recent_searches), 6) or 1)
            for i, past_query in enumerate(st.session_state.recent_searches):
                with chip_cols[i % len(chip_cols)]:
                    if st.button(past_query, key=f"recent-{i}-{past_query}", use_container_width=True):
                        st.session_state.pending_query = past_query
                        st.rerun()

with review_tab:
    st.title("Human Review Queue")
    st.caption("Resolves rows SENTRY wrote to `escalations` — approving completes the paused commit.")

    if st.button("Refresh", key="review-refresh"):
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

    if st.button("Refresh outcomes", key="admin-refresh"):
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

    st.divider()
    st.subheader("Audit trail")
    st.caption("Every tool call and model call, policy-checked (§05) — moved here from the shop view.")
    audit_limit = st.slider("Rows", 5, 100, 20)
    for row in api_get("/audit", limit=audit_limit)["entries"]:
        status = "✅" if row["allowed"] else "⛔"
        st.text(f"{status} {row['caller']:<16} -> {row['tool']:<28} {row['detail'] or ''}")
