"""Dashboard panels. Each function renders one part of the operator interface."""

from __future__ import annotations

import re
from html import escape

import pandas as pd
import streamlit as st

from lifeline.dashboard.theme import (
    CHANNEL_COLOR,
    CONN_COLOR,
    FACT_COLOR,
    TIER_COLOR,
    TIER_LABEL,
    TIMELINE_COLOR,
)
from lifeline.models import (
    AssetKind,
    AssetStatus,
    EvidenceRef,
    Fact,
    PrioritizedNeed,
)
from lifeline.optimization.prioritizer import TIER_NAMES
from lifeline.simulation.engine import Engine


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def tier_chip(t: int) -> str:
    return f'<span class="ll-sev"><i style="background:{TIER_COLOR[t]}"></i>{TIER_LABEL[t]}</span>'


def tier_dot(color: str) -> str:
    return f'<span class="ll-sev"><i style="background:{color}"></i></span>'


def badge(text: str, color: str) -> str:
    return f'<span class="ll-badge" style="color:{color}">{escape(text)}</span>'


def fmt_range(lo: int | None, hi: int | None) -> str:
    if lo is None and hi is None:
        return "unknown"
    if lo == hi or hi is None:
        return f"{lo}"
    if lo is None:
        return f"up to {hi}"
    return f"{lo}–{hi}"


def zone_name(engine: Engine, zid: str | None) -> str:
    return next((z.name for z in engine.scenario.zones if z.id == zid), "unknown location")


def highlight(text: str, quotes: list[str]) -> str:
    out = escape(text)
    for q in sorted(set(quotes), key=len, reverse=True):
        qe = escape(q.strip())
        if not qe:
            continue
        out = re.sub(re.escape(qe), lambda m: f"<mark>{m.group(0)}</mark>", out, flags=re.I)
    return out


def evidence_block(engine: Engine, refs: list[EvidenceRef]) -> str:
    texts = {c.id: (c.text, c.channel.value) for c in engine.comms}
    texts.update({e.id: (e.note, "telemetry") for e in engine.telemetry})
    by_src: dict[str, list[str]] = {}
    for r in refs:
        by_src.setdefault(r.source_id, []).append(r.quote)
    parts = []
    for sid, quotes in by_src.items():
        if sid not in texts:
            continue
        text, ch = texts[sid]
        parts.append(
            f'<div class="ll-quote">{badge(sid, CHANNEL_COLOR.get(ch, "#888"))}'
            f"{highlight(text, quotes)}</div>"
        )
    return "".join(parts)


def fact_line(engine: Engine, f: Fact) -> str:
    color = FACT_COLOR[f.status]
    flag = f' <span class="ll-small">{escape(f.flagged)}</span>' if f.flagged else ""
    srcs = ", ".join(sorted({r.source_id for r in f.evidence})) or "no evidence"
    return (
        f'<div class="ll-fact">{badge(f.status.value, color)}{escape(f.claim)}'
        f' <span class="ll-small">[{srcs}]</span>{flag}</div>'
    )


# --------------------------------------------------------------------------- header / overview


def header(engine: Engine, mode_label: str) -> None:
    m = engine.scenario.meta
    total = len(engine.checkpoint_times)
    html(
        f'<div class="ll-head"><span class="ll-logo">Lifeline</span>'
        f'<span class="ll-scn">{escape(m.name)}</span>'
        f'<span class="ll-dim">{escape(m.hazard_label)}</span>'
        f'<span class="ll-clock">T+{engine.t:.0f} min</span>'
        f'<span class="ll-dim">update {min(engine.cp_index, total)} of {total}</span>'
        f'<span class="ll-right">AI: {escape(mode_label)} · network actions simulated</span></div>'
    )


