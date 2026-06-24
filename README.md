# Acme Invoice Processing

Multi-agent pipeline that automates invoice processing end to end: ingestion, validation, approval, and payment. Built for Acme Corp's AP team to cut a 5-day, 30%-error-rate manual process down to seconds.

---

## What it does

Invoices come in from vendors in whatever format they use: PDF, JSON, CSV, XML, plain text. The pipeline extracts structured data, checks it against vendor records and the item catalog, runs it through an approval agent that reasons with tools and a self-critique loop, and either processes payment or routes to the AP team for review.

**Four agents:**

1. **Ingestion** - reads any supported format, sends to Grok for structured extraction with a confidence score. Retries with a stricter prompt on parse failure. Cross-checks the stated total against the line item sum.
2. **Validation** - deterministic checks against SQLite: vendor whitelist with fuzzy matching, item catalog, quantity limits, price variance, data integrity. No LLM, fully auditable.
3. **Approval** - Grok agent with tool calling. Calls `lookup_vendor_history`, `get_vendor_profile`, and `get_item_price` to gather context before deciding. Applies tiered approval thresholds based on vendor history — trusted vendors (5+ approved invoices, no rejections) get a higher auto-approval limit than first-time vendors. Simple invoices from known vendors skip the tool loop entirely. Self-critique pass reviews the decision before it's finalized.
4. **Payment** - mock payment API on approval. Every outcome writes an immutable audit log with decision source, Grok's tool call chain, and whether the critique changed the initial decision.

**UI:** Streamlit interface for the AP team. Human review invoices surface front and center with inline approve/reject. Detail panel shows the original invoice file side by side with extracted fields, Grok's reasoning, and every tool call it made. When AP staff manually approves an unknown vendor, they're automatically added to the approved vendor list. Vendor spend breakdown in the summary.

---

## Running with Docker

The easiest way to run the full stack:

```bash
# add your API key to .env first
echo "XAI_API_KEY=your_key_here" > .env

docker compose up
```

Then open `http://localhost:8501`. The database and audit logs persist across restarts via volume mounts.

---

## Running locally

**Requirements:** Python 3.11+, an xAI API key

```bash
git clone https://github.com/Burgeoned/acme-invoice-system
cd acme-invoice-system
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
XAI_API_KEY=your_key_here
```

Replace `your_key_here` with a real xAI API key and the system uses Grok automatically. Leave it as-is and it falls back to mock mode — so you can run everything without an API key for eval and testing.

Initialize the database:

```bash
python setup_db.py
```

---

## Running

**Single invoice:**
```bash
python main.py --invoice_path data/invoices/invoice_1001.txt
```

**Full batch:**
```bash
python main.py --batch
```

After a batch run, processed files are automatically moved from `data/invoices/` to `data/processed/`. Drop new invoices in `data/invoices/` and run batch again — no duplicates, no manual cleanup.

**Streamlit UI:**
```bash
streamlit run ui.py --server.headless true
```
Then open `http://localhost:8501`.

**Eval (mock mode, no API calls):**
```bash
python eval.py
```

**Testing payment failures:**

Add `PAYMENT_FAIL_RATE=0.3` to `.env` to simulate 30% transient payment failures. The pipeline will retry up to 3 times, and if all fail it records the invoice as `payment_failed` in the DB and surfaces it in the UI with a Retry Payment button. Set back to 0 (or remove it) to return to normal operation.

---

## Test cases

19 invoices covering the main scenarios the pipeline needs to handle:

| Invoice | Format | Scenario | Expected |
|---------|--------|----------|----------|
| INV-1001 | TXT | Clean order, approved vendor | Approved |
| INV-1002 | TXT | 20x GadgetX, exceeds authorized limit | Rejected |
| INV-1003 | TXT | Fraudster LLC, FakeItem, urgent wire transfer | Rejected |
| INV-1004 | JSON | Clean order with tax | Approved |
| INV-1004 revised | JSON | Same invoice number, updated amounts and items | Revised version wins, original superseded |
| INV-1005 | JSON | $15K order, 8x GadgetX | Rejected |
| INV-1006 | CSV | Single item, clean | Approved |
| INV-1007 | CSV | $15K, 20x WidgetA | Rejected |
| INV-1008 | TXT | Unknown vendor, items not in catalog | Human review |
| INV-1009 | JSON | Negative quantity, blank vendor | Rejected |
| INV-1010 | TXT | Rush order, price variance | Grok decides |
| INV-1011 | PDF | Clean, well-structured | Approved |
| INV-1012 | PDF | OCR artifacts, item name normalization | Approved |
| INV-1013 | JSON | $22K, same item 3x at discount, aggregated check | Human review |
| INV-1014 | XML | EUR invoice | Human review |
| INV-1015 | CSV | Clean tabular format | Approved |
| INV-1016 | JSON | WidgetC not available for ordering | Rejected |
| INV-1017 | TXT | "Widgets lnc" - vendor name spoofing test | Human review |
| INV-1018 | JSON | $12,500 clean order, high value scrutiny path | Approved |
| INV-1019 | CSV | Mixed: some items available, GadgetX over limit | Human review |

