"""Transparent priority policy for intercepted sessions. No weighted scores.

Tiers (the first rule that matches applies):
  T1 Life safety: AI category life_safety, life threat "yes", or a pre-triage life-safety
     flag on the session or any report of the same incident (fail-safe: AI uncertainty cannot
     lower this).
  T2 Critical facility or emergency operation disrupted (or, before analysis completes, a
     responder or facility source, or an elevated pre-triage flag).
  T3 Isolated population, or a session still awaiting analysis: held above routine traffic
     until the AI has read it.
  T4 Routine population traffic.
  T5 Non-urgent (damage reports, information requests, routine business).

Inside a tier, needs are ordered lexicographically by:
  1. Access to help: road travel time from the nearest hospital, clinic or rescue base
     (Dijkstra over the derived road network, lifeline.optimization.routing), in bands. Farther
     first: people close to help can reach it, or be reached, more easily. Unreachable or
     unknown location counts as farthest. Not applied to T2, where the need is the facility.
  2. Probability the life threat is real (Bayesian evidence fusion, lifeline.optimization.fusion).
  3. Connectivity severity of the zone.
  4. Upper bound of people affected over all reports (conservative).
  5. Time waiting, longest first (arrival order).
Bands keep small numeric differences from flipping the order on noise.
"""

from __future__ import annotations

from lifeline.models import (
    CONN_SEVERITY,
    CommNeed,
    Communication,
    ConnState,
    CriterionTrace,
    Incident,
    Interpretation,
    NetworkSnapshot,
    PrioritizedNeed,
    Site,
    SourceRole,
    Tri,
    TriageFloor,
    Zone,
)
from lifeline.optimization import fusion
from lifeline.optimization.network import need_served
from lifeline.optimization.routing import HelpTable, RoadNetwork

TIER_NAMES = {
    1: "Life safety",
    2: "Critical facility / emergency operation",
    3: "Isolated or awaiting analysis",
    4: "Routine traffic",
    5: "Non-urgent",
}
NEAR_MIN, FAR_MIN = 10.0, 30.0
ACCESS_LABELS = ["near help", "moderate distance", "far from help", "no reachable help"]


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
    if need.location_zone_id and need.location_zone_id != need.zone_id:
        lz = zones.get(need.location_zone_id)
        return (lz.lat, lz.lon) if lz else None
    z = zones.get(need.zone_id) if need.zone_id else None
    return (z.lat, z.lon) if z else None


def access_to_help(
    need: CommNeed,
    comms: dict[str, Communication],
    zones: dict[str, Zone],
    sites: list[Site],
    table: HelpTable | None = None,
) -> tuple[int, str]:
    """Band 0 (near help) .. 3 (no reachable help) and a readable explanation."""
    if table is None:
        table = RoadNetwork.build(list(zones.values())).help_routes(sites)
    if not table.any_sites:
        return 3, "no hospital or rescue base on the map"
    loc = need_location(need, comms, zones)
    zone_id = need.location_zone_id or need.zone_id
    if zone_id is None and loc is not None:
        zone_id = table.net.nearest_zone(*loc).id
    if loc is None or zone_id is None:
        return 3, "location unknown, treated as far from help"
    route = table.to(zone_id, *loc)
    if route is None:
        z = zones[zone_id]
        why = (
            f"{z.name}: road {z.road_access.value}"
            if z.road_access.value != "open"
            else ("every route is blocked")
        )
        return 3, f"no open road to any hospital or rescue base ({why})"
    band = 0 if route.minutes <= NEAR_MIN else 1 if route.minutes <= FAR_MIN else 2
    names = {z.id: z.name for z in zones.values()}
    return band, f"{route.describe(names)} ({ACCESS_LABELS[band]})"


def supporting_comms(need: CommNeed, incidents: list[Incident]) -> list[str]:
    """Every message behind a need: its own evidence plus the messages of its incidents."""
    ids = [r.source_id for r in need.evidence]
    for inc in incidents:
        if inc.id in need.incident_ids:
            ids += inc.comm_ids
    return list(dict.fromkeys(ids))


