import io
import pytest
from fastapi.testclient import TestClient
from database import SessionLocal, init_db
from models import PurchaseOrder, GoodsReceipt, Invoice, AuditLog
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


def test_health_check(client):
    """Test health check endpoint."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["database"] == "healthy"


# ============================================================================
# WEBHOOK TESTS
# ============================================================================

def test_ingest_po_webhook_success(client, db):
    """Test real-time PO ingestion with field normalization."""
    payload = {
        "po_number": "  po-test-webhook-01  ",  # Trailing whitespace & lowercase
        "vendor_id": " vend-9001 ",
        "vendor_name": "Acme Industrial Tools",
        "sku": "sku-mach-100",
        "description": "High Precision CNC Drill Bit",
        "quantity": 100,
        "unit_price": 45.50,
        "currency": " usd ",  # Lowercase and whitespace
        "order_date": "2026-03-01T10:00:00Z",
    }
    res = client.post("/ingest/po", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "success"
    assert data["entity_id"] == "PO-TEST-WEBHOOK-01"  # Normalized uppercase & stripped

    # Verify normalization in response
    assert data["data"]["currency"] == "USD"
    assert data["data"]["total_amount"] == 4550.0  # 100 * 45.50 auto-calculated

    # Verify DB record
    po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-TEST-WEBHOOK-01").first()
    assert po is not None
    assert po.vendor_id == "VEND-9001"
    assert po.total_amount == 4550.0

    # Verify Audit log
    audit = db.query(AuditLog).filter(AuditLog.entity_id == "PO-TEST-WEBHOOK-01").first()
    assert audit is not None
    assert audit.action == "WEBHOOK_INGEST"


def test_ingest_po_duplicate_conflict(client):
    """Test duplicate PO number returns 409 Conflict."""
    payload = {
        "po_number": "PO-TEST-WEBHOOK-01",
        "vendor_id": "VEND-9001",
        "vendor_name": "Acme Industrial Tools",
        "sku": "SKU-MACH-100",
        "quantity": 50,
        "unit_price": 45.50,
        "order_date": "2026-03-01T10:00:00Z",
    }
    res = client.post("/ingest/po", json=payload)
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


def test_ingest_po_validation_errors(client):
    """Test Pydantic validation catches malformed payloads."""
    # Negative quantity
    res = client.post("/ingest/po", json={
        "po_number": "PO-BAD-01",
        "vendor_id": "VEND-1",
        "vendor_name": "Test",
        "sku": "SKU-1",
        "quantity": -5,
        "unit_price": 10.0,
        "order_date": "2026-03-01",
    })
    assert res.status_code == 422

    # Invalid currency (not 3-letter ISO)
    res = client.post("/ingest/po", json={
        "po_number": "PO-BAD-02",
        "vendor_id": "VEND-1",
        "vendor_name": "Test",
        "sku": "SKU-1",
        "quantity": 10,
        "unit_price": 10.0,
        "currency": "DOLLARS",
        "order_date": "2026-03-01",
    })
    assert res.status_code == 422

    # Empty po_number
    res = client.post("/ingest/po", json={
        "po_number": "   ",
        "vendor_id": "VEND-1",
        "vendor_name": "Test",
        "sku": "SKU-1",
        "quantity": 10,
        "unit_price": 10.0,
        "order_date": "2026-03-01",
    })
    assert res.status_code == 422


def test_ingest_receipt_webhook_success(client, db):
    """Test Goods Receipt webhook ingestion."""
    payload = {
        "gr_number": "gr-dock-9001",
        "po_number": "po-test-webhook-01",
        "vendor_id": "vend-9001",
        "sku": "sku-mach-100",
        "quantity_received": 100.0,
        "received_date": "2026-03-04 14:30:00",
        "received_by": "Receiving Dock 4",
        "condition_status": "accepted",
    }
    res = client.post("/ingest/receipt", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["entity_id"] == "GR-DOCK-9001"

    gr = db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == "GR-DOCK-9001").first()
    assert gr is not None
    assert gr.condition_status == "ACCEPTED"
    assert gr.quantity_received == 100.0


def test_ingest_receipt_duplicate_conflict(client):
    """Test duplicate GR number returns 409 Conflict."""
    payload = {
        "gr_number": "GR-DOCK-9001",
        "po_number": "PO-TEST-WEBHOOK-01",
        "vendor_id": "VEND-9001",
        "sku": "SKU-MACH-100",
        "quantity_received": 100.0,
        "received_date": "2026-03-04 14:30:00",
    }
    res = client.post("/ingest/receipt", json=payload)
    assert res.status_code == 409


def test_ingest_invoice_webhook_success(client, db):
    """Test Invoice webhook ingestion."""
    payload = {
        "invoice_number": "inv-vendor-8812",
        "po_number": "po-test-webhook-01",
        "vendor_id": "vend-9001",
        "vendor_name": "Acme Industrial Tools",
        "sku": "sku-mach-100",
        "quantity_billed": 100.0,
        "unit_price": 45.50,
        "invoice_date": "2026-03-05",
        "payment_status": "pending_match",
    }
    res = client.post("/ingest/invoice", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["entity_id"] == "INV-VENDOR-8812"
    assert data["data"]["total_amount"] == 4550.0
    assert data["data"]["payment_status"] == "PENDING_MATCH"


# ============================================================================
# BULK CSV INGESTION TESTS
# ============================================================================

def test_ingest_csv_po_bulk(client, db):
    """Test bulk PO CSV upload with aliased headers."""
    csv_content = """PO Number,Supplier ID,Supplier Name,Item SKU,Item Description,Qty,Price,Currency,PO Date
