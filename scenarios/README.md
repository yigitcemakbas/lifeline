# Scenarios

Each `*.json` file is a complete, self-contained environment validated by `lifeline.models.Scenario`. Adding a scenario means adding a file; it needs no code. Scenarios never contain expected recommendations. Recommendations come out of the information, the network state and the constraints.

| File | Category | What it exercises |
|---|---|---|
| `quake_dense_city.json` | exercise | 24 sessions on congested cells: duplicate reports of one collapse (with transcription errors), emergencies no keyword catches (chest pain, a gas smell), a hospital data link competing with routine calls |
| `quake_mountain_valley.json` | exercise | A congested town cell, villages cut off (mobile base station infeasible on blocked roads), satellite messaging, a mesh island, a TETRA dead zone on the pass road |
| `quake_coastal_blackout.json` | exercise | Towers powered but backhaul cut, batteries draining (a site going off air drops its calls), isolated port and shelter |
| `fibre_cut_outage.json` | validation | A non-disaster outage: traffic from a dead business district congesting the neighbouring cell |

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
    // an intercepted session; which session kinds a channel carries is fixed by its adapter
    { "t": 3, "kind": "comm", "channel": "cellular|citizen_app|facility|tetra|mesh|satellite",
      "payload": {
        "id": "C-2", "source_id": "...", "source_role": "citizen|responder|facility|operator",
        "session": "call|sms|data|radio|message",  // default: the channel's first kind
        "cell": "T9",                    // serving access asset, and/or:
        "lat": 0, "lon": 0,              // position (GPS, AVL), and/or:
        "zone_hint": "zone id",          // e.g. the mesh node's zone
        "audio": "audio/<scenario>/C-2.ogg",  // calls and radio: the intercepted audio
        "script": "...",                 // what the audio says; used only to generate it
        "text": "...",                   // SMS, data and messages
        "duration_min": 3                // holding time once connected; null = until the end
      } }
  ]
}
```

| Channel | Session kinds | Network |
|---|---|---|
| `cellular` | `call` (audio), `sms` | cellular |
| `citizen_app` | `data` | cellular |
| `facility` | `data` | cellular |
| `tetra` | `radio` (audio) | TETRA |
| `mesh` | `message` | mesh |
| `satellite` | `message` | satellite messaging |

Rules checked on load:

- the network has at least one `core` asset;
- links point to existing assets, and sites reference existing zones;
- event ids are unique, telemetry targets existing assets, and every channel has an adapter;
- each session is valid for its channel: audio for calls and radio, text otherwise;
- named cells exist and audio files exist;
- the gateway can place every session from its metadata (cell, coordinates or zone hint).

## Audio

`audio/<scenario_id>/*.ogg` is the intercepted audio of every call and TETRA transmission. It is synthetic speech generated from each event's `script` by `make audio` (macOS text-to-speech, then shaped with ffmpeg into narrowband telephone audio or noisy, band-limited radio audio). The engine never reads `script`: Lifeline only hears the audio, through Whisper.

## Recorded AI outputs

`cache/<scenario_id>.json` holds real Whisper and Groq outputs recorded by `make record`, for the offline evaluation and CI (the dashboard always runs live). Entries are keyed by session (`transcribe:<id>`), interpretation batch (`interpret:<ids>`) and synthesis update (`synth:<n>`), and store the model name and a hash of the exact inputs (for transcripts, the audio bytes). Replay is strict: a missing or stale entry raises an error rather than being replaced. Never edit recordings by hand; run `make record` after changing a scenario's sessions or the prompts.
