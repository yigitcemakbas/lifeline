"""Dashboard panels. Each function renders one part of the operator interface."""

from __future__ import annotations

import re
from html import escape

import streamlit as st

from lifeline.dashboard.theme import (
    ASSET_LEGEND,
    CHANNEL_COLOR,
    CONN_COLOR,
    FACT_COLOR,
    REJECTION_COLOR,
    SERVED_STYLE,
    TIER_COLOR,
    TIER_LABEL,
)
from lifeline.models import (
    AssetKind,
    AssetStatus,
    ConnState,
    EvidenceRef,
    Fact,
    PrioritizedNeed,
)
from lifeline.simulation.engine import Engine

MAX_LOG_ITEMS = 12


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def dot(color: str) -> str:
    return f'<i class="ll-dot" style="background:{color}"></i>'


def tier_chip(t: int) -> str:
    return f'<span class="ll-sev">{dot(TIER_COLOR[t])}{TIER_LABEL[t]}</span>'


def badge(text: str, color: str = "#aeb6c2") -> str:
    return f'<span class="ll-badge" style="color:{color}">{escape(text)}</span>'


def fmt_range(lo: int | None, hi: int | None) -> str:
    if lo is None and hi is None:
        return "unknown"
    if lo == hi or hi is None:
        return f"{lo}"
    if lo is None:
        return f"up to {hi}"
    return f"{lo}–{hi}"


def zone_names(engine: Engine) -> dict[str, str]:
    return {z.id: z.name for z in engine.scenario.zones}


def highlight(text: str, quotes: list[str]) -> str:
    out = escape(text)
    for q in sorted(set(quotes), key=len, reverse=True):
        qe = escape(q.strip())
        if qe:
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
        if sid in texts:
            text, ch = texts[sid]
            parts.append(
                f'<div class="ll-quote">{badge(sid, CHANNEL_COLOR.get(ch, "#8a94a3"))}'
                f"{highlight(text, quotes)}</div>"
            )
    return "".join(parts)


def fact_line(f: Fact) -> str:
    flag = f' <span class="ll-small">{escape(f.flagged)}</span>' if f.flagged else ""
    srcs = ", ".join(sorted({r.source_id for r in f.evidence})) or "no evidence"
    return (
        f'<div class="ll-fact">{badge(f.status.value, FACT_COLOR[f.status])}{escape(f.claim)}'
        f' <span class="ll-small">[{escape(srcs)}]</span>{flag}</div>'
    )


# --------------------------------------------------------------------------- header


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


def incident_timeline(engine: Engine) -> None:
    """Horizontal time axis: incident onset, every event released so far, and the clock."""
    m = engine.scenario.meta
    end = max(max((e.t for e in engine.scenario.events), default=10.0), engine.t) + 5
    started = engine.cp_index > 0

    def x(t: float) -> float:
        return 2 + 96 * t / end

    comms = {c.id: c for c in engine.comms}
    marks = []
    for ev in engine.scenario.events:
        if not started or ev.t > engine.t:
            continue
        if ev.kind == "telemetry":
            color, label = "#f5a524", str(ev.payload.get("note", ""))
        else:
            c = comms.get(str(ev.payload["id"]))
            color = "#e5484d" if c and c.triage_floor.value == "life_safety" else "#6b8fc7"
            label = str(ev.payload.get("text", ""))
        marks.append(
            f'<div class="tl-dot" title="T+{ev.t:.0f}: {escape(label[:120])}" '
            f'style="left:{x(ev.t):.1f}%;background:{color}"></div>'
        )
    onset = (
        f'<div class="tl-onset" style="left:{x(0):.1f}%"></div>'
        f'<div class="tl-onset-lbl" style="left:{x(0):.1f}%">Onset · T+0</div>'
        if started
        else ""
    )
    now = (
        f'<div class="tl-now" style="left:{x(engine.t):.1f}%">'
        f"<span>T+{engine.t:.0f} min</span></div>"
    )
    status = (
        f'<span class="tl-alert">{escape(m.hazard_label)} · {engine.t:.0f} min since onset</span>'
        if started
        else '<span class="ll-small">No events released yet. Press Play or Step.</span>'
    )
    legend = (
        f'<span class="ll-small">{dot("#f5a524")}infrastructure alert '
        f"{dot('#e5484d')}life-threatening report {dot('#6b8fc7')}other report</span>"
    )
    html(
        f'<div class="tl-wrap"><div class="tl-head">{status}{legend}</div>'
        f'<div class="tl-axis">{onset}{"".join(marks)}{now}</div></div>'
    )


# --------------------------------------------------------------------------- pipeline view


