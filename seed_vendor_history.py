"""
Seed prior invoice history for all approved vendors so they hit the "trusted" tier
(5+ approved invoices, no rejections) before running the batch.

This simulates what the system looks like after a few weeks of real use,
not a cold start. Run this after setup_db.py to compare warm vs cold behavior.

Usage:
    python seed_vendor_history.py
"""

import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = "inventory.db"

APPROVED_VENDORS = [
    "Widgets Inc.",
    "Gadgets Co.",
    "Precision Parts Ltd.",
    "Global Supply Chain Partners",
    "Acme Industrial Supplies",
    "MegaWidgets Corp",
    "QuickShip Distributers",
    "Consolidated Materials Group",
    "Summit Manufacturing Co.",
    "Atlas Industrial Supply",
    "TechParts International",
    "Reliable Components Inc.",
]

PRIOR_INVOICES_PER_VENDOR = 6  # enough to hit the trusted tier (5+ threshold)


def seed():
    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = 0
        for i, vendor in enumerate(APPROVED_VENDORS):
            for j in range(PRIOR_INVOICES_PER_VENDOR):
                invoice_number = f"HIST-{i:02d}{j:02d}"
                file_path = f"data/processed/historical_{vendor.replace(' ', '_').replace('.', '')}_{j}.json"
                processed_at = (
                    datetime.now(timezone.utc) - timedelta(days=90 - (i * 5 + j))
                ).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO processed_invoices "
                    "(invoice_number, file_path, vendor_name, decision, processed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (invoice_number, file_path, vendor, "approved", processed_at)
                )
                inserted += 1

        conn.commit()
        print(f"Seeded {inserted} prior approved invoices across {len(APPROVED_VENDORS)} vendors.")
        print("All vendors now have 6 prior approved invoices — trusted tier applies ($25K threshold).")
    except Exception as e:
        conn.rollback()
        print(f"Error seeding vendor history: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
