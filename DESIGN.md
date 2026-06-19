# Acme Invoice Processing Automation — Design Doc
Yun Cheih Lee | June 2026

---

## Business Context & Problem Statement

Acme Corp loses $2M/year on manual invoice processing:
- 30% error rate from manual data entry
- 5-day processing delays stuck in VP email chains
- Staff time wasted on work that should be automated

## Proposed Architecture: 4-Agent Pipeline

**Current (broken):**
Invoice → manual extraction → legacy DB check → VP email chain → bank API

**My system:**
Invoice file → Ingestion → Validation → Approval → Payment → Output

---

## The Agents

### Agent 1: Ingestion
- Reads any file format (PDF, TXT, JSON, CSV, XML)
- Sends raw text to Grok for structured extraction
- Extracts: vendor, amount, line items, due date
- Assigns extraction confidence (high/medium/low)
- Writes everything to shared InvoiceState

### Agent 2: Validation
- Reads line items from InvoiceState
- Queries SQLite inventory DB
- Checks: item exists? stock sufficient? quantity valid? vendor approved?
- No Grok here — pure logic is faster, cheaper, and fully auditable
- Writes flags to InvoiceState

### Agent 3: Approval
- Auto-rejects fraud flags without touching Grok (fast path)
- Sends everything else to Grok for VP-style reasoning
- Self-correction loop: Grok critiques its own decision before finalizing
- Extra scrutiny for invoices over $10K
- Writes decision + reasoning to InvoiceState

### Agent 4: Payment
- If approved → calls mock_payment() → logs confirmation
- If rejected → logs reason
- Writes final status to InvoiceState

### Output
- Terminal summary per invoice
- JSON audit log saved to logs/
- Streamlit UI for non-technical users

---

## Design Decisions

- **Why not LangGraph/CrewAI:** Pipeline is A→B→C→D, always in that order — a framework built for dynamic routing or agent collaboration adds complexity we don't need here
- **Why SQLite:** Local, zero setup, single file — perfect for simulating a legacy inventory system without spinning up a server
- **Why OpenAI client instead of xai_sdk:** xAI built their API to be OpenAI-compatible by design, so using the OpenAI client is cleaner, more stable, and actually what their own docs recommend
- **Why separate files per agent:** Each agent can fail, be debugged, and be fixed independently — critical when something breaks at a client site at 2am

---

## Edge Cases

### Ingestion
- Empty or unreadable file → catch early, fail with clear error
- PDF with no extractable text → Grok still attempts, flags low confidence
- Grok returns malformed JSON → retry once with stricter prompt
- Missing fields → default to empty/zero, flag low confidence

### Validation
- Item name casing differences → case-insensitive DB query
- Negative quantity → flagged as data integrity violation
- Zero amount → flagged as suspicious
- Vendor not in whitelist → flagged as unknown vendor

### Approval
- Fraud flags present but small amount → Grok still reasons, doesn't auto-approve small invoices
- Invoice over $10K with no flags → extra scrutiny, not auto-reject
- Grok timeout or error → defaults to reject, error logged

### Payment
- Mock payment fails → logged, system doesn't crash
- Invoice reaches payment unapproved → final check before calling payment function

---

## Above and Beyond

- Extraction confidence score (high/medium/low) — surfaces how reliable the extraction was, useful for knowing when to route to human review
- Vendor whitelist table in SQLite — flags unknown vendors as a fraud signal, beyond the minimum schema
- Batch mode with business metrics — run all invoices at once, output % approved, % flagged, dollar value auto-processed

---

## Evals

- Did ingestion work? → Spot-check extracted fields against source invoices
- Did validation catch the right things? → Known problem invoices (1002, 1003, 1008, 1009) should all flag correctly
- Is the system deployable? → What % of invoices completed end-to-end without errors?
- Business impact: What dollar value of invoices can be processed without human touch?
  - Success metric: Automate 80% of low-risk invoices end-to-end, reducing manual processing costs by an estimated $1.6M annually