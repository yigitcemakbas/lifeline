"""Offline evaluation: how long sessions wait for the network, with and without Lifeline.

Three admission policies run on the same network with the same traffic (see
`lifeline.optimization.admission`):

* `lifeline`: urgent sessions first, in urgency order; routine traffic first come first served;
* `arrival`: a gateway without content analysis, first come first served;
* `uncontrolled`: no gateway, how a congested network behaves today (blocked callers retry and
  every waiting attempt has the same chance). Averaged over seeds.

Two experiments:

1. Scenario replay. Each scenario runs with its recorded AI outputs (real Whisper and Groq
   output from `make record`) and automatic approval of capacity interventions; the baseline
   gateways see exactly the same sessions, network state and interventions. A session's wait
   is the time from interception to its first connection; sessions never connected count with
   the time they waited until the end of the run.
2. Load sweep. Seeded Monte Carlo on one congested cell: urgent and routine sessions arrive
   at random over 30 minutes while background traffic offers 1.2x to 2x the cell's capacity.
   This isolates the policy from the particular scenarios.

Usage: `make evaluate` or `python -m lifeline.evaluation`.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field

from lifeline.ai.cache import AICache
from lifeline.ai.interpreter import CachedInterpreter
from lifeline.ai.transcriber import CachedTranscriber
from lifeline.models import (
    Asset,
    AssetKind,
    NetworkFamily,
    Scenario,
    Service,
    SessionSpec,
    Zone,
)
from lifeline.optimization.admission import POLICIES, AdmissionController, Policy
from lifeline.optimization.network import analyse
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios

POLICY_TITLES = {
    "lifeline": "Lifeline",
    "arrival": "First come, first served",
    "uncontrolled": "No gateway (today)",
}
GROUPS = {"Life safety": (1,), "Facility and operations": (2,), "Routine": (4, 5)}
SEEDS = 20  # uncontrolled baseline seeds per scenario
QUICK_MIN = 2.0  # "connected within" threshold

# load sweep
SWEEP_LOADS = (1.2, 1.5, 2.0)
SWEEP_TRIALS = 100
SWEEP_SEED = 7
SWEEP_CAPACITY = 40.0
SWEEP_ARRIVAL_MIN = 30
SWEEP_HORIZON_MIN = 60
SWEEP_MIX = {1: 0.15, 2: 0.15, 4: 0.5, 5: 0.2}  # tier -> share of scripted sessions
SWEEP_RATE_PER_MIN = 1.0


@dataclass
class Waits:
    """Waiting times (minutes) of one group of sessions under one policy."""

    values: list[float] = field(default_factory=list)

    def add(self, v: float) -> None:
        self.values.append(v)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else 0.0

    @property
    def p90(self) -> float:
        if not self.values:
            return 0.0
        v = sorted(self.values)
        return v[min(len(v) - 1, int(0.9 * len(v)))]

    @property
    def quick(self) -> float:
        return sum(w <= QUICK_MIN for w in self.values) / len(self.values) if self.values else 1.0


def _waits() -> dict[str, Waits]:
    return {p: Waits() for p in POLICIES}


@dataclass
class ScenarioResult:
    scenario_id: str
    name: str
    groups: dict[str, dict[str, Waits]] = field(
        default_factory=lambda: {g: _waits() for g in GROUPS}
    )


def group_of(tier: int) -> str | None:
    return next((g for g, tiers in GROUPS.items() if tier in tiers), None)


def controller_waits(engine: Engine) -> dict[Policy, dict[str, float]]:
    """Per policy, every session's wait so far (baseline seeds averaged)."""
    out: dict[Policy, dict[str, float]] = {"lifeline": {}, "arrival": {}, "uncontrolled": {}}
    seeds = [b for b in engine.baselines if b.policy == "uncontrolled"]
    arrival = next((b for b in engine.baselines if b.policy == "arrival"), None)
    for sid in engine.gateway.states:
        out["lifeline"][sid] = engine.gateway.wait(sid, engine.t)
        if arrival:
            out["arrival"][sid] = arrival.wait(sid, engine.t)
        if seeds:
            out["uncontrolled"][sid] = statistics.fmean(b.wait(sid, engine.t) for b in seeds)
    return out


# --------------------------------------------------------------------------- 1. scenarios


def recorded_engine(scenario: Scenario, seeds: int = SEEDS) -> Engine:
    cache = AICache(scenario.meta.id)
    return Engine(
        scenario,
        CachedInterpreter(cache),
        CachedTranscriber(cache),
        auto_approve=True,
        compare=True,
        baseline_seeds=seeds,
    )


