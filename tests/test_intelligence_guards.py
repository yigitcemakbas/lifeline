from lifeline.ai.intelligence import IntelligenceEngine
from lifeline.ai.interpreter import HeuristicInterpreter
from lifeline.models import (
    Channel,
    CommNeed,
    Communication,
    EvidenceRef,
    Fact,
    FactStatus,
    Incident,
    Scenario,
    Service,
    SourceRole,
    SynthesisDelta,
)


def _comm() -> Communication:
    return Communication(
        id="C-1",
        t=0,
        channel=Channel.CELLULAR,
        adapter="x",
        source_id="s",
        source_role=SourceRole.CITIZEN,
        text="Two people trapped at Lale Apartments",
    )


def test_fabricated_quotes_are_dropped_and_downgraded(city: Scenario) -> None:
    eng = IntelligenceEngine(city, HeuristicInterpreter())
    delta = SynthesisDelta(
        summary="s",
        incidents=[
            Incident(
                id="INC-1",
                title="t",
                comm_ids=["C-1", "C-404"],
                facts=[
                    Fact(
                        claim="ten people trapped",
                        status=FactStatus.REPORTED,
                        evidence=[EvidenceRef(source_id="C-1", quote="ten people trapped")],
                    ),
                    Fact(
                        claim="two trapped",
                        status=FactStatus.VERIFIED,
                        evidence=[EvidenceRef(source_id="C-1", quote="two people trapped")],
                    ),
                ],
            )
        ],
        needs=[
            CommNeed(
                id="NEED-1",
                who="x",
                zone_id="Z-NOWHERE",
                purpose="p",
                service=Service.VOICE,
                category="life_safety",
            )
        ],
    )
    eng.merge(delta, 1.0, [_comm()], [])
    inc = eng.picture.incidents[0]
    assert inc.comm_ids == ["C-1"]
    fabricated, real = inc.facts
    assert fabricated.status == FactStatus.INFERRED and not fabricated.evidence
    assert real.status == FactStatus.REPORTED  # single citizen source cannot be "verified"
    need = eng.picture.needs[0]
    assert need.zone_id is None and need.verification_needed
    assert eng.picture.guard_notes