def _stage(n: int, title: str, body: str, delay: float) -> str:
    return (
        f'<div class="wf-step" style="animation-delay:{delay:.1f}s">'
        f'<div class="wf-n">{n}</div><div class="wf-t">{title}</div>'
        f'<div class="wf-b">{body}</div></div>'
    )


def workflow(engine: Engine) -> None:
    """How the latest update moved through the pipeline: report, interpretation, priority."""
    if engine.cp_index == 0:
        html(
            '<div class="wf-empty">Each update is traced here as it moves through the pipeline: '
            "report received, AI interpretation, resulting priority and action.</div>"
        )
        return
    comms = {c.id: c for c in engine.comms}
    tel = {e.id: e for e in engine.telemetry}
    new_comms = [comms[i] for i in engine.last_event_ids if i in comms]
    new_tel = [tel[i] for i in engine.last_event_ids if i in tel]
    zones = zone_names(engine)
    t = engine.t

    c = min(new_comms, key=lambda c: c.triage_floor.value != "life_safety", default=None)
    if c is not None:
        incoming = (
            f'<div class="wf-src">{escape(c.channel.value.replace("_", " "))} · '
            f"{escape(c.source_role.value)} · {escape(c.delivery_status)}</div>"
            f'<div class="wf-msg">“{escape(c.text)}”</div>'
        )
    else:
        incoming = "".join(
            f'<div class="wf-src">network telemetry · {escape(e.asset_id)}</div>'
            f'<div class="wf-msg">{escape(e.note)}</div>'
            for e in new_tel[:3]
        )

    changes = [e for e in engine.timeline if e.kind == "intelligence" and e.t == t]
    it = engine.intel.interpretations.get(c.id) if c else None
    if it:
        lt = {"yes": "#e5484d", "no": "#3dd68c", "unknown": "#8a94a3"}[it.life_threat.value]
        facts = "".join(
            f'<div class="wf-fact"><span class="fs">{f.status.value}</span>{escape(f.claim)}</div>'
            for f in it.facts[:3]
        )
        ai = (
            f'<div class="wf-kv"><span>Life threat</span>'
            f'<b style="color:{lt}">{it.life_threat.value}</b>'
            f"<span>Where</span><b>{escape(zones.get(it.zone_id or '', 'unknown'))}</b>"
            f"<span>People</span><b>{escape(fmt_range(it.people_min, it.people_max))}</b>"
            f"<span>Confidence</span><b>{it.confidence:.0%}</b></div>{facts}"
        )
        if changes:
            ai += f'<div class="wf-note">Picture updated: {escape(changes[0].text)}</div>'
    else:
        summary = changes[0].text if changes else engine.intel.picture.summary
        ai = f'<div class="wf-msg">{escape(summary)}</div>'

    linked = [
        p for p in engine.priorities if c and any(r.source_id == c.id for r in p.need.evidence)
    ] or [p for p in engine.priorities if p.need.first_seen_t == t]
    if linked:
        prio = "".join(
            f'<div class="wf-need">{tier_chip(p.tier)}<div>{escape(p.need.who)}</div>'
            f'<div class="ll-small">{escape(zones.get(p.need.zone_id or "", "location unknown"))}'
            f" · needs {escape(p.need.service.value.replace('_', ' '))} · "
            f"{escape(SERVED_STYLE[p.served][0].lower())}</div>{algo_lines(p)}</div>"
            for p in linked[:2]
        )
    else:
        prio = '<div class="ll-small">No new communication need identified.</div>'

    decisions = [e for e in engine.timeline if e.kind == "decision" and e.t == t]
    outcomes = [e for e in engine.timeline if e.kind == "outcome" and e.t == t]
    if decisions:
        prio += '<div class="wf-note">Action taken</div>' + "".join(
            f'<div class="wf-dec">{escape(d.text)}</div>'
            f'<div class="ll-small">Why: {escape(d.detail)}</div>'
            for d in decisions[:2]
        )
    prio += "".join(
        f'<div class="wf-res">{escape(o.text)}: {escape(o.detail)}</div>' for o in outcomes[:2]
    )

    stages = [
        ("Report received", incoming),
        ("AI interpretation", ai),
        ("Resulting priority", prio),
    ]
    html(
        f'<div class="wf"><div class="wf-title">Update at T+{t:.0f} min</div><div class="wf-row">'
        + '<div class="wf-arrow">›</div>'.join(
            _stage(i + 1, title, body, i * 0.6) for i, (title, body) in enumerate(stages)
        )
        + "</div></div>"
    )


# --------------------------------------------------------------------------- map legend


