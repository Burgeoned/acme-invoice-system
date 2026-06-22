import sqlite3
import os

DB_PATH = "inventory.db"


def setup():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
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
            ("NoProd Industries",            0),
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
    conn.close()
    print(f"Database created at {DB_PATH}")


if __name__ == "__main__":
    setup()
