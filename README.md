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

## Step 2: Transaction Ingestion Layer (REST API + Bulk CSV)

The ingestion layer simulates real-time ERP event webhooks and end-of-day batch file exports via FastAPI and Pydantic.

### Endpoints

| Method | Endpoint | Description | Payload Format |
|---|---|---|---|
| `GET` | `/health` | API & database connectivity check | N/A |
| `POST` | `/ingest/po` | Real-time webhook for Purchase Orders | JSON (`PurchaseOrderIngestSchema`) |
| `POST` | `/ingest/receipt` | Real-time webhook for Goods Receipts | JSON (`GoodsReceiptIngestSchema`) |
| `POST` | `/ingest/invoice` | Real-time webhook for Vendor Invoices | JSON (`InvoiceIngestSchema`) |
| `POST` | `/ingest/csv/{entity_type}` | Bulk CSV batch export ingestion (`po`, `receipt`, `invoice`) | Multipart Form File (`.csv`) |

### Data Normalization & Validation (Pydantic)
- **Whitespace Sanitization**: Strips leading/trailing spaces from `po_number`, `vendor_id`, `sku`, `gr_number`, and `invoice_number`.
- **Currency Standardization**: Normalizes currency codes to uppercase 3-letter ISO standards (e.g., `" usd "` -> `"USD"`).
- **Auto-Calculated Totals**: Automatically calculates `total_amount = round(quantity * unit_price, 2)` if omitted in payload.
- **Flexible Date Parsing**: Accepts ISO timestamps (`2026-03-01T10:00:00Z`), standard timestamps (`2026-03-01 10:00:00`), and date strings (`2026-03-01`).
- **Strict Business Constraints**: Enforces positive quantities (`quantity > 0`) and non-negative pricing (`unit_price >= 0`).
- **Flexible CSV Header Mapping**: Case-insensitive aliases (e.g. `PO Number`, `Supplier ID`, `Item SKU`, `Qty`, `Price`).
- **Audit Trail**: Every webhook or bulk CSV ingestion logs an entry in `audit_logs`.

---

## Step 3: Deterministic Reconciliation & Exception Engine

OpsTrace uses pure Python deterministic rules to evaluate linked procurement records—**never an LLM** for arithmetic or discrepancy detection.

### 3-Way Match Rules

1. **Quantity Check** (`QUANTITY_MISMATCH`)
   - **Evaluation**: `invoice.quantity_billed != receipt.quantity_received or receipt.quantity_received != po.quantity`
   - **Severity**: `High` if overbilling detected or variance > 10%, else `Medium`
   - **Variance Amount**: `abs(invoice.qty - receipt.qty) * invoice.unit_price`

2. **Price Check** (`PRICE_VARIANCE`)
   - **Evaluation**: `invoice.unit_price > po.unit_price`
   - **Severity**: `High` if markup $\ge$ 10% or total variance $\ge$ $250, else `Medium`
   - **Variance Amount**: `(invoice.unit_price - po.unit_price) * invoice.quantity_billed`

3. **Completeness Check** (`MISSING_PO`, `MISSING_RECEIPT`)
   - **Evaluation**: Invoice exists, but PO is `None` or Goods Receipt is `None`
   - **Severity**: `High`
   - **Variance Amount**: `invoice.total_amount`

4. **SLA Breach Check** (`SLA_BREACH`)
   - **Evaluation**: `status in ["PENDING_APPROVAL", "PENDING_MATCH"]` and `created_at < now() - timedelta(days=3)`
   - **Severity**: `Medium`

5. **Duplicate Invoice Check** (`DUPLICATE_INVOICE`)
   - **Evaluation**: Same `invoice_number` or identical vendor & total amount within 48 hours
   - **Severity**: `High`

### Exception Record Schema & Snapshots
When an anomaly is flagged, OpsTrace creates an `ExceptionRecord` with:
- `exception_code`: Unique identifier (e.g., `EXC-A1B2C3D4`)
- `exception_type`: Standardized discrepancy code
- `severity`: `High`, `Medium`, or `Low`
- `variance_amount`: Exact monetary discrepancy in USD
- `raw_snapshots`: Comprehensive JSON snapshot of the linked `purchase_order`, `goods_receipt`, and `invoice` at the time of evaluation.
- `discrepancy_details`: Diagnostic breakdown with exact numerical deltas.

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
