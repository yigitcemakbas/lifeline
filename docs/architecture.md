# Lifeline architecture

Lifeline answers one question: **where is connectivity needed most urgently, what is happening there, what resources are available, and how can we restore or preserve communications most effectively?**

It works the same for any communications outage, whether a natural disaster, a power failure, a fibre cut or a telecom fault. A hazard is only data in a scenario file.

## Pipeline

```
scenario events ─► simulated channel adapters ─► Communication / TelemetryEvent
   ─► rule-based pre-triage            (instant safety floor; never waits on the LLM)
   ─► AI interpretation (per message)  ┐ AI layer: meaning, evidence, uncertainty.
   ─► AI synthesis → OperationalPicture┘ No ranking, no capacity maths, no feasibility.
   ─► network analysis (NetworkX)      (deterministic: what is usable, where, and why)
   ─► priority policy                  (explicit tiers + ordering criteria → ordered needs)
   ─► optimizer                        (lexicographic search over feasible plans)
   ─► operator approve / reject ─► simulation applies effects ─► everything re-evaluated
```

| Layer | Module | Deterministic? |
|---|---|---|
| Ingestion + pre-triage | `lifeline/ingestion/` | yes |
| Interpretation, synthesis, correlation hints, evidence validation | `lifeline/ai/` | no (LLM), with deterministic guards |
| Network analysis, policy, interventions, optimizer | `lifeline/optimization/` | yes |
| Clock, dynamics, approvals, timeline | `lifeline/simulation/` | yes |
| Operator UI | `lifeline/dashboard/` | n/a |

## AI layer

- **Interpretation** turns each communication into facts (each `verified`/`reported`/`inferred`, with verbatim evidence), needs mentioned, location, people range, life threat (`yes`/`no`/`unknown`), affected operations, missing information and confidence.
- **Synthesis** runs once per checkpoint (a batch of events at the same scenario time). It receives the previous picture, the new interpretations, deterministic correlation hints, verified network facts and operator decisions, and it **updates** the operational picture: incidents and how they relate, communication needs, dependency chains (e.g. backhaul cut → towers isolated → hospital link down), contradictions, information gaps, and a change log that names the evidence behind each change.
- **Guards** (deterministic, after every LLM call): every quote must be a verbatim substring of the referenced communication, otherwise it is removed and the fact downgraded to `inferred`. People counts are merged as the max of overlapping ranges, never summed. Every communication stays attached to the picture.
- **Interpreters**: `GroqInterpreter` (free Groq API, JSON-schema output, validated by pydantic), `CachedInterpreter` (default; replays real Groq outputs from `scenarios/cache/`), and `HeuristicInterpreter` (rule-based fallback, low confidence, clearly labelled).

## Deterministic layer

- **Network analysis**: a graph of core, backhaul, towers, satellite terminals, mesh nodes and zones. An asset is usable when it is up, powered and has a path to the core. Each zone gets a categorical connectivity state (`connected`/`congested`/`degraded`/`isolated`) with the cause traced through the graph.
- **Priority policy**: no weighted scores. Explicit tiers apply:
  - **T1** life safety without adequate connectivity;
  - **T2** critical facility or emergency operation disrupted;
  - **T3** isolated population;
  - **T4** degraded population service;
  - **T5** non-urgent.

  Within a tier, needs are ordered lexicographically by access to help (road travel time band from the nearest hospital, clinic or rescue base; farther first, since people close to help can reach it or be reached more easily; unreachable or unknown location counts as farthest; not applied to T2, where the need is the facility itself), then the fused probability that the life threat is real, then connectivity state, then the conservative upper bound of people affected, then time waiting. Bands keep small numeric differences from flipping the order. Pre-triage life-safety flags hold a need at T1 or above until a human reviews it. Uncertainty adds `verification_needed`; it never lowers priority.
- **Road routing** (`optimization/routing.py`): scenarios describe zones, not streets, so the road network is derived: the Euclidean minimum spanning tree (Kruskal with union-find, guarantees connectivity) plus each zone's 3 nearest neighbours (redundant routes). Edge length is great-circle distance × 1.3 detour; an edge takes the worse road condition of its two zones (blocked: removed; limited: 15 km/h; open: 40 km/h). One multi-source Dijkstra with per-source start offsets gives every zone its travel time from the nearest help site. Single-source Dijkstra routes mobile base stations and TETRA repeaters from their depot, which sets their ETA; when no route exists, the precondition names the blocked zones on the shortest route of the undamaged network.
- **Evidence fusion** (`optimization/fusion.py`): the probability that a life threat is real is a Bayesian update in log-odds: prior 0.2, then each independent source adds signal × ln(likelihood ratio of its role) (responder 9, facility 6, operator 4, citizen 3). The signal is the AI's per-message reading (+1 yes, −1 no) or the pre-triage flag (+0.5); when they disagree the source is neutral and the need is flagged. Repeated messages from one source count once. People affected: Marzullo's algorithm finds the range the most reports agree on and flags outliers; ranking still uses the maximum over all reports.
- **Bandwidth allocation** (`optimization/flow.py`): a min-cost flow solved with network simplex on integer units. Source → core → backhaul → access assets (node-split so each asset's capacity caps everything through it; radio capacity shared across covered zones) → zones → traffic classes → sink. In a zone with priority access (and capacity ≥25% of demand), each urgent voice/data need is its own class; everything else shares a best-effort pool, split proportionally. Each class has a bypass edge from the source whose cost is the penalty for unmet demand: P_r = (D+1)^(N−r) by queue position r, pools cost 1. Minimising cost is therefore exactly lexicographic: the top of the queue is served as fully as physically possible before the next item gets anything. A voice/data need counts as served only if the flow carries its full demand (population needs: half).
- **Optimizer**: candidate interventions are generated from the scenario's assets: traffic rebalancing, priority access, satellite activation, mesh bridging, mobile base station deployment, TETRA relay and satellite backhaul. Each candidate is checked against its preconditions. The optimizer then searches, simulating each plan end to end (apply effects → network analysis → min-cost flow), the combinations of scarce resources for the plan that maximises (T1 served, T2 served, …, T5 served) lexicographically, preferring fewer resources and then a shorter ETA. Rejected alternatives are reported as *infeasible* (naming the failing precondition) or *dominated* (naming the better use of the resource).

## Safety principles

- AI never invents facts, never computes capacity or feasibility, and never acts.
- All interventions are simulated and need operator approval.
- Nothing is discarded: low-priority needs and all communications stay visible.
- Uncertainty and conflicts are shown, not hidden.
