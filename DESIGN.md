# Acme Invoice Processing Automation - Design Doc
Yun Cheih Lee | June 2026

---

## The Problem

Acme Corp is losing $2M/year on manual invoice processing. Invoices come in as PDFs, someone types the data into a system, a VP signs off over email, and someone calls a bank API. That chain has a 30% error rate and takes five days on average.

The goal is to automate as much of that as possible without introducing new failure modes.

---

## Architecture

Four agents in a fixed sequence. Each one reads from and writes to a shared InvoiceState object.

Invoice file -> Ingestion -> Validation -> Approval -> Payment -> Output

Each invoice gets its own InvoiceState, created fresh at the start of run_single. The agents run sequentially on that object. No two agents ever touch the same state at the same time, so there's no locking needed. The only shared resource across invoices is SQLite, which handles its own concurrency. If we parallelized batch processing with threads, each thread would still get its own state object — the only thing that would need attention is enabling WAL mode on the DB so readers and writers don't block each other.

I didn't use LangGraph or CrewAI. The reasoning isn't that the flow is linear — orchestration frameworks solve a real problem (checkpointing, retries, observability, human-in-the-loop as a primitive) and this pipeline has that problem. The choice was to solve it directly instead of through a framework. InvoiceState is the checkpoint object. halted and mark_stage_complete track where we are. Retry logic lives in the agents that need it. The full orchestration is about 20 lines in main.py. At this scale a framework adds a dependency and indirection without adding capability. If the pipeline grows — parallel branches, more agents, distributed execution — LangGraph is the right call at that point.

---

## The Agents

### Agent 1: Ingestion

Reads whatever format the invoice comes in (PDF, TXT, JSON, CSV, XML), extracts the raw text, and sends it to Grok for structured extraction. Gets back vendor, amount, line items, and due date as clean fields.

Grok also normalizes item names during extraction. "Widget A" and "Gadget X" (with spaces, common in OCR output) get mapped to "WidgetA" and "GadgetX" so validation can match them against the DB cleanly.

Every extraction gets a confidence score (high / medium / low). An invoice where Grok had to guess on a malformed PDF should be treated differently than a clean JSON. Low-confidence extractions are flagged, and in a real system, those would route to human review.

### Agent 2: Validation

Checks the extracted data against SQLite. Runs these checks in order:

1. **Foreign currency** - if currency is not USD, flag for human review and stop. We can't safely approve without knowing exchange rate policy.
2. **Duplicate invoice number** - batch files are sorted newest-first so revised invoices always process before originals. If a duplicate is detected mid-batch and the earlier version was already approved, the approval agent auto-rejects it as superseded, so it never hits the human review queue. Cross-session duplicates (same invoice number as a prior run) go to human review unless the amounts match exactly, in which case they're rejected outright.
3. **Vendor check** - three outcomes:
   - Exact match (case-insensitive) -> approved, continue
   - Known bad actor (in table, approved = 0) -> halt immediately, auto-reject
   - No exact match but similarity >= 90% -> flag as possible match, continue to approval. Surfaces the close match so the approval agent can check vendor history before deciding. Catches typos and formatting differences without auto-approving a spoofed vendor.
   - No match, no close match -> unknown vendor flag, continue to approval. Grok can use its vendor history and profile tools to gather context before deciding.

   Case-insensitive matching handles the common case ("widgets inc" vs "Widgets Inc."). Fuzzy matching handles formatting differences ("Widgets Incorporated" vs "Widgets Inc.") but is capped at a high threshold to avoid false positives. A bad actor named "Widgets lnc" (letter substitution) should not auto-match.
4. **Item existence** - does each item exist in the catalog? "Not in catalog" and "not available for ordering" are different failure modes and should produce different messages.
5. **Quantity authorization check** - invoice quantities are compared against the authorized limits in the catalog. This approximates PO matching: in production every invoice line item would be validated against an approved purchase order. Here the catalog limits stand in for that. Quantities are aggregated per item across all line items before checking, so an invoice with the same item on three lines (e.g. volume discounts) gets the quantities summed first.
6. **Price check** - invoice unit price is compared against the DB price. Variance within 15% passes. Over 15% adds a price flag to state; Grok decides in the approval stage. This threshold allows rush order markups and volume discounts to pass through to judgment rather than hard-rejecting.

