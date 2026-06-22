import argparse
import os

from state import InvoiceState
from agents import ingestion, validation, approval, payment

INVOICE_DIR = "data/invoices"

# file extensions we know how to process
SUPPORTED_EXTENSIONS = {".txt", ".json", ".csv", ".xml", ".pdf"}


def print_invoice_result(state: InvoiceState):
    decision = (state.decision or "none").upper()
    vendor = (state.vendor or "unknown")[:25]
    # use is not None so $0.00 invoices dont show as unknown
    amount = f"${state.total_amount:,.2f}" if state.total_amount is not None else "unknown"
    invoice_id = state.invoice_number or os.path.basename(state.file_path)
    flags = ", ".join(f.type for f in state.flags) if state.flags else ""

    print(f"{invoice_id:<20} {vendor:<26} {amount:<12} {decision:<14} {flags}")


def run_single(file_path: str) -> InvoiceState:
    state = InvoiceState(file_path=file_path)

    ingestion.run(state)
    validation.run(state)
    approval.run(state)
    payment.run(state)

    return state


def run_batch() -> list[InvoiceState]:
    if not os.path.exists(INVOICE_DIR):
        print(f"Invoice directory not found: {INVOICE_DIR}")
        return []

    # sort by modified date so revised files come after originals
    files = [
        os.path.join(INVOICE_DIR, f)
        for f in os.listdir(INVOICE_DIR)
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
    ]
    files.sort(key=os.path.getmtime)

    if not files:
        print(f"No supported invoice files found in {INVOICE_DIR}")
        return []

    # shared across all invoices in the batch so duplicates get caught
    seen_invoice_numbers = set()
    results = []

    for file_path in files:
        try:
            state = InvoiceState(file_path=file_path)
            ingestion.run(state)
            validation.run(state, seen_invoice_numbers=seen_invoice_numbers)
            approval.run(state)
            payment.run(state)
            results.append(state)
        except Exception as e:
            # one bad file shouldnt kill the whole batch
            print(f"Unexpected error processing {file_path}: {e}")
            state = InvoiceState(file_path=file_path)
            state.add_error(f"Unexpected pipeline error: {e}")
            state.halt(f"Unexpected pipeline error: {e}")
            results.append(state)

    return results


def manual_approve(state: InvoiceState) -> InvoiceState:
    # AP person reviewed the flags and decided to approve, trust them and run payment
    state.halted = False
    state.halt_reason = None
    state.decision = "approved"
    state.reasoning = "Manually approved by AP team"
    payment.run(state)
    return state


def manual_reject(state: InvoiceState, reason: str) -> InvoiceState:
    if not reason or not reason.strip():
        raise ValueError("A reason is required when manually rejecting an invoice")
    state.decision = "rejected"
    state.reasoning = f"Manually rejected by AP team: {reason.strip()}"
    payment.run(state)
    return state


def print_batch_summary(results: list[InvoiceState]):
    approved = [s for s in results if s.decision == "approved"]
    rejected = [s for s in results if s.decision == "rejected"]
    human = [s for s in results if s.decision == "human_review"]
    errored = [s for s in results if s.decision == "error"]
    no_decision = [s for s in results if s.decision is None]

    # total dollar value that went through without any human involvement
    auto_processed_value = sum(s.total_amount or 0 for s in approved)

    print()
    print("=" * 80)
    print(f"  Processed:    {len(results)}")
    print(f"  Approved:     {len(approved)}")
    print(f"  Rejected:     {len(rejected)}")
    print(f"  Human review: {len(human)}")
    if errored:
        print(f"  Errors:       {len(errored)}  (check logs)")
    if no_decision:
        print(f"  No decision:  {len(no_decision)}  (check errors)")
    print(f"  Auto-processed value: ${auto_processed_value:,.2f}")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Acme invoice processing pipeline")
    # mutually exclusive means you can pass --invoice or --batch but not both
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--invoice", help="Path to a single invoice file")
    group.add_argument("--batch", action="store_true", help="Process all invoices in data/invoices/")
    args = parser.parse_args()

    if not args.invoice and not args.batch:
        parser.print_help()
        raise SystemExit(1)

    if args.invoice and not os.path.exists(args.invoice):
        print(f"File not found: {args.invoice}")
        raise SystemExit(1)

    print(f"\n{'Invoice':<20} {'Vendor':<26} {'Amount':<12} {'Decision':<14} {'Flags'}")
    print("-" * 90)

    if args.invoice:
        state = run_single(args.invoice)
        print_invoice_result(state)
    else:
        results = run_batch()
        for state in results:
            print_invoice_result(state)
        print_batch_summary(results)
