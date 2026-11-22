from lifeline.ingestion.pretriage import pretriage, processing_order
from lifeline.models import Channel, Communication, SessionKind, SourceRole, TriageFloor
from tests.fakes import make_comm


def comm(
    text: str, role: SourceRole = SourceRole.CITIZEN, ch: Channel = Channel.CELLULAR, t: float = 0
) -> Communication:
    kind = SessionKind.MESSAGE if ch == Channel.MESH else SessionKind.SMS
    return make_comm(text[:8], text, t=t, channel=ch, role=role, kind=kind)


def test_life_safety_terms() -> None:
    c = pretriage(comm("my father is trapped under the wall"))
    assert c.triage_floor == TriageFloor.LIFE_SAFETY
    assert "trapped" in c.triage_terms


def test_responder_and_fragile_channel_elevated() -> None:
    assert pretriage(comm("status ok", SourceRole.RESPONDER)).triage_floor == TriageFloor.ELEVATED
    assert pretriage(comm("hello", ch=Channel.MESH)).triage_floor == TriageFloor.ELEVATED
    assert pretriage(comm("tiles fell on cars")).triage_floor == TriageFloor.NONE


def test_processing_order_puts_life_safety_first() -> None:
    a = pretriage(comm("roof tiles fell", t=1))
    b = pretriage(comm("someone is not breathing", t=5))
    assert [c.id for c in processing_order([a, b])] == [b.id, a.id]