PO-CSV-001, VEND-5001, Apex Supply Co, SKU-VALV-10, 2-Inch Brass Ball Valve, 25, 40.00, USD, 2026-02-01
PO-CSV-002, VEND-5002, Precision Bolts, SKU-BOLT-50, Stainless Steel Hex Bolts, 500, 1.25, USD, 2026-02-02
"""
    files = {"file": ("pos_export.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res = client.post("/ingest/csv/po", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total_rows"] == 2
    assert data["ingested_count"] == 2
    assert data["failed_count"] == 0

    po1 = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-CSV-001").first()
    assert po1 is not None
    assert po1.quantity == 25.0
    assert po1.total_amount == 1000.0


def test_ingest_csv_receipt_bulk(client, db):
    """Test bulk Goods Receipt CSV upload."""
    csv_content = """Receipt No,PO Number,Vendor ID,Item SKU,Qty Received,Date Received,Inspector,Condition
GR-CSV-001, PO-CSV-001, VEND-5001, SKU-VALV-10, 25, 2026-02-05 09:30:00, John Doe, accepted
GR-CSV-002, PO-CSV-002, VEND-5002, SKU-BOLT-50, 500, 2026-02-06 11:00:00, Jane Smith, accepted
"""
    files = {"file": ("receipts_export.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res = client.post("/ingest/csv/receipt", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["ingested_count"] == 2

    gr1 = db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == "GR-CSV-001").first()
    assert gr1 is not None
    assert gr1.quantity_received == 25.0


def test_ingest_csv_invoice_bulk(client, db):
    """Test bulk Invoice CSV upload."""
    csv_content = """Invoice No,PO Number,Vendor ID,Vendor Name,Item SKU,Billed Qty,Price,Inv Date
INV-CSV-001, PO-CSV-001, VEND-5001, Apex Supply Co, SKU-VALV-10, 25, 40.00, 2026-02-07
INV-CSV-002, PO-CSV-002, VEND-5002, Precision Bolts, SKU-BOLT-50, 500, 1.25, 2026-02-08
"""
    files = {"file": ("invoices_export.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res = client.post("/ingest/csv/invoice", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["ingested_count"] == 2

    inv1 = db.query(Invoice).filter(Invoice.invoice_number == "INV-CSV-001").first()
    assert inv1 is not None
    assert inv1.total_amount == 1000.0


def test_ingest_csv_partial_failure(client):
    """Test CSV containing malformed rows produces partial success with row-level error reports."""
    csv_content = """PO Number,Vendor ID,Vendor Name,SKU,Quantity,Unit Price,Order Date
PO-PARTIAL-01, VEND-1, Supplier One, SKU-A, 10, 20.00, 2026-03-01
PO-PARTIAL-02, VEND-2, Supplier Two, SKU-B, -5, 20.00, 2026-03-01
PO-PARTIAL-03, VEND-3, Supplier Three, SKU-C, 15, 30.00, 2026-03-01
"""
    files = {"file": ("partial.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res = client.post("/ingest/csv/po", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "partial_success"
    assert data["total_rows"] == 3
    assert data["ingested_count"] == 2
    assert data["failed_count"] == 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["row_number"] == 2


def test_ingest_csv_invalid_entity_type(client):
    """Test unsupported entity type returns 400 Bad Request."""
    csv_content = "Col1,Col2\nVal1,Val2"
    files = {"file": ("test.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    res = client.post("/ingest/csv/unsupported_entity", files=files)
    assert res.status_code == 400
    assert "Unsupported entity type" in res.json()["detail"]
