import { useState, useEffect, useCallback, useRef } from "react";

// ── CSS (same dark theme as before) ─────────────────────────────────────────
const CSS = `
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Syne:wght@400;600;700;800&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:#0d0f14; --surface:#141720; --card:#1a1e2b; --border:#252a3a;
    --accent:#4f6ef7; --accent2:#7c3aed; --success:#22c55e;
    --warning:#f59e0b; --danger:#ef4444; --text:#e8ecf5; --muted:#5a6480;
    --font-head:'Syne',sans-serif; --font-mono:'JetBrains Mono',monospace;
  }
  body { background:var(--bg); color:var(--text); font-family:var(--font-head); }
  ::-webkit-scrollbar{width:6px;height:6px}
  ::-webkit-scrollbar-track{background:var(--surface)}
  ::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
  .app{display:flex;height:100vh;overflow:hidden;
    background:radial-gradient(ellipse 80% 60% at 10% 0%,#1a1f3a 0%,transparent 60%),
               radial-gradient(ellipse 60% 50% at 90% 100%,#1a0a2e 0%,transparent 60%),var(--bg)}
  .sidebar{width:220px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
  .sidebar-logo{padding:20px 20px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
  .logo-icon{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
  .logo-text{font-size:15px;font-weight:800;letter-spacing:-0.3px}
  .logo-sub{font-size:10px;color:var(--muted);font-family:var(--font-mono);margin-top:1px}
  .sidebar-section{padding:16px 12px 6px}
  .sidebar-label{font-size:10px;font-family:var(--font-mono);color:var(--muted);letter-spacing:1px;text-transform:uppercase;padding:0 8px;margin-bottom:4px}
  .nav-item{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:7px;cursor:pointer;font-size:13px;font-weight:600;color:var(--muted);transition:all .15s;border:1px solid transparent}
  .nav-item:hover{background:var(--card);color:var(--text)}
  .nav-item.active{background:rgba(79,110,247,.12);color:var(--accent);border-color:rgba(79,110,247,.2)}
  .nav-item .badge{margin-left:auto;background:var(--accent);color:#fff;font-size:10px;font-family:var(--font-mono);padding:1px 6px;border-radius:10px;font-weight:600}
  .nav-item .badge.warn{background:var(--warning);color:#000}
  .nav-item .badge.ok{background:var(--success)}
  .conn-status{margin:auto 0 0;padding:12px 14px;border-top:1px solid var(--border);font-size:11px;font-family:var(--font-mono)}
  .conn-dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;background:var(--muted)}
  .conn-dot.connected{background:var(--success);box-shadow:0 0 6px var(--success);animation:pulse 2s infinite}
  .conn-dot.connecting{background:var(--warning);animation:pulse 1s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .main{flex:1;display:flex;flex-direction:column;overflow:hidden}
  .topbar{height:56px;flex-shrink:0;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;padding:0 24px;background:rgba(20,23,32,.8);backdrop-filter:blur(8px)}
  .topbar-title{font-size:18px;font-weight:800}
  .topbar-actions{margin-left:auto;display:flex;gap:8px}
  .btn{display:flex;align-items:center;gap:6px;padding:7px 14px;border-radius:7px;border:1px solid var(--border);background:var(--card);color:var(--text);font-family:var(--font-head);font-size:12px;font-weight:600;cursor:pointer;transition:all .15s}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .btn.primary:hover{background:#3d5de0;border-color:#3d5de0}
  .btn.danger{background:rgba(239,68,68,.1);border-color:var(--danger);color:var(--danger)}
  .btn.sm{padding:5px 10px;font-size:11px}
  .btn:disabled{opacity:.4;cursor:not-allowed}
  .content{flex:1;overflow-y:auto;padding:24px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;transition:border-color .2s}
  .card:hover{border-color:#333a50}
  .card-title{font-size:14px;font-weight:700;margin-bottom:4px}
  .card-sub{font-size:12px;color:var(--muted)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
  .stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px 20px;position:relative;overflow:hidden}
  .stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent2))}
  .stat-val{font-size:28px;font-weight:800;font-family:var(--font-mono);line-height:1;margin:6px 0 4px}
  .stat-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.8px}
  .stat-delta{font-size:11px;font-family:var(--font-mono);margin-top:4px}
  .stat-delta.up{color:var(--success)}
  .form-group{margin-bottom:14px}
  .form-label{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:6px;display:block}
  .form-input,.form-select{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:9px 12px;color:var(--text);font-family:var(--font-head);font-size:13px;outline:none;transition:border-color .15s}
  .form-input:focus,.form-select:focus{border-color:var(--accent)}
  .form-select option{background:var(--card)}
  .form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .table-wrap{overflow-x:auto}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:9px 12px;font-size:10px;font-family:var(--font-mono);color:var(--muted);text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid var(--border);font-weight:600}
  td{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:rgba(255,255,255,.02)}
  .printer-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;display:flex;align-items:center;gap:12px;transition:all .2s}
  .printer-card:hover{border-color:var(--accent)}
  .printer-icon{width:40px;height:40px;border-radius:8px;flex-shrink:0;background:rgba(79,110,247,.12);display:flex;align-items:center;justify-content:center;font-size:20px}
  .printer-name{font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .printer-meta{font-size:11px;color:var(--muted);font-family:var(--font-mono);margin-top:2px}
  .pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:20px;font-size:11px;font-family:var(--font-mono);font-weight:600}
  .pill.online{background:rgba(34,197,94,.12);color:var(--success);border:1px solid rgba(34,197,94,.2)}
  .pill.offline{background:rgba(90,100,128,.1);color:var(--muted);border:1px solid var(--border)}
  .pill.error{background:rgba(239,68,68,.1);color:var(--danger);border:1px solid rgba(239,68,68,.2)}
  .pill.pdf{background:rgba(79,110,247,.12);color:var(--accent);border:1px solid rgba(79,110,247,.2)}
  .pill.zpl{background:rgba(124,58,237,.12);color:#a78bfa;border:1px solid rgba(124,58,237,.2)}
  .pill.warn{background:rgba(245,158,11,.12);color:var(--warning);border:1px solid rgba(245,158,11,.2)}
  .log-entry{display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);font-size:12px}
  .log-entry:last-child{border-bottom:none}
  .log-time{font-family:var(--font-mono);color:var(--muted);flex-shrink:0;width:70px}
  .scenario-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;display:flex;align-items:center;gap:12px}
  .toggle{width:36px;height:20px;border-radius:10px;border:none;cursor:pointer;background:var(--border);position:relative;transition:background .2s;flex-shrink:0}
  .toggle.on{background:var(--accent)}
  .toggle::after{content:'';position:absolute;top:3px;left:3px;width:14px;height:14px;border-radius:50%;background:#fff;transition:transform .2s}
  .toggle.on::after{transform:translateX(16px)}
  .connect-screen{flex:1;display:flex;align-items:center;justify-content:center;padding:40px}
  .connect-box{width:100%;max-width:420px;background:var(--card);border:1px solid var(--border);border-radius:16px;padding:32px}
  .connect-logo{text-align:center;margin-bottom:28px}
  .alert{padding:10px 14px;border-radius:8px;font-size:12px;display:flex;align-items:center;gap:8px;margin-top:12px}
  .alert.error{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.2);color:#fca5a5}
  .alert.success{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.2);color:#86efac}
  .section-header{display:flex;align-items:center;gap:12px;margin-bottom:18px}
  .section-header h2{font-size:16px;font-weight:800}
  .section-header p{font-size:12px;color:var(--muted);font-family:var(--font-mono)}
  .section-header .actions{margin-left:auto;display:flex;gap:8px}
  .empty{text-align:center;padding:48px 0;color:var(--muted)}
  .empty .icon{font-size:40px;margin-bottom:12px}
  .modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(4px);display:flex;align-items:center;justify-content:center;z-index:100;padding:20px}
  .modal{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:26px;width:100%;max-width:480px;max-height:90vh;overflow-y:auto;animation:slideUp .2s ease}
  @keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
  .modal-title{font-size:16px;font-weight:800;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between}
  .modal-close{cursor:pointer;background:none;border:none;color:var(--muted);font-size:18px}
  .modal-footer{display:flex;gap:8px;justify-content:flex-end;margin-top:20px}
  .toast-container{position:fixed;bottom:20px;right:20px;display:flex;flex-direction:column;gap:8px;z-index:200}
  .toast{padding:10px 16px;border-radius:8px;font-size:12px;font-family:var(--font-mono);display:flex;align-items:center;gap:8px;min-width:220px;background:var(--card);border:1px solid var(--border);box-shadow:0 4px 20px rgba(0,0,0,.4);animation:toastIn .2s ease}
  @keyframes toastIn{from{transform:translateX(30px);opacity:0}to{transform:translateX(0);opacity:1}}
  .toast.success{border-color:rgba(34,197,94,.3)}
  .toast.error{border-color:rgba(239,68,68,.3)}
  .toast.info{border-color:rgba(79,110,247,.3)}
  .spin{animation:spin .7s linear infinite;display:inline-block}
  @keyframes spin{to{transform:rotate(360deg)}}
  .loading{display:flex;align-items:center;justify-content:center;padding:60px;color:var(--muted);font-family:var(--font-mono);gap:10px}
`;

