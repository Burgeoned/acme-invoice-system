import json
import os
import xml.etree.ElementTree as ET
from openai import OpenAI
from dotenv import load_dotenv
import httpx

import pdfplumber

from state import InvoiceState, LineItem
from agents.mock_responses import MOCK_INGESTION

load_dotenv()

MOCK_GROK = os.getenv("MOCK_GROK", "false").lower() == "true"

# ssl verification fails on this machine due to corporate cert issues, disable for local dev
client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1",
    http_client=httpx.Client(verify=False),
)

EXTRACTION_PROMPT = """
Extract structured data from the invoice text below and return valid JSON only, no explanation.

Normalize item names: remove extra spaces and fix common OCR artifacts.
For example "Widget A" -> "WidgetA", "Gadget X" -> "GadgetX", "2O26" -> "2026".

Return this exact structure:
{
  "invoice_number": "string or null",
  "vendor": "string or null",
  "date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "payment_terms": "e.g. Net 30, Net 60, Due on receipt, or null",
  "currency": "USD or other currency code, default to USD if not mentioned",
  "line_items": [
    {"item": "string", "quantity": number, "unit_price": number}
  ],
  "total_amount": number or null,
  "confidence": "high, medium, or low"
}

Confidence guide:
- high: clean structured input, all fields present and clear
- medium: some fields missing or ambiguous but core data extractable
- low: significant issues like OCR artifacts, missing critical fields, or highly ambiguous content

Invoice text:
{text}
"""

STRICT_EXTRACTION_PROMPT = """
Return valid JSON only. No explanation, no markdown, no code blocks.
Extract invoice data from the text below into this exact structure:
{
  "invoice_number": "string or null",
  "vendor": "string or null",
  "date": "YYYY-MM-DD or null",
  "due_date": "YYYY-MM-DD or null",
  "payment_terms": "e.g. Net 30, Net 60, Due on receipt, or null",
  "currency": "USD or other currency code",
  "line_items": [
    {"item": "string", "quantity": number, "unit_price": number}
  ],
  "total_amount": number or null,
  "confidence": "high, medium, or low"
}

Text:
{text}
"""


def read_file(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        with pdfplumber.open(file_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return text.strip()

    if ext == ".xml":
        tree = ET.parse(file_path)
        # flatten xml to key: value lines so grok gets readable text
        lines = []
        for elem in tree.iter():
            if elem.text and elem.text.strip():
                lines.append(f"{elem.tag}: {elem.text.strip()}")
        return "\n".join(lines)

    # txt, json, csv all read as plain text and let grok figure out the structure
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def call_grok(text: str, strict: bool = False) -> dict:
    prompt = STRICT_EXTRACTION_PROMPT if strict else EXTRACTION_PROMPT
    response = client.chat.completions.create(
        model="grok-3",
        messages=[{"role": "user", "content": prompt.format(text=text)}],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()

    # strip markdown code fences if grok wraps its response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def run(state: InvoiceState):
    # read the file first, halt early if we can't
    if not os.path.exists(state.file_path):
        state.add_error(f"File not found: {state.file_path}")
        state.halt(f"File not found: {state.file_path}")
        return

    try:
        state.raw_text = read_file(state.file_path)
    except Exception as e:
        state.add_error(f"Could not read file: {e}")
        state.halt(f"Could not read file: {e}")
        return

    if not state.raw_text:
        state.add_error("File is empty or has no extractable text")
        state.halt("File is empty or has no extractable text")
        return

    # use mock responses if MOCK_GROK=true, saves API credits during development
    extracted = None
    if MOCK_GROK:
        # pull the number out of the filename e.g. invoice_1001.txt -> INV-1001
        basename = os.path.splitext(os.path.basename(state.file_path))[0]
        parts = basename.split("_")
        for part in parts:
            if part.isdigit():
                invoice_num = f"INV-{part}"
                extracted = MOCK_INGESTION.get(invoice_num)
                break
        if not extracted:
            state.add_error(f"No mock response found for {state.file_path}")
            state.halt("No mock response available for this invoice")
            return
    else:
        # call grok, retry once with a stricter prompt if it returns bad json
        try:
            extracted = call_grok(state.raw_text)
        except json.JSONDecodeError:
            state.retry_count += 1
            try:
                extracted = call_grok(state.raw_text, strict=True)
            except json.JSONDecodeError as e:
                state.add_error(f"Grok returned malformed JSON after retry: {e}")
                state.halt("Ingestion failed, could not parse Grok response")
                return
        except Exception as e:
            state.add_error(f"Grok call failed: {e}")
            state.halt("Ingestion failed, Grok call failed")
            return

    # populate state from extracted data
    state.invoice_number = extracted.get("invoice_number")
    state.vendor = extracted.get("vendor")
    state.date = extracted.get("date")
    state.due_date = extracted.get("due_date")
    state.payment_terms = extracted.get("payment_terms")
    state.currency = extracted.get("currency", "USD")
    state.total_amount = extracted.get("total_amount")
    state.confidence = extracted.get("confidence", "low")

    for item in extracted.get("line_items", []):
        try:
            state.line_items.append(LineItem(
                item=item["item"],
                quantity=float(item["quantity"]),
                unit_price=float(item["unit_price"]),
            ))
        except (KeyError, ValueError, TypeError) as e:
            # if a line item is malformed just flag it and keep going
            state.add_flag("malformed_line_item", f"Could not parse line item {item}: {e}")

    if not state.line_items:
        state.add_flag("no_line_items", "No line items extracted")

    if state.confidence == "low":
        state.add_flag("low_confidence", "Grok flagged this extraction as low confidence, worth a manual check")

    state.mark_stage_complete("ingestion")
