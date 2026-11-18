import json
import pytest
from datetime import datetime, timezone, timedelta
from database import SessionLocal, init_db
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog
from engine import reconcile_invoice, reconcile_all
from workflow import resolve_exception_workflow, get_audit_trail_for_entity, ExceptionState


@pytest.fixture(autouse=True)
def clean_db():
    """Ensure clean database tables for edge case testing."""
    init_db(drop_all=True)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_exact_3way_match_zero_exceptions(db):
    """
    Edge Case 1: Exact 3-Way Match (0 exceptions).
    PO = 100 @ $20.00, Receipt = 100, Invoice = 100 @ $20.00.
    Reconciliation must cleanly pass with 0 exceptions created.
    """
    po_no = "PO-CLEAN-100"
    gr_no = "GR-CLEAN-100"
    inv_no = "INV-CLEAN-100"
    base_time = datetime(2026, 3, 1, 10, 0, 0)

    # 1. PO: 100 units @ $20.00
    po = PurchaseOrder(
        po_number=po_no,
        vendor_id="VEND-CLEAN",
        vendor_name="Precision Industrial Components",
        sku="SKU-BOLT-100",
        description="High-Tensile Precision Bolts",
        quantity=100.0,
        unit_price=20.0,
        total_amount=2000.0,
        status="RECEIVED",
        order_date=base_time,
    )
    db.add(po)

    # 2. Receipt: 100 units received in full
    gr = GoodsReceipt(
        gr_number=gr_no,
        po_number=po_no,
        vendor_id="VEND-CLEAN",
        sku="SKU-BOLT-100",
        quantity_received=100.0,
        received_date=base_time + timedelta(days=3),
        condition_status="ACCEPTED",
    )
    db.add(gr)

    # 3. Invoice: 100 units billed @ $20.00
    inv = Invoice(
        invoice_number=inv_no,
        po_number=po_no,
        vendor_id="VEND-CLEAN",
        vendor_name="Precision Industrial Components",
        sku="SKU-BOLT-100",
        quantity_billed=100.0,
        unit_price=20.0,
        total_amount=2000.0,
        invoice_date=base_time + timedelta(days=5),
        payment_status="PENDING_MATCH",
    )
    db.add(inv)
    db.commit()

    # Reconcile invoice
    exceptions = reconcile_invoice(db, inv)
    assert len(exceptions) == 0, f"Expected 0 exceptions for clean 3-way match, found {len(exceptions)}"

    # Check total exceptions in database
    total_exceptions = db.query(ExceptionRecord).count()
    assert total_exceptions == 0, f"Total exceptions in DB should be 0, found {total_exceptions}"


def test_partial_delivery_mismatch_edge_case(db):
    """
    Edge Case 2: Partial Delivery Mismatch.
    PO = 100 @ $15.00, Receipt = 60 (Shortage: 40 units), Invoice = 100 @ $15.00.
    Must flag QUANTITY_MISMATCH with exact variance $600.00 and full document snapshots.
    """
    po_no = "PO-PARTIAL-200"
    gr_no = "GR-PARTIAL-200"
    inv_no = "INV-PARTIAL-200"
    base_time = datetime(2026, 3, 1, 10, 0, 0)

    po = PurchaseOrder(
        po_number=po_no,
        vendor_id="VEND-SHORT",
        vendor_name="Acme Raw Metals",
        sku="SKU-STEEL-BAR",
        quantity=100.0,
        unit_price=15.0,
        total_amount=1500.0,
        status="PARTIALLY_RECEIVED",
        order_date=base_time,
    )
    db.add(po)

    # Warehouse only received 60 units (shortage of 40)
    gr = GoodsReceipt(
        gr_number=gr_no,
        po_number=po_no,
        vendor_id="VEND-SHORT",
        sku="SKU-STEEL-BAR",
        quantity_received=60.0,
        received_date=base_time + timedelta(days=3),
        condition_status="SHORTAGE",
    )
    db.add(gr)

    # Vendor bills for full 100 units
    inv = Invoice(
        invoice_number=inv_no,
        po_number=po_no,
        vendor_id="VEND-SHORT",
        vendor_name="Acme Raw Metals",
        sku="SKU-STEEL-BAR",
        quantity_billed=100.0,
        unit_price=15.0,
        total_amount=1500.0,
        invoice_date=base_time + timedelta(days=5),
        payment_status="ON_HOLD",
    )
    db.add(inv)
    db.commit()

    # Reconcile
    exceptions = reconcile_invoice(db, inv)
    assert len(exceptions) >= 1

    qty_exc = next((e for e in exceptions if e.exception_type in ("QUANTITY_MISMATCH", "QTY_MISMATCH_3WAY")), None)
    assert qty_exc is not None, "QUANTITY_MISMATCH exception was not created"
    assert qty_exc.severity == "High"
    # Overbilling: (100 billed - 60 delivered) * $15.00 = $600.00
    assert qty_exc.variance_amount == 600.0

    # Verify document snapshots
    snapshots = json.loads(qty_exc.raw_snapshots)
    assert snapshots["purchase_order"]["quantity"] == 100.0
    assert snapshots["goods_receipt"]["quantity_received"] == 60.0
    assert snapshots["invoice"]["quantity_billed"] == 100.0


