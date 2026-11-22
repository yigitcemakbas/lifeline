"""Distance to help: a deterministic tie-breaker inside a priority tier."""

from lifeline.models import (
    CommNeed,
    Communication,
    ConnState,
    EvidenceRef,
    NetworkSnapshot,
    RoadAccess,
    Service,
    Site,
    Zone,
    ZoneState,
)
from lifeline.optimization.prioritizer import access_to_help, prioritize
from tests.fakes import make_comm

# Two zones about 5.5 km apart; the hospital sits in NEAR.
NEAR = Zone(id="NEAR", name="Near", lat=47.53, lon=21.62, radius_m=800, population=1000)
FAR = Zone(id="FAR", name="Far", lat=47.53, lon=21.69, radius_m=800, population=1000)
HOSPITAL = Site(id="H", name="General Hospital", kind="hospital", zone_id="NEAR")


def _comm(cid: str, zone: Zone) -> Communication:
    return make_comm(cid, "person trapped", lat=zone.lat, lon=zone.lon)


def _need(nid: str, cid: str, zone_id: str | None, category: str = "life_safety") -> CommNeed:
    return CommNeed(
        id=nid,
        who=nid,
        zone_id=zone_id,
        purpose="call for rescue",
        service=Service.VOICE,
        category=category,  # type: ignore[arg-type]
        evidence=[EvidenceRef(source_id=cid, quote="trapped")],
    )


def _snapshot(*zones: Zone) -> NetworkSnapshot:
    return NetworkSnapshot(
        zones={
            z.id: ZoneState(
                zone_id=z.id,
                state=ConnState.DEGRADED,
                capacity=1,
                demand=10,
                channels={},
                causes=[],
            )
            for z in zones
        },
        usable_assets=[],
        unusable={},
    )


def test_farther_from_help_ranks_first_within_tier() -> None:
    comms = {"c-near": _comm("c-near", NEAR), "c-far": _comm("c-far", FAR)}
    needs = [_need("N-near", "c-near", "NEAR"), _need("N-far", "c-far", "FAR")]
    ranked = prioritize(needs, _snapshot(NEAR, FAR), comms, None, [NEAR, FAR], [HOSPITAL])
    assert [p.need.id for p in ranked] == ["N-far", "N-near"]
    assert all(p.tier == 1 for p in ranked)
    assert "min from General Hospital" in next(
        c.value for c in ranked[0].criteria if c.criterion == "access to help"
    )


def test_distance_never_crosses_tiers() -> None:
    comms = {"c-near": _comm("c-near", NEAR), "c-far": _comm("c-far", FAR)}
    needs = [_need("N-near", "c-near", "NEAR"), _need("N-far", "c-far", "FAR", "population")]
    ranked = prioritize(needs, _snapshot(NEAR, FAR), comms, None, [NEAR, FAR], [HOSPITAL])
    assert ranked[0].need.id == "N-near"  # life safety stays above a farther population need


def test_blocked_road_means_no_reachable_help() -> None:
    far = FAR.model_copy(update={"road_access": RoadAccess.BLOCKED})
    band, label = access_to_help(
        _need("N", "c", "FAR"), {"c": _comm("c", far)}, {"NEAR": NEAR, "FAR": far}, [HOSPITAL]
    )
    assert band == 3
    assert "blocked" in label


def test_help_in_the_same_zone_is_reachable_even_when_roads_are_blocked() -> None:
    near = NEAR.model_copy(update={"road_access": RoadAccess.BLOCKED})
    need, comms = _need("N", "c", "NEAR"), {"c": _comm("c", near)}
    band, _ = access_to_help(need, comms, {"NEAR": near}, [HOSPITAL])
    assert band == 0


def test_unknown_location_is_treated_as_farthest() -> None:
    need = _need("N", "missing", None)
    band, label = access_to_help(need, {}, {"NEAR": NEAR}, [HOSPITAL])
    assert band == 3
    assert "unknown" in label
