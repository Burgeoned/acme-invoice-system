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
3. **Vendor check** — is the vendor in the approved list?
4. **Item existence** — does each item exist in the catalog? "Unknown item" and "out of stock" are different failure modes and should produce different messages.
5. **Stock check** — quantities are aggregated per item across all line items before checking stock. An invoice with the same item on three lines (e.g. volume discounts) gets the quantities summed first.
6. **Price check** — invoice unit price is compared against the DB price. Variance within 15% passes. Over 15% adds a price flag to state; Grok decides in the approval stage. This threshold allows rush order markups and volume discounts to pass through to judgment rather than hard-rejecting.

No LLM in validation. Every check is deterministic — it either matches the DB or it doesn't. Using Grok here would be slower, more expensive, and harder to audit.

### Agent 3: Approval

Two paths:

- Hard fraud flags present (unknown vendor + suspicious language, negative quantities, negative total) -> auto-reject, don't call Grok
- Everything else -> Grok reasons through it

The Grok call includes a self-correction loop: it generates an initial decision, then critiques that decision before finalizing. Invoices over $10K get extra scrutiny even if they look clean. Price flags from validation get passed to Grok with the variance amount so it can reason about whether the deviation is legitimate.

If Grok times out or errors, the system defaults to reject and logs the error. Better to require a re-review than to silently approve something.

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

### vendors

Approved supplier whitelist. Anything not in this table gets flagged.

| column   | type    |
|----------|---------|
| name     | TEXT PK |
| approved | INTEGER |

This is an addition beyond the minimum schema. Item-level checks catch bad quantities and unknown SKUs, but won't catch a spoofed vendor. The vendors table adds a second signal that the baseline schema misses.

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
- Vendor not in whitelist -> unknown vendor flag
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
