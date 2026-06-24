"""
Unit tests for the invoice pipeline.

These run without Grok or a real database — they test the deterministic
logic in isolation so failures point to a specific function, not "somewhere
in the batch run."

Run with: pytest tests/
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
import pytest

from state import InvoiceState, LineItem, Flag
from agents import validation, approval


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Fresh in-memory-ish DB for each test that needs one."""
    path = str(tmp_path / "test_inventory.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE items (
            id         INTEGER PRIMARY KEY,
            item       TEXT NOT NULL UNIQUE,
            unit_price REAL NOT NULL
        );
        CREATE TABLE inventory (
            id    INTEGER PRIMARY KEY,
            item  TEXT NOT NULL UNIQUE,
            stock INTEGER NOT NULL
        );
        CREATE TABLE vendors (
            id       INTEGER PRIMARY KEY,
            name     TEXT NOT NULL UNIQUE,
            approved INTEGER NOT NULL
        );
        CREATE TABLE processed_invoices (
            id             INTEGER PRIMARY KEY,
            invoice_number TEXT NOT NULL,
            file_path      TEXT NOT NULL,
            vendor_name    TEXT,
            decision       TEXT,
            processed_at   TEXT NOT NULL,
            UNIQUE (invoice_number, file_path)
        );
        INSERT INTO items (item, unit_price) VALUES ('WidgetA', 250.0), ('WidgetB', 500.0), ('GadgetX', 750.0), ('WidgetC', 350.0), ('FakeItem', 0.0);
        INSERT INTO inventory (item, stock) VALUES ('WidgetA', 15), ('WidgetB', 10), ('GadgetX', 5), ('WidgetC', 0), ('FakeItem', 0);
        INSERT INTO vendors (name, approved) VALUES ('Widgets Inc.', 1), ('Fraudster LLC', 0);
    """)
    conn.commit()
    conn.close()

    # patch the DB_PATH used by validation and approval
    original_v = validation.DB_PATH
    original_a = approval.DB_PATH
    validation.DB_PATH = path
    approval.DB_PATH = path
    yield path
    validation.DB_PATH = original_v
    approval.DB_PATH = original_a


def make_state(vendor=None, items=None, total=None, currency="USD", invoice_number="INV-TEST"):
    state = InvoiceState(file_path="test/invoice.txt")
    state.invoice_number = invoice_number
    state.vendor = vendor
    state.currency = currency
    state.total_amount = total
    state.raw_text = "test"
    state.confidence = "high"
    if items:
        for item, qty, price in items:
            state.line_items.append(LineItem(item=item, quantity=qty, unit_price=price))
    return state


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class TestInvoiceState:
    def test_halt_sets_flags(self):
        state = InvoiceState(file_path="x.txt")
        state.halt("bad data")
        assert state.halted is True
        assert state.halt_reason == "bad data"

    def test_halt_requires_reason(self):
        state = InvoiceState(file_path="x.txt")
        with pytest.raises(ValueError):
            state.halt("")

    def test_add_flag_requires_type_and_message(self):
        state = InvoiceState(file_path="x.txt")
        with pytest.raises(ValueError):
            state.add_flag("", "message")
        with pytest.raises(ValueError):
            state.add_flag("type", "")

    def test_has_flag(self):
        state = InvoiceState(file_path="x.txt")
        state.add_flag("price_variance", "too high")
        assert state.has_flag("price_variance")
        assert not state.has_flag("stock_mismatch")

    def test_to_dict_includes_decision_source_and_tool_findings(self):
        state = InvoiceState(file_path="x.txt")
        state.decision = "approved"
        state.decision_source = "auto_grok"
        state.tool_findings = [{"tool": "lookup_vendor_history", "args": {}, "result": {}}]
        d = state.to_dict()
        assert d["decision_source"] == "auto_grok"
        assert len(d["tool_findings"]) == 1

    def test_timestamps_are_utc(self):
        state = InvoiceState(file_path="x.txt")
        state.mark_stage_complete("ingestion")
        ts = state.timestamps["ingestion"]
        assert "+00:00" in ts, "Timestamp should include UTC offset"

    def test_line_item_total(self):
        li = LineItem(item="WidgetA", quantity=5, unit_price=250.0)
        assert li.total == 1250.0


# ---------------------------------------------------------------------------
# Validation — vendor checks
# ---------------------------------------------------------------------------

class TestVendorValidation:
    def test_approved_vendor_passes(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 5, 250)], total=1250)
        validation.run(state)
        assert state.vendor_status == "approved"
        assert not state.has_flag("unknown_vendor")
        assert not state.halted

    def test_bad_actor_halts(self, db_path):
        state = make_state(vendor="Fraudster LLC", items=[("WidgetA", 1, 250)], total=250)
        validation.run(state)
        assert state.vendor_status == "bad_actor"
        assert state.halted
        assert state.has_flag("bad_actor")

    def test_unknown_vendor_flags_but_does_not_halt(self, db_path):
        state = make_state(vendor="NoProd Industries", items=[("WidgetA", 1, 250)], total=250)
        validation.run(state)
        assert state.vendor_status == "unknown"
        assert state.has_flag("unknown_vendor")
        assert not state.halted

    def test_fuzzy_match_flags_possible_match(self, db_path):
        # "Widgets Inc" (missing period) should score high enough to get possible_match
        state = make_state(vendor="Widgets Inc", items=[("WidgetA", 1, 250)], total=250)
        validation.run(state)
        assert state.vendor_status == "possible_match"
        assert state.has_flag("possible_vendor_match")
        assert not state.halted

    def test_missing_vendor_flags(self, db_path):
        state = make_state(vendor=None, items=[("WidgetA", 1, 250)], total=250)
        validation.run(state)
        assert state.has_flag("missing_vendor")


# ---------------------------------------------------------------------------
# Validation — stock and items
# ---------------------------------------------------------------------------

class TestItemValidation:
    def test_stock_mismatch_flagged(self, db_path):
        # GadgetX has stock=5, requesting 20
        state = make_state(vendor="Widgets Inc.", items=[("GadgetX", 20, 750)], total=15000)
        validation.run(state)
        assert state.has_flag("stock_mismatch")

    def test_unknown_item_flagged(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("SuperGizmo", 1, 100)], total=100)
        validation.run(state)
        assert state.has_flag("unknown_item")

    def test_out_of_stock_flagged(self, db_path):
        # WidgetC has stock=0
        state = make_state(vendor="Widgets Inc.", items=[("WidgetC", 1, 350)], total=350)
        validation.run(state)
        assert state.has_flag("out_of_stock")

    def test_quantities_aggregated_across_line_items(self, db_path):
        # two lines of GadgetX, 3 each = 6 total, stock is 5 — should flag
        state = make_state(
            vendor="Widgets Inc.",
            items=[("GadgetX", 3, 750), ("GadgetX", 3, 750)],
            total=4500
        )
        validation.run(state)
        assert state.has_flag("stock_mismatch")

    def test_price_variance_flagged(self, db_path):
        # WidgetA catalog price is $250, invoicing at $320 = 28% over
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 320)], total=320)
        validation.run(state)
        assert state.has_flag("price_variance")

    def test_price_within_tolerance_passes(self, db_path):
        # WidgetA at $280 = 12% over, within 15% tolerance
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 280)], total=280)
        validation.run(state)
        assert not state.has_flag("price_variance")


# ---------------------------------------------------------------------------
# Validation — data integrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    def test_negative_quantity_flagged(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", -5, 250)], total=-1250)
        validation.run(state)
        assert state.has_flag("negative_quantity")

    def test_negative_total_flagged(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=-250)
        # manually set total to negative while keeping qty positive
        state.total_amount = -250
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        from agents.validation import check_data_integrity
        check_data_integrity(state)
        conn.close()
        assert state.has_flag("negative_total")

    def test_foreign_currency_halts(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250, currency="EUR")
        validation.run(state)
        assert state.halted
        assert state.has_flag("foreign_currency")

    def test_duplicate_invoice_in_batch_flagged(self, db_path):
        seen = {"INV-TEST"}
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        validation.run(state, seen_invoice_numbers=seen)
        assert state.has_flag("duplicate_invoice")
        assert state.halted


# ---------------------------------------------------------------------------
# Approval — hard reject flags
# ---------------------------------------------------------------------------

class TestHardRejectFlags:
    def test_bad_actor_auto_rejects(self, db_path):
        state = make_state(vendor="Fraudster LLC", items=[("WidgetA", 1, 250)], total=250)
        state.vendor_status = "bad_actor"
        state.add_flag("bad_actor", "known bad actor")
        state.halted = True
        state.halt_reason = "bad actor"
        approval.run(state)
        assert state.decision == "rejected"
        assert state.decision_source == "auto_reject"

    def test_negative_quantity_auto_rejects(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", -1, 250)], total=-250)
        state.vendor_status = "approved"
        state.add_flag("negative_quantity", "negative qty")
        approval.run(state)
        assert state.decision == "rejected"
        assert state.decision_source == "auto_reject"

    def test_missing_total_auto_rejects(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=None)
        state.vendor_status = "approved"
        state.add_flag("missing_total", "no total")
        approval.run(state)
        assert state.decision == "rejected"
        assert state.decision_source == "auto_reject"

    def test_missing_vendor_auto_rejects(self, db_path):
        state = make_state(vendor=None, items=[("WidgetA", 1, 250)], total=250)
        state.vendor_status = "unknown"
        state.add_flag("missing_vendor", "no vendor")
        approval.run(state)
        assert state.decision == "rejected"
        assert state.decision_source == "auto_reject"

    def test_no_line_items_auto_rejects(self, db_path):
        state = make_state(vendor="Widgets Inc.", items=[], total=0)
        state.vendor_status = "approved"
        state.add_flag("no_line_items", "no items")
        approval.run(state)
        assert state.decision == "rejected"
        assert state.decision_source == "auto_reject"


# ---------------------------------------------------------------------------
# Batch sort order
# ---------------------------------------------------------------------------

class TestBatchSort:
    def test_sort_newest_first(self, tmp_path):
        import time
        from main import SUPPORTED_EXTENSIONS

        # create files with different mtimes
        older = tmp_path / "invoice_1001.txt"
        newer = tmp_path / "invoice_1001_revised.txt"
        older.write_text("old")
        time.sleep(0.05)
        newer.write_text("new")

        files = [str(older), str(newer)]
        files.sort(key=lambda f: (-os.path.getmtime(f), os.path.basename(f)))
        assert files[0] == str(newer)

    def test_sort_tiebreaker_is_filename(self, tmp_path):
        # same mtime — filename should be the tiebreaker
        a = tmp_path / "invoice_b.txt"
        b = tmp_path / "invoice_a.txt"
        a.write_text("a")
        b.write_text("b")
        # force identical mtime
        mtime = os.path.getmtime(str(a))
        os.utime(str(b), (mtime, mtime))

        files = [str(a), str(b)]
        files.sort(key=lambda f: (-os.path.getmtime(f), os.path.basename(f)))
        # invoice_a.txt < invoice_b.txt alphabetically, so it comes first
        assert os.path.basename(files[0]) == "invoice_a.txt"


# ---------------------------------------------------------------------------
# Payment — failure handling and duplicate prevention
# ---------------------------------------------------------------------------

class TestPaymentFailures:
    def test_mock_payment_succeeds_by_default(self):
        from agents.payment import mock_payment
        result = mock_payment("Widgets Inc.", 1000.0)
        assert result["status"] == "success"
        assert result["transaction_id"]
        assert result["amount"] == 1000.0

    def test_mock_payment_fails_at_rate_1(self, monkeypatch):
        monkeypatch.setattr("agents.payment.PAYMENT_FAIL_RATE", 1.0)
        from agents.payment import mock_payment, PaymentError
        with pytest.raises(PaymentError):
            mock_payment("Widgets Inc.", 1000.0)

    def test_failed_payment_records_in_db(self, db_path, monkeypatch):
        """A failed payment must be recorded so a re-run doesn't try to pay again."""
        monkeypatch.setattr("agents.payment.PAYMENT_FAIL_RATE", 1.0)
        monkeypatch.setattr("agents.payment.RETRY_DELAY", 0)  # skip sleep in tests
        monkeypatch.setattr("agents.payment.DB_PATH", db_path)

        from agents import payment
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        state.decision = "approved"
        state.decision_source = "auto_grok"
        state.vendor_status = "approved"

        payment.run(state)

        assert state.payment_status == "failed"
        assert state.decision == "payment_failed"

        # verify it was recorded in the DB so duplicate check will catch it
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT decision FROM processed_invoices WHERE invoice_number = ?",
            (state.invoice_number,)
        ).fetchone()
        conn.close()
        assert row is not None, "Failed payment must be recorded in processed_invoices"
        assert row[0] == "payment_failed"

    def test_successful_payment_recorded_once(self, db_path, monkeypatch):
        """Successful payment should be in DB exactly once — no double recording."""
        monkeypatch.setattr("agents.payment.PAYMENT_FAIL_RATE", 0.0)
        monkeypatch.setattr("agents.payment.DB_PATH", db_path)

        from agents import payment
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        state.decision = "approved"
        state.vendor_status = "approved"

        payment.run(state)

        assert state.payment_status == "paid"

        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM processed_invoices WHERE invoice_number = ?",
            (state.invoice_number,)
        ).fetchone()[0]
        conn.close()
        assert count == 1, "Invoice should be recorded exactly once"

    def test_retry_succeeds_on_second_attempt(self, db_path, monkeypatch):
        """Simulate a transient failure — succeeds on attempt 2."""
        call_count = {"n": 0}

        def flaky_payment(vendor, amount):
            call_count["n"] += 1
            if call_count["n"] == 1:
                from agents.payment import PaymentError
                raise PaymentError("transient timeout")
            return {
                "transaction_id": "txn-retry-test",
                "vendor": vendor,
                "amount": amount,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "success",
            }

        monkeypatch.setattr("agents.payment.mock_payment", flaky_payment)
        monkeypatch.setattr("agents.payment.RETRY_DELAY", 0)
        monkeypatch.setattr("agents.payment.DB_PATH", db_path)

        from agents import payment
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        state.decision = "approved"
        state.vendor_status = "approved"

        payment.run(state)

        assert state.payment_status == "paid"
        assert state.payment_attempts == 2
        assert call_count["n"] == 2  # tried twice, no more

    def test_non_approved_invoice_is_not_paid(self, db_path, monkeypatch):
        monkeypatch.setattr("agents.payment.DB_PATH", db_path)
        from agents import payment
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        state.decision = "rejected"
        state.vendor_status = "approved"

        payment.run(state)

        assert state.payment_status == "skipped"
        assert state.payment_result is None

    def test_retry_payment_clears_failed_record_and_retries(self, db_path, monkeypatch):
        """After 3 failures, AP retries — the payment_failed record is cleared
        before retrying so duplicate detection doesn't block the retry."""
        monkeypatch.setattr("agents.payment.PAYMENT_FAIL_RATE", 0.0)
        monkeypatch.setattr("agents.payment.DB_PATH", db_path)
        monkeypatch.setattr("main.DB_PATH", db_path)

        # seed a payment_failed record as if a prior run failed
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO processed_invoices (invoice_number, file_path, vendor_name, decision, processed_at) VALUES (?,?,?,?,?)",
            ("INV-TEST", "test/invoice.txt", "Widgets Inc.", "payment_failed", datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

        from main import retry_payment
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        state.decision = "payment_failed"
        state.payment_status = "failed"
        state.vendor_status = "approved"

        retry_payment(state)

        assert state.payment_status == "paid"
        assert state.decision == "approved"

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT decision FROM processed_invoices WHERE invoice_number = ?",
            ("INV-TEST",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "approved"

    def test_halted_approved_invoice_is_blocked(self, db_path, monkeypatch):
        """If an invoice is somehow halted but approved, payment must be blocked."""
        monkeypatch.setattr("agents.payment.DB_PATH", db_path)
        from agents import payment
        state = make_state(vendor="Widgets Inc.", items=[("WidgetA", 1, 250)], total=250)
        state.decision = "approved"
        state.halted = True
        state.halt_reason = "foreign currency"

        payment.run(state)

        assert state.payment_status == "blocked"
        assert state.payment_result is None
