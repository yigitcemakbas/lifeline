from lifeline.models import (
    CommNeed,
    Communication,
    ConnState,
    EvidenceRef,
    Scenario,
    Service,
    TriageFloor,
)
from lifeline.optimization.prioritizer import assign_tier
from tests.conftest import engine, run
from tests.fakes import make_comm


def _comm(floor: TriageFloor) -> Communication:
    return make_comm("c1", "someone trapped", floor=floor)


def test_pretriage_floor_holds_t1_even_if_ai_disagrees() -> None:
    need = CommNeed(
        id="N",
        who="x",
        purpose="p",
        service=Service.VOICE,
        category="non_urgent",
        evidence=[EvidenceRef(source_id="c1", quote="trapped")],
    )
    tier, _, flags = assign_tier(need, ConnState.CONNECTED, {"c1": _comm(TriageFloor.LIFE_SAFETY)})
    assert tier == 1
    assert any("pre-triage" in f for f in flags)


def test_population_tier_depends_on_isolation() -> None:
    need = CommNeed(id="N", who="x", purpose="p", service=Service.VOICE, category="population")
    assert assign_tier(need, ConnState.ISOLATED, {})[0] == 3
    assert assign_tier(need, ConnState.CONGESTED, {})[0] == 4


def test_unanalysed_session_is_held_above_routine_traffic() -> None:
    need = CommNeed(
        id="c1", who="x", purpose="p", service=Service.VOICE, category="population", analysed=False
    )
    tier, rule, _ = assign_tier(need, ConnState.CONGESTED, {"c1": _comm(TriageFloor.NONE)})
    assert tier == 3 and "awaiting analysis" in rule
    assert assign_tier(need, ConnState.CONGESTED, {"c1": _comm(TriageFloor.ELEVATED)})[0] == 3
    assert assign_tier(need, ConnState.CONGESTED, {"c1": _comm(TriageFloor.LIFE_SAFETY)})[0] == 1


def test_ai_life_safety_without_any_keyword_is_t1() -> None:
    need = CommNeed(id="c1", who="x", purpose="p", service=Service.VOICE, category="life_safety")
    comms = {"c1": make_comm("c1", "my husband has chest pains and cannot stand")}
    assert assign_tier(need, ConnState.CONGESTED, comms)[0] == 1


def test_blocked_road_makes_mobile_bs_infeasible_with_reason(valley: Scenario) -> None:
    e = run(valley)
    blocked = [
        r
        for r in e.recommendation.rejected
        if r.intervention.kind == "deploy_mobile_bs" and r.reason_kind == "infeasible"
    ]
    assert blocked and all("no open road" in r.detail for r in blocked)


def test_scarce_resource_alternatives_are_explained(city: Scenario) -> None:
    e = run(city, auto_approve=True)
    used = {r for i in e.recommendation.plan for r in i.intervention.scarce}
    dominated = [r for r in e.recommendation.rejected if r.reason_kind == "dominated"]
    assert all(r.intervention.scarce[0] in used for r in dominated)


def test_approval_changes_network_and_replans(valley: Scenario) -> None:
    e = engine(valley)
    while not e.recommendation.plan:
        e.step()
    before = [i.intervention.id for i in e.recommendation.plan]
    first = e.recommendation.plan[0].intervention
    e.approve(first.id)
    for _ in range(int(first.eta_min) + 2):  # let the deployment arrive
        e.step()
    assert first.id in {iv.id for iv in e.applied}
    assert [i.intervention.id for i in e.recommendation.plan] != before
    assert any(t.kind == "outcome" for t in e.timeline)


def test_operator_rejection_is_logged_and_respected(valley: Scenario) -> None:
    e = engine(valley)
    while not e.recommendation.plan:
        e.step()
    iv = e.recommendation.plan[0].intervention
    e.reject(iv.id)
    assert iv.id not in {i.intervention.id for i in e.recommendation.plan}
    assert any(r.reason_kind == "rejected_by_operator" for r in e.recommendation.rejected)
    assert e.timeline[-1].actor == "operator" or any(
        t.kind == "decision" and t.actor == "operator" for t in e.timeline
    )
