"""
CSV Parser & Normalizer for OpsTrace ERP bulk ingestion.
Supports flexible header aliasing, BOM handling, and dictionary normalization.
"""

import csv
import io
from typing import List, Dict, Any, Tuple

# Header aliases dictionary for common ERP column variations
HEADER_ALIASES = {
    # PO aliases
    "po_no": "po_number",
    "ponumber": "po_number",
    "po_id": "po_number",
    "purchase_order": "po_number",
    "purchase_order_number": "po_number",
    "vendor": "vendor_name",
    "supplier_id": "vendor_id",
    "supplier_name": "vendor_name",
    "item_sku": "sku",
    "item_code": "sku",
    "item_description": "description",
    "qty": "quantity",
    "order_qty": "quantity",
    "price": "unit_price",
    "rate": "unit_price",
    "total": "total_amount",
    "po_total": "total_amount",
    "po_date": "order_date",
    "date_ordered": "order_date",
    "delivery_date": "expected_delivery_date",
    # GR aliases
    "receipt_no": "gr_number",
    "gr_no": "gr_number",
    "goods_receipt_number": "gr_number",
    "received_quantity": "quantity_received",
    "qty_received": "quantity_received",
    "intake_date": "received_date",
    "date_received": "received_date",
    "condition": "condition_status",
    "inspector": "received_by",
    # Invoice aliases
    "inv_no": "invoice_number",
    "inv_number": "invoice_number",
    "invoice_no": "invoice_number",
    "billed_qty": "quantity_billed",
    "qty_billed": "quantity_billed",
    "billed_amount": "total_amount",
    "inv_date": "invoice_date",
    "date_invoiced": "invoice_date",
    "status": "payment_status",
}


def normalize_header(header: str) -> str:
    """Normalize header string: lowercased, spaces/hyphens converted to underscores."""
    h = header.strip().lower().replace(" ", "_").replace("-", "_")
    return HEADER_ALIASES.get(h, h)


def parse_csv_content(csv_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parse CSV bytes into a list of normalized dictionaries.
    Handles UTF-8 and UTF-8-SIG (Excel BOM).
    """
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = csv_bytes.decode("latin-1")

    f = io.StringIO(text.strip())
    reader = csv.reader(f)

    try:
        raw_headers = next(reader)
    except StopIteration:
        return []

    headers = [normalize_header(h) for h in raw_headers]
    rows = []

    for row in reader:
        # Skip completely blank lines
        if not any(cell.strip() for cell in row):
            continue

        row_dict = {}
        for i, val in enumerate(row):
            if i < len(headers):
                cleaned_val = val.strip()
                # Empty string is treated as None
                row_dict[headers[i]] = cleaned_val if cleaned_val != "" else None
        rows.append(row_dict)

    return rows
