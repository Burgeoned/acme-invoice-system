"""
Company context for the approval agent.

In production this would be populated from the ERP, vendor contracts,
and historical invoice data. Right now it's seeded manually as a skeleton
so the agent has something to reason against and the structure is in place
when real data is available.
"""

# AP policies the approval agent should apply
AP_POLICIES = """
Acme Corp Accounts Payable Policies:

Payment Terms:
- Standard terms are Net 30. Shorter terms from approved vendors are acceptable. Paying sooner is fine and sometimes comes with early payment discounts.
- Short terms (under 15 days) or "due on receipt" from a new or unrecognized vendor is a pressure tactic and should be flagged. Urgency is a fraud signal when the vendor relationship is not established.
- Due dates more than 60 days out are unusual and should be flagged.
- "Urgent wire transfer" or "immediate payment required" language is a fraud signal regardless of vendor.

Order Size:
- Routine orders: $500 to $8,000. No additional scrutiny needed if vendor is approved.
- Large orders: $8,000 to $10,000. Verify line items match a real business need.
- High value: over $10,000. Requires explicit VP approval. Extra scrutiny on vendor and pricing.

Rush Orders:
- Rush or expedited delivery may carry up to 20% price markup. Acceptable if vendor is approved.
- Markups over 20% require justification even on approved vendors.

New Vendors:
- Any vendor not on the approved list requires procurement sign-off before payment.
- First invoices from new vendors should be treated as higher risk regardless of amount.

Fraud Signals:
- "Wire transfer" or "urgent payment" language in invoice notes is a red flag.
- Requests to change payment destination mid-process should be blocked immediately.
- Vendors with slight name variations from known suppliers (e.g. "Widgets lnc" vs "Widgets Inc") should not be auto-approved.
"""

# Known vendor profiles. Each entry covers what items this vendor typically supplies,
# expected price ranges, and any notes about their order patterns.
# Populate from vendor contracts and ERP data in production.
VENDOR_PROFILES = {
    "Widgets Inc.": {
        "typical_items": ["WidgetA", "WidgetB"],
        "price_ranges": {
            "WidgetA": (230, 290),
            "WidgetB": (475, 525),
        },
        "typical_order_size": (1000, 6000),
        "notes": "Primary Widget supplier. Clean history. PDF and TXT invoices.",
    },
    "Gadgets Co.": {
        "typical_items": ["GadgetX"],
        "price_ranges": {
            "GadgetX": (720, 780),
        },
        "typical_order_size": (1500, 8000),
        "notes": "Sole GadgetX supplier. High volume orders are common.",
    },
    "Precision Parts Ltd.": {
        "typical_items": ["WidgetA", "WidgetB", "GadgetX"],
        "price_ranges": {
            "WidgetA": (240, 265),
            "WidgetB": (490, 510),
            "GadgetX": (740, 760),
        },
        "typical_order_size": (2000, 10000),
        "notes": "Multi-item supplier. JSON invoices with tax line. Revised invoices are common.",
    },
    "Summit Manufacturing Co.": {
        "typical_items": ["WidgetA", "WidgetB"],
        "price_ranges": {
            "WidgetA": (245, 260),
            "WidgetB": (495, 510),
        },
        "typical_order_size": (2000, 5000),
        "notes": "PDF invoices. Clean order history.",
    },
    "Parts Express": {
        "typical_items": ["WidgetA"],
        "price_ranges": {
            "WidgetA": (235, 255),
        },
        "typical_order_size": (500, 3000),
        "notes": "CSV format invoices. Single-item orders typical.",
    },
}

# What normal batch orders look like for context.
# Helps the agent flag unusually large or unusually cheap orders.
ORDER_NORMS = {
    "typical_single_item_qty": 10,
    "high_volume_threshold": 15,        # quantities above this are unusual and worth a second look
    "bulk_discount_max_pct": 10,        # legitimate bulk discounts rarely exceed 10%
    "rush_markup_max_pct": 20,          # rush orders up to 20% over catalog price are normal
}


def get_vendor_profile(vendor_name: str) -> dict | None:
    """
    Return the profile for a vendor by name.
    Case-insensitive, partial match on the start of the name.
    Returns None if no profile exists.
    """
    vendor_lower = vendor_name.lower().strip()
    for name, profile in VENDOR_PROFILES.items():
        if vendor_lower == name.lower() or name.lower().startswith(vendor_lower[:8]):
            return {"vendor": name, **profile}
    return None


def format_vendor_profile(profile: dict) -> str:
    """Format a vendor profile as a readable string for the Grok prompt."""
    if not profile:
        return "No profile found for this vendor."

    lines = [f"Vendor: {profile['vendor']}"]

    if profile.get("typical_items"):
        lines.append(f"Typical items: {', '.join(profile['typical_items'])}")

    if profile.get("price_ranges"):
        ranges = ", ".join(
            f"{item} ${lo}-${hi}"
            for item, (lo, hi) in profile["price_ranges"].items()
        )
        lines.append(f"Expected price ranges: {ranges}")

    if profile.get("typical_order_size"):
        lo, hi = profile["typical_order_size"]
        lines.append(f"Typical order size: ${lo:,} to ${hi:,}")

    if profile.get("notes"):
        lines.append(f"Notes: {profile['notes']}")

    return "\n".join(lines)
