"""Scenario loading. Scenarios are pure data (JSON) validated against `Scenario`."""

from __future__ import annotations

from pathlib import Path

from lifeline.ai.correlator import resolve_zone
from lifeline.ingestion.adapters import AdapterError
from lifeline.ingestion.registry import REGISTRY, ingest
from lifeline.models import AssetKind, Communication, Scenario

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"


class ScenarioError(ValueError):
    pass


def load_scenario(path: Path | str) -> Scenario:
    scenario = Scenario.model_validate_json(Path(path).read_text())
    _check_integrity(scenario)
    return scenario


def list_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    out = [load_scenario(p) for p in sorted(directory.glob("*.json"))]
    return sorted(out, key=lambda s: (s.meta.category != "exercise", s.meta.order, s.meta.id))


def _check_integrity(s: Scenario) -> None:
    zone_ids = {z.id for z in s.zones}
    asset_ids = {a.id for a in s.assets}
    if not any(a.kind == AssetKind.CORE for a in s.assets):
        raise ScenarioError(f"{s.meta.id}: needs at least one core node")
    for a in s.assets:
        for link in a.links:
            if link not in asset_ids:
                raise ScenarioError(f"{s.meta.id}: asset {a.id} links unknown {link}")
    for site in s.sites:
        if site.zone_id not in zone_ids:
            raise ScenarioError(f"{s.meta.id}: site {site.id} in unknown zone {site.zone_id}")
    ids: set[str] = set()
    for e in s.events:
        eid = str(e.payload.get("id", ""))
        if not eid or eid in ids:
            raise ScenarioError(f"{s.meta.id}: event ids must be unique and non-empty ({eid!r})")
        ids.add(eid)
        if e.kind == "telemetry" and e.payload.get("asset_id") not in asset_ids:
            raise ScenarioError(f"{s.meta.id}: telemetry {eid} targets unknown asset")
        if e.channel not in REGISTRY:
            raise ScenarioError(f"{s.meta.id}: no adapter for channel {e.channel.value}")
        if (e.kind == "telemetry") != (e.channel.value == "telemetry"):
            raise ScenarioError(f"{s.meta.id}: {eid} kind does not match channel")
        try:
            item = ingest(e)
        except (AdapterError, KeyError, ValueError) as err:
            raise ScenarioError(f"{s.meta.id}: {err}") from err
        if not isinstance(item, Communication):
            continue
        if item.cell_id is not None and item.cell_id not in asset_ids:
            raise ScenarioError(f"{s.meta.id}: {eid} names unknown cell {item.cell_id}")
        if item.audio is not None and not (SCENARIO_DIR / item.audio).is_file():
            raise ScenarioError(f"{s.meta.id}: {eid} audio missing ({item.audio}); make audio")
        # the gateway always knows where a session attaches from
        if resolve_zone(item.model_copy(update={"text": ""}), s) is None:
            raise ScenarioError(f"{s.meta.id}: {eid} has no cell, coordinates or zone hint")
