"""
OpsTrace Transaction Ingestion Layer - FastAPI Application.
Provides real-time webhook endpoints (JSON) and bulk file endpoints (CSV)
with strict Pydantic validation and audit logging.
"""

from contextlib import asynccontextmanager
import json
from typing import List
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog
from schemas import (
    PurchaseOrderIngestSchema,
    GoodsReceiptIngestSchema,
    InvoiceIngestSchema,
    IngestionSuccessResponse,
    CSVIngestionResponse,
    CSVRowError,
    InvestigationReport,
    BatchInvestigationResponse,
    ResolveExceptionRequest,
    ResolveExceptionResponse,
    AuditTrailEntry,
)
from csv_parser import parse_csv_content
from engine import reconcile_invoice, reconcile_all
from investigator import investigate_exception, investigate_batch
from workflow import resolve_exception_workflow, get_audit_trail_for_entity
from scheduler import (
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    run_scheduled_reconciliation_sweep,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure database tables exist on server startup and initialize background scheduler."""
    init_db()
    start_scheduler(interval_minutes=5)
    yield
    stop_scheduler()


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="OpsTrace ERP Transaction Ingestion API",
    description="Simulates ERP real-time event webhooks and bulk end-of-day CSV batch ingestion.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

frontend_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/ui", StaticFiles(directory=frontend_dist, html=True), name="ui")
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/ui/")


@app.get("/health", tags=["Health"])
def health_check(db: Session = Depends(get_db)):
    """Health check endpoint to verify API and DB connectivity."""
    try:
        db.execute(PurchaseOrder.__table__.select().limit(1))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "ok",
        "service": "OpsTrace Ingestion API",
        "database": db_status,
    }


# ============================================================================
# REAL-TIME WEBHOOK INGESTION ENDPOINTS (JSON)
# ============================================================================

@app.post(
    "/ingest/po",
    response_model=IngestionSuccessResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Real-Time Webhooks"],
    summary="Ingest Purchase Order event",
)
def ingest_purchase_order(payload: PurchaseOrderIngestSchema, db: Session = Depends(get_db)):
    """
    Ingest a single Purchase Order payload from an ERP webhook.
    Normalizes identifiers, enforces strict positive quantities, and logs audit trail.
    """
    # Check for duplicate PO number
    existing_po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == payload.po_number).first()
    if existing_po:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Purchase Order with po_number '{payload.po_number}' already exists in database.",
        )

    po = PurchaseOrder(
        po_number=payload.po_number,
        vendor_id=payload.vendor_id,
        vendor_name=payload.vendor_name,
        sku=payload.sku,
        description=payload.description,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        total_amount=payload.total_amount,
        currency=payload.currency,
        status=payload.status,
        order_date=payload.order_date,
        expected_delivery_date=payload.expected_delivery_date,
    )
    db.add(po)

    # Audit log
    audit = AuditLog(
        entity_type="PURCHASE_ORDER",
        entity_id=payload.po_number,
        action="WEBHOOK_INGEST",
        performed_by="ERP_WEBHOOK",
        timestamp=payload.order_date,
        details=f"Ingested PO {payload.po_number} via real-time webhook (Vendor: {payload.vendor_id}, Total: ${payload.total_amount})",
    )
    db.add(audit)
    db.commit()
    db.refresh(po)

    # Reconcile any existing invoices referencing this PO
    waiting_invoices = db.query(Invoice).filter(Invoice.po_number == po.po_number).all()
    for winv in waiting_invoices:
        reconcile_invoice(db, winv)

    return IngestionSuccessResponse(
        message=f"Purchase Order '{payload.po_number}' ingested successfully.",
        entity_type="purchase_order",
        entity_id=po.po_number,
        data={
            "po_number": po.po_number,
            "vendor_id": po.vendor_id,
            "sku": po.sku,
            "quantity": po.quantity,
            "unit_price": po.unit_price,
            "total_amount": po.total_amount,
            "currency": po.currency,
            "status": po.status,
        },
    )


@app.post(
    "/ingest/receipt",
    response_model=IngestionSuccessResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Real-Time Webhooks"],
    summary="Ingest Goods Receipt event",
)
def ingest_goods_receipt(payload: GoodsReceiptIngestSchema, db: Session = Depends(get_db)):
    """
    Ingest a warehouse Goods Receipt event.
    Normalizes identifiers, validates received quantities, and logs audit trail.
    """
    # Check for duplicate GR number
    existing_gr = db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == payload.gr_number).first()
    if existing_gr:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Goods Receipt with gr_number '{payload.gr_number}' already exists in database.",
        )

    gr = GoodsReceipt(
        gr_number=payload.gr_number,
        po_number=payload.po_number,
        vendor_id=payload.vendor_id,
        sku=payload.sku,
        quantity_received=payload.quantity_received,
        received_date=payload.received_date,
        received_by=payload.received_by,
        condition_status=payload.condition_status,
    )
    db.add(gr)

    # Audit log
    audit = AuditLog(
        entity_type="GOODS_RECEIPT",
        entity_id=payload.gr_number,
        action="WEBHOOK_INGEST",
        performed_by="WAREHOUSE_DOCK",
        timestamp=payload.received_date,
        details=f"Ingested Goods Receipt {payload.gr_number} for PO {payload.po_number} (Qty: {payload.quantity_received})",
    )
    db.add(audit)
    db.commit()
    db.refresh(gr)

    # Reconcile any existing invoices referencing this PO
    waiting_invoices = db.query(Invoice).filter(Invoice.po_number == gr.po_number).all()
    for winv in waiting_invoices:
        reconcile_invoice(db, winv)

    return IngestionSuccessResponse(
        message=f"Goods Receipt '{payload.gr_number}' ingested successfully.",
        entity_type="goods_receipt",
        entity_id=gr.gr_number,
        data={
            "gr_number": gr.gr_number,
            "po_number": gr.po_number,
            "vendor_id": gr.vendor_id,
            "sku": gr.sku,
            "quantity_received": gr.quantity_received,
            "condition_status": gr.condition_status,
        },
    )


@app.post(
    "/ingest/invoice",
    response_model=IngestionSuccessResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Real-Time Webhooks"],
    summary="Ingest Invoice event",
)
def ingest_invoice(payload: InvoiceIngestSchema, db: Session = Depends(get_db)):
    """
    Ingest a vendor Invoice event.
    Normalizes currency and pricing, records invoice, and logs audit trail.
    """
    inv = Invoice(
        invoice_number=payload.invoice_number,
        po_number=payload.po_number,
        vendor_id=payload.vendor_id,
        vendor_name=payload.vendor_name,
        sku=payload.sku,
        quantity_billed=payload.quantity_billed,
        unit_price=payload.unit_price,
        total_amount=payload.total_amount,
        currency=payload.currency,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        payment_status=payload.payment_status,
    )
    db.add(inv)

    # Audit log
    audit = AuditLog(
        entity_type="INVOICE",
        entity_id=payload.invoice_number,
        action="WEBHOOK_INGEST",
        performed_by="AP_GATEWAY",
        timestamp=payload.invoice_date,
        details=f"Ingested Invoice {payload.invoice_number} from vendor {payload.vendor_id} (Total: ${payload.total_amount} {payload.currency})",
    )
    db.add(audit)
    db.commit()
    db.refresh(inv)

    # Trigger deterministic 3-way reconciliation
    reconcile_invoice(db, inv)

    return IngestionSuccessResponse(
        message=f"Invoice '{payload.invoice_number}' ingested successfully.",
        entity_type="invoice",
        entity_id=inv.invoice_number,
        data={
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "po_number": inv.po_number,
            "vendor_id": inv.vendor_id,
            "quantity_billed": inv.quantity_billed,
            "unit_price": inv.unit_price,
            "total_amount": inv.total_amount,
            "currency": inv.currency,
            "payment_status": inv.payment_status,
        },
    )


# ============================================================================
# BULK CSV EXPORT INGESTION ENDPOINTS
# ============================================================================

NORM_ENTITY_TYPES = {
    "po": "purchase_order",
    "pos": "purchase_order",
    "purchase_order": "purchase_order",
    "purchase_orders": "purchase_order",
    "receipt": "goods_receipt",
    "receipts": "goods_receipt",
    "goods_receipt": "goods_receipt",
    "goods_receipts": "goods_receipt",
    "invoice": "invoice",
    "invoices": "invoice",
}


@app.post(
    "/ingest/csv/{entity_type}",
    response_model=CSVIngestionResponse,
    tags=["Bulk CSV Ingestion"],
    summary="Bulk ingest ERP export CSV files",
)
async def ingest_csv(
    entity_type: str,
    file: UploadFile = File(..., description="CSV file from ERP bulk export"),
    db: Session = Depends(get_db),
):
    """
    Parse bulk ERP export CSV files.
    Enforces per-row Pydantic validation, inserts valid rows, and captures row errors.
    """
    clean_type = entity_type.strip().lower()
    canonical_type = NORM_ENTITY_TYPES.get(clean_type)

    if not canonical_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported entity type '{entity_type}'. Allowed types: 'po', 'receipt', 'invoice'.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded CSV file is empty.",
        )

    rows = parse_csv_content(content)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file contains no valid data rows or empty headers.",
        )

    ingested_count = 0
    errors: List[CSVRowError] = []

    if canonical_type == "purchase_order":
        for idx, row in enumerate(rows, start=1):
            try:
                schema = PurchaseOrderIngestSchema(**row)
                # Check DB duplicate
                if db.query(PurchaseOrder).filter(PurchaseOrder.po_number == schema.po_number).first():
                    raise ValueError(f"Duplicate po_number '{schema.po_number}' already exists")

                po = PurchaseOrder(
                    po_number=schema.po_number,
                    vendor_id=schema.vendor_id,
                    vendor_name=schema.vendor_name,
                    sku=schema.sku,
                    description=schema.description,
                    quantity=schema.quantity,
                    unit_price=schema.unit_price,
                    total_amount=schema.total_amount,
                    currency=schema.currency,
                    status=schema.status,
                    order_date=schema.order_date,
                    expected_delivery_date=schema.expected_delivery_date,
                )
                db.add(po)
                ingested_count += 1
            except Exception as e:
                errors.append(CSVRowError(row_number=idx, raw_data=row, error=str(e)))

    elif canonical_type == "goods_receipt":
        for idx, row in enumerate(rows, start=1):
            try:
                schema = GoodsReceiptIngestSchema(**row)
                # Check DB duplicate
                if db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == schema.gr_number).first():
                    raise ValueError(f"Duplicate gr_number '{schema.gr_number}' already exists")

                gr = GoodsReceipt(
                    gr_number=schema.gr_number,
                    po_number=schema.po_number,
                    vendor_id=schema.vendor_id,
                    sku=schema.sku,
                    quantity_received=schema.quantity_received,
                    received_date=schema.received_date,
                    received_by=schema.received_by,
                    condition_status=schema.condition_status,
                )
                db.add(gr)
                ingested_count += 1
            except Exception as e:
                errors.append(CSVRowError(row_number=idx, raw_data=row, error=str(e)))

    elif canonical_type == "invoice":
        for idx, row in enumerate(rows, start=1):
            try:
                schema = InvoiceIngestSchema(**row)
                inv = Invoice(
                    invoice_number=schema.invoice_number,
                    po_number=schema.po_number,
                    vendor_id=schema.vendor_id,
                    vendor_name=schema.vendor_name,
                    sku=schema.sku,
                    quantity_billed=schema.quantity_billed,
                    unit_price=schema.unit_price,
                    total_amount=schema.total_amount,
                    currency=schema.currency,
                    invoice_date=schema.invoice_date,
                    due_date=schema.due_date,
                    payment_status=schema.payment_status,
                )
                db.add(inv)
                ingested_count += 1
            except Exception as e:
                errors.append(CSVRowError(row_number=idx, raw_data=row, error=str(e)))

    if ingested_count > 0:
        db.add(AuditLog(
            entity_type=canonical_type.upper(),
            entity_id=f"CSV_BATCH_{file.filename}",
            action="CSV_BULK_INGEST",
            performed_by="ERP_BATCH_JOB",
            details=f"Bulk ingested {ingested_count}/{len(rows)} records from '{file.filename}'. Failed: {len(errors)}",
        ))
        db.commit()
        # Trigger reconciliation on ingested dataset
        reconcile_all(db)

    res_status = "success" if len(errors) == 0 else ("partial_success" if ingested_count > 0 else "failed")
    return CSVIngestionResponse(
        status=res_status,
        entity_type=canonical_type,
        total_rows=len(rows),
        ingested_count=ingested_count,
        failed_count=len(errors),
        errors=errors,
        message=f"Processed {len(rows)} rows: {ingested_count} ingested, {len(errors)} failed.",
    )


# ============================================================================
# DETERMINISTIC RECONCILIATION & EXCEPTION ENDPOINTS
# ============================================================================

@app.post(
    "/reconcile/batch",
    tags=["Reconciliation Engine"],
    summary="Trigger deterministic 3-way match batch scan across all ERP records",
)
def run_batch_reconciliation(db: Session = Depends(get_db)):
    """
    Executes a comprehensive, deterministic 3-way match reconciliation scan.
    Evaluates quantity matching, price variances, completeness (missing PO/GR), and SLA breaches.
    """
    summary = reconcile_all(db)
    return summary


@app.get(
    "/exceptions",
    tags=["Exceptions"],
    summary="List flagged exceptions with raw snapshots and discrepancy details",
)
def list_exceptions(
    status: str = "OPEN",
    severity: str = None,
    exception_type: str = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Query exceptions flagged by the deterministic reconciliation engine.
    """
    query = db.query(ExceptionRecord)
    if status:
        query = query.filter(ExceptionRecord.status == status.upper())
    if severity:
        query = query.filter(ExceptionRecord.severity == severity.capitalize())
    if exception_type:
        query = query.filter(ExceptionRecord.exception_type == exception_type.upper())

    records = query.order_by(ExceptionRecord.created_at.desc()).limit(limit).all()
    results = []
    for r in records:
        results.append({
            "exception_code": r.exception_code,
            "po_number": r.po_number,
            "invoice_number": r.invoice_number,
            "gr_number": r.gr_number,
            "vendor_id": r.vendor_id,
            "exception_type": r.exception_type,
            "severity": r.severity,
            "variance_amount": r.variance_amount,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "discrepancy_details": json.loads(r.discrepancy_details) if r.discrepancy_details else None,
            "raw_snapshots": json.loads(r.raw_snapshots) if r.raw_snapshots else None,
            "ai_investigation": json.loads(r.ai_investigation) if r.ai_investigation else None,
        })
    return {"total": len(results), "exceptions": results}


# ============================================================================
# STEP 4: AI INVESTIGATION & ROOT-CAUSE ENDPOINTS
# ============================================================================

@app.post(
    "/investigate/batch",
    response_model=BatchInvestigationResponse,
    tags=["AI Investigation"],
    summary="Batch run AI investigations across multiple open exceptions",
)
def run_batch_investigations(
    limit: int = 5,
    severity: str = None,
    db: Session = Depends(get_db),
):
    """
    Batch investigates open exceptions with the AI operations analyst.
    """
    reports = investigate_batch(db, limit=limit, severity_filter=severity)
    return BatchInvestigationResponse(
        status="success",
        total_investigated=len(reports),
        reports=reports,
        message=f"Completed AI root-cause analysis on {len(reports)} exceptions.",
    )


@app.post(
    "/investigate/{exception_code}",
    response_model=InvestigationReport,
    tags=["AI Investigation"],
    summary="Run AI Operations Analyst root-cause investigation on an exception",
)
def run_investigation(exception_code: str, db: Session = Depends(get_db)):
    """
    Passes assembled PO, Goods Receipt, Invoice evidence and rule violation context
    to an LLM operations analyst. Returns root_cause_summary, recommended_actions, and draft_communication.
    """
    try:
        report = investigate_exception(db, exception_code)
        return report
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Investigation failed: {str(e)}")


@app.get(
    "/investigate/{exception_code}",
    response_model=InvestigationReport,
    tags=["AI Investigation"],
    summary="Get existing AI investigation report for an exception",
)
def get_investigation(exception_code: str, db: Session = Depends(get_db)):
    """
    Retrieve the cached AI investigation report for an exception. If not yet investigated, runs it on demand.
    """
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.exception_code == exception_code).first()
    if not exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Exception '{exception_code}' not found.")

    if exc.ai_investigation:
        try:
            data = json.loads(exc.ai_investigation)
            return InvestigationReport(**data)
        except Exception:
            pass

    # Run investigation if not yet cached
    report = investigate_exception(db, exception_code)
    return report


