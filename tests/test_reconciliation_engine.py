import json
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from database import SessionLocal, init_db
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord
from engine import (
    evaluate_quantity_check,
    evaluate_price_check,
    evaluate_completeness_check,
    evaluate_sla_check,
    reconcile_invoice,
    reconcile_all,
)
from app import app


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ============================================================================
# PURE PYTHON UNIT TESTS FOR DETERMINISTIC RULES
# ============================================================================

def test_rule_quantity_check_discrepancy():
    """Test quantity discrepancy: PO = 100, Receipt = 80, Invoice = 100."""
    po = PurchaseOrder(po_number="PO-TEST-Q1", quantity=100.0, unit_price=25.0)
    receipt = GoodsReceipt(gr_number="GR-TEST-Q1", po_number="PO-TEST-Q1", quantity_received=80.0)
    invoice = Invoice(
        invoice_number="INV-TEST-Q1",
        po_number="PO-TEST-Q1",
        quantity_billed=100.0,
        unit_price=25.0,
        total_amount=2500.0,
    )

    result = evaluate_quantity_check(po, receipt, invoice)
    assert result is not None
    assert result.exception_type == "QUANTITY_MISMATCH"
    assert result.severity == "High"
    # Shortage is 20 units * $25.00 = $500.00
    assert result.variance_amount == 500.0
    assert result.details["invoice_quantity"] == 100.0
    assert result.details["receipt_quantity"] == 80.0


def test_rule_quantity_check_matching_clean():
    """Test clean quantity match returns None."""
    po = PurchaseOrder(po_number="PO-TEST-Q2", quantity=50.0, unit_price=10.0)
    receipt = GoodsReceipt(gr_number="GR-TEST-Q2", po_number="PO-TEST-Q2", quantity_received=50.0)
    invoice = Invoice(
        invoice_number="INV-TEST-Q2",
        po_number="PO-TEST-Q2",
        quantity_billed=50.0,
        unit_price=10.0,
        total_amount=500.0,
    )

    result = evaluate_quantity_check(po, receipt, invoice)
    assert result is None


def test_rule_price_check_variance():
    """Test price check variance: PO unit price = $10, Invoice unit price = $12."""
    po = PurchaseOrder(po_number="PO-TEST-P1", quantity=50.0, unit_price=10.0)
    invoice = Invoice(
        invoice_number="INV-TEST-P1",
        po_number="PO-TEST-P1",
        quantity_billed=50.0,
        unit_price=12.0,
        total_amount=600.0,
    )

    result = evaluate_price_check(po, invoice)
    assert result is not None
    assert result.exception_type == "PRICE_VARIANCE"
    # Variance is ($12 - $10) * 50 = $100.00
    assert result.variance_amount == 100.0
    assert result.details["unit_price_variance"] == 2.0
    assert result.details["variance_percentage"] == 20.0


def test_rule_price_check_clean():
    """Test matching price or discount returns None."""
    po = PurchaseOrder(po_number="PO-TEST-P2", quantity=50.0, unit_price=10.0)
    invoice = Invoice(
        invoice_number="INV-TEST-P2",
        po_number="PO-TEST-P2",
        quantity_billed=50.0,
        unit_price=10.0,
        total_amount=500.0,
    )

    result = evaluate_price_check(po, invoice)
    assert result is None


def test_rule_completeness_missing_po():
    """Test completeness check when PO is None."""
    invoice = Invoice(
        invoice_number="INV-ORPHAN-01",
        po_number="PO-NONEXISTENT",
        quantity_billed=10.0,
        unit_price=50.0,
        total_amount=500.0,
    )

    result = evaluate_completeness_check(po=None, receipt=None, invoice=invoice)
    assert result is not None
    assert result.exception_type == "MISSING_PO"
    assert result.severity == "High"
    assert result.variance_amount == 500.0


def test_rule_completeness_missing_receipt():
    """Test completeness check when receipt is None."""
    po = PurchaseOrder(po_number="PO-TEST-C1", quantity=10.0, unit_price=50.0)
    invoice = Invoice(
        invoice_number="INV-TEST-C1",
        po_number="PO-TEST-C1",
        quantity_billed=10.0,
        unit_price=50.0,
        total_amount=500.0,
    )

    result = evaluate_completeness_check(po=po, receipt=None, invoice=invoice)
    assert result is not None
    assert result.exception_type == "MISSING_RECEIPT"
    assert result.severity == "High"
    assert result.variance_amount == 500.0


