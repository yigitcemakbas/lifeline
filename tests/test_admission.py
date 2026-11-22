"""The admission gateway and the lexicographic capacity network behind it."""

from __future__ import annotations

from lifeline.models import (
    Asset,
    AssetKind,
    AssetStatus,
    NetworkFamily,
    Service,
    SessionSpec,
    Zone,
)
from lifeline.optimization.admission import AdmissionController
from lifeline.optimization.flow import CapacityNetwork, Demand
from lifeline.optimization.network import analyse

DPP = 0.01


def _zone(zid: str = "Z", pop: int = 0) -> Zone:
    return Zone(id=zid, name=zid, lat=47.5, lon=21.6, radius_m=500, population=pop)


def _tower(tid: str, cap: float, zones: list[str], links: list[str] | None = None) -> Asset:
    return Asset(
        id=tid,
        name=tid,
        kind=AssetKind.CELL_TOWER,
        lat=47.5,
        lon=21.6,
        capacity=cap,
        coverage_zones=zones,
        links=links or ["CORE"],
    )


def _core() -> Asset:
    return Asset(id="CORE", name="core", kind=AssetKind.CORE, lat=47.5, lon=21.6)


def _spec(sid: str, t: float = 0.0, demand: float = 1.0, dur: float | None = 3.0) -> SessionSpec:
    return SessionSpec(
        id=sid,
        t_arrival=t,
        family=NetworkFamily.CELLULAR,
        service=Service.VOICE if demand == 1 else Service.DATA,
        zone_id="Z",
        demand=demand,
        duration_min=dur,
    )


def _d(key: str, units: float, zone: str = "Z", whole: bool = True) -> Demand:
    return Demand(key, zone, NetworkFamily.CELLULAR, units, whole)


# ----------------------------------------------------------------------------- flow


def _backhaul(bid: str, cap: float) -> Asset:
    return Asset(
        id=bid,
        name=bid,
        kind=AssetKind.BACKHAUL_LINK,
        lat=47.5,
        lon=21.6,
        capacity=cap,
        links=["CORE"],
    )


def test_route_reroutes_admitted_traffic_but_never_reduces_it() -> None:
    # X is covered by A and B, Y only by B; each tower sits behind a 1-unit backhaul. The first
    # class (in X) is routed through B (found first); the second (in Y) can only use B, so the
    # augmenting path moves the first onto A instead of rejecting the second.
    zones = [_zone("X"), _zone("Y")]
    assets = [
        _core(),
        _backhaul("BH2", 1.0),
        _backhaul("BH1", 1.0),
        _tower("B", 10.0, ["X", "Y"], ["BH2"]),
        _tower("A", 10.0, ["X"], ["BH1"]),
    ]
    net = CapacityNetwork(assets, zones, analyse(assets, zones, DPP))
    assert net.route(_d("first", 1.0, "X")) == 1.0
    assert net.via["first"] == "B"
    assert net.route(_d("second", 1.0, "Y")) == 1.0
    assert net.route(_d("third", 1.0, "X")) == 0.0  # both backhauls are now full
    assert net.carried == {"first": 100, "second": 100}
    assert net.loads()["A"][0] == 1.0 and net.loads()["B"][0] == 1.0


def test_whole_sessions_are_all_or_nothing_fluid_takes_what_fits() -> None:
    zones, assets = [_zone()], [_core(), _tower("T", 2.5, ["Z"])]
    net = CapacityNetwork(assets, zones, analyse(assets, zones, DPP))
    assert net.route(_d("data", 3.0)) == 0.0  # does not fit: nothing is held back
    assert net.route(_d("bg", 3.0, whole=False)) == 2.5


def test_backhaul_capacity_caps_every_tower_behind_it() -> None:
    zones = [_zone()]
    bh = _backhaul("BH", 2.0)
    assets = [_core(), bh, _tower("T1", 5.0, ["Z"], ["BH"]), _tower("T2", 5.0, ["Z"], ["BH"])]
    net = CapacityNetwork(assets, zones, analyse(assets, zones, DPP))
    assert net.available("Z", NetworkFamily.CELLULAR) == 2.0


# ----------------------------------------------------------------------------- gateway