def overview(engine: Engine) -> None:
    pic = engine.intel.picture
    pr = engine.priorities
    c1, c2 = st.columns([1, 1])
    with c1:
        changes = [e for e in engine.timeline if e.kind == "intelligence"][-3:][::-1]
        ch_html = "".join(
            f'<div class="ll-change"><span class="ll-small">T+{e.t:.0f}</span> '
            f"{escape(e.text)} <span class='ll-small'>{escape(', '.join(e.refs[:6]))}</span></div>"
            for e in changes
        )
        html(
            f'<div class="ll-panel"><h4>Operational picture · v{pic.version} · '
            f"{escape(pic.interpreter)}</h4>"
            f'<div class="ll-summary">{escape(pic.summary)}</div>'
            f'<h4 style="margin-top:10px">What changed</h4>{ch_html or "<span class=ll-small>—</span>"}'
            f"</div>"
        )
    with c2:
        unserved = {t: sum(1 for p in pr if p.tier == t and p.served == "unserved") for t in (1, 2)}
        lo = sum(i.people_min or 0 for i in pic.incidents)
        hi = sum(i.people_max or i.people_min or 0 for i in pic.incidents)
        unknown = sum(1 for i in pic.incidents if i.people_min is None and i.people_max is None)
        isolated = [z for z, s in engine.snapshot.zones.items() if s.state.value == "isolated"]
        degraded = [z for z, s in engine.snapshot.zones.items() if s.state.value == "degraded"]
        free = [
            a.name.split(" ")[0]
            for a in engine.assets
            if a.status == AssetStatus.STANDBY
            and a.kind in (AssetKind.MOBILE_BS, AssetKind.SAT_TERMINAL, AssetKind.TETRA_SITE)
        ]
        cell_towers = [a for a in engine.assets if a.kind == AssetKind.CELL_TOWER]
        up = sum(1 for a in cell_towers if a.id in engine.snapshot.usable_assets)
        people = f"{lo}" if lo == hi else f"{lo}–{hi}"
        if unknown:
            people += f'<span class="ll-small"> +{unknown} unknown</span>'

        def cell(label: str, value: str, dot: str = "") -> str:
            d = f'<i class="dot" style="background:{dot}"></i>' if dot else ""
            return f'<div class="ll-m"><div class="lbl">{d}{label}</div><div class="val">{value}</div></div>'

        html(
            '<div class="ll-panel"><h4>Situation status</h4><div class="ll-grid">'
            + cell("Critical, unserved", str(unserved[1]), TIER_COLOR[1])
            + cell("High, unserved", str(unserved[2]), TIER_COLOR[2])
            + cell("Open needs", str(len(pr)))
            + cell("Incidents", str(len(pic.incidents)))
            + cell("People affected", people)
            + cell("Towers usable", f"{up}/{len(cell_towers)}")
            + cell("Zones degraded / cut off", f"{len(degraded)} / {len(isolated)}")
            + cell("Actions en route / in effect", f"{len(engine.pending)} / {len(engine.applied)}")
            + cell("Information gaps", str(len(pic.gaps)))
            + "</div>"
            + f'<div class="ll-small" style="margin-top:6px">Available resources: '
            f"{escape(', '.join(free) or 'none')}</div></div>"
        )


# --------------------------------------------------------------------------- recommendations


