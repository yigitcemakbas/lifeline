"""Channel adapter interface.

Lifeline sits in the path of every access network it protects. Each source of traffic
(cellular calls and SMS, citizen-app and facility data sessions, TETRA radio, mesh and
satellite messaging) and the network telemetry feed reach it through an adapter that
normalises the raw event. Every adapter in this repository replays scenario events; a real
integration subclasses `ChannelAdapter` and registers it in `registry.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lifeline.models import Channel, Communication, ScenarioEvent, TelemetryEvent


class ChannelAdapter(ABC):
    channel: Channel
    label: str
    simulated: bool = True

    @abstractmethod
    def convert(self, event: ScenarioEvent) -> Communication | TelemetryEvent:
        """Normalise a raw channel event into Lifeline's common model."""
