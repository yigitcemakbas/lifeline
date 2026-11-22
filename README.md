# Lifeline

**Urgency-aware communications orchestration for degraded networks.**

When cellular infrastructure is damaged, congested or cut off, whether by a disaster, a power failure or a fibre cut, the capacity that remains is handed out first come, first served. A trapped person's call competes on equal terms with routine traffic. Lifeline reads every incoming report across cellular, satellite, mesh, TETRA, citizen apps, facilities and network telemetry. It builds an evidence-backed picture of who needs connectivity and why, ranks those needs by urgency, and allocates and restores capacity in that order.

An LLM interprets the messages. Deterministic, tested algorithms make every decision: ranking, routing, bandwidth allocation and planning. Every result can be traced to the evidence and rule that produced it.

![Lifeline operator dashboard](docs/dashboard.png)

> All channel adapters and network actions are simulated. Lifeline controls no real network.

## Results

`make evaluate` replays every scenario and allocates the same network state under three policies, using the same min-cost flow:

- **Lifeline**: reserved capacity for urgent needs, in urgency order.
- **First come, first served**: reserved capacity for every need, in arrival order.
- **Best effort**: no reservation; proportional sharing, as an urgency-unaware network behaves.

<!-- evaluation:start -->
**Contention trials.** 500 seeded trials. In each, 8–20 needs (15% life-safety, 25% other urgent, 60% routine) arrive in random order at one congested cell. The table shows the share of life-safety needs that get their full bandwidth:

| | Lifeline (urgency order) | First come, first served | Best effort |
|---|---|---|---|
| capacity 25% of demand | 95% | 24% | 0% |
| capacity 50% of demand | 100% | 47% | 0% |
| capacity 75% of demand | 100% | 68% | 0% |

**Scenario replay.** The share of time each open need had its full bandwidth, using the recorded AI outputs:

| | Lifeline (urgency order) | First come, first served | Best effort |
|---|---|---|---|
| Dense urban environment: life-safety | 44% | 44% | 0% |
| Dense urban environment: all urgent | 52% | 52% | 0% |
| Mountainous and isolated communities: life-safety | 69% | 69% | 0% |
| Mountainous and isolated communities: all urgent | 55% | 55% | 7% |
| Coastal infrastructure blackout: life-safety | 95% | 95% | 26% |
| Coastal infrastructure blackout: all urgent | 95% | 95% | 18% |
| Metro fibre cut (non-disaster): life-safety | 51% | 51% | 51% |
| Metro fibre cut (non-disaster): all urgent | 51% | 51% | 51% |

Both kinds of reservation beat best effort by a wide margin. In the four scenarios, first come, first served ties with urgency order: few needs compete inside the same zone, so reserving capacity at all is what matters. The contention trials isolate the order itself. When routine traffic arrives first, first come, first served gives it the capacity, while urgency order still serves almost every life-safety need, even at 25% of demand. The fibre-cut scenario is identical across policies: its needs sit in zones with no coverage at all until restoration arrives, which no allocation policy can change.
<!-- evaluation:end -->

## How it works

```
channel adapters ─► rule-based pre-triage ─► AI interpretation ─► AI synthesis (operational picture)
   ─► network analysis ─► priority policy ─► min-cost flow allocation ─► plan search ─► approval ─► simulation
```

| Stage | What it does | Module |
|---|---|---|
| Ingestion | Normalises seven channel types into one message model | `lifeline/ingestion/` |
| Pre-triage | Keyword and source-role rules set a life-safety floor instantly, before any AI call | `ingestion/pretriage.py` |
| Interpretation | LLM extracts facts (verified / reported / inferred) with verbatim quotes, location, people count, life threat | `ai/interpreter.py` |
| Synthesis | LLM updates a running operational picture: merged incidents, needs, dependency chains, contradictions, gaps | `ai/intelligence.py` |
| Guards | Quotes must appear verbatim in the source; a single citizen cannot make a fact "verified"; nothing is dropped | `ai/intelligence.py` |
| Network analysis | Graph of core, backhaul, towers, satellite and mesh; per-zone state with the cause traced through the graph | `optimization/network.py` |
| Priority policy | Explicit tiers, then a lexicographic within-tier key; no weighted scores | `optimization/prioritizer.py` |
| Allocation | Urgency-ordered min-cost flow over the network | `optimization/flow.py` |
| Planning | Search over combinations of scarce resources, each plan simulated end to end | `optimization/allocator.py` |

### Algorithms