def recommendations(engine: Engine) -> None:
    rec = engine.recommendation
    tiers = {p.need.id: p for p in engine.priorities}
    if engine.pending:
        for p in engine.pending:
            html(
                f'<div class="ll-card t9"><span class="ll-title">{escape(p.intervention.label)}'
                f'</span><div class="ll-small">approved T+{p.approved_t:.0f} · active at '
                f"T+{p.activate_t:.0f} ({max(0.0, p.activate_t - engine.t):.0f} min)</div></div>"
            )
    if not rec.plan:
        html('<div class="ll-small">No intervention currently improves any unserved need.</div>')
    st.caption(
        "Plan chosen by deterministic optimizer: maximise needs served T1 → T5, then fewest "
        "scarce resources, then shortest ETA. Operator approval required."
    )
    for i, item in enumerate(rec.plan, start=1):
        iv = item.intervention
        top = min((tiers[n].tier for n in item.needs_served if n in tiers), default=9)
        pcs = "".join(
            f'<div class="ll-small"><span class="{"ll-pc-ok" if p.ok else "ll-pc-bad"}">'
            f"{'✔' if p.ok else '✘'}</span> {escape(p.name)}: {escape(p.detail)}</div>"
            for p in iv.preconditions
        )
        needs_html = "".join(
            f'<div class="ll-small">{tier_chip(tiers[n].tier)}{escape(tiers[n].need.who)}: '
            f"{escape(tiers[n].need.purpose)}</div>"
            for n in item.needs_served
            if n in tiers
        )
        scarce = f" · uses <b>{', '.join(iv.scarce)}</b>" if iv.scarce else " · no scarce resource"
        html(
            f'<div class="ll-card t{top if top < 9 else 9}"><span class="ll-title">#{i} '
            f"{escape(iv.label)}</span> {badge(iv.kind.replace('_', ' '), '#5aa9e6')}"
            f'<div class="ll-small">ETA {iv.eta_min:.0f} min{scarce} · {escape(item.rationale)}</div>'
            f'<div style="font-size:.85rem;margin:4px 0">{escape(iv.effect)}</div>'
            f"{needs_html}{pcs}</div>"
        )
        a, b, _ = st.columns([1, 1, 3])
        if a.button("Approve", key=f"ap-{iv.id}", type="primary"):
            engine.approve(iv.id)
            st.rerun()
        if b.button("Reject", key=f"rj-{iv.id}"):
            engine.reject(iv.id)
            st.rerun()
    with st.expander(f"Why not…? {len(rec.rejected)} alternatives considered"):
        order = {"dominated": 0, "infeasible": 1, "no_need": 2, "rejected_by_operator": 3}
        for r in sorted(rec.rejected, key=lambda r: order[r.reason_kind]):
            color = {
                "dominated": "#ff9f1a",
                "infeasible": "#ff4d4f",
                "no_need": "#7d8b99",
                "rejected_by_operator": "#b48ef0",
            }[r.reason_kind]
            html(
                f'<div class="ll-small" style="margin:3px 0">{badge(r.reason_kind, color)}'
                f"<b>{escape(r.intervention.label)}</b>: {escape(r.detail)}</div>"
            )


# --------------------------------------------------------------------------- needs


def needs_panel(engine: Engine) -> None:
    compare = st.toggle("Compare with telemetry-only view", value=False)
    if compare:
        c1, c2 = st.columns(2)
        with c1:
            html("<h4 class='ll-small'>Network telemetry only (zones by outage severity)</h4>")
            for i, zid in enumerate(engine.telemetry_rank, start=1):
                zs = engine.snapshot.zones[zid]
                html(
                    f'<div class="ll-small">{i}. <b>{escape(zone_name(engine, zid))}</b> '
                    f'<span style="color:{CONN_COLOR[zs.state]}">{zs.state.value}</span></div>'
                )
        with c2:
            html("<h4 class='ll-small'>Lifeline (needs by policy tier)</h4>")
            trank = {z: i for i, z in enumerate(engine.telemetry_rank, start=1)}
            for i, p in enumerate(engine.priorities, start=1):
                zr = trank.get(p.need.zone_id or "")
                jump = (
                    f' <span style="color:#ff9f1a">↑ zone was #{zr} on telemetry</span>'
                    if zr and zr > i
                    else ""
                )
                html(
                    f'<div class="ll-small">{i}. {tier_chip(p.tier)}{escape(p.need.who)} '
                    f"· {escape(zone_name(engine, p.need.zone_id))}{jump}</div>"
                )
        st.caption(
            "Telemetry shows where networks are degraded; only the intelligence layer "
            "shows who is affected and what is at stake there."
        )
        return
    for i, p in enumerate(engine.priorities, start=1):
        need_card(engine, i, p)
    resolved = [n for n in engine.intel.picture.needs if n.status != "open"]
    if resolved:
        with st.expander(f"{len(resolved)} resolved needs (kept for audit)"):
            for n in resolved:
                html(
                    f'<div class="ll-small">{escape(n.id)} {escape(n.who)}: {escape(n.resolution)}</div>'
                )


