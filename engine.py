"""
Deterministic Reconciliation & Exception Engine for OpsTrace ERP.
Evaluates 3-way matching rules deterministically without non-deterministic LLMs.

Rules:
1. Quantity Check: invoice.qty != receipt.qty or receipt.qty != po.qty
2. Price Check: invoice.unit_price > po.unit_price
3. Completeness Check: Invoice exists, but receipt is None or po is None
4. SLA Check: status == "PENDING_APPROVAL" and created_at < now() - timedelta(days=3)
5. Duplicate Invoice Check: same invoice_number or identical vendor + amount within 48h
"""

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session

from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog


def utc_now() -> datetime:
    """Current UTC datetime."""
    return datetime.now(timezone.utc)


def serialize_entity(entity: Any) -> Optional[Dict[str, Any]]:
    """Convert an SQLAlchemy model instance into a JSON-safe dictionary."""
    if entity is None:
        return None
    data = {}
    for column in entity.__table__.columns:
        val = getattr(entity, column.name)
        if isinstance(val, datetime):
            data[column.name] = val.isoformat()
        else:
            data[column.name] = val
    return data


def build_raw_snapshots(
    po: Optional[PurchaseOrder] = None,
    receipt: Optional[GoodsReceipt] = None,
    invoice: Optional[Invoice] = None,
) -> str:
    """Serialize linked PO, Receipt, and Invoice into a JSON string."""
    snapshots = {
        "purchase_order": serialize_entity(po),
        "goods_receipt": serialize_entity(receipt),
        "invoice": serialize_entity(invoice),
    }
    return json.dumps(snapshots, indent=2)


class DiscrepancyResult:
    """Encapsulates the evaluation result of a reconciliation check."""

    def __init__(
        self,
        exception_type: str,
        severity: str,
        variance_amount: float,
        details: Dict[str, Any],
        po_number: Optional[str] = None,
        invoice_number: Optional[str] = None,
        gr_number: Optional[str] = None,
        vendor_id: Optional[str] = None,
    ):
        self.exception_type = exception_type
        self.severity = severity  # High, Medium, Low
        self.variance_amount = round(float(variance_amount), 2)
        self.details = details
        self.po_number = po_number
        self.invoice_number = invoice_number
        self.gr_number = gr_number
        self.vendor_id = vendor_id


# ============================================================================
# PURE PYTHON DETERMINISTIC RULE CHECKS
# ============================================================================

def evaluate_quantity_check(
    po: Optional[PurchaseOrder],
    receipt: Optional[GoodsReceipt],
    invoice: Optional[Invoice],
) -> Optional[DiscrepancyResult]:
    """
    Quantity Check: invoice.qty != receipt.qty or receipt.qty != po.qty
    """
    if invoice is None or (receipt is None and po is None):
        return None

    discrepancies = []
    inv_qty = invoice.quantity_billed
    receipt_qty = receipt.quantity_received if receipt else None
    po_qty = po.quantity if po else None

    # Compare invoice vs receipt
    if receipt is not None and inv_qty != receipt_qty:
        diff = round(inv_qty - receipt_qty, 2)
        discrepancies.append(
            f"Invoice billed quantity ({inv_qty}) does not match warehouse received quantity ({receipt_qty}). Difference: {diff:+} units"
        )

    # Compare receipt vs PO
    if receipt is not None and po is not None and receipt_qty != po_qty:
        diff = round(receipt_qty - po_qty, 2)
        discrepancies.append(
            f"Warehouse received quantity ({receipt_qty}) does not match PO contracted quantity ({po_qty}). Difference: {diff:+} units"
        )

    # Compare invoice vs PO if receipt missing
    if receipt is None and po is not None and inv_qty != po_qty:
        diff = round(inv_qty - po_qty, 2)
        discrepancies.append(
            f"Invoice billed quantity ({inv_qty}) does not match PO contracted quantity ({po_qty}). Difference: {diff:+} units"
        )

    if not discrepancies:
        return None

    # Calculate monetary variance amount
    if receipt is not None:
        qty_diff = abs(inv_qty - receipt_qty)
        variance_amt = round(qty_diff * invoice.unit_price, 2)
    elif po is not None:
        qty_diff = abs(inv_qty - po_qty)
        variance_amt = round(qty_diff * invoice.unit_price, 2)
    else:
        variance_amt = 0.0

    severity = "High" if (receipt and inv_qty > receipt.quantity_received) or variance_amt > 200 else "Medium"

    details = {
        "rule": "QUANTITY_CHECK",
        "description": "; ".join(discrepancies),
        "invoice_quantity": inv_qty,
        "receipt_quantity": receipt_qty,
        "po_quantity": po_qty,
        "variance_amount": variance_amt,
    }

    return DiscrepancyResult(
        exception_type="QUANTITY_MISMATCH",
        severity=severity,
        variance_amount=variance_amt,
        details=details,
        po_number=invoice.po_number or (po.po_number if po else None),
        invoice_number=invoice.invoice_number,
        gr_number=receipt.gr_number if receipt else None,
        vendor_id=invoice.vendor_id,
    )


