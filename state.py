from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LineItem:
    item: str
    quantity: float
    unit_price: float

    @property
    def total(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class Flag:
    type: str
    message: str


@dataclass
class InvoiceState:
    file_path: str

    # ingestion fills these in, if something is None after ingestion something went wrong
    invoice_number: Optional[str] = None
    vendor: Optional[str] = None
    date: Optional[str] = None
    due_date: Optional[str] = None
    currency: Optional[str] = None
    line_items: list[LineItem] = field(default_factory=list)
    total_amount: Optional[float] = None
    confidence: Optional[str] = None
    raw_text: Optional[str] = None
    retry_count: int = 0

    # validation fills these in
    vendor_status: Optional[str] = None
    possible_vendor_match: Optional[str] = None  # set if fuzzy match found but not confirmed

    # approval fills these in
    decision: Optional[str] = None
    reasoning: Optional[str] = None

    # payment fills these in
    payment_status: Optional[str] = None
    payment_result: Optional[dict] = None

    # pipeline control
    stage: str = "ingestion"
    halted: bool = False
    halt_reason: Optional[str] = None

    # flags are business problems, errors are system failures, keep them separate
    flags: list[Flag] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    timestamps: dict = field(default_factory=dict)

    def add_flag(self, type: str, message: str):
        if not type or not message:
            raise ValueError("Flag type and message are required")
        self.flags.append(Flag(type=type, message=message))

    def add_error(self, message: str):
        if not message:
            raise ValueError("Error message cannot be empty")
        self.errors.append(message)

    def halt(self, reason: str):
        # call this from any agent to stop the pipeline, beats letting it continue with bad data
        if not reason:
            raise ValueError("Halt reason cannot be empty")
        self.halted = True
        self.halt_reason = reason

    def mark_stage_complete(self, stage: str):
        self.timestamps[stage] = datetime.utcnow().isoformat()
        stages = ["ingestion", "validation", "approval", "payment"]
        current = stages.index(stage)
        if current + 1 < len(stages):
            self.stage = stages[current + 1]

    def has_flag(self, type: str) -> bool:
        return any(f.type == type for f in self.flags)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "invoice_number": self.invoice_number,
            "vendor": self.vendor,
            "date": self.date,
            "due_date": self.due_date,
            "currency": self.currency,
            "line_items": [
                {
                    "item": li.item,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                    "total": li.total,
                }
                for li in self.line_items
            ],
            "total_amount": self.total_amount,
            "confidence": self.confidence,
            "retry_count": self.retry_count,
            "vendor_status": self.vendor_status,
            "possible_vendor_match": self.possible_vendor_match,
            "decision": self.decision,
            "reasoning": self.reasoning,
            "payment_status": self.payment_status,
            "payment_result": self.payment_result,
            "stage": self.stage,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "flags": [{"type": f.type, "message": f.message} for f in self.flags],
            "errors": self.errors,
            "timestamps": self.timestamps,
        }