def test_duplicate_invoice_ingestion_edge_case(db):
    """
    Edge Case 3: Duplicate Invoice Ingestion.
    Primary invoice ingested; secondary invoice submitted with identical amount & vendor within 48h.
    Must flag DUPLICATE_INVOICE exception.
    """
    base_time = datetime(2026, 3, 5, 12, 0, 0)
    po_no = "PO-DUP-TEST"

    po = PurchaseOrder(
        po_number=po_no,
        vendor_id="VEND-DUP",
        vendor_name="Double Bill Logistics",
        sku="SKU-PKG-50",
        quantity=50.0,
        unit_price=20.0,
        total_amount=1000.0,
        order_date=base_time - timedelta(days=5),
    )
    db.add(po)

    gr = GoodsReceipt(
        gr_number="GR-DUP-TEST",
        po_number=po_no,
        vendor_id="VEND-DUP",
        sku="SKU-PKG-50",
        quantity_received=50.0,
        received_date=base_time - timedelta(days=2),
    )
    db.add(gr)

    # 1. Primary Invoice
    inv1 = Invoice(
        invoice_number="INV-ORIGINAL-01",
        po_number=po_no,
        vendor_id="VEND-DUP",
        vendor_name="Double Bill Logistics",
        sku="SKU-PKG-50",
        quantity_billed=50.0,
        unit_price=20.0,
        total_amount=1000.0,
        invoice_date=base_time,
        payment_status="APPROVED",
    )
    db.add(inv1)
    db.commit()

    # Reconcile primary invoice (clean)
    reconcile_invoice(db, inv1)

    # 2. Secondary duplicate invoice submitted 18 hours later
    inv2 = Invoice(
        invoice_number="INV-DUPLICATE-02",
        po_number=po_no,
        vendor_id="VEND-DUP",
        vendor_name="Double Bill Logistics",
        sku="SKU-PKG-50",
        quantity_billed=50.0,
        unit_price=20.0,
        total_amount=1000.0,
        invoice_date=base_time + timedelta(hours=18),
        payment_status="PENDING_MATCH",
    )
    db.add(inv2)
    db.commit()

    # Reconcile duplicate invoice
    dup_exceptions = reconcile_invoice(db, inv2)
    assert len(dup_exceptions) >= 1

    dup_exc = next((e for e in dup_exceptions if e.exception_type == "DUPLICATE_INVOICE"), None)
    assert dup_exc is not None, "DUPLICATE_INVOICE exception not flagged on duplicate submission"
    assert dup_exc.variance_amount == 1000.0
    assert dup_exc.severity in ("High", "CRITICAL")


def test_human_approval_updates_exception_and_audit_log_and_invoice(db):
    """
    Edge Case 4: Human Approval Lifecycle Verification.
    Verifying that human approval properly updates both the exception status,
    the linked invoice status, and appends to the immutable audit log.
    """
    po_no = "PO-HUMAN-01"
    inv_no = "INV-HUMAN-01"

    # Seed invoice on hold
    inv = Invoice(
        invoice_number=inv_no,
        po_number=po_no,
        vendor_id="VEND-HUMAN",
        vendor_name="Human Workflow Testing Inc",
        sku="SKU-TEST-01",
        quantity_billed=100.0,
        unit_price=35.0,
        total_amount=3500.0,
        payment_status="ON_HOLD",
        invoice_date=datetime(2026, 3, 10, 10, 0, 0),
    )
    db.add(inv)

    # Seed exception in AI_ANALYZED state
    exc = ExceptionRecord(
        exception_code="EXC-HUMAN-TEST-01",
        invoice_number=inv_no,
        po_number=po_no,
        vendor_id="VEND-HUMAN",
        exception_type="PRICE_VARIANCE",
        severity="High",
        variance_amount=500.0,
        status="AI_ANALYZED",
    )
    db.add(exc)
    db.commit()

    # 1. Verify before states
    assert exc.status == "AI_ANALYZED"
    assert inv.payment_status == "ON_HOLD"
    initial_audit_count = db.query(AuditLog).count()

    # 2. Execute Human Approval: OVERRIDE_AND_PAY
    reviewer = "marcus.vance@enterprise.com"
    justification = "Reviewed and authorized $500 premium due to specialized rush delivery for critical project milestone."

    resolution = resolve_exception_workflow(
        db=db,
        exception_id_or_code="EXC-HUMAN-TEST-01",
        action_taken="OVERRIDE_AND_PAY",
        user_id=reviewer,
        notes=justification,
    )

    # 3. Assert Resolution Output
    assert resolution["status"] == "success"
    assert resolution["before_state"] == "AI_ANALYZED"
    assert resolution["after_state"] == "APPROVED_FOR_PAYMENT"
    assert resolution["user_id"] == reviewer
    assert resolution["invoice_payment_status"] == "APPROVED"

    # 4. Verify Exception Status in DB
    refreshed_exc = db.query(ExceptionRecord).filter(ExceptionRecord.exception_code == "EXC-HUMAN-TEST-01").first()
    assert refreshed_exc.status == "APPROVED_FOR_PAYMENT"
    assert refreshed_exc.resolved_at is not None

    # 5. Verify Invoice Payment Status in DB
    refreshed_inv = db.query(Invoice).filter(Invoice.invoice_number == inv_no).first()
    assert refreshed_inv.payment_status == "APPROVED"

    # 6. Verify Immutable Audit Log
    trail = get_audit_trail_for_entity(db, "EXC-HUMAN-TEST-01")
    assert len(trail) >= 1
    latest_log = trail[-1]

    assert latest_log["performed_by"] == reviewer
    assert latest_log["action"] == "STATE_TRANSITION"
    assert latest_log["before_state"] == "AI_ANALYZED"
    assert latest_log["after_state"] == "APPROVED_FOR_PAYMENT"
    assert latest_log["details"]["action_taken"] == "OVERRIDE_AND_PAY"
    assert latest_log["details"]["notes"] == justification
    assert latest_log["details"]["actor"] == reviewer

    # Total audit logs grew
    assert db.query(AuditLog).count() > initial_audit_count
