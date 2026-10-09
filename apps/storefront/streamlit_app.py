"""Neu.Tail storefront (Streamlit) — a client of the Experience API only.

Shoppers log in as a demo persona (POST /auth/login) and every call carries
their token; the API, not this app, decides whose data a request touches.
The review and admin tabs use staff API keys (X-API-Key).

Run from the repo root: streamlit run apps/storefront/streamlit_app.py   (needs: uvicorn neutail.api:app --port 8000)
"""

import os
import uuid
from pathlib import Path

import requests
import streamlit as st

API_BASE = os.environ.get("NEUTAIL_API_BASE", "http://127.0.0.1:8000")
REVIEWER_KEY = os.environ.get("NEUTAIL_REVIEWER_KEY", "dev-reviewer-key")
ADMIN_KEY = os.environ.get("NEUTAIL_ADMIN_KEY", "dev-admin-key")
ROOT = Path(__file__).resolve().parents[2]  # repo root: assets/ lives there
LOGO_MARK = str(ROOT / "assets" / "logo-mark.png")
LOGO_COMPACT = str(ROOT / "assets" / "logo-compact.png")
CATEGORIES = [("Dresses", "dress"), ("Tops", "top"), ("Jeans", "jeans"), ("Shoes", "shoes"),
              ("Jackets", "jacket"), ("Accessories", "accessories"), ("Athleisure", "athleisure")]
SUGGESTIONS = ["something for date night", "workwear blazers", "wedding guest", "gift ideas", "new arrivals",
               "check my fit for jeans", "I'd like a stylist", "how many points do I have?"]
FIT_NOTES = ["I have wide calves", "I'm between two sizes", "I prefer a relaxed fit", "This category runs small on me"]
RETURN_REASONS = {"Too small": "too_small", "Too large": "too_large", "Not as described": "not_as_described",
                  "Changed my mind": "changed_mind", "Faulty": "defective"}

