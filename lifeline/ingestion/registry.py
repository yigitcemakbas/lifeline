"""Channel → adapter registry. Real integrations would be registered here."""

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
        SatelliteAdapter(),
        MeshAdapter(),
        TetraAdapter(),
        CitizenAppAdapter(),
        FacilityAdapter(),
        TelemetryAdapter(),
    )
}


def ingest(event: ScenarioEvent) -> Communication | TelemetryEvent:
    return REGISTRY[event.channel].convert(event)
