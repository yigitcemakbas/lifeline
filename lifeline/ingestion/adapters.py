"""Simulated channel adapters. They normalise scenario payloads; no real network access.

Each session-carrying channel declares which network family it uses, which session kinds it
carries and the default source role. Voice sessions (calls, TETRA transmissions) arrive as
audio and are transcribed by the AI layer; message and data sessions arrive as text.
"""

from __future__ import annotations

from lifeline.ingestion.base import ChannelAdapter
from lifeline.models import (
    Channel,
    Communication,
    NetworkFamily,
    ScenarioEvent,
    Service,
    SessionKind,
    SourceRole,
    TelemetryEvent,
)

AUDIO_KINDS = frozenset({SessionKind.CALL, SessionKind.RADIO})
KIND_SERVICE = {
    SessionKind.CALL: Service.VOICE,
    SessionKind.SMS: Service.TEXT,
    SessionKind.DATA: Service.DATA,
    SessionKind.RADIO: Service.RESPONDER_RADIO,
    SessionKind.MESSAGE: Service.TEXT,
}
DEFAULT_DURATION_MIN = {  # holding time once connected
    SessionKind.CALL: 3.0,
    SessionKind.SMS: 0.0,
    SessionKind.DATA: 0.0,
    SessionKind.RADIO: 1.0,
    SessionKind.MESSAGE: 0.0,
}


class AdapterError(ValueError):
    pass


def _opt_float(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) else None


class SessionAdapter(ChannelAdapter):
    family: NetworkFamily
    kinds: tuple[SessionKind, ...]
    default_role: SourceRole = SourceRole.CITIZEN

    def convert(self, event: ScenarioEvent) -> Communication:
        p = event.payload
        eid = str(p.get("id", ""))
        kind = SessionKind(str(p.get("session", self.kinds[0].value)))
        if kind not in self.kinds:
            raise AdapterError(f"{eid}: {self.channel.value} does not carry {kind.value} sessions")
        audio = str(p["audio"]) if p.get("audio") else None
        text = str(p.get("text", ""))
        if kind in AUDIO_KINDS and not audio:
            raise AdapterError(f"{eid}: {kind.value} sessions arrive as audio ('audio' missing)")
        if kind not in AUDIO_KINDS and not text:
            raise AdapterError(f"{eid}: {kind.value} sessions need 'text'")
        duration = p.get("duration_min", DEFAULT_DURATION_MIN[kind])
        return Communication(
            id=eid,
            t=event.t,
            channel=self.channel,
            adapter=self.label,
            kind=kind,
            family=self.family,
            service=KIND_SERVICE[kind],
            source_id=str(p.get("source_id", "unknown")),
            source_role=SourceRole(str(p.get("source_role", self.default_role.value))),
            lat=_opt_float(p.get("lat")),
            lon=_opt_float(p.get("lon")),
            zone_hint=str(p["zone_hint"]) if p.get("zone_hint") else None,
            cell_id=str(p["cell"]) if p.get("cell") else None,
            audio=audio,
            text="" if audio else text,
            duration_min=_opt_float(duration),
            delivery_status=str(p.get("delivery_status", "intercepted")),
        )


class CellularAdapter(SessionAdapter):
    channel = Channel.CELLULAR
    label = "Cellular 4G/5G (voice calls, SMS)"
    family = NetworkFamily.CELLULAR
    kinds = (SessionKind.CALL, SessionKind.SMS)


class CitizenAppAdapter(SessionAdapter):
    channel = Channel.CITIZEN_APP
    label = "Citizen emergency app (data over cellular)"
    family = NetworkFamily.CELLULAR
    kinds = (SessionKind.DATA,)


class FacilityAdapter(SessionAdapter):
    channel = Channel.FACILITY
    label = "Critical facility data link (over cellular)"
    family = NetworkFamily.CELLULAR
    kinds = (SessionKind.DATA,)
    default_role = SourceRole.FACILITY


class TetraAdapter(SessionAdapter):
    channel = Channel.TETRA
    label = "TETRA responder radio"
    family = NetworkFamily.TETRA
    kinds = (SessionKind.RADIO,)
    default_role = SourceRole.RESPONDER


class MeshAdapter(SessionAdapter):
    channel = Channel.MESH
    label = "Mesh / device-to-device messaging"
    family = NetworkFamily.MESH
    kinds = (SessionKind.MESSAGE,)


class SatelliteAdapter(SessionAdapter):
    channel = Channel.SATELLITE
    label = "Satellite messaging"
    family = NetworkFamily.SATELLITE
    kinds = (SessionKind.MESSAGE,)


class TelemetryAdapter(ChannelAdapter):
    channel = Channel.TELEMETRY
    label = "Network telemetry (NMS)"

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