def assign_tier(
    need: CommNeed,
    conn: ConnState | None,
    comms: dict[str, Communication],
    support: list[str] | None = None,
) -> tuple[int, str, list[str]]:
    flags: list[str] = []
    ids = support if support is not None else [r.source_id for r in need.evidence]
    ids = list(dict.fromkeys([need.id, *ids]))
    rule_life = any(comms[i].triage_floor == TriageFloor.LIFE_SAFETY for i in ids if i in comms)
    ai_life = need.analysed and (need.category == "life_safety" or need.life_threat == Tri.YES)
    if ai_life or rule_life:
        if rule_life and not ai_life and need.analysed:
            flags.append("pre-triage flagged life safety; AI did not. Held at T1 for review")
        return 1, "life threat reported or flagged", flags
    if not need.analysed:
        own = comms.get(need.id)
        if own and (
            own.triage_floor == TriageFloor.ELEVATED
            or own.source_role in (SourceRole.RESPONDER, SourceRole.FACILITY)
        ):
            return 2, "awaiting analysis; responder, facility or elevated pre-triage", flags
        return 3, "awaiting analysis; held above routine traffic", flags
    if need.category in ("critical_facility", "emergency_operation"):
        return 2, f"{need.category.replace('_', ' ')} disrupted", flags
    if need.category == "population":
        if conn == ConnState.ISOLATED:
            return 3, "population in isolated zone", flags
        return 4, "routine population traffic", flags
    return 5, "non-urgent", flags


def prioritize(
    needs: list[CommNeed],
    snapshot: NetworkSnapshot,
    comms: dict[str, Communication],
    planned: dict[str, list[str]] | None = None,
    zones: list[Zone] | None = None,
    sites: list[Site] | None = None,
    interpretations: dict[str, Interpretation] | None = None,
    incidents: list[Incident] | None = None,
    connected: set[str] | None = None,
) -> list[PrioritizedNeed]:
    planned = planned or {}
    connected = connected or set()
    interps = interpretations or {}
    zmap = {z.id: z for z in zones or []}
    table = RoadNetwork.build(zones).help_routes(sites or []) if zones else None
    keys: dict[str, tuple[int, int, int, int, int, float]] = {}
    out: list[PrioritizedNeed] = []
    for n in needs:
        zs = snapshot.zones.get(n.zone_id) if n.zone_id else None
        conn = zs.state if zs else None
        support = supporting_comms(n, incidents or [])
        tier, rule, flags = assign_tier(n, conn, comms, support)
        if n.zone_id is None:
            flags.append("network location unknown: cannot be routed until verified")
        if not n.analysed:
            flags.append("awaiting AI analysis")
        if n.verification_needed:
            flags.append("verification needed")
        threat = fusion.life_threat(support, comms, interps)
        ppl = fusion.people(n, support, interps)
        if threat.conflict:
            flags.append("reports disagree on whether a life is at risk: verify")
        if ppl.outliers:
            flags.append(f"people counts disagree ({ppl.describe()}): verify")
        criteria: list[CriterionTrace] = []
        band = 0
        if tier != 2 and table is not None:
            band, access_label = access_to_help(n, comms, zmap, sites or [], table)
            criteria.append(CriterionTrace(criterion="access to help", value=access_label))
        criteria += [
            CriterionTrace(criterion="life threat", value=threat.describe()),
            CriterionTrace(criterion="connectivity", value=conn.value if conn else "unknown"),
            CriterionTrace(criterion="people", value=ppl.describe()),
            CriterionTrace(criterion="arrived", value=f"T+{n.first_seen_t:.0f} min"),
        ]
        alloc = snapshot.allocation.get(n.id)
        if alloc is not None and n.id not in connected:
            criteria.append(
                CriterionTrace(
                    criterion="capacity",
                    value=f"{alloc.allocated:g} of {alloc.demand:g} units routable in queue order",
                )
            )
        status = "connected" if n.id in connected else "held"
        if status == "held" and n.id in planned and not need_served(n, snapshot):
            status = "planned"
        p = PrioritizedNeed(
            need=n,
            tier=tier,
            tier_rule=rule,
            criteria=criteria,
            connectivity=conn or ConnState.DEGRADED,
            status=status,  # type: ignore[arg-type]
            served_by=planned.get(n.id, []),
            flags=flags,
        )
        sev = CONN_SEVERITY[conn] if conn else 2
        upper = ppl.upper_bound or n.people_max or n.people_min or 0
        keys[n.id] = (tier, -band, -threat.band, -sev, -upper, n.first_seen_t)
        out.append(p)
    return sorted(out, key=lambda p: keys[p.need.id])
