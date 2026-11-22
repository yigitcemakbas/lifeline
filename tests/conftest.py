from __future__ import annotations

import pytest

from lifeline.models import Scenario
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import SCENARIO_DIR, load_scenario
from tests.fakes import RuleInterpreter, ScriptTranscriber


@pytest.fixture
def city() -> Scenario:
    return load_scenario(SCENARIO_DIR / "quake_dense_city.json")


@pytest.fixture
def valley() -> Scenario:
    return load_scenario(SCENARIO_DIR / "quake_mountain_valley.json")


def engine(scenario: Scenario, **kw: object) -> Engine:
    return Engine(scenario, RuleInterpreter(), ScriptTranscriber(scenario), **kw)  # type: ignore[arg-type]


def run(scenario: Scenario, **kw: object) -> Engine:
    e = engine(scenario, **kw)
    e.run_all()
    return e
