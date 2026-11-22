"""Dashboard panels. Each function renders one part of the operator interface."""

from __future__ import annotations

import re
import statistics
from html import escape

import streamlit as st

from lifeline.ai.transcriber import AUDIO_ROOT
from lifeline.dashboard.theme import (
    ASSET_LEGEND,
    CHANNEL_COLOR,
    CONN_COLOR,
    FACT_COLOR,
    REJECTION_COLOR,
    STATUS_STYLE,
    TIER_COLOR,
    TIER_LABEL,
)
from lifeline.evaluation import GROUPS, POLICY_TITLES, controller_waits
from lifeline.models import (
    AssetKind,
    Communication,
    ConnState,
    EvidenceRef,
    Fact,
    PrioritizedNeed,
)
from lifeline.optimization.admission import POLICIES
from lifeline.simulation.engine import Engine

MAX_LOG_ITEMS = 12
MAX_DONE = 12
GATEWAY_KINDS = (AssetKind.CELL_TOWER, AssetKind.MOBILE_BS, AssetKind.SAT_TERMINAL)
LIFE_COLOR = {"yes": "#e5484d", "no": "#3dd68c", "unknown": "#8a94a3"}


def html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def dot(color: str) -> str:
    return f'<i class="ll-dot" style="background:{color}"></i>'


def tier_chip(t: int) -> str:
    return f'<span class="ll-sev">{dot(TIER_COLOR[t])}{TIER_LABEL[t]}</span>'


def badge(text: str, color: str = "#aeb6c2") -> str:
    return f'<span class="ll-badge" style="color:{color}">{escape(text)}</span>'


def channel_badge(c: Communication) -> str:
    label = f"{c.channel.value.replace('_', ' ')} {c.kind.value}"
    return badge(label, CHANNEL_COLOR.get(c.channel.value, "#8a94a3"))


def fmt_range(lo: int | None, hi: int | None) -> str:
    if lo is None and hi is None:
        return "unknown"
    if lo == hi or hi is None:
        return f"{lo}"
    if lo is None:
        return f"up to {hi}"
    return f"{lo}–{hi}"


def fmt_wait(minutes: float) -> str:
    return "under 1 min" if minutes < 1 else f"{minutes:.0f} min"


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
    texts = {c.id: (c.text, c.channel.value) for c in engine.comms.values()}
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


def admission_order(engine: Engine) -> list[str]:
    """Held sessions in the order the gateway will admit them."""
    tiers = {p.need.id: p.tier for p in engine.priorities}
    order = engine.gateway.queue([p.need.id for p in engine.priorities], tiers)
    return [s for s in order if s in tiers]


# --------------------------------------------------------------------------- header


def header(engine: Engine, ai_label: str) -> None:
    m = engine.scenario.meta
    html(
        f'<div class="ll-head"><span class="ll-logo">Lifeline</span>'
        f'<span class="ll-scn">{escape(m.name)}</span>'
        f'<span class="ll-dim">{escape(m.hazard_label)}</span>'
        f'<span class="ll-clock">T+{engine.t:.0f} min</span>'
        f'<span class="ll-right">Live AI: {escape(ai_label)} · network simulated</span></div>'
    )


def incident_timeline(engine: Engine) -> None:
    """Horizontal time axis: onset, every event released so far, and the clock."""
    m = engine.scenario.meta
    end = max(engine.last_event_t, engine.t) + 5

    def x(t: float) -> float:
        return 2 + 96 * t / end

    marks = []
    for ev in engine.scenario.events:
        if not engine.started or ev.t > engine.t:
            continue
        if ev.kind == "telemetry":
            color, label = "#f5a524", str(ev.payload.get("note", ""))
        else:
            sid = str(ev.payload["id"])
            c = engine.comms.get(sid)
            tier = engine.tiers.get(sid, 4)
            color = TIER_COLOR[tier] if tier <= 2 else "#6b8fc7"
            label = c.text if c and c.text else "audio awaiting transcription"
        marks.append(
            f'<div class="tl-dot" title="T+{ev.t:.0f}: {escape(label[:120])}" '
            f'style="left:{x(ev.t):.1f}%;background:{color}"></div>'
        )
    onset = (
        f'<div class="tl-onset" style="left:{x(0):.1f}%"></div>'
        f'<div class="tl-onset-lbl" style="left:{x(0):.1f}%">Onset · T+0</div>'
        if engine.started
        else ""
    )
    now = (
        f'<div class="tl-now" style="left:{x(engine.t):.1f}%">'
        f"<span>T+{engine.t:.0f} min</span></div>"
    )
    status = (
        f'<span class="tl-alert">{escape(m.hazard_label)} · {engine.t:.0f} min since onset</span>'
        if engine.started
        else '<span class="ll-small">No events released yet.</span>'
    )
    legend = (
        f'<span class="ll-small">{dot("#f5a524")}infrastructure alert '
        f"{dot(TIER_COLOR[1])}life safety {dot(TIER_COLOR[2])}facility or operations "
        f"{dot('#6b8fc7')}other session</span>"
    )
    html(
        f'<div class="tl-wrap"><div class="tl-head">{status}{legend}</div>'
        f'<div class="tl-axis">{onset}{"".join(marks)}{now}</div></div>'
    )


