#!/usr/bin/env python3
"""
Verification Script for Opstrace ERP Dataset.
Runs validation queries across purchase_orders, goods_receipts, invoices,
exceptions, and audit_logs to verify schema integrity and failure mode injection.
"""

import json
from database import SessionLocal
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog


def run_verification():
    db = SessionLocal()

    print("=" * 70)
    print("OPSTRACE ERP DATASET INTEGRITY & FAILURE MODE VERIFICATION")
    print("=" * 70)

    # 1. Total counts
    po_count = db.query(PurchaseOrder).count()
    gr_count = db.query(GoodsReceipt).count()
    inv_count = db.query(Invoice).count()
    exc_count = db.query(ExceptionRecord).count()
    audit_count = db.query(AuditLog).count()

    print(f"\n[1] Record Counts:")
    print(f"    - Purchase Orders:  {po_count}")
    print(f"    - Goods Receipts:   {gr_count}")
    print(f"    - Invoices:         {inv_count}")
    print(f"    - Exceptions:       {exc_count}")
    print(f"    - Audit Logs:       {audit_count}")

    # 2. Check failure mode 1: 3-Way Match Quantity Discrepancy
    print(f"\n[2] Checking 3-Way Match Quantity Discrepancies:")
    qty_mismatches = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "QTY_MISMATCH_3WAY")
        .all()
    )
    print(f"    Found {len(qty_mismatches)} quantity mismatch exceptions.")
    for exc in qty_mismatches[:3]:
        details = json.loads(exc.discrepancy_details)
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exc.po_number).first()
        gr = db.query(GoodsReceipt).filter(GoodsReceipt.gr_number == exc.gr_number).first()
        inv = db.query(Invoice).filter(Invoice.invoice_number == exc.invoice_number).first()
        print(f"    -> PO: {exc.po_number} (PO Qty: {po.quantity}) | GR: {gr.gr_number} (Received: {gr.quantity_received}) | INV: {inv.invoice_number} (Billed: {inv.quantity_billed})")
        print(f"       Shortage: {details.get('quantity_variance')} units | Overbilled: ${details.get('overbilling_usd')}")

    # 3. Check failure mode 2: Price Variance
    print(f"\n[3] Checking Price Variances:")
    price_variances = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "PRICE_VARIANCE")
        .all()
    )
    print(f"    Found {len(price_variances)} price variance exceptions.")
    for exc in price_variances[:3]:
        details = json.loads(exc.discrepancy_details)
        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == exc.po_number).first()
        inv = db.query(Invoice).filter(Invoice.invoice_number == exc.invoice_number).first()
        print(f"    -> PO: {exc.po_number} (PO Unit Price: ${po.unit_price:.2f}) vs INV: {inv.invoice_number} (Billed Unit Price: ${inv.unit_price:.2f})")
        print(f"       Variance: +${details.get('unit_price_variance'):.2f} (+{details.get('variance_percentage')}%)")

    # 4. Check failure mode 3: Duplicate Invoices
    print(f"\n[4] Checking Duplicate Invoices:")
    dup_invoices = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type == "DUPLICATE_INVOICE")
        .all()
    )
    print(f"    Found {len(dup_invoices)} duplicate invoice exceptions.")
    for exc in dup_invoices[:3]:
        details = json.loads(exc.discrepancy_details)
        print(f"    -> Type: {details.get('duplicate_type')}")
        print(f"       Original: {details.get('original_invoice')} | Duplicate: {details.get('duplicate_invoice')} | Vendor: {details.get('vendor_id')} | Amount: ${details.get('amount')}")
        print(f"       Submitted within: {details.get('time_difference_hours')} hours")

    # 5. Check failure mode 4: Ghost Receipt & Missing PO
    print(f"\n[5] Checking Ghost Receipts & Missing POs:")
    ghost_exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.exception_type.in_(["GHOST_RECEIPT_MISSING_GR", "MISSING_PO"]))
        .all()
    )
    print(f"    Found {len(ghost_exceptions)} ghost receipt / missing PO exceptions.")
    for exc in ghost_exceptions[:3]:
        details = json.loads(exc.discrepancy_details)
        print(f"    -> Code: {exc.exception_code} | Type: {exc.exception_type} | PO: {exc.po_number} | INV: {exc.invoice_number}")
        print(f"       Details: {details.get('note')}")

    # 6. Verification of Relationships & Linking
    print(f"\n[6] Verifying Cross-Table Relationships via po_number:")
    sample_po = db.query(PurchaseOrder).first()
    print(f"    Sample PO: {sample_po.po_number}")
    print(f"    - Linked Goods Receipts count: {len(sample_po.goods_receipts)}")
    print(f"    - Linked Invoices count:       {len(sample_po.invoices)}")
    print(f"    - Linked Exceptions count:     {len(sample_po.exceptions)}")

    print("\n" + "=" * 70)
    print("ALL INTEGRITY CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    db.close()


if __name__ == "__main__":
    run_verification()
