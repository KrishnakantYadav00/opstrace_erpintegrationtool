# Opstrace ERP Tool - Step 1: Data Modeling & Synthetic ERP Dataset

## Overview
This module implements the relational data model and synthetic dataset generator for ERP procurement workflows, simulating realistic "messy" enterprise procurement data with injected matching discrepancies and anomalies.

## Relational Schema

The core relational schema consists of 5 tables implemented in `models.py` via SQLAlchemy:

| Table | Primary Key | Key Linking Fields | Purpose |
|---|---|---|---|
| `purchase_orders` | `id` | `po_number` (unique), `vendor_id`, `sku` | Authoritative procurement contracts with approved quantities and unit prices |
| `goods_receipts` | `id` | `gr_number` (unique), `po_number`, `vendor_id`, `sku` | Physical warehouse receiving logs with received quantities and intake timestamps |
| `invoices` | `id` | `invoice_number`, `po_number` (nullable), `vendor_id`, `sku` | Accounts Payable invoices submitted by vendors with billed quantities and unit prices |
| `exceptions` | `id` | `exception_code`, `po_number`, `invoice_number`, `gr_number`, `vendor_id` | Audit records for flagged discrepancies, severity ratings, and investigation diagnostics |
| `audit_logs` | `id` | `entity_type`, `entity_id` | Immutable chronological tracking of all procurement entity lifecycle events |

### Cross-Table Linkage
The three core business tables (`purchase_orders`, `goods_receipts`, and `invoices`) are linked primarily through `po_number` (with secondary matching keys `vendor_id` and `sku`), supported by composite indexes:
- `ix_po_vendor_sku`
- `ix_gr_vendor_sku`
- `ix_invoice_vendor_sku`
- `ix_invoice_vendor_amount_date`

---

## Injected Failure Modes

The synthetic generator (`seed_data.py`) creates 50–100 procurement flows containing both clean workflows and specific injected anomalies:

1. **3-Way Match Discrepancy (Quantity Shortage / Overbilling)**
   - **Pattern**: PO ordered = 100 units, Goods Receipt logged = 80 units, Invoice billed = 100 units.
   - **Exception**: `QTY_MISMATCH_3WAY` (Severity: `HIGH`).

2. **Price Variance (Unauthorized Price Markup)**
   - **Pattern**: Contracted PO unit price = $10.00, Invoice billed unit price = $12.00 (+20% variance).
   - **Exception**: `PRICE_VARIANCE` (Severity: `MEDIUM` to `HIGH`).

3. **Duplicate Invoice**
   - **Pattern A (Exact Number)**: Same `invoice_number` resubmitted within 48 hours.
   - **Pattern B (Identical Amount & Vendor)**: Two distinct invoices from the same vendor for the exact same amount submitted within 48 hours.
   - **Exception**: `DUPLICATE_INVOICE` (Severity: `CRITICAL`).

4. **Ghost Receipt / Missing PO**
   - **Pattern A (Ghost Receipt)**: Invoice arrives > 7 days after PO creation, but warehouse has zero record of goods received.
   - **Pattern B (Missing PO)**: Invoice references an invalid/non-existent PO number or has an orphan reference.
   - **Exception**: `GHOST_RECEIPT_MISSING_GR` and `MISSING_PO` (Severity: `HIGH` / `CRITICAL`).

---

## Quick Start

### 1. Requirements
Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Generate Synthetic ERP Dataset
Run the seeder with default 75 procurement flows:
```bash
python seed_data.py
```
Options:
- `--count <int>`: Number of flows to generate (e.g. `--count 100`).
- `--seed <int>`: Random seed for deterministic reproducibility.

### 3. Verify Integrity & Failure Modes
Run the verification script to inspect dataset statistics and anomaly reports:
```bash
python verify_seed.py
```