st.set_page_config(page_title="Neu.Tail — Smart retail. Real value.", page_icon=LOGO_MARK, layout="wide")
st.markdown(
    """
    <style>
    .block-container { padding-top: 3.2rem; }
    div[data-testid="stForm"] input { border-radius: 999px !important; padding: .6em 1.2em !important; }
    .nt-badge { display:inline-block; padding:2px 9px; border-radius:999px; font-size:.72em; font-weight:600;
                margin:0 4px 4px 0; letter-spacing:.01em; }
    .nt-premium { background:#F0E6C8; color:#8A6516; } .nt-private_label { background:#ECECEA; color:#5F5E5A; }
    .nt-sale { background:#E31837; color:#fff; } .nt-new { background:#333; color:#fff; }
    .nt-trend { background:#FCEBEB; color:#E31837; }
    .nt-risk-low { background:#E3F1E6; color:#2F7A45; } .nt-risk-mid { background:#FBE9D2; color:#96660E; }
    .nt-risk-high { background:#FCEBEB; color:#E31837; }
    .nt-price { font-size:1.05em; font-weight:700; color:#333; }
    .nt-was { text-decoration:line-through; color:#9A9893; margin-left:6px; font-size:.9em; }
    .nt-name { font-weight:600; font-size:.95em; line-height:1.25; min-height:2.5em; color:#333; }
    .nt-reason { font-size:.8em; color:#5F5E5A; min-height:2.4em; }
    .nt-hero { background:linear-gradient(90deg,#FCEBEB 0%,#F5F4F1 100%); border-radius:16px; padding:22px 26px;
               margin-bottom:14px; }
    .nt-hero h2 { margin:0 0 4px 0; color:#333; } .nt-hero p { margin:0; color:#5F5E5A; }
    .nt-offer { border:2px solid #E31837; border-radius:14px; padding:16px 18px; background:#FFFDFD; }
    .nt-big { font-size:1.6em; font-weight:800; color:#E31837; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- API client
def _headers(auth: str) -> dict:
    if auth == "shopper" and st.session_state.get("token"):
        return {"Authorization": f"Bearer {st.session_state.token}"}
    if auth == "reviewer":
        return {"X-API-Key": REVIEWER_KEY}
    if auth == "admin":
        return {"X-API-Key": ADMIN_KEY}
    return {}


def api(method: str, path: str, auth: str = "shopper", json: dict | None = None, params: dict | None = None,
        timeout: int = 90, _retried: bool = False):
    try:
        resp = requests.request(method, f"{API_BASE}{path}", headers=_headers(auth), json=json, params=params,
                                timeout=timeout)
    except requests.RequestException as exc:
        return {"error": "connection", "detail": str(exc)}
    if resp.status_code == 401 and auth == "shopper" and not _retried and st.session_state.get("customer_id"):
        login(st.session_state.customer_id)  # token expired or API restarted with a new secret
        return api(method, path, auth, json, params, timeout, _retried=True)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        return {"error": resp.status_code, "detail": detail}
    return resp.json()


def login(customer_id: str) -> None:
    out = requests.post(f"{API_BASE}/auth/login", json={"handle": customer_id}, timeout=10).json()
    st.session_state.token = out["token"]
    st.session_state.customer_id = customer_id
    st.session_state.customer_name = out["name"]


def img(url: str | None) -> str:
    if url and url.startswith("/"):
        local = ROOT / url.lstrip("/")
        return str(local) if local.exists() else f"{API_BASE}{url}"
    return url or LOGO_MARK


def money(value: float | None) -> str:
    return f"£{value:,.2f}" if value is not None else ""


def new_key() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- render helpers
def badge(text: str, cls: str) -> str:
    return f'<span class="nt-badge nt-{cls}">{text}</span>'


def risk_badge(risk: float) -> str:
    level = "low" if risk < 0.2 else "mid" if risk < 0.35 else "high"
    return badge(f"Return risk {risk:.0%}", f"risk-{level}")


def price_html(item: dict) -> str:
    was = item.get("was_price")
    html = f'<span class="nt-price">{money(item["price"])}</span>'
    if was and was > item["price"]:
        html += f'<span class="nt-was">{money(was)}</span>'
    return html


def badges_for(item: dict) -> str:
    out = badge("Premium" if item["tier"] == "premium" else "Value", item["tier"])
    if item.get("was_price") and item["was_price"] > item["price"]:
        out += badge(f"-{round((1 - item['price'] / item['was_price']) * 100)}%", "sale")
    if item.get("is_new"):
        out += badge("New in", "new")
    if item.get("trending"):
        out += badge("Trending", "trend")
    return out


def open_product(sku: str) -> None:
    st.query_params["sku"] = sku
    st.session_state.stylist_offer = None
    st.rerun()


def product_grid(items: list[dict], key: str, cols: int = 4, show_reason: bool = False, show_risk: bool = False) -> None:
    if not items:
        st.caption("Nothing to show here yet.")
        return
    columns = st.columns(cols)
    for i, item in enumerate(items):
        with columns[i % cols]:
            with st.container(border=True):
                st.image(img(item.get("image_url")), width="stretch")
                st.markdown(f'<div class="nt-name">{item["name"]}</div>', unsafe_allow_html=True)
                st.markdown(price_html(item) + "<br>" + badges_for(item), unsafe_allow_html=True)
                if show_risk and "return_risk" in item:
                    st.markdown(risk_badge(item["return_risk"]), unsafe_allow_html=True)
                if show_reason and item.get("reason"):
                    st.markdown(f'<div class="nt-reason">{item["reason"]}</div>', unsafe_allow_html=True)
                if st.button("View", key=f"{key}-{item['sku']}-{i}", width="stretch"):
                    open_product(item["sku"])


def render_offer(offer: dict, key: str) -> None:
    if "error" in offer:
        st.error(offer["detail"])
        return
    if not offer.get("offered"):
        st.info(f"Styling subscriptions aren't available on this account — {offer.get('reason')}.")
        return
    with st.container():
        perks = "".join(f"<li>{p}</li>" for p in offer.get("perks", []))
        st.markdown(
            f'<div class="nt-offer"><b>{offer["plan_name"]}</b> · <span class="nt-big">{money(offer["amount"])}</span>/month'
            f'<p style="margin:.4em 0">{offer["offer_text"]}</p><ul style="margin:0">{perks}</ul></div>',
            unsafe_allow_html=True,
        )
        decision = st.session_state.offer_decisions.get(offer["offer_id"])
        if decision:
            render_subscription(decision)
            return
        c1, c2 = st.columns(2)
        if c1.button(f"Subscribe — {money(offer['amount'])}/mo", key=f"sub-{key}-{offer['offer_id']}", type="primary",
                     width="stretch"):
            idem = st.session_state.idem.setdefault(offer["offer_id"], new_key())
            with st.spinner("Checking payment policy..."):
                st.session_state.offer_decisions[offer["offer_id"]] = api(
                    "POST", f"/offers/{offer['offer_id']}/accept", json={"idempotency_key": idem})
            st.rerun()
        if c2.button("No thanks", key=f"nothanks-{key}-{offer['offer_id']}", width="stretch"):
            st.session_state.offer_decisions[offer["offer_id"]] = {"declined": True}
            st.rerun()
        st.caption(f"Nothing is charged until you subscribe · offer valid {offer.get('expires_in_minutes', 30)} min · "
                   f"pitch by {'live model' if offer.get('used_live_model') else 'template'}")


def render_subscription(result: dict) -> None:
    if result.get("declined"):
        st.caption("No problem — the offer's there if you change your mind.")
    elif "error" in result:
        st.error(result["detail"])
    elif result.get("escalated"):
        st.warning(f"⏸️ A colleague will confirm this shortly — {result.get('reason')}")
    elif result.get("committed"):
        msg = f"✅ You're subscribed — {result['order_id']} · +{result['points_earned']} points"
        if result.get("tier_upgraded_to"):
            msg += f" · 🎉 welcome to {result['tier_upgraded_to']}!"
        st.success(msg)


def render_fit(fit: dict) -> None:
    if "error" in fit:
        st.error(fit["detail"])
        return
    st.markdown(f"**{fit['category'].title()} fit** " + risk_badge(fit["return_risk"]), unsafe_allow_html=True)
    st.write(fit["guidance"].capitalize() + ".")
    if fit.get("recommended_size"):
        st.markdown(f"Recommended size: **{fit['recommended_size']}**")
    ev = fit.get("evidence", {})
    st.caption(f"Based on {ev.get('orders', 0)} orders and {ev.get('returns', 0)} returns in this category · "
               f"category baseline {fit['baseline_return_risk']:.0%}")
    if fit.get("live_guidance"):
        st.info(f"{'🔴 live' if fit.get('used_live_model') else '⚪ template'} · {fit['live_guidance']}")


def render_loyalty(data: dict) -> None:
    c1, c2, c3 = st.columns(3)
    c1.metric("Tier", data["tier"])
    c2.metric("Points", f"{data['points_balance']:,}", help=f"Worth {money(data['voucher_value'])} in vouchers")
    c3.metric("Multiplier", f"{data['multiplier']}×")
    if data.get("next_tier"):
        st.progress(data["tier_progress"], text=f"{money(data['spend_to_next_tier'])} to {data['next_tier']}")
    if data.get("badges"):
        cols = st.columns(len(data["badges"]))
        for col, b in zip(cols, data["badges"]):
            col.markdown(f"{'🏅' if b['earned'] else '▫️'} **{b['label']}**  \n{b['desc']} ({b['progress']}/{b['goal']})")


def render_result(result: dict, key: str = "res") -> None:
    rtype = result.get("type")
    if "error" in result:
        st.error(f"{result['error']}: {result['detail']}")
    elif rtype == "clarify":
        st.info(result["message"])
    elif rtype == "discovery":
        live = " · ranked live" if result.get("used_live_model") else ""
        st.subheader(f"Picked for you{live}")
        product_grid(result["results"], key=f"{key}-disc", show_reason=True, show_risk=True)
    elif rtype == "fit":
        render_fit(result)
        if result.get("sku"):
            if st.button("Open that product →", key=f"{key}-fitopen"):
                open_product(result["sku"])
    elif rtype == "service":
        render_offer(result, key)
    elif rtype == "subscription":
        render_subscription(result)
    elif rtype == "loyalty":
        render_loyalty(result)
    elif rtype == "composite":
        st.subheader(result.get("summary", ""))
        for i, part in enumerate(result["parts"]):
            if i:
                st.divider()
            render_result(part, key=f"{key}-{i}")
    else:
        st.json(result)
    if result.get("trace_id"):
        with st.expander("🔧 Behind the scenes"):
            intents = ", ".join(f"{i['intent']} {i['confidence']:.0%}" + (f" [{i['category']}]" if i.get("category") else "")
                                for i in result.get("intents", []))
            st.caption(f"segment `{result.get('segment')}` / `{result.get('micro_segment')}` · intents: {intents or '—'} · "
                       f"router live: {result.get('intent_live_model')} · trace `{result['trace_id']}` "
                       "(Admin → Trace to follow every agent call)")


# --------------------------------------------------------------------------- state
for name, default in {"token": None, "customer_id": None, "session_id": None, "last_result": None,
                      "last_query": "", "pending_query": None, "fit_checks": {}, "stylist_offer": None,
                      "purchases": {}, "idem": {}, "offer_decisions": {}, "browsed": set(), "reco": None,
                      "return_feedback": None, "review_result": None, "redeem_result": None}.items():
    if name not in st.session_state:
        st.session_state[name] = default

health = api("GET", "/health", auth="none", timeout=3)
if "error" in health:
    st.error(f"Can't reach the Experience API at {API_BASE}. Start it first: `uvicorn neutail.api:app --port 8000`")
    st.stop()

# --------------------------------------------------------------------------- sidebar: account + loyalty
personas = api("GET", "/demo/customers", auth="none").get("customers", [])
with st.sidebar:
    st.image(LOGO_COMPACT, width="stretch")
    labels = {f"{p['name']} · {p['tier']}": p for p in personas}
    choice = st.selectbox("Shopping as", list(labels))
    persona = labels[choice]
    st.caption(persona["persona_blurb"])
    if st.session_state.customer_id != persona["customer_id"]:
        switching = st.session_state.customer_id is not None
        login(persona["customer_id"])
        for k in ("last_result", "stylist_offer", "reco", "return_feedback", "redeem_result"):
            st.session_state[k] = None
        st.session_state.update(session_id=f"ui-{uuid.uuid4().hex[:8]}", fit_checks={}, purchases={}, idem={},
                                offer_decisions={}, browsed=set())
        if switching:  # keep a deep link (?sku=...) on first load
            st.query_params.clear()
    if st.button("New session (simulate 'next day')", width="stretch"):
        st.session_state.update(session_id=f"ui-{uuid.uuid4().hex[:8]}", last_result=None, reco=None)
        st.query_params.clear()
        st.rerun()

    st.divider()
    loyalty = api("GET", "/loyalty")
    if "error" not in loyalty:
        st.markdown(f"### {loyalty['tier']} member")
        st.metric("Points", f"{loyalty['points_balance']:,}", help=f"Worth {money(loyalty['voucher_value'])}")
        if loyalty.get("next_tier"):
            st.progress(loyalty["tier_progress"], text=f"{money(loyalty['spend_to_next_tier'])} to {loyalty['next_tier']}")
        st.caption(f"{loyalty['multiplier']}× points · {money(loyalty['ytd_spend'])} spent this year")
        earned = [b["label"] for b in loyalty.get("badges", []) if b["earned"]]
        if earned:
            st.caption("🏅 " + " · ".join(earned))
        with st.expander("🎁 Redeem points"):
            for reward in loyalty["rewards"]:
                if st.button(f"{reward['label']} — {reward['points']:,} pts", key=f"redeem-{reward['id']}",
                             disabled=not reward["affordable"], width="stretch"):
                    st.session_state.redeem_result = api("POST", "/loyalty/redeem",
                                                         json={"reward_id": reward["id"], "idempotency_key": new_key()})
                    st.rerun()
            rr = st.session_state.redeem_result
            if rr:
                if "error" in rr:
                    st.error(rr["detail"])
                else:
                    st.success(f"Voucher {rr['voucher_code']} — {rr['redeemed']}")
    st.divider()
    st.caption(f"session `{st.session_state.session_id}` · memory **{health['session_backend']}** · "
               f"vectors **{health['vector_backend']}** · models: "
               f"{', '.join(health['live_model_providers']) or 'deterministic fallback'}")

shop_tab, orders_tab, review_tab, admin_tab = st.tabs(["🛍️ Shop", "📦 My orders", "🛡️ Human review", "📊 Admin"])


# --------------------------------------------------------------------------- shop
def run_search(query: str) -> None:
    with st.spinner("Finding the right specialist..."):
        st.session_state.last_result = api("POST", "/chat", json={"session_id": st.session_state.session_id,
                                                                  "message": query})
    st.session_state.last_query = query
    st.query_params.clear()


with shop_tab:
    with st.form("search", clear_on_submit=False):
        c_in, c_btn = st.columns([6, 1])
        query = c_in.text_input("Search", placeholder="Search for an occasion, ask about fit, talk to a stylist, check your points…",
                                label_visibility="collapsed")
        submitted = c_btn.form_submit_button("Search", width="stretch", type="primary")
    if submitted and query:
        run_search(query)
    if st.session_state.pending_query:
        run_search(st.session_state.pending_query)
        st.session_state.pending_query = None

    chip_cols = st.columns(len(CATEGORIES) + 1)
    for col, (label, cat) in zip(chip_cols, CATEGORIES):
        if col.button(label, key=f"cat-{cat}", width="stretch"):
            st.session_state.pending_query = label.lower()
            st.rerun()
    if chip_cols[-1].button("🏠 Home", key="home", width="stretch"):
        st.session_state.last_result = None
        st.query_params.clear()
        st.rerun()

    selected = st.query_params.get("sku")
    if selected:
        product = api("GET", f"/products/{selected}", auth="none")
        if "error" in product:
            st.error(f"No such product: {selected}")
        else:
            if selected not in st.session_state.browsed:
                api("POST", "/events/browse", json={"sku": selected})
                st.session_state.browsed.add(selected)
            if st.button("← Back"):
                st.query_params.clear()
                st.rerun()
            c_img, c_info = st.columns([2, 3], gap="large")
            with c_img:
                st.image(img(product["image_url"]), width="stretch")
            with c_info:
                st.markdown(f"## {product['name']}")
                st.markdown(price_html(product) + "&nbsp;&nbsp;" + badges_for(product), unsafe_allow_html=True)
                st.write(product["description"])
                st.caption(f"Colour: {product['colour']} · Material: {product['material']} · SKU {product['sku']}")
                in_stock = [s for s in product["sizes"] if s["stock"] > 0]
                if not in_stock:
                    st.warning("Sold out in every size.")
                else:
                    size_labels = [f"{s['size']}" + (" (last few)" if s["stock"] <= 3 else "") for s in in_stock]
                    fit_hint = st.session_state.fit_checks.get(product["category"], {}).get("recommended_size")
                    default = next((i for i, s in enumerate(in_stock) if s["size"] == fit_hint), 0)
                    picked = st.radio("Size", size_labels, index=default, horizontal=True)
                    size = in_stock[size_labels.index(picked)]["size"]
                    qty = st.number_input("Quantity", 1, 10, 1)
                    order = st.session_state.purchases.get(selected)
                    if order:
                        if "error" in order:
                            st.error(order["detail"])
                        elif order.get("escalated"):
                            st.warning(f"⏸️ Your order needs a quick check before it's confirmed — {order['reason']}")
                        else:
                            msg = f"✅ Ordered — {order['order_id']} · size {order['size']} · +{order['points_earned']} points"
                            if order.get("tier_upgraded_to"):
                                msg += f" · 🎉 you're now {order['tier_upgraded_to']}!"
                            st.success(msg)
                    if st.button(f"🛒 Buy now — {money(product['price'] * qty)}", type="primary", width="stretch"):
                        idem = st.session_state.idem.setdefault(f"{selected}:{size}:{qty}", new_key())
                        with st.spinner("Placing your order..."):
                            st.session_state.purchases[selected] = api(
                                "POST", "/orders", json={"sku": selected, "size": size, "quantity": int(qty),
                                                         "idempotency_key": idem})
                        st.rerun()

                st.divider()
                with st.expander("📏 Tell us about your fit (optional)"):
                    note_cols = st.columns(len(FIT_NOTES))
                    for i, note in enumerate(FIT_NOTES):
                        if note_cols[i].button(note, key=f"note-{i}", width="stretch"):
                            st.session_state.fit_note = note
                    note = st.text_input("Your note", key="fit_note", placeholder="e.g. broad shoulders, prefer relaxed")
                c_fit, c_sty = st.columns(2)
                if c_fit.button("📏 Check my fit", width="stretch"):
                    with st.spinner("Checking your fit history..."):
                        st.session_state.fit_checks[product["category"]] = api(
                            "POST", "/fit", json={"sku": selected, "customer_note": st.session_state.get("fit_note") or None})
                    st.rerun()
                if c_sty.button("💬 Talk to a stylist", width="stretch"):
                    with st.spinner("Connecting you with CONCIERGE..."):
                        st.session_state.stylist_offer = api("POST", "/chat", json={
                            "session_id": st.session_state.session_id, "message": "I'd like a stylist"})
                    st.rerun()
                if product["category"] in st.session_state.fit_checks:
                    render_fit(st.session_state.fit_checks[product["category"]])
                if st.session_state.stylist_offer:
                    render_offer(st.session_state.stylist_offer, key="pdp")

            st.markdown("### You might also like")
            similar = api("GET", "/recommendations", params={"session_id": st.session_state.session_id,
                                                              "similar_to": selected, "limit": 4})
            product_grid(similar.get("results", []), key="similar", show_reason=True)

    elif st.session_state.last_result:
        if st.session_state.last_query:
            st.caption(f"You searched: “{st.session_state.last_query}”")
        render_result(st.session_state.last_result)

    else:
        if st.session_state.reco is None:
            st.session_state.reco = api("GET", "/recommendations", params={"session_id": st.session_state.session_id})
        reco = st.session_state.reco
        first = reco.get("first_name") or st.session_state.get("customer_name", "").split(" ")[0]
        tags = ", ".join(reco.get("preference_tags", [])[:3])
        st.markdown(f'<div class="nt-hero"><h2>Welcome back, {first}</h2>'
                    f'<p>Picked for you{" from " + tags if tags else ""} — ranked by MUSE, sized by TAILOR.</p></div>',
                    unsafe_allow_html=True)
        sug_cols = st.columns(4)
        for i, s in enumerate(SUGGESTIONS):
            if sug_cols[i % 4].button(s, key=f"sug-{i}", width="stretch"):
                st.session_state.pending_query = s
                st.rerun()
        st.markdown("### Recommended for you")
        product_grid(reco.get("results", []), key="reco", show_reason=True, show_risk=True)
        viewed = api("GET", "/recently-viewed").get("results", [])
        if viewed:
            st.markdown("### Recently viewed")
            product_grid(viewed[:4], key="viewed")
        st.markdown("### Trending now")
        product_grid(api("GET", "/products", auth="none", params={"trending": True, "limit": 8}).get("results", [])[:8],
                     key="trend")
        st.markdown("### On sale")
        product_grid(api("GET", "/products", auth="none", params={"on_sale": True, "limit": 8}).get("results", [])[:8],
                     key="sale")
        st.markdown("### New in")
        product_grid(api("GET", "/products", auth="none", params={"new": True, "limit": 8}).get("results", [])[:8],
                     key="new")

# --------------------------------------------------------------------------- my orders
with orders_tab:
    st.markdown("### Your orders")
    st.caption("Returning something for fit? GRADE records the reason and TAILOR learns from it — check your fit "
               "for that category again afterwards.")
    if st.session_state.return_feedback:
        fb = st.session_state.return_feedback
        if "error" in fb:
            st.error(fb["detail"])
        else:
            st.success(f"Return {fb['return_id']} logged ({fb['reason_code'].replace('_', ' ')}) → feeds {fb['feeds']}.")
    history = api("GET", "/orders").get("orders", [])
    if not history:
        st.info("No orders yet.")
    for o in history:
        with st.container(border=True):
            c_img, c_txt, c_act = st.columns([1, 4, 2])
            if o.get("image_url"):
                c_img.image(img(o["image_url"]), width="stretch")
            if o["kind"] == "subscription":
                title = f"{'Neu.Tail Elite' if o.get('plan') == 'elite' else 'Neu.Tail+'} styling subscription"
            else:
                title = o.get("product_name") or o.get("sku")
            c_txt.markdown(f"**{title}**  \n{o['order_id']} · {o['created_at'][:10]} · {money(o['amount'])}"
                           + (f" · size {o['size']}" if o.get("size") else ""))
            if o.get("returned_reason"):
                c_txt.caption(f"↩️ Returned — {o['returned_reason'].replace('_', ' ')}")
            elif o["kind"] == "order":
                reason = c_act.selectbox("Reason", list(RETURN_REASONS), key=f"rr-{o['order_id']}", label_visibility="collapsed")
                if c_act.button("Return", key=f"ret-{o['order_id']}", width="stretch"):
                    st.session_state.return_feedback = api("POST", "/returns", json={
                        "order_id": o["order_id"], "reason_code": RETURN_REASONS[reason]})
                    st.session_state.fit_checks = {}
                    st.rerun()

# --------------------------------------------------------------------------- human review
with review_tab:
    st.markdown("### Human review queue")
    st.caption("SENTRY pauses risky charges here. Approving completes the commit (order + points) in one transaction; "
               "the reviewer identity comes from the staff key, not the request.")
    rr = st.session_state.review_result
    if rr:
        if "error" in rr:
            st.error(rr["detail"])
        elif rr["status"] == "approved":
            st.success(f"✅ Approved — order {rr['order_id']} · +{rr.get('points_earned')} points to the customer")
        else:
            st.info(f"Denied — escalation #{rr['escalation_id']} closed, nothing charged.")
    pending = api("GET", "/escalations", auth="reviewer", params={"status": "pending"}).get("escalations", [])
    if not pending:
        st.info("Nothing waiting for review.")
    for esc in pending:
        with st.container(border=True):
            c_img, c_txt, c_act = st.columns([1, 5, 2])
            if esc.get("image_url"):
                c_img.image(img(esc["image_url"]), width="stretch")
            what = esc.get("product_name") or f"{(esc.get('plan') or 'styling').title()} subscription"
            c_txt.markdown(f"**#{esc['escalation_id']} · {what}** · {money(esc['amount'])}"
                           + (f" · size {esc['size']} × {esc['quantity']}" if esc.get("size") else ""))
            c_txt.caption(f"customer {esc['customer_id']} · risk {esc['risk_score']:.2f} · {esc['reason']} · {esc['created_at']}")
            if c_act.button("✅ Approve", key=f"ap-{esc['escalation_id']}", width="stretch"):
                st.session_state.review_result = api("POST", f"/escalations/{esc['escalation_id']}/resolve",
                                                     auth="reviewer", json={"approve": True})
                st.rerun()
            if c_act.button("❌ Deny", key=f"dn-{esc['escalation_id']}", width="stretch"):
                st.session_state.review_result = api("POST", f"/escalations/{esc['escalation_id']}/resolve",
                                                     auth="reviewer", json={"approve": False})
                st.rerun()
    with st.expander("Resolved history"):
        for esc in api("GET", "/escalations", auth="reviewer", params={"status": ""}).get("escalations", []):
            if esc["status"] != "pending":
                st.text(f"#{esc['escalation_id']:<4} {esc['status']:<9} {money(esc['amount']):>10}  by {esc['resolved_by']}")

# --------------------------------------------------------------------------- admin
with admin_tab:
    outcomes = api("GET", "/admin/outcomes", auth="admin")
    if "error" in outcomes:
        st.error(outcomes["detail"])
    else:
        st.markdown("### Business outcomes")
        pop = outcomes["population"]
        cols = st.columns(6)
        for col, (label, key) in zip(cols, [("Customers", "customers"), ("SKUs", "skus"), ("Orders", "orders"),
                                            ("Revenue", "revenue"), ("Returns", "returns"), ("Audit rows", "audit_log_rows")]):
            col.metric(label, money(pop[key]) if key == "revenue" else f"{pop[key]:,}")
        rr_ = outcomes["return_rate"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Return rate — no fit guidance", f"{rr_['baseline_return_rate']:.1%}")
        c2.metric("Return rate — after TAILOR guidance", f"{rr_['guided_return_rate']:.1%}",
                  delta=f"{(rr_['guided_return_rate'] - rr_['baseline_return_rate']) * 100:.1f} pts", delta_color="inverse")
        stp = outcomes["search_to_purchase"]
        c3.metric("Engagement → purchase", f"{stp['search_to_purchase_rate']:.1%}",
                  help=f"{stp['impressions_logged']:,} impressions logged separately")
        st.caption(rr_["note"])
        up, loy = outcomes["upsell"], outcomes["loyalty"]
        cols = st.columns(5)
        for col, (label, val) in zip(cols, [("Offers composed", up["offers_composed"]), ("Accepted", up["offers_accepted"]),
                                            ("Escalations pending", up["escalations_pending"]),
                                            ("Points outstanding", f"{loy['points_outstanding']:,}"),
                                            ("Points redeemed", f"{loy['points_redeemed']:,}")]):
            col.metric(label, val)

        st.divider()
        st.markdown("### 🔎 Trace a request")
        trace_id = st.text_input("trace_id (shown under 'Behind the scenes' on any result)",
                                 value=(st.session_state.last_result or {}).get("trace_id", ""))
        if trace_id:
            entries = api("GET", f"/admin/traces/{trace_id}", auth="admin").get("entries", [])
            st.dataframe([{"#": e["log_id"], "kind": e["kind"], "caller": e["caller"], "tool": e["tool"],
                           "outcome": e["outcome"], "ms": e["latency_ms"], "model": e["model"] or "",
                           "prompt": e["prompt_version"] or ""} for e in entries], width="stretch", hide_index=True)

        st.markdown("### 🧭 Vector search")
        c_q, c_k = st.columns([4, 1])
        vq = c_q.text_input("Query", value="something for a summer wedding")
        vk = c_k.number_input("Top K", 3, 30, 8)
        if st.button("Search the index"):
            vs = api("GET", "/admin/vector_search", auth="admin", params={"query": vq, "top_k": vk})
            st.caption(f"backend `{vs['backend']}` · 'close match' threshold {vs['threshold']}")
            product_grid([{**r} for r in vs["results"]][:8], key="vs")

        st.markdown("### 🛡️ Policy — who can touch what")
        pol = api("GET", "/admin/policy", auth="admin")
        domains = ["crm", "loyalty", "catalogue", "inventory", "returns", "behavioural", "transactional", "escalations", "fit"]
        st.dataframe([{"agent": pol["agents"].get(a, {}).get("name", a),
                       **{d: (f"{g[d]['mode']} · {g[d]['method']}" if d in g else "") for d in domains}}
                      for a, g in pol["domain_matrix"].items()], width="stretch", hide_index=True)
        with st.expander("Tool contracts"):
            st.dataframe([{"tool": c["name"], "owner": c["owner"], "callers": ", ".join(c["allowed_callers"]),
                           "domains": ", ".join(f"{k}:{v}" for k, v in c["domains"].items()),
                           "side effect": c["side_effect"]} for c in pol["contracts"]], width="stretch", hide_index=True)
        with st.expander("Prompts & models"):
            st.dataframe(pol["prompts"], width="stretch", hide_index=True)
            st.json(pol["models"])

        st.markdown("### 📜 Audit trail (redacted)")
        limit = st.slider("Rows", 10, 200, 40)
        st.dataframe([{"time (UTC)": e["created_at"], "trace": e["trace_id"], "kind": e["kind"], "caller": e["caller"],
                       "actor": e["actor"], "tool": e["tool"], "outcome": e["outcome"], "ms": e["latency_ms"],
                       "detail": (e["detail"] or "")[:160]} for e in api("GET", "/audit", auth="admin",
                                                                          params={"limit": limit})["entries"]],
                     width="stretch", hide_index=True)
