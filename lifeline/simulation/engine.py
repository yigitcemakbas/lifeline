"""Simulation engine: clock, scenario events, dynamics, operator decisions, re-evaluation.

No Streamlit imports: the whole pipeline is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lifeline.ai.intelligence import IntelligenceEngine
from lifeline.ai.interpreter import Interpreter
from lifeline.ingestion.pretriage import pretriage
from lifeline.ingestion.registry import ingest
from lifeline.models import (
    Asset,
    AssetStatus,
    Communication,
    Intervention,
    NetworkSnapshot,
    PrioritizedNeed,
    Recommendation,
    Scenario,
    TelemetryEvent,
    TimelineEntry,
    Zone,
)
from lifeline.optimization.allocator import recommend
from lifeline.optimization.interventions import apply, generate
from lifeline.optimization.network import analyse, need_served
from lifeline.optimization.prioritizer import prioritize, telemetry_only_ranking

IDLE_STEP_MIN = 10.0


@dataclass
class Pending:
    intervention: Intervention
    approved_t: float
    activate_t: float


@dataclass
class Engine:
    scenario: Scenario
    interpreter: Interpreter
    t: float = 0.0
    assets: list[Asset] = field(default_factory=list)
    priority_zones: set[str] = field(default_factory=set)
    comms: list[Communication] = field(default_factory=list)
    telemetry: list[TelemetryEvent] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    pending: list[Pending] = field(default_factory=list)
    applied: list[Intervention] = field(default_factory=list)
    rejected_ids: set[str] = field(default_factory=set)
    checkpoint_times: list[float] = field(default_factory=list)
    cp_index: int = 0

    def __post_init__(self) -> None:
        self.assets = [a.model_copy(deep=True) for a in self.scenario.assets]
        self.checkpoint_times = sorted({e.t for e in self.scenario.events})
        self.intel = IntelligenceEngine(self.scenario, self.interpreter)
        self._tier_view: dict[str, int] = {}
        self.log(
            0, "system", f"Scenario loaded: {self.scenario.meta.name}. All integrations simulated."
        )
        self.recompute()

    # ------------------------------------------------------------------ state

    @property
    def events_done(self) -> bool:
        return self.cp_index >= len(self.checkpoint_times)

    @property
    def zones(self) -> list[Zone]:
        return self.scenario.zones

    @property
    def dpp(self) -> float:
        return self.scenario.dynamics.demand_per_person

    def log(self, t: float, kind: str, text: str, refs: list[str] | None = None) -> None:
        self.timeline.append(TimelineEntry(t=t, kind=kind, text=text, refs=refs or []))  # type: ignore[arg-type]

    # ------------------------------------------------------------------ stepping

    def step(self) -> None:
        """Advance to the next checkpoint (or by an idle interval once events are exhausted)."""
        target = (
            self.checkpoint_times[self.cp_index] if not self.events_done else self.t + IDLE_STEP_MIN
        )
        self._advance_clock(target)
        if self.events_done:
            self.recompute()
            return
        new_comms: list[Communication] = []
        for ev in [e for e in self.scenario.events if e.t == target]:
            item = ingest(ev)
            if isinstance(item, Communication):
                c = pretriage(item)
                self.comms.append(c)
                new_comms.append(c)
                flag = f" [pre-triage: {c.triage_floor.value}]" if c.triage_terms else ""
                self.log(
                    target,
                    "comm",
                    f'{c.channel.value.upper()} {c.source_role.value}: "{c.text[:90]}"{flag}',
                    [c.id],
                )
            else:
                self.telemetry.append(item)
                self._apply_telemetry(item)
                self.log(
                    target, "telemetry", item.note or f"{item.asset_id}: {item.changes}", [item.id]
                )
        self.cp_index += 1
        changes = self.intel.process(target, new_comms, self.comms, self.telemetry)
        for ch in changes:
            self.log(target, "intelligence", ch.summary, ch.caused_by + ch.needs_affected)
        self.recompute()

    def _advance_clock(self, target: float) -> None:
        dt = max(0.0, target - self.t)
        rate = self.scenario.dynamics.battery_drain_pct_per_min
        if rate and dt:
            for a in self.assets:
                if a.power == "battery" and a.battery_pct is not None and a.battery_pct > 0:
                    a.battery_pct = max(0.0, a.battery_pct - rate * dt)
                    if a.battery_pct == 0:
                        self.log(
                            target, "telemetry", f"{a.name}: battery depleted, site off air", [a.id]
                        )
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

    # ------------------------------------------------------------------ decisions

    def approve(self, iv_id: str) -> None:
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
            self.t, "decision", f"Operator APPROVED: {iv.label} (ETA {iv.eta_min:.0f} min)", [iv.id]
        )
        p = Pending(iv, self.t, self.t + iv.eta_min)
        if iv.eta_min <= 5:
            self._activate(p)
        else:
            self.pending.append(p)
        self.recompute()

    def reject(self, iv_id: str) -> None:
        self.rejected_ids.add(iv_id)
        self.log(self.t, "decision", f"Operator rejected: {iv_id}", [iv_id])
        self.recompute()

    def _activate(self, p: Pending) -> None:
        before = analyse(self.assets, self.zones, self.dpp, self.priority_zones)
        needs = [n for n in self.intel.picture.needs if n.status == "open"]
        served_before = {n.id for n in needs if need_served(n, before)}
        self.assets, self.priority_zones = apply(
            p.intervention, self.assets, self.zones, self.priority_zones
        )
        after = analyse(self.assets, self.zones, self.dpp, self.priority_zones)
        served_after = {n.id for n in needs if need_served(n, after)}
        if p in self.pending:
            self.pending.remove(p)
        self.applied.append(p.intervention)
        z = p.intervention.target_zone
        zname = next((x.name for x in self.zones if x.id == z), z)
        delta = ""
        if z in before.zones:
            delta = f"{zname}: {before.zones[z].state.value} → {after.zones[z].state.value}"
        newly = sorted(served_after - served_before)
        self.log(
            self.t,
            "outcome",
            f"Simulated effect applied: {p.intervention.label}. {delta}"
            + (f"; needs now served: {', '.join(newly)}" if newly else ""),
            [p.intervention.id, *newly],
        )

    # ------------------------------------------------------------------ evaluation

    def projected(self) -> tuple[list[Asset], set[str]]:
        a, pz = self.assets, self.priority_zones
        for p in self.pending:
            a, pz = apply(p.intervention, a, self.zones, pz)
        return a, pz

    def recompute(self) -> None:
        self.snapshot: NetworkSnapshot = analyse(
            self.assets, self.zones, self.dpp, self.priority_zones
        )
        needs = self.intel.picture.needs
        planned: dict[str, list[str]] = {}
        pa, ppz = self.projected()
        proj_snap = analyse(pa, self.zones, self.dpp, ppz)
        for n in needs:
            if not need_served(n, self.snapshot) and need_served(n, proj_snap):
                planned[n.id] = [p.intervention.label for p in self.pending]
        comms_by_id = {c.id: c for c in self.comms}
        self.priorities: list[PrioritizedNeed] = prioritize(
            needs, self.snapshot, comms_by_id, planned
        )
        proj_priorities = prioritize(needs, proj_snap, comms_by_id)
        candidates = generate(self.zones, pa, proj_snap, needs, ppz)
        committed = {r for p in self.pending for r in p.intervention.scarce}
        candidates = [c for c in candidates if not set(c.scarce) & committed]
        self.recommendation: Recommendation = recommend(
            candidates, pa, self.zones, ppz, proj_priorities, self.dpp, self.rejected_ids
        )
        self.telemetry_rank: list[str] = telemetry_only_ranking(self.snapshot, self.zones)
        for p in self.priorities:
            old = self._tier_view.get(p.need.id)
            if old != p.tier:
                verb = "identified" if old is None else f"re-tiered T{old} →"
                self.log(
                    self.t,
                    "priority",
                    f"{p.need.id} {verb} T{p.tier}: {p.need.who}: {p.tier_rule}",
                    [p.need.id],
                )
                self._tier_view[p.need.id] = p.tier

    def run_all(self) -> None:
        while not self.events_done:
            self.step()
