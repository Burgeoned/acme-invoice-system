# Acme Invoice Processing Automation — Design Doc
Yun Cheih Lee | June 2026

---

## The Problem

Acme Corp is losing $2M/year on manual invoice processing. Invoices come in as PDFs, someone types the data into a system, a VP signs off over email, and someone calls a bank API. That chain has a 30% error rate and takes five days on average.

The goal is to automate as much of that as possible without introducing new failure modes.

---

## Architecture

Four agents in a fixed sequence. Each one reads from and writes to a shared InvoiceState object.

Invoice file -> Ingestion -> Validation -> Approval -> Payment -> Output

I didn't use LangGraph or CrewAI. The flow is always linear — there's no dynamic routing or agent collaboration happening here. An orchestration framework solves a problem this system doesn't have, and adds surface area for things to break.

---

## The Agents

### Agent 1: Ingestion

Reads whatever format the invoice comes in (PDF, TXT, JSON, CSV, XML), extracts the raw text, and sends it to Grok for structured extraction. Gets back vendor, amount, line items, and due date as clean fields.

Grok also normalizes item names during extraction — "Widget A" and "Gadget X" (with spaces, common in OCR output) get mapped to "WidgetA" and "GadgetX" so validation can match them against the DB cleanly.

Every extraction gets a confidence score (high / medium / low). An invoice where Grok had to guess on a malformed PDF should be treated differently than a clean JSON. Low-confidence extractions are flagged — in a real system, those would route to human review.

### Agent 2: Validation

Checks the extracted data against SQLite. Runs these checks in order:

1. **Foreign currency** — if currency is not USD, flag for human review and stop. We can't safely approve without knowing exchange rate policy.
2. **Duplicate invoice number** — if the invoice number has been seen before, keep only the latest file (by modified date) and discard the earlier one.
3. **Vendor check** — three outcomes:
   - Exact match (case-insensitive) -> approved, continue
   - Known bad actor (in table, approved = 0) -> halt immediately, auto-reject
   - No exact match but similarity >= 90% -> flag as possible match, halt for human review. Surfaces the close match so a reviewer can confirm rather than the system assuming. Catches typos and formatting differences without auto-approving a spoofed vendor.
   - No match, no close match -> unknown vendor, halt for human review

   Case-insensitive matching handles the common case ("widgets inc" vs "Widgets Inc."). Fuzzy matching handles formatting differences ("Widgets Incorporated" vs "Widgets Inc.") but is capped at a high threshold to avoid false positives — a bad actor named "Widgets lnc" (letter substitution) should not auto-match.
4. **Item existence** — does each item exist in the catalog? "Unknown item" and "out of stock" are different failure modes and should produce different messages.
5. **Stock check** — quantities are aggregated per item across all line items before checking stock. An invoice with the same item on three lines (e.g. volume discounts) gets the quantities summed first.
6. **Price check** — invoice unit price is compared against the DB price. Variance within 15% passes. Over 15% adds a price flag to state; Grok decides in the approval stage. This threshold allows rush order markups and volume discounts to pass through to judgment rather than hard-rejecting.

No LLM in validation. Every check is deterministic — it either matches the DB or it doesn't. Using Grok here would be slower, more expensive, and harder to audit.

### Agent 3: Approval

Three paths:

- Already halted from validation (bad actor, unknown vendor, foreign currency) -> set decision based on halt reason, don't call Grok
- Hard fraud flags (bad_actor, negative_quantity, negative_total) -> auto-reject immediately, no Grok call needed
- Everything else -> Grok reasons through it with a self-critique loop

The self-critique loop is two Grok calls. First call makes an initial decision with reasoning. Second call receives that decision and critiques it — asks whether it was too lenient, too strict, or missed anything — and can change the decision if the critique reveals a problem. Invoices over $10K get flagged as high value in the prompt so Grok applies extra scrutiny.

If the first Grok call fails, default to rejected and log the error. If only the critique call fails, use the first decision rather than defaulting to rejected — we still have a valid answer, just unreviewed.

If Grok returns a decision value that isn't approved, rejected, or human_review, default to human_review and log the unexpected value.

Three possible outcomes: approved, rejected, human_review.

