import sqlite3
import os

DB_PATH = "inventory.db"


def setup():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except PermissionError:
            print(f"Error: {DB_PATH} is locked by another process. Close any DB connections and try again.")
            return

    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE items (
                item       TEXT PRIMARY KEY,
                unit_price REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE inventory (
                item  TEXT PRIMARY KEY,
                stock INTEGER NOT NULL,
                FOREIGN KEY (item) REFERENCES items(item)
            )
        """)

        cursor.execute("""
            CREATE TABLE vendors (
                name     TEXT PRIMARY KEY,
                approved INTEGER NOT NULL
            )
        """)

        # tracks every invoice that has been processed so duplicates get caught across sessions
        cursor.execute("""
            CREATE TABLE processed_invoices (
                invoice_number TEXT NOT NULL,
                file_path      TEXT NOT NULL,
                vendor_name    TEXT,
                decision       TEXT,
                processed_at   TEXT NOT NULL,
                PRIMARY KEY (invoice_number, file_path)
            )
        """)

        cursor.executemany(
            "INSERT INTO items VALUES (?, ?)",
            [
                ("WidgetA",  250.00),
                ("WidgetB",  500.00),
                ("WidgetC",  350.00),
                ("GadgetX",  750.00),
                ("FakeItem",   0.00),
            ]
        )

        cursor.executemany(
            "INSERT INTO inventory VALUES (?, ?)",
            [
                ("WidgetA",  15),
                ("WidgetB",  10),
                ("WidgetC",   0),
                ("GadgetX",   5),
                ("FakeItem",  0),
            ]
        )

        cursor.executemany(
            "INSERT INTO vendors VALUES (?, ?)",
            [
                ("Widgets Inc.",                 1),
                ("Gadgets Co.",                  1),
                ("Precision Parts Ltd.",         1),
                ("Global Supply Chain Partners", 1),
                ("Acme Industrial Supplies",     1),
                ("MegaWidgets Corp",             1),
                ("QuickShip Distributers",       1),
                ("Consolidated Materials Group", 1),
                ("Summit Manufacturing Co.",     1),
                ("Atlas Industrial Supply",      1),
                ("TechParts International",      1),
                ("Reliable Components Inc.",     1),
                ("Fraudster LLC",                0),
            ]
        )

        conn.commit()
        print(f"Database created at {DB_PATH}")

    except Exception as e:
        conn.rollback()
        print(f"Error setting up database: {e}")
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    finally:
        conn.close()


if __name__ == "__main__":
    setup()
