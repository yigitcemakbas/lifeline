"""Capacity network and lexicographic admission.

The network is a flow graph per network family, sharing one source:

  cellular   source -> core -> backhaul -> access assets (towers, mobile base stations,
             satellite access terminals) -> zone
  tetra      source -> TETRA site -> zone
  mesh       source -> bridged mesh node -> zone
  satellite  source -> zone (handheld satellite messaging uses an external constellation)

Every asset is split into in/out nodes so its capacity limits everything passing through it (a
backhaul link caps all towers behind it). An access asset's radio capacity is shared evenly
across the zones it covers, as in the network analysis. Each admitted session (or fluid
traffic class) is a terminal node fed from its zone.

Admission is a lexicographic maximum flow: classes are routed one at a time in queue order,
each by augmenting paths (Edmonds-Karp) in the residual network. An augmenting path may reroute
traffic that is already admitted (for example from one covering tower to another) but can never
reduce it, because admitted classes are terminal nodes. So the top of the queue gets capacity
first and nothing admitted earlier is displaced. A session is all-or-nothing (a call needs its
whole channel); fluid classes (background traffic) take whatever fits. Integer units (x100) keep
results exact and deterministic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from lifeline.models import (
    Asset,
    AssetKind,
    CommNeed,
    NeedAllocation,
    NetworkFamily,
    NetworkSnapshot,
    Service,
    Zone,
)
from lifeline.optimization.network import ACCESS_KINDS, covers

SCALE = 100
INF = 10**12
SESSION_UNITS = {
    Service.VOICE: 1.0,
    Service.RESPONDER_RADIO: 1.0,
    Service.DATA: 3.0,
    Service.TEXT: 0.05,
}
FACILITY_SESSIONS = 4  # a facility's data link carries several sessions at once
SOURCE = "SRC"


def session_demand(service: Service, facility: bool = False) -> float:
    units = SESSION_UNITS[service]
    return units * (FACILITY_SESSIONS if facility and service == Service.DATA else 1)


def need_demand(need: CommNeed) -> float:
    if need.demand > 0:
        return need.demand
    return session_demand(need.service, facility=need.category == "critical_facility")


def _units(x: float) -> int:
    return max(0, round(x * SCALE))


def zone_node(zone_id: str, family: NetworkFamily) -> str:
    return f"Z:{zone_id}:{family.value}"


@dataclass(frozen=True)
class Demand:
    key: str
    zone_id: str
    family: NetworkFamily
    units: float
    whole: bool = True  # all-or-nothing (a session) vs fluid (background traffic)


class CapacityNetwork:
    """Residual graph of the current network; route classes in priority order."""

    def __init__(self, assets: list[Asset], zones: list[Zone], snap: NetworkSnapshot) -> None:
        self.cap: dict[str, dict[str, int]] = {}
        self.zone_ids = {z.id for z in zones}
        self.carried: dict[str, int] = {}  # class key -> units routed
        self.via: dict[str, str] = {}  # class key -> access asset that carried most of it
        self.capacity: dict[str, int] = {}  # asset id -> capacity of its in->out edge
        usable = set(snap.usable_assets)
        by_id = {a.id: a for a in assets}
        sat_backhaul = {
            lk
            for a in assets
            if a.kind in ACCESS_KINDS
            for lk in a.links
            if lk in by_id and by_id[lk].kind == AssetKind.SAT_TERMINAL
        }
        # cellular transport
        carriers = [
            a
            for a in assets
            if a.kind not in (AssetKind.MESH_NODE, AssetKind.TETRA_SITE)
            and a.id not in snap.unusable
            and (a.id in usable or a.kind in (AssetKind.CORE, AssetKind.BACKHAUL_LINK))
        ]
        carrier_ids = {a.id for a in carriers}
        for a in carriers:
            cap = a.bandwidth if a.kind == AssetKind.SAT_TERMINAL else a.capacity
            uncapped = a.kind in (AssetKind.CORE, AssetKind.BACKHAUL_LINK) and cap <= 0
            self._edge(f"{a.id}:in", f"{a.id}:out", INF if uncapped else _units(cap))
            if not uncapped:
                self.capacity[a.id] = _units(cap)
            for lk in a.links:
                if lk in carrier_ids:
                    self._edge(f"{lk}:out", f"{a.id}:in", INF)
                    self._edge(f"{a.id}:out", f"{lk}:in", INF)
            if a.kind == AssetKind.CORE:
                self._edge(SOURCE, f"{a.id}:in", INF)
        for a in carriers:
            access = a.kind in ACCESS_KINDS or (
                a.kind == AssetKind.SAT_TERMINAL and a.id not in sat_backhaul
            )
            if not access:
                continue
            cz = [z for z in zones if covers(a, z)]
            cap = a.bandwidth if a.kind == AssetKind.SAT_TERMINAL else a.capacity
            for z in cz:
                self._edge(
                    f"{a.id}:out", zone_node(z.id, NetworkFamily.CELLULAR), _units(cap / len(cz))
                )
        # standalone radio networks
        for a in assets:
            if a.id not in usable:
                continue
            fam = {
                AssetKind.TETRA_SITE: NetworkFamily.TETRA,
                AssetKind.MESH_NODE: NetworkFamily.MESH,
            }
            if a.kind not in fam:
                continue
            self._edge(SOURCE, f"{a.id}:in", INF)
            self._edge(f"{a.id}:in", f"{a.id}:out", _units(a.capacity))
            self.capacity[a.id] = _units(a.capacity)
            for z in zones:
                if covers(a, z):
                    self._edge(f"{a.id}:out", zone_node(z.id, fam[a.kind]), INF)
        for z in zones:
            self._edge(SOURCE, zone_node(z.id, NetworkFamily.SATELLITE), INF)

    def _edge(self, u: str, v: str, c: int) -> None:
        self.cap.setdefault(u, {})
        self.cap.setdefault(v, {})
        self.cap[u][v] = self.cap[u].get(v, 0) + c
        self.cap[v].setdefault(u, 0)

    def _augment(self, target: str, limit: int) -> tuple[int, list[tuple[str, str, int]]]:
        """Push up to `limit` units from the source to `target`; returns (pushed, log)."""
        pushed, log = 0, []
        while pushed < limit:
            prev: dict[str, str] = {SOURCE: SOURCE}
            q = deque([SOURCE])
            while q and target not in prev:
                u = q.popleft()
                for v, c in self.cap[u].items():
                    if c > 0 and v not in prev:
                        prev[v] = u
                        q.append(v)
            if target not in prev:
                break
            path, v = [], target
            while v != SOURCE:
                path.append((prev[v], v))
                v = prev[v]
            amount = min(limit - pushed, min(self.cap[u][v] for u, v in path))
            for u, v in path:
                self.cap[u][v] -= amount
                self.cap[v][u] += amount
                log.append((u, v, amount))
            pushed += amount
        return pushed, log

    def _rollback(self, log: list[tuple[str, str, int]]) -> None:
        for u, v, amount in reversed(log):
            self.cap[u][v] += amount
            self.cap[v][u] -= amount

    def route(self, d: Demand) -> float:
        """Admit one class: all of a whole demand or nothing; as much fluid as fits."""
        need = _units(d.units)
        if d.zone_id not in self.zone_ids:
            return 0.0
        if need == 0:
            return d.units
        node = f"K:{d.key}"
        self._edge(zone_node(d.zone_id, d.family), node, need)
        pushed, log = self._augment(node, need)
        if d.whole and pushed < need:
            self._rollback(log)
            self.cap[zone_node(d.zone_id, d.family)][node] = 0
            return 0.0
        self.carried[d.key] = self.carried.get(d.key, 0) + pushed
        feeders = {
            u.removesuffix(":out"): self.cap[zone_node(d.zone_id, d.family)].get(u, 0)
            for u in self.cap[zone_node(d.zone_id, d.family)]
            if u.endswith(":out")
        }
        if feeders and any(feeders.values()):
            self.via[d.key] = max(sorted(feeders), key=lambda k: feeders[k])
        return pushed / SCALE

    def loads(self) -> dict[str, tuple[float, float]]:
        """Asset id -> (units carried, capacity)."""
        return {
            a: ((c - self.cap[f"{a}:in"][f"{a}:out"]) / SCALE, c / SCALE)
            for a, c in self.capacity.items()
        }

    def available(self, zone_id: str, family: NetworkFamily) -> float:
        """Capacity still free for new traffic in a zone (probe, nothing committed)."""
        if zone_id not in self.zone_ids:
            return 0.0
        node = "K:__probe__"
        self._edge(zone_node(zone_id, family), node, INF)
        pushed, log = self._augment(node, INF)
        self._rollback(log)
        self.cap[zone_node(zone_id, family)][node] = 0
        return pushed / SCALE


def with_allocation(
    snap: NetworkSnapshot,
    assets: list[Asset],
    zones: list[Zone],
    ranked: list[CommNeed],
) -> NetworkSnapshot:
    """Capacity view for planning: route every open session in queue order and record what the
    network could carry. Background traffic ranks below every intercepted session, so it does
    not appear here."""
    net = CapacityNetwork(assets, zones, snap)
    alloc: dict[str, NeedAllocation] = {}
    for n in ranked:
        if n.zone_id is None:
            continue
        dem = need_demand(n)
        got = net.route(Demand(n.id, n.zone_id, n.family, dem))
        alloc[n.id] = NeedAllocation(demand=dem, allocated=round(got, 2))
    return snap.model_copy(update={"allocation": alloc})
