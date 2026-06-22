import json
import os
import time
import uuid
from datetime import datetime

from state import InvoiceState

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between attempts

LOGS_DIR = "logs"


def mock_payment(vendor: str, amount: float) -> dict:
    # simulates a payment API call, always succeeds
    return {
        "transaction_id": str(uuid.uuid4()),
        "vendor": vendor,
        "amount": amount,
        "timestamp": datetime.utcnow().isoformat(),
        "status": "success",
    }


def write_audit_log(state: InvoiceState):
    os.makedirs(LOGS_DIR, exist_ok=True)
    invoice_id = state.invoice_number or os.path.basename(state.file_path)
    log_path = os.path.join(LOGS_DIR, f"{invoice_id}.json")

    try:
        with open(log_path, "w") as f:
            json.dump(state.to_dict(), f, indent=2)
    except Exception as e:
        # logging failure shouldnt crash the pipeline, just note it
        state.add_error(f"Could not write audit log: {e}")


def run(state: InvoiceState):
    # final safety check before touching payment, decision must be explicitly set
    if state.decision is None:
        state.add_error("Reached payment stage with no decision set, this should not happen")
        state.payment_status = "blocked"
        write_audit_log(state)
        return

    if state.decision != "approved":
        # rejected or human_review, log and stop
        state.payment_status = "skipped"
        write_audit_log(state)
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
            last_error = None
            break
        except Exception as e:
            last_error = e
            state.add_error(f"Payment attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    if last_error is not None:
        state.payment_status = "failed"
        write_audit_log(state)
        return

    state.mark_stage_complete("payment")
    write_audit_log(state)