def need_card(engine: Engine, rank: int, p: PrioritizedNeed) -> None:
    n = p.need
    served_color = {"served": "#2ea078", "planned": "#ffd250", "unserved": "#ff4d4f"}[p.served]
    crit = " · ".join(f"{c.criterion}: <b>{escape(c.value)}</b>" for c in p.criteria)
    flags = "".join(
        f'<div class="ll-small" style="color:#ff9f1a">{escape(f)}</div>' for f in p.flags
    )
    html(
        f'<div class="ll-card t{p.tier}"><span class="ll-title">{rank}. {tier_chip(p.tier)}'
        f"{escape(n.who)}</span> {badge(p.served, served_color)}"
        f"{badge(n.service.value, '#5aa9e6')}"
        f'<div style="font-size:.86rem">{escape(n.purpose)}</div>'
        f'<div class="ll-small">{escape(zone_name(engine, n.zone_id))} · '
        f"people {fmt_range(n.people_min, n.people_max)} · confidence {n.confidence:.0%} · "
        f"tier rule: {escape(TIER_NAMES[p.tier])} ({escape(p.tier_rule)})</div>"
        f'<div class="ll-small">{crit}</div>{flags}</div>'
    )
    if n.evidence:
        with st.expander(f"Evidence for {n.id}"):
            html(evidence_block(engine, n.evidence))


# --------------------------------------------------------------------------- intelligence


def intelligence_panel(engine: Engine) -> None:
    pic = engine.intel.picture
    if not pic.incidents:
        html('<div class="ll-small">No incidents yet.</div>')
    for inc in pic.incidents:
        lt = {"yes": "#ff4d4f", "no": "#2ea078", "unknown": "#7d8b99"}[inc.life_threat.value]
        rel = ", ".join(f"{r.relation} {r.incident_id}" for r in inc.related)
        with st.expander(
            f"{inc.id} · {inc.title} · {len(inc.comm_ids)} report(s) · "
            f"{zone_name(engine, inc.zone_id)}"
        ):
            html(
                f'<div class="ll-small">{badge("life threat: " + inc.life_threat.value, lt)}'
                f"people {fmt_range(inc.people_min, inc.people_max)} · reports: "
                f"{escape(', '.join(inc.comm_ids))}{(' · ' + escape(rel)) if rel else ''}</div>"
                f'<div class="ll-bar"><div style="width:{inc.confidence * 100:.0f}%"></div></div>'
                f'<div class="ll-small">confidence {inc.confidence:.0%}</div>'
            )
            html("".join(fact_line(engine, f) for f in inc.facts))
            html(evidence_block(engine, [r for f in inc.facts for r in f.evidence]))
    if pic.dependencies:
        html("<h4 class='ll-small' style='margin-top:10px'>DEPENDENCY CHAINS</h4>")
        for d in pic.dependencies:
            steps = '<span class="ll-small">→</span>'.join(
                f'<span class="step">{escape(s.description)}</span>' for s in d.steps
            )
            html(
                f'<div class="ll-card t9"><div class="ll-title">{escape(d.title)} '
                f"{badge(d.status.value, FACT_COLOR[d.status])}</div>"
                f'<div class="ll-chain">{steps}</div></div>'
            )
    if pic.contradictions:
        html("<h4 class='ll-small' style='margin-top:10px'>CONTRADICTIONS</h4>")
        for c in pic.contradictions:
            html(
                f'<div class="ll-card t2"><div style="font-size:.86rem">{escape(c.description)}</div>'
                f'<div class="ll-small">verify: {escape(c.suggested_verification)}</div>'
                f"{evidence_block(engine, c.evidence)}</div>"
            )
    if pic.gaps:
        html("<h4 class='ll-small' style='margin-top:10px'>INFORMATION GAPS</h4>")
        for g in pic.gaps:
            html(
                f'<div class="ll-small">? <b>{escape(g.question)}</b> '
                f"{escape(g.why_it_matters)}</div>"
            )
    if pic.unlinked_comm_ids:
        html(
            f'<div class="ll-small" style="margin-top:8px">Not yet linked to an incident (kept): '
            f"{escape(', '.join(pic.unlinked_comm_ids))}</div>"
        )
    if pic.guard_notes:
        with st.expander(f"Evidence guard actions ({len(pic.guard_notes)})"):
            for note in pic.guard_notes:
                html(f'<div class="ll-small">{escape(note)}</div>')


