from __future__ import annotations

from pathlib import Path

import pytest

from lifeline.simulation.scenario import SCENARIO_DIR, list_scenarios
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
    from lifeline.simulation.scenario import load_scenario

    e = run(load_scenario(path))
    assert e.events_done
    assert e.priorities, "needs identified"
    assert e.recommendation.plan, "interventions recommended"
    # nothing silently dropped: every communication is linked or listed as unlinked
    pic = e.intel.picture
    linked = {c for i in pic.incidents for c in i.comm_ids} | set(pic.unlinked_comm_ids)
    linked |= {r.source_id for n in pic.needs for r in n.evidence}
    assert {c.id for c in e.comms} <= linked


def test_non_disaster_outage_is_handled_by_same_engine() -> None:
    from lifeline.simulation.scenario import load_scenario

    e = run(load_scenario(SCENARIO_DIR / "fibre_cut_outage.json"))
    kinds = {i.intervention.kind for i in e.recommendation.plan}
    assert kinds & {"activate_satellite", "deploy_mobile_bs", "satellite_backhaul"}