# --------------------------------------------------------------------------- status panels


def kpis(engine: Engine) -> None:
    held = engine.gateway.held()
    connected = engine.connected_ids
    backlog = engine.intel.status.backlog
    holding = {engine.specs[s].zone_id for s in held} - {None}
    urgent_held = sum(1 for s in held if engine.tiers.get(s, 9) <= 2)

    def cell(label: str, value: str, color: str = "") -> str:
        d = dot(color) if color else ""
        return (
            f'<div class="ll-m"><div class="lbl">{d}{label}</div>'
            f'<div class="val">{value}</div></div>'
        )

    html(
        '<div class="ll-panel"><h4>Gateway</h4><div class="ll-grid">'
        + cell("Sessions intercepted", str(len(engine.specs)))
        + cell("Held now", str(len(held)))
        + cell("Urgent held", str(urgent_held), TIER_COLOR[1] if urgent_held else "")
        + cell("Connected now", str(len(connected)))
        + cell("Zones holding traffic", str(len(holding)))
        + cell("Awaiting AI analysis", str(backlog), "#f5a524" if backlog else "")
        + "</div></div>"
    )


def comparison(engine: Engine) -> None:
    """Mean wait per tier group under Lifeline and the two baselines, on the same traffic."""
    waits = controller_waits(engine)
    rows = ""
    for label, ts in GROUPS.items():
        ids = [s for s, t in engine.tiers.items() if t in ts]
        if not ids:
            continue
        cells = ""
        for policy in POLICIES:
            vals = [waits[policy][s] for s in ids if s in waits[policy]]
            cells += f"<b>{fmt_wait(statistics.fmean(vals)) if vals else '–'}</b>"
        rows += f"<span>{label} ({len(ids)})</span>{cells}"
    head = "".join(f'<span class="hdr">{POLICY_TITLES[p]}</span>' for p in POLICIES)
    html(
        '<div class="ll-panel"><h4>Mean wait to connect, same traffic and network</h4>'
        f'<div class="cmp"><span class="hdr"></span>{head}'
        f"{rows or '<span class=ll-small>No sessions yet.</span>'}</div></div>"
    )


def gateways(engine: Engine) -> None:
    """Load on every access asset carrying traffic, and how many sessions it is holding."""
    held_by_zone: dict[str, int] = {}
    for s in engine.gateway.held():
        z = engine.specs[s].zone_id
        if z:
            held_by_zone[z] = held_by_zone.get(z, 0) + 1
    rows = ""
    for a in engine.assets:
        if a.kind not in GATEWAY_KINDS or a.id not in engine.gateway.loads:
            continue
        used, cap = engine.gateway.loads[a.id]
        if cap <= 0:
            continue
        share = min(1.0, used / cap)
        color = "#e5484d" if share > 0.97 else "#d6b84a" if share > 0.8 else "#3dd68c"
        held = sum(held_by_zone.get(z, 0) for z in a.coverage_zones + a.extra_zones)
        state = f"holding {held}" if held else "pass-through"
        rows += (
            f"<span>{escape(a.name)}</span><span>{state}</span>"
            f'<span><div class="gw-bar"><i style="width:{share:.0%};background:{color}"></i>'
            f'</div><span class="ll-small">{used:.0f} of {cap:.0f} units</span></span>'
        )
    if rows:
        html(
            '<div class="ll-panel"><h4>Access sites behind the gateway</h4><div class="gw">'
            '<span class="hdr">Site</span><span class="hdr">Gateway</span>'
            f'<span class="hdr">Load</span>{rows}</div></div>'
        )


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
        '<span><i class="ll-dot ll-ring"></i>held session</span></div>'
    )


# --------------------------------------------------------------------------- latest event


def _stage(n: int, title: str, body: str, delay: float) -> str:
    return (
        f'<div class="wf-step" style="animation-delay:{delay:.1f}s">'
        f'<div class="wf-n">{n}</div><div class="wf-t">{title}</div>'
        f'<div class="wf-b">{body}</div></div>'
    )


