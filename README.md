# Lifeline

**AI-powered multi-network communications intelligence and orchestration.**

When communication infrastructure is damaged, congested or down, whether from disasters, power failures, fibre cuts or telecom outages, Lifeline ingests information from heterogeneous channels (cellular, satellite, mesh, TETRA, citizen apps, facilities, telemetry). It builds an evolving, evidence-backed operational picture with AI and recommends how the available communication resources should be used. Recommendations come from a transparent policy and a deterministic optimizer, and a human operator approves every action.

> All network integrations are **simulated**. Lifeline does not control real networks.

## Quickstart
```bash
python3 -m venv .venv
make install
cp .env.example .env     # optional: add a free GROQ_API_KEY for live AI
make run                 # opens the dashboard at http://localhost:8501
```
The default AI mode, **Cached AI (replay)**, replays real Groq outputs from `scenarios/cache/`, so every scenario works offline and reproducibly. **Live AI (Groq)** calls the model as messages arrive. **Rules only (no AI)** shows what the system can do without the intelligence layer.

## How it works
```
channel adapters → rule-based pre-triage → AI interpretation → AI synthesis (operational picture)
→ network analysis (NetworkX) → priority policy (tiers) → optimizer → operator approval → simulation
```
- **AI interprets and synthesises:** facts labelled verified / reported / inferred with verbatim evidence, incidents merged across duplicate reports, communication needs, dependency chains, contradictions, information gaps, and what changed and why.
- **Deterministic code decides feasibility:** zone connectivity from the network graph, explicit priority tiers (no weighted scores), and a lexicographic search over scarce resources. Every rejected alternative carries its reason.
- **Guards:** quotes must appear verbatim in the source, a single citizen report cannot be "verified", unknown locations are flagged, AI uncertainty can never lower a pre-triage life-safety flag, and no message is dropped.

See [docs/architecture.md](docs/architecture.md) for details and [scenarios/README.md](scenarios/README.md) for the scenario format.

## Demo script (≈5 minutes)
1. **Dense urban environment.** Press ▶. Watch the reports about one collapsed block (citizen app, SMS, TETRA) merge into a single incident with a range of trapped people and a flagged contradiction, not counted three times. In *Needs & priority*, switch on **Compare with telemetry-only view**: the Hillside grandfather and the hospital rank far above where outage severity alone would put them. In *Recommendations*, read the plan and the "Why not…?" list, then **Approve** the mobile base station. It travels (yellow arc), arrives, and the map, needs and plan update.
2. **Mountainous and isolated communities.** The mobile base station is *infeasible* (roads blocked by landslides), so Lifeline turns to the clinic's satellite terminal, a portable TETRA relay for mountain rescue, and satellite backhaul to the ridge tower.
3. **Coastal infrastructure blackout.** Towers stand but have no backhaul; the AI's dependency chain explains the outage. Satellite backhaul restoration and two mobile base stations compete for the port and the shelter. Keep playing: batteries run out, towers drop, and the plan changes.
4. **Validation: metro fibre cut.** A non-disaster outage on the same engine. The care home's oxygen monitoring is held at T1 by the pre-triage fail-safe until an operator reviews it.

## Development
```bash
make check   # ruff, mypy, pytest
make fmt     # format + autofix
make cache   # re-record cached AI outputs with Groq (needs GROQ_API_KEY)
```
See [AGENTS.md](AGENTS.md) for conventions.
