"""
Eval script for the Acme invoice processing pipeline.

Runs all test invoices through the pipeline in mock mode and measures:
  - Decision accuracy (hard and soft assertions)
  - False positive and false negative rates
  - Flag coverage (did validation catch what it should)
  - Extraction quality (field presence, confidence distribution)
  - Operational metrics (auto-processing rate, latency)
  - Estimated cost savings projection

Run with: python eval.py
"""

import os
import subprocess
import sys
import time

# force mock mode for eval so we get deterministic results without burning API credits
os.environ["MOCK_GROK"] = "true"

from main import run_batch
from state import InvoiceState

# ---- cost model assumptions ----
# AP specialist fully loaded cost (salary + benefits + overhead)
# based on $42K average AP specialist salary with 1.35x burden
AP_HOURLY_RATE = 28

# average minutes to manually process one invoice end to end
# includes data entry, validation against DB, email chain for approval, payment initiation
# clean invoices: ~8 min, complex/error ones: ~20 min, average across the mix
MINUTES_PER_INVOICE = 12

# assumed annual invoice volume for a PE-backed manufacturing firm at this scale
ANNUAL_INVOICE_VOLUME = 3000

# acme's stated annual loss from manual processing per the project brief
STATED_ANNUAL_LOSS = 2_000_000

# ---- ground truth ----
# expected_decision: what the pipeline should output
# hard: True means the pipeline must get this right, no acceptable alternatives
# hard: False means human_review is acceptable when we expected approved (grok may be conservative)
# expected_flags: at least one of these flag types should be present (empty means no flags expected)
# acceptable: list of decisions that count as a pass (used for soft assertions)

GROUND_TRUTH = [
    {
        "invoice_number": "INV-1001",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": [],
        "note": "Clean order, normal stock",
    },
    {
        "invoice_number": "INV-1002",
        "expected_decision": "rejected",
        "hard": True,
        "acceptable": ["rejected"],
        "expected_flags": ["stock_mismatch"],
        "note": "20x GadgetX, only 5 in stock",
    },
    {
        "invoice_number": "INV-1003",
        "expected_decision": "rejected",
        "hard": True,
        "acceptable": ["rejected"],
        "expected_flags": ["bad_actor"],
        "note": "Fraudster LLC, FakeItem",
    },
    {
        "invoice_number": "INV-1004",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "rejected", "human_review"],
        "expected_flags": [],
        "note": "INV-1004 and INV-1004_revised share an invoice number. Revised file has newer mtime so it processes first, gets approved, original is auto-rejected as superseded. Either file passing is acceptable.",
    },
    {
        "invoice_number": "INV-1005",
        "expected_decision": "rejected",
        "hard": True,
        "acceptable": ["rejected"],
        "expected_flags": ["stock_mismatch"],
        "note": "8x GadgetX, only 5 in stock",
    },
    {
        "invoice_number": "INV-1006",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": [],
        "note": "Clean CSV, single item",
    },
    {
        "invoice_number": "INV-1007",
        "expected_decision": "rejected",
        "hard": True,
        "acceptable": ["rejected"],
        "expected_flags": ["stock_mismatch"],
        "note": "20x WidgetA, only 15 in stock",
    },
    {
        "invoice_number": "INV-1008",
        "expected_decision": "human_review",
        "hard": True,
        "acceptable": ["human_review", "rejected"],
        "expected_flags": ["unknown_vendor"],
        "note": "NoProd Industries not on vendor list, halts on vendor check before item check",
    },
    {
        "invoice_number": "INV-1009",
        "expected_decision": "rejected",
        "hard": True,
        "acceptable": ["rejected"],
        "expected_flags": ["negative_quantity"],
        "note": "Negative quantity, blank vendor",
    },
    {
        "invoice_number": "INV-1010",
        "expected_decision": "human_review",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": ["price_variance"],
        "note": "Rush order with price variance, grok decides",
    },
    {
        "invoice_number": "INV-1011",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": [],
        "note": "Clean PDF",
    },
    {
        "invoice_number": "INV-1012",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": [],
        "note": "OCR artifacts, grok normalizes",
    },
    {
        "invoice_number": "INV-1013",
        "expected_decision": "rejected",
        "hard": False,
        "acceptable": ["rejected", "human_review"],
        "expected_flags": ["stock_mismatch"],
        "note": "Duplicate line items, aggregated qty exceeds stock",
    },
    {
        "invoice_number": "INV-1014",
        "expected_decision": "human_review",
        "hard": True,
        "acceptable": ["human_review"],
        "expected_flags": ["foreign_currency"],
        "note": "EUR invoice",
    },
    {
        "invoice_number": "INV-1015",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": [],
        "note": "Clean CSV tabular",
    },
    {
        "invoice_number": "INV-1016",
        "expected_decision": "rejected",
        "hard": True,
        "acceptable": ["rejected"],
        "expected_flags": ["out_of_stock"],
        "note": "WidgetC exists but zero stock",
    },
    {
        "invoice_number": "INV-1017",
        "expected_decision": "human_review",
        "hard": True,
        "acceptable": ["human_review", "rejected"],
        "expected_flags": ["possible_vendor_match"],
        "note": "Widgets lnc (lowercase L) fuzzy matches Widgets Inc. at high similarity, spoofing test",
    },
    {
        "invoice_number": "INV-1018",
        "expected_decision": "approved",
        "hard": False,
        "acceptable": ["approved", "human_review"],
        "expected_flags": [],
        "note": "$12,500 clean order from known vendor, exercises high value scrutiny path",
    },
    {
        "invoice_number": "INV-1019",
        "expected_decision": "human_review",
        "hard": True,
        "acceptable": ["human_review", "rejected"],
        "expected_flags": ["stock_mismatch"],
        "note": "WidgetA and WidgetB in stock, GadgetX x10 exceeds stock of 5, mixed result",
    },
]