def evaluate_price_check(
    po: Optional[PurchaseOrder],
    invoice: Optional[Invoice],
) -> Optional[DiscrepancyResult]:
    """
    Price Check: invoice.unit_price > po.unit_price
    """
    if po is None or invoice is None:
        return None

    if invoice.unit_price > po.unit_price:
        unit_diff = round(invoice.unit_price - po.unit_price, 2)
        variance_pct = round((unit_diff / po.unit_price) * 100, 2)
        total_variance = round(unit_diff * invoice.quantity_billed, 2)

        severity = "High" if variance_pct >= 10.0 or total_variance >= 250.0 else "Medium"

        details = {
            "rule": "PRICE_CHECK",
            "description": f"Invoice unit price ${invoice.unit_price:.2f} exceeds PO unit price ${po.unit_price:.2f} by ${unit_diff:.2f} ({variance_pct}% markup)",
            "po_unit_price": po.unit_price,
            "invoice_unit_price": invoice.unit_price,
            "unit_price_variance": unit_diff,
            "variance_percentage": variance_pct,
            "total_variance_amount": total_variance,
        }

        return DiscrepancyResult(
            exception_type="PRICE_VARIANCE",
            severity=severity,
            variance_amount=total_variance,
            details=details,
            po_number=po.po_number,
            invoice_number=invoice.invoice_number,
            vendor_id=invoice.vendor_id,
        )

    return None


def evaluate_completeness_check(
    po: Optional[PurchaseOrder],
    receipt: Optional[GoodsReceipt],
    invoice: Optional[Invoice],
) -> Optional[DiscrepancyResult]:
    """
    Completeness Check: Invoice exists, but receipt is None or po is None
    """
    if invoice is None:
        return None

    # Check Missing PO
    if po is None:
        return DiscrepancyResult(
            exception_type="MISSING_PO",
            severity="High",
            variance_amount=invoice.total_amount,
            details={
                "rule": "COMPLETENESS_CHECK",
                "description": f"Invoice '{invoice.invoice_number}' references missing or non-existent PO '{invoice.po_number}'.",
                "invoice_number": invoice.invoice_number,
                "referenced_po": invoice.po_number,
                "amount": invoice.total_amount,
            },
            po_number=invoice.po_number,
            invoice_number=invoice.invoice_number,
            vendor_id=invoice.vendor_id,
        )

    # Check Missing Goods Receipt
    if receipt is None:
        return DiscrepancyResult(
            exception_type="MISSING_RECEIPT",
            severity="High",
            variance_amount=invoice.total_amount,
            details={
                "rule": "COMPLETENESS_CHECK",
                "description": f"Invoice '{invoice.invoice_number}' exists for PO '{po.po_number}', but no Goods Receipt was found.",
                "invoice_number": invoice.invoice_number,
                "po_number": po.po_number,
                "amount": invoice.total_amount,
            },
            po_number=po.po_number,
            invoice_number=invoice.invoice_number,
            vendor_id=invoice.vendor_id,
        )

    return None


