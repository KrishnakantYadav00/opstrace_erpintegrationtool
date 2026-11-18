from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index
from sqlalchemy.orm import relationship, foreign
from database import Base


def utc_now():
    return datetime.now(timezone.utc)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    po_number = Column(String(50), unique=True, nullable=False, index=True)
    vendor_id = Column(String(50), nullable=False, index=True)
    vendor_name = Column(String(255), nullable=False)
    sku = Column(String(50), nullable=False, index=True)
    description = Column(String(255), nullable=True)
    quantity = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    status = Column(String(50), default="ISSUED", nullable=False)  # ISSUED, PARTIALLY_RECEIVED, RECEIVED, CLOSED, CANCELLED
    order_date = Column(DateTime, nullable=False)
    expected_delivery_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Cross-table ORM links via po_number
    goods_receipts = relationship(
        "GoodsReceipt",
        primaryjoin="PurchaseOrder.po_number == foreign(GoodsReceipt.po_number)",
        backref="purchase_order",
        viewonly=True,
    )
    invoices = relationship(
        "Invoice",
        primaryjoin="PurchaseOrder.po_number == foreign(Invoice.po_number)",
        backref="purchase_order",
        viewonly=True,
    )
    exceptions = relationship(
        "ExceptionRecord",
        primaryjoin="PurchaseOrder.po_number == foreign(ExceptionRecord.po_number)",
        backref="purchase_order",
        viewonly=True,
    )

    def __repr__(self):
        return f"<PurchaseOrder(po_number='{self.po_number}', vendor='{self.vendor_name}', sku='{self.sku}', qty={self.quantity}, unit_price={self.unit_price})>"


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gr_number = Column(String(50), unique=True, nullable=False, index=True)
    po_number = Column(String(50), nullable=False, index=True)
    vendor_id = Column(String(50), nullable=False, index=True)
    sku = Column(String(50), nullable=False, index=True)
    quantity_received = Column(Float, nullable=False)
    received_date = Column(DateTime, nullable=False)
    received_by = Column(String(100), default="Warehouse Intake", nullable=False)
    condition_status = Column(String(50), default="ACCEPTED", nullable=False)  # ACCEPTED, DAMAGED, REJECTED, SHORTAGE
    created_at = Column(DateTime, default=utc_now, nullable=False)

    def __repr__(self):
        return f"<GoodsReceipt(gr_number='{self.gr_number}', po_number='{self.po_number}', qty_received={self.quantity_received})>"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String(50), nullable=False, index=True)  # Not globally unique to support synthetic duplicate invoice scenarios
    po_number = Column(String(50), nullable=True, index=True)  # Nullable for missing PO scenarios
    vendor_id = Column(String(50), nullable=False, index=True)
    vendor_name = Column(String(255), nullable=False)
    sku = Column(String(50), nullable=False, index=True)
    quantity_billed = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    invoice_date = Column(DateTime, nullable=False)
    due_date = Column(DateTime, nullable=True)
    payment_status = Column(String(50), default="PENDING_MATCH", nullable=False)  # PENDING_MATCH, APPROVED, REJECTED, ON_HOLD, PAID
    created_at = Column(DateTime, default=utc_now, nullable=False)

    def __repr__(self):
        return f"<Invoice(invoice_number='{self.invoice_number}', po_number='{self.po_number}', qty_billed={self.quantity_billed}, unit_price={self.unit_price}, total={self.total_amount})>"


class ExceptionRecord(Base):
    __tablename__ = "exceptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exception_code = Column(String(50), unique=True, nullable=False, index=True)
    po_number = Column(String(50), nullable=True, index=True)
    invoice_number = Column(String(50), nullable=True, index=True)
    gr_number = Column(String(50), nullable=True, index=True)
    vendor_id = Column(String(50), nullable=True, index=True)
    exception_type = Column(String(100), nullable=False, index=True)
    # Types:
    # - QTY_MISMATCH_3WAY
    # - PRICE_VARIANCE
    # - DUPLICATE_INVOICE
    # - GHOST_RECEIPT_MISSING_GR
    # - MISSING_PO
    severity = Column(String(20), default="Medium", nullable=False)  # High, Medium, Low, Critical
    variance_amount = Column(Float, nullable=True, default=0.0)
    raw_snapshots = Column(Text, nullable=True)  # Raw JSON snapshots of PO, Receipt, Invoice
    discrepancy_details = Column(Text, nullable=True)  # Structured description or JSON of the variance
    ai_investigation = Column(Text, nullable=True)  # Stored AI investigation report JSON
    status = Column(String(50), default="OPEN", nullable=False)  # OPEN, UNDER_REVIEW, RESOLVED, DISMISSED
    created_at = Column(DateTime, default=utc_now, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ExceptionRecord(code='{self.exception_code}', type='{self.exception_type}', severity='{self.severity}', po='{self.po_number}')>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String(50), nullable=False, index=True)  # PURCHASE_ORDER, GOODS_RECEIPT, INVOICE, EXCEPTION
    entity_id = Column(String(100), nullable=False, index=True)
    action = Column(String(100), nullable=False)  # CREATED, STATE_TRANSITION, EXCEPTION_RESOLVED, etc.
    performed_by = Column(String(100), default="SYSTEM_SEEDER", nullable=False)  # SYSTEM, AI_AGENT, or human user_id
    before_state = Column(String(50), nullable=True, index=True)  # e.g. DETECTED, AI_ANALYZED
    after_state = Column(String(50), nullable=True, index=True)  # e.g. AI_ANALYZED, APPROVED_FOR_PAYMENT
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    details = Column(Text, nullable=True)

    def __repr__(self):
        return f"<AuditLog(entity='{self.entity_type}', id='{self.entity_id}', action='{self.action}', time='{self.timestamp}')>"


# Composite indexes for high performance matching queries
Index("ix_po_vendor_sku", PurchaseOrder.vendor_id, PurchaseOrder.sku)
Index("ix_gr_vendor_sku", GoodsReceipt.vendor_id, GoodsReceipt.sku)
Index("ix_invoice_vendor_sku", Invoice.vendor_id, Invoice.sku)
Index("ix_invoice_vendor_amount_date", Invoice.vendor_id, Invoice.total_amount, Invoice.invoice_date)