def map_legend() -> None:
    zones = "".join(
        f"<span>{dot(CONN_COLOR[s])}{label}</span>"
        for s, label in [
            (ConnState.CONNECTED, "connected"),
            (ConnState.CONGESTED, "congested"),
            (ConnState.DEGRADED, "degraded"),
            (ConnState.ISOLATED, "cut off"),
        ]
    )
    assets = "".join(f"<span>{dot(color)}{label}</span>" for label, color in ASSET_LEGEND)
    html(
        f'<div class="ll-legend">{zones}{assets}'
        '<span><i class="ll-dot ll-ring"></i>latest event</span></div>'
    )


# --------------------------------------------------------------------------- overview


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
            f'<h4 class="ll-sub">What changed</h4>'
            f"{ch_html or '<span class=ll-small>No changes yet.</span>'}</div>"
        )
    with c2:
        unserved = {t: sum(1 for p in pr if p.tier == t and p.served == "unserved") for t in (1, 2)}
        lo = sum(i.people_min or 0 for i in pic.incidents)
        hi = sum(i.people_max or i.people_min or 0 for i in pic.incidents)
        unknown = sum(1 for i in pic.incidents if i.people_min is None and i.people_max is None)
        states = [s.state for s in engine.snapshot.zones.values()]
        free = [
            a.name
            for a in engine.assets
            if a.status == AssetStatus.STANDBY
            and a.kind in (AssetKind.MOBILE_BS, AssetKind.SAT_TERMINAL, AssetKind.TETRA_SITE)
        ]
        towers = [a for a in engine.assets if a.kind == AssetKind.CELL_TOWER]
        up = sum(1 for a in towers if a.id in engine.snapshot.usable_assets)
        people = f"{lo}" if lo == hi else f"{lo}–{hi}"
        if unknown:
            people += f'<span class="ll-small"> +{unknown} unknown</span>'

        def cell(label: str, value: str, color: str = "") -> str:
            d = dot(color) if color else ""
            return (
                f'<div class="ll-m"><div class="lbl">{d}{label}</div>'
                f'<div class="val">{value}</div></div>'
            )

        n_degraded = states.count(ConnState.DEGRADED)
        n_isolated = states.count(ConnState.ISOLATED)
        html(
            '<div class="ll-panel"><h4>Situation status</h4><div class="ll-grid">'
            + cell("Critical, unserved", str(unserved[1]), TIER_COLOR[1])
            + cell("High, unserved", str(unserved[2]), TIER_COLOR[2])
            + cell("Open needs", str(len(pr)))
            + cell("Incidents", str(len(pic.incidents)))
            + cell("People affected", people)
            + cell("Towers usable", f"{up}/{len(towers)}")
            + cell("Zones degraded / cut off", f"{n_degraded} / {n_isolated}")
            + cell("Actions en route / in effect", f"{len(engine.pending)} / {len(engine.applied)}")
            + cell("Information gaps", str(len(pic.gaps)))
            + "</div>"
            + f'<div class="ll-small ll-gap">Standby resources: '
            f"{escape(', '.join(free) or 'none')}</div></div>"
        )
    with st.expander("Incidents, dependencies, contradictions and information gaps"):
        intelligence_details(engine)


def intelligence_details(engine: Engine) -> None:
    pic = engine.intel.picture
    zones = zone_names(engine)
    if not pic.incidents:
        html('<div class="ll-small">No incidents yet.</div>')
    for inc in pic.incidents:
        lt = {"yes": "#e5484d", "no": "#3dd68c", "unknown": "#8a94a3"}[inc.life_threat.value]
        rel = ", ".join(f"{r.relation.replace('_', ' ')} {r.incident_id}" for r in inc.related)
        html(
            f'<div class="ll-card"><div class="ll-title">{escape(inc.id)} · {escape(inc.title)}'
            f"</div>"
            f'<div class="ll-small">{badge("life threat: " + inc.life_threat.value, lt)}'
            f"{escape(zones.get(inc.zone_id or '', 'location unknown'))} · people "
            f"{fmt_range(inc.people_min, inc.people_max)} · confidence {inc.confidence:.0%} · "
            f"reports {escape(', '.join(inc.comm_ids))}{(' · ' + escape(rel)) if rel else ''}"
            f"</div>{''.join(fact_line(f) for f in inc.facts)}"
            f"{evidence_block(engine, [r for f in inc.facts for r in f.evidence])}</div>"
        )
    if pic.dependencies:
        html('<h4 class="ll-sub">Dependency chains</h4>')
        for d in pic.dependencies:
            steps = '<span class="ll-small">→</span>'.join(
                f'<span class="step">{escape(s.description)}</span>' for s in d.steps
            )
            html(
                f'<div class="ll-card"><div class="ll-title">{escape(d.title)} '
                f"{badge(d.status.value, FACT_COLOR[d.status])}</div>"
                f'<div class="ll-chain">{steps}</div></div>'
            )
    if pic.contradictions:
        html('<h4 class="ll-sub">Contradictions</h4>')
        for c in pic.contradictions:
            html(
                f'<div class="ll-card"><div class="ll-fact">{escape(c.description)}</div>'
                f'<div class="ll-small">Verify: {escape(c.suggested_verification)}</div>'
                f"{evidence_block(engine, c.evidence)}</div>"
            )
    if pic.gaps:
        html('<h4 class="ll-sub">Information gaps</h4>')
        for g in pic.gaps:
            why = f" · {escape(g.why_it_matters)}" if g.why_it_matters else ""
            html(f'<div class="ll-small"><b>{escape(g.question)}</b>{why}</div>')
    if pic.unlinked_comm_ids:
        html(
            '<div class="ll-small ll-gap">Not yet linked to an incident (kept visible): '
            f"{escape(', '.join(pic.unlinked_comm_ids))}</div>"
        )
    if pic.guard_notes:
        html('<h4 class="ll-sub">Evidence guard actions</h4>')
        html("".join(f'<div class="ll-small">{escape(n)}</div>' for n in pic.guard_notes))


