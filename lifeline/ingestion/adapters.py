"""Simulated channel adapters. They normalise scenario payloads; no real network access."""

from __future__ import annotations

from lifeline.ingestion.base import ChannelAdapter
from lifeline.models import (
    Channel,
    Communication,
    ScenarioEvent,
    SourceRole,
    TelemetryEvent,
)


def _opt_float(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


class _MessageAdapter(ChannelAdapter):
    default_role: SourceRole = SourceRole.CITIZEN

    def convert(self, event: ScenarioEvent) -> Communication:
        p = event.payload
        return Communication(
            id=str(p["id"]),
            t=event.t,
            channel=self.channel,
            adapter=self.label,
            source_id=str(p.get("source_id", "unknown")),
            source_role=SourceRole(str(p.get("source_role", self.default_role.value))),
            lat=_opt_float(p.get("lat")),
            lon=_opt_float(p.get("lon")),
            zone_hint=str(p["zone_hint"]) if p.get("zone_hint") else None,
            text=str(p["text"]),
            delivery_status=str(p.get("delivery_status", "delivered")),
        )


class CellularAdapter(_MessageAdapter):
    channel = Channel.CELLULAR
    label = "Cellular 4G/5G (emergency calls & SMS)"


class SatelliteAdapter(_MessageAdapter):
    channel = Channel.SATELLITE
    label = "Satellite messaging (LEO/Iridium-class)"


class MeshAdapter(_MessageAdapter):
    channel = Channel.MESH
    label = "Mesh / device-to-device"


class TetraAdapter(_MessageAdapter):
    channel = Channel.TETRA
    label = "TETRA responder radio (transcribed)"
    default_role = SourceRole.RESPONDER


class CitizenAppAdapter(_MessageAdapter):
    channel = Channel.CITIZEN_APP
    label = "Citizen emergency app"


class FacilityAdapter(_MessageAdapter):
    channel = Channel.FACILITY
    label = "Critical facility reporting"
    default_role = SourceRole.FACILITY


class TelemetryAdapter(ChannelAdapter):
    channel = Channel.TELEMETRY
    label = "Network telemetry / NMS"

    def convert(self, event: ScenarioEvent) -> TelemetryEvent:
        p = event.payload
        changes = p.get("changes", {})
        return TelemetryEvent(
            id=str(p["id"]),
            t=event.t,
            asset_id=str(p["asset_id"]),
            changes=dict(changes) if isinstance(changes, dict) else {},
            note=str(p.get("note", "")),
        )
