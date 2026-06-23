# Acme Invoice Processing

Multi-agent pipeline that automates invoice processing end to end. ingestion, validation, approval, and payment. Built for Acme Corp's AP team to cut a 5-day, 30%-error-rate manual process down to seconds.

---

## What it does

Invoices come in from vendors in whatever format they use. PDF, JSON, CSV, XML, plain text. The pipeline extracts structured data, checks it against inventory and vendor records, runs it through an approval agent that reasons with tools and a self-critique loop, and either processes payment or routes to the AP team for review.

**Four agents:**

1. **Ingestion** - reads any supported format, sends to Grok for structured extraction with a confidence score. Retries with a stricter prompt on parse failure.
2. **Validation** - deterministic checks against SQLite: vendor whitelist with fuzzy matching, item catalog, stock levels, price variance, data integrity. No LLM, fully auditable.
3. **Approval** - Grok agent with tool calling. Calls `lookup_vendor_history`, `get_vendor_profile`, and `get_item_price` to gather context before deciding. Self-critique loop: a second call reviews the first decision and can change it. Hard rules block fraud and data integrity failures before Grok is involved.
4. **Payment** - mock payment API on approval, audit log on every outcome.

**UI:** Streamlit interface for the AP team. Human review invoices surface front and center with inline approve/reject. Detail panel shows the original invoice file side by side with extracted fields and Grok's reasoning.

---

## Running with Docker

The easiest way to run the full stack:

```bash
# add your API key to .env first
echo "XAI_API_KEY=your_key_here" > .env
echo "MOCK_GROK=true" >> .env

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
MOCK_GROK=false
```

Set `MOCK_GROK=true` to run without API calls. useful for testing and eval.

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

**Streamlit UI:**
```bash
streamlit run ui.py --server.headless true
```
Then open `http://localhost:8501`.

**Eval (mock mode, no API calls):**
```bash
python eval.py
```

---

## Test cases

16 invoices covering the main scenarios the pipeline needs to handle:

| Invoice | Format | Scenario | Expected |
|---------|--------|----------|----------|
| INV-1001 | TXT | Clean order, normal stock | Approved |
| INV-1002 | TXT | 20x GadgetX, only 5 in stock | Human review |
| INV-1003 | TXT | Fraudster LLC, FakeItem, urgent wire transfer | Rejected |
| INV-1004 | JSON | Clean order with tax | Approved |
| INV-1004 revised | JSON | Same invoice number, updated amounts | Revised version wins, original superseded |
| INV-1005 | JSON | $15K order, 8x GadgetX | Human review |
| INV-1006 | CSV | Single item, clean | Approved |
| INV-1007 | CSV | $15K, 20x WidgetA | Human review |
| INV-1008 | TXT | Unknown vendor, items not in catalog | Human review |
| INV-1009 | JSON | Negative quantity, blank vendor | Rejected |
| INV-1010 | TXT | Rush order, price variance | Grok decides |
| INV-1011 | PDF | Clean, well-structured | Approved |
| INV-1012 | PDF | OCR artifacts | Approved |
| INV-1013 | JSON | $22K, same item 3x at volume discount | Human review |
| INV-1014 | XML | EUR invoice | Human review |
| INV-1015 | CSV | Clean tabular format | Approved |
| INV-1016 | JSON | WidgetC exists but zero stock | Human review |

---

## Eval results (mock mode)

```
Hard assertions (deterministic):  8/8  (100%)
Soft assertions (grok may vary):  8/8  (100%)
Overall:                          16/16

False negatives (bad invoice approved):  0
False positives (clean invoice flagged): 0
Auto-processing rate:                   88%
```

Run `python eval.py` to reproduce. It resets the database first for a clean run.

---

## Design decisions

A few choices worth knowing about:

**No LangGraph or CrewAI.** The flow is always linear. A routing framework solves a problem this pipeline doesn't have and adds surface area for things to break.

**Validation is fully deterministic.** Every check is a DB query or arithmetic, no LLM. Fast, cheap, auditable. Grok only enters at extraction and approval where judgment is actually needed.

**Approval agent uses tool calling.** Grok decides what context it needs before making a decision. For a price variance flag it calls `get_item_price` to see the actual delta. For an unfamiliar vendor it calls `lookup_vendor_history`. A self-critique pass then reviews whether the tool findings supported the decision.

**Human review is intentional.** The pipeline defaults to human review when uncertain rather than guessing. Stock issues, unknown vendors, and price variances that Grok cannot confidently resolve go to the AP team's queue. In production with a fully populated vendor whitelist the queue shrinks considerably. Most of the human reviews in the test batch are from vendors not yet onboarded to the whitelist.

**No in-tool amount corrections.** AP staff reject with a reason and request a corrected resubmission from the vendor. Letting someone edit a financial field in an internal tool creates audit risk that is harder to trace than the original problem.

Full design rationale in [DESIGN.md](DESIGN.md).

---

## Project structure

```
agents/
  ingestion.py     reads invoice files, calls Grok for structured extraction
  validation.py    checks extracted data against SQLite
  approval.py      Grok agent with tool calling and self-critique loop
  payment.py       mock payment, audit logging, records to DB

data/invoices/     16 test invoices in various formats
logs/              per-invoice audit logs (gitignored)

main.py            orchestrator, CLI, importable run_single and run_batch
ui.py              Streamlit UI for the AP team
eval.py            runs all test invoices and reports accuracy and cost metrics
setup_db.py        creates and seeds inventory.db
company_context.py AP policies and vendor profiles for the approval agent
state.py           InvoiceState dataclass passed between agents
DESIGN.md          full design doc with rationale for every decision
```