No LLM in validation. Every check is deterministic: it either matches the DB or it doesn't. Using Grok here would be slower, more expensive, and harder to audit.

### Agent 3: Approval

Three paths:

- Already halted from validation (bad actor, foreign currency, unrecoverable errors) -> set decision based on halt reason, don't call Grok
- Hard fraud flags (bad_actor, negative_quantity, negative_total) -> auto-reject immediately, no Grok call needed
- Everything else, including unknown vendors and possible vendor matches -> Grok reasons through it with a self-critique loop. Unknown vendors aren't auto-rejected or auto-escalated — the approval agent can check vendor history and profile before deciding, which is the right place to make that call.

The approval agent runs a tool-calling loop before making a decision. Grok receives three tools and decides which to call based on what it sees:

- lookup_vendor_history: queries processed_invoices for prior activity, useful for spotting a first-time vendor or one with a history of rejected invoices
- get_item_price: queries the items table for the catalog price on a specific item, useful when a price_variance flag is present so Grok can see the actual delta rather than just knowing a flag exists
- flag_for_escalation: lets Grok explicitly route to human review with a typed reason, rather than just returning "human_review" as a string

Grok runs this loop up to 3 rounds, calling whatever tools it needs and receiving results back. Once it stops calling tools it returns its initial decision. A second Grok call then critiques that decision, the critique includes what tools were called and what they found. The model can change its decision if the critique reveals a problem.

Invoices over $10K are flagged as high value in the prompt. If the first Grok call fails, default to rejected. If only the critique fails, use the first decision. If Grok returns a decision value outside the allowed set, default to human_review and log it.

Four possible outcomes: approved, rejected, human_review, error. Error is reserved for system failures like file not found or DB errors, distinct from rejected, so the AP team knows it needs a technical fix, not a business decision.

**Hard reject flags (auto-rejected without calling Grok):**
- bad_actor: vendor is on the blocked list
- negative_quantity: line item has negative quantity
- negative_total: invoice total is negative
- missing_total: no total amount — can't process payment
- missing_vendor: no vendor name — can't route payment
- no_line_items: no items on the invoice — nothing to validate

**Soft flags (passed to Grok for judgment):**
- stock_mismatch: invoice quantity exceeds the authorized order limit
- out_of_stock: item exists in catalog but is not available for ordering
- unknown_item: item not in catalog at all
- price_variance: invoice price deviates more than 15% from DB price
- unknown_vendor: vendor not in approved list
- possible_vendor_match: vendor not found but closely matches a known vendor (>=90% similarity)
- foreign_currency: invoice is not in USD
- duplicate_invoice: invoice number already processed in this batch
- low_confidence: Grok flagged extraction confidence as low
- malformed_line_item: a line item could not be parsed
- vendor_not_onboarded: Grok approved an unknown vendor — AP team needs to confirm before the vendor is added to the whitelist

### Agent 4: Payment

Four paths:

- Rejected or human_review -> skip payment, write audit log, record in DB so duplicate detection catches re-submissions
- Halted or no decision set -> block, write audit log
- Approved -> retry loop (up to 3 attempts). On success: write transaction result, mark "paid", record in DB. On failure after all retries: mark "payment_failed", record in DB with that status so the next run doesn't attempt payment again. AP team retries manually via the UI.

Every outcome writes an immutable audit log. The audit log includes payment_attempts so you can see whether it succeeded on the first try or had to retry.

The retry_payment() function in main.py handles manual retries. It clears the payment_failed DB record before retrying so duplicate detection doesn't block the attempt. On success the record is updated to "approved". On another failure it records as payment_failed again.

Does a final state check before calling payment. If an invoice somehow reaches this stage without approval, it gets blocked here.

---

## Database Schema

Three tables.

### items

Master catalog - what items exist and what they cost. This is the source of truth for item existence and expected pricing.

| column     | type    |
|------------|---------|
| item       | TEXT PK |
| unit_price | REAL    |

### inventory

Authorized order quantities per item. References items. In production this would be replaced by PO matching: validating invoice quantities against approved purchase orders. Here the quantity limits simulate that constraint so the validation agent has something real to check against.

| column | type    |
|--------|---------|
| item   | TEXT PK |
| stock  | INTEGER |

