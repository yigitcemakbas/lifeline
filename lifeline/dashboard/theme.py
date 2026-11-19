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
TIER_COLOR = {1: "#ff4d4f", 2: "#ff9f1a", 3: "#e3c84b", 4: "#5aa9e6", 5: "#7d8b99"}
TIER_RGB = {
    1: [255, 77, 79],
    2: [255, 159, 26],
    3: [227, 200, 75],
    4: [90, 169, 230],
    5: [125, 139, 153],
}
CONN_RGB = {
    ConnState.CONNECTED: [46, 160, 120],
    ConnState.CONGESTED: [227, 200, 75],
    ConnState.DEGRADED: [255, 140, 40],
    ConnState.ISOLATED: [230, 60, 60],
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
html, body, [class*="css"], .stApp, .stApp * { font-family: "Lato", sans-serif !important; }
:root { --ll-bg:#0b1016; --ll-panel:#121a23; --ll-line:#22303d; --ll-dim:#8796a5; }
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 100%; }
header[data-testid="stHeader"] { background: transparent; }
.ll-head { display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  border-bottom:1px solid var(--ll-line); padding-bottom:8px; margin-bottom:10px; }
.ll-logo {  font-weight:900; font-size:1.5rem; color:#3fb6a8; }
.ll-scn { font-size:1.05rem; font-weight:600; }
.ll-dim { color: var(--ll-dim); font-size:.85rem; }
.ll-clock {  font-size:1.05rem; color:#d7e0ea;
  background:#16212c; border:1px solid var(--ll-line); border-radius:4px; padding:2px 8px; }
.ll-badge { display:inline-block; font-size:.72rem; padding:1px 7px; border-radius:3px;
  border:1px solid currentColor; margin-right:4px; white-space:nowrap; font-weight:600; }
.ll-chip { display:inline-block; font-size:.7rem; letter-spacing:.02em; padding:1px 6px; border-radius:3px;
  color:#0b1016; font-weight:700; margin-right:4px; }
.ll-panel { background: var(--ll-panel); border:1px solid var(--ll-line); border-radius:6px;
  padding:10px 12px; margin-bottom:8px; }
.ll-panel h4 { margin:0 0 6px 0; font-size:.78rem; 
  color: var(--ll-dim); font-weight:600; }
.ll-summary { font-size:1.15rem; line-height:1.45; }
.ll-change { border-left:3px solid #b48ef0; padding:2px 8px; margin:4px 0; font-size:.88rem; }
.ll-strip { display:flex; gap:18px; flex-wrap:wrap; font-size:.85rem; }
.ll-strip b {  font-size:1.05rem; }
.ll-card { background:#0f161e; border:1px solid var(--ll-line); border-radius:6px;
  padding:8px 10px; margin:6px 0; }
.ll-card.t1 { border-left:4px solid #ff4d4f; } .ll-card.t2 { border-left:4px solid #ff9f1a; }
.ll-card.t3 { border-left:4px solid #e3c84b; } .ll-card.t4 { border-left:4px solid #5aa9e6; }
.ll-card.t5 { border-left:4px solid #7d8b99; } .ll-card.t9 { border-left:4px solid #3fb6a8; }
.ll-title { font-weight:600; font-size:.95rem; }
.ll-small { font-size:.8rem; color: var(--ll-dim); }
.ll-pc-ok { color:#2ea078; } .ll-pc-bad { color:#ff4d4f; }
.ll-quote { font-size:.84rem; color:#c6d2de; border-left:2px solid #3a4a5a; padding-left:8px;
  margin:3px 0; }
.ll-quote mark { background:#5b4a12; color:#ffe9a8; padding:0 2px; border-radius:2px; }
.ll-fact { font-size:.86rem; margin:2px 0; }
.ll-chain { display:flex; flex-wrap:wrap; align-items:center; gap:4px; font-size:.83rem; }
.ll-chain span.step { background:#16212c; border:1px solid var(--ll-line); border-radius:4px;
  padding:2px 6px; }
.ll-tl { font-size:.82rem; padding:3px 0 3px 8px; margin:2px 0; border-left:3px solid #333; }
.ll-tl .ts {  color: var(--ll-dim); margin-right:6px; }
.ll-bar { height:5px; background:#22303d; border-radius:3px; overflow:hidden; margin-top:3px; }
.ll-bar > div { height:100%; background:#3fb6a8; }
.ll-legend { font-size:.75rem; color: var(--ll-dim); display:flex; gap:10px; flex-wrap:wrap;
  margin-top:4px; }
.ll-legend i { display:inline-block; width:10px; height:10px; border-radius:50%;
  margin-right:4px; vertical-align:middle; }
div[data-testid="stTabs"] button p { font-size:.85rem; }
</style>
"""

EVENT_CSS = """
<style>
.ll-event { display:flex; gap:16px; align-items:stretch; background:#121a23; border:1px solid #22303d;
  border-left:6px solid #ff4d4f; border-radius:8px; padding:12px 16px; margin:6px 0 12px 0; }
.ll-event .when { min-width:110px; text-align:center; }
.ll-event .when b { font-size:2rem; font-weight:900; display:block; color:#fff; }
.ll-event .what { font-size:1.1rem; font-weight:700; }
.ll-event .src { font-size:.85rem; color:#8796a5; }
.ll-ai { background:#151d2b; border:1px solid #3b3260; border-left:6px solid #b48ef0; border-radius:8px;
  padding:10px 14px; margin:6px 0; }
.ll-ai .ttl { font-weight:900; color:#cdb8ff; }
</style>
"""
