from __future__ import annotations

import pytest

from lifeline.ai.interpreter import HeuristicInterpreter
from lifeline.models import Scenario
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import SCENARIO_DIR, load_scenario


@pytest.fixture
def city() -> Scenario:
    return load_scenario(SCENARIO_DIR / "quake_dense_city.json")


@pytest.fixture
def valley() -> Scenario:
    return load_scenario(SCENARIO_DIR / "quake_mountain_valley.json")


def run(scenario: Scenario) -> Engine:
    e = Engine(scenario, HeuristicInterpreter())
    e.run_all()
    return e