Keeping these separate means "item doesn't exist in our catalog" (not in items) and "item exists but is not available for ordering" (in items, quantity = 0) are distinct. they produce different validation messages and mean different things operationally.

Seed data:

| item     | stock | unit_price |
|----------|-------|------------|
| WidgetA  | 15    | 250.00     |
| WidgetB  | 10    | 500.00     |
| WidgetC  | 0     | 350.00     |
| GadgetX  | 5     | 750.00     |
| FakeItem | 0     | 0.00       |

### processed_invoices

Tracks every invoice that has completed the pipeline. Used to catch duplicates across sessions. if a new file comes in claiming the same invoice number as something already processed, it gets flagged for review. Same file reprocessed is allowed (the user might be re-checking something), but a different file with the same invoice number is not.

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
| id       | INTEGER PK |
| name     | TEXT UNIQUE |
| approved | INTEGER |

This is an addition beyond the minimum schema. Item-level checks catch bad quantities and unknown SKUs, but won't catch a spoofed vendor. The vendors table adds a second signal that the baseline schema misses.

Tables use integer surrogate PKs with UNIQUE constraints on the natural key columns (name, item). Lookups are still by name since that's what every agent has, but the PK is stable if a name ever changes and the schema is correct for production use.

Two distinct states: a vendor not in this table is unknown (never seen before, flag for review). A vendor in this table with approved = 0 is a known bad actor (explicit rejection). Fraudster LLC is seeded as approved = 0. NoProd Industries is not in the table. they're unknown, not confirmed bad.

---

## Design Decisions

**Why not LangGraph/CrewAI**
Not because the flow is linear. Orchestration frameworks solve a real problem and this pipeline has that problem. We just solve it directly. InvoiceState is the checkpoint. halted and mark_stage_complete track progress. Retry logic is in the agents that need it. Total orchestration is about 20 lines in main.py. A framework at this scale adds a dependency without adding capability. If this grows into parallel branches or distributed execution, LangGraph is the right answer then.

**Why SQLite**
Zero setup, single file, runs locally. Simulates a legacy inventory system without needing a server.

**Why the OpenAI client instead of xai_sdk**
xAI's API is OpenAI-compatible by design. their own docs recommend the OpenAI client. More stable, better documented, one less dependency.

**Why separate files per agent**
Each agent can fail independently. When something breaks, I want to open one file and fix it. not trace through a monolithic pipeline.

**Why three tables instead of two**
The minimum schema merges item existence and stock levels into one table. Separating them into items + inventory means validation can tell the difference between "this item doesn't exist in our catalog" (INV-1008: SuperGizmo) vs "this item exists but is out of stock" (INV-1016: WidgetC). Those are different problems and should produce different messages.

**Why 15% price tolerance**
Hard price matching rejects legitimate invoices. rush orders run at a markup, volume orders come with discounts. 15% captures both without letting through significant price gouging. Anything over the threshold doesn't auto-reject; it gets flagged and Grok reasons about it in approval.

**Cold start behavior and warm-up**
On a fresh database every vendor has zero invoice history, so the approval agent applies the most conservative threshold to all of them. Approved vendors with no history get the standard $10K threshold. This produces a larger-than-usual human review queue on first use.

As the system processes invoices, vendor history builds. After 5+ approved invoices with no rejections, a vendor moves into the trusted tier and their threshold rises to $25K. The human review queue shrinks naturally as this happens.

For demos or testing, `seed_vendor_history.py` pre-populates 6 prior approved invoices per vendor so you can see warm-start behavior without waiting for real history to accumulate. Run it after `setup_db.py`.

**Why snapshot catalog limits in batch mode**
Each invoice is checked against the catalog independently. In a real PO-matching system you'd decrement authorized quantities as invoices are approved, but for a prototype that introduces non-determinism in tests. the first invoice to run would affect every one after it. Snapshot keeps the evals consistent.

**Why sort batch files by modified date (newest first)**
Alphabetical sort breaks down for revised invoices. INV-1004 and INV-1004_revised have the same invoice number. sorting newest-first means the revised file processes first and wins. When the older original comes up next, it gets caught as a duplicate of an already-approved invoice and is auto-rejected as superseded, so it never hits the human review queue. The AP team only sees the version that should actually be paid. Filename is the tiebreaker when two files have identical mtime so the sort is always deterministic.