# --------------------------------------------------------------------------- priority queue


ALGO_LINES = [
    ("Travel time to help", "access to help"),
    ("Life at risk", "life threat"),
    ("Bandwidth", "bandwidth"),
]


def criterion(p: PrioritizedNeed, name: str) -> str:
    return next((c.value for c in p.criteria if c.criterion == name), "")


def algo_lines(p: PrioritizedNeed) -> str:
    """The deterministic results behind a rank: routing, evidence fusion, flow allocation."""
    return "".join(
        f'<div class="ll-small">{label}: {escape(criterion(p, key))}</div>'
        for label, key in ALGO_LINES
        if criterion(p, key)
    )


def priority_queue(engine: Engine) -> None:
    """Every open need ranked by urgency, most urgent first."""
    pr = engine.priorities
    html(
        '<div class="ll-section"><div class="ttl">Priority queue</div>'
        '<div class="ll-small">Open communication needs, most urgent first. Scarce capacity goes '
        "to the top of the list first; nothing is dropped.</div></div>"
    )
    if not pr:
        html('<div class="ll-small">No needs identified yet.</div>')
        return
    arrival = {p.need.id: i for i, p in enumerate(sorted(pr, key=lambda p: p.need.first_seen_t), 1)}
    zones = zone_names(engine)
    for i, p in enumerate(pr, start=1):
        n = p.need
        is_new = n.first_seen_t == engine.t and engine.cp_index > 0
        status, scol = SERVED_STYLE[p.served]
        jump = arrival[n.id] - i
        jump_txt = (
            f'<span class="pq-jump">{jump} place{"s" if jump > 1 else ""} ahead of arrival order'
            "</span>"
            if jump > 0
            else ""
        )
        flags = "".join(f'<div class="ll-small ll-flag">{escape(f)}</div>' for f in p.flags)
        html(
            f'<div class="pq{" pq-new" if is_new else ""}">'
            f'<div class="pq-rank">{i}</div><div class="pq-body">'
            f"<div>{tier_chip(p.tier)}{'<span class=pq-newtag>new</span>' if is_new else ''}</div>"
            f'<div class="pq-who">{escape(n.who)}</div>'
            f'<div class="ll-small">{escape(n.purpose)} · '
            f"{escape(zones.get(n.zone_id or '', 'location unknown'))}</div>"
            f'<div class="ll-small">Tier rule: {escape(p.tier_rule)}</div>'
            f"{algo_lines(p)}{flags}"
            f'<div class="pq-status" style="color:{scol}">{dot(scol)}{status}</div>{jump_txt}'
            "</div></div>"
        )
    with st.expander("How the ranking is computed"):
        html(
            '<div class="ll-small"><b>AI</b> reads each message: what happened, where, how many '
            "people, whether a life is at risk. It never ranks.</div>"
            '<div class="ll-small"><b>Tier rules</b>: life at risk, then hospitals and rescuers, '
            "then cut-off people, then degraded service, then everything else.</div>"
            '<div class="ll-small"><b>Road routing</b>: a road graph between areas (minimum '
            "spanning tree plus nearest neighbours), with blocked roads removed and damaged roads "
            "slowed. Dijkstra gives travel time from the nearest hospital or rescue base. Farther "
            "from help ranks higher.</div>"
            '<div class="ll-small"><b>Evidence fusion</b>: Bayesian log-odds over independent '
            "sources, weighted by reliability (rescuer &gt; facility &gt; citizen); repeats from "
            "one phone count once. Marzullo's algorithm finds the people count most reports "
            "agree on.</div>"
            '<div class="ll-small"><b>Bandwidth</b>: a min-cost flow over core, backhaul, towers '
            "and areas. Penalties grow with queue position, so where priority access is on, "
            "capacity goes strictly to the top of this list first; elsewhere traffic shares best "
            "effort.</div>"
            '<div class="ll-small"><b>Plan search</b>: every combination of scarce resources is '
            "simulated end to end; the plan serving the most needs, tier by tier, wins.</div>"
        )


