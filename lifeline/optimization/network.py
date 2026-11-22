"""Deterministic network analysis.

Builds a NetworkX graph from the current assets and derives, per zone, which channels are
usable, how much capacity is available versus demand, and a categorical connectivity state with
the causes traced through the graph. No AI involved.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import networkx as nx

from lifeline.models import (
    Asset,
    AssetKind,
    AssetStatus,
    CommNeed,
    ConnState,
    NetworkSnapshot,
    Zone,
    ZoneState,
)

ACCESS_KINDS = {AssetKind.CELL_TOWER, AssetKind.MOBILE_BS}
URGENT_CATEGORIES = frozenset({"life_safety", "critical_facility", "emergency_operation"})


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def covers(asset: Asset, zone: Zone) -> bool:
    if zone.id in asset.extra_zones or zone.id in asset.coverage_zones:
        return True
    if asset.coverage_zones:
        return False
    if asset.coverage_radius_m <= 0:
        return False
    d = distance_m(asset.lat, asset.lon, zone.lat, zone.lon)
    return d <= asset.coverage_radius_m + zone.radius_m * 0.6


def power_problem(a: Asset) -> str | None:
    if a.power == "none":
        return "no power"
    if a.power == "battery" and (a.battery_pct or 0) <= 0:
        return "battery depleted"
    return None


def _alive(a: Asset) -> bool:
    return a.status in (AssetStatus.UP, AssetStatus.DEGRADED) and power_problem(a) is None


def build_graph(assets: Iterable[Asset]) -> nx.Graph:
    g: nx.Graph = nx.Graph()
    by_id = {a.id: a for a in assets}
    for a in by_id.values():
        g.add_node(a.id, alive=_alive(a), kind=a.kind)
    for a in by_id.values():
        for other in a.links:
            if other in by_id:
                g.add_edge(a.id, other)
    return g


def analyse(
    assets: list[Asset],
    zones: list[Zone],
    demand_per_person: float,
) -> NetworkSnapshot:
    by_id = {a.id: a for a in assets}
    g = build_graph(assets)
    alive_nodes = [n for n, d in g.nodes(data=True) if d["alive"]]
    live = g.subgraph(alive_nodes)
    # mesh nodes carry text only: they never provide backhaul for towers or terminals
    live_backhaul = g.subgraph(
        [n for n in alive_nodes if g.nodes[n]["kind"] != AssetKind.MESH_NODE]
    )
    core_ids = [a.id for a in assets if a.kind == AssetKind.CORE and _alive(a)]

    def reaches_core(aid: str) -> bool:
        return aid in live_backhaul and any(
            c in live_backhaul and nx.has_path(live_backhaul, aid, c) for c in core_ids
        )

    usable: set[str] = set()
    unusable: dict[str, str] = {}
    for a in assets:
        if a.kind in (AssetKind.CORE, AssetKind.BACKHAUL_LINK):
            if not _alive(a):
                unusable[a.id] = a.status.value if a.status == AssetStatus.DOWN else "no power"
            continue
        if a.status == AssetStatus.STANDBY:
            continue
        if a.status == AssetStatus.DOWN:
            unusable[a.id] = "down"
            continue
        pp = power_problem(a)
        if pp:
            unusable[a.id] = pp
            continue
        if a.kind == AssetKind.TETRA_SITE:
            usable.add(a.id)  # TETRA works in direct/standalone mode
        elif a.kind in ACCESS_KINDS or a.kind == AssetKind.SAT_TERMINAL:
            if reaches_core(a.id):
                usable.add(a.id)
            else:
                dead = [lk for lk in a.links if lk in by_id and not _alive(by_id[lk])]
                why = (
                    f"no backhaul path to core ({', '.join(dead)} down)"
                    if dead
                    else ("no backhaul path to core")
                )
                unusable[a.id] = why
    # mesh: usable for text only when its cluster is bridged to a usable gateway
    mesh_ids = [a.id for a in assets if a.kind == AssetKind.MESH_NODE and _alive(a)]
    for mid in mesh_ids:
        comp = nx.node_connected_component(live, mid) if mid in live else {mid}
        if any(
            c in usable or c in core_ids
            for c in comp
            if c != mid and by_id[c].kind not in (AssetKind.MESH_NODE,)
        ):
            usable.add(mid)
        else:
            unusable[mid] = "mesh island: local text only, no gateway"

    # sat terminals used as backhaul for a tower don't provide local access capacity
    backhaul_terminals = {
        lk
        for a in assets
        if a.kind in ACCESS_KINDS
        for lk in a.links
        if lk in by_id and by_id[lk].kind == AssetKind.SAT_TERMINAL
    }

    # share each access asset's capacity across the zones it covers
    zone_cover: dict[str, list[Zone]] = {a.id: [z for z in zones if covers(a, z)] for a in assets}
    states: dict[str, ZoneState] = {}
    for z in zones:
        ch = {"cellular": 0.0, "satellite": 0.0, "mesh": 0.0, "tetra": 0.0}
        causes: list[str] = []
        for a in assets:
            if z not in zone_cover[a.id]:
                continue
            share = (a.capacity if a.kind != AssetKind.SAT_TERMINAL else a.bandwidth) / max(
                1, len(zone_cover[a.id])
            )
            if a.id not in usable:
                if a.id in unusable and a.kind != AssetKind.MESH_NODE:
                    causes.append(f"{a.name} unusable: {unusable[a.id]}")
                continue
            if a.kind in ACCESS_KINDS:
                ch["cellular"] += share
            elif a.kind == AssetKind.SAT_TERMINAL and a.id not in backhaul_terminals:
                ch["satellite"] += share
            elif a.kind == AssetKind.MESH_NODE:
                ch["mesh"] += share
            elif a.kind == AssetKind.TETRA_SITE:
                ch["tetra"] += share
        capacity = ch["cellular"] + ch["satellite"]
        demand = z.population * demand_per_person
        ratio = capacity / demand if demand > 0 else float("inf")
        if capacity <= 0 and ch["mesh"] <= 0:
            state = ConnState.ISOLATED
            causes.append("no usable voice/data or bridged mesh coverage")
        elif ratio < 0.5:
            state = ConnState.DEGRADED
            causes.append(
                "text only via mesh" if capacity <= 0 else f"capacity {ratio:.0%} of demand"
            )
        elif ratio < 1.0:
            state = ConnState.CONGESTED
            causes.append(f"capacity {ratio:.0%} of demand")
        else:
            state = ConnState.CONNECTED
        states[z.id] = ZoneState(
            zone_id=z.id,
            state=state,
            capacity=round(capacity, 2),
            demand=round(demand, 2),
            channels={k: round(v, 2) for k, v in ch.items()},
            causes=causes,
        )
    return NetworkSnapshot(zones=states, usable_assets=sorted(usable), unusable=unusable)


def need_served(need: CommNeed, snap: NetworkSnapshot) -> bool:
    """Whether the admission flow (lifeline.optimization.flow) can carry this session."""
    alloc = snap.allocation.get(need.id)
    return alloc is not None and alloc.fraction >= 0.999