def test_rule_sla_check_breach():
    """Test SLA check for document pending approval older than 3 days."""
    now = datetime(2026, 3, 10, 12, 0, 0)
    five_days_ago = now - timedelta(days=5)

    invoice = Invoice(
        invoice_number="INV-SLA-01",
        po_number="PO-1",
        quantity_billed=10.0,
        unit_price=10.0,
        total_amount=100.0,
        payment_status="PENDING_APPROVAL",
        created_at=five_days_ago,
    )

    result = evaluate_sla_check(invoice, "Invoice", days_threshold=3, now_dt=now)
    assert result is not None
    assert result.exception_type == "SLA_BREACH"
    assert result.severity == "Medium"
    assert result.details["days_elapsed"] >= 5.0


def test_rule_sla_check_within_sla():
    """Test SLA check for document within 3-day SLA."""
    now = datetime(2026, 3, 10, 12, 0, 0)
    one_day_ago = now - timedelta(days=1)

    invoice = Invoice(
        invoice_number="INV-SLA-02",
        po_number="PO-1",
        quantity_billed=10.0,
        unit_price=10.0,
        total_amount=100.0,
        payment_status="PENDING_APPROVAL",
        created_at=one_day_ago,
    )

    result = evaluate_sla_check(invoice, "Invoice", days_threshold=3, now_dt=now)
    assert result is None


# ============================================================================
# INTEGRATION & SNAPSHOT GENERATION TESTS
# ============================================================================

def test_real_time_reconciliation_generates_snapshots(client, db):
    """
    Ingest PO, Receipt, and mismatched Invoice through REST API,
    and verify structured Exception record contains raw JSON snapshots.
    """
    po_no = "PO-ENGINE-SNAP-01"
    gr_no = "GR-ENGINE-SNAP-01"
    inv_no = "INV-ENGINE-SNAP-01"

    # Ingest PO: Qty = 100, Unit Price = $10.00
    client.post("/ingest/po", json={
        "po_number": po_no,
        "vendor_id": "VEND-SNAP",
        "vendor_name": "Snapshot Testing Co",
        "sku": "SKU-SNAP-01",
        "quantity": 100,
        "unit_price": 10.0,
        "order_date": "2026-03-01T10:00:00Z",
    })

    # Ingest Receipt: Qty = 80 (Shortage of 20)
    client.post("/ingest/receipt", json={
        "gr_number": gr_no,
        "po_number": po_no,
        "vendor_id": "VEND-SNAP",
        "sku": "SKU-SNAP-01",
        "quantity_received": 80,
        "received_date": "2026-03-03T10:00:00Z",
    })

    # Ingest Invoice: Qty = 100, Unit Price = $12.00 (Both Qty Mismatch and Price Variance!)
    client.post("/ingest/invoice", json={
        "invoice_number": inv_no,
        "po_number": po_no,
        "vendor_id": "VEND-SNAP",
        "vendor_name": "Snapshot Testing Co",
        "sku": "SKU-SNAP-01",
        "quantity_billed": 100,
        "unit_price": 12.0,
        "invoice_date": "2026-03-05T10:00:00Z",
    })

    # Query generated exceptions
    exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.invoice_number == inv_no)
        .all()
    )
    assert len(exceptions) >= 2, "Expected both Quantity Mismatch and Price Variance exceptions"

    for exc in exceptions:
        assert exc.raw_snapshots is not None
        snapshots = json.loads(exc.raw_snapshots)
        assert "purchase_order" in snapshots
        assert "goods_receipt" in snapshots
        assert "invoice" in snapshots

        # Verify linked PO snapshot details
        po_snap = snapshots["purchase_order"]
        assert po_snap["po_number"] == po_no
        assert po_snap["quantity"] == 100.0

        # Verify linked Receipt snapshot details
        gr_snap = snapshots["goods_receipt"]
        assert gr_snap["gr_number"] == gr_no
        assert gr_snap["quantity_received"] == 80.0

        # Verify linked Invoice snapshot details
        inv_snap = snapshots["invoice"]
        assert inv_snap["invoice_number"] == inv_no
        assert inv_snap["quantity_billed"] == 100.0


def test_batch_reconcile_api_endpoint(client):
    """Test POST /reconcile/batch executes batch scan."""
    res = client.post("/reconcile/batch")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "total_invoices_scanned" in data
    assert "exceptions_generated" in data


def test_list_exceptions_api_endpoint(client):
    """Test GET /exceptions returns exception list with snapshots."""
    res = client.get("/exceptions?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "exceptions" in data
    if data["exceptions"]:
        first_exc = data["exceptions"][0]
        assert "exception_code" in first_exc
        assert "exception_type" in first_exc
        assert "severity" in first_exc
        assert "variance_amount" in first_exc
        assert "raw_snapshots" in first_exc
