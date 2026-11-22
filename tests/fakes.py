"""Deterministic stand-ins for the AI services, so tests never call the network.

`RuleInterpreter` extracts with keyword rules and groups correlated reports into incidents;
`ScriptTranscriber` returns the script each audio file was generated from.
"""

from __future__ import annotations

import re
from typing import Any

from lifeline.ingestion.adapters import KIND_SERVICE
from lifeline.ingestion.pretriage import pretriage
from lifeline.models import (
    Channel,
    Communication,
    EvidenceRef,
    Fact,
    FactStatus,
    Incident,
    InfoGap,
    Interpretation,
    NeedCategory,
    NetworkFamily,
    Scenario,
    SessionKind,
    SourceRole,
    SynthesisDelta,
    Transcript,
    Tri,
    TriageFloor,
)

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "ten": 10, "twenty": 20}


def _numbers(text: str) -> list[int]:
    low = text.lower()
    nums = [int(n) for n in re.findall(r"\b(\d{1,4})\b", low)]
    nums += [v for w, v in _NUM_WORDS.items() if re.search(rf"\b{w}\b", low)]
    return [n for n in nums if 0 < n < 5000]


def rule_category(c: Communication) -> NeedCategory:
    floor = pretriage(c).triage_floor
    if floor == TriageFloor.LIFE_SAFETY:
        return "life_safety"
    if c.source_role == SourceRole.FACILITY:
        return "critical_facility"
    if c.source_role == SourceRole.RESPONDER:
        return "emergency_operation"
    if re.search(r"\b(nobody|no one) (is )?(hurt|injured)\b|claim|refund|ticket", c.text.lower()):
        return "non_urgent"
    return "population"


class RuleInterpreter:
    name = "rules (test double)"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def ready(self, stage: str, payload_chars: int = 0) -> bool:
        return True

    def interpret(
        self, comms: list[Communication], context: dict[str, Any]
    ) -> list[Interpretation]:
        self.calls.append([c.id for c in comms])
        out = []
        for c in comms:
            cat = rule_category(c)
            nums = _numbers(c.text)
            out.append(
                Interpretation(
                    comm_id=c.id,
                    summary=f"{c.source_role.value} {c.kind.value} via {c.channel.value}",
                    category=cat,
                    who=f"{c.source_role.value} {c.id}",
                    purpose=c.text[:60],
                    facts=[
                        Fact(
                            claim=c.text,
                            status=FactStatus.REPORTED,
                            evidence=[EvidenceRef(source_id=c.id, quote=c.text[:120])],
                        )
                    ],
                    people_min=min(nums) if nums else None,
                    people_max=max(nums) if nums else None,
                    life_threat=Tri.YES if cat == "life_safety" else Tri.UNKNOWN,
                    confidence=0.6,
                    interpreter=self.name,
                )
            )
        return out

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        prev = {i["id"]: set(i["comm_ids"]) for i in payload["previous_picture"]["incidents"]}
        new_ids = [c["id"] for c in payload["new_sessions"]]
        cats = {c["id"]: c["interpretation"]["category"] for c in payload["new_sessions"]}
        groups = [set(h["comm_ids"]) for h in payload["correlation_hints"]]
        incidents: list[Incident] = []
        handled: set[str] = set()
        counter = len(prev)
        for cid in new_ids:
            if cid in handled or cats[cid] in ("population", "non_urgent"):
                continue
            group = next((g for g in groups if cid in g), {cid})
            group = {
                g for g in group if g == cid or cats.get(g) not in ("population", "non_urgent")
            }
            handled |= group
            inc_id = next((i for i, ids in prev.items() if ids & group), None)
            if inc_id is None:
                counter += 1
                inc_id = f"INC-{counter}"
            incidents.append(
                Incident(
                    id=inc_id,
                    title=f"Reports {', '.join(sorted(group))}",
                    comm_ids=sorted(group | prev.get(inc_id, set())),
                    confidence=0.5,
                )
            )
        gaps = [
            InfoGap(id=f"GAP-{c['id']}", question=f"Where exactly is {c['id']}?")
            for c in payload["sessions"]
            if c["id"] in new_ids and c["network_zone"] is None
        ]
        return SynthesisDelta(summary="Rule-based picture.", incidents=incidents, gaps=gaps)


class ScriptTranscriber:
    name = "script (test double)"

    def __init__(self, scenario: Scenario) -> None:
        self.scripts = {
            str(e.payload["id"]): str(e.payload["script"])
            for e in scenario.events
            if e.payload.get("script")
        }

    def transcribe(self, comm: Communication) -> Transcript:
        return Transcript(text=self.scripts[comm.id], model=self.name, avg_logprob=-0.2)


class FailingInterpreter(RuleInterpreter):
    """Fails the first `n` requests, then behaves like `RuleInterpreter`."""

    def __init__(self, n: int) -> None:
        super().__init__()
        self.remaining = n

    def interpret(
        self, comms: list[Communication], context: dict[str, Any]
    ) -> list[Interpretation]:
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("provider unavailable")
        return super().interpret(comms, context)


def make_comm(
    cid: str,
    text: str,
    *,
    t: float = 0.0,
    channel: Channel = Channel.CELLULAR,
    role: SourceRole = SourceRole.CITIZEN,
    kind: SessionKind = SessionKind.SMS,
    lat: float | None = None,
    lon: float | None = None,
    floor: TriageFloor = TriageFloor.NONE,
    source: str | None = None,
) -> Communication:
    """A text session for unit tests (the adapters build real ones from scenario events)."""
    family = {
        Channel.TETRA: NetworkFamily.TETRA,
        Channel.MESH: NetworkFamily.MESH,
        Channel.SATELLITE: NetworkFamily.SATELLITE,
    }.get(channel, NetworkFamily.CELLULAR)
    return Communication(
        id=cid,
        t=t,
        channel=channel,
        adapter="test",
        kind=kind,
        family=family,
        service=KIND_SERVICE[kind],
        source_id=source or cid,
        source_role=role,
        lat=lat,
        lon=lon,
        text=text,
        triage_floor=floor,
    )
