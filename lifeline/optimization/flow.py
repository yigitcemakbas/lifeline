"""Urgency-ordered bandwidth allocation as a min-cost flow.

The network is a flow graph: core -> backhaul -> access assets (towers, mobile base stations,
satellite access) -> zones -> traffic classes -> sink. Every asset is split into in/out nodes so
its capacity limits everything passing through it (a backhaul link with a capacity caps all
towers behind it). An access asset's radio capacity is shared across the zones it covers, as in
the network analysis.

Traffic classes:
  * In a zone with priority access (and enough residual capacity for it to work), each urgent
    voice/data need is its own class.
  * Everything else in the zone shares one best-effort pool: the population's background demand
    plus every need without priority. A pool is shared proportionally, which is what a network
    without urgency awareness does.

Every class also has a bypass edge straight from the source with a penalty cost: flow on it is
demand the network could not carry. Penalties grow geometrically with queue position
(P_r = (D+1)^(N-r), pools cost 1), so minimising total cost is exactly lexicographic: the top of
the priority queue is served as fully as physically possible before the next item gets anything.
Solved with network simplex on integer units (x100) so the result is exact and deterministic.
"""

from __future__ import annotations

import networkx as nx

from lifeline.models import (
    Asset,
    AssetKind,
    CommNeed,
    NeedAllocation,
    NetworkSnapshot,
    Service,
    Zone,
)
from lifeline.optimization.network import ACCESS_KINDS, PRIORITY_MIN_RATIO, covers

SCALE = 100
SESSION_UNITS = {Service.VOICE: 1.0, Service.DATA: 3.0}  # one active emergency session
FACILITY_SESSIONS = 4  # a facility's data link carries several sessions at once
URGENT = frozenset({"life_safety", "critical_facility", "emergency_operation"})
ALL_CATEGORIES = URGENT | {"population", "non_urgent"}


def need_demand(need: CommNeed) -> float:
    units = SESSION_UNITS.get(need.service, 0.0)
    return units * (FACILITY_SESSIONS if need.category == "critical_facility" else 1)


def _units(x: float) -> int:
    return max(0, round(x * SCALE))


