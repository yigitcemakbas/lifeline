"""Transparent priority policy. No weighted scores.

Tiers (the first rule that matches applies):
  T1 Life safety: AI category life_safety, life threat "yes", or a pre-triage life-safety
     flag on any supporting message (fail-safe: AI uncertainty cannot lower this).
  T2 Critical facility or emergency operation disrupted.
  T3 Isolated population (zone has no usable voice/data or bridged mesh).
  T4 Degraded population service.
  T5 Non-urgent (damage reports, information requests).
Inside a tier, needs are ordered lexicographically by: evidence strength, connectivity
severity, upper bound of people affected, then time waiting (longest first).
"""

from __future__ import annotations

from lifeline.models import (
    CONN_SEVERITY,
    CommNeed,
    Communication,
    ConnState,
    CriterionTrace,
    NetworkSnapshot,
    PrioritizedNeed,
    SourceRole,
    Tri,
    TriageFloor,
    Zone,
)
from lifeline.optimization.network import need_served

TIER_NAMES = {
    1: "Life safety",
    2: "Critical facility / emergency operation",
    3: "Isolated population",
    4: "Degraded population service",
    5: "Non-urgent",
}
EVIDENCE_LABELS = ["inferred only", "single report", "corroborated", "authoritative source"]


def evidence_strength(need: CommNeed, comms: dict[str, Communication]) -> int:
    ids = {r.source_id for r in need.evidence}
    if not ids:
        return 0
    roles = {comms[i].source_role for i in ids if i in comms}
    if roles & {SourceRole.RESPONDER, SourceRole.FACILITY} or any(i not in comms for i in ids):
        return 3  # responders/facilities on scene, or telemetry ids
    return 2 if len(ids) >= 2 else 1


def assign_tier(
    need: CommNeed, conn: ConnState | None, comms: dict[str, Communication]
) -> tuple[int, str, list[str]]:
    flags: list[str] = []
    rule_life = any(
        comms[r.source_id].triage_floor == TriageFloor.LIFE_SAFETY
        for r in need.evidence
        if r.source_id in comms
    )
    ai_life = need.category == "life_safety" or need.life_threat == Tri.YES
    if ai_life or rule_life:
        if rule_life and not ai_life:
            flags.append("pre-triage flagged life safety; AI did not. Held at T1 for review")
        return 1, "life threat reported or flagged", flags
    if need.category in ("critical_facility", "emergency_operation"):
        return 2, f"{need.category.replace('_', ' ')} disrupted", flags
    if need.category == "population":
        if conn == ConnState.ISOLATED:
            return 3, "population in isolated zone", flags
        return 4, "population service degraded", flags
    return 5, "non-urgent", flags


def prioritize(
    needs: list[CommNeed],
    snapshot: NetworkSnapshot,
    comms: dict[str, Communication],
    planned: dict[str, list[str]] | None = None,
) -> list[PrioritizedNeed]:
    planned = planned or {}
    out: list[PrioritizedNeed] = []
    for n in needs:
        if n.status != "open":
            continue
        zs = snapshot.zones.get(n.zone_id) if n.zone_id else None
        conn = zs.state if zs else None
        tier, rule, flags = assign_tier(n, conn, comms)
        if n.zone_id is None:
            flags.append("location unknown: cannot target interventions until verified")
        if n.verification_needed:
            flags.append("verification needed")
        ev = evidence_strength(n, comms)
        people = n.people_max if n.people_max is not None else n.people_min
        criteria = [
            CriterionTrace(criterion="evidence", value=EVIDENCE_LABELS[ev]),
            CriterionTrace(criterion="connectivity", value=conn.value if conn else "unknown"),
            CriterionTrace(
                criterion="people (upper bound)", value=str(people) if people else "unknown"
            ),
            CriterionTrace(criterion="first reported", value=f"T+{n.first_seen_t:.0f} min"),
        ]
        served = "served" if need_served(n, snapshot) else "unserved"
        if served == "unserved" and n.id in planned:
            served = "planned"
        out.append(
            PrioritizedNeed(
                need=n,
                tier=tier,
                tier_rule=rule,
                criteria=criteria,
                connectivity=conn or ConnState.DEGRADED,
                served=served,  # type: ignore[arg-type]
                served_by=planned.get(n.id, []),
                flags=flags,
            )
        )

    def key(p: PrioritizedNeed) -> tuple[int, int, int, int, float]:
        n = p.need
        zs = snapshot.zones.get(n.zone_id) if n.zone_id else None
        sev = CONN_SEVERITY[zs.state] if zs else 2
        people = n.people_max if n.people_max is not None else (n.people_min or 0)
        return (p.tier, -evidence_strength(n, comms), -sev, -people, n.first_seen_t)

    return sorted(out, key=key)


def telemetry_only_ranking(snapshot: NetworkSnapshot, zones: list[Zone]) -> list[str]:
    """What a network-only view would prioritise: worst connectivity, then most residents."""
    pop = {z.id: z.population for z in zones}
    return sorted(
        snapshot.zones,
        key=lambda zid: (-CONN_SEVERITY[snapshot.zones[zid].state], -pop.get(zid, 0)),
    )
