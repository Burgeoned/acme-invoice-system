import sqlite3
from rapidfuzz import fuzz, process

from state import InvoiceState

DB_PATH = "inventory.db"
PRICE_TOLERANCE = 0.15  # flag if invoice price deviates more than 15% from DB price
FUZZY_MATCH_THRESHOLD = 90  # minimum similarity score to suggest a vendor match


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us access columns by name instead of index
    return conn


def check_vendor(state: InvoiceState, cursor):
    vendor = (state.vendor or "").strip()

    if not vendor:
        state.add_flag("missing_vendor", "No vendor name on invoice")
        state.vendor_status = "unknown"
        return

    # check for known bad actor first, exact match case insensitive
    cursor.execute("SELECT approved FROM vendors WHERE LOWER(name) = LOWER(?)", (vendor,))
    row = cursor.fetchone()

    if row:
        if row["approved"] == 0:
            state.vendor_status = "bad_actor"
            state.add_flag("bad_actor", f"{vendor} is on the blocked vendor list")
            state.halt(f"Vendor {vendor} is a known bad actor")
        else:
            state.vendor_status = "approved"
        return

    # no exact match, try fuzzy against all vendor names
    cursor.execute("SELECT name, approved FROM vendors")
    all_vendors = cursor.fetchall()
    vendor_names = [r["name"] for r in all_vendors]

    match, score, _ = process.extractOne(vendor, vendor_names, scorer=fuzz.ratio) if vendor_names else (None, 0, None)

    if match and score >= FUZZY_MATCH_THRESHOLD:
        # found a close match but won't assume it's the same vendor, flag for human review
        state.vendor_status = "possible_match"
        state.possible_vendor_match = match
        state.add_flag("possible_vendor_match", f"Vendor '{vendor}' not found but closely matches '{match}' (similarity: {score}%). Needs confirmation.")
        state.halt("Vendor requires human review, possible name mismatch")
    else:
        # never seen this vendor before
        state.vendor_status = "unknown"
        state.add_flag("unknown_vendor", f"Vendor '{vendor}' is not in the approved vendor list")
        state.halt("Unknown vendor, requires human review before processing")


def check_items_and_stock(state: InvoiceState, cursor):
    # aggregate quantities per item first so we check total demand not per-line
    aggregated = {}
    for li in state.line_items:
        key = li.item.lower()
        if key not in aggregated:
            aggregated[key] = {"item": li.item, "quantity": 0}
        aggregated[key]["quantity"] += li.quantity

    for key, data in aggregated.items():
        item_name = data["item"]
        total_qty = data["quantity"]

        # check item exists in catalog
        cursor.execute("SELECT unit_price FROM items WHERE LOWER(item) = LOWER(?)", (item_name,))
        item_row = cursor.fetchone()

        if not item_row:
            state.add_flag("unknown_item", f"'{item_name}' is not in the item catalog")
            continue

        # check stock
        cursor.execute("SELECT stock FROM inventory WHERE LOWER(item) = LOWER(?)", (item_name,))
        inv_row = cursor.fetchone()

        if not inv_row or inv_row["stock"] == 0:
            state.add_flag("out_of_stock", f"'{item_name}' is out of stock")
        elif inv_row["stock"] < total_qty:
            state.add_flag("stock_mismatch", f"'{item_name}' requested {total_qty}, only {inv_row['stock']} in stock")


def check_prices(state: InvoiceState, cursor):
    for li in state.line_items:
        cursor.execute("SELECT unit_price FROM items WHERE LOWER(item) = LOWER(?)", (li.item,))
        row = cursor.fetchone()

        if not row:
            continue  # unknown items already flagged in check_items_and_stock

        db_price = row["unit_price"]
        if db_price == 0:
            continue  # no expected price to compare against

        variance = abs(li.unit_price - db_price) / db_price

        if variance > PRICE_TOLERANCE:
            state.add_flag(
                "price_variance",
                f"'{li.item}' invoiced at ${li.unit_price:.2f}, expected ${db_price:.2f} ({variance:.0%} variance)"
            )


def check_data_integrity(state: InvoiceState):
    # catch obviously bad data before hitting the db
    if state.total_amount is not None and state.total_amount < 0:
        state.add_flag("negative_total", f"Invoice total is negative: ${state.total_amount:.2f}")

    for li in state.line_items:
        if li.quantity < 0:
            state.add_flag("negative_quantity", f"'{li.item}' has negative quantity: {li.quantity}")
        if li.quantity == 0:
            state.add_flag("zero_quantity", f"'{li.item}' has zero quantity")


def run(state: InvoiceState, seen_invoice_numbers: set = None):
    if state.halted:
        return

    # foreign currency check, can't process without knowing exchange rate policy
    if state.currency and state.currency.upper() != "USD":
        state.add_flag("foreign_currency", f"Invoice is in {state.currency}, not USD. Needs human review.")
        state.halt("Foreign currency requires human review")
        return

    # duplicate invoice number check, only relevant in batch mode
    if seen_invoice_numbers is not None and state.invoice_number:
        if state.invoice_number in seen_invoice_numbers:
            state.add_flag("duplicate_invoice", f"Invoice number {state.invoice_number} has already been processed, keeping latest file only")
            state.halt("Duplicate invoice number")
            return
        seen_invoice_numbers.add(state.invoice_number)

    # data integrity before touching the db
    check_data_integrity(state)

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        check_vendor(state, cursor)
        if state.halted:
            return

        check_items_and_stock(state, cursor)
        check_prices(state, cursor)

    except sqlite3.Error as e:
        state.add_error(f"Database error during validation: {e}")
        state.halt("Validation failed due to database error")
        return
    finally:
        if conn:
            conn.close()

    state.mark_stage_complete("validation")
