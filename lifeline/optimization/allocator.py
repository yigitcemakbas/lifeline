"""Capacity planning. The gateway orders traffic; it cannot carry sessions where there is no
capacity at all. Every candidate plan is evaluated end to end: apply its simulated effects,
re-run the network analysis, then route every open session in queue order through the capacity
network (lifeline.optimization.flow) to see which sessions it could carry. Searches combinations
of scarce resources (mobile base stations, satellite terminals, portable repeaters) and picks
the plan that carries the most T1 sessions, then T2, ..., T5 (lexicographic), preferring fewer
scarce resources and then a shorter ETA. Non-scarce actions (traffic rebalancing, mesh bridging
via existing gateways) are added where they help. Every candidate that is not chosen is
reported with the reason.
"""

from __future__ import annotations

import itertools
from collections import defaultdict

from lifeline.models import (
    Asset,
    Intervention,
    PlanItem,
    PrioritizedNeed,
    Recommendation,
    Rejected,
    Zone,
)
from lifeline.optimization.flow import with_allocation
from lifeline.optimization.interventions import apply
from lifeline.optimization.network import analyse, need_served

MAX_COMBOS = 4000


def _served(
    plan: list[Intervention],
    assets: list[Asset],
    zones: list[Zone],
    needs: list[PrioritizedNeed],
    demand_per_person: float,
) -> set[str]:
    a = assets
    for iv in plan:
        a = apply(iv, a, zones)
    snap = analyse(a, zones, demand_per_person)
    snap = with_allocation(snap, a, zones, [p.need for p in needs])
    return {p.need.id for p in needs if need_served(p.need, snap)}


def _vector(served: set[str], needs: list[PrioritizedNeed]) -> tuple[int, ...]:
    return tuple(sum(1 for p in needs if p.tier == t and p.need.id in served) for t in range(1, 6))


def recommend(
    candidates: list[Intervention],
    assets: list[Asset],
    zones: list[Zone],
    needs: list[PrioritizedNeed],
    demand_per_person: float,
    operator_rejected: set[str] | None = None,
) -> Recommendation:
    operator_rejected = operator_rejected or set()
    tier_of = {p.need.id: p.tier for p in needs}
    rejected: list[Rejected] = []
    feasible: list[Intervention] = []
    for c in candidates:
        if c.id in operator_rejected:
            rejected.append(
                Rejected(
                    intervention=c, reason_kind="rejected_by_operator", detail="Operator rejected"
                )
            )
        elif not c.feasible:
            failed = "; ".join(f"{p.name}: {p.detail}" for p in c.preconditions if not p.ok)
            rejected.append(Rejected(intervention=c, reason_kind="infeasible", detail=failed))
        else:
            feasible.append(c)

    def served(plan: list[Intervention]) -> set[str]:
        return _served(plan, assets, zones, needs, demand_per_person)

    non_scarce = [c for c in feasible if not c.scarce]
    groups: dict[str, list[Intervention]] = defaultdict(list)
    for c in feasible:
        if c.scarce:
            groups[c.scarce[0]].append(c)
    options: list[list[Intervention | None]] = [[None, *cs] for cs in groups.values()]
    n_combos = 1
    for o in options:
        n_combos *= len(o)

    def score(plan: list[Intervention], s: set[str]) -> tuple[tuple[int, ...], int, float]:
        scarce_used = sum(1 for p in plan if p.scarce)
        eta = max((p.eta_min for p in plan if p.scarce), default=0.0)
        return (_vector(s, needs), -scarce_used, -eta)

    best_plan: list[Intervention] = list(non_scarce)
    best_served = served(best_plan)
    best_key = score(best_plan, best_served)
    if n_combos <= MAX_COMBOS:
        combos = itertools.product(*options)
        for combo in combos:
            chosen = [c for c in combo if c is not None]
            plan = chosen + non_scarce
            s = served(plan)
            k = score(plan, s)
            if k > best_key:
                best_plan, best_served, best_key = plan, s, k
    else:  # greedy fallback for large instances
        plan = list(non_scarce)
        for group in options:
            best_c, best_k = None, score(plan, served(plan))
            for cand in group[1:]:
                if cand is None:
                    continue
                s = served([*plan, cand])
                k = score([*plan, cand], s)
                if k > best_k:
                    best_c, best_k = cand, k
            if best_c:
                plan.append(best_c)
        best_plan, best_served = plan, served(plan)

    # prune actions that add nothing
    pruned = list(best_plan)
    for c in list(pruned):
        trial = [p for p in pruned if p is not c]
        if served(trial) >= best_served:
            pruned = trial
    best_plan = pruned

    items: list[PlanItem] = []
    for c in best_plan:
        without = served([p for p in best_plan if p is not c])
        marginal = sorted(best_served - without, key=lambda nid: tier_of.get(nid, 9))
        tiers = sorted({tier_of[n] for n in marginal})
        rationale = (
            f"Carries {len(marginal)} held session(s), highest tier T{tiers[0]}"
            if tiers
            else "Supports other plan items"
        )
        items.append(PlanItem(intervention=c, needs_served=marginal, rationale=rationale))
    items.sort(
        key=lambda i: (min((tier_of[n] for n in i.needs_served), default=9), i.intervention.eta_min)
    )

    used = {r: it for it in items for r in it.intervention.scarce}
    chosen_ids = {c.id for c in best_plan}
    for c in feasible:
        if c.id in chosen_ids:
            continue
        if c.scarce and c.scarce[0] in used:
            other = used[c.scarce[0]]
            ts = sorted({tier_of[n] for n in other.needs_served})
            detail = f"{c.scarce[0]} is better used for '{other.intervention.label}'" + (
                f" (carries T{ts[0]} sessions)" if ts else ""
            )
            rejected.append(Rejected(intervention=c, reason_kind="dominated", detail=detail))
        else:
            gain = served([*best_plan, c]) - best_served
            detail = (
                "would not carry any additional session"
                if not gain
                else f"lower value than chosen plan (would add {len(gain)} session(s))"
            )
            rejected.append(Rejected(intervention=c, reason_kind="no_need", detail=detail))

    return Recommendation(
        plan=items,
        rejected=rejected,
        served_by_tier=dict(enumerate(_vector(best_served, needs), start=1)),
        unserved_needs=[p.need.id for p in needs if p.need.id not in best_served],
    )
