"""Candidate interventions: generation, deterministic preconditions and effect models.

Candidates are generated only from assets the scenario actually has. Every precondition is
checked deterministically and reported pass/fail so operators can see why an option is
(in)feasible. `apply` returns modified copies; nothing here touches a real network.
"""

from __future__ import annotations

from lifeline.models import (
    Asset,
    AssetKind,
    AssetStatus,
    CommNeed,
    Intervention,
    NetworkFamily,
    NetworkSnapshot,
    Precondition,
    RoadAccess,
    Service,
    Zone,
)
from lifeline.optimization.network import (
    covers,
    distance_m,
    need_served,
    power_problem,
)
from lifeline.optimization.routing import DETOUR, SPEED_KMH, RoadNetwork

SETUP_MIN = {"deploy_mobile_bs": 20.0, "activate_satellite": 10.0, "satellite_backhaul": 25.0}
BRIDGE_RANGE_M = 3000.0
SAT_RELOCATE_RANGE_M = 6000.0
VOICE_DATA = [Service.VOICE, Service.DATA, Service.TEXT]


def _pc(name: str, ok: bool, detail: str) -> Precondition:
    return Precondition(name=name, ok=ok, detail=detail)


def generate(
    zones: list[Zone],
    assets: list[Asset],
    snap: NetworkSnapshot,
    needs: list[CommNeed],
) -> list[Intervention]:
    """Candidates for the zones where some intercepted session cannot be carried."""
    by_id = {a.id: a for a in assets}
    zmap = {z.id: z for z in zones}
    unserved = [n for n in needs if n.zone_id in zmap and not need_served(n, snap)]

    def zones_of(family: NetworkFamily) -> list[str]:
        return sorted({n.zone_id for n in unserved if n.zone_id and n.family == family})

    need_zones = zones_of(NetworkFamily.CELLULAR)
    mesh_zones = zones_of(NetworkFamily.MESH)
    out: list[Intervention] = []
    roads = RoadNetwork.build(zones)

    # 1. traffic rebalancing from towers whose zones have headroom
    for zid in need_zones:
        z = zmap[zid]
        for a in assets:
            if a.kind != AssetKind.CELL_TOWER or a.id not in snap.usable_assets or covers(a, z):
                continue
            d = distance_m(a.lat, a.lon, z.lat, z.lon)
            if d > 3500:
                continue
            donor_zones = [zz for zz in zones if covers(a, zz)]
            headroom = all(snap.zones[zz.id].ratio >= 1.3 for zz in donor_zones)
            out.append(
                Intervention(
                    id=f"REBAL-{a.id}-{zid}",
                    kind="rebalance_traffic",
                    label=f"Extend {a.name} coverage into {z.name}",
                    target_zone=zid,
                    asset_ids=[a.id],
                    services=VOICE_DATA,
                    eta_min=5,
                    preconditions=[
                        _pc("within reach", True, f"{d / 1000:.1f} km from zone centre"),
                        _pc(
                            "donor headroom",
                            headroom,
                            ", ".join(
                                f"{zz.name} {min(snap.zones[zz.id].ratio, 9.99):.0%}"
                                for zz in donor_zones
                            )
                            + " (needs ≥130%)",
                        ),
                    ],
                    effect="Tilt/power change shares spare capacity with the target zone.",
                )
            )

    # 2/3. satellite terminals: local access or tower backhaul
    for s in assets:
        if s.kind != AssetKind.SAT_TERMINAL or s.status != AssetStatus.STANDBY:
            continue
        pp = power_problem(s)
        for zid in need_zones:
            z = zmap[zid]
            if not covers(s, z):
                continue
            out.append(
                Intervention(
                    id=f"SAT-{s.id}-{zid}",
                    kind="activate_satellite",
                    label=f"Activate {s.name} for {z.name}",
                    target_zone=zid,
                    asset_ids=[s.id],
                    scarce=[s.id],
                    services=VOICE_DATA,
                    capacity_added=s.bandwidth,
                    eta_min=SETUP_MIN["activate_satellite"],
                    preconditions=[
                        _pc("terminal power", pp is None, pp or "powered"),
                        _pc("coverage", True, f"{s.name} covers {z.name}"),
                    ],
                    effect=f"+{s.bandwidth:.0f} units satellite access (voice/data/text).",
                )
            )
        for t in assets:
            if (
                t.kind != AssetKind.CELL_TOWER
                or t.status == AssetStatus.DOWN
                or t.id in snap.usable_assets
                or "backhaul" not in snap.unusable.get(t.id, "")
            ):
                continue
            tzones = [z for z in zones if covers(t, z)]
            if not any(z.id in need_zones for z in tzones):
                continue
            d = distance_m(s.lat, s.lon, t.lat, t.lon)
            tp = power_problem(t)
            out.append(
                Intervention(
                    id=f"SATBH-{s.id}-{t.id}",
                    kind="satellite_backhaul",
                    label=f"Restore {t.name} backhaul via {s.name}",
                    target_zone=tzones[0].id,
                    asset_ids=[s.id, t.id],
                    scarce=[s.id],
                    services=VOICE_DATA,
                    capacity_added=min(t.capacity, s.bandwidth),
                    eta_min=SETUP_MIN["satellite_backhaul"] + d / 1000 / 30 * 60,
                    preconditions=[
                        _pc("terminal power", pp is None, pp or "powered"),
                        _pc("tower power", tp is None, tp or "powered"),
                        _pc(
                            "terminal within relocation range",
                            d <= SAT_RELOCATE_RANGE_M,
                            f"{d / 1000:.1f} km (max {SAT_RELOCATE_RANGE_M / 1000:.0f} km)",
                        ),
                    ],
                    effect=f"{t.name} back online for "
                    f"{', '.join(z.name for z in tzones)} at ≤{min(t.capacity, s.bandwidth):.0f}"
                    " units.",
                )
            )

    # 4. mesh bridging
    mesh_islands = [
        a
        for a in assets
        if a.kind == AssetKind.MESH_NODE and "mesh island" in snap.unusable.get(a.id, "")
    ]
    seen_clusters: set[frozenset[str]] = set()
    for m in mesh_islands:
        cluster = _mesh_cluster(m, by_id)
        if cluster in seen_clusters:
            continue
        seen_clusters.add(cluster)
        czones = sorted({z.id for z in zones for nid in cluster if covers(by_id[nid], z)})
        if not any(z in mesh_zones for z in czones):
            continue
        for g in assets:
            gateway_ok = g.id in snap.usable_assets and g.kind in (
                AssetKind.CELL_TOWER,
                AssetKind.MOBILE_BS,
                AssetKind.SAT_TERMINAL,
            )
            standby_sat = g.kind == AssetKind.SAT_TERMINAL and g.status == AssetStatus.STANDBY
            if not (gateway_ok or standby_sat):
                continue
            d = min(distance_m(by_id[n].lat, by_id[n].lon, g.lat, g.lon) for n in cluster)
            if d > BRIDGE_RANGE_M * 2:
                continue
            pp = power_problem(g)
            zname = zmap[czones[0]].name if czones else "mesh area"
            out.append(
                Intervention(
                    id=f"MESH-{g.id}-{sorted(cluster)[0]}",
                    kind="bridge_mesh",
                    label=f"Bridge {zname} mesh to {g.name}",
                    target_zone=czones[0] if czones else "",
                    asset_ids=[g.id, *sorted(cluster)],
                    scarce=[g.id] if standby_sat else [],
                    services=[Service.TEXT],
                    eta_min=15,
                    preconditions=[
                        _pc(
                            "gateway in range", d <= BRIDGE_RANGE_M, f"{d / 1000:.1f} km (max 3 km)"
                        ),
                        _pc("gateway power", pp is None, pp or "powered"),
                    ],
                    effect="Mesh text traffic reaches the core network"
                    + (" (activates the satellite terminal)" if standby_sat else "")
                    + ".",
                )
            )

    # 5. mobile base station deployment
    for u in assets:
        if u.kind != AssetKind.MOBILE_BS or u.status != AssetStatus.STANDBY:
            continue
        for zid in need_zones:
            z = zmap[zid]
            route, route_txt = roads.route(u.lat, u.lon, zid, u.depot or u.name)
            drive = route.minutes if route else 0.0
            out.append(
                Intervention(
                    id=f"MBS-{u.id}-{zid}",
                    kind="deploy_mobile_bs",
                    label=f"Deploy {u.name} to {z.name}",
                    target_zone=zid,
                    asset_ids=[u.id],
                    scarce=[u.id],
                    services=VOICE_DATA,
                    capacity_added=u.capacity,
                    eta_min=round(SETUP_MIN["deploy_mobile_bs"] + drive),
                    preconditions=[
                        _pc("road route (Dijkstra)", route is not None, route_txt),
                        _pc("self-backhaul", True, "microwave/satellite backhaul on unit"),
                        _pc("unit power", power_problem(u) is None, "generator on board"),
                    ],
                    effect=f"+{u.capacity:.0f} units cellular capacity in {z.name}.",
                )
            )

    # 6. portable TETRA repeaters for responder radio gaps
    radio_zones = zones_of(NetworkFamily.TETRA)
    for r in assets:
        if r.kind != AssetKind.TETRA_SITE or r.status != AssetStatus.STANDBY:
            continue
        for zid in radio_zones:
            z = zmap[zid]
            route, route_txt = roads.route(r.lat, r.lon, zid, r.name)
            if route is not None:
                eta, how = 10 + route.minutes, f"vehicle: {route_txt}"
            else:  # carried in on foot over the debris
                walk = distance_m(r.lat, r.lon, z.lat, z.lon) * DETOUR / 1000
                eta = 10 + walk / SPEED_KMH[RoadAccess.BLOCKED] * 60
                how = f"carried in on foot ({walk:.1f} km): {route_txt}"
            out.append(
                Intervention(
                    id=f"TETRA-{r.id}-{zid}",
                    kind="tetra_relay",
                    label=f"Deploy {r.name} as TETRA relay for {z.name}",
                    target_zone=zid,
                    asset_ids=[r.id],
                    scarce=[r.id],
                    services=[Service.RESPONDER_RADIO],
                    capacity_added=r.capacity,
                    eta_min=round(eta),
                    preconditions=[
                        _pc("transport", True, how),
                        _pc("battery", power_problem(r) is None, "portable battery pack"),
                    ],
                    effect=f"Responder radio coverage in {z.name}.",
                )
            )
    return out