**Why vendor onboarding is human-gated**
When Grok auto-approves an invoice from an unknown vendor, payment goes through (Grok had enough confidence), but the vendor isn't added to the approved list. Instead a flag surfaces in the UI prompting AP to confirm the relationship first. When AP manually approves an invoice from an unknown vendor, they're already making a judgment call, so the vendor gets added to the whitelist at that point. This keeps onboarding human-confirmed rather than letting Grok unilaterally expand the vendor list.

**Why audit logs use a timestamp suffix instead of overwriting**
The first time an invoice is processed it writes a log. If an AP person manually approves it later (after a rejection, for example), that's a second pipeline run and a second log. Overwriting the first one would hide the original rejection from the audit trail. Timestamp suffix means every run produces an immutable record, and the full history is visible.

**Why we don't allow in-tool amount corrections**
AP staff can reject an invoice with a reason and request a corrected resubmission from the vendor. We don't let anyone edit extracted fields (amount, line items) directly in the tool. Any correction to financial data should come from the source. the vendor. not from a number someone typed into an internal tool. The audit trail is cleaner, the liability stays where it belongs, and we avoid the failure mode where a well-intentioned correction introduces a new error. Foreign currency invoices specifically get a hard block: converting EUR to USD requires an exchange rate policy decision, not a field edit.

**Why run_single and run_batch are separate functions**
The CLI uses them directly, but so does the UI. Exposing them as importable functions means the UI can call them in-process and get results back as Python objects rather than having to spawn a subprocess and parse stdout.

**Why batch archiving is on by default but eval skips it**
After each batch run, processed files move from data/invoices/ to data/processed/. This prevents the same file from re-entering the queue on the next run and flooding the human review queue with cross-session duplicates. Eval passes archive=False so the test invoices stay in place across multiple eval runs without needing to be manually restored.

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
- Duplicate invoice number in same batch -> newest file wins, older version auto-rejected as superseded if newer was approved
- Duplicate invoice number cross-session -> human review, UI shows original decision and date for context
- Item name casing differences -> case-insensitive DB query
- Same item multiple times on one invoice -> aggregate quantities before stock check
- Negative quantity -> data integrity flag
- Zero amount -> flagged as suspicious
- Vendor not in whitelist, no close match -> unknown vendor flag, approval agent reasons with vendor history tool
- Vendor not in whitelist but high similarity match (>=90%) -> possible match flag, approval agent surfaces the candidate and decides
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

## UI

Built in Streamlit. Designed for AP team members, not engineers. the goal is that someone non-technical can sit down and use it without training.

**Layout:**

The primary view is the action queue. Invoices that need a human decision come first. that's the job. Below that, summary metrics (approved count, rejected count, needs review count, total auto-processed value). The "already handled" list is collapsed by default and there for auditing, not daily use.

Single invoice mode is a separate tab. file upload, run, result shows immediately with the same detail view.

**Review cards:**

Each invoice needing action shows vendor, invoice number, amount, and the single most important flag in plain English. everything the reviewer needs to make a call without clicking into anything. Actions are inline on the card:

- Approve. one click, immediate
- Reject. two-step: click Reject, enter a reason, confirm. Prevents accidental rejections.
- Details. toggles a full detail panel below the card

**Detail panel:**

Two columns: left is extracted fields, line items, flags (in plain English), and AI reasoning. Right is the original invoice file. PDFs render embedded, text formats render as a code block. The reviewer sees exactly what the system was working with.

**Revised invoices:**

When an invoice is flagged as a duplicate but the amount differs from the original, the UI surfaces this as a possible revision rather than a plain duplicate. It shows what happened to the original (approved/rejected, when) and what the amount difference is.

- If the original was already approved and paid: a hard warning blocks action and directs to finance for reconciliation. The "Accept Revision" button does not appear. you cannot pay twice without a manual review.
- If the original was rejected (no payment sent): an "Accept Revision" button appears. It strips the duplicate flag, clears the halt, and runs payment on the revised version.

In same-batch processing this is handled automatically: newest file processes first and wins. The older original gets auto-rejected as superseded if the newer version was already approved. it never hits the human review queue.

**Manual approval:**

On approval the system clears the halt, skips re-validation (the human saw the flags and made the call), runs payment, writes the audit log, and records in processed_invoices. On rejection a reason is required. the UI enforces this, and main.py raises a ValueError if an empty reason is passed programmatically.