# ============================================================================
# STEP 5: HUMAN-IN-THE-LOOP RESOLUTION & AUDIT LOGGING ENDPOINTS
# ============================================================================

@app.post(
    "/exceptions/{id}/resolve",
    response_model=ResolveExceptionResponse,
    tags=["Human-in-the-Loop Workflow"],
    summary="Human reviewer resolves an exception with accountable state transition and audit logging",
)
def resolve_exception_endpoint(
    id: str,
    payload: ResolveExceptionRequest,
    db: Session = Depends(get_db),
):
    """
    Accepts human reviewer decision:
    - action_taken: REQUEST_CREDIT_MEMO, OVERRIDE_AND_PAY, ESCALATE, DISMISS
    - user_id: Reviewer identifier
    - notes: Auditable justification
    Updates state machine and appends an immutable row to audit_logs.
    """
    try:
        result = resolve_exception_workflow(
            db=db,
            exception_id_or_code=id,
            action_taken=payload.action_taken,
            user_id=payload.user_id,
            notes=payload.notes,
        )
        return ResolveExceptionResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Resolution failed: {str(e)}")


@app.get(
    "/exceptions/{id}/audit",
    response_model=List[AuditTrailEntry],
    tags=["Audit Trail"],
    summary="Get immutable audit trail of state transitions for an exception",
)
def get_exception_audit_trail(id: str, db: Session = Depends(get_db)):
    """
    Retrieve chronologically ordered immutable audit trail for an exception.
    """
    # Resolve code if numeric id passed
    code = id
    if id.isdigit():
        exc = db.query(ExceptionRecord).filter(ExceptionRecord.id == int(id)).first()
        if exc:
            code = exc.exception_code

    trail = get_audit_trail_for_entity(db, entity_id=code, entity_type="EXCEPTION")
    return trail


# ============================================================================
# AUTOMATION / SCHEDULER ENDPOINTS
# ============================================================================

@app.get(
    "/scheduler/status",
    tags=["Automation"],
    summary="Get operational status of background reconciliation sweeper",
)
def get_scheduler_status_endpoint():
    """Returns background APScheduler operational status and next sweep time."""
    return get_scheduler_status()


@app.post(
    "/scheduler/sweep",
    tags=["Automation"],
    summary="Manually trigger immediate 5-minute reconciliation sweep",
)
def trigger_sweep_endpoint():
    """Executes reconciliation sweep immediately and returns summary metrics."""
    summary = run_scheduled_reconciliation_sweep()
    return {"status": "success", "summary": summary}
