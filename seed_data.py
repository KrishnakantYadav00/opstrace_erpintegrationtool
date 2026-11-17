#!/usr/bin/env python3
"""
Seed Data Script for Opstrace ERP Tool.
Generates realistic 'messy' synthetic ERP datasets for 3-way matching and anomaly detection.

Failure modes injected:
1. 3-Way Match Discrepancy: PO = 100, Receipt = 80, Invoice = 100 (quantity mismatch / overbilling).
2. Price Variance: PO unit price = $10, Invoice unit price = $12 (unauthorized price hike).
3. Duplicate Invoice: Exact same invoice_number or identical vendor + amount within 48 hours.
4. Ghost Receipt / Missing PO: Invoice arrives with no Goods Receipt after 7+ days or missing PO entirely.
"""

import argparse
import json
import random
from datetime import datetime, timedelta

from database import SessionLocal, init_db
from models import PurchaseOrder, GoodsReceipt, Invoice, ExceptionRecord, AuditLog

# Vendors catalog
VENDORS = [
    {"vendor_id": "VEND-1001", "vendor_name": "Apex Industrial Supplies"},
    {"vendor_id": "VEND-1002", "vendor_name": "Titan Heavy Machinery Corp"},
    {"vendor_id": "VEND-1003", "vendor_name": "Precision Tooling & Hardware"},
    {"vendor_id": "VEND-1004", "vendor_name": "Global Tech Components"},
    {"vendor_id": "VEND-1005", "vendor_name": "Summit Raw Materials Ltd"},
    {"vendor_id": "VEND-1006", "vendor_name": "Vanguard Logistics & Packaging"},
    {"vendor_id": "VEND-1007", "vendor_name": "Quantum Electronic Sensors"},
    {"vendor_id": "VEND-1008", "vendor_name": "BlueStar Chemicals & Reagents"},
]

# SKUs catalog
ITEMS = [
    {"sku": "SKU-HYD-500", "description": "Hydraulic Cylinder High-Pressure 500mm", "base_price": 250.0},
    {"sku": "SKU-BEAR-100", "description": "Sealed Deep-Groove Ball Bearings (Box of 50)", "base_price": 85.0},
    {"sku": "SKU-STEEL-01", "description": "Galvanized High-Tensile Steel Bolts M12", "base_price": 10.0},
    {"sku": "SKU-SENS-404", "description": "Optical Proximity Sensor 24V DC", "base_price": 45.0},
    {"sku": "SKU-VALV-200", "description": "Cast Iron Pneumatic Butterfly Valve 4in", "base_price": 180.0},
    {"sku": "SKU-POLY-800", "description": "Industrial Grade Polyurethane Resin 25kg", "base_price": 115.0},
    {"sku": "SKU-CBL-100", "description": "Heavy Duty Copper Shielded Core Cable 100m", "base_price": 320.0},
    {"sku": "SKU-PUMP-050", "description": "Centrifugal Submersible Slurry Pump 5HP", "base_price": 750.0},
]


