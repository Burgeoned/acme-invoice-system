import json
import os
import httpx
from openai import OpenAI
from dotenv import load_dotenv

from state import InvoiceState
from agents.mock_responses import MOCK_APPROVAL

load_dotenv()

MOCK_GROK = os.getenv("MOCK_GROK", "false").lower() == "true"
HIGH_VALUE_THRESHOLD = 10000

# ssl verification disabled for local dev, same issue as ingestion
client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1",
    http_client=httpx.Client(verify=False),
)

DECISION_PROMPT = """
You are reviewing an invoice for approval on behalf of a VP at a manufacturing company.

Invoice details:
  Vendor: {vendor}
  Total: ${total_amount}
  Line items: {line_items}
  Flags raised during validation: {flags}
  High value invoice (over $10,000): {high_value}

Flag types and what they mean:
  stock_mismatch: requested quantity exceeds available stock
  price_variance: invoice price deviates more than 15% from expected price
  low_confidence: extraction confidence was low, data may be unreliable
  negative_quantity: a line item has a negative quantity
  negative_total: invoice total is negative
  missing_vendor: no vendor name on the invoice
  no_line_items: invoice has no line items
  unknown_item: item not found in our catalog
  missing_total: invoice has no total amount

Make a decision: approved, rejected, or human_review.

Return valid JSON only:
{{
  "decision": "approved or rejected or human_review",
  "reasoning": "your reasoning here"
}}
"""

CRITIQUE_PROMPT = """
You previously made this decision on an invoice:

Decision: {decision}
Reasoning: {reasoning}

Invoice details:
  Vendor: {vendor}
  Total: ${total_amount}
  Flags: {flags}
  High value invoice (over $10,000): {high_value}

Critique your decision. Ask yourself:
  - Are you being too lenient with any flags?
  - Are you being too strict on anything that has a legitimate explanation?
  - Did you miss anything important?
  - For high value invoices, are you applying enough scrutiny?

You may change your decision if your critique reveals a problem with your first answer.

Return valid JSON only:
{{
  "decision": "approved or rejected or human_review",
  "reasoning": "your final reasoning after self-critique"
}}
"""

# hard fraud signals that skip grok entirely
HARD_REJECT_FLAGS = {"bad_actor", "negative_quantity", "negative_total"}


def format_line_items(state: InvoiceState) -> str:
    return ", ".join(
        f"{li.item} x{li.quantity} @ ${li.unit_price}"
        for li in state.line_items
    )


def format_flags(state: InvoiceState) -> str:
    if not state.flags:
        return "none"
    return "; ".join(f"{f.type}: {f.message}" for f in state.flags)


def call_grok(prompt: str) -> dict:
    response = client.chat.completions.create(
        model="grok-3",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()

    # strip markdown code fences if grok wraps the response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def run_grok_approval(state: InvoiceState):
    high_value = (state.total_amount or 0) >= HIGH_VALUE_THRESHOLD

    decision_prompt = DECISION_PROMPT.format(
        vendor=state.vendor or "unknown",
        total_amount=state.total_amount or 0,
        line_items=format_line_items(state),
        flags=format_flags(state),
        high_value=high_value,
    )

    try:
        first = call_grok(decision_prompt)
    except Exception as e:
        state.add_error(f"Grok approval call failed: {e}")
        state.decision = "rejected"
        state.reasoning = "Defaulting to rejected due to Grok failure during approval"
        return

    critique_prompt = CRITIQUE_PROMPT.format(
        decision=first.get("decision", "unknown"),
        reasoning=first.get("reasoning", ""),
        vendor=state.vendor or "unknown",
        total_amount=state.total_amount or 0,
        flags=format_flags(state),
        high_value=high_value,
    )

    try:
        final = call_grok(critique_prompt)
    except Exception as e:
        # if critique fails just use the first decision rather than defaulting to reject
        state.add_error(f"Grok critique call failed, using initial decision: {e}")
        final = first

    decision = final.get("decision", "human_review")
    # if grok returns something unexpected default to human review rather than passing garbage forward
    if decision not in ("approved", "rejected", "human_review"):
        state.add_error(f"Grok returned unexpected decision value '{decision}', defaulting to human_review")
        decision = "human_review"
    state.decision = decision
    state.reasoning = final.get("reasoning", "No reasoning provided")


def get_mock_decision(state: InvoiceState) -> dict:
    # pick the right mock based on what flags are present
    flag_types = {f.type for f in state.flags}

    if "bad_actor" in flag_types:
        return MOCK_APPROVAL["rejected_fraud"]
    if flag_types & {"stock_mismatch", "out_of_stock", "unknown_item"}:
        return MOCK_APPROVAL["rejected_stock"]
    if flag_types & {"unknown_vendor", "foreign_currency", "possible_vendor_match"}:
        return MOCK_APPROVAL["human_review"]
    return MOCK_APPROVAL["approved"]


def run(state: InvoiceState):
    if state.halted:
        if state.vendor_status == "bad_actor":
            state.decision = "rejected"
            state.reasoning = state.halt_reason
        elif state.errors and not state.vendor_status:
            # halted due to a system error like file not found, not a business logic decision
            state.decision = "error"
            state.reasoning = state.halt_reason
        else:
            # unknown vendor, possible match, foreign currency all need human eyes
            state.decision = "human_review"
            state.reasoning = state.halt_reason
        return

    flag_types = {f.type for f in state.flags}

    # hard reject without calling grok, these are unambiguous
    hard_flags = flag_types & HARD_REJECT_FLAGS
    if hard_flags:
        state.decision = "rejected"
        state.reasoning = f"Auto-rejected due to: {', '.join(hard_flags)}"
        state.mark_stage_complete("approval")
        return

    if MOCK_GROK:
        mock = get_mock_decision(state)
        state.decision = mock["decision"]
        state.reasoning = mock["reasoning"]
    else:
        run_grok_approval(state)

    state.mark_stage_complete("approval")
