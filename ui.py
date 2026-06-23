import base64
import os
import sqlite3
import streamlit as st

from main import run_single, run_batch, manual_approve, manual_reject
from state import InvoiceState

st.set_page_config(
    page_title="Acme Invoice Review",
    page_icon="🧾",
    layout="wide",
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }

    /* base button */
    div[data-testid="stButton"] button {
        border-radius: 6px !important;
        font-weight: 600 !important;
        width: 100%;
        transition: transform 0.08s ease, filter 0.08s ease, box-shadow 0.08s ease !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.12) !important;
    }
    div[data-testid="stButton"] button:hover {
        filter: brightness(0.94) !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15) !important;
    }
    div[data-testid="stButton"] button:active {
        transform: translateY(1px) !important;
        filter: brightness(0.88) !important;
        box-shadow: 0 0px 1px rgba(0,0,0,0.1) !important;
    }

    /* marker-based button coloring
       each st.markdown marker sits in a sibling div right before the button div,
       so we can target the very next stButton using the adjacent + selector */
    div:has(span.mk-green) + div button,
    div:has(span.mk-green) + div + div button {
        background-color: #16a34a !important;
        color: white !important;
        border: none !important;
    }
    div:has(span.mk-red) + div button,
    div:has(span.mk-red) + div + div button {
        background-color: #dc2626 !important;
        color: white !important;
        border: none !important;
    }
    div:has(span.mk-amber) + div button,
    div:has(span.mk-amber) + div + div button {
        background-color: #d97706 !important;
        color: white !important;
        border: none !important;
    }
    div:has(span.mk-gray) + div button,
    div:has(span.mk-gray) + div + div button {
        background-color: white !important;
        color: #374151 !important;
        border: 1px solid #d1d5db !important;
    }

    /* metric cards */
    div[data-testid="metric-container"] {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        padding: 16px;
    }

    /* already-handled table rows */
    .handled-row {
        display: grid;
        grid-template-columns: 2fr 1fr 1fr;
        align-items: center;
        padding: 10px 4px;
        border-bottom: 1px solid #f3f4f6;
        font-size: 0.9rem;
    }
    .handled-row:last-child { border-bottom: none; }
    .handled-vendor { font-weight: 600; color: #111; }
    .handled-id { color: #9ca3af; font-size: 0.8rem; margin-top: 2px; }
    .handled-amount { color: #374151; }

    /* page header */
    .acme-header {
        display: flex;
        align-items: baseline;
        gap: 12px;
        margin-bottom: 1.5rem;
        padding-bottom: 1rem;
        border-bottom: 2px solid #e5e7eb;
    }
    .acme-header h1 { margin: 0; font-size: 1.6rem; font-weight: 700; color: #111; }
    .acme-header span { color: #6b7280; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# drop one of these right before st.button() to color it
def mk(color: str):
    tag = {"green": "mk-green", "red": "mk-red", "amber": "mk-amber", "gray": "mk-gray"}.get(color, "mk-gray")
    st.markdown(f'<span class="{tag}"></span>', unsafe_allow_html=True)

DECISION_COLORS = {
    "approved":     "#22c55e",
    "rejected":     "#ef4444",
    "human_review": "#f59e0b",
    "error":        "#8b5cf6",
}

DECISION_LABELS = {
    "approved":     "Approved",
    "rejected":     "Rejected",
    "human_review": "Needs review",
    "error":        "Error",
}

FLAG_PLAIN_ENGLISH = {
    "stock_mismatch":        "Quantity exceeds available stock",
    "out_of_stock":          "Item is out of stock",
    "unknown_item":          "Item not in catalog",
    "price_variance":        "Price deviates from expected",
    "unknown_vendor":        "Vendor not on approved list",
    "possible_vendor_match": "Vendor name closely matches a known vendor. Confirm identity before approving.",
    "bad_actor":             "Vendor is flagged as a bad actor",
    "foreign_currency":      "Invoice is not in USD",
    "duplicate_invoice":     "Invoice number already processed",
    "negative_quantity":     "Line item has negative quantity",
    "negative_total":        "Invoice total is negative",
    "missing_total":         "No total amount on invoice",
    "no_line_items":         "No line items found",
    "missing_vendor":        "No vendor name on invoice",
    "low_confidence":        "Extraction confidence was low, data may be unreliable",
    "malformed_line_item":   "A line item could not be parsed",
}


def badge(decision: str) -> str:
    color = DECISION_COLORS.get(decision, "#6b7280")
    label = DECISION_LABELS.get(decision, decision or "none")
    return f'<span style="background:{color}18;color:{color};padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:600;border:1px solid {color}40">{label}</span>'


def flag_summary(state: InvoiceState) -> str:
    if not state.flags:
        return ""
    priority_order = [
        "bad_actor", "foreign_currency", "negative_quantity", "negative_total",
        "unknown_vendor", "possible_vendor_match", "stock_mismatch", "out_of_stock",
        "unknown_item", "price_variance", "duplicate_invoice", "low_confidence",
        "missing_total", "no_line_items", "missing_vendor", "malformed_line_item",
    ]
    for flag_type in priority_order:
        for f in state.flags:
            if f.type == flag_type:
                return FLAG_PLAIN_ENGLISH.get(flag_type, f.message)
    return state.flags[0].message


def render_invoice_file(file_path: str, label: str = "Original invoice"):
    st.markdown(f"**{label}**")
    if not file_path or not os.path.exists(file_path):
        st.caption("File not available")
        return

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="500px" style="border:1px solid #e5e7eb;border-radius:6px"></iframe>',
            unsafe_allow_html=True,
        )
    else:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            lang = ext.lstrip(".") if ext in (".json", ".xml", ".csv") else "text"
            st.code(content, language=lang)
        except Exception as e:
            st.caption(f"Could not read file: {e}")


def _read_processed_amount(file_path: str) -> float | None:
    """Best-effort: extract total amount from a processed invoice file for revision detection."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if ext == ".json":
            import json
            data = json.loads(content)
            return float(data.get("total") or data.get("total_amount") or 0) or None
    except Exception:
        pass
    return None


def lookup_duplicate_source(invoice_number: str, current_file: str) -> dict | None:
    try:
        conn = sqlite3.connect("inventory.db")
        row = conn.execute(
            "SELECT file_path, decision, processed_at FROM processed_invoices WHERE invoice_number = ? AND file_path != ?",
            (invoice_number, current_file),
        ).fetchone()
        conn.close()
        if row:
            return {"file_path": row[0], "decision": row[1], "processed_at": row[2]}
    except Exception:
        pass
    return None


def show_line_items(state: InvoiceState):
    if not state.line_items:
        return
    for li in state.line_items:
        qty = int(li.quantity) if li.quantity == int(li.quantity) else li.quantity
        # plain text with unicode dot, avoids markdown entity rendering issues
        st.text(f"  {li.item}  ·  x{qty}  ·  ${li.unit_price:,.2f} ea  ·  ${li.total:,.2f}")


def show_invoice_fields(state: InvoiceState):
    """Extracted fields, flags, and AI reasoning. Left side of the detail panel."""
    st.markdown(f"**Invoice #:** {state.invoice_number or 'unknown'}")
    st.markdown(f"**Vendor:** {state.vendor or 'unknown'}")
    st.markdown(f"**Date:** {state.date or 'unknown'}")
    st.markdown(f"**Due:** {state.due_date or 'unknown'}")
    if state.payment_terms:
        st.markdown(f"**Payment terms:** {state.payment_terms}")
    st.markdown(f"**Confidence:** {state.confidence or 'unknown'}")

    if state.line_items:
        st.markdown("**Line items:**")
        show_line_items(state)

    if state.flags:
        st.markdown("**Flags:**")
        for f in state.flags:
            # item-specific flags have the actual item name in the message, use that over the generic label
            ITEM_SPECIFIC = {"stock_mismatch", "out_of_stock", "unknown_item", "price_variance", "malformed_line_item"}
            plain = f.message if f.type in ITEM_SPECIFIC else FLAG_PLAIN_ENGLISH.get(f.type, f.message)
            if f.type == "duplicate_invoice" and state.invoice_number:
                source = lookup_duplicate_source(state.invoice_number, state.file_path)
                if source:
                    decision_label = DECISION_LABELS.get(source["decision"], source["decision"])
                    date_str = source["processed_at"][:10]
                    fname = os.path.basename(source["file_path"])
                    orig_amount = _read_processed_amount(source["file_path"])
                    is_revision = (
                        orig_amount is not None
                        and state.total_amount is not None
                        and abs(orig_amount - state.total_amount) > 0.01
                    )
                    if is_revision:
                        st.warning(
                            f"Possible revised invoice. Same number as **{fname}** ({decision_label} on {date_str}), "
                            f"but amount differs: original ${orig_amount:,.2f} vs this ${state.total_amount:,.2f}. "
                            f"Compare both versions before deciding."
                        )
                        # surface the right action based on what happened to the original
                        if source["decision"] == "approved":
                            st.error(
                                "Payment was already sent for the original. "
                                "Accepting this revision requires a manual adjustment with finance. "
                                "do not approve here. Contact the vendor for a credit memo or supplemental invoice."
                            )
                            state._revision_blocked = True
                        else:
                            st.info(
                                f"Original was {decision_label.lower()}, no payment was sent. "
                                "You can accept this revision and reprocess it."
                            )
                            state._revision_source = source
                    else:
                        st.warning(f"Duplicate of **{fname}** ({decision_label} on {date_str})")
                    state._dupe_source_path = source["file_path"]
                else:
                    st.warning(plain)
            else:
                st.warning(plain)

    if state.reasoning:
        st.markdown("**AI reasoning:**")
        st.info(state.reasoning)

    if state.payment_result:
        pr = state.payment_result
        st.success(f"Payment confirmed. Transaction {pr['transaction_id']} · ${pr['amount']:,.2f} to {pr['vendor']} at {pr['timestamp']}")

    if state.errors:
        for e in state.errors:
            st.error(e)


def show_full_detail(state: InvoiceState):
    """Two-column detail panel: fields + reasoning on the left, invoice file(s) on the right."""
    dupe_source = getattr(state, "_dupe_source_path", None)

    col_left, col_right = st.columns([1, 1])
    with col_left:
        show_invoice_fields(state)
    with col_right:
        # if this is a duplicate, show both invoices so the reviewer can compare
        if dupe_source:
            tab_current, tab_original = st.tabs(["This invoice", "Original (processed)"])
            with tab_current:
                render_invoice_file(state.file_path, label="")
            with tab_original:
                render_invoice_file(dupe_source, label="")
        else:
            render_invoice_file(state.file_path, label="")


def review_card(state: InvoiceState, idx: int):
    """Single invoice card for the needs-attention queue."""
    amount = f"${state.total_amount:,.2f}" if state.total_amount is not None else "unknown"
    vendor = state.vendor or "Unknown vendor"
    invoice_id = state.invoice_number or os.path.basename(state.file_path)
    primary_flag = flag_summary(state)

    st.markdown(f"""
    <div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px 20px;margin-bottom:4px;background:white">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
                <span style="font-size:1rem;font-weight:600;color:#111">{vendor}</span>
                <span style="color:#9ca3af;margin-left:10px;font-size:0.85rem">{invoice_id}</span>
            </div>
            <span style="font-size:1.1rem;font-weight:700;color:#111">{amount}</span>
        </div>
        <div style="margin-top:6px;color:#6b7280;font-size:0.85rem">{primary_flag}</div>
    </div>
    """, unsafe_allow_html=True)

    # approve always visible, reject is two-step to prevent accidents
    # "Accept Revision" appears only when original wasn't paid and amounts differ
    has_revision = getattr(state, "_revision_source", None) and not getattr(state, "_revision_blocked", False)
    cols = st.columns([1, 1, 1, 1] if has_revision else [1, 1, 1])
    col_approve, col_reject, *rest = cols
    col_revision = rest[0] if has_revision else None
    col_detail = rest[-1]

    with col_approve:
        mk("green")
        if st.button("Approve", key=f"approve_{idx}", use_container_width=True):
            manual_approve(state)
            st.session_state.results[idx] = state
            st.rerun()

    with col_reject:
        rejecting = st.session_state.get(f"rejecting_{idx}", False)
        if not rejecting:
            mk("red")
            if st.button("Reject", key=f"reject_{idx}", use_container_width=True):
                st.session_state[f"rejecting_{idx}"] = True
                st.rerun()
        else:
            mk("gray")
            if st.button("Cancel", key=f"cancel_reject_{idx}", use_container_width=True):
                st.session_state[f"rejecting_{idx}"] = False
                st.rerun()

    if col_revision:
        with col_revision:
            mk("amber")
            if st.button("Accept Revision", key=f"accept_revision_{idx}", use_container_width=True):
                state.flags = [f for f in state.flags if f.type != "duplicate_invoice"]
                state.halted = False
                state.halt_reason = None
                state.reasoning = (state.reasoning or "") + " | Revision accepted by AP team — original was not paid."
                manual_approve(state)
                st.session_state.results[idx] = state
                st.rerun()

    with col_detail:
        label = "Hide" if st.session_state.get(f"show_detail_{idx}") else "Details"
        mk("gray")
        if st.button(label, key=f"detail_{idx}", use_container_width=True):
            st.session_state[f"show_detail_{idx}"] = not st.session_state.get(f"show_detail_{idx}", False)
            st.rerun()

    if st.session_state.get(f"rejecting_{idx}", False):
        reason = st.text_input(
            "Reason for rejection",
            key=f"reason_{idx}",
            placeholder="Enter reason, then confirm",
        )
        mk("red")
        if st.button("Confirm rejection", key=f"confirm_reject_{idx}", use_container_width=True):
            if not reason.strip():
                st.error("A reason is required")
            else:
                try:
                    manual_reject(state, reason)
                    st.session_state.results[idx] = state
                    st.session_state[f"rejecting_{idx}"] = False
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    if st.session_state.get(f"show_detail_{idx}", False):
        st.markdown("---")
        show_full_detail(state)
        st.markdown("---")

    st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)


def show_handled_row(state: InvoiceState, row_idx: int):
    """Uniform 5-column row: vendor/id | amount | badge | override (rejected only) | details."""
    amount = f"${state.total_amount:,.2f}" if state.total_amount is not None else "—"
    invoice_id = state.invoice_number or os.path.basename(state.file_path)
    key = f"handled_detail_{row_idx}"
    can_override = state.decision == "rejected"

    # fixed columns every row so the list looks uniform, override col is empty for approved/error
    col1, col2, col3, col4, col5 = st.columns([3, 2, 2, 1, 1])

    with col1:
        st.markdown(f"**{state.vendor or 'unknown'}**  \n<span style='color:#9ca3af;font-size:0.8rem'>{invoice_id}</span>", unsafe_allow_html=True)
    with col2:
        st.markdown(f"<span style='line-height:2.2'>{amount}</span>", unsafe_allow_html=True)
    with col3:
        st.markdown(f"<span style='line-height:2.5'>{badge(state.decision)}</span>", unsafe_allow_html=True)
    with col4:
        if can_override:
            overriding = st.session_state.get(f"overriding_{row_idx}", False)
            mk("gray")
            if st.button("Cancel" if overriding else "Override", key=f"override_toggle_{row_idx}", use_container_width=True):
                st.session_state[f"overriding_{row_idx}"] = not overriding
                st.rerun()
    with col5:
        detail_label = "Hide" if st.session_state.get(key) else "Details"
        mk("gray")
        if st.button(detail_label, key=f"btn_{key}", use_container_width=True):
            st.session_state[key] = not st.session_state.get(key, False)
            st.rerun()

    if st.session_state.get(f"overriding_{row_idx}"):
        st.info("This invoice was rejected. No payment was sent. Approving it now will run payment and write to the audit log.")
        mk("green")
        if st.button("Approve and process payment", key=f"override_approve_{row_idx}", use_container_width=True):
            state.decision = None
            manual_approve(state)
            for i, s in enumerate(st.session_state.results):
                if s.file_path == state.file_path:
                    st.session_state.results[i] = state
                    break
            st.session_state[f"overriding_{row_idx}"] = False
            st.rerun()

    if st.session_state.get(key):
        show_full_detail(state)

    st.markdown("<hr style='margin:6px 0;border:none;border-top:1px solid #f3f4f6'>", unsafe_allow_html=True)


def show_metrics(results: list):
    approved = [s for s in results if s.decision == "approved"]
    rejected = [s for s in results if s.decision == "rejected"]
    human = [s for s in results if s.decision == "human_review"]
    auto_value = sum(s.total_amount or 0 for s in approved)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Processed", len(results))
    with c2:
        st.metric("Approved", len(approved))
    with c3:
        st.metric("Rejected", len(rejected))
    with c4:
        st.metric("Needs Review", len(human))

    if auto_value:
        st.caption(f"${auto_value:,.0f} auto-processed without human intervention")

    errored = [s for s in results if s.decision == "error"]
    if errored:
        st.warning(f"{len(errored)} invoice(s) had system errors — check logs.")


# ---- app ----

st.markdown("""
<div class="acme-header">
    <h1>Acme Invoice Review</h1>
    <span>Accounts Payable · Internal Tool</span>
</div>
<script>
function applyButtonColors() {
    document.querySelectorAll('button').forEach(function(btn) {
        const text = btn.innerText.trim();
        if (['Approve', 'Run Batch', 'Run Invoice', 'Approve and process payment'].includes(text)) {
            btn.style.backgroundColor = '#16a34a';
            btn.style.color = 'white';
            btn.style.border = 'none';
        } else if (['Reject', 'Confirm rejection'].includes(text)) {
            btn.style.backgroundColor = '#dc2626';
            btn.style.color = 'white';
            btn.style.border = 'none';
        } else if (text === 'Accept Revision') {
            btn.style.backgroundColor = '#d97706';
            btn.style.color = 'white';
            btn.style.border = 'none';
        }
    });
}
applyButtonColors();
setTimeout(applyButtonColors, 50);
setTimeout(applyButtonColors, 200);
</script>
""", unsafe_allow_html=True)

tab_batch, tab_single = st.tabs(["Batch Processing", "Single Invoice"])

# ---- batch tab ----
with tab_batch:
    col_run, col_reset, col_db, _ = st.columns([1, 1, 1, 4])
    with col_run:
        mk("green")
        if st.button("Run Batch", use_container_width=True):
            with st.spinner("Processing invoices..."):
                st.session_state.results = run_batch()
    with col_reset:
        mk("gray")
        if st.button("Reset view", use_container_width=True, help="Clears the display only. All decisions stay in the database."):
            st.session_state.results = []
            st.rerun()
    with col_db:
        mk("gray")
        if st.button("Reset DB", use_container_width=True, help="Wipes processed_invoices and audit logs. Testing only."):
            st.session_state.confirm_reset_db = True
            st.rerun()

    if st.session_state.get("confirm_reset_db"):
        st.warning("This will wipe all processed invoices and audit logs. Are you sure?")
        c1, c2 = st.columns([1, 5])
        with c1:
            mk("red")
            if st.button("Yes, reset", key="confirm_db_yes", use_container_width=True):
                import subprocess, sys
                subprocess.run([sys.executable, "setup_db.py"], check=True)
                st.session_state.results = []
                st.session_state.confirm_reset_db = False
                st.rerun()
        with c2:
            mk("gray")
            if st.button("Cancel", key="confirm_db_cancel", use_container_width=True):
                st.session_state.confirm_reset_db = False
                st.rerun()

    if st.session_state.get("results"):
        results = st.session_state.results
        human_review = [s for s in results if s.decision == "human_review"]
        handled = [s for s in results if s.decision in ("approved", "rejected", "error")]

        if human_review:
            st.markdown(f"### Needs Your Attention ({len(human_review)})")
            for i, state in enumerate(results):
                if state.decision == "human_review":
                    review_card(state, i)
            st.markdown("---")

        st.markdown("### Summary")
        show_metrics(results)

        if handled:
            with st.expander(f"Already handled ({len(handled)} invoices)", expanded=False):
                for j, state in enumerate(handled):
                    show_handled_row(state, j)

# ---- single invoice tab ----
with tab_single:
    uploaded = st.file_uploader(
        "Upload an invoice",
        type=["txt", "json", "csv", "xml", "pdf"],
        help="Supported: TXT, JSON, CSV, XML, PDF"
    )

    if uploaded:
        tmp_path = os.path.join("data", "invoices", f"_upload_{uploaded.name}")
        with open(tmp_path, "wb") as f:
            f.write(uploaded.getbuffer())

        mk("green")
        if st.button("Run Invoice", use_container_width=True):
            with st.spinner("Processing..."):
                state = run_single(tmp_path)
                st.session_state.single_result = state

        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    if st.session_state.get("single_result"):
        state = st.session_state.single_result
        amount = f"${state.total_amount:,.2f}" if state.total_amount is not None else "unknown"

        st.markdown("---")
        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(f"### {state.vendor or 'Unknown vendor'} &nbsp; {amount}", unsafe_allow_html=True)
            primary_flag = flag_summary(state)
            if primary_flag:
                st.caption(primary_flag)
        with col2:
            st.markdown(badge(state.decision), unsafe_allow_html=True)

        show_full_detail(state)

        if state.decision == "human_review":
            st.markdown("---")
            st.markdown("**Action required**")

            ca, cb = st.columns([1, 1])
            with ca:
                mk("green")
                if st.button("Approve", key="single_approve", use_container_width=True):
                    manual_approve(state)
                    st.session_state.single_result = state
                    st.rerun()
            with cb:
                if not st.session_state.get("single_rejecting"):
                    mk("red")
                    if st.button("Reject", key="single_reject", use_container_width=True):
                        st.session_state.single_rejecting = True
                        st.rerun()
                else:
                    mk("gray")
                    if st.button("Cancel", key="single_cancel", use_container_width=True):
                        st.session_state.single_rejecting = False
                        st.rerun()

            if st.session_state.get("single_rejecting"):
                reason = st.text_input("Reason for rejection", key="single_reason", placeholder="Enter reason, then confirm")
                mk("red")
                if st.button("Confirm rejection", key="single_confirm_reject", use_container_width=True):
                    if not reason.strip():
                        st.error("A reason is required")
                    else:
                        try:
                            manual_reject(state, reason)
                            st.session_state.single_result = state
                            st.session_state.single_rejecting = False
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
