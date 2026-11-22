from lifeline.ai.intelligence import IntelligenceEngine
from lifeline.models import (
    EvidenceRef,
    Fact,
    FactStatus,
    Incident,
    Interpretation,
    Scenario,
    SynthesisDelta,
)
from tests.fakes import RuleInterpreter, ScriptTranscriber, make_comm


def _engine(city: Scenario) -> IntelligenceEngine:
    return IntelligenceEngine(city, RuleInterpreter(), ScriptTranscriber(city))


def test_fabricated_quotes_are_dropped_and_downgraded(city: Scenario) -> None:
    eng = _engine(city)
    delta = SynthesisDelta(
        summary="s",
        incidents=[
            Incident(
                id="INC-1",
                title="t",
                zone_id="Z-NOWHERE",
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
    )
    eng.merge(delta, 1.0, [make_comm("C-1", "Two people trapped at Lale Apartments")], [])
    inc = eng.picture.incidents[0]
    assert inc.comm_ids == ["C-1"]
    assert inc.zone_id is None
    fabricated, real = inc.facts
    assert fabricated.status == FactStatus.INFERRED and not fabricated.evidence
    assert real.status == FactStatus.REPORTED  # single citizen source cannot be "verified"
    assert eng.picture.guard_notes


def test_interpretation_quotes_are_checked_against_the_transcript(city: Scenario) -> None:
    eng = _engine(city)
    comm = make_comm("C-9", "my husband has chest pains, we are on bakery street")
    it = Interpretation(
        comm_id="C-9",
        summary="s",
        category="life_safety",
        zone_id="Z-OLD",
        facts=[
            Fact(
                claim="chest pains",
                status=FactStatus.REPORTED,
                evidence=[EvidenceRef(source_id="C-9", quote="has chest pains")],
            ),
            Fact(
                claim="heart attack",
                status=FactStatus.VERIFIED,
                evidence=[EvidenceRef(source_id="C-9", quote="is having a heart attack")],
            ),
        ],
        confidence=0.9,
    )
    guarded = eng._guard_interpretation(it, comm)
    assert guarded.facts[0].status == FactStatus.REPORTED
    assert guarded.facts[1].status == FactStatus.INFERRED and guarded.facts[1].flagged


def test_sound_alike_place_names_are_correlated(city: Scenario) -> None:
    from lifeline.ai.correlator import correlation_hints

    a = make_comm("A", "the building at Lale Apartments collapsed", t=2)
    b = make_comm("B", "my mother is inside Lail Apartments block C", t=3)
    hints = correlation_hints([a, b], city)
    assert hints and set(hints[0]["comm_ids"]) == {"A", "B"}
