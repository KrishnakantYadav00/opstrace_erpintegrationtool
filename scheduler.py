"""
Scheduled Background Reconciliation Sweep for OpsTrace ERP.
Uses APScheduler to run automated sweeps every 5 minutes (configurable)
to detect time-based exceptions like overdue approvals (SLA breaches) and un-invoiced/un-received orders.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from database import SessionLocal
from engine import reconcile_all
from models import AuditLog

# Global scheduler instance
_scheduler: Optional[BackgroundScheduler] = None
_last_run_time: Optional[datetime] = None
_last_run_summary: Optional[Dict[str, Any]] = None


def run_scheduled_reconciliation_sweep() -> Dict[str, Any]:
    """
    Periodic worker job: executes deterministic reconciliation across all ERP records.
    """
    global _last_run_time, _last_run_summary
    db = SessionLocal()
    start_time = datetime.now(timezone.utc)
    try:
        summary = reconcile_all(db)
        _last_run_time = start_time
        _last_run_summary = summary

        # Log audit entry for the sweep
        audit = AuditLog(
            entity_type="SYSTEM",
            entity_id="APS_SWEEPER",
            action="SCHEDULED_RECONCILIATION_SWEEP",
            performed_by="SYSTEM_SCHEDULER",
            before_state=None,
            after_state="SWEEP_COMPLETED",
            timestamp=start_time,
            details=f"Background 5-minute sweep completed: Invoices={summary.get('total_invoices_scanned')}, POs={summary.get('total_pos_scanned')}, New Exceptions={summary.get('exceptions_generated')}",
        )
        db.add(audit)
        db.commit()
        return summary
    finally:
        db.close()


def start_scheduler(interval_minutes: int = 5) -> BackgroundScheduler:
    """
    Start the background reconciliation sweep scheduler.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        func=run_scheduled_reconciliation_sweep,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="reconciliation_sweep_job",
        name="OpsTrace 5-Minute Deterministic Reconciliation Sweep",
        replace_existing=True,
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler_status() -> Dict[str, Any]:
    """Inspect scheduler operational state."""
    global _scheduler, _last_run_time, _last_run_summary
    is_running = _scheduler is not None and _scheduler.running
    next_run = None
    if is_running:
        job = _scheduler.get_job("reconciliation_sweep_job")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    return {
        "scheduler_running": is_running,
        "job_id": "reconciliation_sweep_job",
        "interval_minutes": 5,
        "next_run_time": next_run,
        "last_run_time": _last_run_time.isoformat() if _last_run_time else None,
        "last_run_summary": _last_run_summary,
    }