def _tick(g: AdmissionController, t: float, specs, ranked, tiers, assets, zones) -> list[str]:
    snap = analyse(assets, zones, DPP)
    return [
        e.session_id
        for e in g.tick(t, specs, ranked, tiers, assets, zones, snap, DPP)
        if e.kind == "connected"
    ]


def test_urgent_sessions_are_admitted_first_and_never_preempted() -> None:
    zones, assets = [_zone()], [_core(), _tower("T", 2.0, ["Z"])]
    g = AdmissionController("lifeline")
    routine = [_spec("r1"), _spec("r2")]
    assert _tick(g, 0, routine, ["r1", "r2"], {"r1": 4, "r2": 4}, assets, zones) == ["r1", "r2"]
    urgent = _spec("u", t=1)
    specs = [*routine, urgent]
    tiers = {"r1": 4, "r2": 4, "u": 1}
    assert _tick(g, 1, specs, ["u", "r1", "r2"], tiers, assets, zones) == []  # no pre-emption
    assert g.states["u"].state == "held"
    for t in (2, 3):
        _tick(g, t, specs, ["u", "r1", "r2"], tiers, assets, zones)
    assert g.states["u"].state == "connected" and g.wait("u", 3) == 2


def test_urgency_order_beats_arrival_order_under_contention() -> None:
    zones, assets = [_zone()], [_core(), _tower("T", 1.0, ["Z"])]
    specs = [_spec("routine", t=0), _spec("urgent", t=0.5)]
    tiers = {"routine": 4, "urgent": 1}
    lifeline, fcfs = AdmissionController("lifeline"), AdmissionController("arrival")
    for g in (lifeline, fcfs):
        _tick(g, 1, specs, ["urgent", "routine"], tiers, assets, zones)
    assert lifeline.states["urgent"].state == "connected"
    assert fcfs.states["routine"].state == "connected"


def test_session_on_a_failed_tower_drops_and_is_held_again() -> None:
    zones = [_zone()]
    up = [_core(), _tower("T", 2.0, ["Z"])]
    g = AdmissionController("lifeline")
    _tick(g, 0, [_spec("a")], ["a"], {"a": 1}, up, zones)
    down = [_core(), up[1].model_copy(update={"status": AssetStatus.DOWN})]
    snap = analyse(down, zones, DPP)
    events = g.tick(1, [_spec("a")], ["a"], {"a": 1}, down, zones, snap, DPP)
    assert [(e.session_id, e.kind) for e in events] == [("a", "dropped")]
    assert g.states["a"].state == "held" and g.states["a"].drops == 1


def test_large_urgent_session_reserves_freed_capacity_until_it_fits() -> None:
    # Background fills the cell; the hospital link (12 units) must not be starved by the
    # small routine calls behind it.
    zones, assets = [_zone(pop=2000)], [_core(), _tower("T", 20.0, ["Z"])]
    specs = [_spec("hospital", t=1, demand=12.0, dur=None)]
    specs += [_spec(f"r{i}", t=1 + i * 0.1) for i in range(20)]
    tiers = {"hospital": 2} | {f"r{i}": 4 for i in range(20)}
    ranked = ["hospital"] + [f"r{i}" for i in range(20)]
    g = AdmissionController("lifeline")
    for t in range(0, 8):
        _tick(g, float(t), [s for s in specs if s.t_arrival <= t], ranked, tiers, assets, zones)
    assert g.states["hospital"].state == "connected"
    assert g.wait("hospital", 8) <= 3


def test_routine_traffic_keeps_arrival_order() -> None:
    zones, assets = [_zone()], [_core(), _tower("T", 1.0, ["Z"])]
    specs = [_spec("early", t=0), _spec("late", t=0.5)]
    g = AdmissionController("lifeline")
    _tick(g, 1, specs, ["late", "early"], {"early": 5, "late": 4}, assets, zones)
    assert g.states["early"].state == "connected"


def test_uncontrolled_baseline_is_reproducible_per_seed() -> None:
    zones, assets = [_zone(pop=3000)], [_core(), _tower("T", 10.0, ["Z"])]
    specs = [_spec(f"s{i}", t=0) for i in range(6)]

    def run(seed: int) -> list[float]:
        g = AdmissionController("uncontrolled", seed=seed)
        for t in range(12):
            _tick(g, float(t), specs, [], {}, assets, zones)
        return [g.wait(s.id, 12) for s in specs]

    assert run(3) == run(3)
