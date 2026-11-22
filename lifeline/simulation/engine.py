"""Simulation engine: clock, scenario events, the admission gateway, decisions, re-evaluation.

The clock advances in one-minute ticks. Each tick:

1. releases the scenario events due (sessions are intercepted and held at the gateway;
   telemetry changes the network);
2. runs the AI stages on whatever is pending: transcription, interpretation, synthesis;
3. ranks every open session with the priority policy;
4. lets the gateway admit held sessions in that order as capacity allows;
5. re-plans capacity interventions for sessions the network cannot carry at all.

Interventions are approved by an operator (`approve` / `reject`) or, with `auto_approve`, by
the engine in plan order. With `compare`, baseline gateways (first come first served, and no
gateway at all) see exactly the same traffic and network, so every session's wait can be
compared with what it would have been without Lifeline.

No Streamlit imports: the whole pipeline is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lifeline.ai.intelligence import IntelligenceEngine
from lifeline.ai.interpreter import Interpreter
from lifeline.ai.transcriber import Transcriber
from lifeline.ingestion.registry import ingest
from lifeline.models import (
    Actor,
    Asset,
    AssetStatus,
    CommNeed,
    Communication,
    EvidenceRef,
    Intervention,
    NetworkSnapshot,
    PrioritizedNeed,
    Recommendation,
    Scenario,
    SessionSpec,
    SourceRole,
    TelemetryEvent,
    TimelineEntry,
    TimelineKind,
    TriageFloor,
    Zone,
)
from lifeline.optimization.admission import AdmissionController, AdmissionEvent
from lifeline.optimization.allocator import recommend
from lifeline.optimization.flow import session_demand, with_allocation
from lifeline.optimization.interventions import apply, generate
from lifeline.optimization.network import analyse, need_served
from lifeline.optimization.prioritizer import prioritize

TICK_MIN = 1.0
TAIL_MIN = 30.0  # keep the clock running this long after the last scenario event
BASELINE_SEEDS = 8


@dataclass
class Pending:
    intervention: Intervention
    approved_t: float
    activate_t: float


@dataclass
class Engine:
    scenario: Scenario
    interpreter: Interpreter
    transcriber: Transcriber
    auto_approve: bool = False  # approve the recommended plan automatically, in plan order
    live: bool = False  # live AI: defer AI requests while their model has no rate-limit budget
    compare: bool = False  # run baseline gateways alongside on the same traffic
    baseline_seeds: int = BASELINE_SEEDS
    t: float = 0.0
    started: bool = False
    assets: list[Asset] = field(default_factory=list)
    comms: dict[str, Communication] = field(default_factory=dict)
    specs: dict[str, SessionSpec] = field(default_factory=dict)
    telemetry: list[TelemetryEvent] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    pending: list[Pending] = field(default_factory=list)
    applied: list[Intervention] = field(default_factory=list)
    rejected_ids: set[str] = field(default_factory=set)
    last_event_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.assets = [a.model_copy(deep=True) for a in self.scenario.assets]
        self.intel = IntelligenceEngine(
            self.scenario, self.interpreter, self.transcriber, defer=self.live
        )
        self.gateway = AdmissionController("lifeline")
        self.baselines: list[AdmissionController] = []
        if self.compare:
            self.baselines = [AdmissionController("arrival")] + [
                AdmissionController("uncontrolled", seed=i) for i in range(self.baseline_seeds)
            ]
        self.last_admissions: list[AdmissionEvent] = []
        self._tier_view: dict[str, int] = {}
        self._ranked: list[PrioritizedNeed] = []
        self.log(0, "system", f"Scenario loaded: {self.scenario.meta.name}.")
        self.recompute()

    # ------------------------------------------------------------------ state

    @property
    def last_event_t(self) -> float:
        return max((e.t for e in self.scenario.events), default=0.0)

    @property
    def end_t(self) -> float:
        """The run lasts until `TAIL_MIN` after the last event, and at least until every
        approved intervention has taken effect."""
        arrivals = [p.activate_t + TICK_MIN for p in self.pending]
        return max([self.last_event_t + TAIL_MIN, *arrivals])

    @property
    def events_done(self) -> bool:
        return self.started and self.t >= self.last_event_t

    @property
    def finished(self) -> bool:
        if not self.events_done:
            return False
        idle = not self.gateway.held() and not self.pending and not self.intel.status.backlog
        return idle or self.t >= self.end_t

    @property
    def zones(self) -> list[Zone]:
        return self.scenario.zones

    @property
    def dpp(self) -> float:
        return self.scenario.dynamics.demand_per_person

    @property
    def tiers(self) -> dict[str, int]:
        """Latest tier of every session ranked so far (completed sessions keep their last)."""
        return dict(self._tier_view)

    @property
    def connected_ids(self) -> set[str]:
        return {s for s, st in self.gateway.states.items() if st.state == "connected"}

    @property
    def open_ids(self) -> list[str]:
        return [s for s, st in self.gateway.states.items() if st.state != "completed"] + [
            s for s in self.specs if s not in self.gateway.states
        ]

    def log(
        self,
        t: float,
        kind: TimelineKind,
        text: str,
        refs: list[str] | None = None,
        detail: str = "",
        actor: Actor = "",
    ) -> None:
        self.timeline.append(
            TimelineEntry(t=t, kind=kind, text=text, refs=refs or [], detail=detail, actor=actor)
        )

    # ------------------------------------------------------------------ stepping

    def step(self) -> None:
        """Advance one tick."""
        prev = self.t if self.started else float("-inf")
        target = self.t + TICK_MIN if self.started else 0.0
        self.started = True
        self._advance_clock(target)
        due = [e for e in self.scenario.events if prev < e.t <= target]
        self.last_event_ids = [str(e.payload["id"]) for e in due]
        new_telemetry: list[str] = []
        for ev in due:
            item = ingest(ev)
            if isinstance(item, Communication):
                self._intercept(item)
            else:
                self.telemetry.append(item)
                self._apply_telemetry(item)
                new_telemetry.append(item.id)
                self.log(target, "telemetry", item.note or f"{item.asset_id}: {item.changes}")
        self.intel.transcribe(target, self.comms)
        self.intel.interpret(target, self.comms)
        for ch in self.intel.synthesize(target, self.comms, self.telemetry, new_telemetry):
            self.log(target, "intelligence", ch.summary, ch.caused_by)
        self._rank()
        self._admit()
        self._plan()
        self.run_auto_approval()

    def run_all(self) -> None:
        while not self.finished:
            self.step()

    def _intercept(self, comm: Communication) -> None:
        c = self.intel.intercept(comm)
        self.comms[c.id] = c
        self.specs[c.id] = SessionSpec(
            id=c.id,
            t_arrival=c.t,
            family=c.family,
            service=c.service,
            zone_id=self.intel.comm_zone.get(c.id),
            demand=session_demand(c.service, facility=c.source_role == SourceRole.FACILITY),
            duration_min=c.duration_min,
        )
        self.log(
            c.t,
            "comm",
            f"Intercepted {c.channel.value.replace('_', ' ')} {c.kind.value} from "
            f"{c.source_role.value}",
            [c.id],
        )

    def _advance_clock(self, target: float) -> None:
        dt = max(0.0, target - self.t)
        rate = self.scenario.dynamics.battery_drain_pct_per_min
        if rate and dt:
            for a in self.assets:
                if a.power == "battery" and a.battery_pct is not None and a.battery_pct > 0:
                    a.battery_pct = max(0.0, a.battery_pct - rate * dt)
                    if a.battery_pct == 0:
                        self.log(target, "telemetry", f"{a.name}: battery depleted, site off air")
        self.t = target
        for p in sorted(self.pending, key=lambda p: p.activate_t):
            if p.activate_t <= self.t:
                self._activate(p)

    def _apply_telemetry(self, ev: TelemetryEvent) -> None:
        a = next(x for x in self.assets if x.id == ev.asset_id)
        for k, v in ev.changes.items():
            if k == "status":
                a.status = AssetStatus(str(v))
            elif k in ("battery_pct", "capacity", "load", "bandwidth") and isinstance(
                v, int | float
            ):
                setattr(a, k, float(v))
            elif k == "power" and v in ("grid", "battery", "none"):
                a.power = v

    # ------------------------------------------------------------------ sessions -> needs

    def need_for(self, sid: str) -> CommNeed:
        """What one session needs, from its interpretation (or its pre-triage until then)."""
        c, sp = self.comms[sid], self.specs[sid]
        it = self.intel.interpretations.get(sid)
        linked = [i for i in self.intel.picture.incidents if sid in i.comm_ids]
        incidents = [i.id for i in linked]
        incident_zone = next((i.zone_id for i in linked if i.zone_id), None)
        low_asr = c.transcript is not None and c.transcript.low_confidence
        if it is None:
            category = (
                "life_safety"
                if c.triage_floor == TriageFloor.LIFE_SAFETY
                else "critical_facility"
                if c.source_role == SourceRole.FACILITY
                else "population"
            )
            evidence = [EvidenceRef(source_id=sid, quote=c.text[:160])] if c.text else []
            return CommNeed(
                id=sid,
                who=f"{c.source_role.value} via {c.channel.value.replace('_', ' ')}",
                zone_id=sp.zone_id,
                purpose="awaiting transcription" if c.awaiting_transcript else "awaiting analysis",
                service=c.service,
                family=c.family,
                category=category,  # type: ignore[arg-type]
                analysed=False,
                incident_ids=incidents,
                evidence=evidence,
                verification_needed=low_asr,
                first_seen_t=c.t,
                demand=sp.demand,
            )
        return CommNeed(
            id=sid,
            who=it.who or f"{c.source_role.value} via {c.channel.value.replace('_', ' ')}",
            zone_id=sp.zone_id,
            location_zone_id=incident_zone or it.zone_id,
            purpose=it.purpose or it.summary,
            service=c.service,
            family=c.family,
            category=it.category,
            incident_ids=incidents,
            people_min=it.people_min,
            people_max=it.people_max,
            life_threat=it.life_threat,
            evidence=[r for f in it.facts for r in f.evidence],
            confidence=it.confidence,
            verification_needed=low_asr or it.confidence < 0.4,
            first_seen_t=c.t,
            demand=sp.demand,
        )

    def needs(self) -> list[CommNeed]:
        return [self.need_for(s) for s in self.open_ids]

    def _prioritize(
        self,
        needs: list[CommNeed],
        snap: NetworkSnapshot,
        planned: dict[str, list[str]] | None = None,
    ) -> list[PrioritizedNeed]:
        return prioritize(
            needs,
            snap,
            self.comms,
            planned,
            self.zones,
            self.scenario.sites,
            self.intel.interpretations,
            self.intel.picture.incidents,
            self.connected_ids,
        )

    # ------------------------------------------------------------------ rank, admit, plan

    def _rank(self) -> None:
        self.snapshot: NetworkSnapshot = analyse(self.assets, self.zones, self.dpp)
        self._ranked = self._prioritize(self.needs(), self.snapshot)

    def _admit(self) -> None:
        ranked = [p.need.id for p in self._ranked]
        tiers = {p.need.id: p.tier for p in self._ranked}
        specs = list(self.specs.values())
        args = (ranked, tiers, self.assets, self.zones, self.snapshot, self.dpp)
        self.last_admissions = self.gateway.tick(self.t, specs, *args)
        for b in self.baselines:
            b.tick(self.t, specs, *args)
        for ev in self.last_admissions:
            if ev.kind == "connected":
                self.log(
                    self.t,
                    "admission",
                    f"{ev.session_id} connected via {ev.via or 'satellite messaging'}",
                    [ev.session_id],
                    detail=f"waited {ev.wait:.0f} min",
                )
            elif ev.kind == "dropped":
                self.log(
                    self.t,
                    "admission",
                    f"{ev.session_id} dropped: {ev.via or 'network'} can no longer carry it; "
                    "held again",
                    [ev.session_id],
                )

    def _queue_order(self, needs: list[CommNeed]) -> list[CommNeed]:
        """Connected sessions first (they keep their capacity), then held ones in rank order."""
        rank = {p.need.id: i for i, p in enumerate(self._ranked)}
        st = self.gateway.states
        return sorted(
            needs,
            key=lambda n: (
                0 if n.id in st and st[n.id].state == "connected" else 1,
                st[n.id].connected_t or 0.0 if n.id in st else 0.0,
                rank.get(n.id, len(rank)),
            ),
        )

    def projected(self) -> list[Asset]:
        a = self.assets
        for p in self.pending:
            a = apply(p.intervention, a, self.zones)
        return a

    def _plan(self) -> None:
        needs = self._queue_order(self.needs())
        pa = self.projected()
        snap = analyse(self.assets, self.zones, self.dpp)
        self.snapshot = with_allocation(snap, self.assets, self.zones, needs)
        proj_snap = with_allocation(analyse(pa, self.zones, self.dpp), pa, self.zones, needs)
        planned: dict[str, list[str]] = {}
        for n in needs:
            if not need_served(n, self.snapshot) and need_served(n, proj_snap):
                planned[n.id] = [p.intervention.label for p in self.pending]
        self.priorities: list[PrioritizedNeed] = self._prioritize(needs, self.snapshot, planned)
        proj_priorities = self._prioritize(needs, proj_snap)
        candidates = generate(self.zones, pa, proj_snap, needs)
        committed = {r for p in self.pending for r in p.intervention.scarce}
        candidates = [c for c in candidates if not set(c.scarce) & committed]
        self.recommendation: Recommendation = recommend(
            candidates, pa, self.zones, proj_priorities, self.dpp, self.rejected_ids
        )
        for p in self.priorities:
            old = self._tier_view.get(p.need.id)
            if old != p.tier:
                verb = "ranked" if old is None else f"re-tiered T{old} →"
                self.log(
                    self.t,
                    "priority",
                    f"{p.need.id} {verb} T{p.tier}: {p.need.who}: {p.tier_rule}",
                    [p.need.id],
                )
                self._tier_view[p.need.id] = p.tier

    def recompute(self) -> None:
        """Re-rank and re-plan without advancing the clock (after a decision)."""
        self._rank()
        self._plan()

    # ------------------------------------------------------------------ decisions

    def run_auto_approval(self) -> None:
        """Approve the recommended plan in order, logging the sessions each item carries."""
        if not self.auto_approve:
            return
        tiers = {p.need.id: p for p in self.priorities}
        for _ in range(10):
            plan = [
                i for i in self.recommendation.plan if i.intervention.id not in self.rejected_ids
            ]
            if not plan:
                return
            item = plan[0]
            served = [tiers[n] for n in item.needs_served if n in tiers]
            why = "; ".join(f"{p.need.who} ({p.tier_rule})" for p in served[:3]) or item.rationale
            before = len(self.applied) + len(self.pending)
            self.approve(item.intervention.id, reason=why, actor="lifeline")
            if len(self.applied) + len(self.pending) == before:
                return

    def approve(self, iv_id: str, reason: str = "", actor: Actor = "operator") -> None:
        iv = next(
            (i.intervention for i in self.recommendation.plan if i.intervention.id == iv_id), None
        )
        if iv is None:
            iv = next(
                (
                    r.intervention
                    for r in self.recommendation.rejected
                    if r.intervention.id == iv_id and r.intervention.feasible
                ),
                None,
            )
        if iv is None:
            return
        self.log(
            self.t,
            "decision",
            iv.label,
            [iv.id],
            detail=reason or f"approved by operator, ETA {iv.eta_min:.0f} min",
            actor=actor,
        )
        p = Pending(iv, self.t, self.t + iv.eta_min)
        if iv.eta_min <= 5:
            self._activate(p)
        else:
            self.pending.append(p)
        self.recompute()

    def reject(self, iv_id: str) -> None:
        self.rejected_ids.add(iv_id)
        label = next(
            (i.intervention.label for i in self.recommendation.plan if i.intervention.id == iv_id),
            iv_id,
        )
        self.log(self.t, "decision", f"Rejected: {label}", [iv_id], actor="operator")
        self.recompute()

    def _activate(self, p: Pending) -> None:
        needs = self._queue_order(self.needs())

        def routable(assets: list[Asset]) -> tuple[NetworkSnapshot, set[str]]:
            snap = analyse(assets, self.zones, self.dpp)
            snap = with_allocation(snap, assets, self.zones, needs)
            return snap, {n.id for n in needs if need_served(n, snap)}

        before, ok_before = routable(self.assets)
        self.assets = apply(p.intervention, self.assets, self.zones)
        after, ok_after = routable(self.assets)
        if p in self.pending:
            self.pending.remove(p)
        self.applied.append(p.intervention)
        z = p.intervention.target_zone
        zname = next((x.name for x in self.zones if x.id == z), z)
        delta = ""
        if z in before.zones:
            delta = f"{zname}: {before.zones[z].state.value} → {after.zones[z].state.value}"
        newly = sorted(ok_after - ok_before)
        self.log(
            self.t,
            "outcome",
            p.intervention.label,
            [p.intervention.id, *newly],
            detail=delta + (f"; sessions now routable: {', '.join(newly)}" if newly else ""),
        )
