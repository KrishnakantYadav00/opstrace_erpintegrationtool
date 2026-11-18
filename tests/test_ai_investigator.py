import json
import pytest
from fastapi.testclient import TestClient
from database import SessionLocal, init_db
from models import ExceptionRecord, AuditLog
from investigator import (
    assemble_investigation_context,
    build_investigation_prompt,
    investigate_exception,
    investigate_batch,
)
from schemas import InvestigationReport
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


def test_assemble_investigation_context(db):
    """Test context assembly bundles PO, GR, Invoice, and rule details."""
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.exception_code == "EXC-2026-0001").first()
    assert exc is not None

    context = assemble_investigation_context(exc, db)
    assert context["exception_code"] == "EXC-2026-0001"
    assert context["exception_type"] == "QTY_MISMATCH_3WAY"

    docs = context["linked_documents"]
    assert docs["purchase_order"] is not None
    assert docs["goods_receipt"] is not None
    assert docs["invoice"] is not None
    assert docs["invoice"]["quantity_billed"] == 100.0
    assert docs["goods_receipt"]["quantity_received"] == 80.0

    prompt = build_investigation_prompt(context)
    assert "EXC-2026-0001" in prompt
    assert "root_cause_summary" in prompt
    assert "recommended_actions" in prompt
    assert "draft_communication" in prompt


def test_investigate_quantity_mismatch(db):
    """
    Test AI investigation of 3-way match quantity mismatch:
    PO = 100, Receipt = 80, Invoice = 100.
    """
    report = investigate_exception(db, "EXC-2026-0001", use_live_llm=False)
    assert isinstance(report, InvestigationReport)
    assert report.exception_code == "EXC-2026-0001"

    # 1. Plain-English Root Cause Summary
    assert "100" in report.root_cause_summary
    assert "80" in report.root_cause_summary
    assert "overbilling" in report.root_cause_summary.lower()

    # 2. Recommended Actions: 2–3 concrete next steps
    assert 2 <= len(report.recommended_actions) <= 3
    actions_text = " ".join(report.recommended_actions).lower()
    assert "credit memo" in actions_text
    assert "warehouse" in actions_text or "hold" in actions_text

    # 3. Ready-to-send draft email
    assert "Subject:" in report.draft_communication
    assert "Dear" in report.draft_communication
    assert "80" in report.draft_communication
    assert "100" in report.draft_communication


def test_investigate_price_variance(db):
    """
    Test AI investigation of price variance:
    PO unit price = $10.00, Invoice unit price = $12.00.
    """
    report = investigate_exception(db, "EXC-2026-0009", use_live_llm=False)
    assert isinstance(report, InvestigationReport)
    assert report.exception_code == "EXC-2026-0009"

    # Root Cause Summary
    assert "12.00" in report.root_cause_summary
    assert "10.00" in report.root_cause_summary
    assert "exceeds" in report.root_cause_summary.lower()

    # Recommended Actions
    assert 2 <= len(report.recommended_actions) <= 3
    actions_text = " ".join(report.recommended_actions).lower()
    assert "price" in actions_text
    assert "invoice" in actions_text or "procurement" in actions_text

    # Draft Communication
    assert "Subject:" in report.draft_communication
    assert "12.00" in report.draft_communication
    assert "10.00" in report.draft_communication


def test_investigate_duplicate_invoice(db):
    """Test AI investigation of duplicate invoice."""
    dup_exc = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "DUPLICATE_INVOICE")
        .first()
    )
    assert dup_exc is not None

    report = investigate_exception(db, dup_exc.exception_code, use_live_llm=False)
    assert "duplicate" in report.root_cause_summary.lower()
    assert len(report.recommended_actions) >= 2
    assert "duplicate" in report.draft_communication.lower()


def test_investigate_ghost_receipt(db):
    """Test AI investigation of ghost receipt (> 7 days with no GR)."""
    ghost_exc = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "GHOST_RECEIPT_MISSING_GR")
        .first()
    )
    assert ghost_exc is not None

    report = investigate_exception(db, ghost_exc.exception_code, use_live_llm=False)
    assert "goods receipt" in report.root_cause_summary.lower()
    assert len(report.recommended_actions) >= 2
    assert "proof of delivery" in " ".join(report.recommended_actions).lower() or "dock" in " ".join(report.recommended_actions).lower()


def test_investigation_persisted_to_db_and_audit(db):
    """Verify investigation is cached in exceptions table and logged in audit_logs."""
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.exception_code == "EXC-2026-0001").first()
    assert exc.ai_investigation is not None

    cached = json.loads(exc.ai_investigation)
    assert "root_cause_summary" in cached
    assert "recommended_actions" in cached
    assert "draft_communication" in cached

    audit = (
        db.query(AuditLog)
        .filter(
            AuditLog.entity_id == "EXC-2026-0001",
            AuditLog.action == "AI_INVESTIGATION_COMPLETED",
        )
        .first()
    )
    assert audit is not None


# ============================================================================
# FASTAPI ENDPOINT TESTS
# ============================================================================

def test_api_investigate_single_endpoint(client):
    """Test POST /investigate/{exception_code} API endpoint."""
    res = client.post("/investigate/EXC-2026-0001")
    assert res.status_code == 200
    data = res.json()
    assert data["exception_code"] == "EXC-2026-0001"
    assert "root_cause_summary" in data
    assert len(data["recommended_actions"]) >= 2
    assert "draft_communication" in data


def test_api_get_investigation_endpoint(client):
    """Test GET /investigate/{exception_code} API endpoint."""
    res = client.get("/investigate/EXC-2026-0001")
    assert res.status_code == 200
    data = res.json()
    assert data["exception_code"] == "EXC-2026-0001"
    assert "root_cause_summary" in data


def test_api_batch_investigate_endpoint(client):
    """Test POST /investigate/batch API endpoint."""
    res = client.post("/investigate/batch?limit=3")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total_investigated"] == 3
    assert len(data["reports"]) == 3