def find_result(results: list[InvoiceState], invoice_number: str, prefer_file: str = None) -> InvoiceState | None:
    matches = [s for s in results if s.invoice_number == invoice_number]
    if not matches:
        return None
    if prefer_file:
        for s in matches:
            if prefer_file in s.file_path:
                return s
    # prefer the one that was not flagged as a duplicate
    for s in matches:
        if not s.has_flag("duplicate_invoice"):
            return s
    return matches[0]


def find_by_file(results: list[InvoiceState], filename_fragment: str) -> InvoiceState | None:
    for s in results:
        if filename_fragment in s.file_path:
            return s
    return None


def check_flag_coverage(state: InvoiceState, expected_flags: list[str]) -> tuple[bool, str]:
    if not expected_flags:
        return True, ""
    actual_flag_types = {f.type for f in state.flags}
    missing = [f for f in expected_flags if f not in actual_flag_types]
    if missing:
        return False, f"missing flags: {', '.join(missing)}"
    return True, ""


def run_eval():
    print("=" * 70)
    print("  Acme Invoice Pipeline Eval")
    print("  Mode: MOCK (deterministic, no API calls)")
    print("=" * 70)
    print()

    # reset the DB so prior runs don't pollute results with cross-session duplicate flags
    print("  Resetting database for clean eval run...")
    subprocess.run([sys.executable, "setup_db.py"], check=True, capture_output=True)
    print()

    start_time = time.time()
    results = run_batch(archive=False)  # keep test invoices in place
    total_time = time.time() - start_time

    if not results:
        print("ERROR: run_batch returned no results. Check that data/invoices/ exists.")
        sys.exit(1)

    # ---- per-invoice results ----
    print(f"{'Invoice':<18} {'Expected':<14} {'Got':<14} {'Flags OK':<10} {'Hard':<6} {'Result'}")
    print("-" * 75)

    hard_total = hard_pass = 0
    soft_total = soft_pass = 0
    flag_total = flag_pass = 0
    false_negatives = []   # bad invoice that got approved
    false_positives = []   # clean invoice rejected or sent to review unexpectedly

    for gt in GROUND_TRUTH:
        inv = gt["invoice_number"]

        # for invoices with both PDF and TXT/JSON, find_result picks the non-duplicate one
        state = find_result(results, inv)

        if state is None:
            print(f"{inv:<18} {'?':<14} {'NOT FOUND':<14} {'?':<10} {'H' if gt['hard'] else 'S':<6} MISSING")
            continue

        got = state.decision or "none"
        passed = got in gt["acceptable"]
        flags_ok, flags_msg = check_flag_coverage(state, gt["expected_flags"])

        # track false negatives: invoice marked as problematic but got approved
        is_problematic = gt["expected_decision"] in ("rejected",) and gt["hard"]
        if is_problematic and got == "approved":
            false_negatives.append(inv)

        # track false positives: clean invoice (expected approved, no flags) sent to review or rejected
        is_clean = gt["expected_decision"] == "approved" and not gt["expected_flags"]
        if is_clean and got not in ("approved",):
            false_positives.append(inv)

        if gt["hard"]:
            hard_total += 1
            if passed:
                hard_pass += 1
        else:
            soft_total += 1
            if passed:
                soft_pass += 1

        if gt["expected_flags"]:
            flag_total += 1
            if flags_ok:
                flag_pass += 1

        result_label = "PASS" if passed else "FAIL"
        hard_label = "H" if gt["hard"] else "S"
        flags_label = "yes" if (not gt["expected_flags"] or flags_ok) else f"NO ({flags_msg})"

        print(f"{inv:<18} {gt['expected_decision']:<14} {got:<14} {flags_label:<10} {hard_label:<6} {result_label}")

    print()

    # ---- aggregate accuracy ----
    total_assertions = hard_total + soft_total
    total_pass = hard_pass + soft_pass

    print("=" * 70)
    print("  Decision Accuracy")
    print("=" * 70)
    print(f"  Hard assertions (deterministic):  {hard_pass}/{hard_total}  ({100*hard_pass//hard_total if hard_total else 0}%)")
    print(f"  Soft assertions (grok may vary):  {soft_pass}/{soft_total}  ({100*soft_pass//soft_total if soft_total else 0}%)")
    print(f"  Overall:                          {total_pass}/{total_assertions}  ({100*total_pass//total_assertions if total_assertions else 0}%)")
    print()
    print(f"  Flag coverage:    {flag_pass}/{flag_total} expected flags correctly raised")
    print()

    # ---- false positive / negative ----
    print("=" * 70)
    print("  Error Analysis")
    print("=" * 70)
    fn_rate = len(false_negatives) / hard_total if hard_total else 0
    fp_rate = len(false_positives) / soft_total if soft_total else 0

    print(f"  False negatives (bad invoice approved):  {len(false_negatives)}  ({fn_rate:.0%})")
    if false_negatives:
        print(f"    Invoices: {', '.join(false_negatives)}")
        print("    Risk: financial loss, fraud, overpayment")
    else:
        print("    None detected. Pipeline correctly blocked all problematic invoices.")

    print()
    print(f"  False positives (clean invoice flagged):  {len(false_positives)}  ({fp_rate:.0%})")
    if false_positives:
        print(f"    Invoices: {', '.join(false_positives)}")
        print("    Risk: AP team review time, vendor friction, delayed payment")
    else:
        print("    None detected. Clean invoices passed through correctly.")

    print()
    print("  Note: false negatives are higher cost than false positives.")
    print("  A false negative on a $15K invoice is worse than a false positive")
    print("  that costs 12 minutes of AP review time. Pipeline is calibrated to")
    print("  prefer human_review over approved when uncertain.")

    # ---- operational metrics ----
    print()
    print("=" * 70)
    print("  Operational Metrics")
    print("=" * 70)

    approved = [s for s in results if s.decision == "approved"]
    rejected = [s for s in results if s.decision == "rejected"]
    human_review = [s for s in results if s.decision == "human_review"]
    errored = [s for s in results if s.decision == "error" or s.decision is None]

    auto_processed = len(approved) + len(rejected)
    auto_rate = auto_processed / len(results) if results else 0

    confidence_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    fields_present = 0
    fields_total = 0
    key_fields = ["invoice_number", "vendor", "total_amount", "date", "due_date"]

    for s in results:
        conf = (s.confidence or "unknown").lower()
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1
        for field in key_fields:
            fields_total += 1
            if getattr(s, field, None) is not None:
                fields_present += 1

    extraction_rate = fields_present / fields_total if fields_total else 0

    print(f"  Invoices processed:     {len(results)}")
    print(f"  Approved:               {len(approved)}")
    print(f"  Rejected:               {len(rejected)}")
    print(f"  Needs human review:     {len(human_review)}")
    if errored:
        print(f"  Errors:                 {len(errored)}")
    print()
    print(f"  Auto-processing rate:   {auto_rate:.0%}  ({auto_processed}/{len(results)} required no human action)")
    print(f"  Human review queue:     {len(human_review)} invoice(s) need AP attention")
    print()
    print(f"  Extraction quality:     {extraction_rate:.0%} of key fields successfully extracted")
    print(f"  Confidence breakdown:   high={confidence_counts['high']}  medium={confidence_counts['medium']}  low={confidence_counts['low']}")
    print()
    print(f"  Total batch time:       {total_time:.2f}s  ({total_time/len(results):.2f}s avg per invoice)")

    # ---- cost savings projection ----
    print()
    print("=" * 70)
    print("  Cost Savings Projection")
    print("=" * 70)
    print()
    print(f"  Assumptions:")
    print(f"    AP specialist fully loaded cost:  ${AP_HOURLY_RATE}/hr")
    print(f"    Manual processing time:           {MINUTES_PER_INVOICE} min/invoice")
    print(f"    Annual invoice volume:            {ANNUAL_INVOICE_VOLUME:,}")
    print(f"    Manual cost per invoice:          ${AP_HOURLY_RATE * MINUTES_PER_INVOICE / 60:.2f}")
    print()

    manual_cost_per_invoice = AP_HOURLY_RATE * MINUTES_PER_INVOICE / 60
    annual_manual_cost = ANNUAL_INVOICE_VOLUME * manual_cost_per_invoice
    annual_auto_savings = annual_manual_cost * auto_rate

    print(f"  Annual manual processing cost (labor only):  ${annual_manual_cost:,.0f}")
    print(f"  At {auto_rate:.0%} auto-processing rate:              ${annual_auto_savings:,.0f} saved in labor")
    print()
    print(f"  The stated $2M annual loss includes costs we cannot fully quantify")
    print(f"  from test data: error rework (30% error rate), late payment penalties")
    print(f"  from 5-day delays, management time in email approval chains, and fraud")
    print(f"  exposure. The labor figure above is the conservative, calculable piece.")
    print()

    # error rate reduction
    current_error_rate = 0.30
    our_hard_fail_rate = (hard_total - hard_pass) / hard_total if hard_total else 0
    print(f"  Error rate:  manual process {current_error_rate:.0%}  vs  pipeline {our_hard_fail_rate:.0%} on hard cases")
    print(f"  Processing time:  5 days manual  vs  {total_time/len(results):.1f}s per invoice automated")
    print()
    print("=" * 70)


if __name__ == "__main__":
    run_eval()
