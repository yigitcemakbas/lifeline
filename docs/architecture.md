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

  Within a tier, needs are ordered lexicographically by life-threat evidence strength, then connectivity state, then the upper bound of people affected, then time waiting. Pre-triage life-safety flags hold a need at T1 or above until a human reviews it. Uncertainty adds `verification_needed`; it never lowers priority.
- **Optimizer**: candidate interventions are generated from the scenario's assets: traffic rebalancing, priority access, satellite activation, mesh bridging, mobile base station deployment, TETRA relay and satellite backhaul. Each candidate is checked against its preconditions. The optimizer then searches the combinations of scarce resources for the plan that maximises (T1 served, T2 served, …, T5 served) lexicographically, preferring fewer resources and then a shorter ETA. Rejected alternatives are reported as *infeasible* (naming the failing precondition) or *dominated* (naming the better use of the resource).

## Safety principles

- AI never invents facts, never computes capacity or feasibility, and never acts.
- All interventions are simulated and need operator approval.
- Nothing is discarded: low-priority needs and all communications stay visible.
- Uncertainty and conflicts are shown, not hidden.
