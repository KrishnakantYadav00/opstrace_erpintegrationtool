import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from database import SessionLocal, init_db
from models import ExceptionRecord, Invoice, AuditLog
from workflow import (
    ExceptionState,
    transition_exception_state,
    resolve_exception_workflow,
    get_audit_trail_for_entity,
)
from scheduler import (
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    run_scheduled_reconciliation_sweep,
)
from seed_data import generate_seed_data
from app import app


@pytest.fixture(scope="module")
def setup_data():
    generate_seed_data(total_flows=75, seed=42)
    yield


@pytest.fixture(scope="module")
def client(setup_data):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_state_machine_detected_to_ai_analyzed(db):
    """Test state transition from DETECTED to AI_ANALYZED with AI_AGENT actor."""
    exc = ExceptionRecord(
        exception_code="EXC-WORKFLOW-01",
        exception_type="QUANTITY_MISMATCH",
        severity="High",
        variance_amount=500.0,
        status=ExceptionState.DETECTED,
    )
    db.add(exc)
    db.commit()

    before, after, audit = transition_exception_state(
        db=db,
        exception=exc,
        target_state=ExceptionState.AI_ANALYZED,
        actor="AI_AGENT",
        action_taken="AI_ROOT_CAUSE_ANALYSIS",
        notes="Root cause identified: warehouse shortage of 20 units",
    )
    db.commit()

    assert before == ExceptionState.DETECTED
    assert after == ExceptionState.AI_ANALYZED
    assert exc.status == ExceptionState.AI_ANALYZED

    # Verify immutable audit log
    assert audit.entity_id == "EXC-WORKFLOW-01"
    assert audit.performed_by == "AI_AGENT"
    assert audit.before_state == "DETECTED"
    assert audit.after_state == "AI_ANALYZED"


def test_human_override_and_pay_resolution(db):
    """
    Test human reviewer approving payment via OVERRIDE_AND_PAY.
    Ensures state transitions to APPROVED_FOR_PAYMENT and invoice becomes APPROVED.
    """
    # Create invoice and linked exception
    inv = Invoice(
        invoice_number="INV-OVERRIDE-01",
        po_number="PO-OVERRIDE-01",
        vendor_id="VEND-1001",
        vendor_name="Apex Supplies",
        sku="SKU-STEEL-01",
        quantity_billed=50,
        unit_price=12.0,
        total_amount=600.0,
        payment_status="ON_HOLD",
        invoice_date=datetime.now(timezone.utc),
    )
    db.add(inv)

    exc = ExceptionRecord(
        exception_code="EXC-OVERRIDE-01",
        invoice_number="INV-OVERRIDE-01",
        po_number="PO-OVERRIDE-01",
        vendor_id="VEND-1001",
        exception_type="PRICE_VARIANCE",
        severity="Medium",
        variance_amount=100.0,
        status=ExceptionState.AI_ANALYZED,
    )
    db.add(exc)
    db.commit()

    result = resolve_exception_workflow(
        db=db,
        exception_id_or_code="EXC-OVERRIDE-01",
        action_taken="OVERRIDE_AND_PAY",
        user_id="cfo.director@enterprise.com",
        notes="Approved expedited pricing variance per emergency project requirements.",
    )

    assert result["status"] == "success"
    assert result["before_state"] == "AI_ANALYZED"
    assert result["after_state"] == "APPROVED_FOR_PAYMENT"
    assert result["invoice_payment_status"] == "APPROVED"

    # Verify in DB
    refreshed_exc = db.query(ExceptionRecord).filter(ExceptionRecord.exception_code == "EXC-OVERRIDE-01").first()
    assert refreshed_exc.status == "APPROVED_FOR_PAYMENT"
    assert refreshed_exc.resolved_at is not None

    refreshed_inv = db.query(Invoice).filter(Invoice.invoice_number == "INV-OVERRIDE-01").first()
    assert refreshed_inv.payment_status == "APPROVED"

    # Verify audit trail contains state transition
    trail = get_audit_trail_for_entity(db, "EXC-OVERRIDE-01")
    latest = trail[-1]
    assert latest["performed_by"] == "cfo.director@enterprise.com"
    assert latest["before_state"] == "AI_ANALYZED"
    assert latest["after_state"] == "APPROVED_FOR_PAYMENT"


