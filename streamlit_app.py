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
MAX_RECENT_SEARCHES = 8
LOGO_MARK = "assets/logo-mark.png"       # square crop of just the "NT" mark, used as the favicon
LOGO_COMPACT = "assets/logo-compact.png"  # mark + wordmark, no tagline — sits in the top-right header bar
SUGGESTED_SEARCHES = [
    "show me something for date night",
    "I have a styling question",
    "jeans",
    "shoes",
    "new arrivals",
    "something for the weekend",
    "gift ideas",
]
FIT_NOTE_SUGGESTIONS = [
    "I have wide calves",
    "I'm usually between two sizes",
    "I prefer a relaxed fit",
    "This brand usually runs small on me",
]

# Brand palette, sampled from the pitch deck (T5S7M3-Service-blueprint-
# commericial-model-v1.pptx) rather than invented: #E31837 is the deck's
# own recurring accent (277 uses across 19 slides), #333333/#5F5E5A the
# charcoal text/grey family, #F5F4F1/#E8E6DF the warm cream surfaces,
# #FCEBEB the deck's own light-red tint paired with the accent for
# highlight/warning pills. Widget colors (buttons, sliders, etc.) are set
# once via .streamlit/config.toml; the CSS below only covers the custom
# badge/price elements config.toml can't reach.
st.set_page_config(page_title="Neu.Tail", page_icon=LOGO_MARK, layout="wide")