def allocate(
    assets: list[Asset],
    zones: list[Zone],
    snap: NetworkSnapshot,
    ranked: list[CommNeed],
    demand_per_person: float,
    priority_zones: set[str],
    admit: frozenset[str] = URGENT,
) -> dict[str, NeedAllocation]:
    """Allocate capacity to needs. `admit`: categories that get their own priority class
    (urgent ones by default; every category for a first-come-first-served baseline)."""
    by_id = {a.id: a for a in assets}
    usable = set(snap.usable_assets)
    backhaul_terminals = {
        lk
        for a in assets
        if a.kind in ACCESS_KINDS
        for lk in a.links
        if lk in by_id and by_id[lk].kind == AssetKind.SAT_TERMINAL
    }
    g: nx.DiGraph = nx.DiGraph()
    src, sink = "SRC", "SINK"

    # transport: node-split assets that carry traffic toward the core
    carriers = [
        a
        for a in assets
        if a.kind != AssetKind.MESH_NODE
        and a.kind != AssetKind.TETRA_SITE
        and (a.id in usable or a.kind in (AssetKind.CORE, AssetKind.BACKHAUL_LINK))
        and a.id not in snap.unusable
    ]
    carrier_ids = {a.id for a in carriers}
    for a in carriers:
        cap = a.bandwidth if a.kind == AssetKind.SAT_TERMINAL else a.capacity
        if a.kind in (AssetKind.CORE, AssetKind.BACKHAUL_LINK) and cap <= 0:
            g.add_edge(f"{a.id}:in", f"{a.id}:out")  # uncapacitated
        else:
            g.add_edge(f"{a.id}:in", f"{a.id}:out", capacity=_units(cap))
        for lk in a.links:
            if lk in carrier_ids:
                g.add_edge(f"{lk}:out", f"{a.id}:in")  # toward the edge of the network
                g.add_edge(f"{a.id}:out", f"{lk}:in")
        if a.kind == AssetKind.CORE:
            g.add_edge(src, f"{a.id}:in")

    # radio access: asset -> covered zones
    zmap = {z.id: z for z in zones}
    for a in carriers:
        is_access = a.kind in ACCESS_KINDS or (
            a.kind == AssetKind.SAT_TERMINAL and a.id not in backhaul_terminals
        )
        if not is_access:
            continue
        cz = [z for z in zones if covers(a, z)]
        cap = a.bandwidth if a.kind == AssetKind.SAT_TERMINAL else a.capacity
        for z in cz:
            g.add_edge(f"{a.id}:out", f"Z:{z.id}", capacity=_units(cap / len(cz)))

    # traffic classes
    in_flow = [n for n in ranked if n.service in SESSION_UNITS and n.zone_id in zmap]

    def prioritised(n: CommNeed) -> bool:
        zs = snap.zones.get(n.zone_id or "")
        return (
            n.category in admit
            and n.zone_id in priority_zones
            and zs is not None
            and zs.ratio >= PRIORITY_MIN_RATIO
        )

    prio = [n for n in in_flow if prioritised(n)]
    pool_members: dict[str, list[CommNeed]] = {}
    for n in in_flow:
        if not prioritised(n):
            pool_members.setdefault(n.zone_id or "", []).append(n)
    pool_demand = {
        z.id: _units(z.population * demand_per_person)
        + sum(_units(need_demand(n)) for n in pool_members.get(z.id, []))
        for z in zones
    }
    classes: list[tuple[str, str, int]] = []  # (node, zone, demand)
    classes += [(f"N:{n.id}", n.zone_id or "", _units(need_demand(n))) for n in prio]
    classes += [(f"P:{zid}", zid, d) for zid, d in pool_demand.items() if d > 0]
    total = sum(d for _, _, d in classes)
    n_prio = len(prio)
    for i, (node, zid, d) in enumerate(classes):
        penalty = (total + 1) ** (n_prio - i) if i < n_prio else 1
        g.add_edge(f"Z:{zid}", node, capacity=d, weight=0)
        g.add_edge(node, sink, capacity=d, weight=0)
        g.add_edge(src, node, capacity=d, weight=penalty)  # unmet demand
    g.nodes[src]["demand"] = -total
    g.nodes[sink]["demand"] = total

    flow = nx.min_cost_flow(g) if total > 0 else {}

    def carried(node: str, zid: str) -> int:
        return int(flow.get(f"Z:{zid}", {}).get(node, 0))

    out: dict[str, NeedAllocation] = {}
    for n in prio:
        dem = need_demand(n)
        got = carried(f"N:{n.id}", n.zone_id or "") / SCALE
        out[n.id] = NeedAllocation(
            demand=dem, allocated=round(got, 2), mode="priority", pool_fraction=None
        )
    for zid, members in pool_members.items():
        frac = carried(f"P:{zid}", zid) / pool_demand[zid] if pool_demand[zid] else 0.0
        for n in members:
            dem = need_demand(n)
            out[n.id] = NeedAllocation(
                demand=dem,
                allocated=round(dem * frac, 2),
                mode="best_effort",
                pool_fraction=round(frac, 3),
            )
    return out


def with_allocation(
    snap: NetworkSnapshot,
    assets: list[Asset],
    zones: list[Zone],
    ranked: list[CommNeed],
    demand_per_person: float,
    priority_zones: set[str],
    admit: frozenset[str] = URGENT,
) -> NetworkSnapshot:
    """Copy of the snapshot with the flow allocation for the given queue order attached."""
    alloc = allocate(assets, zones, snap, ranked, demand_per_person, priority_zones, admit)
    return snap.model_copy(update={"allocation": alloc})
