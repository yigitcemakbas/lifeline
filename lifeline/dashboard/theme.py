"""Visual language for the operator dashboard: colours, labels and stylesheet."""

from __future__ import annotations

from lifeline.models import ConnState, FactStatus


def _hex(rgb: list[int]) -> str:
    return "#" + "".join(f"{c:02x}" for c in rgb)


TIER_LABEL = {
    1: "Critical: life safety",
    2: "High: essential service",
    3: "Isolated area",
    4: "Degraded service",
    5: "Low",
}
TIER_RGB = {
    1: [229, 72, 77],
    2: [245, 165, 36],
    3: [214, 184, 74],
    4: [107, 143, 199],
    5: [107, 116, 128],
}
TIER_COLOR = {t: _hex(rgb) for t, rgb in TIER_RGB.items()}
CONN_RGB = {
    ConnState.CONNECTED: [61, 214, 140],
    ConnState.CONGESTED: [214, 184, 74],
    ConnState.DEGRADED: [245, 140, 36],
    ConnState.ISOLATED: [229, 72, 77],
}
CONN_COLOR = {k: _hex(v) for k, v in CONN_RGB.items()}
ASSET_RGB = {
    "usable": [200, 208, 218],
    "unusable": [229, 72, 77],
    "standby": [76, 141, 255],
}
ASSET_LEGEND = [
    ("working site", _hex(ASSET_RGB["usable"])),
    ("standby resource", _hex(ASSET_RGB["standby"])),
]
SERVED_STYLE = {
    "served": ("Served", "#3dd68c"),
    "planned": ("Restoration en route", "#f5a524"),
    "unserved": ("Waiting for capacity", "#8a94a3"),
}
FACT_COLOR = {
    FactStatus.VERIFIED: "#3dd68c",
    FactStatus.REPORTED: "#d6b84a",
    FactStatus.INFERRED: "#9b7ede",
}
REJECTION_COLOR = {
    "dominated": "#f5a524",
    "infeasible": "#e5484d",
    "no_need": "#8a94a3",
    "rejected_by_operator": "#9b7ede",
}
CHANNEL_COLOR = {
    "cellular": "#5aa9e6",
    "satellite": "#b48ef0",
    "mesh": "#3fb6a8",
    "tetra": "#f5a524",
    "citizen_app": "#8fd16b",
    "facility": "#e87a90",
    "telemetry": "#8a94a3",
}

CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Lato:wght@400;700;900&display=swap");
html, body, .stApp, .stApp *:not([data-testid="stIconMaterial"]):not([class*="material"]) {
  font-family: "Lato", sans-serif !important; }
