"""Deterministic road routing between zones.

Scenarios describe zones, not streets, so the road network is derived from geometry:
the union of the Euclidean minimum spanning tree (guarantees one connected network) and each
zone's K nearest neighbours (adds the redundant routes real road grids have). Edge length is the
great-circle distance times a road detour factor. An edge takes the worse road condition of its
two zones: a blocked zone has no usable roads in or out, a limited one slows every road touching
it.

Travel times come from Dijkstra's algorithm with per-source start offsets, which lets one
multi-source run answer "how long from the nearest hospital, clinic or rescue base" for every
zone at once. Unreachable means no path in the graph after blocked roads are removed; the
explanation names the blocked zones on the shortest path of the undamaged network.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field

from lifeline.models import RoadAccess, Site, Zone
from lifeline.optimization.network import distance_m

DETOUR = 1.3  # road distance / straight-line distance
K_NEAREST = 3
SPEED_KMH = {RoadAccess.OPEN: 40.0, RoadAccess.LIMITED: 15.0, RoadAccess.BLOCKED: 4.0}
_RANK = {RoadAccess.OPEN: 0, RoadAccess.LIMITED: 1, RoadAccess.BLOCKED: 2}
HELP_SITE_KINDS = frozenset(
    {"hospital", "clinic", "health_centre", "emergency_base", "ambulance_station", "fire_station"}
)


def _minutes(metres: float, access: RoadAccess) -> float:
    return metres * DETOUR / 1000 / SPEED_KMH[access] * 60


def _worse(a: RoadAccess, b: RoadAccess) -> RoadAccess:
    return a if _RANK[a] >= _RANK[b] else b


@dataclass(frozen=True)
class Road:
    a: str
    b: str
    metres: float
    access: RoadAccess

    @property
    def minutes(self) -> float:
        return _minutes(self.metres, self.access)


@dataclass(frozen=True)
class Route:
    minutes: float
    path: tuple[str, ...]  # zone ids, origin first
    origin: str  # label of where the route starts (site or asset name)
    limited_legs: int

    def describe(self, names: dict[str, str]) -> str:
        via = [names.get(z, z) for z in self.path[1:-1]]
        txt = f"{self.minutes:.0f} min from {self.origin}"
        if via:
            txt += " via " + ", ".join(via)
        if self.limited_legs:
            txt += f" ({self.limited_legs} damaged road{'s' if self.limited_legs > 1 else ''})"
        return txt


@dataclass
class RoadNetwork:
    zones: dict[str, Zone]
    roads: list[Road] = field(default_factory=list)
    adj: dict[str, list[Road]] = field(default_factory=dict)

    @classmethod
    def build(cls, zones: list[Zone]) -> RoadNetwork:
        zmap = {z.id: z for z in zones}
        ids = sorted(zmap)
        dist = {
            (a, b): distance_m(zmap[a].lat, zmap[a].lon, zmap[b].lat, zmap[b].lon)
            for a, b in itertools.combinations(ids, 2)
        }
        pairs = _mst(ids, dist) | _knn(ids, dist, K_NEAREST)
        net = cls(zones=zmap)
        for a, b in sorted(pairs):
            access = _worse(zmap[a].road_access, zmap[b].road_access)
            net.roads.append(Road(a, b, dist[(a, b)], access))
        for r in net.roads:
            if r.access == RoadAccess.BLOCKED:
                continue
            net.adj.setdefault(r.a, []).append(r)
            net.adj.setdefault(r.b, []).append(Road(r.b, r.a, r.metres, r.access))
        return net

    def nearest_zone(self, lat: float, lon: float) -> Zone:
        return min(self.zones.values(), key=lambda z: (distance_m(lat, lon, z.lat, z.lon), z.id))

    def dijkstra(
        self, sources: dict[str, tuple[float, str]]
    ) -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
        """Multi-source Dijkstra. sources: zone id -> (start offset minutes, origin label).

        Returns distance (minutes), predecessor and origin label per reached zone.
        """
        dist: dict[str, float] = {}
        prev: dict[str, str] = {}
        origin: dict[str, str] = {}
        heap = [(off, zid, label) for zid, (off, label) in sources.items()]
        heapq.heapify(heap)
        best = {zid: off for zid, (off, _) in sources.items()}
        while heap:
            d, u, label = heapq.heappop(heap)
            if u in dist:
                continue
            dist[u], origin[u] = d, label
            for r in self.adj.get(u, []):
                nd = d + r.minutes
                if r.b not in dist and nd < best.get(r.b, float("inf")):
                    best[r.b], prev[r.b] = nd, u
                    heapq.heappush(heap, (nd, r.b, label))
        return dist, prev, origin

    def _path(self, prev: dict[str, str], target: str) -> tuple[str, ...]:
        path = [target]
        while path[-1] in prev:
            path.append(prev[path[-1]])
        return tuple(reversed(path))

    def _limited(self, path: tuple[str, ...]) -> int:
        access = {(r.a, r.b): r.access for r in self.roads}
        access |= {(b, a): v for (a, b), v in access.items()}
        return sum(1 for a, b in itertools.pairwise(path) if access[(a, b)] == RoadAccess.LIMITED)

    def blocked_on_direct_route(self, src: str, dst: str) -> list[str]:
        """Zones with blocked roads on the shortest route of the undamaged network."""
        undamaged = RoadNetwork.build(
            [z.model_copy(update={"road_access": RoadAccess.OPEN}) for z in self.zones.values()]
        )
        _, prev, _ = undamaged.dijkstra({src: (0.0, "")})
        path = undamaged._path(prev, dst)
        return [z for z in path if self.zones[z].road_access == RoadAccess.BLOCKED]

    def route(
        self, lat: float, lon: float, target_zone: str, label: str
    ) -> tuple[Route | None, str]:
        """Route from a point (e.g. a depot) to a zone. Returns (route, explanation)."""
        start = self.nearest_zone(lat, lon)
        entry = _minutes(distance_m(lat, lon, start.lat, start.lon), start.road_access)
        if start.id == target_zone:
            return Route(entry, (start.id,), label, 0), "already in the zone"
        dist, prev, _ = self.dijkstra({start.id: (entry, label)})
        if target_zone not in dist:
            blocked = self.blocked_on_direct_route(start.id, target_zone)
            names = ", ".join(self.zones[z].name for z in blocked) or "the target zone"
            return None, f"no open road: blocked at {names}"
        path = self._path(prev, target_zone)
        r = Route(dist[target_zone], path, label, self._limited(path))
        return r, r.describe({z.id: z.name for z in self.zones.values()})

    def help_routes(self, sites: list[Site]) -> HelpTable:
        """One multi-source Dijkstra from every help site."""
        help_sites = [s for s in sites if s.kind in HELP_SITE_KINDS and s.zone_id in self.zones]
        sources: dict[str, tuple[float, str]] = {}
        for s in help_sites:
            z = self.zones[s.zone_id]
            off = 0.0
            if s.lat is not None and s.lon is not None:
                off = _minutes(distance_m(s.lat, s.lon, z.lat, z.lon), z.road_access)
            if z.id not in sources or off < sources[z.id][0]:
                sources[z.id] = (off, s.name)
        dist, prev, origin = self.dijkstra(sources)
        return HelpTable(self, bool(help_sites), dist, prev, origin)


@dataclass
class HelpTable:
    net: RoadNetwork
    any_sites: bool
    dist: dict[str, float]
    prev: dict[str, str]
    origin: dict[str, str]

    def to(self, zone_id: str, lat: float | None = None, lon: float | None = None) -> Route | None:
        if zone_id not in self.dist:
            return None
        z = self.net.zones[zone_id]
        last = 0.0
        if lat is not None and lon is not None:
            last = _minutes(distance_m(lat, lon, z.lat, z.lon), z.road_access)
        path = self.net._path(self.prev, zone_id)  # help site's zone first
        return Route(self.dist[zone_id] + last, path, self.origin[zone_id], self.net._limited(path))


def _mst(ids: list[str], dist: dict[tuple[str, str], float]) -> set[tuple[str, str]]:
    """Kruskal's algorithm with union-find on the complete distance graph."""
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    out: set[tuple[str, str]] = set()
    for (a, b), _ in sorted(dist.items(), key=lambda kv: (kv[1], kv[0])):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
            out.add((a, b))
    return out


def _knn(ids: list[str], dist: dict[tuple[str, str], float], k: int) -> set[tuple[str, str]]:
    def d(a: str, b: str) -> float:
        return dist[(a, b)] if (a, b) in dist else dist[(b, a)]

    out: set[tuple[str, str]] = set()
    for a in ids:
        near = sorted((x for x in ids if x != a), key=lambda x: (d(a, x), x))[:k]
        out |= {(min(a, x), max(a, x)) for x in near}
    return out
