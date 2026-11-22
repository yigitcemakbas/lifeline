"""AI layer: rate limiting, strict replay of recordings, request compaction and fail-safe
behaviour when the provider is unavailable. No network calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lifeline.ai.cache import AICache
from lifeline.ai.groq import TokenBucket, inline_refs, parse_duration
from lifeline.ai.interpreter import CachedInterpreter, MissingRecording, RecordingInterpreter
from lifeline.ai.transcriber import CachedTranscriber
from lifeline.models import Interpretation, InterpretationBatch, Scenario, SynthesisDelta
from lifeline.simulation.engine import Engine
from tests.conftest import engine
from tests.fakes import FailingInterpreter, RuleInterpreter, ScriptTranscriber


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_token_bucket_refills_continuously_and_follows_the_server() -> None:
    clock = Clock()
    b = TokenBucket(6000, clock=clock)
    b.spend(6000)
    assert b.wait_for(3000) == pytest.approx(30.0)  # 100 tokens per second
    clock.t = 30.0
    assert b.wait_for(3000) == 0.0
    b.sync({"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "800"})
    assert b.limit == 8000 and b.wait_for(800) == 0.0 and b.wait_for(1600) > 0


def test_reset_headers_are_parsed() -> None:
    assert parse_duration("810") == 810.0  # retry-after: plain seconds
    assert parse_duration("577ms") == pytest.approx(0.577)
    assert parse_duration("1m26.4s") == pytest.approx(86.4)
    assert parse_duration("") == 0.0


def test_request_schemas_are_compact() -> None:
    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            out = set(node)
            for k, v in node.items():
                if k != "properties":  # property names are data, not schema keywords
                    out |= keys(v)
                else:
                    out |= {kk for p in v.values() for kk in keys(p)}
            return out
        if isinstance(node, list):
            return {k for v in node for k in keys(v)}
        return set()

    found = keys(inline_refs(SynthesisDelta.model_json_schema()))
    assert not found & {"$ref", "$defs", "title", "default"}


class Recorder(RuleInterpreter):
    """Stands in for the live interpreter inside `RecordingInterpreter`."""

    last_model = {"interpret": "test-model", "synthesis": "test-model"}


def _record(tmp_path: Path, scenario: Scenario) -> AICache:
    cache = AICache(scenario.meta.id, tmp_path)
    rec = RecordingInterpreter(Recorder(), cache)  # type: ignore[arg-type]
    e = Engine(scenario, rec, _RecordingScript(scenario, cache))
    e.run_all()
    return cache


class _RecordingScript(ScriptTranscriber):
    def __init__(self, scenario: Scenario, cache: AICache) -> None:
        super().__init__(scenario)
        self.cache = cache

    def transcribe(self, comm):  # type: ignore[no-untyped-def]
        from lifeline.ai.transcriber import audio_bytes, audio_hash

        out = super().transcribe(comm)
        self.cache.put(
            f"transcribe:{comm.id}",
            hash_=audio_hash(audio_bytes(comm)),
            model=out.model,
            output=out.model_dump(mode="json"),
        )
        return out


def test_recordings_replay_exactly(tmp_path: Path, valley: Scenario) -> None:
    cache = _record(tmp_path, valley)
    cache.save()
    replay = Engine(valley, CachedInterpreter(cache), CachedTranscriber(cache))
    replay.run_all()
    live = engine(valley)
    live.run_all()
    assert replay.tiers == live.tiers
    assert {s: x.state for s, x in replay.gateway.states.items()} == {
        s: x.state for s, x in live.gateway.states.items()
    }


def test_stale_or_missing_recordings_are_errors_not_substitutes(
    tmp_path: Path, valley: Scenario
) -> None:
    cache = _record(tmp_path, valley)
    key = next(k for k in cache.entries if k.startswith("interpret:"))
    cache.entries[key]["hash"] = "0" * 16
    replay = CachedInterpreter(cache)
    with pytest.raises(MissingRecording, match="stale"):
        replay._lookup(key, "1" * 16)
    with pytest.raises(MissingRecording, match="not recorded"):
        replay._lookup("interpret:NOPE", "x")
    with pytest.raises(MissingRecording):
        CachedInterpreter(AICache("none", tmp_path))


def test_failed_analysis_keeps_sessions_held_on_their_floor_and_retries(city: Scenario) -> None:
    flaky = FailingInterpreter(n=2)
    e = Engine(city, flaky, ScriptTranscriber(city))
    for _ in range(4):  # T+0..3: first two interpretation requests fail
        e.step()
    assert e.intel.status.errors
    while e.intel.status.pending_interpretation and e.t < 10:
        e.step()
    assert not e.intel.status.pending_interpretation
    assert set(e.intel.interpretations) == set(e.specs)


def test_unanalysed_life_safety_session_still_ranks_first(city: Scenario) -> None:
    e = Engine(city, FailingInterpreter(n=100), ScriptTranscriber(city))
    for _ in range(7):
        e.step()
    assert not e.intel.interpretations
    t1 = {p.need.id for p in e.priorities if p.tier == 1}
    assert "C-3" in t1  # "trapped" in the transcript sets the life-safety floor


class NotReady(RuleInterpreter):
    def __init__(self) -> None:
        super().__init__()
        self.budget = False

    def ready(self, stage: str, payload_chars: int = 0) -> bool:
        return self.budget


def test_live_mode_defers_requests_until_budget_and_then_batches(city: Scenario) -> None:
    ai = NotReady()
    e = Engine(city, ai, ScriptTranscriber(city), live=True)
    for _ in range(5):
        e.step()
    assert not ai.calls and e.intel.status.pending_interpretation
    ai.budget = True
    e.step()
    assert len(ai.calls[0]) > 1, "pending sessions go out together"


def test_payload_sends_full_text_only_for_new_and_matching_sessions(city: Scenario) -> None:
    e = engine(city)
    for _ in range(12):
        e.step()
    intel = e.intel
    comms = [c for c in e.comms.values() if c.text]
    new = [e.comms["C-5"]]
    payload: dict[str, Any] = intel.build_payload(e.t, new, comms, e.telemetry, 99)
    sent = {c["id"] for c in payload["sessions"]}
    assert "C-5" in sent and "C-1" in sent  # same named place
    assert "C-10" not in sent  # unrelated, already summarised
    assert payload["telemetry"] == [
        {"id": t.id, "asset_id": t.asset_id, "note": t.note} for t in e.telemetry
    ]


def test_batch_results_must_match_session_ids(city: Scenario) -> None:
    class Partial(RuleInterpreter):
        def interpret(self, comms, context):  # type: ignore[no-untyped-def]
            return super().interpret(comms, context)[:1]

    e = Engine(city, Partial(), ScriptTranscriber(city))
    for _ in range(4):
        e.step()
    assert any("no result" in err for err in e.intel.status.errors)
    assert len(e.intel.interpretations) < len(e.specs)


def test_interpretation_batch_schema_round_trips() -> None:
    it = Interpretation(comm_id="C", summary="s", facts=[], confidence=0.5)
    assert (
        InterpretationBatch.model_validate_json(InterpretationBatch(items=[it]).model_dump_json())
        .items[0]
        .comm_id
        == "C"
    )


def test_recording_prunes_entries_from_earlier_inputs(tmp_path: Path) -> None:
    cache = AICache("s", tmp_path)
    cache.entries = {"old": {"hash": "h", "model": "m", "output": {}}}
    cache.put("new", hash_="h", model="m", output={})
    assert cache.prune() == ["old"] and list(cache.entries) == ["new"]
