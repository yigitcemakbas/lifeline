from lifeline.models import AssetStatus, ConnState, Scenario
from lifeline.optimization.network import analyse


def test_backhaul_loss_makes_powered_tower_unusable(valley: Scenario) -> None:
    assets = [a.model_copy(deep=True) for a in valley.assets]
    next(a for a in assets if a.id == "MW-2").status = AssetStatus.DOWN
    snap = analyse(assets, valley.zones, valley.dynamics.demand_per_person)
    assert "backhaul" in snap.unusable["V3"]
    assert snap.zones["Z-RIDGE"].state == ConnState.ISOLATED


def test_depleted_battery_takes_site_off_air(valley: Scenario) -> None:
    assets = [a.model_copy(deep=True) for a in valley.assets]
    v1 = next(a for a in assets if a.id == "V1")
    v1.power, v1.battery_pct = "battery", 0
    snap = analyse(assets, valley.zones, valley.dynamics.demand_per_person)
    assert snap.unusable["V1"] == "battery depleted"


def test_unbridged_mesh_is_an_island(valley: Scenario) -> None:
    snap = analyse(valley.assets, valley.zones, valley.dynamics.demand_per_person)
    assert "mesh island" in snap.unusable["N1"]
