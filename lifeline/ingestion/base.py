"""Channel adapter interface.

Every source of information (cellular, satellite, mesh, TETRA, citizen apps, facilities,
telemetry) is reached through an adapter. In this prototype every adapter is SIMULATED: it
replays scenario events. A real integration would subclass `ChannelAdapter` and register it.
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
