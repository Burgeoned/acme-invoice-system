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

Every extraction also gets a confidence score (high / medium / low). An invoice where Grok had to guess on a malformed PDF should be treated differently than a clean JSON. Low-confidence extractions are flagged — in a real system, those would route to human review.

### Agent 2: Validation

Checks the extracted line items against SQLite. Is the item in inventory? Is there enough stock? Is the vendor on the approved list?

No LLM here. Validation is deterministic — it either matches the DB or it doesn't. Using Grok for this would be slower, more expensive, and harder to audit.

I added a vendors table beyond the minimum schema. Item-level checks catch bad quantities and unknown SKUs, but won't catch a spoofed vendor. The vendors table adds a second signal that the baseline schema misses.

### Agent 3: Approval

Two paths:

- Fraud flags present -> auto-reject, don't call Grok
- Everything else -> Grok reasons through it

The Grok call includes a self-correction loop: it generates an initial decision, then critiques that decision before finalizing. Invoices over $10K get extra scrutiny even if they look clean.

If Grok times out or errors, the system defaults to reject and logs the error. Better to require a re-review than to silently approve something.

### Agent 4: Payment

Approved -> mock_payment() -> log confirmation.
Rejected -> log reason.

Does a final state check before calling payment. If an invoice somehow reaches this stage without approval, it gets blocked here.

---

## Database Schema

Two tables.

### inventory

Tracks what's in stock and what it costs.

| column     | type    |
|------------|---------|
| item       | TEXT PK |
| stock      | INTEGER |
| unit_price | REAL    |

Seed data: WidgetA (15), WidgetB (10), WidgetC (0), GadgetX (5), FakeItem (0).  
WidgetC and FakeItem are intentional edge cases — zero-stock items the test invoices hit.

### vendors

Approved supplier whitelist. Anything not in this table gets flagged.

| column   | type    |
|----------|---------|
| name     | TEXT PK |
| approved | INTEGER |

This is an addition beyond the minimum schema. The prompt mentioned extending the seed data to support richer validation — I added this because unknown vendors are a meaningfully different failure mode than bad item quantities.

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

---

## Edge Cases

**Ingestion**
- Empty or unreadable file -> fail early, don't pass garbage downstream
- PDF with no extractable text -> Grok attempts extraction, flags low confidence
- Grok returns malformed JSON -> retry once with stricter prompt
- Missing fields -> default to empty/zero, flag low confidence

**Validation**
- Item name casing differences -> case-insensitive DB query
- Negative quantity -> data integrity flag
- Zero amount -> flagged as suspicious
- Vendor not in whitelist -> unknown vendor flag

**Approval**
- Fraud flags + small amount -> Grok still reasons through it, no automatic pass
- Over $10K with no flags -> extra scrutiny, not auto-reject
- Grok timeout or error -> defaults to reject, error logged

**Payment**
- Mock payment fails -> logged, system doesn't crash
- Invoice reaches payment without approval -> blocked at final state check

---

## Above and Beyond

- Extraction confidence score on every ingestion — surfaces how reliable the parse was
- Vendor whitelist table — catches a fraud signal the minimum schema misses
- Batch mode + business metrics — run all invoices at once, see approval rate, fraud rate, and total dollar value auto-processed

---

## Evals

| Invoice | What it tests | Expected |
|---------|--------------|----------|
| INV-1001 | Clean invoice, normal stock | Approved |
| INV-1002 | 20x GadgetX, only 5 in stock | Rejected |
| INV-1003 | FakeItem, fake vendor, "URGENT wire transfer" | Rejected |
| INV-1008 | Items not in DB | Rejected |
| INV-1009 | Negative quantity | Rejected |
| INV-1016 | WidgetC, zero stock | Rejected |

Target: 80% of low-risk invoices auto-processed end-to-end. On Acme's volume, that's roughly $1.6M in annual savings.
