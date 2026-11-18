"""
Human-in-the-Loop Workflow State Machine & Immutable Audit Trail for OpsTrace ERP.
Enforces verifiable financial state transitions and accountability on human decisions.

Lifecycle:
DETECTED -> AI_ANALYZED -> APPROVED_FOR_PAYMENT / CREDIT_MEMO_REQUESTED / ESCALATED / DISMISSED
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.orm import Session

from models import ExceptionRecord, Invoice, AuditLog


class ExceptionState:
    DETECTED = "DETECTED"
    AI_ANALYZED = "AI_ANALYZED"
    APPROVED_FOR_PAYMENT = "APPROVED_FOR_PAYMENT"
    CREDIT_MEMO_REQUESTED = "CREDIT_MEMO_REQUESTED"
    ESCALATED = "ESCALATED"
    DISMISSED = "DISMISSED"
    RESOLVED = "RESOLVED"
    # Legacy alias support
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"


# Terminal states that cannot be re-resolved
TERMINAL_STATES = {
    ExceptionState.APPROVED_FOR_PAYMENT,
    ExceptionState.CREDIT_MEMO_REQUESTED,
    ExceptionState.DISMISSED,
    ExceptionState.RESOLVED,
}

ACTION_TO_STATE = {
    "OVERRIDE_AND_PAY": ExceptionState.APPROVED_FOR_PAYMENT,
    "REQUEST_CREDIT_MEMO": ExceptionState.CREDIT_MEMO_REQUESTED,
    "ESCALATE": ExceptionState.ESCALATED,
    "DISMISS": ExceptionState.DISMISSED,
    "RESOLVE": ExceptionState.RESOLVED,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def log_state_transition(
    db: Session,
    entity_type: str,
    entity_id: str,
    before_state: Optional[str],
    after_state: str,
    actor: str,
    action: str = "STATE_TRANSITION",
    action_taken: Optional[str] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """
    Append an immutable state transition record to audit_logs.
    """
    details_payload = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before_state": before_state,
        "after_state": after_state,
        "actor": actor,
        "action_taken": action_taken,
        "notes": notes,
        "timestamp": utc_now().isoformat(),
        "metadata": metadata or {},
    }

    audit_entry = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        performed_by=actor,
        before_state=before_state,
        after_state=after_state,
        timestamp=utc_now(),
        details=json.dumps(details_payload),
    )
    db.add(audit_entry)
    return audit_entry


def transition_exception_state(
    db: Session,
    exception: ExceptionRecord,
    target_state: str,
    actor: str,
    action_taken: Optional[str] = None,
    notes: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, AuditLog]:
    """
    Validate and execute a state transition for an ExceptionRecord.
    Writes an append-only row to audit_logs.
    """
    before_state = exception.status or ExceptionState.DETECTED

    # Check terminal state
    if before_state in TERMINAL_STATES and target_state != ExceptionState.ESCALATED:
        raise ValueError(
            f"Cannot transition exception '{exception.exception_code}' from terminal state '{before_state}' to '{target_state}'."
        )

    # Update exception model
    exception.status = target_state
    if target_state in TERMINAL_STATES or target_state == ExceptionState.ESCALATED:
        exception.resolved_at = utc_now()

    # Append to immutable audit log
    audit_entry = log_state_transition(
        db=db,
        entity_type="EXCEPTION",
        entity_id=exception.exception_code,
        before_state=before_state,
        after_state=target_state,
        actor=actor,
        action="STATE_TRANSITION",
        action_taken=action_taken,
        notes=notes,
        metadata=metadata,
    )

    return before_state, target_state, audit_entry


class TupleAuditResult:
    """Helper for typing."""
    pass


def resolve_exception_workflow(
    db: Session,
    exception_id_or_code: str,
    action_taken: str,
    user_id: str,
    notes: str,
) -> Dict[str, Any]:
    """
    Execute human-in-the-loop resolution:
    1. Looks up exception by integer ID or string code.
    2. Validates and maps action to target state.
    3. Updates exception and linked invoice status.
    4. Appends audit log entry.
    """
    # 1. Look up exception
    query = db.query(ExceptionRecord)
    if exception_id_or_code.isdigit():
        exc = query.filter(ExceptionRecord.id == int(exception_id_or_code)).first()
    else:
        exc = query.filter(ExceptionRecord.exception_code == exception_id_or_code.upper()).first()

    if not exc:
        raise ValueError(f"Exception with identifier '{exception_id_or_code}' not found.")

    clean_action = action_taken.strip().upper().replace(" ", "_")
    target_state = ACTION_TO_STATE.get(clean_action)
    if not target_state:
        raise ValueError(f"Unknown action '{action_taken}'. Allowed: {', '.join(ACTION_TO_STATE.keys())}")

    # 2. Transition exception state and record audit log
    before_state, after_state, audit_entry = transition_exception_state(
        db=db,
        exception=exc,
        target_state=target_state,
        actor=user_id,
        action_taken=clean_action,
        notes=notes,
        metadata={"invoice_number": exc.invoice_number, "po_number": exc.po_number},
    )

    # 3. Synchronize linked Invoice payment status
    invoice_status = None
    if exc.invoice_number:
        inv = db.query(Invoice).filter(Invoice.invoice_number == exc.invoice_number).first()
        if inv:
            inv_before = inv.payment_status
            if clean_action == "OVERRIDE_AND_PAY":
                inv.payment_status = "APPROVED"
            elif clean_action in ("REQUEST_CREDIT_MEMO", "ESCALATE"):
                inv.payment_status = "ON_HOLD"
            elif clean_action == "DISMISS":
                inv.payment_status = "REJECTED"

            invoice_status = inv.payment_status

            # Write corresponding invoice state transition audit log
            log_state_transition(
                db=db,
                entity_type="INVOICE",
                entity_id=inv.invoice_number,
                before_state=inv_before,
                after_state=inv.payment_status,
                actor=user_id,
                action="INVOICE_STATUS_UPDATED",
                action_taken=clean_action,
                notes=f"Triggered by human resolution of exception {exc.exception_code}: {notes}",
            )

    db.commit()
    db.refresh(exc)

    return {
        "status": "success",
        "exception_code": exc.exception_code,
        "action_taken": clean_action,
        "before_state": before_state,
        "after_state": after_state,
        "user_id": user_id,
        "resolved_at": exc.resolved_at.isoformat() if exc.resolved_at else utc_now().isoformat(),
        "invoice_number": exc.invoice_number,
        "invoice_payment_status": invoice_status,
        "notes": notes,
        "audit_log_id": audit_entry.id,
        "message": f"Exception '{exc.exception_code}' successfully transitioned from '{before_state}' to '{after_state}' by '{user_id}'.",
    }


def get_audit_trail_for_entity(
    db: Session,
    entity_id: str,
    entity_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve chronologically ordered append-only audit trail for an entity.
    """
    query = db.query(AuditLog).filter(AuditLog.entity_id == entity_id)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type.upper())

    logs = query.order_by(AuditLog.timestamp.asc()).all()
    results = []
    for log in logs:
        details_val = None
        if log.details:
            try:
                details_val = json.loads(log.details)
            except Exception:
                details_val = log.details

        results.append({
            "id": log.id,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "action": log.action,
            "performed_by": log.performed_by,
            "before_state": log.before_state,
            "after_state": log.after_state,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "details": details_val,
        })
    return results
