from lifeline.models import (
    Channel,
    CommNeed,
    Communication,
    ConnState,
    EvidenceRef,
    Scenario,
    Service,
    SourceRole,
    TriageFloor,
)
from lifeline.optimization.prioritizer import assign_tier
from tests.conftest import run


def _comm(floor: TriageFloor) -> Communication:
    return Communication(
        id="c1",
        t=0,
        channel=Channel.CELLULAR,
        adapter="x",
        source_id="s",
        source_role=SourceRole.CITIZEN,
        text="someone trapped",
        triage_floor=floor,
    )


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


def test_blocked_road_makes_mobile_bs_infeasible_with_reason(valley: Scenario) -> None:
    e = run(valley)
    blocked = [
        r
        for r in e.recommendation.rejected
        if r.intervention.kind == "deploy_mobile_bs" and r.reason_kind == "infeasible"
    ]
    assert blocked and all("no open road" in r.detail for r in blocked)


def test_scarce_resource_alternatives_are_explained(city: Scenario) -> None:
    e = run(city)
    used = {r for i in e.recommendation.plan for r in i.intervention.scarce}
    dominated = [r for r in e.recommendation.rejected if r.reason_kind == "dominated"]
    assert all(r.intervention.scarce[0] in used for r in dominated)


def test_approval_changes_network_and_replans(city: Scenario) -> None:
    e = run(city)
    before = [i.intervention.id for i in e.recommendation.plan]
    first = e.recommendation.plan[0].intervention
    e.approve(first.id)
    for _ in range(12):  # let pending deployments arrive
        e.step()
    assert first.id in {iv.id for iv in e.applied}
    assert [i.intervention.id for i in e.recommendation.plan] != before
    assert any(t.kind == "outcome" for t in e.timeline)
