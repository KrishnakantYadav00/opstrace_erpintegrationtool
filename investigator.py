"""
AI Investigation & Root-Cause Engine for OpsTrace ERP.
Analyzes flagged exceptions using LLM Operations Analyst intelligence.
Bundles PO, Goods Receipt, Invoice evidence into a structured context
and enforces strict Pydantic output schemas (root_cause_summary, recommended_actions, draft_communication).
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from models import ExceptionRecord, PurchaseOrder, GoodsReceipt, Invoice, AuditLog
from schemas import InvestigationReport


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def assemble_investigation_context(exception: ExceptionRecord, db: Session) -> Dict[str, Any]:
    """
    Bundle the PO, Goods Receipt, Invoice, and specific rule violation into a clean JSON context.
    """
    # 1. Parse raw snapshots if available
    snapshots = {}
    if exception.raw_snapshots:
        try:
            snapshots = json.loads(exception.raw_snapshots)
        except Exception:
            pass

    # 2. Retrieve entities from DB or snapshots
    po_data = snapshots.get("purchase_order")
    if not po_data and exception.po_number:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exception.po_number).first()
        if po:
            po_data = {
                "po_number": po.po_number,
                "vendor_id": po.vendor_id,
                "vendor_name": po.vendor_name,
                "sku": po.sku,
                "description": po.description,
                "quantity": po.quantity,
                "unit_price": po.unit_price,
                "total_amount": po.total_amount,
                "order_date": po.order_date.isoformat() if po.order_date else None,
                "status": po.status,
            }

    receipt_data = snapshots.get("goods_receipt")
    if not receipt_data and exception.gr_number:
        gr = db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == exception.gr_number).first()
        if gr:
            receipt_data = {
                "gr_number": gr.gr_number,
                "po_number": gr.po_number,
                "sku": gr.sku,
                "quantity_received": gr.quantity_received,
                "received_date": gr.received_date.isoformat() if gr.received_date else None,
                "condition_status": gr.condition_status,
                "received_by": gr.received_by,
            }

    invoice_data = snapshots.get("invoice")
    if not invoice_data and exception.invoice_number:
        inv = db.query(Invoice).filter(Invoice.invoice_number == exception.invoice_number).first()
        if inv:
            invoice_data = {
                "invoice_number": inv.invoice_number,
                "po_number": inv.po_number,
                "vendor_id": inv.vendor_id,
                "vendor_name": inv.vendor_name,
                "sku": inv.sku,
                "quantity_billed": inv.quantity_billed,
                "unit_price": inv.unit_price,
                "total_amount": inv.total_amount,
                "currency": inv.currency,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "payment_status": inv.payment_status,
            }

    # Parse discrepancy details
    discrepancy = {}
    if exception.discrepancy_details:
        try:
            discrepancy = json.loads(exception.discrepancy_details)
        except Exception:
            discrepancy = {"raw": exception.discrepancy_details}

    context = {
        "exception_code": exception.exception_code,
        "exception_type": exception.exception_type,
        "severity": exception.severity,
        "variance_amount": exception.variance_amount or 0.0,
        "discrepancy_diagnostic": discrepancy,
        "linked_documents": {
            "purchase_order": po_data,
            "goods_receipt": receipt_data,
            "invoice": invoice_data,
        },
    }
    return context


def build_investigation_prompt(context: Dict[str, Any]) -> str:
    """Construct an operations analyst instruction prompt."""
    prompt = f"""You are an expert ERP Operations & Accounts Payable Forensic Analyst for OpsTrace.
Review the following flagged procurement exception and its linked 3-way match documents:

```json
{json.dumps(context, indent=2)}
```

Analyze the failure mode carefully. Provide:
1. root_cause_summary: A plain-English explanation of exactly what broke, referencing document IDs, quantities, prices, and quantifying the financial exposure or overbilling risk.
2. recommended_actions: Exactly 2 to 3 concrete, operational next steps (e.g. request credit memo, hold invoice, contact warehouse dock, verify receiving logs).
3. draft_communication: A ready-to-send, professional email snippet addressed to the vendor or internal approver clearly explaining the issue and requesting the required resolution.