**Flag types validation can raise:**
- stock_mismatch: requested quantity exceeds available stock
- out_of_stock: item exists in catalog but stock is zero
- unknown_item: item not in catalog at all
- price_variance: invoice price deviates more than 15% from DB price
- unknown_vendor: vendor not in approved list
- possible_vendor_match: vendor not found but closely matches a known vendor (>=90% similarity)
- bad_actor: vendor is on the blocked list
- foreign_currency: invoice is not in USD
- duplicate_invoice: invoice number already processed in this batch
- negative_quantity: line item has negative quantity
- negative_total: invoice total is negative
- missing_total: invoice has no total amount
- no_line_items: invoice has no line items
- missing_vendor: no vendor name on invoice
- low_confidence: Grok flagged extraction confidence as low
- malformed_line_item: a line item could not be parsed

### Agent 4: Payment

Approved -> mock_payment() -> log confirmation.
Rejected -> log reason.

Does a final state check before calling payment. If an invoice somehow reaches this stage without approval, it gets blocked here.

---

## Database Schema

Three tables.

### items

Master catalog — what items exist and what they cost. This is the source of truth for item existence and expected pricing.

| column     | type    |
|------------|---------|
| item       | TEXT PK |
| unit_price | REAL    |

### inventory

Stock levels. References items.

| column | type    |
|--------|---------|
| item   | TEXT PK |
| stock  | INTEGER |

Keeping these separate means "item doesn't exist" (not in items) and "item exists but zero stock" (in items, stock = 0) are distinct — they produce different validation messages and mean different things operationally.

Seed data:

| item     | stock | unit_price |
|----------|-------|------------|
| WidgetA  | 15    | 250.00     |
| WidgetB  | 10    | 500.00     |
| WidgetC  | 0     | 350.00     |
| GadgetX  | 5     | 750.00     |
| FakeItem | 0     | 0.00       |

### processed_invoices

Tracks every invoice that has completed the pipeline. Used to catch duplicates across sessions — if a new file comes in claiming the same invoice number as something already processed, it gets flagged for review. Same file reprocessed is allowed (the user might be re-checking something), but a different file with the same invoice number is not.

| column         | type    |
|----------------|---------|
| invoice_number | TEXT    |
| file_path      | TEXT    |
| decision       | TEXT    |
| processed_at   | TEXT    |

Primary key is (invoice_number, file_path) together so the same file can be reprocessed without conflict.

### vendors

Approved supplier whitelist. Anything not in this table gets flagged.

| column   | type    |
|----------|---------|
| name     | TEXT PK |
| approved | INTEGER |

This is an addition beyond the minimum schema. Item-level checks catch bad quantities and unknown SKUs, but won't catch a spoofed vendor. The vendors table adds a second signal that the baseline schema misses.

Two distinct states: a vendor not in this table is unknown (never seen before, flag for review). A vendor in this table with approved = 0 is a known bad actor (explicit rejection). Fraudster LLC is seeded as approved = 0. NoProd Industries is not in the table — they're unknown, not confirmed bad.

---

## Design Decisions

**Why not LangGraph/CrewAI**
Fixed sequential pipeline. A routing framework doesn't add anything here and makes the code harder to follow.

**Why SQLite**
Zero setup, single file, runs locally. Simulates a legacy inventory system without needing a server.

**Why the OpenAI client instead of xai_sdk**
xAI's API is OpenAI-compatible by design — their own docs recommend the OpenAI client. More stable, better documented, one less dependency.

**Why separate files per agent**
Each agent can fail independently. When something breaks, I want to open one file and fix it — not trace through a monolithic pipeline.

**Why three tables instead of two**
The minimum schema merges item existence and stock levels into one table. Separating them into items + inventory means validation can tell the difference between "this item doesn't exist in our catalog" (INV-1008: SuperGizmo) vs "this item exists but is out of stock" (INV-1016: WidgetC). Those are different problems and should produce different messages.

**Why 15% price tolerance**
Hard price matching rejects legitimate invoices — rush orders run at a markup, volume orders come with discounts. 15% captures both without letting through significant price gouging. Anything over the threshold doesn't auto-reject; it gets flagged and Grok reasons about it in approval.

**Why snapshot stock in batch mode**
Each invoice is checked against DB stock independently. In a real system you'd have stock reservations, but for a prototype that introduces non-determinism in tests — the first invoice to run would affect every one after it. Snapshot keeps the evals consistent.