def evaluate_scenario(engine: Engine) -> ScenarioResult:
    while engine.t < engine.end_t:
        engine.step()
    result = ScenarioResult(engine.scenario.meta.id, engine.scenario.meta.name)
    waits = controller_waits(engine)
    for sid, tier in engine.tiers.items():
        g = group_of(tier)
        if g is None:
            continue
        for policy in POLICIES:
            result.groups[g][policy].add(waits[policy][sid])
    return result


# --------------------------------------------------------------------------- 2. load sweep


def _sweep_network(load: float) -> tuple[list[Asset], list[Zone], float]:
    dpp = 0.01
    population = round(load * SWEEP_CAPACITY / dpp)
    zone = Zone(id="Z", name="Z", lat=0.0, lon=0.0, radius_m=500, population=population)
    assets = [
        Asset(id="CORE", name="core", kind=AssetKind.CORE, lat=0.0, lon=0.0),
        Asset(
            id="T",
            name="cell",
            kind=AssetKind.CELL_TOWER,
            lat=0.0,
            lon=0.0,
            capacity=SWEEP_CAPACITY,
            coverage_zones=["Z"],
            links=["CORE"],
        ),
    ]
    return assets, [zone], dpp


def _sweep_sessions(rng: random.Random) -> tuple[list[SessionSpec], dict[str, int]]:
    tiers, shares = zip(*SWEEP_MIX.items(), strict=True)
    specs: list[SessionSpec] = []
    tier_of: dict[str, int] = {}
    for minute in range(SWEEP_ARRIVAL_MIN):
        for _ in range(_poisson(rng, SWEEP_RATE_PER_MIN)):
            sid = f"S{len(specs)}"
            tier = rng.choices(tiers, shares)[0]
            data = tier == 2 and rng.random() < 0.5
            specs.append(
                SessionSpec(
                    id=sid,
                    t_arrival=float(minute),
                    family=NetworkFamily.CELLULAR,
                    service=Service.DATA if data else Service.VOICE,
                    zone_id="Z",
                    demand=12.0 if data else 1.0,
                    duration_min=10.0 if data else 3.0,
                )
            )
            tier_of[sid] = tier
    return specs, tier_of


def _poisson(rng: random.Random, lam: float) -> int:
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def load_sweep(load: float, trials: int = SWEEP_TRIALS, seed: int = SWEEP_SEED) -> dict[str, Waits]:
    """Life-safety waits on one cell whose background load is `load` x its capacity."""
    rng = random.Random(seed)
    assets, zones, dpp = _sweep_network(load)
    snap = analyse(assets, zones, dpp)
    out = _waits()
    for trial in range(trials):
        specs, tier_of = _sweep_sessions(rng)
        gates = {p: AdmissionController(p, seed=trial) for p in POLICIES}
        for minute in range(SWEEP_HORIZON_MIN + 1):
            t = float(minute)
            arrived = [s for s in specs if s.t_arrival <= t]
            ranked = [s.id for s in sorted(arrived, key=lambda s: (tier_of[s.id], s.t_arrival))]
            for g in gates.values():
                g.tick(t, arrived, ranked, tier_of, assets, zones, snap, dpp)
        for p, g in gates.items():
            for s in specs:
                if tier_of[s.id] == 1:
                    out[p].add(g.wait(s.id, float(SWEEP_HORIZON_MIN)))
    return out


# --------------------------------------------------------------------------- report


def _cell(w: Waits) -> str:
    return f"{w.mean:.1f} min ({w.quick:.0%})"


def report(results: list[ScenarioResult], sweep: dict[float, dict[str, Waits]]) -> str:
    head = "| | " + " | ".join(POLICY_TITLES[p] for p in POLICIES) + " |\n|---|---|---|---|"
    lines = [
        "Scenario replay: mean wait from interception to connection "
        f"(share connected within {QUICK_MIN:.0f} min)",
        "",
        head,
    ]
    for r in results:
        for g, waits in r.groups.items():
            n = len(waits["lifeline"].values)
            if n:
                cells = " | ".join(_cell(waits[p]) for p in POLICIES)
                lines.append(f"| {r.name}: {g.lower()} ({n}) | {cells} |")
    lines += [
        "",
        f"Load sweep: life-safety mean wait / 90th percentile ({SWEEP_TRIALS} trials per load)",
        "",
        head,
    ]
    for load, waits in sweep.items():
        cells = " | ".join(f"{waits[p].mean:.1f} / {waits[p].p90:.0f} min" for p in POLICIES)
        lines.append(f"| background {load:.1f}x capacity | {cells} |")
    return "\n".join(lines)


def main() -> int:
    results = [evaluate_scenario(recorded_engine(s)) for s in list_scenarios()]
    sweep = {load: load_sweep(load) for load in SWEEP_LOADS}
    print(report(results, sweep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
