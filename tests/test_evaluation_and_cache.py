"""Evaluation harness, recorded-output replay and request budgeting."""

from pathlib import Path
from typing import Any

from lifeline.ai.cache import AICache
from lifeline.ai.interpreter import CachedInterpreter, HeuristicInterpreter, _max_output_tokens
from lifeline.evaluation import contention_trials, evaluate_scenario
from lifeline.models import Scenario, SynthesisDelta
from lifeline.simulation.engine import Engine


def test_urgency_order_beats_both_baselines_under_contention() -> None:
    cov = contention_trials(0.5, trials=60)
    assert cov["lifeline"].share > cov["arrival"].share > cov["best_effort"].share
    assert cov["lifeline"].share > 0.95


def test_scenario_replay_reports_bounded_shares(valley: Scenario) -> None:
    r = evaluate_scenario(valley, HeuristicInterpreter())
    for cov in (*r.life_safety.values(), *r.urgent.values()):
        assert 0.0 <= cov.share <= 1.0
    assert r.urgent["lifeline"].share >= r.urgent["best_effort"].share


def test_stale_recording_is_not_replayed(tmp_path: Path, city: Scenario) -> None:
    cache = AICache(city.meta.id, directory=tmp_path)
    first = next(e for e in city.events if e.kind == "comm")
    key = f"interpret:{first.payload['id']}"
    cache.put(key, hash_="0" * 16, model="m", output={})
    interp = CachedInterpreter(cache, HeuristicInterpreter())
    Engine(city, interp).run_all()
    assert f"{key}: recorded from different inputs (stale)" in interp.misses


def test_synthesis_sends_full_text_only_for_new_and_matching_reports(city: Scenario) -> None:
    payloads: list[dict[str, Any]] = []

    class Spy(HeuristicInterpreter):
        def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
            payloads.append(payload)
            return super().synthesize(payload)

    Engine(city, Spy()).run_all()
    last = payloads[-1]
    new = {c["id"] for c in last["new_communications"]}
    with_text = {c["id"] for c in last["all_communications"] if "text" in c}
    assert new <= with_text
    assert len(with_text) < len(last["all_communications"])


def test_output_budget_shrinks_with_prompt_and_respects_floor() -> None:
    small = _max_output_tokens("x" * 3000)
    large = _max_output_tokens("x" * 18000)
    assert small > large >= 1500
