# Scenarios

Each `*.json` file is a complete, self-contained environment validated by `lifeline.models.Scenario`. Adding a scenario means adding a file; it needs no code. Scenarios never contain expected recommendations. Recommendations come out of the information, the network state and the constraints.

| File | Category | What it exercises |
|---|---|---|
| `quake_dense_city.json` | exercise | Correlating duplicate reports, a degraded hospital, responders on TETRA, a need that telemetry alone does not reveal |
| `quake_mountain_valley.json` | exercise | Isolated villages, roads blocked (mobile base station infeasible), satellite/mesh/TETRA alternatives |
| `quake_coastal_blackout.json` | exercise | Backhaul cut with powered towers, batteries draining over time, two mobile base stations competing |
| `fibre_cut_outage.json` | validation | A non-disaster outage on the same engine |

## Schema

```jsonc
{
  "meta": {
    "id": "unique_id", "name": "...", "description": "...",
    "hazard_label": "display only, never read by the engine",
    "category": "exercise | validation", "order": 1,
    "center_lat": 0, "center_lon": 0, "zoom": 12
  },
  "dynamics": {
    "battery_drain_pct_per_min": 0.5,   // battery-powered assets drain over simulated time
    "demand_per_person": 0.01           // emergency traffic demand, capacity units per resident
  },
  "zones": [{ "id", "name", "lat", "lon", "radius_m", "population", "road_access": "open|limited|blocked" }],
  "sites": [{ "id", "name", "kind", "zone_id", "critical" }],
  "assets": [{
    "id", "name",
    "kind": "core|backhaul_link|cell_tower|sat_terminal|mesh_node|mobile_base_station|tetra_site",
    "lat", "lon",
    "status": "up|degraded|down|standby",       // standby = available resource, not active
    "capacity", "bandwidth",                    // abstract capacity units (bandwidth for satellite)
    "power": "grid|battery|none", "battery_pct",
    "links": ["asset ids"],                     // graph edges towards the core
    "coverage_zones": ["zone ids"],             // explicit coverage (otherwise coverage_radius_m)
    "mobile": false, "depot": "", "serves": ["voice|data|text|responder_radio"]
  }],
  "events": [
    { "t": 0, "kind": "telemetry", "channel": "telemetry",
      "payload": { "id": "TM-1", "asset_id": "T1", "changes": { "status": "down" }, "note": "..." } },
    { "t": 2, "kind": "comm", "channel": "cellular|satellite|mesh|tetra|citizen_app|facility",
      "payload": { "id": "C-1", "source_id": "...", "source_role": "citizen|responder|facility",
                   "lat": 0, "lon": 0, "zone_hint": "optional zone id", "text": "...",
                   "delivery_status": "..." } }
  ]
}
```

Rules checked on load: at least one `core` asset, links point to existing assets, sites reference existing zones, event ids are unique, and telemetry targets existing assets.

## Cached AI outputs

`cache/<scenario_id>.json` holds real Groq outputs recorded by `make cache`. They are keyed by message id (`interpret:<id>`) and checkpoint (`synth:<n>`) and store an input hash and the model name. An entry is replayed only when its hash matches the current input, so a changed scenario or prompt falls back to the rules interpreter (listed in the sidebar) rather than replaying stale output. Never edit them by hand; re-record with `make cache` after changing a scenario's messages.