def evaluate_sla_check(
    entity: Any,
    entity_type: str,
    days_threshold: int = 3,
    now_dt: Optional[datetime] = None,
) -> Optional[DiscrepancyResult]:
    """
    SLA Check: status == 'PENDING_APPROVAL' (or PENDING_MATCH) and created_at < now() - timedelta(days=3)
    """
    if entity is None:
        return None

    current_time = now_dt or utc_now()
    created_at = getattr(entity, "created_at", None) or getattr(entity, "order_date", None)
    if not created_at:
        return None

    # Make tz-naive for consistent subtraction if needed
    if created_at.tzinfo is not None and current_time.tzinfo is None:
        created_at = created_at.replace(tzinfo=None)
    elif created_at.tzinfo is None and current_time.tzinfo is not None:
        current_time = current_time.replace(tzinfo=None)

    elapsed_time = current_time - created_at
    threshold = timedelta(days=days_threshold)

    status_val = getattr(entity, "status", None) or getattr(entity, "payment_status", "")
    status_upper = str(status_val).upper()

    pending_statuses = {"PENDING_APPROVAL", "PENDING_MATCH", "ON_HOLD", "ISSUED"}
    if status_upper in pending_statuses and elapsed_time > threshold:
        elapsed_days = round(elapsed_time.total_seconds() / 86400, 1)
        po_no = getattr(entity, "po_number", None)
        inv_no = getattr(entity, "invoice_number", None)
        total_amt = getattr(entity, "total_amount", 0.0)

        return DiscrepancyResult(
            exception_type="SLA_BREACH",
            severity="Medium",
            variance_amount=total_amt,
            details={
                "rule": "SLA_CHECK",
                "description": f"{entity_type} {inv_no or po_no} in status '{status_upper}' has breached the {days_threshold}-day SLA ({elapsed_days} days elapsed).",
                "status": status_upper,
                "days_elapsed": elapsed_days,
                "days_threshold": days_threshold,
                "created_at": created_at.isoformat(),
            },
            po_number=po_no,
            invoice_number=inv_no,
            vendor_id=getattr(entity, "vendor_id", None),
        )

    return None


def evaluate_duplicate_invoice_check(
    invoice: Invoice,
    db: Session,
) -> Optional[DiscrepancyResult]:
    """
    Duplicate Invoice Check:
    Same invoice_number OR identical amount/vendor within 48 hours.
    """
    # 1. Exact invoice_number duplicate
    same_number_invoices = (
        db.query(Invoice)
        .filter(Invoice.invoice_number == invoice.invoice_number, Invoice.id != invoice.id)
        .all()
    )
    if same_number_invoices:
        orig = same_number_invoices[0]
        return DiscrepancyResult(
            exception_type="DUPLICATE_INVOICE",
            severity="High",
            variance_amount=invoice.total_amount,
            details={
                "rule": "DUPLICATE_CHECK",
                "duplicate_type": "EXACT_INVOICE_NUMBER",
                "description": f"Invoice '{invoice.invoice_number}' is a duplicate of invoice record ID {orig.id}.",
                "original_invoice_id": orig.id,
                "amount": invoice.total_amount,
            },
            po_number=invoice.po_number,
            invoice_number=invoice.invoice_number,
            vendor_id=invoice.vendor_id,
        )

    # 2. Identical vendor and amount within 48 hours
    window_start = invoice.invoice_date - timedelta(hours=48)
    window_end = invoice.invoice_date + timedelta(hours=48)

    same_vendor_amount = (
        db.query(Invoice)
        .filter(
            Invoice.vendor_id == invoice.vendor_id,
            Invoice.total_amount == invoice.total_amount,
            Invoice.id != invoice.id,
            Invoice.invoice_date.between(window_start, window_end),
        )
        .first()
    )
    if same_vendor_amount:
        return DiscrepancyResult(
            exception_type="DUPLICATE_INVOICE",
            severity="High",
            variance_amount=invoice.total_amount,
            details={
                "rule": "DUPLICATE_CHECK",
                "duplicate_type": "VENDOR_AMOUNT_WITHIN_48H",
                "description": f"Invoice '{invoice.invoice_number}' matches vendor '{invoice.vendor_id}' and amount ${invoice.total_amount:.2f} of invoice '{same_vendor_amount.invoice_number}' within 48h.",
                "original_invoice_number": same_vendor_amount.invoice_number,
                "amount": invoice.total_amount,
            },
            po_number=invoice.po_number,
            invoice_number=invoice.invoice_number,
            vendor_id=invoice.vendor_id,
        )

    return None


# ============================================================================
# ORCHESTRATION & RECORD PERSISTENCE
# ============================================================================

