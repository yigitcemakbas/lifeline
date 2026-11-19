"""Visual language for the operator dashboard (dark network-operations style)."""

from __future__ import annotations

from lifeline.models import ConnState, FactStatus

TIER_LABEL = {
    1: "CRITICAL · LIFE",
    2: "HIGH · ESSENTIAL SERVICE",
    3: "ISOLATED AREA",
    4: "DEGRADED SERVICE",
    5: "LOW",
}
TIER_COLOR = {1: "#e5484d", 2: "#f5a524", 3: "#d6b84a", 4: "#6b8fc7", 5: "#6b7480"}
TIER_RGB = {
    1: [255, 77, 79],
    2: [255, 159, 26],
    3: [227, 200, 75],
    4: [90, 169, 230],
    5: [125, 139, 153],
}
CONN_RGB = {
    ConnState.CONNECTED: [61, 214, 140],
    ConnState.CONGESTED: [214, 184, 74],
    ConnState.DEGRADED: [245, 140, 36],
    ConnState.ISOLATED: [229, 72, 77],
}
CONN_COLOR = {k: "#" + "".join(f"{c:02x}" for c in v) for k, v in CONN_RGB.items()}
FACT_COLOR = {
    FactStatus.VERIFIED: "#2ea078",
    FactStatus.REPORTED: "#e3c84b",
    FactStatus.INFERRED: "#9b7ede",
}
CHANNEL_COLOR = {
    "cellular": "#5aa9e6",
    "satellite": "#b48ef0",
    "mesh": "#3fb6a8",
    "tetra": "#ff9f1a",
    "citizen_app": "#8fd16b",
    "facility": "#ff6b8b",
    "telemetry": "#7d8b99",
}
TIMELINE_COLOR = {
    "comm": "#5aa9e6",
    "telemetry": "#7d8b99",
    "intelligence": "#b48ef0",
    "priority": "#ff9f1a",
    "decision": "#3fb6a8",
    "outcome": "#2ea078",
    "system": "#55606b",
}

CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700;900&display=swap");
html, body, .stApp, .stApp *:not([data-testid="stIconMaterial"]):not([class*="material"]) { font-family: "Lato", sans-serif !important; }
:root { --bg:#0e1116; --panel:#151a21; --line:#252c36; --txt:#e6eaf0; --dim:#8a94a3; --acc:#4c8dff; }
.stApp { background: var(--bg); }
section[data-testid="stSidebar"] { background:#11151b; border-right:1px solid var(--line); }
.block-container { padding-top: .6rem; padding-bottom: 2rem; max-width: 100%; }
header[data-testid="stHeader"] { background: transparent; }
.ll-head { display:flex; align-items:baseline; gap:18px; flex-wrap:wrap; padding:0 0 8px 0;
  border-bottom:1px solid var(--line); margin-bottom:8px; }
.ll-logo { font-weight:900; font-size:1.1rem; color:var(--txt); letter-spacing:-.01em; }
.ll-scn { font-size:.88rem; color:var(--txt); font-weight:700; }
.ll-dim { color: var(--dim); font-size:.78rem; }
.ll-clock { font-size:.88rem; color:var(--txt); font-weight:700; font-variant-numeric: tabular-nums; }
.ll-right { margin-left:auto; color:var(--dim); font-size:.8rem; }
.ll-badge { display:inline-block; font-size:.74rem; padding:1px 7px; border-radius:4px;
  background:#1d232c; color:#aeb6c2 !important; margin-right:6px; white-space:nowrap; font-weight:400; }
.ll-sev { display:inline-flex; align-items:center; gap:5px; font-size:.7rem; font-weight:700;
  color:var(--txt); margin-right:8px; white-space:nowrap; }
.ll-sev i { width:8px; height:8px; border-radius:50%; display:inline-block; }
.ll-panel { background: var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:8px 10px; margin-bottom:6px; }
.ll-panel h4 { margin:0 0 8px 0; font-size:.8rem; color: var(--dim); font-weight:700; }
.ll-summary { font-size:.85rem; line-height:1.55; color:var(--txt); }
.ll-change { padding:4px 0; margin:2px 0; font-size:.88rem; color:#c9d0da; border-top:1px solid var(--line); }
.ll-strip { display:grid; grid-template-columns: repeat(2, 1fr); gap:10px 16px; font-size:.75rem; color:var(--dim); }
.ll-strip b { display:block; font-size:1rem; color:var(--txt); font-weight:900; }
.ll-card { background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:6px 9px; margin:4px 0; }
.ll-title { font-weight:700; font-size:.82rem; color:var(--txt); }
.ll-small { font-size:.72rem; color: var(--dim); line-height:1.5; }
.ll-pc-ok { color:#3dd68c; } .ll-pc-bad { color:#e5484d; }
.ll-quote { font-size:.86rem; color:#c9d0da; border-left:2px solid var(--line); padding-left:10px; margin:6px 0; }
.ll-quote mark { background:rgba(76,141,255,.22); color:#e6eaf0; padding:0 2px; border-radius:2px; }
.ll-fact { font-size:.88rem; margin:4px 0; color:#d5dbe3; }
.ll-chain { display:flex; flex-wrap:wrap; align-items:center; gap:6px; font-size:.85rem; color:#c9d0da; }
.ll-chain span.step { border:1px solid var(--line); border-radius:4px; padding:3px 8px; }
.ll-tl { font-size:.75rem; padding:3px 0; border-top:1px solid var(--line); color:#c9d0da; }
.ll-tl .ts { color: var(--dim); margin-right:10px; font-variant-numeric: tabular-nums; }
.ll-bar { height:3px; background:var(--line); border-radius:2px; overflow:hidden; margin-top:4px; }
.ll-bar > div { height:100%; background:var(--acc); }
.ll-legend { font-size:.7rem; color: var(--dim); display:flex; gap:14px; flex-wrap:wrap; margin:6px 0 12px 0; }
.ll-legend i { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; vertical-align:middle; }
div[data-testid="stTabs"] button p { font-size:.9rem; }
</style>
"""
EVENT_CSS = """
<style>
.ll-event { display:flex; gap:14px; background:var(--panel); border:1px solid var(--line);
  border-radius:6px; padding:8px 12px; margin:0 0 8px 0; }
.ll-event .when { min-width:58px; border-right:1px solid var(--line); padding-right:12px; }
.ll-event .when b { font-size:1.1rem; font-weight:900; display:block; color:var(--txt);
  font-variant-numeric: tabular-nums; line-height:1.1; }
.ll-event .what { font-size:.86rem; color:var(--txt); line-height:1.45; }
.ll-event .src { font-size:.7rem; color:var(--dim); margin-bottom:4px; }
.ll-event .ai { margin-top:4px; font-size:.76rem; color:#c9d0da; }
.ll-event .ai span { color:var(--acc); font-weight:700; margin-right:6px; }
.ll-ai { border-bottom:1px solid var(--line); padding:0 0 6px 0; margin-bottom:6px; }
.ll-ai .ttl { font-weight:900; color:var(--txt); font-size:.85rem; }
.ll-grid { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:8px 12px; }
.ll-m .lbl { font-size:.68rem; color:var(--dim); line-height:1.3; }
.ll-m .val { font-size:1rem; font-weight:900; color:var(--txt); font-variant-numeric: tabular-nums; }
.ll-m .dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:5px; vertical-align:middle; }
.tl-wrap { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:8px 14px 22px 14px; margin-bottom:8px; }
.tl-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.tl-head .dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin:0 4px 0 10px; vertical-align:middle; }
.tl-alert { color:#e5484d; font-weight:900; font-size:.85rem; }
.tl-axis { position:relative; height:16px; border-bottom:1px solid #3a4350; }
.tl-dot { position:absolute; bottom:-5px; width:10px; height:10px; border-radius:50%; margin-left:-5px; border:2px solid var(--panel); }
.tl-onset { position:absolute; bottom:-8px; height:26px; width:3px; background:#e5484d; margin-left:-1px; }
.tl-onset-lbl { position:absolute; top:-2px; margin-left:8px; font-size:.68rem; color:#e5484d; font-weight:700; white-space:nowrap; }
.tl-now { position:absolute; bottom:-10px; height:28px; width:2px; background:var(--acc); }
.tl-now span { position:absolute; top:28px; left:-30px; font-size:.65rem; color:var(--acc); white-space:nowrap; font-weight:700; }
.wf { margin-bottom:8px; }
.wf-title { font-size:.78rem; color:var(--dim); margin:2px 0 6px 0; }
.wf-empty { background:var(--panel); border:1px dashed var(--line); border-radius:6px; padding:10px 12px; font-size:.78rem; color:var(--dim); margin-bottom:8px; }
.wf-row { display:flex; align-items:stretch; gap:4px; }
.wf-arrow { color:#4a5563; font-size:1.3rem; align-self:center; }
.wf-step { flex:1 1 0; min-width:0; background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:8px 10px;
  opacity:0; transform:translateY(6px); animation: wfIn .55s ease forwards; }
.wf-step:last-child { border-color:#2d4a7a; }
@keyframes wfIn { 0% { opacity:0; transform:translateY(6px); border-color:var(--acc); }
  60% { opacity:1; transform:none; border-color:var(--acc); box-shadow:0 0 0 2px rgba(76,141,255,.25); }
  100% { opacity:1; transform:none; } }
.wf-n { display:inline-flex; width:16px; height:16px; border-radius:50%; background:#1f2a3a; color:var(--acc); font-size:.62rem;
  font-weight:900; align-items:center; justify-content:center; margin-right:6px; }
.wf-t { display:inline; font-size:.74rem; font-weight:900; color:var(--txt); }
.wf-b { margin-top:6px; font-size:.74rem; color:#c9d0da; line-height:1.4; }
.wf-src { font-size:.64rem; color:var(--dim); }
.wf-msg { font-size:.76rem; color:var(--txt); margin-bottom:4px; }
.wf-kv { display:grid; grid-template-columns:auto 1fr; gap:1px 8px; font-size:.7rem; margin-bottom:4px; }
.wf-kv span { color:var(--dim); } .wf-kv b { color:var(--txt); }
.wf-fact { font-size:.68rem; color:#c9d0da; margin-top:2px; }
.wf-fact .fs { color:var(--acc); margin-right:5px; font-size:.62rem; }
.wf-note { font-size:.66rem; color:var(--dim); margin-top:4px; border-top:1px solid var(--line); padding-top:3px; }
.wf-need { margin-bottom:5px; font-size:.75rem; color:var(--txt); }
.wf-dec { font-size:.78rem; font-weight:700; color:var(--txt); margin-top:2px; }
.wf-res { font-size:.74rem; color:#3dd68c; margin-bottom:3px; }
</style>
"""
