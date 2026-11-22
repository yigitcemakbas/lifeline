"""Road routing (Dijkstra), evidence fusion (Bayes + Marzullo), urgency-ordered min-cost flow."""

from lifeline.models import (
    CommNeed,
    Communication,
    Interpretation,
    RoadAccess,
    Service,
    SourceRole,
    Tri,
    TriageFloor,
    Zone,
)
from lifeline.optimization import fusion
from lifeline.optimization.routing import RoadNetwork, _mst
from tests.fakes import make_comm

# ----------------------------------------------------------------------------- routing


def _zone(zid: str, lon: float, access: RoadAccess = RoadAccess.OPEN, pop: int = 1000) -> Zone:
    return Zone(
        id=zid, name=zid, lat=47.5, lon=lon, radius_m=500, population=pop, road_access=access
    )


def test_mst_connects_every_zone_with_n_minus_one_roads() -> None:
    ids = ["A", "B", "C", "D"]
    dist = {(a, b): abs(ord(a) - ord(b)) * 1000.0 for a in ids for b in ids if a < b}
    assert _mst(ids, dist) == {("A", "B"), ("B", "C"), ("C", "D")}


def test_dijkstra_takes_the_open_detour_over_the_damaged_shortcut() -> None:
    # A - B - C in a line; B is limited (slow). With D north of B on open roads, Dijkstra may
    # route A -> D -> C if faster. Either way the time must equal the best path in the graph.
    zones = [
        _zone("A", 21.60),
        _zone("B", 21.62, RoadAccess.LIMITED),
        _zone("C", 21.64),
    ]
    net = RoadNetwork.build(zones)
    route, txt = net.route(47.5, 21.60, "C", "depot")
    assert route is not None
    direct = next(r for r in net.roads if {r.a, r.b} == {"A", "C"})
    via_b = sum(r.minutes for r in net.roads if {r.a, r.b} in ({"A", "B"}, {"B", "C"}))
    assert route.minutes == min(direct.minutes, via_b)
    assert "min from depot" in txt


def test_blocked_zone_is_unreachable_and_named() -> None:
    zones = [_zone("A", 21.60), _zone("B", 21.62), _zone("C", 21.64, RoadAccess.BLOCKED)]
    route, txt = RoadNetwork.build(zones).route(47.5, 21.60, "C", "depot")
    assert route is None
    assert "blocked at C" in txt


# ----------------------------------------------------------------------------- fusion


def _msg(cid: str, source: str, role: SourceRole, floor: TriageFloor) -> Communication:
    return make_comm(cid, "x", role=role, floor=floor, source=source)


def _reading(cid: str, threat: Tri, lo: int | None = None, hi: int | None = None) -> Interpretation:
    return Interpretation(
        comm_id=cid,
        summary="",
        facts=[],
        life_threat=threat,
        people_min=lo,
        people_max=hi,
        confidence=0.8,
    )


def test_independent_sources_raise_probability_but_repeats_do_not() -> None:
    none = TriageFloor.NONE
    comms = {
        "a": _msg("a", "phone-1", SourceRole.CITIZEN, none),
        "b": _msg("b", "phone-1", SourceRole.CITIZEN, none),  # same phone again
        "c": _msg("c", "team-7", SourceRole.RESPONDER, none),
    }
    reads = {k: _reading(k, Tri.YES) for k in comms}
    one = fusion.life_threat(["a"], comms, reads)
    repeated = fusion.life_threat(["a", "b"], comms, reads)
    corroborated = fusion.life_threat(["a", "c"], comms, reads)
    assert repeated.probability == one.probability
    # prior odds 0.25 x LR 3 (citizen) x LR 9 (responder) = 6.75 -> 0.87
    assert abs(corroborated.probability - 6.75 / 7.75) < 1e-9
    assert corroborated.probability > one.probability > fusion.PRIOR
    assert len(corroborated.sources) == 2


def test_ai_saying_no_against_rule_flag_is_neutral_and_flagged() -> None:
    comms = {"a": _msg("a", "p", SourceRole.CITIZEN, TriageFloor.LIFE_SAFETY)}
    est = fusion.life_threat(["a"], comms, {"a": _reading("a", Tri.NO)})
    assert est.conflict
    assert abs(est.probability - fusion.PRIOR) < 1e-9


def test_marzullo_finds_the_range_most_reports_agree_on() -> None:
    assert fusion.marzullo([(3, 3), (5, 8), (6, 7), (20, 20)]) == (6, 7, 2)
    assert fusion.marzullo([(1, 4), (4, 6)]) == (4, 4, 2)  # touching ranges agree


def test_people_estimate_flags_outliers_but_keeps_conservative_upper_bound() -> None:
    need = CommNeed(id="N", who="x", purpose="p", service=Service.VOICE, category="life_safety")
    reads = {
        "a": _reading("a", Tri.YES, 5, 8),
        "b": _reading("b", Tri.YES, 6, 7),
        "c": _reading("c", Tri.YES, 20, 20),
    }
    est = fusion.people(need, ["a", "b", "c"], reads)
    assert (est.lo, est.hi, est.agree, est.total) == (6, 7, 2, 3)
    assert est.outliers == [(20, 20)]
    assert est.upper_bound == 20
