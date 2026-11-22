"""Every communication source is supported end to end: adapter, interception, analysis,
ranking and admission. These tests fail if a channel is added without that support."""

from __future__ import annotations

import pytest

from lifeline.ingestion.adapters import AUDIO_KINDS, AdapterError, SessionAdapter
from lifeline.ingestion.registry import REGISTRY, ingest
from lifeline.models import Channel, ScenarioEvent, SessionKind
from lifeline.simulation.scenario import ScenarioError, _check_integrity, list_scenarios
from tests.conftest import run

SESSION_CHANNELS = [c for c in Channel if c != Channel.TELEMETRY]


def test_every_channel_has_exactly_one_adapter() -> None:
    assert set(REGISTRY) == set(Channel)
    for ch, adapter in REGISTRY.items():
        assert adapter.channel == ch and adapter.label


@pytest.mark.parametrize("channel", SESSION_CHANNELS, ids=lambda c: c.value)
def test_session_channels_declare_network_and_session_kinds(channel: Channel) -> None:
    adapter = REGISTRY[channel]
    assert isinstance(adapter, SessionAdapter)
    assert adapter.family and adapter.kinds


def test_scenarios_exercise_every_channel_and_session_kind() -> None:
    events = [e for s in list_scenarios() for e in s.events]
    assert {e.channel for e in events} == set(Channel)
    kinds = {ingest(e).kind for e in events if e.kind == "comm"}  # type: ignore[union-attr]
    assert kinds == set(SessionKind)


@pytest.mark.parametrize("channel", SESSION_CHANNELS, ids=lambda c: c.value)
def test_every_channel_is_intercepted_analysed_ranked_and_admitted(channel: Channel) -> None:
    scenario = next(s for s in list_scenarios() if any(e.channel == channel for e in s.events))
    e = run(scenario, auto_approve=True)
    ids = [str(ev.payload["id"]) for ev in scenario.events if ev.channel == channel]
    for sid in ids:
        comm = e.comms[sid]
        assert comm.text, "voice sessions are transcribed"
        if comm.kind in AUDIO_KINDS:
            assert comm.transcript is not None
        assert sid in e.intel.interpretations, "every session is interpreted"
        assert sid in e.tiers, "every session is ranked"
        assert sid in e.gateway.states, "every session reaches the gateway"
    assert any(e.gateway.states[s].first_connected_t is not None for s in ids)


def _event(channel: Channel, **payload: object) -> ScenarioEvent:
    return ScenarioEvent(t=0, kind="comm", channel=channel, payload={"id": "X", **payload})


def test_adapters_reject_sessions_their_channel_cannot_carry() -> None:
    with pytest.raises(AdapterError, match="does not carry"):
        ingest(_event(Channel.TETRA, session="sms", text="hello"))
    with pytest.raises(AdapterError, match="arrive as audio"):
        ingest(_event(Channel.CELLULAR, session="call", text="hello"))
    with pytest.raises(AdapterError, match="need 'text'"):
        ingest(_event(Channel.MESH, audio="x.ogg"))


def test_loader_rejects_unknown_cell_and_missing_audio() -> None:
    s = list_scenarios()[0]
    bad_cell = _event(Channel.CELLULAR, session="sms", text="hi", cell="NOPE")
    with pytest.raises(ScenarioError, match="unknown cell"):
        _check_integrity(s.model_copy(update={"events": [bad_cell]}))
    no_audio = _event(Channel.TETRA, audio="audio/none.ogg", lat=s.zones[0].lat, lon=s.zones[0].lon)
    with pytest.raises(ScenarioError, match="audio missing"):
        _check_integrity(s.model_copy(update={"events": [no_audio]}))
    nowhere = _event(Channel.CELLULAR, session="sms", text="hi")
    with pytest.raises(ScenarioError, match="no cell, coordinates or zone hint"):
        _check_integrity(s.model_copy(update={"events": [nowhere]}))
