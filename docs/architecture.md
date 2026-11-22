# Lifeline architecture

When a base station is congested, the network admits whichever attempt reaches a free channel first. Lifeline sits in the path between callers and the radio network. It intercepts every new session, works out what it is about, and lets the network carry sessions in order of urgency. It gets ahead of the congestion instead of leaving the order to chance.

The engine knows nothing about specific hazards. A disaster, a power failure and a fibre cut are all just scenario data.

## Pipeline

```
scenario events ─► channel adapters ─► intercepted session (held at the gateway)
   ─► speech-to-text (Whisper)          voice calls and TETRA transmissions
   ─► rule-based pre-triage             instant safety floor; never waits on a model
   ─► AI interpretation (batched)       ┐ AI layer: meaning, evidence, uncertainty.
   ─► AI synthesis → operational picture┘ No ranking, capacity maths or feasibility.
   ─► network analysis (NetworkX)       what is usable, where, and why
   ─► priority policy                   explicit tiers + lexicographic criteria → queue order
   ─► admission (every minute)          lexicographic max-flow over core, backhaul and radio
   ─► capacity planning                 search over interventions for sessions nothing can carry
   ─► approval (operator or automatic) ─► simulated effects ─► everything re-evaluated
```

| Layer | Module | Deterministic? |
|---|---|---|
| Channel adapters, pre-triage | `lifeline/ingestion/` | yes |
| Transcription, interpretation, synthesis, guards, correlation hints | `lifeline/ai/` | no (models), with deterministic guards |
| Network analysis, policy, capacity network, admission, planning | `lifeline/optimization/` | yes |
| Clock, dynamics, approvals, timeline | `lifeline/simulation/` | yes |
| Operator UI | `lifeline/dashboard/` | n/a |

## Sessions and channels

Every source of traffic goes through an adapter (`ingestion/adapters.py`). Each adapter declares the network family the session uses and the kinds of session it carries:

| Channel | Network | Sessions | Arrives as |
|---|---|---|---|
| Cellular | cellular | voice calls, SMS | audio (calls), text (SMS) |
| Citizen app | cellular | data | text |
| Facility | cellular | data link | text |
| TETRA | TETRA | radio transmissions | audio |
| Mesh | mesh | messages | text |
| Satellite | satellite messaging | messages | text |
| Telemetry | (network management) | status changes | structured |

The gateway knows where every session attaches from (serving cell, GPS position, or mesh node), so it can route the session before the AI has read it. The scenario loader enforces this, and the tests enforce that every channel has an adapter and is exercised end to end.

## AI layer

All AI runs live on Groq's free tier; the dashboard has no offline or rules-only mode.