def _mesh_cluster(start: Asset, by_id: dict[str, Asset]) -> frozenset[str]:
    seen, stack = {start.id}, [start.id]
    while stack:
        cur = by_id[stack.pop()]
        for lk in cur.links:
            if lk in by_id and by_id[lk].kind == AssetKind.MESH_NODE and lk not in seen:
                seen.add(lk)
                stack.append(lk)
    return frozenset(seen)


def apply(iv: Intervention, assets: list[Asset], zones: list[Zone]) -> list[Asset]:
    """Return copies of the assets with the intervention's simulated effect applied."""
    out = [a.model_copy(deep=True) for a in assets]
    by_id = {a.id: a for a in out}
    zmap = {z.id: z for z in zones}
    core = next(a.id for a in out if a.kind == AssetKind.CORE)
    if iv.kind == "rebalance_traffic":
        by_id[iv.asset_ids[0]].extra_zones.append(iv.target_zone)
    elif iv.kind == "activate_satellite":
        s = by_id[iv.asset_ids[0]]
        s.status = AssetStatus.UP
        s.links = [core]
        s.coverage_zones = [iv.target_zone]
    elif iv.kind == "satellite_backhaul":
        s, t = by_id[iv.asset_ids[0]], by_id[iv.asset_ids[1]]
        s.status = AssetStatus.UP
        s.links = [core]
        s.coverage_zones = []
        s.lat, s.lon = t.lat, t.lon
        t.links = [*t.links, s.id]
        t.capacity = min(t.capacity, s.bandwidth)
    elif iv.kind == "bridge_mesh":
        g = by_id[iv.asset_ids[0]]
        if g.status == AssetStatus.STANDBY:  # terminal becomes the mesh gateway only
            g.status = AssetStatus.UP
            g.links = [core]
        node = by_id[iv.asset_ids[1]]
        node.links = [*node.links, g.id]
    elif iv.kind == "deploy_mobile_bs":
        u = by_id[iv.asset_ids[0]]
        z = zmap[iv.target_zone]
        u.status, u.lat, u.lon = AssetStatus.UP, z.lat, z.lon
        u.coverage_zones = [z.id]
        u.links = [core]
    elif iv.kind == "tetra_relay":
        r = by_id[iv.asset_ids[0]]
        z = zmap[iv.target_zone]
        r.status, r.lat, r.lon = AssetStatus.UP, z.lat, z.lon
        r.coverage_zones = [z.id]
    return out