// ── Odoo JSON-RPC client ─────────────────────────────────────────────────────
function createOdooClient(baseUrl, sessionCookie) {
  const call = async (endpoint, params = {}) => {
    const res = await fetch(`${baseUrl}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ jsonrpc: "2.0", method: "call", id: Date.now(), params }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.error) throw new Error(json.error.data?.message || json.error.message || "RPC error");
    return json.result;
  };

  // Auth
  const getSession = async () => {
    try {
      return await call("/web/session/get_session_info", {});
    } catch {
      return null;
    }
  };
  const authenticate = async (db, login, password) => {
    const result = await call("/web/session/authenticate", { db, login, password });
    if (!result?.uid) throw new Error("Authentication failed — check credentials.");
    return result;
  };

  // IAG Direct Print endpoints
  const print = {
    listPrinters:    ()           => call("/iag/print/printers"),
    checkStatus:     (ids)        => call("/iag/print/printers/check_status", { printer_ids: ids }),
    testPrinter:     (id)         => call("/iag/print/printers/test", { printer_id: id }),
    createPrinter:   (vals)       => call("/iag/print/printers/create", { vals }),
    writePrinter:    (id, vals)   => call("/iag/print/printers/write", { printer_id: id, vals }),
    deletePrinter:   (id)         => call("/iag/print/printers/delete", { printer_id: id }),

    listReports:     ()           => call("/iag/print/reports"),

    sendJob:         (p)          => call("/iag/print/send", p),

    listScenarios:   ()           => call("/iag/print/scenarios"),
    createScenario:  (vals)       => call("/iag/print/scenarios/create", { vals }),
    updateScenario:  (id, vals)   => call("/iag/print/scenarios/update", { scenario_id: id, vals }),
    toggleScenario:  (id, active) => call("/iag/print/scenarios/toggle", { scenario_id: id, active }),
    deleteScenario:  (id)         => call("/iag/print/scenarios/delete", { scenario_id: id }),

    listUserRules:   ()           => call("/iag/print/user_rules"),
    saveUserRule:    (vals)       => call("/iag/print/user_rules/save", { vals }),
    updateUserRule:  (id, vals)   => call("/iag/print/user_rules/update", { rule_id: id, vals }),
    deleteUserRule:  (id)         => call("/iag/print/user_rules/delete", { rule_id: id }),

    listJobs:        (limit)      => call("/iag/print/jobs", { limit: limit || 100 }),
  };

  // Helpers
  const searchRead = (model, domain, fields, limit = 100) =>
    call("/web/dataset/call_kw", {
      model, method: "search_read",
      args: [domain], kwargs: { fields, limit },
    });

  return { getSession, authenticate, print, searchRead, call };
}

// ── Hooks ─────────────────────────────────────────────────────────────────────
function useToasts() {
  const [toasts, setToasts] = useState([]);
  const add = useCallback((msg, type = "info") => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, msg, type }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3500);
  }, []);
  return { toasts, add };
}

function useOdooData(fetcher, deps = []) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetcher();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, deps);
  useEffect(() => { refresh(); }, [refresh]);
  return { data, loading, error, refresh };
}

// ── Helper components ─────────────────────────────────────────────────────────
function Toggle({ on, onToggle }) {
  return <button className={`toggle ${on ? "on" : ""}`} onClick={onToggle} />;
}
function Pill({ type, label }) {
  return <span className={`pill ${type}`}>{label}</span>;
}
function Loading() {
  return <div className="loading"><span className="spin">⟳</span> Loading…</div>;
}
function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return iso; }
}
function fmtBytes(b) {
  if (!b) return "—";
  return b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB`
       : b > 1024 ? `${(b / 1024).toFixed(1)} KB`
       : `${b} B`;
}

// ── VIEWS ─────────────────────────────────────────────────────────────────────

function DashboardView({ client, addToast }) {
  const { data: printers } = useOdooData(() => client.print.listPrinters(), []);
  const { data: jobs }     = useOdooData(() => client.print.listJobs(20), []);
  const { data: scenarios } = useOdooData(() => client.print.listScenarios(), []);

  if (!printers || !jobs || !scenarios) return <Loading />;

  const online   = printers.filter(p => p.status === "online").length;
  const failed   = jobs.filter(j => j.state === "failed").length;
  const active   = scenarios.filter(s => s.active).length;
  const todayJobs = jobs.length;

  return (
    <div>
      <div className="section-header">
        <div><h2>Dashboard</h2><p>Live overview — connected to Odoo</p></div>
      </div>

      <div className="grid3" style={{ marginBottom: 20 }}>
        <div className="stat-card">
          <div className="stat-label">Printers Online</div>
          <div className="stat-val" style={{ color: "var(--success)" }}>{online}</div>
          <div className="stat-delta up">{online} of {printers.length} reachable</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Recent Jobs</div>
          <div className="stat-val">{todayJobs}</div>
          <div className="stat-delta" style={{ color: failed ? "var(--danger)" : "var(--success)" }}>
            {failed ? `⚠ ${failed} failed` : "✓ All successful"}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Active Scenarios</div>
          <div className="stat-val" style={{ color: "var(--accent)" }}>{active}</div>
          <div className="stat-delta">{scenarios.length} total configured</div>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <div className="card-title">🖨️ Printer Status</div>
          <div className="card-sub" style={{ marginBottom: 14 }}>Live from Odoo server</div>
          {printers.length === 0
            ? <div className="empty"><div className="icon">🖨️</div><p>No printers configured yet</p></div>
            : printers.map(p => (
              <div key={p.id} className="printer-card" style={{ marginBottom: 8 }}>
                <div className="printer-icon">{p.printer_type?.includes("zpl") ? "🏷️" : "📄"}</div>
                <div style={{ flex: 1 }}>
                  <div className="printer-name">{p.name}</div>
                  <div className="printer-meta">{p.location || "—"} · {p.host || p.cups_name || "—"}</div>
                </div>
                <Pill type={p.status === "online" ? "online" : p.status === "error" ? "error" : "offline"} label={p.status} />
              </div>
            ))
          }
        </div>

        <div className="card">
          <div className="card-title">📋 Recent Jobs</div>
          <div className="card-sub" style={{ marginBottom: 14 }}>Last {jobs.length} print events</div>
          {jobs.length === 0
            ? <div className="empty"><div className="icon">📋</div><p>No jobs yet</p></div>
            : jobs.slice(0, 10).map(j => (
              <div key={j.id} className="log-entry">
                <span className="log-time">{fmtTime(j.time)}</span>
                <span style={{ fontSize: 14 }}>{j.state === "done" ? "✅" : j.state === "failed" ? "❌" : "⏳"}</span>
                <div style={{ flex: 1, fontSize: 12 }}>
                  <span style={{ fontWeight: 700 }}>{j.name}</span><br />
                  <span style={{ color: "var(--muted)", fontFamily: "var(--font-mono)" }}>→ {j.printer}</span>
                </div>
              </div>
            ))
          }
        </div>
      </div>
    </div>
  );
}

const EMPTY_PRINTER_FORM = { name: "", printer_type: "zpl_raw", location: "", host: "", port: 9100, cups_name: "", cups_host: "localhost", cups_port: 631, paper_format: "A4", dpi: 203, copies: 1 };

function PrintersView({ client, addToast }) {
  const { data: printers, refresh, loading } = useOdooData(() => client.print.listPrinters(), []);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [form, setForm] = useState(EMPTY_PRINTER_FORM);

  const openAdd = () => { setEditingId(null); setForm(EMPTY_PRINTER_FORM); setShowAdd(true); };
  const openEdit = (p) => {
    setEditingId(p.id);
    setForm({
      name: p.name, printer_type: p.printer_type || "zpl_raw", location: p.location || "",
      host: p.host || "", port: p.port ?? 9100, cups_name: p.cups_name || "", cups_host: p.cups_host || "localhost",
      cups_port: p.cups_port ?? 631, paper_format: p.paper_format || "A4", dpi: p.dpi ?? 203, copies: p.copies ?? 1,
    });
    setShowAdd(true);
  };

  const savePrinter = async () => {
    if (!form.name) return;
    setSaving(true);
    try {
      if (editingId) {
        await client.print.writePrinter(editingId, form);
        addToast(`Printer "${form.name}" updated`, "success");
      } else {
        await client.print.createPrinter(form);
        addToast(`Printer "${form.name}" added`, "success");
      }
      setShowAdd(false);
      setForm(EMPTY_PRINTER_FORM);
      setEditingId(null);
      refresh();
    } catch (e) {
      addToast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  const removePrinter = async (p) => {
    try {
      await client.print.deletePrinter(p.id);
      addToast(`${p.name} removed`, "error");
      refresh();
    } catch (e) { addToast(e.message, "error"); }
  };

  const testPrinter = async (p) => {
    addToast(`Sending test to ${p.name}…`, "info");
    try {
      const r = await client.print.testPrinter(p.id);
      if (r.success) addToast(`✓ Test sent to ${p.name}`, "success");
      else addToast(`Test failed: ${r.error}`, "error");
    } catch (e) { addToast(e.message, "error"); }
  };

  const checkAll = async () => {
    setChecking(true);
    try {
      await client.print.checkStatus();
      await refresh();
      addToast("Status refreshed", "success");
    } catch (e) { addToast(e.message, "error"); }
    finally { setChecking(false); }
  };

  const PRINTER_TYPES = [
    { value: "zpl_raw", label: "ZPL Raw Socket (Zebra/Thermal)" },
    { value: "pdf_cups", label: "PDF via CUPS" },
    { value: "pdf_raw", label: "PDF via Raw Socket" },
    { value: "ipp",     label: "IPP (Internet Printing Protocol)" },
  ];

  if (loading) return <Loading />;

  return (
    <div>
      <div className="section-header">
        <div><h2>Printers</h2><p>{printers?.length || 0} configured</p></div>
        <div className="actions">
          <button className="btn sm" onClick={checkAll} disabled={checking}>
            {checking ? <><span className="spin">⟳</span> Checking…</> : "⟳ Check Status"}
          </button>
          <button className="btn primary sm" onClick={openAdd}>+ Add Printer</button>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {(printers || []).map(p => (
          <div key={p.id} className="card" style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div className="printer-icon" style={{ width: 48, height: 48, fontSize: 24 }}>
              {p.printer_type?.includes("zpl") ? "🏷️" : "📄"}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>{p.name}</span>
                <Pill type={p.status === "online" ? "online" : p.status === "error" ? "error" : "offline"} label={p.status} />
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", display: "flex", gap: 16, flexWrap: "wrap" }}>
                <span>📍 {p.location || "—"}</span>
                <span>🌐 {p.host || p.cups_name || "—"}:{p.port}</span>
                <span>🖨 {p.dpi} DPI</span>
                <span>📐 {p.paper_format || "—"}</span>
                {p.last_error && <span style={{ color: "var(--danger)" }}>⚠ {p.last_error}</span>}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
              <button className="btn sm" onClick={() => openEdit(p)}>Edit</button>
              <button className="btn sm" onClick={() => testPrinter(p)}>Test Print</button>
              <button className="btn sm danger" onClick={() => removePrinter(p)}>Remove</button>
            </div>
          </div>
        ))}
        {(printers || []).length === 0 && (
          <div className="empty"><div className="icon">🖨️</div><p>No printers yet. Add one to get started.</p></div>
        )}
      </div>

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-title">
              {editingId ? "Edit Printer" : "Add Printer"}
              <button className="modal-close" onClick={() => setShowAdd(false)}>✕</button>
            </div>
            <div className="form-group">
              <label className="form-label">Name</label>
              <input className="form-input" placeholder="e.g. Zebra ZT410 - Shipping" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Connection Type</label>
              <select className="form-select" value={form.printer_type} onChange={e => setForm(f => ({ ...f, printer_type: e.target.value }))}>
                {PRINTER_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Location</label>
              <input className="form-input" placeholder="e.g. Shipping Desk" value={form.location} onChange={e => setForm(f => ({ ...f, location: e.target.value }))} />
            </div>

            {["zpl_raw", "pdf_raw", "ipp"].includes(form.printer_type) && (
              <div className="form-row">
                <div className="form-group">
                  <label className="form-label">IP Address</label>
                  <input className="form-input" placeholder="192.168.1.50" value={form.host} onChange={e => setForm(f => ({ ...f, host: e.target.value }))} />
                </div>
                <div className="form-group">
                  <label className="form-label">Port</label>
                  <input className="form-input" type="number" value={form.port} onChange={e => setForm(f => ({ ...f, port: Number(e.target.value) }))} />
                </div>
              </div>
            )}

            {form.printer_type === "pdf_cups" && (
              <>
                <div className="form-group">
                  <label className="form-label">CUPS Printer Name</label>
                  <input className="form-input" placeholder="Exact name from `lpstat -p`" value={form.cups_name} onChange={e => setForm(f => ({ ...f, cups_name: e.target.value }))} />
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label className="form-label">CUPS Host</label>
                    <input className="form-input" value={form.cups_host} onChange={e => setForm(f => ({ ...f, cups_host: e.target.value }))} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">CUPS Port</label>
                    <input className="form-input" type="number" value={form.cups_port} onChange={e => setForm(f => ({ ...f, cups_port: Number(e.target.value) }))} />
                  </div>
                </div>
              </>
            )}

            <div className="form-row">
              <div className="form-group">
                <label className="form-label">Paper Format</label>
                <select className="form-select" value={form.paper_format} onChange={e => setForm(f => ({ ...f, paper_format: e.target.value }))}>
                  {["A4","Letter","Legal","label_4x6","label_4x4","label_2x1","label_3x2"].map(v => <option key={v}>{v}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">DPI</label>
                <input className="form-input" type="number" value={form.dpi} onChange={e => setForm(f => ({ ...f, dpi: Number(e.target.value) }))} />
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn" onClick={() => setShowAdd(false)}>Cancel</button>
              <button className="btn primary" onClick={savePrinter} disabled={saving}>
                {saving ? <><span className="spin">⟳</span> Saving…</> : (editingId ? "Save Changes" : "Add Printer")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PrintView({ client, addToast }) {
  const { data: reports, loading: rLoading } = useOdooData(() => client.print.listReports(), []);
  const { data: printers, loading: pLoading } = useOdooData(() => client.print.listPrinters(), []);

  const [selectedReport, setSelectedReport] = useState(null);
  const [selectedPrinter, setSelectedPrinter] = useState("");
  const [resModel, setResModel]   = useState("");
  const [resId, setResId]         = useState("");
  const [copies, setCopies]       = useState(1);
  const [printing, setPrinting]   = useState(false);
  const [filterCat, setFilterCat] = useState("All");

  const categories = reports
    ? ["All", ...new Set(reports.map(r => r.model.split(".")[0]))]
    : ["All"];

  const filtered = (reports || []).filter(r =>
    filterCat === "All" || r.model.startsWith(filterCat)
  );

  const onSelectReport = (r) => {
    setSelectedReport(r);
    setResModel(r.model);
  };

  const doPrint = async () => {
    if (!selectedReport || !selectedPrinter || !resId) {
      addToast("Select report, printer and record ID", "error"); return;
    }
    setPrinting(true);
    try {
      const result = await client.print.sendJob({
        report_xml_id: selectedReport.xml_id,
        res_model: resModel,
        res_id: parseInt(resId),
        printer_id: parseInt(selectedPrinter),
        copies,
      });
      if (result.success) {
        const p = (printers || []).find(x => x.id === parseInt(selectedPrinter));
        addToast(`✓ Printed ${fmtBytes(result.bytes)} → ${p?.name || "printer"}`, "success");
      } else {
        addToast(`Print failed: ${result.error}`, "error");
      }
    } catch (e) {
      addToast(e.message, "error");
    } finally {
      setPrinting(false);
    }
  };

  if (rLoading || pLoading) return <Loading />;

  const onlinePrinters = (printers || []).filter(p => p.status === "online");

  return (
    <div>
      <div className="section-header">
        <div><h2>Print Now</h2><p>Render &amp; send directly to network printer</p></div>
      </div>
      <div className="grid2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 14 }}>1. Select Report</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
            {categories.map(c => (
              <button key={c} className="btn sm"
                style={filterCat === c ? { background: "var(--accent)", borderColor: "var(--accent)", color: "#fff" } : {}}
                onClick={() => setFilterCat(c)}>{c}</button>
            ))}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 380, overflowY: "auto" }}>
            {filtered.map(r => (
              <div key={r.id}
                onClick={() => onSelectReport(r)}
                style={{
                  padding: "10px 12px", borderRadius: 8, cursor: "pointer", fontSize: 13,
                  border: `1px solid ${selectedReport?.id === r.id ? "var(--accent)" : "var(--border)"}`,
                  background: selectedReport?.id === r.id ? "rgba(79,110,247,.08)" : "var(--bg)",
                  display: "flex", alignItems: "center", gap: 10, transition: "all .15s",
                }}>
                <span style={{ flex: 1 }}>{r.name}</span>
                <span style={{ fontSize: 10, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>{r.model_description || r.model_label || r.model}</span>
                <Pill type={r.doc_type === "zpl" ? "zpl" : "pdf"} label={r.doc_type?.toUpperCase()} />
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-title" style={{ marginBottom: 14 }}>2. Configure &amp; Send</div>

            <div className="form-group">
              <label className="form-label">Printer</label>
              <select className="form-select" value={selectedPrinter} onChange={e => setSelectedPrinter(e.target.value)}>
                <option value="">— Select printer —</option>
                {onlinePrinters.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              {onlinePrinters.length === 0 && (
                <div style={{ fontSize: 11, color: "var(--warning)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
                  ⚠ No printers online — check status in Printers tab
                </div>
              )}
            </div>

            {selectedReport && (
              <>
                <div className="form-group">
                  <label className="form-label">Model</label>
                  <input className="form-input" value={resModel} onChange={e => setResModel(e.target.value)} />
                </div>
                <div className="form-group">
                  <label className="form-label">Record ID in Odoo</label>
                  <input className="form-input" type="number" placeholder={`e.g. 42`} value={resId} onChange={e => setResId(e.target.value)} />
                </div>
              </>
            )}

            <div className="form-group">
              <label className="form-label">Copies</label>
              <input className="form-input" type="number" min={1} max={20} value={copies} onChange={e => setCopies(Number(e.target.value))} style={{ width: 90 }} />
            </div>

            {selectedReport && selectedPrinter && (
              <div style={{ background: "rgba(79,110,247,.06)", border: "1px solid rgba(79,110,247,.15)", borderRadius: 8, padding: 12, fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--muted)", marginBottom: 14, lineHeight: 1.8 }}>
                <div><span style={{ color: "var(--text)" }}>Report:</span> {selectedReport.name}</div>
                <div><span style={{ color: "var(--text)" }}>XML ID:</span> {selectedReport.xml_id}</div>
                <div><span style={{ color: "var(--text)" }}>Format:</span> {selectedReport.doc_type?.toUpperCase()}</div>
                <div><span style={{ color: "var(--text)" }}>Printer:</span> {(printers || []).find(p => p.id === parseInt(selectedPrinter))?.name}</div>
              </div>
            )}

            <button className="btn primary" style={{ width: "100%", justifyContent: "center", padding: 11 }}
              onClick={doPrint}
              disabled={printing || !selectedReport || !selectedPrinter || !resId}>
              {printing ? <><span className="spin">⟳</span> Rendering &amp; Sending…</> : "🖨️ Print Now"}
            </button>
          </div>

          <div className="card">
            <div className="card-title" style={{ marginBottom: 10 }}>📡 How It Works</div>
            <div style={{ fontSize: 12, fontFamily: "var(--font-mono)", lineHeight: 2, color: "var(--muted)" }}>
              <div><span style={{ color: "var(--success)" }}>1.</span> Odoo renders the report server-side</div>
              <div><span style={{ color: "var(--success)" }}>2.</span> PDF/ZPL bytes sent to printer IP directly</div>
              <div><span style={{ color: "var(--success)" }}>3.</span> No download, no browser print dialog</div>
              <div><span style={{ color: "var(--accent)" }}>●</span> Fully server-side — no subscription needed</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ScenariosView({ client, addToast }) {
  const { data: scenarios, refresh, loading } = useOdooData(() => client.print.listScenarios(), []);
  const { data: reports } = useOdooData(() => client.print.listReports(), []);
  const { data: printers } = useOdooData(() => client.print.listPrinters(), []);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving]   = useState(false);
  const [form, setForm] = useState({ name: "", trigger_event: "", report_id: "", printer_id: "", copies: 1, domain_filter: "" });

  const openAdd = () => { setEditingId(null); setForm({ name: "", trigger_event: "", report_id: "", printer_id: "", copies: 1, domain_filter: "" }); setShowAdd(true); };
  const openEdit = (s) => {
    setEditingId(s.id);
    setForm({
      name: s.name, trigger_event: s.trigger_event || "", report_id: String(s.report_id || ""),
      printer_id: String(s.printer_id || ""), copies: s.copies ?? 1, domain_filter: s.domain_filter || "",
    });
    setShowAdd(true);
  };

  const TRIGGERS = [
    ["stock.picking.button_validate",    "After Any Transfer Validate"],
    ["stock.picking.do_transfer",        "After Delivery _action_done"],
    ["stock.picking.action_put_in_pack", "After Put in Pack"],
    ["sale.order.action_confirm",        "After Sales Order Confirmation"],
    ["account.move.action_post",         "After Invoice Confirmation"],
    ["purchase.order.button_confirm",    "After Purchase Order Confirmation"],
    ["mrp.production.button_mark_done",  "After Manufacturing Order Done"],
  ];

  const toggle = async (s) => {
    try {
      await client.print.toggleScenario(s.id, !s.active);
      await refresh();
      addToast(`Scenario ${s.active ? "disabled" : "enabled"}`, s.active ? "error" : "success");
    } catch (e) { addToast(e.message, "error"); }
  };

  const remove = async (s) => {
    try {
      await client.print.deleteScenario(s.id);
      await refresh();
      addToast("Scenario removed", "error");
    } catch (e) { addToast(e.message, "error"); }
  };

  const saveScenario = async () => {
    if (!form.name || !form.trigger_event || !form.report_id || !form.printer_id) return;
    setSaving(true);
    try {
      const vals = { name: form.name, trigger_event: form.trigger_event, report_id: parseInt(form.report_id), printer_id: parseInt(form.printer_id), copies: parseInt(form.copies), domain_filter: form.domain_filter || "" };
      if (editingId) {
        await client.print.updateScenario(editingId, vals);
        addToast("Scenario updated", "success");
      } else {
        await client.print.createScenario(vals);
        addToast("Scenario created", "success");
      }
      await refresh();
      setShowAdd(false);
      setEditingId(null);
    } catch (e) { addToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  if (loading) return <Loading />;

  return (
    <div>
      <div className="section-header">
        <div><h2>Auto-Print Scenarios</h2><p>Trigger print jobs on Odoo actions automatically</p></div>
        <div className="actions">
          <button className="btn primary sm" onClick={openAdd}>+ New Scenario</button>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {(scenarios || []).map(s => (
          <div key={s.id} className="scenario-card">
            <div style={{ fontSize: 22, width: 36, textAlign: "center" }}>⚡</div>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{s.name}</div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3, lineHeight: 1.6 }}>
                <span style={{ color: "var(--accent)" }}>{TRIGGERS.find(t => t[0] === s.trigger_event)?.[1] || s.trigger_event}</span>
                {s.report_name && <> → <span style={{ color: "var(--text)" }}>{s.report_name}</span></>}
                {s.printer_name && <> → <span>{s.printer_name}</span></>}
                {s.domain_filter && <> · <span style={{ color: "var(--warning)" }}>filter: {s.domain_filter}</span></>}
              </div>
              <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--muted)", marginTop: 2 }}>
                Runs: {s.run_count} · Last: {s.last_run ? fmtTime(s.last_run) : "never"}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
              <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: s.active ? "var(--success)" : "var(--muted)" }}>
                {s.active ? "Active" : "Off"}
              </span>
              <Toggle on={s.active} onToggle={() => toggle(s)} />
              <button className="btn sm" onClick={() => openEdit(s)}>Edit</button>
              <button className="btn sm danger" onClick={() => remove(s)}>Remove</button>
            </div>
          </div>
        ))}
        {(scenarios || []).length === 0 && (
          <div className="empty"><div className="icon">⚡</div><p>No scenarios yet. Create one to auto-print on Odoo actions.</p></div>
        )}
      </div>

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-title">{editingId ? "Edit Scenario" : "New Scenario"} <button className="modal-close" onClick={() => setShowAdd(false)}>✕</button></div>
            <div className="form-group">
              <label className="form-label">Name</label>
              <input className="form-input" placeholder="e.g. Auto-print ZPL on delivery" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            </div>
            <div className="form-group">
              <label className="form-label">Trigger</label>
              <select className="form-select" value={form.trigger_event} onChange={e => setForm(f => ({ ...f, trigger_event: e.target.value }))}>
                <option value="">— Select trigger —</option>
                {TRIGGERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Report</label>
              <select className="form-select" value={form.report_id} onChange={e => setForm(f => ({ ...f, report_id: e.target.value }))}>
                <option value="">— Select report —</option>
                {(reports || []).map(r => <option key={r.id} value={r.id}>{r.name}{(r.model_description || r.model_label || r.model) ? ` · ${r.model_description || r.model_label || r.model}` : ""}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Printer</label>
              <select className="form-select" value={form.printer_id} onChange={e => setForm(f => ({ ...f, printer_id: e.target.value }))}>
                <option value="">— Select printer —</option>
                {(printers || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="form-row">
              <div className="form-group">
                <label className="form-label">Copies</label>
                <input className="form-input" type="number" min={1} max={10} value={form.copies} onChange={e => setForm(f => ({ ...f, copies: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Domain Filter (optional)</label>
                <input className="form-input" placeholder='[("picking_type_code","=","outgoing")]' value={form.domain_filter} onChange={e => setForm(f => ({ ...f, domain_filter: e.target.value }))} />
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn" onClick={() => setShowAdd(false)}>Cancel</button>
              <button className="btn primary" onClick={saveScenario} disabled={saving}>
                {saving ? <><span className="spin">⟳</span> Saving…</> : (editingId ? "Save Changes" : "Create")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function UserRulesView({ client, addToast }) {
  const { data: rules, refresh, loading } = useOdooData(() => client.print.listUserRules(), []);
  const { data: reports } = useOdooData(() => client.print.listReports(), []);
  const { data: printers } = useOdooData(() => client.print.listPrinters(), []);
  const { data: users }    = useOdooData(() => client.searchRead("res.users", [["active","=",true]], ["id","name"], 50), []);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving]   = useState(false);
  const [form, setForm] = useState({ user_id: "", report_id: "", printer_id: "", copies: 1 });

  const openAdd = () => { setEditingId(null); setForm({ user_id: "", report_id: "", printer_id: "", copies: 1 }); setShowAdd(true); };
  const openEdit = (r) => {
    setEditingId(r.id);
    setForm({ user_id: String(r.user_id || ""), report_id: String(r.report_id || ""), printer_id: String(r.printer_id || ""), copies: r.copies ?? 1 });
    setShowAdd(true);
  };

  const remove = async (r) => {
    try { await client.print.deleteUserRule(r.id); await refresh(); addToast("Rule removed", "error"); }
    catch (e) { addToast(e.message, "error"); }
  };

  const saveRule = async () => {
    if (!form.user_id || !form.report_id || !form.printer_id) return;
    setSaving(true);
    try {
      const vals = { user_id: parseInt(form.user_id), report_id: parseInt(form.report_id), printer_id: parseInt(form.printer_id), copies: parseInt(form.copies) };
      if (editingId) {
        await client.print.updateUserRule(editingId, vals);
        addToast("Rule updated", "success");
      } else {
        await client.print.saveUserRule(vals);
        addToast("Rule saved", "success");
      }
      await refresh();
      setShowAdd(false);
      setEditingId(null);
    } catch (e) { addToast(e.message, "error"); }
    finally { setSaving(false); }
  };

  if (loading) return <Loading />;

  return (
    <div>
      <div className="section-header">
        <div><h2>User Printer Rules</h2><p>Default printer per user per report</p></div>
        <div className="actions">
          <button className="btn primary sm" onClick={openAdd}>+ Add Rule</button>
        </div>
      </div>
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead><tr><th>User</th><th>Report</th><th>Default Printer</th><th>Copies</th><th></th></tr></thead>
            <tbody>
              {(rules || []).map(r => (
                <tr key={r.id}>
                  <td style={{ fontWeight: 600 }}>👤 {r.user_name}</td>
                  <td>{r.report_name}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>🖨️ {r.printer_name}</td>
                  <td><Pill type="pdf" label={`${r.copies}×`} /></td>
                  <td>
                    <button className="btn sm" onClick={() => openEdit(r)}>Edit</button>
                    <button className="btn sm danger" onClick={() => remove(r)} style={{ marginLeft: 6 }}>Remove</button>
                  </td>
                </tr>
              ))}
              {(rules || []).length === 0 && (
                <tr><td colSpan={5}><div className="empty"><div className="icon">👤</div><p>No rules yet</p></div></td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showAdd && (
        <div className="modal-overlay" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-title">{editingId ? "Edit User Rule" : "Add User Rule"} <button className="modal-close" onClick={() => setShowAdd(false)}>✕</button></div>
            <div className="form-group">
              <label className="form-label">User</label>
              <select className="form-select" value={form.user_id} onChange={e => setForm(f => ({ ...f, user_id: e.target.value }))}>
                <option value="">— Select user —</option>
                {(users || []).map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Report</label>
              <select className="form-select" value={form.report_id} onChange={e => setForm(f => ({ ...f, report_id: e.target.value }))}>
                <option value="">— Select report —</option>
                {(reports || []).map(r => <option key={r.id} value={r.id}>{r.name}{(r.model_description || r.model_label || r.model) ? ` · ${r.model_description || r.model_label || r.model}` : ""}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Printer</label>
              <select className="form-select" value={form.printer_id} onChange={e => setForm(f => ({ ...f, printer_id: e.target.value }))}>
                <option value="">— Select printer —</option>
                {(printers || []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Copies</label>
              <input className="form-input" type="number" min={1} max={10} value={form.copies} onChange={e => setForm(f => ({ ...f, copies: e.target.value }))} style={{ width: 80 }} />
            </div>
            <div className="modal-footer">
              <button className="btn" onClick={() => setShowAdd(false)}>Cancel</button>
              <button className="btn primary" onClick={saveRule} disabled={saving}>
                {saving ? <><span className="spin">⟳</span> Saving…</> : (editingId ? "Save Changes" : "Save Rule")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function LogsView({ client }) {
  const { data: jobs, loading, refresh } = useOdooData(() => client.print.listJobs(200), []);
  if (loading) return <Loading />;
  return (
    <div>
      <div className="section-header">
        <div><h2>Print Log</h2><p>{jobs?.length || 0} jobs recorded</p></div>
        <div className="actions"><button className="btn sm" onClick={refresh}>⟳ Refresh</button></div>
      </div>
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Time</th><th>Job</th><th>Document</th><th>Printer</th><th>Size</th><th>Copies</th><th>Status</th><th>Error</th></tr></thead>
            <tbody>
              {(jobs || []).map(j => (
                <tr key={j.id}>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>{fmtTime(j.time)}</td>
                  <td style={{ fontWeight: 600 }}>{j.name}</td>
                  <td style={{ fontSize: 12 }}>{j.res_name}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>🖨️ {j.printer}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{fmtBytes(j.size_bytes)}</td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{j.copies}×</td>
                  <td><Pill type={j.state === "done" ? "online" : j.state === "failed" ? "error" : "warn"} label={j.state === "done" ? "✓ sent" : j.state === "failed" ? "✗ failed" : "⏳"} /></td>
                  <td style={{ fontSize: 11, color: "var(--danger)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{j.error || ""}</td>
                </tr>
              ))}
              {(jobs || []).length === 0 && (
                <tr><td colSpan={8}><div className="empty"><div className="icon">📋</div><p>No print jobs yet</p></div></td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── CONNECT SCREEN ────────────────────────────────────────────────────────────
function ConnectScreen({ onConnect }) {
  const [form, setForm] = useState({ url: "", db: "", user: "admin", pass: "" });
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const connect = async () => {
    const url = form.url.replace(/\/$/, "");
    if (!url || !form.db || !form.user || !form.pass) {
      setErr("All fields are required."); return;
    }
    setLoading(true); setErr("");
    try {
      const client = createOdooClient(url);
      const session = await client.authenticate(form.db, form.user, form.pass);
      onConnect({ ...form, url, session, client });
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="connect-screen">
      <div className="connect-box">
        <div className="connect-logo">
          <div style={{ fontSize: 40 }}>🖨️</div>
          <h1 style={{ fontSize: 22, fontWeight: 800, marginTop: 10 }}>IAG Direct Print</h1>
          <p style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
            Server-side printing for Odoo 19 · No subscription
          </p>
        </div>
        <div className="form-group">
          <label className="form-label">Odoo URL</label>
          <input className="form-input" placeholder="https://your-odoo.com" value={form.url} onChange={e => setForm(f => ({ ...f, url: e.target.value }))} />
        </div>
        <div className="form-group">
          <label className="form-label">Database</label>
          <input className="form-input" placeholder="odoo_db" value={form.db} onChange={e => setForm(f => ({ ...f, db: e.target.value }))} />
        </div>
        <div className="form-row">
          <div className="form-group">
            <label className="form-label">Username</label>
            <input className="form-input" placeholder="admin" value={form.user} onChange={e => setForm(f => ({ ...f, user: e.target.value }))} />
          </div>
          <div className="form-group">
            <label className="form-label">Password / API Key</label>
            <input className="form-input" type="password" placeholder="••••••••" value={form.pass}
              onChange={e => setForm(f => ({ ...f, pass: e.target.value }))}
              onKeyDown={e => e.key === "Enter" && connect()} />
          </div>
        </div>
        {err && <div className="alert error">⚠️ {err}</div>}
        <button className="btn primary" style={{ width: "100%", justifyContent: "center", padding: 12, marginTop: 12 }}
          onClick={connect} disabled={loading}>
          {loading ? <><span className="spin">⟳</span> Authenticating…</> : "Connect to Odoo"}
        </button>
        <div style={{ marginTop: 16, fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", lineHeight: 1.7, textAlign: "center" }}>
          Requires iag_direct_print module installed on Odoo server
        </div>
      </div>
    </div>
  );
}

// ── ROOT ──────────────────────────────────────────────────────────────────────
export default function App() {
  const [conn, setConn] = useState(null);
  const [view, setView] = useState("dashboard");
  const [checkingSession, setCheckingSession] = useState(true);
  const { toasts, add: addToast } = useToasts();

  const nav = [
    { id: "dashboard", label: "Dashboard",       icon: "◈" },
    { id: "print",     label: "Print Now",        icon: "⎙" },
    { id: "printers",  label: "Printers",         icon: "🖨" },
    { id: "scenarios", label: "Scenarios",        icon: "⚡" },
    { id: "rules",     label: "User Rules",       icon: "👤" },
    { id: "logs",      label: "Print Log",        icon: "📋" },
  ];

  // When opened from Odoo app menu, we're already logged in — use existing session
  useEffect(() => {
    const url = window.location.origin;
    const client = createOdooClient(url);
    client.getSession().then((session) => {
      setCheckingSession(false);
      if (session?.uid && session?.db) {
        setConn({ url, db: session.db, session, client });
      }
    }).catch(() => setCheckingSession(false));
  }, []);

  if (checkingSession) {
    return (
      <>
        <style>{CSS}</style>
        <div className="app">
          <div className="loading" style={{ flex: 1 }}>
            <span className="spin">⟳</span> Checking Odoo session…
          </div>
        </div>
      </>
    );
  }

  if (!conn) {
    return (
      <>
        <style>{CSS}</style>
        <div className="app"><ConnectScreen onConnect={c => setConn(c)} /></div>
        <div className="toast-container">{toasts.map(t => <div key={t.id} className={`toast ${t.type}`}>{t.type === "success" ? "✅" : t.type === "error" ? "❌" : "ℹ️"} {t.msg}</div>)}</div>
      </>
    );
  }

  const { client } = conn;

  return (
    <>
      <style>{CSS}</style>
      <div className="app">
        <div className="sidebar">
          <div className="sidebar-logo">
            <div className="logo-icon">🖨️</div>
            <div>
              <div className="logo-text">DirectPrint</div>
              <div className="logo-sub">IAG · Odoo 19</div>
            </div>
          </div>
          <div className="sidebar-section">
            <div className="sidebar-label">Navigation</div>
            {nav.map(n => (
              <div key={n.id} className={`nav-item ${view === n.id ? "active" : ""}`} onClick={() => setView(n.id)}>
                <span>{n.icon}</span><span>{n.label}</span>
              </div>
            ))}
          </div>
          <div className="conn-status">
            <span className="conn-dot connected" />
            <span style={{ color: "var(--success)" }}>Connected</span>
            <div style={{ color: "var(--muted)", fontSize: 10, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{conn.url}</div>
            <div style={{ color: "var(--muted)", fontSize: 10 }}>db: {conn.db} · uid: {conn.session?.uid}</div>
          </div>
        </div>

        <div className="main">
          <div className="topbar">
            <div className="topbar-title">
              {nav.find(n => n.id === view)?.label || ""}
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>{conn.url}</div>
            <div className="topbar-actions">
              <button className="btn sm danger" onClick={() => setConn(null)}>Disconnect</button>
            </div>
          </div>
          <div className="content">
            {view === "dashboard" && <DashboardView client={client} addToast={addToast} />}
            {view === "print"     && <PrintView     client={client} addToast={addToast} />}
            {view === "printers"  && <PrintersView  client={client} addToast={addToast} />}
            {view === "scenarios" && <ScenariosView client={client} addToast={addToast} />}
            {view === "rules"     && <UserRulesView client={client} addToast={addToast} />}
            {view === "logs"      && <LogsView      client={client} />}
          </div>
        </div>
      </div>

      <div className="toast-container">
        {toasts.map(t => (
          <div key={t.id} className={`toast ${t.type}`}>
            {t.type === "success" ? "✅" : t.type === "error" ? "❌" : "ℹ️"} {t.msg}
          </div>
        ))}
      </div>
    </>
  );
}
