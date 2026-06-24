import json
import os
import random
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from state import InvoiceState

DB_PATH = "inventory.db"

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between attempts, would use exponential backoff with a real API

LOGS_DIR = "logs"

# set PAYMENT_FAIL_RATE=0.3 in .env to simulate 30% payment failures for testing
PAYMENT_FAIL_RATE = float(os.getenv("PAYMENT_FAIL_RATE", "0"))


class PaymentError(Exception):
    pass


def mock_payment(vendor: str, amount: float) -> dict:
    if PAYMENT_FAIL_RATE > 0 and random.random() < PAYMENT_FAIL_RATE:
        raise PaymentError(f"Payment gateway timeout (simulated {PAYMENT_FAIL_RATE:.0%} failure rate)")

    return {
        "transaction_id": str(uuid.uuid4()),
        "vendor": vendor,
        "amount": amount,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "success",
    }


def record_processed(state: InvoiceState):
    if not state.invoice_number:
        return
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO processed_invoices (invoice_number, file_path, vendor_name, decision, processed_at) VALUES (?, ?, ?, ?, ?)",
            (state.invoice_number, state.file_path, state.vendor, state.decision, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    except sqlite3.Error as e:
        state.add_error(f"Could not record invoice in processed_invoices: {e}")
    finally:
        if conn:
            conn.close()


def write_audit_log(state: InvoiceState):
    os.makedirs(LOGS_DIR, exist_ok=True)
    invoice_id = state.invoice_number or os.path.basename(state.file_path)
    log_path = os.path.join(LOGS_DIR, f"{invoice_id}.json")

    # if a log already exists for this invoice, append a timestamp so we don't
    # overwrite it — each run of the pipeline produces an immutable record
    if os.path.exists(log_path):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(LOGS_DIR, f"{invoice_id}_{stamp}.json")

    try:
        with open(log_path, "w") as f:
            json.dump(state.to_dict(), f, indent=2)
    except Exception as e:
        state.add_error(f"Could not write audit log: {e}")


def run(state: InvoiceState):
    # final safety check before touching payment, decision must be explicitly set
    if state.decision is None:
        state.add_error("Reached payment stage with no decision set, this should not happen")
        state.payment_status = "blocked"
        write_audit_log(state)
        return

    if state.decision != "approved":
        # rejected or human_review — log and record so cross-session duplicate detection catches it
        state.payment_status = "skipped"
        write_audit_log(state)
        record_processed(state)
        return

    # one more check: invoice must not be halted even if somehow approved
    if state.halted:
        state.add_error("Invoice is halted but decision is approved, blocking payment as a safety measure")
        state.payment_status = "blocked"
        write_audit_log(state)
        return

    # retry on transient failures, give up after MAX_RETRIES
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = mock_payment(
                vendor=state.vendor or "unknown",
                amount=state.total_amount or 0,
            )
            state.payment_result = result
            state.payment_status = "paid"
            state.payment_attempts = attempt
            last_error = None
            break
        except PaymentError as e:
            last_error = e
            state.add_error(f"Payment attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    if last_error is not None:
        # record in processed_invoices even on failure — this is what prevents a re-run
        # from trying to pay the same invoice again. the AP team will see payment_failed
        # in the audit log and can investigate before anyone manually retries.
        state.payment_status = "failed"
        state.decision = "payment_failed"
        write_audit_log(state)
        record_processed(state)
        return

    state.mark_stage_complete("payment")
    write_audit_log(state)
    record_processed(state)
