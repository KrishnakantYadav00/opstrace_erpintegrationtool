"""
Pydantic schemas for OpsTrace Transaction Ingestion Layer.
Enforces strict type validation, normalization, and payload sanitization.
"""

from datetime import datetime, date
from typing import Optional, Any, List, Dict
from pydantic import BaseModel, Field, field_validator, model_validator


def clean_string(value: Optional[str]) -> Optional[str]:
    """Helper to strip whitespace from strings."""
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned if cleaned else None


def parse_flexible_datetime(value: Any) -> Optional[datetime]:
    """Parse string or datetime into a datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        # Try ISO format
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Try common ERP date string formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        raise ValueError(f"Unable to parse date/time string: '{cleaned}'")
    raise ValueError(f"Invalid date/time type: {type(value)}")


class PurchaseOrderIngestSchema(BaseModel):
    po_number: str = Field(..., description="Unique PO number identifier")
    vendor_id: str = Field(..., description="Unique vendor ID")
    vendor_name: str = Field(..., description="Vendor legal entity name")
    sku: str = Field(..., description="Item SKU code")
    description: Optional[str] = Field(None, description="Item or service description")
    quantity: float = Field(..., gt=0, description="Order quantity (must be > 0)")
    unit_price: float = Field(..., ge=0, description="Unit price (must be >= 0)")
    total_amount: Optional[float] = Field(None, description="Total amount (auto-calculated if omitted)")
    currency: Optional[str] = Field("USD", description="Currency code (e.g., USD, EUR)")
    status: Optional[str] = Field("ISSUED", description="PO status (e.g. ISSUED, RECEIVED)")
    order_date: Any = Field(..., description="Date order was placed")
    expected_delivery_date: Optional[Any] = Field(None, description="Expected delivery date")

    @field_validator("po_number", "vendor_id", "sku", mode="before")
    @classmethod
    def validate_non_empty_identifiers(cls, v: Any) -> str:
        cleaned = clean_string(v)
        if not cleaned:
            raise ValueError("Field cannot be empty or only whitespace")
        return cleaned.upper()

    @field_validator("vendor_name", "description", mode="before")
    @classmethod
    def clean_text_fields(cls, v: Any) -> Optional[str]:
        return clean_string(v)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> str:
        cleaned = clean_string(v)
        if not cleaned:
            return "USD"
        cleaned = cleaned.upper()
        if len(cleaned) != 3:
            raise ValueError(f"Currency code must be a 3-letter ISO code, got '{cleaned}'")
        return cleaned

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Any) -> str:
        cleaned = clean_string(v)
        return cleaned.upper() if cleaned else "ISSUED"

    @field_validator("order_date", "expected_delivery_date", mode="before")
    @classmethod
    def validate_dates(cls, v: Any) -> Optional[datetime]:
        return parse_flexible_datetime(v)

    @model_validator(mode="after")
    def compute_total_if_missing(self) -> "PurchaseOrderIngestSchema":
        if self.total_amount is None:
            self.total_amount = round(self.quantity * self.unit_price, 2)
        else:
            self.total_amount = round(float(self.total_amount), 2)
        return self


class GoodsReceiptIngestSchema(BaseModel):
    gr_number: str = Field(..., description="Unique Goods Receipt number")
    po_number: str = Field(..., description="Associated Purchase Order number")
    vendor_id: str = Field(..., description="Vendor ID")
    sku: str = Field(..., description="Item SKU code")
    quantity_received: float = Field(..., gt=0, description="Received quantity (must be > 0)")
    received_date: Any = Field(..., description="Timestamp goods were accepted at dock")
    received_by: Optional[str] = Field("Warehouse Intake", description="Dock worker or inspector")
    condition_status: Optional[str] = Field("ACCEPTED", description="Intake condition (ACCEPTED, DAMAGED, SHORTAGE)")

    @field_validator("gr_number", "po_number", "vendor_id", "sku", mode="before")
    @classmethod
    def validate_identifiers(cls, v: Any) -> str:
        cleaned = clean_string(v)
        if not cleaned:
            raise ValueError("Field cannot be empty or only whitespace")
        return cleaned.upper()

    @field_validator("received_by", mode="before")
    @classmethod
    def clean_received_by(cls, v: Any) -> str:
        cleaned = clean_string(v)
        return cleaned if cleaned else "Warehouse Intake"

    @field_validator("condition_status", mode="before")
    @classmethod
    def normalize_condition(cls, v: Any) -> str:
        cleaned = clean_string(v)
        return cleaned.upper() if cleaned else "ACCEPTED"

    @field_validator("received_date", mode="before")
    @classmethod
    def validate_received_date(cls, v: Any) -> datetime:
        dt = parse_flexible_datetime(v)
        if dt is None:
            raise ValueError("received_date cannot be empty")
        return dt


class InvoiceIngestSchema(BaseModel):
    invoice_number: str = Field(..., description="Vendor invoice number")
    po_number: Optional[str] = Field(None, description="Referenced Purchase Order number (nullable for unreferenced invoices)")
    vendor_id: str = Field(..., description="Vendor ID")
    vendor_name: str = Field(..., description="Vendor name")
    sku: str = Field(..., description="Item SKU code")
    quantity_billed: float = Field(..., gt=0, description="Billed quantity (must be > 0)")
    unit_price: float = Field(..., ge=0, description="Billed unit price (must be >= 0)")
    total_amount: Optional[float] = Field(None, description="Billed total amount (auto-calculated if omitted)")
    currency: Optional[str] = Field("USD", description="Currency code (e.g., USD, EUR)")
    invoice_date: Any = Field(..., description="Invoice issue date")
    due_date: Optional[Any] = Field(None, description="Payment due date")
    payment_status: Optional[str] = Field("PENDING_MATCH", description="Payment status")

    @field_validator("invoice_number", "vendor_id", "sku", mode="before")
    @classmethod
    def validate_identifiers(cls, v: Any) -> str:
        cleaned = clean_string(v)
        if not cleaned:
            raise ValueError("Field cannot be empty or only whitespace")
        return cleaned.upper()

    @field_validator("po_number", mode="before")
    @classmethod
    def clean_po_number(cls, v: Any) -> Optional[str]:
        cleaned = clean_string(v)
        return cleaned.upper() if cleaned else None

    @field_validator("vendor_name", mode="before")
    @classmethod
    def clean_vendor_name(cls, v: Any) -> str:
        cleaned = clean_string(v)
        if not cleaned:
            raise ValueError("vendor_name cannot be empty")
        return cleaned

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> str:
        cleaned = clean_string(v)
        if not cleaned:
            return "USD"
        cleaned = cleaned.upper()
        if len(cleaned) != 3:
            raise ValueError(f"Currency code must be a 3-letter ISO code, got '{cleaned}'")
        return cleaned

    @field_validator("payment_status", mode="before")
    @classmethod
    def normalize_payment_status(cls, v: Any) -> str:
        cleaned = clean_string(v)
        return cleaned.upper() if cleaned else "PENDING_MATCH"

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def validate_dates(cls, v: Any) -> Optional[datetime]:
        return parse_flexible_datetime(v)

    @model_validator(mode="after")
    def compute_total_if_missing(self) -> "InvoiceIngestSchema":
        if self.total_amount is None:
            self.total_amount = round(self.quantity_billed * self.unit_price, 2)
        else:
            self.total_amount = round(float(self.total_amount), 2)
        return self


# API Response schemas
class IngestionSuccessResponse(BaseModel):
    status: str = "success"
    message: str
    entity_type: str
    entity_id: str
    data: Dict[str, Any]


class CSVRowError(BaseModel):
    row_number: int
    raw_data: Dict[str, Any]
    error: str


class CSVIngestionResponse(BaseModel):
    status: str
    entity_type: str
    total_rows: int
    ingested_count: int
    failed_count: int
    errors: List[CSVRowError] = []
    message: str


# ============================================================================
# STEP 4: AI INVESTIGATION SCHEMAS
# ============================================================================

class InvestigationReport(BaseModel):
    exception_code: str = Field(..., description="Unique exception identifier")
    exception_type: str = Field(..., description="Exception type code")
    severity: str = Field("Medium", description="Severity level")
    variance_amount: float = Field(0.0, description="Quantified monetary risk or variance")
    root_cause_summary: str = Field(
        ...,
        description="Plain-English explanation of what broke (e.g. 'Invoice #409 bills for 100 units, but Warehouse Receipt #88 confirms only 80 units were delivered, creating a $400 overbilling risk.')",
    )
    recommended_actions: List[str] = Field(
        ...,
        description="2–3 concrete next steps (e.g. 'Request credit memo for 20 units', 'Ping warehouse manager to verify unlogged pallets')",
    )
    draft_communication: str = Field(
        ...,
        description="A ready-to-send email snippet to the vendor or internal approver.",
    )
    model_name: Optional[str] = Field("gemini-3.8-flash", description="LLM model used for analysis")
    investigated_at: Optional[str] = Field(None, description="ISO timestamp of when the analysis occurred")


class BatchInvestigationResponse(BaseModel):
    status: str
    total_investigated: int
    reports: List[InvestigationReport]
    message: str