- **Transcription**: Whisper (`whisper-large-v3-turbo`) transcribes intercepted audio. Low-confidence transcripts (mean segment log-probability below −0.7) mark the session for verification.
- **Interpretation**: one request per update covers every session ready for it, up to six (`openai/gpt-oss-20b`). For each session the model returns what it is about (life safety, critical facility, emergency operation, routine population traffic, non-urgent), who and why, where the situation is (which can differ from where the caller's phone is), people affected, life threat, facts with verbatim evidence, missing information and confidence.
- **Synthesis**: a delta against the previous operational picture (`openai/gpt-oss-120b`). It merges sessions about the same event into incidents and links related incidents. It also traces dependency chains (backhaul cut → towers isolated → hospital cannot reach dispatch) and records contradictions, information gaps and a change log naming its evidence.
- **Guards** (deterministic, after every model call): quotes must be verbatim substrings of the transcript or message, otherwise they are removed and the fact downgraded to `inferred`. A single citizen cannot make a fact `verified`. Zones must exist, and every session stays in the picture.
- **Correlation hints**: union-find over sessions that are close in space and time or name the same place. Place names match through a consonant skeleton plus edit distance, because speech recognition misspells unfamiliar names: "Lail Apartments" still matches "Lale Apartments".
- **Rate limits**: each model has its own client-side token bucket, synchronised from Groq's `x-ratelimit-*` headers, so requests wait for budget rather than failing. Output budgets are sized per request to stay within the per-minute limit. In live mode, a request that cannot be sent yet is deferred and batched with the next.
- **Failure is never silent**: a failed or deferred request leaves the session held on its pre-triage floor and retries on the next update. Pending work and errors are shown in the dashboard.
- **Recordings**: `make record` runs the same pipeline live and stores every output with a hash of its inputs (`scenarios/cache/`). The offline evaluation and CI replay them strictly: a missing or stale recording is an error, never a substitute.

## Priority policy

No weighted scores. Explicit tiers, first match wins:

- **T1 life safety**: the AI's category or life threat, or a life-safety pre-triage flag on the session or any report of the same incident. AI uncertainty cannot lower this.
- **T2 critical facility or emergency operation**. Before analysis, a responder or facility source, or an elevated pre-triage flag, also lands here.
- **T3 isolated population**, and sessions still awaiting analysis, which are held above routine traffic until the AI has read them.
- **T4 routine population traffic.**
- **T5 non-urgent.**

Within a tier, sessions are ordered lexicographically by:

1. access to help, as road travel time from the nearest hospital or rescue base, in bands (farther first);
2. the fused probability that the life threat is real;
3. connectivity state;
4. the conservative upper bound of people affected;
5. arrival time.

- **Road routing** (`optimization/routing.py`): the road network is derived from zone geometry:
  - **Structure**: a Euclidean minimum spanning tree (Kruskal with union-find) plus each zone's three nearest neighbours.
  - **Edges**: each edge takes the worse road condition of its two zones. Blocked roads are removed, limited ones run at 15 km/h, open ones at 40 km/h.
  - **Travel times**: one multi-source Dijkstra gives every zone its travel time to help. Single-source Dijkstra routes mobile base stations and repeaters from their depot, which sets their ETA.
- **Evidence fusion** (`optimization/fusion.py`):
  - **Life threat**: a Bayesian update in log-odds. Each independent source adds evidence weighted by its role's likelihood ratio (responder 9, facility 6, operator 4, citizen 3), and repeats from one source count once. Where the AI and pre-triage disagree, the source is neutral and the session is flagged.
  - **People affected**: Marzullo's algorithm finds the people count most reports agree on and flags outliers.

## Admission: the gateway

`optimization/admission.py` runs once per simulated minute over the capacity network in `optimization/flow.py`:

- **Capacity network**: one flow graph per network family, sharing a source.
  - Cellular: core → backhaul → access assets (towers, mobile base stations, satellite access) → zone.
  - TETRA site → zone; bridged mesh node → zone; satellite messaging → zone.
  - Assets are node-split, so a backhaul link's capacity caps every tower behind it, and radio capacity is shared across the zones an asset covers.
- **Lexicographic maximum flow**: classes are routed one at a time in queue order, each by augmenting paths (Edmonds–Karp) in the residual network. A path may reroute traffic that is already admitted, for example from one covering tower to another, but can never reduce it. So the head of the queue gets capacity first and nothing admitted earlier is displaced. Sessions are all-or-nothing; background traffic is fluid. Integer units keep it exact.
- **Each tick**:
  1. Sessions past their holding time complete.
  2. Connected sessions are routed first and never pre-empted. If their site fails they drop and are held again.
  3. Connected background traffic keeps its capacity.
  4. Held sessions and waiting background attempts are admitted in policy order.
- **Background traffic**: the zone's ordinary demand (population × demand per person), modelled as a fluid. Calls end with a mean holding time of 3 minutes, new attempts arrive at the matching rate, and attempts waiting longer than 5 minutes give up.
- **Lifeline's policy**:
  - **Urgent first**: tiers 1–3 go first, in urgency order.
  - **Capacity held back**: when a T1 or T2 session doesn't fit yet, the capacity it could already use is held back for it. A large session, such as a hospital's 12-unit data link, is then not starved by small routine traffic. This applies only if the zone could ever carry the session.
  - **Routine traffic**: routine and non-urgent sessions keep arrival order with the background traffic. Lifeline never reorders ordinary traffic among itself.
- **Baselines**, on the same traffic and network:
  - `arrival`: a gateway without content analysis, first come first served, with backfilling.
  - `uncontrolled`: no gateway. Blocked callers retry, and every waiting attempt in a zone has the same chance at freed capacity. Seeded.

## Capacity planning

The gateway orders traffic; it cannot carry a session where there is no capacity at all, such as a cut-off village, a mesh island or a radio dead zone. For those sessions, `optimization/interventions.py` generates candidates from the scenario's assets:

- traffic rebalancing;
- satellite activation;
- satellite backhaul;
- mesh bridging;
- mobile base station deployment;
- TETRA relay.

Each candidate has explicit preconditions: an open road (Dijkstra), relocation range, power, bridge range. `optimization/allocator.py` searches combinations of scarce resources and simulates each plan end to end: effects, then network analysis, then routing every open session in queue order. The winning plan carries the most T1 sessions, then T2 and so on, preferring fewer scarce resources and then a shorter ETA. Rejected alternatives are reported as *infeasible* (naming the failed precondition) or *dominated* (naming the better use).

## Decisions and simulation

The engine (`simulation/engine.py`) advances in one-minute ticks:

1. Release the events due.
2. Run the AI stages on pending work.
3. Rank.
4. Admit.
5. Re-plan.

Interventions are approved by an operator (`approve` / `reject`) or, with `auto_approve`, by the engine in plan order. Each decision is logged with its actor and reason. An approved intervention takes effect after its ETA. With `compare`, the baseline gateways run alongside on the same traffic. The dashboard shows each session's wait under all three policies.

## Evaluation

`lifeline/evaluation.py` (`make evaluate`) runs two experiments:

1. **Scenario replays** with the recorded AI outputs and automatic approval. It reports each tier group's mean wait from interception to first connection, and the share connected within 2 minutes, under the three policies.
2. **A load sweep**: seeded Monte Carlo on one congested cell, background load 1.2× to 2× capacity. It reports life-safety mean and 90th-percentile waits.

## Safety principles

- The AI never invents facts, never computes capacity or feasibility, and never acts.
- A pre-triage floor holds possible life-safety sessions at the top until the AI has read them; a failed AI request never drops or delays a session below that floor.
- Connected sessions are never pre-empted; routine traffic is never reordered among itself; nothing is discarded.
- All network actions are simulated. Every decision, automatic or by an operator, is logged with its reason.
