import React, { useState, useEffect } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Copy,
  DollarSign,
  FileText,
  Filter,
  RefreshCw,
  Search,
  Send,
  ShieldAlert,
  Sparkles,
  UserCheck,
  XCircle,
  TrendingDown,
  Layers
} from 'lucide-react';

const API_BASE = 'http://127.0.0.1:8000';

export default function App() {
  const [exceptions, setExceptions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedException, setSelectedException] = useState(null);
  const [investigation, setInvestigation] = useState(null);
  const [auditTrail, setAuditTrail] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [sweepLoading, setSweepLoading] = useState(false);
  const [notification, setNotification] = useState(null);

  // Filters
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState('ALL');
  const [statusFilter, setStatusFilter] = useState('ALL');

  // Human Action inputs
  const [reviewerUser, setReviewerUser] = useState('sarah.chen@enterprise.com');
  const [reviewNotes, setReviewNotes] = useState('');
  const [copiedEmail, setCopiedEmail] = useState(false);

  // Load exceptions
  const fetchExceptions = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE}/exceptions?limit=100`);
      if (!res.ok) throw new Error('Failed to fetch exceptions');
      const data = await res.json();
      setExceptions(data.exceptions || []);
      if (data.exceptions && data.exceptions.length > 0 && !selectedId) {
        setSelectedId(data.exceptions[0].exception_code);
      }
    } catch (err) {
      console.error(err);
      showNotification('Error loading exceptions from API. Is backend running?', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchExceptions();
  }, []);

  // When selected exception changes, fetch investigation & audit trail
  useEffect(() => {
    if (!selectedId) return;
    const exc = exceptions.find((e) => e.exception_code === selectedId);
    setSelectedException(exc);
    setReviewNotes('');

    if (exc) {
      fetchInvestigation(exc.exception_code);
      fetchAuditTrail(exc.exception_code);
    }
  }, [selectedId, exceptions]);

  const fetchInvestigation = async (code) => {
    try {
      setAiLoading(true);
      const res = await fetch(`${API_BASE}/investigate/${code}`);
      if (res.ok) {
        const data = await res.json();
        setInvestigation(data);
      } else {
        setInvestigation(null);
      }
    } catch (err) {
      console.error('Failed to load investigation:', err);
      setInvestigation(null);
    } finally {
      setAiLoading(false);
    }
  };

  const fetchAuditTrail = async (code) => {
    try {
      const res = await fetch(`${API_BASE}/exceptions/${code}/audit`);
      if (res.ok) {
        const data = await res.json();
        setAuditTrail(data);
      }
    } catch (err) {
      console.error('Failed to load audit trail:', err);
    }
  };

  const showNotification = (msg, type = 'success') => {
    setNotification({ msg, type });
    setTimeout(() => setNotification(null), 4000);
  };

  // Human Resolution Handler
  const handleResolveAction = async (actionType) => {
    if (!selectedException) return;
    if (!reviewNotes.trim()) {
      showNotification('Please enter audit notes explaining the resolution rationale.', 'error');
      return;
    }

    try {
      setActionLoading(true);
      const res = await fetch(`${API_BASE}/exceptions/${selectedException.exception_code}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action_taken: actionType,
          user_id: reviewerUser,
          notes: reviewNotes,
        }),
      });

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.detail || 'Resolution failed');
      }

      const result = await res.json();
      showNotification(`Exception successfully resolved: ${actionType} -> ${result.after_state}`);
      setReviewNotes('');
      await fetchExceptions();
      fetchAuditTrail(selectedException.exception_code);
    } catch (err) {
      showNotification(err.message, 'error');
    } finally {
      setActionLoading(false);
    }
  };

  // Trigger 5-Minute Sweep
  const handleTriggerSweep = async () => {
    try {
      setSweepLoading(true);
      const res = await fetch(`${API_BASE}/scheduler/sweep`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showNotification(`Reconciliation sweep completed: ${data.summary?.exceptions_generated || 0} exceptions found`);
        fetchExceptions();
      }
    } catch (err) {
      showNotification('Sweep trigger failed', 'error');
    } finally {
      setSweepLoading(false);
    }
  };

  // Copy email snippet to clipboard
  const handleCopyEmail = () => {
    if (!investigation?.draft_communication) return;
    navigator.clipboard.writeText(investigation.draft_communication);
    setCopiedEmail(true);
    setTimeout(() => setCopiedEmail(false), 2000);
  };

  // Filter & sort exceptions
  const filteredExceptions = exceptions
    .filter((exc) => {
      if (severityFilter !== 'ALL' && exc.severity.toUpperCase() !== severityFilter.toUpperCase()) return false;
      if (statusFilter !== 'ALL' && exc.status.toUpperCase() !== statusFilter.toUpperCase()) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const matchesCode = exc.exception_code?.toLowerCase().includes(q);
        const matchesPo = exc.po_number?.toLowerCase().includes(q);
        const matchesInv = exc.invoice_number?.toLowerCase().includes(q);
        const matchesType = exc.exception_type?.toLowerCase().includes(q);
        const matchesVendor = exc.vendor_id?.toLowerCase().includes(q);
        return matchesCode || matchesPo || matchesInv || matchesType || matchesVendor;
      }
      return true;
    })
    .sort((a, b) => (b.variance_amount || 0) - (a.variance_amount || 0));

  // Snapshot helpers for side-by-side comparison
  const snapshots = selectedException?.raw_snapshots || {};
  const poDoc = snapshots.purchase_order || null;
  const grDoc = snapshots.goods_receipt || null;
  const invDoc = snapshots.invoice || null;

  // Detect mismatches
  const isQtyMismatched =
    (poDoc && grDoc && poDoc.quantity !== grDoc.quantity_received) ||
    (grDoc && invDoc && grDoc.quantity_received !== invDoc.quantity_billed) ||
    (poDoc && invDoc && poDoc.quantity !== invDoc.quantity_billed);

  const isPriceMismatched = poDoc && invDoc && invDoc.unit_price > poDoc.unit_price;
  const isMissingReceipt = invDoc && !grDoc;
  const isMissingPo = invDoc && !poDoc;

  // Stats calculation
  const totalVarianceRisk = exceptions.reduce((sum, e) => sum + (e.variance_amount || 0), 0);
  const openCount = exceptions.filter((e) => ['DETECTED', 'OPEN', 'UNDER_REVIEW', 'AI_ANALYZED'].includes(e.status)).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Top Header */}
      <header className="app-header">
        <div className="logo-section">
          <div className="logo-badge">OPSTRACE</div>
          <div>
            <div className="brand-title">3-Way Match Investigation Workbench</div>
            <div className="brand-sub">ERP Reconciliation, Forensic Audit & Human-in-the-Loop Decisions</div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12 }}>
            <span className="badge badge-high" style={{ fontSize: 12, padding: '4px 10px' }}>
              <ShieldAlert size={14} /> {openCount} Active Exceptions
            </span>
            <span className="badge badge-medium" style={{ fontSize: 12, padding: '4px 10px' }}>
              <TrendingDown size={14} /> Total Risk: ${totalVarianceRisk.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </span>
          </div>

          <button
            onClick={handleTriggerSweep}
            disabled={sweepLoading}
            className="btn btn-secondary"
            title="Run 5-minute automated reconciliation sweep now"
          >
            <RefreshCw size={14} className={sweepLoading ? 'spin' : ''} />
            {sweepLoading ? 'Sweeping...' : '5-Min Sweep'}
          </button>
        </div>
      </header>

      {/* Global Notification */}
      {notification && (
        <div
          style={{
            background: notification.type === 'error' ? '#ef4444' : '#10b981',
            color: 'white',
            padding: '8px 20px',
            fontSize: 13,
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span>{notification.msg}</span>
          <button onClick={() => setNotification(null)} style={{ background: 'none', border: 'none', color: 'white', cursor: 'pointer' }}>
            ✕
          </button>
        </div>
      )}

      {/* 3-Pane Workbench */}
      <div className="workbench-container">
        {/* =================================================================== */}
        {/* PANE 1: EXCEPTION QUEUE (LEFT)                                      */}
        {/* =================================================================== */}
        <section className="pane" style={{ background: 'var(--bg-secondary)' }}>
          <div className="pane-header">
            <span className="pane-title">
              <Layers size={16} /> Exception Queue ({filteredExceptions.length})
            </span>
            <button onClick={fetchExceptions} className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: 11 }}>
              <RefreshCw size={12} /> Refresh
            </button>
          </div>

          {/* Filters & Search */}
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: 10, color: 'var(--text-faint)' }} />
              <input
                type="text"
                className="search-input"
                style={{ paddingLeft: 30 }}
                placeholder="Search PO, invoice, vendor, code..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <select className="filter-select" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
                <option value="ALL">All Severities</option>
                <option value="HIGH">High / Critical</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>

              <select className="filter-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="ALL">All Statuses</option>
                <option value="DETECTED">Detected / Open</option>
                <option value="AI_ANALYZED">AI Analyzed</option>
                <option value="APPROVED_FOR_PAYMENT">Approved for Pay</option>
                <option value="CREDIT_MEMO_REQUESTED">Credit Memo</option>
                <option value="ESCALATED">Escalated</option>
              </select>
            </div>
          </div>

          {/* List of Exceptions */}
          <div className="pane-body">
            {loading ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>Loading exceptions...</div>
            ) : filteredExceptions.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-faint)' }}>No matching exceptions found.</div>
            ) : (
              filteredExceptions.map((exc) => {
                const isSelected = exc.exception_code === selectedId;
                const isHigh = exc.severity?.toUpperCase() === 'HIGH' || exc.severity?.toUpperCase() === 'CRITICAL';
                return (
                  <div
                    key={exc.exception_code}
                    className={`queue-card ${isSelected ? 'active' : ''}`}
                    onClick={() => setSelectedId(exc.exception_code)}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                      <span className={`badge ${isHigh ? 'badge-high' : 'badge-medium'}`}>{exc.exception_type}</span>
                      <span style={{ fontSize: 13, fontWeight: 700, color: isHigh ? '#f87171' : '#fbbf24' }}>
                        ${(exc.variance_amount || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                      </span>
                    </div>

                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-main)', marginBottom: 4 }}>
                      {exc.invoice_number ? `INV: ${exc.invoice_number}` : 'No Invoice'} • {exc.po_number || 'Missing PO'}
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11, color: 'var(--text-muted)' }}>
                      <span>{exc.vendor_id || 'Vendor N/A'}</span>
                      <span
                        className="badge"
                        style={{
                          background: exc.status === 'APPROVED_FOR_PAYMENT' ? 'var(--status-green-bg)' : 'rgba(255,255,255,0.05)',
                          color: exc.status === 'APPROVED_FOR_PAYMENT' ? 'var(--status-green-text)' : 'var(--text-muted)',
                        }}
                      >
                        {exc.status}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </section>

        {/* =================================================================== */}
        {/* PANE 2: EVIDENCE & TIMELINE (CENTER)                                */}
        {/* =================================================================== */}
        <section className="pane" style={{ background: 'var(--bg-primary)' }}>
          <div className="pane-header">
            <span className="pane-title">
              <FileText size={16} /> Evidence & Side-by-Side Comparison: {selectedException?.exception_code || 'None Selected'}
            </span>
            {selectedException && (
              <span className={`badge ${selectedException.severity?.toUpperCase() === 'HIGH' ? 'badge-high' : 'badge-medium'}`}>
                {selectedException.severity} Severity
              </span>
            )}
          </div>

          <div className="pane-body" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {!selectedException ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Select an exception from the queue to view evidence.</div>
            ) : (
              <>
                {/* Visual Mismatch Alert Banner */}
                {(isQtyMismatched || isPriceMismatched || isMissingReceipt || isMissingPo) && (
                  <div
                    style={{
                      background: 'var(--mismatch-bg)',
                      border: '1px solid var(--mismatch-border)',
                      borderRadius: 8,
                      padding: '12px 16px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      color: '#fecaca',
                    }}
                  >
                    <AlertTriangle size={20} style={{ color: '#ef4444', flexShrink: 0 }} />
                    <div style={{ fontSize: 13 }}>
                      <strong>Discrepancy Highlighted:</strong>{' '}
                      {isQtyMismatched && 'Quantities do not match across procurement documents. '}
                      {isPriceMismatched && 'Billed unit price exceeds contracted PO price. '}
                      {isMissingReceipt && 'Invoice exists with zero Goods Receipt on file (> 7 days). '}
                      {isMissingPo && 'Invoice references an invalid or non-existent purchase order. '}
                    </div>
                  </div>
                )}

                {/* 3-Column Side-by-Side Comparison */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
                  {/* Column 1: Purchase Order */}
                  <div className="doc-card">
                    <div className="doc-card-header" style={{ color: '#93c5fd' }}>
                      <span>1. Purchase Order (PO)</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{poDoc?.po_number || 'MISSING'}</span>
                    </div>
                    {poDoc ? (
                      <div>
                        <div className={`comparison-field ${isQtyMismatched ? 'mismatched' : ''}`}>
                          <span style={{ color: 'var(--text-muted)' }}>Quantity:</span>
                          <span>{poDoc.quantity} units</span>
                        </div>
                        <div className={`comparison-field ${isPriceMismatched ? 'mismatched' : ''}`}>
                          <span style={{ color: 'var(--text-muted)' }}>Unit Price:</span>
                          <span>${Number(poDoc.unit_price).toFixed(2)}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Total Contract:</span>
                          <span>${Number(poDoc.total_amount).toFixed(2)}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>SKU:</span>
                          <span>{poDoc.sku}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Status:</span>
                          <span>{poDoc.status}</span>
                        </div>
                      </div>
                    ) : (
                      <div style={{ padding: 20, textAlign: 'center', color: '#f87171', fontSize: 13, fontWeight: 600 }}>
                        <XCircle size={24} style={{ marginBottom: 6 }} />
                        <div>PO Missing on Record</div>
                      </div>
                    )}
                  </div>

                  {/* Column 2: Goods Receipt */}
                  <div className="doc-card">
                    <div className="doc-card-header" style={{ color: '#6ee7b7' }}>
                      <span>2. Goods Receipt (GR)</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{grDoc?.gr_number || 'NONE'}</span>
                    </div>
                    {grDoc ? (
                      <div>
                        <div className={`comparison-field ${isQtyMismatched ? 'mismatched' : ''}`}>
                          <span style={{ color: 'var(--text-muted)' }}>Received Qty:</span>
                          <span>{grDoc.quantity_received} units</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Condition:</span>
                          <span style={{ color: grDoc.condition_status === 'SHORTAGE' ? '#f87171' : 'inherit' }}>
                            {grDoc.condition_status}
                          </span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Received By:</span>
                          <span>{grDoc.received_by || 'Dock Intake'}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>SKU:</span>
                          <span>{grDoc.sku}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Date:</span>
                          <span>{grDoc.received_date ? grDoc.received_date.split('T')[0] : 'N/A'}</span>
                        </div>
                      </div>
                    ) : (
                      <div style={{ padding: 20, textAlign: 'center', color: '#f87171', fontSize: 13, fontWeight: 600 }}>
                        <XCircle size={24} style={{ marginBottom: 6 }} />
                        <div>No Goods Receipt Logged</div>
                        <div style={{ fontSize: 11, fontWeight: 400, marginTop: 4, color: 'var(--text-muted)' }}>
                          Dock intake not recorded
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Column 3: Invoice */}
                  <div className="doc-card">
                    <div className="doc-card-header" style={{ color: '#fcd34d' }}>
                      <span>3. Vendor Invoice</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{invDoc?.invoice_number || 'NONE'}</span>
                    </div>
                    {invDoc ? (
                      <div>
                        <div className={`comparison-field ${isQtyMismatched ? 'mismatched' : ''}`}>
                          <span style={{ color: 'var(--text-muted)' }}>Billed Qty:</span>
                          <span>{invDoc.quantity_billed} units</span>
                        </div>
                        <div className={`comparison-field ${isPriceMismatched ? 'mismatched' : ''}`}>
                          <span style={{ color: 'var(--text-muted)' }}>Billed Unit Price:</span>
                          <span>${Number(invDoc.unit_price).toFixed(2)}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Billed Total:</span>
                          <span style={{ fontWeight: 700 }}>${Number(invDoc.total_amount).toFixed(2)}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Vendor:</span>
                          <span>{invDoc.vendor_name || invDoc.vendor_id}</span>
                        </div>
                        <div className="comparison-field">
                          <span style={{ color: 'var(--text-muted)' }}>Payment Status:</span>
                          <span className="badge" style={{ background: 'rgba(245, 158, 11, 0.2)', color: '#fbbf24' }}>
                            {invDoc.payment_status}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                        No invoice data available.
                      </div>
                    )}
                  </div>
                </div>

                {/* Chronological Audit Log & State Transitions */}
                <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, fontWeight: 600 }}>
                    <Clock size={16} style={{ color: 'var(--accent-cyan)' }} />
                    <span>Chronological Immutable Audit Trail</span>
                  </div>

                  {auditTrail.length === 0 ? (
                    <div style={{ color: 'var(--text-faint)', fontSize: 13 }}>No audit logs recorded for this exception.</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      {auditTrail.map((entry, idx) => {
                        const isStateTransition = entry.action === 'STATE_TRANSITION';
                        return (
                          <div key={idx} className="timeline-item">
                            <div className="timeline-bullet" />
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                              <span style={{ fontWeight: 600, color: 'var(--text-main)', fontSize: 13 }}>
                                {entry.performed_by}{' '}
                                <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>({entry.action})</span>
                              </span>
                              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{entry.timestamp}</span>
                            </div>

                            {isStateTransition && entry.after_state && (
                              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, margin: '4px 0' }}>
                                <span className="badge badge-low">{entry.before_state || 'DETECTED'}</span>
                                <span style={{ color: 'var(--text-faint)' }}>→</span>
                                <span className="badge badge-resolved">{entry.after_state}</span>
                              </div>
                            )}

                            {entry.details && (
                              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 4 }}>
                                {typeof entry.details === 'object'
                                  ? entry.details.notes || JSON.stringify(entry.details)
                                  : entry.details}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </section>

        {/* =================================================================== */}
        {/* PANE 3: AI COPILOT & ACTION BAR (RIGHT)                             */}
        {/* =================================================================== */}
        <section className="pane" style={{ background: 'var(--bg-secondary)' }}>
          <div className="pane-header">
            <span className="pane-title" style={{ color: '#a78bfa' }}>
              <Sparkles size={16} /> AI Operations Copilot
            </span>
            <button
              onClick={() => selectedException && fetchInvestigation(selectedException.exception_code)}
              disabled={aiLoading || !selectedException}
              className="btn btn-secondary"
              style={{ padding: '4px 8px', fontSize: 11 }}
            >
              <RefreshCw size={12} className={aiLoading ? 'spin' : ''} /> {aiLoading ? 'Analyzing...' : 'Re-Analyze'}
            </button>
          </div>

          <div className="pane-body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {aiLoading ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
                <Sparkles size={24} style={{ animation: 'pulse 1.5s infinite', marginBottom: 8, color: '#a78bfa' }} />
                <div>Analyzing documents with Gemini 3.8 Flash...</div>
              </div>
            ) : !investigation ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
                No AI analysis yet. Select an exception or click Re-Analyze.
              </div>
            ) : (
              <>
                {/* 1. Plain-English Root Cause Summary */}
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 6 }}>
                    Root-Cause Analysis
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.5, color: '#f1f5f9' }}>
                    {investigation.root_cause_summary}
                  </div>
                </div>

                {/* 2. Recommended Next Steps */}
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 8 }}>
                    Recommended Next Steps
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {investigation.recommended_actions?.map((act, i) => (
                      <div key={i} style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'flex-start' }}>
                        <CheckCircle2 size={16} style={{ color: '#10b981', flexShrink: 0, marginTop: 2 }} />
                        <span>{act}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* 3. Pre-Drafted Vendor / Approver Email */}
                <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                      Pre-Drafted Vendor Email
                    </span>
                    <button
                      onClick={handleCopyEmail}
                      className="btn btn-secondary"
                      style={{ padding: '3px 8px', fontSize: 11 }}
                    >
                      <Copy size={12} /> {copiedEmail ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  <textarea
                    readOnly
                    className="textarea-input"
                    value={investigation.draft_communication || ''}
                    style={{ fontSize: 12, height: 110, background: 'var(--bg-primary)' }}
                  />
                </div>

                {/* 4. Human Action Bar */}
                <div style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-strong)', borderRadius: 8, padding: 14, marginTop: 'auto' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 13, marginBottom: 10 }}>
                    <UserCheck size={16} style={{ color: 'var(--accent-blue)' }} />
                    <span>Human-in-the-Loop Decision Execution</span>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div>
                      <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>Reviewer Identity</label>
                      <input
                        type="text"
                        className="search-input"
                        value={reviewerUser}
                        onChange={(e) => setReviewerUser(e.target.value)}
                        placeholder="user.id@enterprise.com"
                      />
                    </div>

                    <div>
                      <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>Audit Rationale / Notes *</label>
                      <textarea
                        className="textarea-input"
                        style={{ height: 60 }}
                        placeholder="e.g. Approved price variance per category lead authorization..."
                        value={reviewNotes}
                        onChange={(e) => setReviewNotes(e.target.value)}
                      />
                    </div>

                    {/* One-Click Action Buttons */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 4 }}>
                      <button
                        onClick={() => handleResolveAction('OVERRIDE_AND_PAY')}
                        disabled={actionLoading || selectedException?.status === 'APPROVED_FOR_PAYMENT'}
                        className="btn btn-success"
                        title="Approve override and set invoice to APPROVED"
                      >
                        Approve Override
                      </button>

                      <button
                        onClick={() => handleResolveAction('REQUEST_CREDIT_MEMO')}
                        disabled={actionLoading}
                        className="btn btn-warning"
                        title="Request credit memo and hold invoice"
                      >
                        Request Credit Memo
                      </button>
                    </div>

                    <button
                      onClick={() => handleResolveAction('ESCALATE')}
                      disabled={actionLoading}
                      className="btn btn-danger"
                      style={{ marginTop: 4 }}
                      title="Escalate issue to procurement category director"
                    >
                      Hold Payment & Escalate
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