def latest_interception(engine: Engine) -> None:
    """How the most urgent session of the latest update moved through the gateway."""
    new = [engine.comms[i] for i in engine.last_event_ids if i in engine.comms]
    if not new:
        tel = [e for e in engine.telemetry if e.id in engine.last_event_ids]
        if tel:
            html(
                '<div class="wf-empty">'
                + "<br/>".join(f"Network telemetry: {escape(e.note)}" for e in tel)
                + "</div>"
            )
        elif not engine.started:
            html(
                '<div class="wf-empty">Each intercepted session is traced here: held at the '
                "gateway, transcribed and analysed by the AI, then admitted in priority order."
                "</div>"
            )
        return
    c = min(new, key=lambda c: (engine.tiers.get(c.id, 9), c.t))
    zones = zone_names(engine)
    source = (
        f'<div class="wf-src">{escape(c.adapter)} · {escape(c.source_role.value)} · '
        f"{escape(zones.get(engine.specs[c.id].zone_id or '', 'unknown zone'))}</div>"
    )
    if c.awaiting_transcript:
        heard = '<div class="wf-msg">Audio held; transcription pending.</div>'
    else:
        asr = ""
        if c.transcript is not None:
            conf = "low confidence" if c.transcript.low_confidence else "transcribed"
            asr = f'<div class="wf-src">{escape(c.transcript.model)}: {conf}</div>'
        heard = f'{asr}<div class="wf-msg">“{escape(c.text)}”</div>'
    it = engine.intel.interpretations.get(c.id)
    if it:
        facts = "".join(
            f'<div class="wf-fact"><span class="fs">{f.status.value}</span>{escape(f.claim)}</div>'
            for f in it.facts[:3]
        )
        lt = it.life_threat.value
        ai = (
            f'<div class="wf-kv"><span>About</span><b>{escape(it.category.replace("_", " "))}</b>'
            f'<span>Life threat</span><b style="color:{LIFE_COLOR[lt]}">{lt}</b>'
            f"<span>Where</span><b>{escape(zones.get(it.zone_id or '', 'not stated'))}</b>"
            f"<span>People</span><b>{escape(fmt_range(it.people_min, it.people_max))}</b>"
            f"<span>Confidence</span><b>{it.confidence:.0%}</b></div>{facts}"
        )
    else:
        ai = (
            '<div class="wf-msg">Analysis pending (rate limit or retry); held with its '
            f"pre-triage floor: {escape(c.triage_floor.value.replace('_', ' '))}.</div>"
        )
    p = next((p for p in engine.priorities if p.need.id == c.id), None)
    state = engine.gateway.states.get(c.id)
    decision = '<div class="ll-small">Queued.</div>'
    if p and state:
        if state.state == "held":
            order = admission_order(engine)
            pos = order.index(c.id) + 1 if c.id in order else len(order)
            outcome = f"Held at the gateway, position {pos} of {len(order)}"
        elif state.state == "connected":
            outcome = f"Connected via {escape(state.via or 'satellite messaging')}"
        else:
            outcome = "Completed"
        decision = (
            f"{tier_chip(p.tier)}<div class='wf-need'>{escape(p.need.who)}</div>"
            f'<div class="ll-small">Rule: {escape(p.tier_rule)}</div>'
            f'<div class="wf-res">{outcome}</div>'
        )
    stages = [("Intercepted", source + heard), ("AI analysis", ai), ("Gateway", decision)]
    html(
        f'<div class="wf"><div class="wf-title">Latest interception at T+{engine.t:.0f} min</div>'
        '<div class="wf-row">'
        + '<div class="wf-arrow">›</div>'.join(
            _stage(i + 1, title, body, i * 0.5) for i, (title, body) in enumerate(stages)
        )
        + "</div></div>"
    )


# --------------------------------------------------------------------------- queue


def _details(engine: Engine, p: PrioritizedNeed) -> None:
    c = engine.comms[p.need.id]
    if c.audio:
        st.audio(str(AUDIO_ROOT / c.audio))
        if c.transcript is not None:
            conf = "low confidence" if c.transcript.low_confidence else "ok"
            html(
                f'<div class="ll-small">Transcribed by {escape(c.transcript.model)} (mean '
                f"log-probability {c.transcript.avg_logprob}, {conf})</div>"
            )
    it = engine.intel.interpretations.get(c.id)
    quotes = [r.quote for f in it.facts for r in f.evidence] if it else []
    html(f'<div class="ll-quote">{highlight(c.text, quotes) or "No text yet."}</div>')
    if it:
        html(
            f'<div class="ll-small">AI ({escape(it.interpreter)}): {escape(it.summary)}</div>'
            + "".join(fact_line(f) for f in it.facts)
        )
        if it.missing_info:
            html(f'<div class="ll-small">Unknown: {escape("; ".join(it.missing_info))}</div>')
    html(
        "".join(
            f'<div class="ll-small"><b>{escape(cr.criterion)}</b>: {escape(cr.value)}</div>'
            for cr in p.criteria
        )
    )
    if c.triage_terms:
        html(f'<div class="ll-small">Pre-triage: {escape(", ".join(c.triage_terms))}</div>')