Respond strictly following the requested JSON schema.
"""
    return prompt


def generate_rule_based_fallback_analysis(context: Dict[str, Any]) -> InvestigationReport:
    """
    High-fidelity deterministic analyst used when live LLM API is offline or unconfigured.
    Generates exact plain-English summaries, 2-3 actions, and draft communications.
    """
    exc_code = context["exception_code"]
    exc_type = context["exception_type"]
    severity = context["severity"]
    variance = context["variance_amount"]
    docs = context["linked_documents"]

    po = docs.get("purchase_order") or {}
    gr = docs.get("goods_receipt") or {}
    inv = docs.get("invoice") or {}

    inv_num = inv.get("invoice_number", "Unknown")
    po_num = po.get("po_number") or inv.get("po_number", "Unknown")
    gr_num = gr.get("gr_number", "None")
    vendor_name = inv.get("vendor_name") or po.get("vendor_name", "Vendor")

    if exc_type in ("QTY_MISMATCH_3WAY", "QUANTITY_MISMATCH"):
        po_qty = po.get("quantity", 0)
        gr_qty = gr.get("quantity_received", 0)
        inv_qty = inv.get("quantity_billed", 0)
        shortage = inv_qty - gr_qty
        unit_price = inv.get("unit_price", 0)
        overbill = round(shortage * unit_price, 2) if shortage > 0 else variance

        summary = (
            f"Invoice #{inv_num} bills for {inv_qty} units, but Warehouse Receipt #{gr_num} confirms only "
            f"{gr_qty} units were delivered (PO contracted: {po_qty} units), creating a ${overbill:.2f} overbilling risk."
        )
        actions = [
            f"Place Invoice #{inv_num} on payment hold pending quantity reconciliation.",
            f"Request a credit memo of ${overbill:.2f} for the {shortage} undelivered units from {vendor_name}.",
            f"Ping warehouse receiving dock to confirm no unlogged pallets or split shipments exist for PO #{po_num}.",
        ]
        comm = (
            f"Subject: Discrepancy on Invoice {inv_num} for PO {po_num}\n\n"
            f"Dear {vendor_name} Accounts Receivable Team,\n\n"
            f"Our warehouse intake records confirm receipt of {gr_qty} units under Goods Receipt {gr_num}. "
            f"However, invoice {inv_num} bills for {inv_qty} units at ${unit_price:.2f}/unit, resulting in a shortage of "
            f"{shortage} units (${overbill:.2f}).\n\n"
            f"We have placed this invoice on temporary hold. Please issue a credit memo for ${overbill:.2f} or advise if a secondary "
            f"shipment is in transit.\n\n"
            f"Best regards,\nOpsTrace Accounts Payable Operations"
        )

    elif exc_type == "PRICE_VARIANCE":
        po_price = po.get("unit_price", 0)
        inv_price = inv.get("unit_price", 0)
        qty = inv.get("quantity_billed", 0)
        unit_diff = round(inv_price - po_price, 2)

        summary = (
            f"Invoice #{inv_num} billed unit price of ${inv_price:.2f} exceeds approved Purchase Order #{po_num} "
            f"contracted price of ${po_price:.2f} by ${unit_diff:.2f} per unit, representing an unauthorized premium of ${variance:.2f}."
        )
        actions = [
            f"Notify procurement category manager to verify if a price adjustment was formally authorized for {vendor_name}.",
            f"Request a corrected invoice reflecting the agreed purchase order price of ${po_price:.2f}/unit.",
            f"Maintain payment hold on Invoice #{inv_num} to prevent overpayment.",
        ]
        comm = (
            f"Subject: Pricing Variance on Invoice {inv_num} (PO {po_num})\n\n"
            f"Dear {vendor_name} Billing Department,\n\n"
            f"Invoice {inv_num} reflects a unit price of ${inv_price:.2f}, which exceeds our contracted purchase order unit price "
            f"of ${po_price:.2f} on PO {po_num}. The unauthorized price difference totals ${variance:.2f}.\n\n"
            f"Kindly issue a revised invoice matching the approved PO rate or provide documentation of any mutually approved price change.\n\n"
            f"Sincerely,\nOpsTrace Procurement & AP Operations"
        )

    elif exc_type == "DUPLICATE_INVOICE":
        summary = (
            f"Invoice #{inv_num} submitted by {vendor_name} appears to be a duplicate submission for total amount "
            f"${variance:.2f} within 48 hours of an existing billing event, presenting an immediate double-payment risk."
        )
        actions = [
            f"Immediately reject or flag Invoice #{inv_num} in the ERP payment queue to prevent disbursement.",
            f"Check banking and automated clearing house (ACH) batches to confirm the original invoice is not scheduled twice.",
            f"Contact {vendor_name} AR team to clarify if this was an accidental duplicate transmission.",
        ]
        comm = (
            f"Subject: Notice of Duplicate Invoice Submission - {inv_num}\n\n"
            f"Dear {vendor_name} Accounts Receivable,\n\n"
            f"Our automated invoice screening system detected that invoice {inv_num} for amount ${variance:.2f} appears identical "
            f"to a recently processed invoice from your company.\n\n"
            f"We have voided this second entry to prevent double payment. Please confirm your records.\n\n"
            f"Regards,\nOpsTrace Accounts Payable"
        )

    elif exc_type in ("GHOST_RECEIPT_MISSING_GR", "MISSING_RECEIPT"):
        summary = (
            f"Invoice #{inv_num} was submitted against PO #{po_num} for ${variance:.2f}, but after more than 7 days "
            f"no Goods Receipt has been registered by warehouse receiving, indicating either missing delivery or unlogged goods."
        )
        actions = [
            f"Contact warehouse logistics manager to perform physical dock check for PO #{po_num}.",
            f"Request proof of delivery (POD) and carrier tracking documentation from {vendor_name}.",
            f"Hold Invoice #{inv_num} until physical goods receipt and inspection are certified in the ERP.",
        ]
        comm = (
            f"Subject: Proof of Delivery Required for Invoice {inv_num} / PO {po_num}\n\n"
            f"Dear {vendor_name} Shipping & Logistics,\n\n"
            f"We have received invoice {inv_num} for PO {po_num} (${variance:.2f}). However, our facility has no record "
            f"of receiving the associated goods.\n\n"
            f"Please furnish carrier tracking and signed proof of delivery (POD) so we may locate the shipment and authorize payment.\n\n"
            f"Thank you,\nOpsTrace Receiving & AP"
        )

    elif exc_type == "MISSING_PO":
        summary = (
            f"Invoice #{inv_num} for ${variance:.2f} references non-existent or unrecognized Purchase Order '{po_num}', "
            f"indicating an unauthorized purchase, misrouted bill, or fraudulent billing attempt."
        )
        actions = [
            f"Reject Invoice #{inv_num} and request a valid, approved purchase order number from {vendor_name}.",
            f"Forward invoice to department purchasing lead to determine if goods were ordered outside standard ERP requisition.",
            f"Flag vendor account for non-PO compliance review.",
        ]
        comm = (
            f"Subject: Invoice {inv_num} Rejected - Missing Valid Purchase Order\n\n"
            f"Dear {vendor_name},\n\n"
            f"We received invoice {inv_num} for ${variance:.2f} referencing PO '{po_num}'. Our ERP system does not contain "
            f"an approved purchase order matching this reference. In accordance with company policy, invoices without a valid PO cannot be processed.\n\n"
            f"Please provide the correct PO number or contact the department contact who initiated the order.\n\n"
            f"Regards,\nOpsTrace AP Compliance"
        )

    else:
        summary = (
            f"Exception {exc_code} ({exc_type}) flagged on document {inv_num or po_num} with severity {severity} "
            f"and estimated variance of ${variance:.2f}."
        )
        actions = [
            f"Review exception details and verify document records for {exc_code}.",
            f"Coordinate between AP specialist and vendor {vendor_name} to reconcile discrepancy.",
            f"Update exception status to RESOLVED upon settlement.",
        ]
        comm = (
            f"Subject: Operational Review on {exc_code}\n\n"
            f"Hello,\n\nPlease review exception {exc_code} regarding document {inv_num or po_num}. "
            f"A discrepancy of ${variance:.2f} requires approval before proceeding.\n\n"
            f"Regards,\nOpsTrace ERP Team"
        )

    return InvestigationReport(
        exception_code=exc_code,
        exception_type=exc_type,
        severity=severity,
        variance_amount=variance,
        root_cause_summary=summary,
        recommended_actions=actions,
        draft_communication=comm,
        model_name="deterministic-analyst-fallback",
        investigated_at=utc_now_iso(),
    )


def investigate_exception(
    db: Session,
    exception_code: str,
    use_live_llm: bool = True,
) -> InvestigationReport:
    """
    Investigate an exception: assemble context, query LLM with structured output schema,
    persist result into the exception record, and write audit log.
    """
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.exception_code == exception_code).first()
    if not exc:
        raise ValueError(f"Exception with code '{exception_code}' not found.")

    # 1. Assemble clean context
    context = assemble_investigation_context(exc, db)
    report: Optional[InvestigationReport] = None

    # 2. Attempt live Gemini API call if API key is configured
    api_key = os.getenv("GEMINI_API_KEY")
    if use_live_llm and api_key:
        try:
            from google import genai

            client = genai.Client(api_key=api_key)
            prompt = build_investigation_prompt(context)

            interaction = client.interactions.create(
                model="gemini-3.8-flash",
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": InvestigationReport.model_json_schema(),
                },
            )

            if interaction.output_text:
                report = InvestigationReport.model_validate_json(interaction.output_text)
                report.model_name = "gemini-3.8-flash"
                report.investigated_at = utc_now_iso()
        except Exception as e:
            # Fall back smoothly if live API call errors out
            print(f"[!] Warning: Live Gemini API call encountered an error ({e}). Using deterministic analyst fallback.")

    # 3. Fallback if live LLM not available or returned None
    if report is None:
        report = generate_rule_based_fallback_analysis(context)

    # 4. Persist to ExceptionRecord and write immutable AuditLog for state transition
    before_state = exc.status or "DETECTED"
    exc.ai_investigation = json.dumps(report.model_dump())
    exc.status = "AI_ANALYZED"

    audit = AuditLog(
        entity_type="EXCEPTION",
        entity_id=exc.exception_code,
        action="STATE_TRANSITION",
        performed_by="AI_AGENT",
        before_state=before_state,
        after_state="AI_ANALYZED",
        timestamp=datetime.now(timezone.utc),
        details=json.dumps({
            "action_taken": "AI_ROOT_CAUSE_ANALYSIS",
            "model_name": report.model_name,
            "root_cause_summary": report.root_cause_summary,
            "recommended_actions": report.recommended_actions,
        }),
    )
    db.add(audit)
    db.commit()
    db.refresh(exc)

    return report


def investigate_batch(
    db: Session,
    limit: int = 10,
    severity_filter: Optional[str] = None,
    use_live_llm: bool = True,
) -> List[InvestigationReport]:
    """
    Batch investigate open exceptions.
    """
    query = db.query(ExceptionRecord).filter(ExceptionRecord.status.in_(["OPEN", "DETECTED", "UNDER_REVIEW"]))
    if severity_filter:
        query = query.filter(ExceptionRecord.severity == severity_filter.capitalize())

    exceptions = query.limit(limit).all()
    reports = []
    for exc in exceptions:
        rep = investigate_exception(db, exc.exception_code, use_live_llm=use_live_llm)
        reports.append(rep)
    return reports
