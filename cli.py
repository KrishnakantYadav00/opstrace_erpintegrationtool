#!/usr/bin/env python3
"""
Opstrace ERP MVP - CLI Inspector & Test Utility.
Allows testing, inspecting, and querying the ERP synthetic database.
"""

import argparse
import json
import subprocess
import sys
from database import SessionLocal
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f" {title.upper()}")
    print("=" * 70)


def cmd_stats(args):
    """Show dataset statistics and anomaly breakdown."""
    db = SessionLocal()
    print_header("ERP Dataset Executive Summary")

    po_count = db.query(PurchaseOrder).count()
    gr_count = db.query(GoodsReceipt).count()
    inv_count = db.query(Invoice).count()
    exc_count = db.query(ExceptionRecord).count()
    audit_count = db.query(AuditLog).count()

    print(f"  Purchase Orders (POs):  {po_count}")
    print(f"  Goods Receipts (GRs):   {gr_count}")
    print(f"  Invoices:               {inv_count}")
    print(f"  Exceptions Flagged:     {exc_count}")
    print(f"  Audit Log Entries:      {audit_count}")

    print("\n  Breakdown by Exception Type:")
    exc_types = [
        ("QTY_MISMATCH_3WAY", "3-Way Match Quantity Discrepancy"),
        ("PRICE_VARIANCE", "Unit Price Variance"),
        ("DUPLICATE_INVOICE", "Duplicate Invoice (Exact / Vendor 48h)"),
        ("GHOST_RECEIPT_MISSING_GR", "Ghost Receipt (Invoice with No GR after 7+ days)"),
        ("MISSING_PO", "Missing / Orphan Purchase Order"),
    ]
    for code, label in exc_types:
        count = db.query(ExceptionRecord).filter(ExceptionRecord.exception_type == code).count()
        print(f"    - {label:<48}: {count:>2}")

    db.close()


def cmd_inspect_po(args):
    """Inspect a specific PO and its linked records."""
    db = SessionLocal()
    po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == args.po_number).first()
    if not po:
        print(f"\n[!] Purchase Order '{args.po_number}' not found.")
        db.close()
        return

    print_header(f"Purchase Order Details: {po.po_number}")
    print(f"  Vendor:     {po.vendor_name} ({po.vendor_id})")
    print(f"  SKU:        {po.sku} - {po.description}")
    print(f"  Quantity:   {po.quantity}")
    print(f"  Unit Price: ${po.unit_price:.2f}")
    print(f"  Total:      ${po.total_amount:.2f} {po.currency}")
    print(f"  Status:     {po.status}")
    print(f"  Order Date: {po.order_date}")

    print("\n  Linked Goods Receipts:")
    if po.goods_receipts:
        for gr in po.goods_receipts:
            print(f"    * GR: {gr.gr_number} | Qty Received: {gr.quantity_received} | Date: {gr.received_date} | Status: {gr.condition_status}")
    else:
        print("    * (None on record)")

    print("\n  Linked Invoices:")
    if po.invoices:
        for inv in po.invoices:
            print(f"    * INV: {inv.invoice_number} | Qty Billed: {inv.quantity_billed} | Unit: ${inv.unit_price:.2f} | Total: ${inv.total_amount:.2f} | Status: {inv.payment_status}")
    else:
        print("    * (None on record)")

    print("\n  Flagged Exceptions:")
    if po.exceptions:
        for exc in po.exceptions:
            print(f"    * Code: {exc.exception_code} [{exc.severity}] Type: {exc.exception_type}")
            if exc.discrepancy_details:
                details = json.loads(exc.discrepancy_details)
                print(f"      Diagnostic: {details}")
    else:
        print("    * (None - Clean match)")

    db.close()


def cmd_exceptions(args):
    """List exceptions, optionally filtered by type."""
    db = SessionLocal()
    query = db.query(ExceptionRecord)
    if args.type:
        query = query.filter(ExceptionRecord.exception_type == args.type)
    exceptions = query.all()

    print_header(f"Exceptions List ({len(exceptions)} records)")
    for exc in exceptions:
        print(f"  [{exc.severity:<8}] {exc.exception_code} | Type: {exc.exception_type:<24} | PO: {str(exc.po_number):<14} | INV: {str(exc.invoice_number):<14}")
        if exc.discrepancy_details:
            details = json.loads(exc.discrepancy_details)
            note = details.get("note", str(details))
            print(f"             Note: {note}")
    db.close()


def cmd_reconcile(args):
    """Run deterministic reconciliation engine batch scan."""
    from engine import reconcile_all
    db = SessionLocal()
    print_header("Running Deterministic 3-Way Reconciliation Batch Scan")
    summary = reconcile_all(db)
    print(f"  Total Invoices Scanned: {summary['total_invoices_scanned']}")
    print(f"  Total POs Scanned:      {summary['total_pos_scanned']}")
    print(f"  New Exceptions Raised:  {summary['exceptions_generated']}")
    if summary["breakdown_by_type"]:
        print("\n  Exceptions by Type:")
        for etype, count in summary["breakdown_by_type"].items():
            print(f"    - {etype:<26}: {count}")
    db.close()


def cmd_run_tests(args):
    """Run pytest test suite."""
    print_header("Executing Automated Pytest Suite")
    result = subprocess.run([sys.executable, "-m", "pytest", "-v", "tests/"])
    sys.exit(result.returncode)


def cmd_serve(args):
    """Start FastAPI ingestion server using uvicorn."""
    import uvicorn
    print_header(f"Starting OpsTrace Ingestion Server on {args.host}:{args.port}")
    uvicorn.run("app:app", host=args.host, port=args.port, reload=args.reload)


def main():
    parser = argparse.ArgumentParser(description="Opstrace ERP MVP CLI Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # stats
    subparsers.add_parser("stats", help="View summary counts and anomaly breakdown")

    # po
    po_parser = subparsers.add_parser("po", help="Inspect a specific PO and its 3-way match relationships")
    po_parser.add_argument("po_number", type=str, help="Purchase order number (e.g. PO-2026-0048)")

    # exceptions
    exc_parser = subparsers.add_parser("exceptions", help="List all flagged exceptions")
    exc_parser.add_argument("--type", type=str, choices=[
        "QTY_MISMATCH_3WAY", "PRICE_VARIANCE", "DUPLICATE_INVOICE", "GHOST_RECEIPT_MISSING_GR", "MISSING_PO"
    ], help="Filter by exception type")

    # reconcile
    subparsers.add_parser("reconcile", help="Run deterministic 3-way reconciliation scan across database")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start FastAPI REST server")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload on file changes")

    # test
    subparsers.add_parser("test", help="Run automated test suite (pytest)")

    args = parser.parse_args()
    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "po":
        cmd_inspect_po(args)
    elif args.command == "exceptions":
        cmd_exceptions(args)
    elif args.command == "reconcile":
        cmd_reconcile(args)
    elif args.command == "serve":
        cmd_serve(args)
    elif args.command == "test":
        cmd_run_tests(args)


if __name__ == "__main__":
    main()
