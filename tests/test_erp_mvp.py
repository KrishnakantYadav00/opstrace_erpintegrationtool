import json
import pytest
from datetime import datetime, timedelta
from sqlalchemy import inspect
from database import engine, SessionLocal, init_db
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog
from seed_data import generate_seed_data


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Ensure database is seeded for test session."""
    generate_seed_data(total_flows=75, seed=42)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_schema_tables_exist():
    """Verify all 5 required tables exist in the schema."""
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    required_tables = [
        "purchase_orders",
        "goods_receipts",
        "invoices",
        "exceptions",
        "audit_logs",
    ]
    for table in required_tables:
        assert table in table_names, f"Table '{table}' missing from database schema."


def test_table_columns_and_keys():
    """Verify critical columns and primary keys exist on all core tables."""
    inspector = inspect(engine)

    # 1. purchase_orders
    po_cols = {col["name"]: col for col in inspector.get_columns("purchase_orders")}
    for field in ["id", "po_number", "vendor_id", "vendor_name", "sku", "quantity", "unit_price", "total_amount", "status"]:
        assert field in po_cols, f"Column '{field}' missing from purchase_orders"

    # 2. goods_receipts
    gr_cols = {col["name"]: col for col in inspector.get_columns("goods_receipts")}
    for field in ["id", "gr_number", "po_number", "vendor_id", "sku", "quantity_received", "condition_status"]:
        assert field in gr_cols, f"Column '{field}' missing from goods_receipts"

    # 3. invoices
    inv_cols = {col["name"]: col for col in inspector.get_columns("invoices")}
    for field in ["id", "invoice_number", "po_number", "vendor_id", "sku", "quantity_billed", "unit_price", "total_amount"]:
        assert field in inv_cols, f"Column '{field}' missing from invoices"

    # 4. exceptions
    exc_cols = {col["name"]: col for col in inspector.get_columns("exceptions")}
    for field in ["id", "exception_code", "po_number", "invoice_number", "exception_type", "severity", "discrepancy_details"]:
        assert field in exc_cols, f"Column '{field}' missing from exceptions"

    # 5. audit_logs
    audit_cols = {col["name"]: col for col in inspector.get_columns("audit_logs")}
    for field in ["id", "entity_type", "entity_id", "action", "performed_by", "timestamp"]:
        assert field in audit_cols, f"Column '{field}' missing from audit_logs"


def test_dataset_generation_volume(db):
    """Verify total procurement flows generate 50-100 realistic records."""
    po_count = db.query(PurchaseOrder).count()
    gr_count = db.query(GoodsReceipt).count()
    inv_count = db.query(Invoice).count()
    exc_count = db.query(ExceptionRecord).count()
    audit_count = db.query(AuditLog).count()

    assert 50 <= po_count <= 100, f"Purchase orders count {po_count} outside 50-100 target"
    assert gr_count > 0, "No goods receipts generated"
    assert inv_count >= po_count, "Invoices should be at least equal to or greater than POs"
    assert exc_count > 0, "Exceptions should be logged"
    assert audit_count > 0, "Audit logs should be logged"


def test_failure_mode_3way_match_quantity_discrepancy(db):
    """
    Test Failure Mode: 3-Way Match Discrepancy.
    Prompt requirement: PO = 100, Receipt = 80, Invoice = 100.
    """
    exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "QTY_MISMATCH_3WAY")
        .all()
    )
    assert len(exceptions) > 0, "No 3-way match quantity exceptions found"

    # Check exact match case: PO = 100, Receipt = 80, Invoice = 100
    exact_case_found = False
    for exc in exceptions:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exc.po_number).first()
        gr = db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == exc.gr_number).first()
        inv = db.query(Invoice).filter(Invoice.invoice_number == exc.invoice_number).first()

        assert po is not None and gr is not None and inv is not None
        assert inv.quantity_billed > gr.quantity_received, "Invoice billed quantity must exceed received quantity"

        if po.quantity == 100.0 and gr.quantity_received == 80.0 and inv.quantity_billed == 100.0:
            exact_case_found = True
            details = json.loads(exc.discrepancy_details)
            assert details["quantity_variance"] == 20.0
            assert details["overbilling_usd"] == round(20.0 * po.unit_price, 2)
            break

    assert exact_case_found, "Exact PO=100, GR=80, INV=100 test case was not found"


def test_failure_mode_price_variance(db):
    """
    Test Failure Mode: Price Variance.
    Prompt requirement: PO unit price = $10, Invoice unit price = $12.
    """
    exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "PRICE_VARIANCE")
        .all()
    )
    assert len(exceptions) > 0, "No price variance exceptions found"

    exact_price_variance_found = False
    for exc in exceptions:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exc.po_number).first()
        inv = db.query(Invoice).filter(Invoice.invoice_number == exc.invoice_number).first()

        assert po is not None and inv is not None
        assert inv.unit_price > po.unit_price, "Invoice unit price must exceed PO unit price"

        if po.unit_price == 10.0 and inv.unit_price == 12.0:
            exact_price_variance_found = True
            details = json.loads(exc.discrepancy_details)
            assert details["unit_price_variance"] == 2.0
            assert details["variance_percentage"] == 20.0
            break

    assert exact_price_variance_found, "Exact PO unit price=$10, Invoice unit price=$12 case was not found"


def test_failure_mode_duplicate_invoice(db):
    """
    Test Failure Mode: Duplicate Invoice.
    Prompt requirement: Same invoice_number OR identical amount/vendor within 48 hours.
    """
    exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "DUPLICATE_INVOICE")
        .all()
    )
    assert len(exceptions) > 0, "No duplicate invoice exceptions found"

    exact_number_dup_found = False
    amount_vendor_dup_found = False

    for exc in exceptions:
        details = json.loads(exc.discrepancy_details)
        assert details["time_difference_hours"] <= 48, "Duplicate submission must occur within 48 hours"

        if details["duplicate_type"] == "EXACT_INVOICE_NUMBER":
            exact_number_dup_found = True
            assert details["original_invoice"] == details["duplicate_invoice"]
            # Verify database actually contains multiple rows with that invoice_number
            dup_rows = db.query(Invoice).filter(Invoice.invoice_number == details["duplicate_invoice"]).all()
            assert len(dup_rows) >= 2

        elif details["duplicate_type"] == "IDENTICAL_AMOUNT_AND_VENDOR_WITHIN_48H":
            amount_vendor_dup_found = True
            orig_inv = db.query(Invoice).filter(Invoice.invoice_number == details["original_invoice"]).first()
            dup_inv = db.query(Invoice).filter(Invoice.invoice_number == details["duplicate_invoice"]).first()
            assert orig_inv is not None and dup_inv is not None
            assert orig_inv.vendor_id == dup_inv.vendor_id
            assert orig_inv.total_amount == dup_inv.total_amount

    assert exact_number_dup_found, "Subtype 'Same invoice_number' duplicate not found"
    assert amount_vendor_dup_found, "Subtype 'Identical amount/vendor within 48 hours' duplicate not found"


def test_failure_mode_ghost_receipt_and_missing_po(db):
    """
    Test Failure Mode: Ghost Receipt / Missing PO.
    Prompt requirement: Invoice arrives with no corresponding Goods Receipt after 7 days (or missing PO).
    """
    ghost_gr_exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "GHOST_RECEIPT_MISSING_GR")
        .all()
    )
    assert len(ghost_gr_exceptions) > 0, "No Ghost Receipt exceptions found"

    for exc in ghost_gr_exceptions:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exc.po_number).first()
        inv = db.query(Invoice).filter(Invoice.invoice_number == exc.invoice_number).first()
        assert po is not None and inv is not None

        # Verify no goods receipt exists for this PO
        gr_count = db.query(GoodsReceipt).filter(GoodsReceipt.po_number == exc.po_number).count()
        assert gr_count == 0, f"Expected 0 goods receipts for Ghost Receipt PO {exc.po_number}, found {gr_count}"

        # Verify invoice arrived > 7 days after PO
        days_diff = (inv.invoice_date - po.order_date).days
        assert days_diff >= 7, f"Invoice arrived after {days_diff} days, expected >= 7 days"

    # Test Missing PO
    missing_po_exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "MISSING_PO")
        .all()
    )
    assert len(missing_po_exceptions) > 0, "No Missing PO exceptions found"
    for exc in missing_po_exceptions:
        # Verify referenced PO does not exist in purchase_orders table
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exc.po_number).first()
        assert po is None, f"Expected PO {exc.po_number} to be missing from database, but found record"


def test_clean_procurement_flows(db):
    """Verify normal clean flows have 100% matched PO, GR, and Invoice."""
    clean_pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status == "RECEIVED")
        .all()
    )
    assert len(clean_pos) > 0

    verified_clean_flows = 0
    for po in clean_pos:
        # Skip if an exception is flagged on this PO
        has_exception = db.query(ExceptionRecord).filter(ExceptionRecord.po_number == po.po_number).first()
        if has_exception:
            continue

        grs = po.goods_receipts
        invs = po.invoices
        if grs and invs and len(grs) == 1 and len(invs) == 1:
            gr = grs[0]
            inv = invs[0]
            assert po.quantity == gr.quantity_received == inv.quantity_billed
            assert po.unit_price == inv.unit_price
            assert po.vendor_id == gr.vendor_id == inv.vendor_id
            assert po.sku == gr.sku == inv.sku
            verified_clean_flows += 1

    assert verified_clean_flows >= 30, f"Expected at least 30 verified clean flows, found {verified_clean_flows}"


def test_orm_cross_table_relationships(db):
    """Test ORM convenience links po.goods_receipts, po.invoices, po.exceptions."""
    sample_po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == "PO-2026-0001").first()
    assert sample_po is not None
    assert len(sample_po.goods_receipts) >= 1
    assert len(sample_po.invoices) >= 1

    # Backref verification
    sample_gr = sample_po.goods_receipts[0]
    assert sample_gr.purchase_order is not None
    assert sample_gr.purchase_order.po_number == sample_po.po_number