Session state is managed via st.session_state so batch results persist across interactions. actioning one invoice doesn't lose the rest. The "already handled" section has a Details toggle on every row so approved and rejected invoices are fully auditable without leaving the page.

---

## Above and Beyond

- Extraction confidence score on every ingestion — surfaces how reliable the parse was, and cross-checks stated total against line item sum
- Vendor whitelist with fuzzy matching — exact match, bad actor detection, and 90% similarity threshold catches typos and spoofed vendor names
- Three-table schema — separates item existence from quantity limits for cleaner, more actionable validation messages
- Price variance check with 15% tolerance — flags outliers for Grok rather than hard-rejecting, allowing legitimate rush order markups
- Dynamic tool loop in approval — simple invoices skip tool calls entirely, complex ones get up to 3 rounds with vendor history, item prices, and vendor profiles
- Prompt injection guardrails — invoice data wrapped in XML tags and explicitly marked untrusted; Python payment agent independently validates decision before money moves
- Inbox/archive workflow — processed files automatically move to data/processed/ after each batch, preventing duplicate re-processing
- Batch mode with business metrics — total auto-processed value, vendor spend breakdown, approval/rejection counts
- Foreign currency detection — flags non-USD invoices for human review rather than silently failing
- Cross-session duplicate detection — processed_invoices table catches duplicate invoice numbers across separate runs
- Revised invoice handling — newest file wins in same-batch processing; older originals are auto-rejected as superseded
- Interactive UI with inline approval — AP team can approve, reject with a required reason, or accept a revision without leaving the tool
- Original invoice viewer — detail panel embeds the actual invoice file so reviewers see exactly what the system was working with
- Revision detection — UI distinguishes between a true duplicate and a revised invoice by comparing amounts, blocks action if the original was already paid
- Docker support — full stack runs with docker compose up, DB and logs persist via volume mounts

---

## Invoice Test Cases

| Invoice | Format | Scenario | Expected |
|---------|--------|----------|----------|
| INV-1001 | TXT | Clean order, normal stock | Approved |
| INV-1002 | TXT | Typos throughout, 20x GadgetX (stock: 5) | Rejected. stock mismatch |
| INV-1003 | TXT | Fake vendor, FakeItem, "URGENT wire transfer" | Rejected. fraud |
| INV-1004 | JSON | Clean order with tax | Approved |
| INV-1004 revised | JSON | Revised invoice, newer mtime, higher amount (GadgetX added) | Processed first (newest wins), INV-1004 original auto-rejected as superseded |
| INV-1005 | JSON | $15,225 total, 8x GadgetX (stock: 5) | Rejected. stock mismatch |
| INV-1006 | CSV | Key-value format, single item | Approved |
| INV-1007 | CSV | $15,525 total, 20x WidgetA (stock: 15) | Rejected. stock mismatch |
| INV-1008 | TXT | Items not in catalog (SuperGizmo, MegaSprocket) | Rejected. unknown items |
| INV-1009 | JSON | Negative quantity, blank vendor, negative total | Rejected. data integrity |
| INV-1010 | TXT | Rush order line item, price variance | Flagged. Grok decides |
| INV-1011 | PDF | Clean, well-structured | Approved |
| INV-1012 | TXT+PDF | OCR artifacts, spacing in item names | Approved if Grok normalizes correctly |
| INV-1013 | JSON+PDF | $22,562, duplicate items at discount prices, aggregated stock check | Rejected. exceeds stock when aggregated |
| INV-1014 | XML | EUR currency | Flagged. human review |
| INV-1015 | CSV | Clean, tabular format | Approved |
| INV-1016 | JSON | WidgetC exists in catalog, stock = 0 | Rejected. out of stock |
| INV-1017 | TXT | "Widgets lnc". lowercase L instead of capital I, fuzzy match test | Human review. possible vendor match |
| INV-1018 | JSON | $12,500 clean order from known vendor, over $10K threshold | Approved. high value scrutiny passes |
| INV-1019 | CSV | WidgetA and WidgetB in stock, GadgetX x10 exceeds stock of 5 | Human review. partial stock issue |

Target: 80% of low-risk invoices auto-processed end-to-end. On Acme's volume, that's roughly $1.6M in annual savings.