def queue(engine: Engine) -> None:
    """Held sessions in the order the gateway will admit them, then recent connections."""
    by_id = {p.need.id: p for p in engine.priorities}
    order = admission_order(engine)
    html(
        '<div class="ll-section"><div class="ttl">Interception queue</div>'
        '<div class="ll-small">Sessions held at the gateway, in the order the network will carry '
        "them. Urgent sessions go first; routine traffic keeps its arrival order.</div></div>"
    )
    if not order:
        html(
            '<div class="ll-small">Nothing is waiting: the network is carrying every session.</div>'
        )
    zones = zone_names(engine)
    for i, sid in enumerate(order, start=1):
        p, c = by_id[sid], engine.comms[sid]
        label, color = STATUS_STYLE[p.status]
        wait = engine.gateway.wait(sid, engine.t)
        flags = "".join(f'<div class="ll-small ll-flag">{escape(f)}</div>' for f in p.flags)
        text = c.text or "Audio held; transcription pending."
        more = "…" if len(text) > 180 else ""
        html(
            f'<div class="pq{" pq-new" if c.t == engine.t else ""}">'
            f'<div class="pq-rank">{i}</div><div class="pq-body">'
            f"<div>{tier_chip(p.tier)}{channel_badge(c)}</div>"
            f'<div class="pq-who">{escape(p.need.who)}</div>'
            f'<div class="ll-small">{escape(p.need.purpose)} · '
            f"{escape(zones.get(p.need.zone_id or '', 'unknown zone'))}</div>"
            f'<div class="pq-text">{escape(text[:180])}{more}</div>'
            f'<div class="ll-small">Rule: {escape(p.tier_rule)}</div>{flags}'
            f'<div class="pq-status" style="color:{color}">{dot(color)}{label} · '
            f'<span class="pq-wait">waiting {fmt_wait(wait)}</span></div></div></div>'
        )
        with st.expander(f"{sid}: transcript, analysis and ranking"):
            _details(engine, p)
    admitted(engine)
    with st.expander("How the queue is ordered"):
        html(
            '<div class="ll-small"><b>Interception</b>: every new call, message, data session '
            "and radio transmission is held at the gateway in front of the base station or "
            "radio network.</div>"
            '<div class="ll-small"><b>AI</b>: Whisper transcribes voice; a language model reads '
            "each session (what it is about, where, how many people, whether a life is at "
            "risk) and merges reports into incidents. It never ranks.</div>"
            '<div class="ll-small"><b>Tier rules</b>: life safety, then facilities and rescue '
            "operations, then isolated or not-yet-analysed sessions, then routine and "
            "non-urgent traffic. A rule-based pre-triage floor holds possible life-safety "
            "sessions at the top until the AI has read them.</div>"
            '<div class="ll-small"><b>Within a tier</b>: road travel time to help (Dijkstra), '
            "Bayesian evidence fusion across reports, connectivity, people affected, waiting "
            "time.</div>"
            '<div class="ll-small"><b>Admission</b>: once a minute, a lexicographic maximum '
            "flow over core, backhaul and radio capacity admits held sessions in this order "
            "without displacing anything already connected.</div>"
        )


def admitted(engine: Engine) -> None:
    states = engine.gateway.states
    done = [s for s, x in states.items() if x.first_connected_t is not None]
    done.sort(key=lambda s: -(states[s].first_connected_t or 0.0))
    if not done:
        return
    waits = controller_waits(engine)
    rows = ""
    for sid in done[:MAX_DONE]:
        x = states[sid]
        tier = engine.tiers.get(sid, 4)
        base = waits["arrival"].get(sid)
        vs = (
            f"; first come, first served: {fmt_wait(base)}"
            if base is not None and tier <= 2
            else ""
        )
        rows += (
            f'<div class="pq-done"><span class="id">{sid}</span>{tier_chip(tier)}'
            f"<span>{x.state} via {escape(x.via or 'satellite messaging')}, waited "
            f"{fmt_wait(engine.gateway.wait(sid, engine.t))}{vs}</span></div>"
        )
    html(f'<h4 class="ll-sub">Admitted ({len(done)})</h4>{rows}')