| Problem | Approach |
|---|---|
| How far is help? | Road graph derived from zone geometry (Kruskal minimum spanning tree plus k-nearest neighbours); damaged roads slowed, blocked roads removed. One multi-source Dijkstra gives every zone its travel time from the nearest hospital or rescue base. People farther from help rank higher within a tier. |
| Is the life threat real? | Bayesian update in log-odds space: each independent source adds evidence weighted by its reliability (rescuer > facility > citizen). Repeated messages from one phone count once. AI and rule disagreements are neutralised and flagged. |
| How many people? | Marzullo's interval-intersection algorithm finds the count most reports agree on and flags outliers; ranking still uses the conservative maximum. |
| Who gets bandwidth? | Min-cost flow (network simplex, integer units) from core through backhaul, towers and zones to per-need traffic classes. Penalties grow geometrically with queue position, so minimising cost is exactly lexicographic: the most urgent need is served as fully as the network allows before the next gets anything. |
| Which actions? | Mobile base stations, satellite terminals, TETRA repeaters, mesh bridging, priority access and traffic rebalancing are generated from the scenario's assets, checked against explicit preconditions, and combined by exhaustive search (greedy above 4,000 combinations). A plan wins by serving the most T1 needs, then T2, and so on to T5, then on fewest scarce resources, then on shortest ETA. Every rejected alternative is reported as infeasible (naming the failed precondition) or dominated (naming the better use). |

### Safety properties

- **AI never decides.** The AI layer contains no ranking, capacity or feasibility logic, and the optimisation layer makes no LLM calls.
- **Fail-safe floor.** A rule-based life-safety flag holds a need at the top tier until a person reviews it, whatever the AI says.
- **Evidence or it didn't happen.** Facts without a verbatim source are downgraded to "inferred" and flagged.
- **Nothing is dropped.** Every message stays attached to an incident or is listed as unlinked; lower-priority needs remain visible.
- **Explicit decision authority.** Interventions are approved by an operator or, in automatic mode, by the engine in plan order; every decision is logged with its actor and reason.
- **Event-agnostic core.** The engine contains no hazard-specific code (enforced by a test); hazards exist only as scenario data.

## Scenarios

| Scenario | What it exercises |
|---|---|
| Dense urban environment | Duplicate reports about one collapse with conflicting counts; a degraded hospital; a need that network telemetry alone does not reveal |
| Mountainous and isolated communities | Roads blocked by landslides make the mobile base station infeasible; satellite, mesh and TETRA alternatives |
| Coastal infrastructure blackout | Towers powered but backhaul cut; batteries draining over time; two mobile base stations competing |
| Metro fibre cut (validation) | A routine, non-disaster outage handled by the same engine |

Scenarios are plain JSON validated against the domain model ([format](scenarios/README.md)); adding one requires no code.

## Getting started

Requires Python 3.12+.

```bash
python3 -m venv .venv
make install
make run        # dashboard at http://localhost:8501
```

By default the dashboard replays **recorded AI outputs** (real `openai/gpt-oss-120b` responses via Groq, stored in `scenarios/cache/`), so it runs offline and reproducibly. A recording is used only if its input hash matches the current input. For live interpretation, put a free [Groq](https://console.groq.com) key in `.env` (see `.env.example`) and choose **Live AI**. **Rules only** shows what the system does with no AI at all.

In the sidebar, choose **Automatic** approval to let the plan execute in order, or **Operator approval** to approve or reject each intervention yourself.

## Development

```bash
make check      # ruff lint + format check, mypy, pytest
make evaluate   # policy evaluation (results above)
make cache      # re-record AI outputs with Groq (needs GROQ_API_KEY)
make fmt        # auto-format and autofix
```

CI runs lint, type checks, tests, the evaluation and a wheel build on Python 3.12 and 3.13. Conventions for contributors are in [AGENTS.md](AGENTS.md); design details are in [docs/architecture.md](docs/architecture.md).

```
lifeline/
  ingestion/      channel adapters, pre-triage
  ai/             interpreters (Groq, recorded, rules), synthesis, guards, correlation hints
  optimization/   network analysis, routing, evidence fusion, prioritisation, flow, planning
  simulation/     scenario loading, engine (clock, dynamics, approvals)
  dashboard/      Streamlit + PyDeck operator interface
  evaluation.py   policy evaluation against baselines
scenarios/        scenario data and recorded AI outputs
tests/            unit and end-to-end tests (no network calls)
```

## Limitations

- Integrations are simulated. Capacity is in abstract units (one voice session = 1), not radio-level throughput.
- The road network is derived from zone geometry, not street data.
- The scenarios and their messages are synthetic, written to exercise specific behaviours; the evaluation measures the allocation policy on them, not real-world outcomes.