def generate_seed_data(total_flows: int = 75, seed: int = 42):
    random.seed(seed)
    db = SessionLocal()

    # Clear existing data
    print("[*] Reinitializing ERP database schema...")
    init_db(drop_all=True)

    # Calculate distribution
    # Target proportions:
    # ~60% Clean Flows
    # ~11% 3-Way Quantity Mismatch
    # ~11% Price Variance
    # ~9% Duplicate Invoices
    # ~9% Ghost Receipt / Missing PO
    count_qty_mismatch = max(1, int(total_flows * 0.11))
    count_price_variance = max(1, int(total_flows * 0.11))
    count_duplicate = max(1, int(total_flows * 0.09))
    count_ghost_receipt = max(1, int(total_flows * 0.09))
    count_clean = total_flows - (count_qty_mismatch + count_price_variance + count_duplicate + count_ghost_receipt)

    print(f"[*] Generating {total_flows} procurement flows:")
    print(f"    - Clean Match: {count_clean}")
    print(f"    - 3-Way Match Qty Discrepancy: {count_qty_mismatch}")
    print(f"    - Price Variance: {count_price_variance}")
    print(f"    - Duplicate Invoices: {count_duplicate}")
    print(f"    - Ghost Receipt / Missing PO: {count_ghost_receipt}")

    base_time = datetime(2026, 1, 10, 9, 0, 0)
    exception_seq = 1

    po_seq = 1
    gr_seq = 1
    inv_seq = 1

    # 1. Clean Flows
    for i in range(count_clean):
        vendor = random.choice(VENDORS)
        item = random.choice(ITEMS)
        po_number = f"PO-2026-{po_seq:04d}"
        gr_number = f"GR-2026-{gr_seq:04d}"
        inv_number = f"INV-2026-{inv_seq:04d}"
        po_seq += 1
        gr_seq += 1
        inv_seq += 1

        order_date = base_time + timedelta(days=random.randint(0, 45), hours=random.randint(1, 8))
        delivery_date = order_date + timedelta(days=random.randint(2, 5))
        receipt_date = delivery_date + timedelta(hours=random.randint(2, 6))
        invoice_date = receipt_date + timedelta(days=random.randint(1, 3))
        due_date = invoice_date + timedelta(days=30)

        qty = float(random.choice([25, 50, 75, 100, 150, 200]))
        unit_price = item["base_price"]
        total_amount = round(qty * unit_price, 2)

        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            description=item["description"],
            quantity=qty,
            unit_price=unit_price,
            total_amount=total_amount,
            status="RECEIVED",
            order_date=order_date,
            expected_delivery_date=delivery_date,
        )
        db.add(po)

        gr = GoodsReceipt(
            gr_number=gr_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            sku=item["sku"],
            quantity_received=qty,
            received_date=receipt_date,
            received_by=random.choice(["Dock A Intake", "Dock B Inspector", "Receiving Team 1"]),
            condition_status="ACCEPTED",
        )
        db.add(gr)

        inv = Invoice(
            invoice_number=inv_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            quantity_billed=qty,
            unit_price=unit_price,
            total_amount=total_amount,
            invoice_date=invoice_date,
            due_date=due_date,
            payment_status="APPROVED",
        )
        db.add(inv)

        db.add(AuditLog(
            entity_type="PURCHASE_ORDER",
            entity_id=po_number,
            action="FLOW_SEEDED_CLEAN",
            performed_by="SYSTEM_SEEDER",
            timestamp=order_date,
            details=f"Clean 3-way match flow created: Qty={qty}, UnitPrice={unit_price}",
        ))

    # 2. Failure Mode: 3-Way Match Discrepancy (PO = 100, Receipt = 80, Invoice = 100)
    for i in range(count_qty_mismatch):
        vendor = random.choice(VENDORS)
        item = random.choice(ITEMS)
        po_number = f"PO-2026-{po_seq:04d}"
        gr_number = f"GR-2026-{gr_seq:04d}"
        inv_number = f"INV-2026-{inv_seq:04d}"
        po_seq += 1
        gr_seq += 1
        inv_seq += 1

        order_date = base_time + timedelta(days=random.randint(10, 50), hours=random.randint(1, 8))
        delivery_date = order_date + timedelta(days=3)
        receipt_date = delivery_date + timedelta(hours=4)
        invoice_date = receipt_date + timedelta(days=2)
        due_date = invoice_date + timedelta(days=30)

        # Failure mode specifics: PO = 100 (or scale), Receipt = 80, Invoice = 100
        po_qty = 100.0 if i == 0 else float(random.choice([50, 100, 150, 200]))
        gr_qty = 80.0 if i == 0 else round(po_qty * random.choice([0.7, 0.75, 0.8, 0.85]), 1)
        inv_qty = po_qty  # Vendor bills for full PO qty despite receiving shortage

        unit_price = item["base_price"]
        po_total = round(po_qty * unit_price, 2)
        inv_total = round(inv_qty * unit_price, 2)

        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            description=item["description"],
            quantity=po_qty,
            unit_price=unit_price,
            total_amount=po_total,
            status="PARTIALLY_RECEIVED",
            order_date=order_date,
            expected_delivery_date=delivery_date,
        )
        db.add(po)

        gr = GoodsReceipt(
            gr_number=gr_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            sku=item["sku"],
            quantity_received=gr_qty,
            received_date=receipt_date,
            received_by="Dock Intake - Shortage Logged",
            condition_status="SHORTAGE",
        )
        db.add(gr)

        inv = Invoice(
            invoice_number=inv_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            quantity_billed=inv_qty,
            unit_price=unit_price,
            total_amount=inv_total,
            invoice_date=invoice_date,
            due_date=due_date,
            payment_status="ON_HOLD",
        )
        db.add(inv)

        # Flag exception record
        exc_code = f"EXC-2026-{exception_seq:04d}"
        exception_seq += 1
        qty_variance = inv_qty - gr_qty
        overbill_amount = round(qty_variance * unit_price, 2)
        details = {
            "failure_mode": "3_WAY_MATCH_QTY_DISCREPANCY",
            "po_quantity": po_qty,
            "received_quantity": gr_qty,
            "billed_quantity": inv_qty,
            "quantity_variance": qty_variance,
            "overbilling_usd": overbill_amount,
            "note": f"Invoice billed {inv_qty} units but warehouse only received {gr_qty} units."
        }
        exc = ExceptionRecord(
            exception_code=exc_code,
            po_number=po_number,
            invoice_number=inv_number,
            gr_number=gr_number,
            vendor_id=vendor["vendor_id"],
            exception_type="QTY_MISMATCH_3WAY",
            severity="HIGH",
            discrepancy_details=json.dumps(details),
            status="OPEN",
            created_at=invoice_date,
        )
        db.add(exc)

        db.add(AuditLog(
            entity_type="EXCEPTION",
            entity_id=exc_code,
            action="INVOICE_QTY_MISMATCH_FLAGGED",
            performed_by="SYSTEM_SEEDER",
            timestamp=invoice_date,
            details=f"Injected 3-way match quantity discrepancy: PO={po_qty}, GR={gr_qty}, INV={inv_qty} (Shortage: {qty_variance})",
        ))

    # 3. Failure Mode: Price Variance (PO unit price = $10, Invoice unit price = $12)
    for i in range(count_price_variance):
        vendor = random.choice(VENDORS)
        # Select steel bolts (base $10) for first instance to match prompt exactly ($10 vs $12)
        item = ITEMS[2] if i == 0 else random.choice(ITEMS)
        po_number = f"PO-2026-{po_seq:04d}"
        gr_number = f"GR-2026-{gr_seq:04d}"
        inv_number = f"INV-2026-{inv_seq:04d}"
        po_seq += 1
        gr_seq += 1
        inv_seq += 1

        order_date = base_time + timedelta(days=random.randint(15, 60), hours=random.randint(1, 8))
        delivery_date = order_date + timedelta(days=4)
        receipt_date = delivery_date + timedelta(hours=3)
        invoice_date = receipt_date + timedelta(days=2)
        due_date = invoice_date + timedelta(days=30)

        qty = float(random.choice([50, 100, 200]))
        po_unit_price = 10.0 if i == 0 else item["base_price"]
        inv_unit_price = 12.0 if i == 0 else round(po_unit_price * random.choice([1.15, 1.20, 1.25]), 2)

        po_total = round(qty * po_unit_price, 2)
        inv_total = round(qty * inv_unit_price, 2)

        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            description=item["description"],
            quantity=qty,
            unit_price=po_unit_price,
            total_amount=po_total,
            status="RECEIVED",
            order_date=order_date,
            expected_delivery_date=delivery_date,
        )
        db.add(po)

        gr = GoodsReceipt(
            gr_number=gr_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            sku=item["sku"],
            quantity_received=qty,
            received_date=receipt_date,
            received_by="Dock Intake - Inspection OK",
            condition_status="ACCEPTED",
        )
        db.add(gr)

        inv = Invoice(
            invoice_number=inv_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            quantity_billed=qty,
            unit_price=inv_unit_price,
            total_amount=inv_total,
            invoice_date=invoice_date,
            due_date=due_date,
            payment_status="ON_HOLD",
        )
        db.add(inv)

        # Flag exception record
        exc_code = f"EXC-2026-{exception_seq:04d}"
        exception_seq += 1
        unit_variance = round(inv_unit_price - po_unit_price, 2)
        variance_pct = round((unit_variance / po_unit_price) * 100, 2)
        details = {
            "failure_mode": "PRICE_VARIANCE",
            "po_unit_price": po_unit_price,
            "invoice_unit_price": inv_unit_price,
            "unit_price_variance": unit_variance,
            "variance_percentage": variance_pct,
            "total_unauthorized_premium": round(qty * unit_variance, 2),
            "note": f"Invoice unit price ${inv_unit_price} exceeds contracted PO unit price ${po_unit_price} by {variance_pct}%."
        }
        exc = ExceptionRecord(
            exception_code=exc_code,
            po_number=po_number,
            invoice_number=inv_number,
            gr_number=gr_number,
            vendor_id=vendor["vendor_id"],
            exception_type="PRICE_VARIANCE",
            severity="MEDIUM" if variance_pct < 15 else "HIGH",
            discrepancy_details=json.dumps(details),
            status="OPEN",
            created_at=invoice_date,
        )
        db.add(exc)

        db.add(AuditLog(
            entity_type="EXCEPTION",
            entity_id=exc_code,
            action="PRICE_VARIANCE_FLAGGED",
            performed_by="SYSTEM_SEEDER",
            timestamp=invoice_date,
            details=f"Injected price variance: PO=${po_unit_price}, INV=${inv_unit_price} (+{variance_pct}%)",
        ))

    # 4. Failure Mode: Duplicate Invoice
    # Sub-case A: Exact duplicate invoice_number
    # Sub-case B: Identical amount and vendor within 48 hours
    for i in range(count_duplicate):
        vendor = random.choice(VENDORS)
        item = random.choice(ITEMS)
        po_number = f"PO-2026-{po_seq:04d}"
        gr_number = f"GR-2026-{gr_seq:04d}"
        po_seq += 1
        gr_seq += 1

        order_date = base_time + timedelta(days=random.randint(20, 65), hours=random.randint(1, 8))
        delivery_date = order_date + timedelta(days=4)
        receipt_date = delivery_date + timedelta(hours=2)
        first_inv_date = receipt_date + timedelta(days=2)
        due_date = first_inv_date + timedelta(days=30)

        qty = float(random.choice([30, 60, 100]))
        unit_price = item["base_price"]
        total_amount = round(qty * unit_price, 2)

        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            description=item["description"],
            quantity=qty,
            unit_price=unit_price,
            total_amount=total_amount,
            status="RECEIVED",
            order_date=order_date,
            expected_delivery_date=delivery_date,
        )
        db.add(po)

        gr = GoodsReceipt(
            gr_number=gr_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            sku=item["sku"],
            quantity_received=qty,
            received_date=receipt_date,
            received_by="Dock Intake - Verified",
            condition_status="ACCEPTED",
        )
        db.add(gr)

        # Primary Invoice
        primary_inv_number = f"INV-2026-{inv_seq:04d}"
        inv_seq += 1
        inv1 = Invoice(
            invoice_number=primary_inv_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            quantity_billed=qty,
            unit_price=unit_price,
            total_amount=total_amount,
            invoice_date=first_inv_date,
            due_date=due_date,
            payment_status="APPROVED",
        )
        db.add(inv1)

        # Secondary (Duplicate) Invoice within 48 hours
        hours_delta = random.randint(6, 42)
        duplicate_inv_date = first_inv_date + timedelta(hours=hours_delta)

        if i % 2 == 0:
            # Type A: Same exact invoice_number re-submitted
            duplicate_inv_number = primary_inv_number
            dup_sub_type = "EXACT_INVOICE_NUMBER"
        else:
            # Type B: Different invoice number, but identical vendor & amount within 48h
            duplicate_inv_number = f"INV-2026-{inv_seq:04d}"
            inv_seq += 1
            dup_sub_type = "IDENTICAL_AMOUNT_AND_VENDOR_WITHIN_48H"

        inv2 = Invoice(
            invoice_number=duplicate_inv_number,
            po_number=po_number,
            vendor_id=vendor["vendor_id"],
            vendor_name=vendor["vendor_name"],
            sku=item["sku"],
            quantity_billed=qty,
            unit_price=unit_price,
            total_amount=total_amount,
            invoice_date=duplicate_inv_date,
            due_date=due_date,
            payment_status="REJECTED",
        )
        db.add(inv2)

        # Exception
        exc_code = f"EXC-2026-{exception_seq:04d}"
        exception_seq += 1
        details = {
            "failure_mode": "DUPLICATE_INVOICE",
            "duplicate_type": dup_sub_type,
            "original_invoice": primary_inv_number,
            "duplicate_invoice": duplicate_inv_number,
            "vendor_id": vendor["vendor_id"],
            "amount": total_amount,
            "time_difference_hours": hours_delta,
            "note": f"Duplicate invoice detected ({dup_sub_type}) within {hours_delta} hours for amount ${total_amount}."
        }
        exc = ExceptionRecord(
            exception_code=exc_code,
            po_number=po_number,
            invoice_number=duplicate_inv_number,
            gr_number=gr_number,
            vendor_id=vendor["vendor_id"],
            exception_type="DUPLICATE_INVOICE",
            severity="CRITICAL",
            discrepancy_details=json.dumps(details),
            status="OPEN",
            created_at=duplicate_inv_date,
        )
        db.add(exc)

        db.add(AuditLog(
            entity_type="EXCEPTION",
            entity_id=exc_code,
            action="DUPLICATE_INVOICE_FLAGGED",
            performed_by="SYSTEM_SEEDER",
            timestamp=duplicate_inv_date,
            details=f"Injected duplicate invoice: {dup_sub_type} (Original={primary_inv_number}, Dup={duplicate_inv_number}, Δt={hours_delta}h)",
        ))

    # 5. Failure Mode: Ghost Receipt / Missing PO
    # Sub-case A: Invoice arrives with no corresponding Goods Receipt after 7+ days
    # Sub-case B: Invoice arrives referencing non-existent PO or with PO missing entirely
    for i in range(count_ghost_receipt):
        vendor = random.choice(VENDORS)
        item = random.choice(ITEMS)
        inv_number = f"INV-2026-{inv_seq:04d}"
        inv_seq += 1

        order_date = base_time + timedelta(days=random.randint(25, 70), hours=random.randint(1, 8))
        qty = float(random.choice([20, 50, 100]))
        unit_price = item["base_price"]
        total_amount = round(qty * unit_price, 2)

        if i % 2 == 0:
            # Sub-case A: PO exists, Invoice arrived > 7 days ago, but NO Goods Receipt has ever been filed (Ghost Receipt)
            po_number = f"PO-2026-{po_seq:04d}"
            po_seq += 1
            po = PurchaseOrder(
                po_number=po_number,
                vendor_id=vendor["vendor_id"],
                vendor_name=vendor["vendor_name"],
                sku=item["sku"],
                description=item["description"],
                quantity=qty,
                unit_price=unit_price,
                total_amount=total_amount,
                status="ISSUED",  # Still ISSUED, goods never logged as received
                order_date=order_date,
                expected_delivery_date=order_date + timedelta(days=5),
            )
            db.add(po)

            # Invoice arrives 9-14 days after PO (> 7 days elapsed with zero Goods Receipt)
            days_elapsed = random.randint(8, 15)
            invoice_date = order_date + timedelta(days=days_elapsed)
            due_date = invoice_date + timedelta(days=30)

            inv = Invoice(
                invoice_number=inv_number,
                po_number=po_number,
                vendor_id=vendor["vendor_id"],
                vendor_name=vendor["vendor_name"],
                sku=item["sku"],
                quantity_billed=qty,
                unit_price=unit_price,
                total_amount=total_amount,
                invoice_date=invoice_date,
                due_date=due_date,
                payment_status="ON_HOLD",
            )
            db.add(inv)

            exc_code = f"EXC-2026-{exception_seq:04d}"
            exception_seq += 1
            details = {
                "failure_mode": "GHOST_RECEIPT_MISSING_GR",
                "po_number": po_number,
                "invoice_number": inv_number,
                "days_since_po": days_elapsed,
                "note": f"Invoice received {days_elapsed} days after PO order, but no Goods Receipt exists in warehouse inventory."
            }
            exc = ExceptionRecord(
                exception_code=exc_code,
                po_number=po_number,
                invoice_number=inv_number,
                gr_number=None,
                vendor_id=vendor["vendor_id"],
                exception_type="GHOST_RECEIPT_MISSING_GR",
                severity="HIGH",
                discrepancy_details=json.dumps(details),
                status="OPEN",
                created_at=invoice_date,
            )
            db.add(exc)

            db.add(AuditLog(
                entity_type="EXCEPTION",
                entity_id=exc_code,
                action="GHOST_RECEIPT_FLAGGED",
                performed_by="SYSTEM_SEEDER",
                timestamp=invoice_date,
                details=f"Injected Ghost Receipt exception: Invoice {inv_number} has no Goods Receipt after {days_elapsed} days for PO {po_number}",
            ))

        else:
            # Sub-case B: Invoice arrives referencing non-existent / orphaned PO number or Missing PO entirely
            ghost_po_number = f"PO-GHOST-{random.randint(8000, 9999)}"
            invoice_date = order_date + timedelta(days=10)
            due_date = invoice_date + timedelta(days=30)

            inv = Invoice(
                invoice_number=inv_number,
                po_number=ghost_po_number,  # PO doesn't exist in purchase_orders table
                vendor_id=vendor["vendor_id"],
                vendor_name=vendor["vendor_name"],
                sku=item["sku"],
                quantity_billed=qty,
                unit_price=unit_price,
                total_amount=total_amount,
                invoice_date=invoice_date,
                due_date=due_date,
                payment_status="REJECTED",
            )
            db.add(inv)

            exc_code = f"EXC-2026-{exception_seq:04d}"
            exception_seq += 1
            details = {
                "failure_mode": "MISSING_PO_ORPHAN_INVOICE",
                "referenced_po_number": ghost_po_number,
                "invoice_number": inv_number,
                "note": f"Invoice submitted against non-existent PO '{ghost_po_number}'. No matching purchase order on record."
            }
            exc = ExceptionRecord(
                exception_code=exc_code,
                po_number=ghost_po_number,
                invoice_number=inv_number,
                gr_number=None,
                vendor_id=vendor["vendor_id"],
                exception_type="MISSING_PO",
                severity="CRITICAL",
                discrepancy_details=json.dumps(details),
                status="OPEN",
                created_at=invoice_date,
            )
            db.add(exc)

            db.add(AuditLog(
                entity_type="EXCEPTION",
                entity_id=exc_code,
                action="MISSING_PO_FLAGGED",
                performed_by="SYSTEM_SEEDER",
                timestamp=invoice_date,
                details=f"Injected Missing PO exception: Invoice {inv_number} submitted with unrecognized PO {ghost_po_number}",
            ))

    # Commit all staged records
    db.commit()

    # Print summary statistics
    po_count = db.query(PurchaseOrder).count()
    gr_count = db.query(GoodsReceipt).count()
    inv_count = db.query(Invoice).count()
    exc_count = db.query(ExceptionRecord).count()
    audit_count = db.query(AuditLog).count()

    print("\n[+] Seeding complete!")
    print(f"    - Purchase Orders:  {po_count}")
    print(f"    - Goods Receipts:   {gr_count}")
    print(f"    - Invoices:         {inv_count}")
    print(f"    - Exceptions:       {exc_count}")
    print(f"    - Audit Logs:       {audit_count}")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed ERP Synthetic Dataset with realistic matching failures")
    parser.add_argument("--count", type=int, default=75, help="Number of procurement flows to generate (default: 75)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible generation (default: 42)")
    args = parser.parse_args()

    generate_seed_data(total_flows=args.count, seed=args.seed)
