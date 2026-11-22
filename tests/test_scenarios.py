from __future__ import annotations

from pathlib import Path

import pytest

from lifeline.simulation.scenario import SCENARIO_DIR, list_scenarios, load_scenario
from tests.conftest import run

FORBIDDEN = ("earthquake", "quake", "seismic")


def test_all_scenarios_load() -> None:
    ids = {s.meta.id for s in list_scenarios()}
    assert {"quake_dense_city", "quake_mountain_valley", "quake_coastal_blackout"} <= ids
    assert "fibre_cut_outage" in ids


def test_core_has_no_hazard_specific_code() -> None:
    root = Path(__file__).resolve().parents[1] / "lifeline"
    for path in root.rglob("*.py"):
        text = path.read_text().lower()
        for word in FORBIDDEN:
            assert word not in text, f"{word!r} found in {path}"


@pytest.mark.parametrize("path", sorted(SCENARIO_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_every_scenario_runs_end_to_end(path: Path) -> None:
    e = run(load_scenario(path), auto_approve=True, compare=True, baseline_seeds=2)
    assert e.events_done and e.finished
    assert set(e.gateway.states) == set(e.specs), "every session reached the gateway"
    assert set(e.intel.interpretations) == set(e.specs), "every session was analysed"
    # nothing silently dropped: every session is in an incident, listed as unlinked, or
    # visibly awaiting the next (batched) synthesis
    pic = e.intel.picture
    linked = {c for i in pic.incidents for c in i.comm_ids} | set(pic.unlinked_comm_ids)
    linked |= set(e.intel.status.pending_synthesis)
    assert set(e.comms) <= linked
    # every life-safety session is connected by the end, or held for a stated reason
    for sid, tier in e.tiers.items():
        st = e.gateway.states[sid]
        if tier == 1 and st.first_connected_t is None:
            assert sid in e.recommendation.unserved_needs or e.pending


def test_lifeline_never_makes_urgent_sessions_wait_longer_than_fcfs(city) -> None:  # type: ignore[no-untyped-def]
    from lifeline.evaluation import controller_waits

    e = run(city, auto_approve=True, compare=True, baseline_seeds=2)
    waits = controller_waits(e)
    urgent = [s for s, t in e.tiers.items() if t <= 2]
    assert sum(waits["lifeline"][s] for s in urgent) < sum(waits["arrival"][s] for s in urgent)
    for s in urgent:
        assert waits["lifeline"][s] <= waits["arrival"][s]


def test_non_disaster_outage_is_handled_by_same_engine() -> None:
    e = run(load_scenario(SCENARIO_DIR / "fibre_cut_outage.json"))
    kinds = {i.intervention.kind for i in e.recommendation.plan}
    assert kinds & {"activate_satellite", "deploy_mobile_bs", "satellite_backhaul"}
