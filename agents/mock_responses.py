# mock grok responses keyed by invoice number
# used when MOCK_GROK=true in .env so we can test the pipeline without burning API credits

MOCK_INGESTION = {
    "INV-1001": {
        "invoice_number": "INV-1001",
        "vendor": "Widgets Inc.",
        "date": "2026-01-15",
        "due_date": "2026-02-01",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 10, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 5,  "unit_price": 500.00},
        ],
        "total_amount": 5000.00,
        "confidence": "high",
    },
    "INV-1002": {
        "invoice_number": "INV-1002",
        "vendor": "Gadgets Co.",
        "date": "2026-01-30",
        "due_date": "2026-01-30",
        "currency": "USD",
        "line_items": [
            {"item": "GadgetX", "quantity": 20, "unit_price": 750.00},
        ],
        "total_amount": 15000.00,
        "confidence": "medium",  # typos in source but data is extractable
    },
    "INV-1003": {
        "invoice_number": "INV-1003",
        "vendor": "Fraudster LLC",
        "date": "2026-01-20",
        "due_date": "2026-01-19",  # due yesterday
        "currency": "USD",
        "line_items": [
            {"item": "FakeItem", "quantity": 100, "unit_price": 1000.00},
        ],
        "total_amount": 100000.00,
        "confidence": "high",  # clearly extracted, just fraudulent
    },
    "INV-1004": {
        "invoice_number": "INV-1004",
        "vendor": "Precision Parts Ltd.",
        "date": "2026-01-22",
        "due_date": "2026-02-22",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 3, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 2, "unit_price": 500.00},
        ],
        "total_amount": 1890.00,
        "confidence": "high",
    },
    "INV-1005": {
        "invoice_number": "INV-1005",
        "vendor": "Global Supply Chain Partners",
        "date": "2026-01-18",
        "due_date": "2026-03-18",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 14, "unit_price": 250.00},
            {"item": "GadgetX", "quantity": 8,  "unit_price": 750.00},
            {"item": "WidgetB", "quantity": 10, "unit_price": 500.00},
        ],
        "total_amount": 15225.00,
        "confidence": "high",
    },
    "INV-1006": {
        "invoice_number": "INV-1006",
        "vendor": "Acme Industrial Supplies",
        "date": "2026-01-25",
        "due_date": "2026-02-10",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 5, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 3, "unit_price": 500.00},
        ],
        "total_amount": 2750.00,
        "confidence": "medium",  # unusual key-value csv format
    },
    "INV-1007": {
        "invoice_number": "INV-1007",
        "vendor": "MegaWidgets Corp",
        "date": "2026-01-28",
        "due_date": "2026-02-28",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 20, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 15, "unit_price": 500.00},
            {"item": "GadgetX", "quantity": 3,  "unit_price": 750.00},
        ],
        "total_amount": 15525.00,
        "confidence": "high",
    },
    "INV-1008": {
        "invoice_number": "INV-1008",
        "vendor": "NoProd Industries",
        "date": "2026-01-10",
        "due_date": "2026-01-20",
        "currency": "USD",
        "line_items": [
            {"item": "SuperGizmo",   "quantity": 12, "unit_price": 400.00},
            {"item": "MegaSprocket", "quantity": 6,  "unit_price": 850.00},
        ],
        "total_amount": 9900.00,
        "confidence": "high",
    },
    "INV-1009": {
        "invoice_number": "INV-1009",
        "vendor": "",
        "date": "2026-01-15",
        "due_date": None,
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": -5, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 2,  "unit_price": 500.00},
        ],
        "total_amount": -250.00,
        "confidence": "low",
    },
    "INV-1010": {
        "invoice_number": "INV-1010",
        "vendor": "Consolidated Materials Group",
        "date": "2026-01-27",
        "due_date": "2026-02-26",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 8, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 4, "unit_price": 500.00},
            {"item": "GadgetX", "quantity": 2, "unit_price": 750.00},
            {"item": "WidgetA", "quantity": 4, "unit_price": 300.00},  # rush order, price variance
        ],
        "total_amount": 7185.00,
        "confidence": "high",
    },
    "INV-1011": {
        "invoice_number": "INV-1011",
        "vendor": "Summit Manufacturing Co.",
        "date": "2026-01-20",
        "due_date": "2026-02-20",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 6, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 3, "unit_price": 500.00},
        ],
        "total_amount": 3000.00,
        "confidence": "high",
    },
    "INV-1012": {
        "invoice_number": "INV-1012",
        "vendor": "QuickShip Distributers",
        "date": "2026-01-26",
        "due_date": "2026-02-25",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 12, "unit_price": 250.00},  # normalized from "Widget A"
            {"item": "WidgetB", "quantity": 7,  "unit_price": 500.00},
            {"item": "GadgetX", "quantity": 4,  "unit_price": 750.00},  # normalized from "Gadget X"
        ],
        "total_amount": 9975.00,
        "confidence": "medium",  # ocr artifacts in source
    },
    "INV-1013": {
        "invoice_number": "INV-1013",
        "vendor": "Atlas Industrial Supply",
        "date": "2026-01-24",
        "due_date": "2026-03-24",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 15, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 10, "unit_price": 500.00},
            {"item": "GadgetX", "quantity": 5,  "unit_price": 750.00},
            {"item": "WidgetA", "quantity": 5,  "unit_price": 240.00},  # volume discount
            {"item": "WidgetB", "quantity": 8,  "unit_price": 480.00},  # volume discount
            {"item": "GadgetX", "quantity": 3,  "unit_price": 750.00},  # expedited
            {"item": "WidgetA", "quantity": 2,  "unit_price": 250.00},  # replacement
            {"item": "GadgetX", "quantity": 1,  "unit_price": 750.00},  # sample
        ],
        "total_amount": 22562.80,
        "confidence": "high",
    },
    "INV-1014": {
        "invoice_number": "INV-1014",
        "vendor": "TechParts International",
        "date": "2026-01-26",
        "due_date": "2026-02-26",
        "currency": "EUR",
        "line_items": [
            {"item": "WidgetA", "quantity": 4, "unit_price": 225.00},
            {"item": "WidgetB", "quantity": 6, "unit_price": 475.00},
        ],
        "total_amount": 4125.00,
        "confidence": "high",
    },
    "INV-1015": {
        "invoice_number": "INV-1015",
        "vendor": "Reliable Components Inc.",
        "date": "2026-01-29",
        "due_date": "2026-02-28",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 10, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 5,  "unit_price": 500.00},
            {"item": "GadgetX", "quantity": 2,  "unit_price": 750.00},
        ],
        "total_amount": 6500.00,
        "confidence": "high",
    },
    "INV-1016": {
        "invoice_number": "INV-1016",
        "vendor": "Widgets Inc.",
        "date": "2026-01-27",
        "due_date": "2026-02-27",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 4, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 2, "unit_price": 500.00},
            {"item": "WidgetC", "quantity": 3, "unit_price": 350.00},
        ],
        "total_amount": 3233.00,
        "confidence": "high",
    },
    "INV-1017": {
        "invoice_number": "INV-1017",
        "vendor": "Widgets lnc",  # lowercase L not capital I, classic spoofing attempt
        "date": "2026-01-31",
        "due_date": "2026-03-02",
        "payment_terms": "Net 30",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 5, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 2, "unit_price": 500.00},
        ],
        "total_amount": 2250.00,
        "confidence": "high",
    },
    "INV-1018": {
        "invoice_number": "INV-1018",
        "vendor": "Precision Parts Ltd.",
        "date": "2026-01-31",
        "due_date": "2026-03-02",
        "payment_terms": "Net 30",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 15, "unit_price": 250.00},
            {"item": "WidgetB", "quantity": 10, "unit_price": 500.00},
            {"item": "GadgetX", "quantity": 5,  "unit_price": 750.00},
        ],
        "total_amount": 12500.00,
        "confidence": "high",
    },
    "INV-1019": {
        "invoice_number": "INV-1019",
        "vendor": "Gadgets Co.",
        "date": "2026-01-31",
        "due_date": "2026-03-02",
        "payment_terms": "Net 30",
        "currency": "USD",
        "line_items": [
            {"item": "WidgetA", "quantity": 3,  "unit_price": 250.00},
            {"item": "GadgetX", "quantity": 10, "unit_price": 750.00},  # 10 requested, only 5 in stock
            {"item": "WidgetB", "quantity": 2,  "unit_price": 500.00},
        ],
        "total_amount": 8750.00,
        "confidence": "high",
    },
}

# mock approval responses keyed by invoice number
# just covers the main cases, real grok handles the nuanced ones
MOCK_APPROVAL = {
    "approved": {
        "decision": "approved",
        "reasoning": "Invoice passes all validation checks. Vendor is approved, items are in stock, quantities and pricing are within expected ranges.",
    },
    "rejected_fraud": {
        "decision": "rejected",
        "reasoning": "Multiple fraud indicators present: vendor is on the blocked list, item has zero stock and appears fabricated, payment terms are suspicious (immediate wire transfer demanded).",
    },
    "rejected_stock": {
        "decision": "rejected",
        "reasoning": "Requested quantities exceed available stock. Cannot approve an invoice we cannot fulfill.",
    },
    "human_review": {
        "decision": "human_review",
        "reasoning": "Invoice flagged for human review due to unknown vendor or foreign currency. Cannot auto-approve without confirmation.",
    },
}
