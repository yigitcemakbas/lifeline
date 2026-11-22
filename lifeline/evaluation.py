"""Offline evaluation of the allocation policy against two baselines.

Three policies allocate the same network with the same min-cost flow; only the admission rule
and the queue order differ:

* `lifeline`: urgent needs get reserved capacity in urgency order (Lifeline's policy);
* `arrival`: first come, first served: every need, urgent or not, gets reserved capacity in
  the order it was first reported;
* `best_effort`: no reservation; every need shares its zone's capacity proportionally, as an
  urgency-unaware network does.

Two experiments:

1. Scenario replay. Each scenario runs with automatic approval and its recorded AI outputs. At
   every step the current network state (assets, interventions, priority-access zones) is
   allocated under each policy, and the time each open need is fully served is integrated:
   need-minutes served / need-minutes open, for life-safety (T1) and all urgent (T1-T2) needs.
2. Contention trials. Seeded Monte Carlo: one congested cell, a random mix of needs arriving in
   random order, capacity swept from 25% to 75% of total demand. Measures the share of
   life-safety needs fully served. This isolates the effect of the queue order, which the
   scenarios exercise only lightly (few needs compete inside the same zone).

Usage: `make evaluate` or `python -m lifeline.evaluation`.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field

from lifeline.ai.cache import AICache
from lifeline.ai.interpreter import CachedInterpreter, HeuristicInterpreter, Interpreter
from lifeline.models import (
    Asset,
    AssetKind,
    CommNeed,
    Scenario,
    Service,
    Zone,
)
from lifeline.optimization.flow import ALL_CATEGORIES, URGENT, need_demand, with_allocation
from lifeline.optimization.network import analyse, need_served
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios

POLICIES = ("lifeline", "arrival", "best_effort")
POLICY_TITLES = ("Lifeline (urgency order)", "First come, first served", "Best effort")
ADMIT = {"lifeline": URGENT, "arrival": ALL_CATEGORIES, "best_effort": frozenset[str]()}
HORIZON_AFTER_LAST_EVENT_MIN = 30.0

# contention trials
TRIALS = 500
SEED = 7
NEEDS_PER_TRIAL = (8, 20)
CATEGORY_MIX = {  # share of incoming needs per category
    "life_safety": 0.15,
    "critical_facility": 0.10,
    "emergency_operation": 0.15,
    "population": 0.40,
    "non_urgent": 0.20,
}
CATEGORY_RANK = {
    "life_safety": 1,
    "critical_facility": 2,
    "emergency_operation": 2,
    "population": 4,
    "non_urgent": 5,
}
CAPACITY_SHARES = (0.25, 0.5, 0.75)


@dataclass
class Coverage:
    served: float = 0.0  # need-minutes (or trials) fully served
    open: float = 0.0  # need-minutes (or trials) open

    @property
    def share(self) -> float:
        return self.served / self.open if self.open else 1.0

    def add(self, weight: float, ok: bool) -> None:
        self.open += weight
        self.served += weight * ok


def _coverage() -> dict[str, Coverage]:
    return {p: Coverage() for p in POLICIES}


@dataclass
class ScenarioResult:
    scenario_id: str
    name: str
    life_safety: dict[str, Coverage] = field(default_factory=_coverage)
    urgent: dict[str, Coverage] = field(default_factory=_coverage)


def _order(policy: str, ranked: list[CommNeed]) -> list[CommNeed]:
    if policy == "arrival":
        return sorted(ranked, key=lambda n: (n.first_seen_t, n.id))
    return ranked


def _served_ids(
    assets: list[Asset],
    zones: list[Zone],
    dpp: float,
    priority_zones: set[str],
    ranked: list[CommNeed],
    policy: str,
) -> set[str]:
    order = _order(policy, ranked)
    snap = analyse(assets, zones, dpp, priority_zones)
    snap = with_allocation(snap, assets, zones, order, dpp, priority_zones, ADMIT[policy])
    return {n.id for n in order if need_served(n, snap)}


# --------------------------------------------------------------------------- 1. scenarios


def evaluate_scenario(scenario: Scenario, interpreter: Interpreter) -> ScenarioResult:
    engine = Engine(scenario, interpreter, auto_approve=True)
    result = ScenarioResult(scenario.meta.id, scenario.meta.name)
    horizon = max(e.t for e in scenario.events) + HORIZON_AFTER_LAST_EVENT_MIN
    engine.step()
    while engine.t < horizon:
        t0 = engine.t
        tiers = {p.need.id: p.tier for p in engine.priorities}
        ranked = [p.need for p in engine.priorities]
        served = {
            policy: _served_ids(
                engine.assets, engine.zones, engine.dpp, engine.priority_zones, ranked, policy
            )
            for policy in POLICIES
        }
        engine.step()
        dt = min(engine.t, horizon) - t0
        for policy in POLICIES:
            for nid, tier in tiers.items():
                ok = nid in served[policy]
                if tier == 1:
                    result.life_safety[policy].add(dt, ok)
                if tier <= 2:
                    result.urgent[policy].add(dt, ok)
    return result


def recorded_interpreter(scenario: Scenario) -> CachedInterpreter:
    return CachedInterpreter(AICache(scenario.meta.id), HeuristicInterpreter())


# --------------------------------------------------------------------------- 2. contention


def _trial_needs(rng: random.Random) -> list[CommNeed]:
    cats, weights = zip(*CATEGORY_MIX.items(), strict=True)
    needs = []
    for i in range(rng.randint(*NEEDS_PER_TRIAL)):
        cat = rng.choices(cats, weights)[0]
        service = Service.DATA if rng.random() < 0.3 else Service.VOICE
        needs.append(
            CommNeed(
                id=f"N{i}",
                who=f"need {i}",
                zone_id="Z",
                purpose="synthetic",
                service=service,
                category=cat,
                first_seen_t=float(i),  # arrival order = generation order
            )
        )
    return needs


def contention_trials(
    capacity_share: float, trials: int = TRIALS, seed: int = SEED
) -> dict[str, Coverage]:
    """Share of life-safety needs fully served when one congested cell carries all needs."""
    rng = random.Random(seed)
    zone = Zone(id="Z", name="Z", lat=0.0, lon=0.0, radius_m=500, population=0)
    out = _coverage()
    for _ in range(trials):
        needs = _trial_needs(rng)
        if not any(n.category == "life_safety" for n in needs):
            continue
        capacity = capacity_share * sum(need_demand(n) for n in needs)
        assets = [
            Asset(id="CORE", name="core", kind=AssetKind.CORE, lat=0.0, lon=0.0),
            Asset(
                id="T",
                name="cell",
                kind=AssetKind.CELL_TOWER,
                lat=0.0,
                lon=0.0,
                capacity=capacity,
                coverage_zones=["Z"],
                links=["CORE"],
            ),
        ]
        ranked = sorted(needs, key=lambda n: (CATEGORY_RANK[n.category], n.first_seen_t))
        for policy in POLICIES:
            served = _served_ids(assets, [zone], 0.0, {"Z"}, ranked, policy)
            for n in needs:
                if n.category == "life_safety":
                    out[policy].add(1.0, n.id in served)
    return out


# --------------------------------------------------------------------------- report


def _row(label: str, cov: dict[str, Coverage]) -> str:
    return f"| {label} | " + " | ".join(f"{cov[p].share:.0%}" for p in POLICIES) + " |"


def report(results: list[ScenarioResult], contention: dict[float, dict[str, Coverage]]) -> str:
    head = "| | " + " | ".join(POLICY_TITLES) + " |\n|---|---|---|---|"
    lines = ["Scenario replay: need-minutes fully served", "", head]
    for r in results:
        lines.append(_row(f"{r.name}: life-safety", r.life_safety))
        lines.append(_row(f"{r.name}: all urgent", r.urgent))
    lines += ["", f"Contention trials: life-safety needs fully served ({TRIALS} trials)", "", head]
    for share, cov in contention.items():
        lines.append(_row(f"capacity {share:.0%} of demand", cov))
    return "\n".join(lines)


def main() -> int:
    results = []
    for s in list_scenarios():
        interp = recorded_interpreter(s)
        results.append(evaluate_scenario(s, interp))
        if interp.misses:
            print(f"warning: {s.meta.id}: {len(interp.misses)} rules fallbacks", file=sys.stderr)
    contention = {share: contention_trials(share) for share in CAPACITY_SHARES}
    print(report(results, contention))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