**Why sort batch files by modified date**
Alphabetical sort is coincidentally correct for most test invoices but breaks down for revised invoices. INV-1004 and INV-1004_revised have the same invoice number — sorting by modified date means the revised file always comes second, gets caught by the duplicate check, and the correct version (the one that was processed first, which is the older original) is kept. If we wanted to keep the latest revision instead we'd reverse the sort and let the revised one process first.

**Why run_single and run_batch are separate functions**
The CLI uses them directly, but so does the UI. Exposing them as importable functions means the UI can call them in-process and get results back as Python objects rather than having to spawn a subprocess and parse stdout.

---

## Edge Cases

**Ingestion**
- Empty or unreadable file -> fail early, don't pass garbage downstream
- PDF with no extractable text -> Grok attempts extraction, flags low confidence
- Grok returns malformed JSON -> retry once with stricter prompt
- Missing fields -> default to empty/zero, flag low confidence
- OCR artifacts ("Widget A", "2O26", "$3,500.O0") -> Grok normalizes item names during extraction

**Validation**
- Foreign currency -> flag for human review, stop processing
- Duplicate invoice number -> keep latest file only, discard earlier
- Item name casing differences -> case-insensitive DB query
- Same item multiple times on one invoice -> aggregate quantities before stock check
- Negative quantity -> data integrity flag
- Zero amount -> flagged as suspicious
- Vendor not in whitelist, no close match -> unknown vendor flag
- Vendor not in whitelist but high similarity match (>=90%) -> possible match flag, surfaces the candidate for human review
- Vendor in whitelist, approved = 0 -> known bad actor, immediate halt
- Price variance >15% -> price flag, passed to Grok in approval

**Approval**
- Fraud flags + small amount -> Grok still reasons through it, no automatic pass
- Over $10K with no flags -> extra scrutiny, not auto-reject
- Price flag present -> Grok receives variance amount and reasons about legitimacy
- Grok timeout or error -> defaults to reject, error logged

**Payment**
- Mock payment fails -> logged, system doesn't crash
- Invoice reaches payment without approval -> blocked at final state check

---

## Above and Beyond

- Extraction confidence score on every ingestion — surfaces how reliable the parse was
- Vendor whitelist table — catches a fraud signal the minimum schema misses
- Three-table schema — separates item existence from stock levels for cleaner validation messages
- Price variance check with tolerance threshold — flags outliers for Grok rather than hard-rejecting
- Batch mode + business metrics — run all invoices at once, see approval rate, fraud rate, and total dollar value auto-processed
- Foreign currency detection — flags non-USD invoices for human review

---

## Invoice Test Cases

| Invoice | Format | Scenario | Expected |
|---------|--------|----------|----------|
| INV-1001 | TXT | Clean order, normal stock | Approved |
| INV-1002 | TXT | Typos throughout, 20x GadgetX (stock: 5) | Rejected — stock mismatch |
| INV-1003 | TXT | Fake vendor, FakeItem, "URGENT wire transfer" | Rejected — fraud |
| INV-1004 | JSON | Clean order with tax | Approved |
| INV-1004 revised | JSON | Duplicate invoice number, newer file | Replaces INV-1004, processed as latest |
| INV-1005 | JSON | $15,225 total, 8x GadgetX (stock: 5) | Rejected — stock mismatch |
| INV-1006 | CSV | Key-value format, single item | Approved |
| INV-1007 | CSV | $15,525 total, 20x WidgetA (stock: 15) | Rejected — stock mismatch |
| INV-1008 | TXT | Items not in catalog (SuperGizmo, MegaSprocket) | Rejected — unknown items |
| INV-1009 | JSON | Negative quantity, blank vendor, negative total | Rejected — data integrity |
| INV-1010 | TXT | Rush order line item, price variance | Flagged — Grok decides |
| INV-1011 | PDF | Clean, well-structured | Approved |
| INV-1012 | TXT+PDF | OCR artifacts, spacing in item names | Approved if Grok normalizes correctly |
| INV-1013 | JSON+PDF | $22,562, duplicate items at discount prices, aggregated stock check | Rejected — exceeds stock when aggregated |
| INV-1014 | XML | EUR currency | Flagged — human review |
| INV-1015 | CSV | Clean, tabular format | Approved |
| INV-1016 | JSON | WidgetC exists in catalog, stock = 0 | Rejected — out of stock |

Target: 80% of low-risk invoices auto-processed end-to-end. On Acme's volume, that's roughly $1.6M in annual savings.
