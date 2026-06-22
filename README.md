# Invoice Processing Automation

Multi-agent system for automating end-to-end invoice processing — ingestion, validation, approval, and payment.

See [DESIGN.md](DESIGN.md) for architecture and decisions.

---

## Setup

```bash
pip install -r requirements.txt
python setup_db.py
```

## Running

Single invoice:
```bash
python main.py --invoice data/invoices/invoice_1001.txt
```

All invoices (batch):
```bash
python main.py --batch
```

UI:
```bash
streamlit run ui.py
```

## Project Structure

```
agents/
  ingestion.py
  validation.py
  approval.py
  payment.py
main.py          # orchestrator
setup_db.py      # initializes SQLite DB
state.py         # shared InvoiceState object
ui.py            # Streamlit dashboard
data/invoices/   # test invoices
logs/            # audit logs (generated at runtime)
```