# --------------------------------------------------------------------------- plan + decisions


def recommended_plan(engine: Engine) -> None:
    """Operator-approval mode: capacity interventions with approve / reject controls."""
    rec = engine.recommendation
    tiers = {p.need.id: p for p in engine.priorities}
    html(
        '<div class="ll-section"><div class="ttl">Capacity plan</div>'
        '<div class="ll-small">For held sessions no network can carry yet. Chosen by exhaustive '
        "search: most sessions carried tier by tier, then fewest scarce resources, then "
        "shortest ETA.</div></div>"
    )
    if not rec.plan:
        html('<div class="ll-small">No intervention would carry any held session.</div>')
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
        html('<div class="ll-small">No capacity interventions yet.</div>')
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


# --------------------------------------------------------------------------- picture + AI


def operational_picture(engine: Engine) -> None:
    pic = engine.intel.picture
    zones = zone_names(engine)
    changes = [e for e in engine.timeline if e.kind == "intelligence"][-3:][::-1]
    html(
        f'<div class="ll-panel"><h4>Operational picture · v{pic.version}</h4>'
        f'<div class="ll-summary">{escape(pic.summary)}</div>'
        + "".join(
            f'<div class="ll-change"><span class="ll-small">T+{e.t:.0f}</span> {escape(e.text)}'
            "</div>"
            for e in changes
        )
        + "</div>"
    )
    incidents = [i for i in pic.incidents if i.comm_ids]
    with st.expander(f"Incidents ({len(incidents)}), dependencies, contradictions and gaps"):
        for inc in incidents:
            lt = inc.life_threat.value
            rel = ", ".join(f"{r.relation.replace('_', ' ')} {r.incident_id}" for r in inc.related)
            html(
                f'<div class="ll-card"><div class="ll-title">{escape(inc.id)} · '
                f"{escape(inc.title)}</div>"
                f'<div class="ll-small">{badge("life threat: " + lt, LIFE_COLOR[lt])}'
                f"{escape(zones.get(inc.zone_id or '', 'location unknown'))} · people "
                f"{fmt_range(inc.people_min, inc.people_max)} · sessions "
                f"{escape(', '.join(inc.comm_ids))}{(' · ' + escape(rel)) if rel else ''}</div>"
                f"{''.join(fact_line(f) for f in inc.facts)}"
                f"{evidence_block(engine, [r for f in inc.facts for r in f.evidence])}</div>"
            )
        for d in pic.dependencies:
            steps = '<span class="ll-small">→</span>'.join(
                f'<span class="step">{escape(s.description)}</span>' for s in d.steps
            )
            html(
                f'<div class="ll-card"><div class="ll-title">{escape(d.title)} '
                f"{badge(d.status.value, FACT_COLOR[d.status])}</div>"
                f'<div class="ll-chain">{steps}</div></div>'
            )
        for c in pic.contradictions:
            html(
                f'<div class="ll-card"><div class="ll-fact">Contradiction: '
                f"{escape(c.description)}</div>"
                f'<div class="ll-small">Verify: {escape(c.suggested_verification)}</div>'
                f"{evidence_block(engine, c.evidence)}</div>"
            )
        for g in pic.gaps:
            why = f" · {escape(g.why_it_matters)}" if g.why_it_matters else ""
            html(f'<div class="ll-small"><b>{escape(g.question)}</b>{why}</div>')
        if pic.guard_notes:
            html('<h4 class="ll-sub">Evidence guard actions</h4>')
            html("".join(f'<div class="ll-small">{escape(n)}</div>' for n in pic.guard_notes))


def ai_status(engine: Engine) -> None:
    s = engine.intel.status
    lines = [
        f"Awaiting transcription: {', '.join(s.pending_transcripts) or 'none'}",
        f"Awaiting interpretation: {', '.join(s.pending_interpretation) or 'none'}",
        f"Awaiting synthesis: {', '.join(s.pending_synthesis) or 'none'}",
    ]
    used = getattr(engine.interpreter, "tokens_used", None)
    if used is not None:
        waited = getattr(engine.interpreter, "waited_s", 0.0)
        lines.append(f"Tokens used this run: {used:,}; waited for rate limits: {waited:.0f} s")
    html("".join(f'<div class="ll-small">{escape(x)}</div>' for x in lines))
    for e in s.errors[:5]:
        html(f'<div class="ll-small ll-flag">{escape(e[:240])}</div>')