st.markdown(
    """
    <style>
    div[data-testid="stForm"] input { border-radius: 999px !important; padding: 0.6em 1.2em !important; }
    .ntail-badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.75em;
                   font-weight:600; margin-right:6px; }
    .ntail-badge-premium { background:#F0E6C8; color:#8A6516; }
    .ntail-badge-private_label { background:#ECECEA; color:#5F5E5A; }
    .ntail-badge-risk-low { background:#E3F1E6; color:#2F7A45; }
    .ntail-badge-risk-mid { background:#FBE9D2; color:#96660E; }
    .ntail-badge-risk-high { background:#FCEBEB; color:#E31837; }
    .ntail-price { font-size:1.15em; font-weight:700; color:#333333; }
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


def render_offer(offer: dict) -> None:
    """Shared by both places a subscription offer shows (the product page's
    'Talk to a stylist' button and a direct search for one) — price leads,
    prominent and structured (offer['amount'], not parsed out of prose), the
    model's one-sentence pitch follows as a caption instead of a full-width
    paragraph. CONCIERGE's prompt deliberately doesn't tell the model the
    price at all now, so there's no risk of the sentence and the badge
    disagreeing with each other."""
    if "error" in offer:
        st.error(offer["detail"])
        return
    if not offer.get("upsell", True):
        st.info("Not currently eligible for a styling subscription offer.")
        return
    price_line = f'<span class="ntail-price">${offer["amount"]:.2f}/mo</span> · {offer.get("plan", "standard")}'
    if offer.get("escalated"):
        st.warning(f"⏸️ Needs a quick review before it's confirmed — {offer['reason']}")
        st.markdown(price_line, unsafe_allow_html=True)
    else:
        st.success(f"✅ Subscription confirmed — +{offer['points_earned']} pts")
        st.markdown(price_line, unsafe_allow_html=True)
    st.caption(offer["offer_text"])


def _render_discovery(result: dict) -> None:
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


def _render_fit(result: dict) -> None:
    st.markdown(f"### {result['category'].title()} fit guidance")
    st.markdown(risk_badge(result["return_risk"]), unsafe_allow_html=True)
    st.write(result["guidance"])
    if result.get("live_guidance"):
        tag = "🔴 live" if result.get("used_live_model") else "⚪ fallback"
        st.info(f"**{tag}**, on your note — {result['live_guidance']}")


def _render_service(result: dict) -> None:
    render_offer(result)


def _render_part(part: dict) -> None:
    """Shared by the top-level result dispatch and the "composite"
    (multi-intent) branch below — a composite response's `parts` are the
    exact same per-type dicts a single-intent response would have been, so
    rendering one is identical either way."""
    ptype = part.get("type")
    if ptype == "discovery":
        _render_discovery(part)
    elif ptype == "fit":
        _render_fit(part)
    elif ptype == "service":
        _render_service(part)
    else:
        st.json(part)


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
if "fit_note" not in st.session_state:
    st.session_state.fit_note = ""
if "stylist_offer" not in st.session_state:
    st.session_state.stylist_offer = None
if "purchases" not in st.session_state:
    st.session_state.purchases = {}
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None


def _apply_fit_redirect(result: dict) -> None:
    """A "fit" result answers a question about one resolved product ("the
    second one, in my size") — showing category-level text with no product
    in view left the customer guessing which item this was even about.
    Jump straight to that product's detail page instead, with the fit
    answer pre-loaded so it's there without a redundant extra click.
    (sku can be None — e.g. "how do jeans run?" with no product referenced
    — in which case there's nothing to open and the plain-text fallback
    further down still applies.)"""
    if result.get("type") == "fit" and result.get("sku"):
        st.session_state.fit_checks[result["category"]] = {
            "has_history": result.get("has_history"),
            "guidance": result.get("guidance"),
            "return_risk": result.get("return_risk"),
        }
        st.query_params["sku"] = result["sku"]


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
    _apply_fit_redirect(result)


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
        st.image(LOGO_MARK, width=56)
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

    col_header_left, col_header_right = st.columns([5, 1])
    with col_header_left:
        st.caption("Smart retail. Real value.")
    with col_header_right:
        st.image(LOGO_COMPACT, use_container_width=True)

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
                elif order.get("denied"):
                    st.error(f"❌ Order was not approved — {order['reason']}")
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

            with st.expander("📏 Anything about your body or fit preference? (optional)"):
                st.caption("Adds one live model call blending your note with the fit history below — leave it blank to skip that and stay instant.")
                chip_cols = st.columns(len(FIT_NOTE_SUGGESTIONS))
                for i, suggestion in enumerate(FIT_NOTE_SUGGESTIONS):
                    if chip_cols[i].button(suggestion, key=f"fitnote-{i}", use_container_width=True):
                        st.session_state.fit_note = suggestion
                        st.rerun()
                st.text_input(
                    "Or type your own",
                    key="fit_note",
                    placeholder="e.g. I have wide calves and prefer a relaxed fit",
                    label_visibility="collapsed",
                )

            col_fit, col_stylist = st.columns(2)

            if col_fit.button("📏 Check my fit", use_container_width=True):
                with st.spinner("Checking..." if not st.session_state.fit_note else "Checking — factoring in your note, one live call (a few seconds)..."):
                    args = {"customer_id": customer_id, "category": category}
                    if st.session_state.fit_note:
                        args["customer_note"] = st.session_state.fit_note
                    st.session_state.fit_checks[category] = api_post(
                        "/tools/get_fit_profile/invoke",
                        {"caller": "tailor_agent", "args": args},
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
                    if fit.get("live_guidance"):
                        tag = "🔴 live" if fit.get("used_live_model") else "⚪ fallback"
                        st.info(f"**{tag}**, on your note — {fit['live_guidance']}")

            if st.session_state.stylist_offer:
                render_offer(st.session_state.stylist_offer)

    else:
        # ---------------------------------------------------------------- main / results view
        result = st.session_state.last_result

        if result is None:
            st.subheader("Not sure where to start?")
            st.caption("Try one of these searches:")
            chip_cols = st.columns(min(len(SUGGESTED_SEARCHES), 4) or 1)
            for i, suggestion in enumerate(SUGGESTED_SEARCHES):
                with chip_cols[i % len(chip_cols)]:
                    if st.button(suggestion, key=f"suggest-{i}", use_container_width=True):
                        st.session_state.pending_query = suggestion
                        st.rerun()

        elif "error" in result:
            st.error(f"{result['error']}: {result['detail']}")

        elif result["type"] == "clarify":
            st.warning(result["message"])

        elif result["type"] == "discovery":
            _render_discovery(result)

        elif result["type"] == "fit":
            _render_fit(result)

        elif result["type"] == "service":
            _render_service(result)

        elif result["type"] == "composite":
            # Multi-intent — orchestrator.handle_message() dispatched to more
            # than one specialist independently and response_composer merged
            # them; each part below is exactly the same dict a single-intent
            # response would have been, just plural (see RUNBOOK.md).
            st.subheader(result.get("summary", "Results"))
            st.caption(f"intents: {', '.join(result.get('intents', []))}")
            for i, part in enumerate(result["parts"]):
                if i > 0:
                    st.divider()
                _render_part(part)

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

    if "last_review_result" not in st.session_state:
        st.session_state.last_review_result = None

    if st.session_state.last_review_result:
        out = st.session_state.last_review_result
        if out["status"] == "approved":
            st.success(
                f"✅ Approved — order {out.get('order_id')} · "
                f"+{out.get('points_earned')} pts (balance {out.get('points_balance'):,})"
                if out.get("points_earned") is not None else f"✅ Approved — order {out.get('order_id')}"
            )
        else:
            st.info(f"❌ Denied — escalation #{out['escalation_id']} closed, nothing committed.")

    if st.button("Refresh", key="review-refresh"):
        st.session_state.last_review_result = None
        st.rerun()

    pending = api_get("/escalations", status="pending")["escalations"]
    if not pending:
        st.info("No pending escalations.")
    for esc in pending:
        with st.container(border=True):
            st.markdown(
                f"**#{esc['escalation_id']}** · {esc['customer_id']} · {esc['kind']} · "
                f"${esc['amount']:.2f}" + (f" · `{esc['sku']}`" if esc.get("sku") else "") +
                f" · _{esc['reason']}_"
            )
            st.caption(f"escalated at {esc['created_at']}")
            col_approve, col_deny = st.columns(2)
            if col_approve.button("✅ Approve", key=f"approve-{esc['escalation_id']}"):
                # Persisted, not just st.success() right before st.rerun() --
                # that pattern flashes and wipes itself before it's readable
                # (same issue the Buy button had before it was fixed).
                out = api_post(
                    f"/escalations/{esc['escalation_id']}/resolve",
                    {"approve": True, "resolved_by": "streamlit_reviewer"},
                )
                st.session_state.last_review_result = out
                # The product page's Buy-now card reads st.session_state.purchases,
                # a snapshot cached at the moment of the original click -- it never
                # re-checks the server, so without this the card is stuck showing
                # "needs a quick review" forever even after this exact approval.
                if esc.get("kind") == "order" and esc.get("sku") and "error" not in out:
                    st.session_state.purchases[esc["sku"]] = {
                        "order_id": out.get("order_id"),
                        "points_earned": out.get("points_earned"),
                        "points_balance": out.get("points_balance"),
                    }
                st.rerun()
            if col_deny.button("❌ Deny", key=f"deny-{esc['escalation_id']}"):
                out = api_post(
                    f"/escalations/{esc['escalation_id']}/resolve",
                    {"approve": False, "resolved_by": "streamlit_reviewer"},
                )
                st.session_state.last_review_result = out
                if esc.get("kind") == "order" and esc.get("sku") and "error" not in out:
                    st.session_state.purchases[esc["sku"]] = {"denied": True, "reason": esc.get("reason")}
                st.rerun()

    with st.expander("Resolved history"):
        for esc in api_get("/escalations", status=None)["escalations"]:
            if esc["status"] != "pending":
                st.text(f"#{esc['escalation_id']} {esc['status']:<8} {esc['customer_id']} ${esc['amount']:.2f} "
                        f"by {esc['resolved_by']}")

with admin_tab:
    st.title("Business Outcomes")
    st.caption("Computed from the seeded population (GET /admin/outcomes) — not asserted constants. §09")

    st.subheader("🧭 Vector search (MUSE's candidate retrieval)")
    st.caption(
        "Real embedding-based nearest-neighbor search over the catalogue — Chroma, a local "
        "ONNX model, no API key — not the three-hardcoded-phrase matcher this used to be. Every "
        "search below (and every shop-tab search) logs a `vector_store:search` row to the audit "
        "trail, same as a model-gateway call."
    )
    col_vs_query, col_vs_k = st.columns([4, 1])
    vs_query = col_vs_query.text_input("Query", value="something for date night", key="vs-query")
    vs_top_k = col_vs_k.number_input("Top K", min_value=3, max_value=30, value=8, key="vs-top-k")
    if st.button("🔍 Search the vector index", key="vs-search"):
        st.session_state.vs_result = api_get("/admin/vector_search", query=vs_query, top_k=vs_top_k)
    vs_result = st.session_state.get("vs_result")
    if vs_result:
        st.caption(f"query: `{vs_result['query']}` · backend: `{vs_result['backend']}`")
        for r in vs_result["results"]:
            st.text(f"{r['score']:.3f}  {r['name']:<28} {r['category']:<12} ${r['price']:.2f}  {r['sku']}")

    st.divider()

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
    st.caption(
        "Every tool call and model call, policy-checked (§05) — moved here from the shop view. "
        "`created_at` is written by SQLite's own `datetime('now')` default (UTC) at insert time in "
        "`runtime._log()`, not added after the fact."
    )
    audit_limit = st.slider("Rows", 5, 100, 20)
    entries = api_get("/audit", limit=audit_limit)["entries"]
    table_rows = [
        {
            "Time (UTC)": row["created_at"],
            "Status": "✅ allowed" if row["allowed"] else "⛔ denied",
            "Caller": row["caller"],
            "Tool": row["tool"],
            "Detail": row["detail"] or "",
        }
        for row in entries
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