# --------------------------------------------------------------------------- plan + decisions


def recommended_plan(engine: Engine) -> None:
    """Operator-approval mode: the optimizer's plan with approve / reject controls."""
    rec = engine.recommendation
    tiers = {p.need.id: p for p in engine.priorities}
    html(
        '<div class="ll-section"><div class="ttl">Recommended plan</div>'
        '<div class="ll-small">Chosen by exhaustive search: most needs served tier by tier, then '
        "fewest scarce resources, then shortest ETA.</div></div>"
    )
    if not rec.plan:
        html('<div class="ll-small">No intervention currently improves any unserved need.</div>')
    for i, item in enumerate(rec.plan, start=1):
        iv = item.intervention
        pcs = "".join(
            f'<div class="ll-small"><span class="{"ll-ok" if pc.ok else "ll-bad"}">'
            f"{'pass' if pc.ok else 'fail'}</span> {escape(pc.name)}: {escape(pc.detail)}</div>"
            for pc in iv.preconditions
        )
        needs = "".join(
            f'<div class="ll-small">{tier_chip(tiers[n].tier)}{escape(tiers[n].need.who)}</div>'
            for n in item.needs_served
            if n in tiers
        )
        scarce = f"uses {', '.join(iv.scarce)}" if iv.scarce else "no scarce resource"
        html(
            f'<div class="ll-card"><div class="ll-title">{i}. {escape(iv.label)}</div>'
            f'<div class="ll-small">ETA {iv.eta_min:.0f} min · {escape(scarce)} · '
            f"{escape(item.rationale)}</div>"
            f'<div class="ll-fact">{escape(iv.effect)}</div>{needs}{pcs}</div>'
        )
        a, b, _ = st.columns([1, 1, 2])
        if a.button("Approve", key=f"ap-{iv.id}", type="primary", use_container_width=True):
            engine.approve(iv.id)
            st.rerun()
        if b.button("Reject", key=f"rj-{iv.id}", use_container_width=True):
            engine.reject(iv.id)
            st.rerun()
    if rec.rejected:
        with st.expander(f"Alternatives considered ({len(rec.rejected)})"):
            order = {"dominated": 0, "infeasible": 1, "no_need": 2, "rejected_by_operator": 3}
            for r in sorted(rec.rejected, key=lambda r: order[r.reason_kind]):
                kind = r.reason_kind.replace("_", " ")
                html(
                    f'<div class="ll-small ll-row">{badge(kind, REJECTION_COLOR[r.reason_kind])}'
                    f"<b>{escape(r.intervention.label)}</b>: {escape(r.detail)}</div>"
                )


def decision_log(engine: Engine) -> None:
    entries = [e for e in engine.timeline if e.kind in ("decision", "outcome")][::-1]
    for p in engine.pending:
        html(
            f'<div class="ll-card"><span class="ll-sev">{dot("#f5a524")}En route</span>'
            f"{escape(p.intervention.label)}, active at T+{p.activate_t:.0f} min</div>"
        )
    if not entries:
        html('<div class="ll-small">No actions yet.</div>')
    for e in entries[:MAX_LOG_ITEMS]:
        if e.kind == "outcome":
            html(
                f'<div class="ll-tl"><span class="ts">T+{e.t:.0f}</span>'
                f'<span class="ll-sev">{dot("#3dd68c")}In effect</span>{escape(e.text)}'
                f'<div class="ll-small">{escape(e.detail)}</div></div>'
            )
            continue
        who = {"lifeline": "approved automatically", "operator": "operator decision"}.get(
            e.actor, ""
        )
        html(
            f'<div class="ll-card"><div class="ll-small">T+{e.t:.0f} min · {who}</div>'
            f'<div class="ll-title">{escape(e.text)}</div>'
            + (f'<div class="ll-small"><b>Why:</b> {escape(e.detail)}</div>' if e.detail else "")
            + "</div>"
        )