# --------------------------------------------------------------------------- channels


def channels_panel(engine: Engine) -> None:
    rows = []
    kinds = {
        "Cellular towers": AssetKind.CELL_TOWER,
        "Mobile base stations": AssetKind.MOBILE_BS,
        "Satellite terminals": AssetKind.SAT_TERMINAL,
        "Mesh nodes": AssetKind.MESH_NODE,
        "TETRA sites": AssetKind.TETRA_SITE,
    }
    for label, k in kinds.items():
        items = [a for a in engine.assets if a.kind == k]
        if not items:
            continue
        rows.append(
            {
                "technology": label,
                "usable": sum(1 for a in items if a.id in engine.snapshot.usable_assets),
                "standby": sum(1 for a in items if a.status == AssetStatus.STANDBY),
                "unusable": sum(1 for a in items if a.id in engine.snapshot.unusable),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    zrows = []
    for z in engine.scenario.zones:
        zs = engine.snapshot.zones[z.id]
        zrows.append(
            {
                "zone": z.name,
                "state": zs.state.value,
                "cap/demand": f"{zs.capacity:.0f}/{zs.demand:.0f}",
                "cellular": zs.channels["cellular"],
                "satellite": zs.channels["satellite"],
                "mesh": zs.channels["mesh"],
                "tetra": zs.channels["tetra"],
                "priority access": "on" if zs.priority_access else "",
                "road": z.road_access.value,
            }
        )
    st.dataframe(pd.DataFrame(zrows), hide_index=True, use_container_width=True)
    if engine.snapshot.unusable:
        with st.expander("Unusable assets and causes"):
            for a, why in engine.snapshot.unusable.items():
                html(f'<div class="ll-small"><b>{escape(a)}</b>: {escape(why)}</div>')


# --------------------------------------------------------------------------- feed / timeline


def feed(engine: Engine) -> None:
    if not engine.comms and not engine.telemetry:
        html(
            '<div class="ll-small">Waiting for the first communications. Press Step or Play.</div>'
        )
    items = [(c.t, "c", c.id) for c in engine.comms] + [(e.t, "t", e.id) for e in engine.telemetry]
    comms = {c.id: c for c in engine.comms}
    tel = {e.id: e for e in engine.telemetry}
    for t, kind, iid in sorted(items, reverse=True)[:40]:
        if kind == "t":
            e = tel[iid]
            html(
                f'<div class="ll-tl" style="border-color:#7d8b99"><span class="ts">T+{t:.0f}</span>'
                f"{badge('telemetry', '#7d8b99')}{escape(e.note)}</div>"
            )
            continue
        c = comms[iid]
        it = engine.intel.interpretations.get(c.id)
        floor_color = {"life_safety": "#ff4d4f", "elevated": "#ff9f1a", "none": "#55606b"}
        interp = ""
        if it:
            interp = (
                f'<div class="ll-small">↳ {escape(it.summary)} · life threat '
                f"{it.life_threat.value} · conf {it.confidence:.0%} · {escape(it.interpreter)}</div>"
            )
        html(
            f'<div class="ll-tl" style="border-color:{CHANNEL_COLOR.get(c.channel.value, "#888")}">'
            f'<span class="ts">T+{t:.0f}</span>{badge(c.channel.value, CHANNEL_COLOR.get(c.channel.value, "#888"))}'
            f"{badge(c.source_role.value, '#8796a5')}"
            f"{badge('pre-triage: ' + c.triage_floor.value, floor_color[c.triage_floor.value])}"
            f'<span class="ll-small">{escape(c.id)} · {escape(c.delivery_status)}</span>'
            f'<div style="font-size:.86rem">{escape(c.text)}</div>{interp}</div>'
        )


def timeline(engine: Engine) -> None:
    kinds = st.multiselect(
        "Show",
        list(TIMELINE_COLOR),
        default=["intelligence", "priority", "decision", "outcome", "telemetry"],
        label_visibility="collapsed",
    )
    for e in reversed(engine.timeline):
        if e.kind not in kinds:
            continue
        html(
            f'<div class="ll-tl" style="border-color:{TIMELINE_COLOR[e.kind]}">'
            f'<span class="ts">T+{e.t:.0f}</span>{badge(e.kind, TIMELINE_COLOR[e.kind])}'
            f"{escape(e.text)}</div>"
        )


def event_banner(engine: Engine) -> None:
    """Exactly what just happened, when and where."""
    if not engine.last_event_ids:
        html(
            '<div class="ll-event" style="border-left-color:#3fb6a8"><div class="what">'
            "Waiting for the first event. Press ▶ Play.</div></div>"
        )
        return
    comms = {c.id: c for c in engine.comms}
    tel = {e.id: e for e in engine.telemetry}
    zones = {z.id: z.name for z in engine.scenario.zones}
    for eid in engine.last_event_ids[:3]:
        if eid in tel:
            e = tel[eid]
            html(
                f'<div class="ll-event"><div class="when">'
                f'<b>T+{e.t:.0f}</b><span class="src">minutes</span></div>'
                f'<div><div class="src">Infrastructure alert · network telemetry · {escape(e.asset_id)}</div>'
                f'<div class="what">{escape(e.note)}</div></div></div>'
            )
        elif eid in comms:
            c = comms[eid]
            it = engine.intel.interpretations.get(c.id)
            where = zones.get(it.zone_id or "", "location unknown") if it else ""
            ai = (
                f'<div class="ai"><span>AI interpretation</span>{escape(it.summary)} '
                f"· life threat: <b>{it.life_threat.value}</b> · {escape(where)} "
                f"· confidence {it.confidence:.0%}</div>"
                if it
                else ""
            )
            color = "#e5484d" if c.triage_floor.value == "life_safety" else "#6b8fc7"
            html(
                f'<div class="ll-event"><div class="when">'
                f'<b>T+{c.t:.0f}</b><span class="src">minutes</span></div>'
                f'<div><div class="src">{tier_dot(color)}Incoming {escape(c.channel.value.replace("_", " "))} message '
                f"· from {escape(c.source_role.value)}</div>"
                f'<div class="what">“{escape(c.text)}”</div>{ai}</div></div>'
            )


def ai_decisions(engine: Engine) -> None:
    decisions = [e for e in engine.timeline if e.kind in ("decision", "outcome")][::-1]
    html(
        '<div class="ll-ai"><div class="ttl">Autonomous coordination</div>'
        '<div class="ll-small">The AI reads every message and decides what matters most. Lifeline then '
        "carries out the best feasible actions, most urgent first. Each decision shows its reason.</div></div>"
    )
    if not decisions:
        html('<div class="ll-small">No actions yet.</div>')
    for e in decisions[:12]:
        if e.kind == "outcome":
            body = e.text.replace("Simulated effect applied: ", "")
            action, _, result = body.partition(". ")
            html(
                f'<div class="ll-tl"><span class="ts">T+{e.t:.0f}</span>'
                f'<span class="ll-sev"><i style="background:#3dd68c"></i>In effect</span>'
                f'<div class="ll-small" style="margin-top:2px">{escape(result or action)}</div></div>'
            )
            continue
        body = e.text.replace("Lifeline AUTO-APPROVED: ", "").replace("Operator APPROVED: ", "")
        action, _, reason = body.partition(". Reason: ")
        html(
            f'<div class="ll-card"><div class="ll-small">T+{e.t:.0f} min · decided by Lifeline</div>'
            f'<div class="ll-title" style="margin:2px 0 4px 0">{escape(action.rstrip("."))}</div>'
            f'<div class="ll-small"><b style="color:#c9d0da">Why:</b> '
            f"{escape(reason or 'operator decision')}</div></div>"
        )
    if engine.pending:
        for p in engine.pending:
            html(
                f'<div class="ll-card"><span class="ll-sev"><i style="background:#f5a524"></i>En route</span>{escape(p.intervention.label)}, arrives '
                f"T+{p.activate_t:.0f} min</div>"
            )


# --------------------------------------------------------------------------- incident timeline + workflow


def disaster_timeline(engine: Engine) -> None:
    """Horizontal time axis: when the hazard struck, every event so far, and 'now'."""
    m = engine.scenario.meta
    end = max(max((e.t for e in engine.scenario.events), default=10.0), engine.t) + 5

    def x(t: float) -> float:
        return 2 + 96 * t / end

    comms = {c.id: c for c in engine.comms}
    marks = []
    for ev in engine.scenario.events:
        if ev.t > engine.t or engine.cp_index == 0:
            continue
        eid = str(ev.payload["id"])
        if ev.kind == "telemetry":
            color, label = "#f5a524", str(ev.payload.get("note", ""))
        else:
            c = comms.get(eid)
            color = "#e5484d" if c and c.triage_floor.value == "life_safety" else "#6b8fc7"
            label = str(ev.payload.get("text", ""))
        marks.append(
            f'<div class="tl-dot" title="T+{ev.t:.0f}: {escape(label[:120])}" '
            f'style="left:{x(ev.t):.1f}%;background:{color}"></div>'
        )
    started = engine.cp_index > 0
    onset = (
        f'<div class="tl-onset" style="left:{x(0):.1f}%"></div>'
        f'<div class="tl-onset-lbl" style="left:{x(0):.1f}%">{escape(m.hazard_label)} strikes · T+0</div>'
        if started
        else ""
    )
    now = f'<div class="tl-now" style="left:{x(engine.t):.1f}%"><span>now T+{engine.t:.0f} min</span></div>'
    status = (
        f'<span class="tl-alert">{escape(m.hazard_label)} struck {engine.t:.0f} min ago</span>'
        if started
        else '<span class="ll-small">Normal operations. Press ▶ to start the incident.</span>'
    )
    html(
        f'<div class="tl-wrap"><div class="tl-head">{status}'
        '<span class="ll-small">'
        '<i class="dot" style="background:#f5a524"></i>infrastructure alert '
        '<i class="dot" style="background:#e5484d"></i>life-threatening message '
        '<i class="dot" style="background:#6b8fc7"></i>other message</span></div>'
        f'<div class="tl-axis">{onset}{"".join(marks)}{now}</div></div>'
    )


def _stage(n: int, title: str, body: str, delay: float, key: str) -> str:
    return (
        f'<div class="wf-step" data-k="{key}" style="animation-delay:{delay:.1f}s">'
        f'<div class="wf-n">{n}</div><div class="wf-t">{title}</div><div class="wf-b">{body}</div></div>'
    )


def workflow(engine: Engine) -> None:
    """The pipeline for the latest update, revealed stage by stage."""
    if engine.cp_index == 0:
        html(
            '<div class="wf-empty">Lifeline is idle. When the incident starts, each update is '
            "shown here as it moves through the platform: message received → AI interpretation → "
            "priority → decision → network result.</div>"
        )
        return
    key = f"{engine.scenario.meta.id}-{engine.cp_index}-{len(engine.applied)}"
    comms = {c.id: c for c in engine.comms}
    tel = {e.id: e for e in engine.telemetry}
    ids = engine.last_event_ids
    new_comms = [comms[i] for i in ids if i in comms]
    new_tel = [tel[i] for i in ids if i in tel]
    zones = {z.id: z.name for z in engine.scenario.zones}
    t = engine.t

    # 1. incoming
    if new_comms:
        c = sorted(new_comms, key=lambda c: c.triage_floor.value != "life_safety")[0]
        incoming = (
            f'<div class="wf-src">{escape(c.channel.value.replace("_", " "))} · {escape(c.source_role.value)}'
            f' · {escape(c.delivery_status)}</div><div class="wf-msg">“{escape(c.text)}”</div>'
        )
    else:
        c = None
        incoming = "".join(
            f'<div class="wf-src">network telemetry · {escape(e.asset_id)}</div>'
            f'<div class="wf-msg">{escape(e.note)}</div>'
            for e in new_tel[:3]
        )

    # 2. AI interpretation
    changes = [e for e in engine.timeline if e.kind == "intelligence" and e.t == t]
    it = engine.intel.interpretations.get(c.id) if c else None
    if it:
        lt = {"yes": "#e5484d", "no": "#3dd68c", "unknown": "#8a94a3"}[it.life_threat.value]
        people = fmt_range(it.people_min, it.people_max)
        facts = "".join(
            f'<div class="wf-fact"><span class="fs">{f.status.value}</span>{escape(f.claim)}</div>'
            for f in it.facts[:3]
        )
        ai = (
            f'<div class="wf-kv"><span>Life threat</span><b style="color:{lt}">{it.life_threat.value}</b>'
            f"<span>Where</span><b>{escape(zones.get(it.zone_id or '', 'unknown'))}</b>"
            f"<span>People</span><b>{escape(people)}</b>"
            f"<span>Confidence</span><b>{it.confidence:.0%}</b></div>{facts}"
        )
    else:
        ai = (
            '<div class="wf-msg">'
            + escape(changes[0].text if changes else engine.intel.picture.summary)
            + "</div>"
        )
    if changes and it:
        ai += f'<div class="wf-note">Picture updated: {escape(changes[0].text)}</div>'

    # 3. priority
    linked = [
        p for p in engine.priorities if c and (any(r.source_id == c.id for r in p.need.evidence))
    ] or [p for p in engine.priorities if p.need.first_seen_t == t]
    if linked:
        prio = "".join(
            f'<div class="wf-need">{tier_chip(p.tier)}<div>{escape(p.need.who)}</div>'
            f'<div class="ll-small">{escape(zones.get(p.need.zone_id or "", "location unknown"))} · '
            f"needs {escape(p.need.service.value.replace('_', ' '))} · {escape(p.served)}</div></div>"
            for p in linked[:2]
        )
    else:
        prio = '<div class="ll-small">No new communication need identified.</div>'

    # 4. decision
    decisions = [e for e in engine.timeline if e.kind == "decision" and e.t == t]
    if decisions:
        dec = ""
        for d in decisions[:2]:
            body = d.text.replace("Lifeline AUTO-APPROVED: ", "")
            action, _, why = body.partition(". Reason: ")
            dec += (
                f'<div class="wf-dec">{escape(action.rstrip("."))}</div>'
                f'<div class="ll-small">Why: {escape(why)}</div>'
            )
    else:
        covered = [p for p in linked if p.served in ("planned", "served")]
        if covered:
            p0 = covered[0]
            how = ", ".join(p0.served_by) if p0.served_by else "existing network capacity"
            dec = (
                f'<div class="wf-dec">No new action needed</div><div class="ll-small">'
                f"{escape(p0.need.who)} is already covered by {escape(how)}.</div>"
            )
        elif linked:
            dec = (
                '<div class="wf-dec">No feasible action yet</div><div class="ll-small">'
                "Need stays open and visible; Lifeline re-plans as resources change.</div>"
            )
        else:
            dec = '<div class="ll-small">No action required for this update.</div>'

    # 5. result
    outcomes = [e for e in engine.timeline if e.kind == "outcome" and e.t == t]
    res = "".join(
        f'<div class="wf-res">{escape(o.text.replace("Simulated effect applied: ", ""))}</div>'
        for o in outcomes[:2]
    )
    res += "".join(
        f'<div class="wf-res">En route: {escape(p.intervention.label)}, arrives T+{p.activate_t:.0f} min</div>'
        for p in engine.pending
        if p.approved_t == t
    )
    if not res:
        res = '<div class="ll-small">Network state unchanged.</div>'

    stages = [
        ("Message received", incoming),
        ("AI interprets", ai),
        ("Urgency assessed", prio),
        ("Lifeline decides", dec),
        ("Network result", res),
    ]
    html(
        f'<div class="wf" data-k="{key}"><div class="wf-title">Update at T+{t:.0f} min: how Lifeline '
        'handled it</div><div class="wf-row">'
        + '<div class="wf-arrow">›</div>'.join(
            _stage(i + 1, title, body, i * 0.9, key) for i, (title, body) in enumerate(stages)
        )
        + "</div></div>"
    )
