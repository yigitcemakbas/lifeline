"""PyDeck map of zones, infrastructure, needs and interventions."""

from __future__ import annotations

from typing import Any

import pydeck as pdk

from lifeline.dashboard.theme import CONN_RGB, TIER_RGB
from lifeline.models import AssetKind, AssetStatus
from lifeline.simulation.engine import Engine

ASSET_RGB = {
    "usable": [200, 208, 218],
    "unusable": [229, 72, 77],
    "standby": [76, 141, 255],
}
KIND_LABEL = {
    AssetKind.CELL_TOWER: "Cell tower",
    AssetKind.SAT_TERMINAL: "Satellite terminal",
    AssetKind.MESH_NODE: "Mesh node",
    AssetKind.MOBILE_BS: "Mobile base station",
    AssetKind.TETRA_SITE: "TETRA site",
    AssetKind.BACKHAUL_LINK: "Backhaul",
    AssetKind.CORE: "Core",
}


def build_deck(engine: Engine) -> pdk.Deck:
    sc = engine.scenario
    snap = engine.snapshot
    zmap = {z.id: z for z in sc.zones}
    zone_rows = []
    for z in sc.zones:
        zs = snap.zones[z.id]
        rgb = CONN_RGB[zs.state]
        zone_rows.append(
            {
                "lon": z.lon,
                "lat": z.lat,
                "r": z.radius_m,
                "fill": [*rgb, 0 if zs.state.value == "connected" else 38],
                "line": [*rgb, 90 if zs.state.value == "connected" else 170],
                "name": z.name,
                "tip": f"<b>{z.name}</b><br/>{zs.state.value.upper()} · pop {z.population:,}"
                f"<br/>capacity {zs.capacity:.0f} / demand {zs.demand:.0f}"
                f"<br/>{'<br/>'.join(zs.causes[:3])}",
            }
        )

    asset_rows, link_rows = [], []
    by_id = {a.id: a for a in engine.assets}
    for a in engine.assets:
        if a.kind == AssetKind.CORE:
            continue
        if a.status == AssetStatus.STANDBY:
            state = "standby"
        elif a.id in snap.usable_assets or (
            a.kind == AssetKind.BACKHAUL_LINK and a.id not in snap.unusable
        ):
            state = "usable"
        else:
            state = "unusable"
        why = snap.unusable.get(a.id, "")
        batt = f"<br/>battery {a.battery_pct:.0f}%" if a.battery_pct is not None else ""
        asset_rows.append(
            {
                "lon": a.lon,
                "lat": a.lat,
                "color": ASSET_RGB[state],
                "size": 9 if a.kind in (AssetKind.CELL_TOWER, AssetKind.MOBILE_BS) else 6,
                "label": a.id,
                "tip": f"<b>{a.name}</b><br/>{KIND_LABEL.get(a.kind, a.kind)} · {state}"
                f"{('<br/>' + why) if why else ''}{batt}",
            }
        )
        for lk in a.links:
            b = by_id.get(lk)
            if not b:
                continue
            dead = a.id in snap.unusable or lk in snap.unusable or a.status == AssetStatus.DOWN
            link_rows.append(
                {
                    "from": [a.lon, a.lat],
                    "to": [b.lon, b.lat],
                    "color": [230, 60, 60, 160] if dead else [90, 120, 150, 110],
                }
            )

    comm_pos = {c.id: (c.lon, c.lat) for c in engine.comms if c.lat is not None}
    need_rows = []
    for i, p in enumerate(engine.priorities):
        n = p.need
        pos = next((comm_pos[r.source_id] for r in n.evidence if r.source_id in comm_pos), None)
        if pos is None and n.zone_id in zmap:
            z = zmap[n.zone_id]
            pos = (z.lon + 0.0012 * ((i % 3) - 1), z.lat + 0.0009 * ((i % 2) * 2 - 1))
        if pos is None:
            continue
        need_rows.append(
            {
                "lon": pos[0],
                "lat": pos[1],
                "color": [*TIER_RGB[p.tier], 235],
                "r": {1: 16, 2: 13, 3: 11, 4: 9, 5: 7}[p.tier],
                "line": [255, 255, 255] if p.served != "served" else [46, 160, 120],
                "tip": f"<b>T{p.tier} · {n.id}</b><br/>{n.who}<br/>{n.purpose}"
                f"<br/>service: {n.service.value} · {p.served}",
            }
        )

    arc_rows = []
    for iv, pending in [(x, False) for x in engine.applied] + [
        (p.intervention, True) for p in engine.pending
    ]:
        tz = zmap.get(iv.target_zone)
        src = by_id.get(iv.asset_ids[0]) if iv.asset_ids else None
        orig = next((a for a in sc.assets if src and a.id == src.id), None)
        if not tz or not orig:
            continue
        arc_rows.append(
            {
                "from": [orig.lon, orig.lat],
                "to": [tz.lon, tz.lat],
                "color": [255, 210, 80] if pending else [63, 220, 200],
            }
        )

    layers: list[Any] = [
        pdk.Layer(
            "ScatterplotLayer",
            zone_rows,
            get_position="[lon, lat]",
            get_radius="r",
            get_fill_color="fill",
            get_line_color="line",
            stroked=True,
            line_width_min_pixels=1,
            pickable=True,
        ),
        pdk.Layer(
            "LineLayer",
            link_rows,
            get_source_position="from",
            get_target_position="to",
            get_color="color",
            get_width=2,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            asset_rows,
            get_position="[lon, lat]",
            get_fill_color="color",
            get_radius="size",
            radius_units="'pixels'",
            stroked=True,
            get_line_color=[11, 16, 22],
            line_width_min_pixels=1,
            pickable=True,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            need_rows,
            get_position="[lon, lat]",
            get_fill_color="color",
            get_radius="r",
            radius_units="'pixels'",
            stroked=True,
            get_line_color="line",
            line_width_min_pixels=2,
            pickable=True,
        ),
        pdk.Layer(
            "ArcLayer",
            arc_rows,
            get_source_position="from",
            get_target_position="to",
            get_source_color="color",
            get_target_color="color",
            get_width=4,
        ),
        pdk.Layer(
            "TextLayer",
            [{"lon": r["lon"], "lat": r["lat"], "t": r["name"]} for r in zone_rows],
            get_position="[lon, lat]",
            get_text="t",
            get_size=13,
            font_family="Arial",
            get_color=[230, 234, 240, 220],
            get_pixel_offset=[0, -26],
        ),
        pdk.Layer(
            "TextLayer",
            asset_rows,
            get_position="[lon, lat]",
            get_text="label",
            get_size=10,
            font_family="Arial",
            get_color=[138, 148, 163, 200],
            get_pixel_offset=[0, 12],
        ),
    ]
    latest = []
    latest_pos = {c.id: (c.lon, c.lat) for c in engine.comms if c.lat is not None}
    tel_asset = {e.id: e.asset_id for e in engine.telemetry}
    for eid in engine.last_event_ids:
        if eid in latest_pos:
            lon, lat = latest_pos[eid]
            latest.append({"lon": lon, "lat": lat})
        elif eid in tel_asset and tel_asset[eid] in by_id:
            a = by_id[tel_asset[eid]]
            latest.append({"lon": a.lon, "lat": a.lat})
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            latest,
            get_position="[lon, lat]",
            get_radius=28,
            radius_units="'pixels'",
            transitions={"getRadius": 1200},
            filled=False,
            stroked=True,
            get_line_color=[255, 255, 255, 230],
            line_width_min_pixels=4,
        )
    )
    focus = latest[0] if latest else {"lat": sc.meta.center_lat, "lon": sc.meta.center_lon}
    view = pdk.ViewState(
        latitude=focus["lat"],
        longitude=focus["lon"],
        zoom=sc.meta.zoom + (0.5 if latest else 0),
        pitch=0,
        transition_duration=1500,
    )
    return pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style="dark_no_labels",
        tooltip={
            "html": "{tip}",
            "style": {"backgroundColor": "#121a23", "color": "#d7e0ea", "fontSize": "12px"},
        },
    )