:root { --bg:#0e1116; --panel:#151a21; --line:#252c36; --txt:#e6eaf0; --dim:#8a94a3;
  --soft:#c9d0da; --acc:#4c8dff; }
.stApp { background: var(--bg); }
section[data-testid="stSidebar"] { background:#11151b; border-right:1px solid var(--line); }
.block-container { padding-top:.6rem; padding-bottom:2rem; max-width:100%; }
header[data-testid="stHeader"] { background:transparent; }

/* primitives */
.ll-small { font-size:.72rem; color:var(--dim); line-height:1.5; }
.ll-dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:5px;
  vertical-align:middle; }
.ll-ring { background:transparent; border:2px solid #fff; width:8px; height:8px; }
.ll-sev { display:inline-flex; align-items:center; font-size:.7rem; font-weight:700;
  color:var(--txt); margin-right:8px; white-space:nowrap; }
.ll-badge { display:inline-block; font-size:.72rem; padding:1px 7px; border-radius:4px;
  background:#1d232c; margin-right:6px; white-space:nowrap; }
.ll-ok { color:#3dd68c; font-weight:700; } .ll-bad { color:#e5484d; font-weight:700; }
.ll-flag { color:#f5a524; }
.ll-gap { margin-top:6px; }
.ll-row { margin:3px 0; }
.ll-title { font-weight:700; font-size:.82rem; color:var(--txt); margin:2px 0; }
.ll-fact { font-size:.8rem; margin:4px 0; color:#d5dbe3; }
.ll-quote { font-size:.8rem; color:var(--soft); border-left:2px solid var(--line);
  padding-left:10px; margin:6px 0; }
.ll-quote mark { background:rgba(76,141,255,.22); color:var(--txt); padding:0 2px;
  border-radius:2px; }
.ll-chain { display:flex; flex-wrap:wrap; align-items:center; gap:6px; font-size:.78rem;
  color:var(--soft); }
.ll-chain span.step { border:1px solid var(--line); border-radius:4px; padding:3px 8px; }

/* containers */
.ll-head { display:flex; align-items:baseline; gap:18px; flex-wrap:wrap; padding:0 0 8px 0;
  border-bottom:1px solid var(--line); margin-bottom:8px; }
.ll-logo { font-weight:900; font-size:1.1rem; color:var(--txt); }
.ll-scn, .ll-clock { font-size:.88rem; color:var(--txt); font-weight:700; }
.ll-clock { font-variant-numeric:tabular-nums; }
.ll-dim { color:var(--dim); font-size:.78rem; }
.ll-right { margin-left:auto; color:var(--dim); font-size:.8rem; }
.ll-panel { background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:8px 10px; margin-bottom:6px; }
.ll-panel h4 { margin:0 0 8px 0; font-size:.8rem; color:var(--dim); font-weight:700; }
h4.ll-sub { margin:10px 0 6px 0; font-size:.78rem; color:var(--dim); font-weight:700; }
.ll-summary { font-size:.85rem; line-height:1.55; color:var(--txt); }
.ll-change { padding:4px 0; margin:2px 0; font-size:.82rem; color:var(--soft);
  border-top:1px solid var(--line); }
.ll-grid { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:8px 12px; }
.ll-m .lbl { font-size:.68rem; color:var(--dim); line-height:1.3; }
.ll-m .val { font-size:1rem; font-weight:900; color:var(--txt);
  font-variant-numeric:tabular-nums; }
.ll-card { background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:6px 9px; margin:4px 0; }
.ll-section { border-bottom:1px solid var(--line); padding:0 0 6px 0; margin-bottom:6px; }
.ll-section .ttl { font-weight:900; color:var(--txt); font-size:.85rem; }
.ll-tl { font-size:.75rem; padding:4px 0; border-top:1px solid var(--line); color:var(--soft); }
.ll-tl .ts { color:var(--dim); margin-right:10px; font-variant-numeric:tabular-nums; }
.ll-legend { font-size:.7rem; color:var(--dim); display:flex; gap:14px; flex-wrap:wrap;
  margin:6px 0 12px 0; }

/* incident timeline */
.tl-wrap { background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:8px 14px 22px 14px; margin-bottom:8px; }
.tl-head { display:flex; justify-content:space-between; align-items:center;
  margin-bottom:10px; gap:12px; flex-wrap:wrap; }
.tl-alert { color:#e5484d; font-weight:900; font-size:.85rem; }
.tl-axis { position:relative; height:16px; border-bottom:1px solid #3a4350; }
.tl-dot { position:absolute; bottom:-5px; width:10px; height:10px; border-radius:50%;
  margin-left:-5px; border:2px solid var(--panel); }
.tl-onset { position:absolute; bottom:-8px; height:26px; width:3px; background:#e5484d;
  margin-left:-1px; }
.tl-onset-lbl { position:absolute; top:-2px; margin-left:8px; font-size:.68rem;
  color:#e5484d; font-weight:700; white-space:nowrap; }
.tl-now { position:absolute; bottom:-10px; height:28px; width:2px; background:var(--acc); }
.tl-now span { position:absolute; top:28px; left:-24px; font-size:.65rem; color:var(--acc);
  white-space:nowrap; font-weight:700; }

/* pipeline view */
.wf { margin-bottom:8px; }
.wf-title { font-size:.78rem; color:var(--dim); margin:2px 0 6px 0; }
.wf-empty { background:var(--panel); border:1px dashed var(--line); border-radius:6px;
  padding:10px 12px; font-size:.78rem; color:var(--dim); margin-bottom:8px; }
.wf-row { display:flex; align-items:stretch; gap:4px; }
.wf-arrow { color:#4a5563; font-size:1.3rem; align-self:center; }
.wf-step { flex:1 1 0; min-width:0; background:var(--panel); border:1px solid var(--line);
  border-radius:6px; padding:8px 10px; opacity:0; animation:wfIn .45s ease forwards; }
@keyframes wfIn { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }
.wf-n { display:inline-flex; width:16px; height:16px; border-radius:50%; background:#1f2a3a;
  color:var(--acc); font-size:.62rem; font-weight:900; align-items:center;
  justify-content:center; margin-right:6px; }
.wf-t { display:inline; font-size:.74rem; font-weight:900; color:var(--txt); }
.wf-b { margin-top:6px; font-size:.74rem; color:var(--soft); line-height:1.4; }
.wf-src { font-size:.64rem; color:var(--dim); }
.wf-msg { font-size:.76rem; color:var(--txt); margin-bottom:4px; }
.wf-kv { display:grid; grid-template-columns:auto 1fr; gap:1px 8px; font-size:.7rem;
  margin-bottom:4px; }
.wf-kv span { color:var(--dim); } .wf-kv b { color:var(--txt); }
.wf-fact { font-size:.68rem; color:var(--soft); margin-top:2px; }
.wf-fact .fs { color:var(--acc); margin-right:5px; font-size:.62rem; }
.wf-note { font-size:.66rem; color:var(--dim); margin-top:4px; border-top:1px solid var(--line);
  padding-top:3px; }
.wf-need { margin-bottom:5px; font-size:.75rem; color:var(--txt); }
.wf-dec { font-size:.78rem; font-weight:700; color:var(--txt); margin-top:2px; }
.wf-res { font-size:.74rem; color:#3dd68c; margin-top:3px; }

/* priority queue */
.pq { display:flex; gap:10px; background:var(--panel); border:1px solid var(--line);
  border-radius:6px; padding:7px 10px; margin:5px 0; }
.pq-new { border-color:#2d4a7a; animation:wfIn .45s ease both; }
.pq-rank { font-size:1.2rem; font-weight:900; color:var(--txt); min-width:22px; }
.pq-body { flex:1; min-width:0; }
.pq-who { font-size:.82rem; font-weight:700; color:var(--txt); margin:2px 0; }
.pq-status { font-size:.72rem; font-weight:700; margin-top:2px; }
.pq-jump { font-size:.66rem; color:var(--acc); }
.pq-newtag { font-size:.6rem; font-weight:700; color:var(--acc); border:1px solid var(--acc);
  border-radius:3px; padding:0 4px; }
</style>
"""