def test_human_request_credit_memo_resolution(db):
    """
    Test human reviewer requesting credit memo.
    Ensures state transitions to CREDIT_MEMO_REQUESTED and invoice remains ON_HOLD.
    """
    inv = Invoice(
        invoice_number="INV-CM-01",
        po_number="PO-CM-01",
        vendor_id="VEND-1002",
        vendor_name="Titan Corp",
        sku="SKU-HYD-500",
        quantity_billed=100,
        unit_price=250.0,
        total_amount=25000.0,
        payment_status="PENDING_MATCH",
        invoice_date=datetime.now(timezone.utc),
    )
    db.add(inv)

    exc = ExceptionRecord(
        exception_code="EXC-CM-01",
        invoice_number="INV-CM-01",
        po_number="PO-CM-01",
        vendor_id="VEND-1002",
        exception_type="QTY_MISMATCH_3WAY",
        severity="High",
        variance_amount=5000.0,
        status=ExceptionState.DETECTED,
    )
    db.add(exc)
    db.commit()

    result = resolve_exception_workflow(
        db=db,
        exception_id_or_code="EXC-CM-01",
        action_taken="REQUEST_CREDIT_MEMO",
        user_id="ap.analyst.alex@enterprise.com",
        notes="Vendor acknowledged 20 unit shortage, formal credit memo requested.",
    )

    assert result["status"] == "success"
    assert result["after_state"] == "CREDIT_MEMO_REQUESTED"
    assert result["invoice_payment_status"] == "ON_HOLD"


def test_terminal_state_cannot_be_re_resolved(db):
    """Verify that exceptions in terminal states cannot be re-resolved."""
    exc = ExceptionRecord(
        exception_code="EXC-TERMINAL-01",
        exception_type="PRICE_VARIANCE",
        severity="Low",
        status=ExceptionState.APPROVED_FOR_PAYMENT,
    )
    db.add(exc)
    db.commit()

    with pytest.raises(ValueError, match="Cannot transition exception"):
        resolve_exception_workflow(
            db=db,
            exception_id_or_code="EXC-TERMINAL-01",
            action_taken="OVERRIDE_AND_PAY",
            user_id="bad.actor@enterprise.com",
            notes="Attempting duplicate authorization",
        )


def test_api_resolve_endpoint(client):
    """Test POST /exceptions/{id}/resolve FastAPI endpoint."""
    payload = {
        "action_taken": "ESCALATE",
        "user_id": "sarah.chen@enterprise.com",
        "notes": "Escalating unknown PO reference to senior procurement manager.",
    }
    res = client.post("/exceptions/EXC-2026-0002/resolve", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["exception_code"] == "EXC-2026-0002"
    assert data["action_taken"] == "ESCALATE"
    assert data["after_state"] == "ESCALATED"
    assert data["user_id"] == "sarah.chen@enterprise.com"


def test_api_audit_trail_endpoint(client):
    """Test GET /exceptions/{id}/audit returns chronologically ordered immutable trail."""
    res = client.get("/exceptions/EXC-2026-0002/audit")
    assert res.status_code == 200
    trail = res.json()
    assert len(trail) >= 1
    # Check that the resolution state transition is present
    state_transitions = [e for e in trail if e["action"] == "STATE_TRANSITION"]
    assert len(state_transitions) >= 1
    assert state_transitions[-1]["performed_by"] == "sarah.chen@enterprise.com"
    assert state_transitions[-1]["after_state"] == "ESCALATED"


def test_scheduler_operational_lifecycle(client):
    """Test APScheduler background sweep functions and endpoints."""
    # 1. Sweep worker function directly
    sweep_summary = run_scheduled_reconciliation_sweep()
    assert "total_invoices_scanned" in sweep_summary
    assert "exceptions_generated" in sweep_summary

    # 2. Status API endpoint
    status_res = client.get("/scheduler/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert "scheduler_running" in status_data
    assert status_data["interval_minutes"] == 5

    # 3. Manual trigger API endpoint
    trigger_res = client.post("/scheduler/sweep")
    assert trigger_res.status_code == 200
    trigger_data = trigger_res.json()
    assert trigger_data["status"] == "success"
    assert "summary" in trigger_data
