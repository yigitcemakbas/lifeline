# Lifeline

**AI-powered multi-network communications intelligence and orchestration.**

When communication infrastructure is damaged, congested or down, whether from disasters, power failures, fibre cuts or telecom outages, Lifeline ingests information from heterogeneous channels (cellular, satellite, mesh, TETRA, citizen reports, telemetry). It builds an evolving, evidence-backed operational picture with AI and recommends how the available communication resources should be used. Recommendations come from a transparent policy and a deterministic optimizer, and a human operator approves every action.

> All network integrations are **simulated**. Lifeline does not control real networks.

## Quickstart
```bash
python3 -m venv .venv
make install
cp .env.example .env     # optional: add a free GROQ_API_KEY for live AI
make run                 # opens the dashboard
```
Without a key the dashboard replays cached AI outputs, so all scenarios work offline and reproducibly.

## Development
```bash
make check   # ruff, mypy, pytest
make fmt     # format + autofix
make cache   # regenerate cached AI outputs with Groq (needs GROQ_API_KEY)
```
See [AGENTS.md](AGENTS.md) for conventions and [docs/architecture.md](docs/architecture.md) for the design.