def record_exception_if_not_exists(
    db: Session,
    discrepancy: DiscrepancyResult,
    po: Optional[PurchaseOrder] = None,
    receipt: Optional[GoodsReceipt] = None,
    invoice: Optional[Invoice] = None,
) -> Optional[ExceptionRecord]:
    """
    Persist an ExceptionRecord if an identical open exception does not already exist.
    """
    existing = (
        db.query(ExceptionRecord)
        .filter(
            ExceptionRecord.exception_type == discrepancy.exception_type,
            ExceptionRecord.po_number == discrepancy.po_number,
            ExceptionRecord.invoice_number == discrepancy.invoice_number,
            ExceptionRecord.status == "OPEN",
        )
        .first()
    )
    if existing:
        return None

    code = f"EXC-{uuid.uuid4().hex[:8].upper()}"
    raw_snapshots = build_raw_snapshots(po, receipt, invoice)

    record = ExceptionRecord(
        exception_code=code,
        po_number=discrepancy.po_number,
        invoice_number=discrepancy.invoice_number,
        gr_number=discrepancy.gr_number,
        vendor_id=discrepancy.vendor_id,
        exception_type=discrepancy.exception_type,
        severity=discrepancy.severity,
        variance_amount=discrepancy.variance_amount,
        raw_snapshots=raw_snapshots,
        discrepancy_details=json.dumps(discrepancy.details),
        status="OPEN",
        created_at=utc_now(),
    )
    db.add(record)

    # Audit log
    audit = AuditLog(
        entity_type="EXCEPTION",
        entity_id=code,
        action="DETERMINISTIC_EXCEPTION_GENERATED",
        performed_by="RECONCILIATION_ENGINE",
        details=f"Raised {discrepancy.severity} severity exception '{discrepancy.exception_type}' (Variance: ${discrepancy.variance_amount}): {discrepancy.details.get('description')}",
    )
    db.add(audit)
    db.commit()
    db.refresh(record)
    return record


def reconcile_invoice(
    db: Session,
    invoice: Invoice,
    now_dt: Optional[datetime] = None,
) -> List[ExceptionRecord]:
    """
    Run all deterministic reconciliation rules on a single invoice and its linked records.
    """
    created_exceptions: List[ExceptionRecord] = []

    # Look up linked PO and Goods Receipt
    po = None
    receipt = None
    if invoice.po_number:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == invoice.po_number).first()
        receipt = db.query(GoodsReceipt).filter(GoodsReceipt.po_number == invoice.po_number).first()

    # 1. Duplicate check
    dup_res = evaluate_duplicate_invoice_check(invoice, db)
    if dup_res:
        exc = record_exception_if_not_exists(db, dup_res, po, receipt, invoice)
        if exc:
            created_exceptions.append(exc)

    # 2. Completeness check
    comp_res = evaluate_completeness_check(po, receipt, invoice)
    if comp_res:
        exc = record_exception_if_not_exists(db, comp_res, po, receipt, invoice)
        if exc:
            created_exceptions.append(exc)

    # 3. Quantity check (if PO or receipt available)
    if po is not None or receipt is not None:
        qty_res = evaluate_quantity_check(po, receipt, invoice)
        if qty_res:
            exc = record_exception_if_not_exists(db, qty_res, po, receipt, invoice)
            if exc:
                created_exceptions.append(exc)

    # 4. Price check (if PO available)
    if po is not None:
        price_res = evaluate_price_check(po, invoice)
        if price_res:
            exc = record_exception_if_not_exists(db, price_res, po, receipt, invoice)
            if exc:
                created_exceptions.append(exc)

    # 5. SLA check
    sla_res = evaluate_sla_check(invoice, "Invoice", days_threshold=3, now_dt=now_dt)
    if sla_res:
        exc = record_exception_if_not_exists(db, sla_res, po, receipt, invoice)
        if exc:
            created_exceptions.append(exc)

    return created_exceptions


def reconcile_all(db: Session, now_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Batch scan across all invoices and POs in the ERP database.
    Evaluates every deterministic 3-way matching and SLA rule.
    """
    all_invoices = db.query(Invoice).all()
    all_pos = db.query(PurchaseOrder).all()

    total_exceptions_created = 0
    exceptions_by_type: Dict[str, int] = {}

    # Scan invoices
    for inv in all_invoices:
        excs = reconcile_invoice(db, inv, now_dt=now_dt)
        for exc in excs:
            total_exceptions_created += 1
            exceptions_by_type[exc.exception_type] = exceptions_by_type.get(exc.exception_type, 0) + 1

    # Scan POs for SLA breaches
    for po in all_pos:
        sla_res = evaluate_sla_check(po, "PurchaseOrder", days_threshold=3, now_dt=now_dt)
        if sla_res:
            exc = record_exception_if_not_exists(db, sla_res, po=po)
            if exc:
                total_exceptions_created += 1
                exceptions_by_type[exc.exception_type] = exceptions_by_type.get(exc.exception_type, 0) + 1

    return {
        "status": "success",
        "total_invoices_scanned": len(all_invoices),
        "total_pos_scanned": len(all_pos),
        "exceptions_generated": total_exceptions_created,
        "breakdown_by_type": exceptions_by_type,
    }
