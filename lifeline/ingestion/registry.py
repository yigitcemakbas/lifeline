"""Channel → adapter registry. Every `Channel` must have exactly one adapter (tested)."""

from __future__ import annotations

from lifeline.ingestion.adapters import (
    CellularAdapter,
    CitizenAppAdapter,
    FacilityAdapter,
    MeshAdapter,
    SatelliteAdapter,
    TelemetryAdapter,
    TetraAdapter,
)
from lifeline.ingestion.base import ChannelAdapter
from lifeline.models import Channel, Communication, ScenarioEvent, TelemetryEvent

REGISTRY: dict[Channel, ChannelAdapter] = {
    a.channel: a
    for a in (
        CellularAdapter(),
        CitizenAppAdapter(),
        FacilityAdapter(),
        TetraAdapter(),
        MeshAdapter(),
        SatelliteAdapter(),
        TelemetryAdapter(),
    )
}


def ingest(event: ScenarioEvent) -> Communication | TelemetryEvent:
    return REGISTRY[event.channel].convert(event)