---

## Eval results (mock mode)

```
Hard assertions (deterministic):  10/10  (100%)
Soft assertions (grok may vary):   9/9  (100%)
Overall:                          19/19  (100%)

False negatives (bad invoice approved):  0
False positives (clean invoice flagged): 0
Auto-processing rate:                   85%
```

Run `python eval.py` to reproduce. It resets the database first for a clean run.

---

## Design decisions

**No LangGraph or CrewAI.** Not because the flow is linear — orchestration frameworks solve real problems (checkpointing, retries, human-in-the-loop as a primitive) and this pipeline has those problems. The call was to solve them directly. InvoiceState is the checkpoint object, halt and stage tracking live on it, retry logic is per-agent. Full orchestration is about 20 lines in main.py. A framework here adds a dependency without adding capability at this scale. If the pipeline grows into parallel branches or distributed execution, LangGraph becomes the right choice.

**Validation is fully deterministic.** Every check is a DB query or arithmetic, no LLM. Fast, cheap, auditable. Grok only enters at extraction and approval where judgment is actually needed.

**Approval agent uses tool calling with tiered thresholds.** Grok decides what context it needs before making a decision. Approval thresholds scale with vendor history: first-time vendors (not on the approved list) get extra scrutiny above $5K, approved vendors with no prior invoice history get the standard $10K threshold, and trusted vendors with 5+ approved invoices and no rejections can be approved up to $25K. Simple invoices from known vendors skip the tool loop entirely. For complex cases Grok calls tools, then a self-critique pass reviews the decision.

**Audit trail is complete and immutable.** Every invoice gets a JSON audit log with the full decision chain: which tools Grok called, what they returned, the initial decision, whether the critique changed it, and whether the decision was automated or manual. Re-running an invoice appends a new log with a timestamp suffix instead of overwriting the old one.

**Vendor onboarding is human-gated.** When AP staff manually approves an invoice from an unknown vendor, that vendor is automatically added to the approved list. When Grok auto-approves one, it flags it for AP review first — the system doesn't unilaterally onboard vendors without a human confirming the relationship.

**Invoice data is sandboxed in prompts.** Vendor-supplied content is wrapped in XML tags with XML escaping, so a malicious line item containing `</invoice_data>` can't break the prompt boundary. And even if it could, Grok can only set a string field — Python's payment agent independently validates the decision before any money moves.

**Inbox/archive workflow.** After each batch run, processed files move from `data/invoices/` to `data/processed/` automatically. New invoices go in the inbox, processed ones stay in the archive. No duplicates from re-running the same folder.

**Human review is intentional.** The pipeline defaults to human review when uncertain. In production with a fully populated vendor whitelist, most of the queue shrinks. The test batch has vendors not yet onboarded, which accounts for most of the human review cases.

**No in-tool amount corrections.** AP staff reject and request a corrected resubmission from the vendor. Editing financial data in an internal tool creates audit risk that's harder to trace than the original problem.

Full design rationale in [DESIGN.md](DESIGN.md).

---

## Project structure

```
agents/
  ingestion.py     reads files, calls Grok for extraction, cross-checks totals
  validation.py    deterministic checks against SQLite
  approval.py      Grok agent with tool calling and self-critique
  payment.py       mock payment, audit log, records to DB

data/invoices/     invoice inbox (processed files move to data/processed/)
data/processed/    archive after each batch run (gitignored)
logs/              per-invoice audit logs (gitignored)

main.py            orchestrator, CLI, run_single and run_batch
ui.py              Streamlit UI for the AP team
eval.py            test suite, 19 invoices, accuracy and cost metrics
setup_db.py        creates and seeds inventory.db
company_context.py AP policies and vendor profiles for the approval agent
state.py           InvoiceState dataclass shared between agents
DESIGN.md          full design doc
AP_GUIDE.md        plain-language guide for the AP team
```
