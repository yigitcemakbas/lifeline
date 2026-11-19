"""Transparent priority policy. No weighted scores.

Tiers (the first rule that matches applies):
  T1 Life safety: AI category life_safety, life threat "yes", or a pre-triage life-safety
     flag on any supporting message (fail-safe: AI uncertainty cannot lower this).
  T2 Critical facility or emergency operation disrupted.
  T3 Isolated population (zone has no usable voice/data or bridged mesh).
  T4 Degraded population service.
  T5 Non-urgent (damage reports, information requests).
Inside a tier, needs are ordered lexicographically by: access to help (farther from the
nearest reachable hospital, clinic or rescue base first, because people close to help can
reach it or be reached by it more easily; blocked roads count as unreachable; unknown location
counts as farthest), evidence strength, connectivity severity, upper bound of people affected,
then time waiting (longest first). Access to help does not apply to T2, where
the need is usually the facility itself.
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
    RoadAccess,
    Site,
    SourceRole,
    Tri,
    TriageFloor,
    Zone,
)
from lifeline.optimization.network import distance_m, need_served

TIER_NAMES = {
    1: "Life safety",
    2: "Critical facility / emergency operation",
    3: "Isolated population",
    4: "Degraded population service",
    5: "Non-urgent",
}
# Site kinds that provide help: people can reach them, or they can reach people.
HELP_SITE_KINDS = frozenset(
    {"hospital", "clinic", "health_centre", "emergency_base", "ambulance_station", "fire_station"}
)
NEAR_M, FAR_M = 2_000.0, 8_000.0
ACCESS_LABELS = ["near help", "moderate distance", "far from help", "no reachable help"]
EVIDENCE_LABELS = ["inferred only", "single report", "corroborated", "authoritative source"]


def evidence_strength(need: CommNeed, comms: dict[str, Communication]) -> int:
    ids = {r.source_id for r in need.evidence}
    if not ids:
        return 0
    roles = {comms[i].source_role for i in ids if i in comms}
    if roles & {SourceRole.RESPONDER, SourceRole.FACILITY} or any(i not in comms for i in ids):
        return 3  # responders/facilities on scene, or telemetry ids
    return 2 if len(ids) >= 2 else 1


def need_location(
    need: CommNeed, comms: dict[str, Communication], zones: dict[str, Zone]
) -> tuple[float, float] | None:
    """Mean position of the supporting messages that carry coordinates, else the zone centre."""
    pts = [
        (c.lat, c.lon)
        for r in need.evidence
        if (c := comms.get(r.source_id)) and c.lat is not None and c.lon is not None
    ]
    if pts:
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    z = zones.get(need.zone_id) if need.zone_id else None
    return (z.lat, z.lon) if z else None


def access_to_help(
    need: CommNeed,
    comms: dict[str, Communication],
    zones: dict[str, Zone],
    sites: list[Site],
) -> tuple[int, str]:
    """Band 0 (near help) .. 3 (no reachable help) and a readable explanation.

    Deterministic: great-circle distance to the nearest help site whose road connection is
    not blocked, raised one band when the road is limited. Unknown location is treated as
    farthest, so uncertainty never lowers priority.
    """
    loc = need_location(need, comms, zones)
    if loc is None:
        return 3, "location unknown, treated as far from help"
    help_sites = [s for s in sites if s.kind in HELP_SITE_KINDS and s.zone_id in zones]
    if not help_sites:
        return 3, "no hospital or rescue base on the map"
    nz = zones.get(need.zone_id) if need.zone_id else None
    best: tuple[int, float, str] | None = None
    for s in help_sites:
        sz = zones[s.zone_id]
        same = nz is not None and s.zone_id == nz.id
        roads = {sz.road_access, nz.road_access if nz else RoadAccess.OPEN}
        if not same and RoadAccess.BLOCKED in roads:
            continue
        slat, slon = (s.lat, s.lon) if s.lat is not None and s.lon is not None else (sz.lat, sz.lon)
        d = distance_m(loc[0], loc[1], slat, slon)
        band = 0 if d <= NEAR_M else 1 if d <= FAR_M else 2
        limited = not same and RoadAccess.LIMITED in roads
        if limited:
            band = min(band + 1, 2)
        label = f"{s.name} {d / 1000:.1f} km away" + (", road limited" if limited else "")
        if best is None or (band, d) < best[:2]:
            best = (band, d, label)
    if best is None:
        return 3, "no reachable hospital or rescue base: roads blocked"
    return best[0], f"{best[2]} ({ACCESS_LABELS[best[0]]})"


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
    zones: list[Zone] | None = None,
    sites: list[Site] | None = None,
) -> list[PrioritizedNeed]:
    planned = planned or {}
    zmap = {z.id: z for z in zones or []}
    access: dict[str, int] = {}
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
        criteria: list[CriterionTrace] = []
        if tier != 2:
            band, access_label = access_to_help(n, comms, zmap, sites or [])
            access[n.id] = band
            criteria.append(CriterionTrace(criterion="access to help", value=access_label))
        criteria += [
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

    def key(p: PrioritizedNeed) -> tuple[int, int, int, int, int, float]:
        n = p.need
        zs = snapshot.zones.get(n.zone_id) if n.zone_id else None
        sev = CONN_SEVERITY[zs.state] if zs else 2
        people = n.people_max if n.people_max is not None else (n.people_min or 0)
        far = access.get(n.id, 0)
        return (p.tier, -far, -evidence_strength(n, comms), -sev, -people, n.first_seen_t)

    return sorted(out, key=key)


def telemetry_only_ranking(snapshot: NetworkSnapshot, zones: list[Zone]) -> list[str]:
    """What a network-only view would prioritise: worst connectivity, then most residents."""
    pop = {z.id: z.population for z in zones}
    return sorted(
        snapshot.zones,
        key=lambda zid: (-CONN_SEVERITY[snapshot.zones[zid].state], -pop.get(zid, 0)),
    )
