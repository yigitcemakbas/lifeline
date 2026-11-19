"""Evolving situational intelligence.

`IntelligenceEngine` keeps the operational picture up to date: it interprets new messages
(in pre-triage order), asks the interpreter for a delta against the previous picture, merges
it, and applies deterministic guards (evidence must be verbatim, "verified" needs an
authoritative or corroborated source, unknown zones are flagged, no communication is dropped).
"""

from __future__ import annotations

import re
from typing import Any

from lifeline.ai.correlator import correlation_hints, resolve_zone
from lifeline.ai.interpreter import Interpreter
from lifeline.ingestion.pretriage import processing_order
from lifeline.models import (
    ChangeEntry,
    CommNeed,
    Communication,
    EvidenceRef,
    Fact,
    FactStatus,
    Interpretation,
    OperationalPicture,
    Scenario,
    SourceRole,
    SynthesisDelta,
    TelemetryEvent,
)

AUTHORITATIVE_ROLES = {SourceRole.RESPONDER, SourceRole.FACILITY, SourceRole.OPERATOR}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().strip(".,;:!?\"'")


class IntelligenceEngine:
    def __init__(self, scenario: Scenario, interpreter: Interpreter) -> None:
        self.scenario = scenario
        self.interpreter = interpreter
        self.picture = OperationalPicture()
        self.interpretations: dict[str, Interpretation] = {}
        self.comm_zone: dict[str, str | None] = {}
        self.checkpoint = 0

    # ------------------------------------------------------------------ public

    def zones_context(self) -> list[dict[str, Any]]:
        return [
            {
                "id": z.id,
                "name": z.name,
                "population": z.population,
                "sites": [s.name for s in self.scenario.sites if s.zone_id == z.id],
            }
            for z in self.scenario.zones
        ]

    def process(
        self,
        t: float,
        new_comms: list[Communication],
        all_comms: list[Communication],
        telemetry: list[TelemetryEvent],
    ) -> list[ChangeEntry]:
        """Run one checkpoint. Returns the change entries for the timeline."""
        self.checkpoint += 1
        zones = self.zones_context()
        for c in processing_order(new_comms):
            zid = resolve_zone(c, self.scenario)
            self.comm_zone[c.id] = zid
            it = self.interpreter.interpret(c, {"zones": zones, "zone_id": zid})
            self.interpretations[c.id] = self._guard_interpretation(it, c)
        if not new_comms and not telemetry:
            return []
        payload = self.build_payload(t, new_comms, all_comms, telemetry)
        delta = self.interpreter.synthesize(payload)
        return self.merge(delta, t, all_comms, telemetry)

    def build_payload(
        self,
        t: float,
        new_comms: list[Communication],
        all_comms: list[Communication],
        telemetry: list[TelemetryEvent],
    ) -> dict[str, Any]:
        p = self.picture
        return {
            "checkpoint": self.checkpoint,
            "time_min": t,
            "zones": self.zones_context(),
            "previous_picture": {
                "summary": p.summary,
                "incidents": [
                    {
                        "id": i.id,
                        "title": i.title,
                        "zone_id": i.zone_id,
                        "comm_ids": i.comm_ids,
                        "people": [i.people_min, i.people_max],
                        "life_threat": i.life_threat.value,
                    }
                    for i in p.incidents
                ],
                "needs": [
                    {
                        "id": n.id,
                        "who": n.who,
                        "zone_id": n.zone_id,
                        "service": n.service.value,
                        "category": n.category,
                        "people": [n.people_min, n.people_max],
                        "status": n.status,
                    }
                    for n in p.needs
                ],
                "dependencies": [{"id": d.id, "title": d.title} for d in p.dependencies],
                "contradictions": [
                    {"id": c.id, "description": c.description} for c in p.contradictions
                ],
                "gaps": [{"id": g.id, "question": g.question} for g in p.gaps],
            },
            "new_communications": [
                {
                    "id": c.id,
                    "interpretation": self.interpretations[c.id].model_dump(
                        mode="json",
                        include={
                            "summary",
                            "facts",
                            "people_min",
                            "people_max",
                            "life_threat",
                            "operations_affected",
                            "missing_info",
                            "zone_id",
                        },
                    ),
                }
                for c in new_comms
            ],
            "all_communications": [
                {
                    "id": c.id,
                    "t": c.t,
                    "channel": c.channel.value,
                    "source_role": c.source_role.value,
                    "zone_id": self.comm_zone.get(c.id),
                    "triage_floor": c.triage_floor.value,
                    "text": c.text,
                }
                for c in all_comms
            ],
            "telemetry": [{"id": e.id, "asset_id": e.asset_id, "note": e.note} for e in telemetry],
            "correlation_hints": correlation_hints(all_comms, self.scenario),
        }

    # ------------------------------------------------------------------ guards + merge

    def _sources(
        self, comms: list[Communication], telemetry: list[TelemetryEvent]
    ) -> dict[str, tuple[str, SourceRole]]:
        out = {c.id: (c.text, c.source_role) for c in comms}
        out.update({e.id: (e.note, SourceRole.SYSTEM) for e in telemetry})
        return out

    def _valid_refs(
        self, refs: list[EvidenceRef], sources: dict[str, tuple[str, SourceRole]], notes: list[str]
    ) -> list[EvidenceRef]:
        good = []
        for r in refs:
            src = sources.get(r.source_id)
            if src and r.quote.strip() and _norm(r.quote) in _norm(src[0]):
                good.append(r)
            else:
                notes.append(f'dropped unverifiable quote from {r.source_id}: "{r.quote[:50]}"')
        return good

    def _guard_fact(
        self, f: Fact, sources: dict[str, tuple[str, SourceRole]], notes: list[str]
    ) -> Fact:
        refs = self._valid_refs(f.evidence, sources, notes)
        status, flag = f.status, None
        if not refs and f.status != FactStatus.INFERRED:
            status, flag = FactStatus.INFERRED, "no valid evidence; downgraded to inferred"
        elif f.status == FactStatus.VERIFIED:
            ids = {r.source_id for r in refs}
            roles = {sources[i][1] for i in ids}
            if len(ids) < 2 and not roles & (AUTHORITATIVE_ROLES | {SourceRole.SYSTEM}):
                status, flag = FactStatus.REPORTED, "single non-authoritative source"
        if flag:
            notes.append(f'{flag}: "{f.claim[:60]}"')
        return f.model_copy(update={"evidence": refs, "status": status, "flagged": flag})

    def _guard_interpretation(self, it: Interpretation, c: Communication) -> Interpretation:
        notes: list[str] = []
        sources = {c.id: (c.text, c.source_role)}
        facts = [self._guard_fact(f, sources, notes) for f in it.facts]
        zone_ids = {z.id for z in self.scenario.zones}
        zone = it.zone_id if it.zone_id in zone_ids else self.comm_zone.get(c.id)
        return it.model_copy(update={"facts": facts, "zone_id": zone, "comm_id": c.id})

    def merge(
        self,
        delta: SynthesisDelta,
        t: float,
        all_comms: list[Communication],
        telemetry: list[TelemetryEvent],
    ) -> list[ChangeEntry]:
        sources = self._sources(all_comms, telemetry)
        notes: list[str] = []
        zone_ids = {z.id for z in self.scenario.zones}
        p = self.picture.model_copy(deep=True)

        def upsert(existing: list[Any], items: list[Any]) -> list[Any]:
            by_id = {x.id: x for x in existing}
            for x in items:
                by_id[x.id] = x
            return list(by_id.values())

        incidents = []
        for inc in delta.incidents:
            facts = [self._guard_fact(f, sources, notes) for f in inc.facts]
            comm_ids = [c for c in inc.comm_ids if c in sources]
            zone = inc.zone_id if inc.zone_id in zone_ids else None
            incidents.append(
                inc.model_copy(update={"facts": facts, "comm_ids": comm_ids, "zone_id": zone})
            )
        p.incidents = upsert(p.incidents, incidents)

        prev_needs = {n.id: n for n in p.needs}
        needs: list[CommNeed] = []
        for n in delta.needs:
            refs = self._valid_refs(n.evidence, sources, notes)
            update: dict[str, Any] = {"evidence": refs}
            if n.zone_id not in zone_ids:
                update["zone_id"] = None
                update["verification_needed"] = True
            if not refs:
                update["verification_needed"] = True
                notes.append(f"{n.id}: no valid evidence, marked for verification")
            update["first_seen_t"] = prev_needs[n.id].first_seen_t if n.id in prev_needs else t
            needs.append(n.model_copy(update=update))
        p.needs = upsert(p.needs, needs)
        for nid in delta.resolved_need_ids:
            for n in p.needs:
                if n.id == nid:
                    n.status = "resolved"
                    n.resolution = "resolved per new evidence"

        deps = []
        for d in delta.dependencies:
            steps = [
                s.model_copy(update={"evidence": self._valid_refs(s.evidence, sources, notes)})
                for s in d.steps
            ]
            deps.append(d.model_copy(update={"steps": steps}))
        p.dependencies = upsert(p.dependencies, deps)
        p.contradictions = upsert(
            p.contradictions,
            [
                c.model_copy(update={"evidence": self._valid_refs(c.evidence, sources, notes)})
                for c in delta.contradictions
            ],
        )
        p.gaps = upsert(p.gaps, delta.gaps)
        closed = set(delta.closed_item_ids)
        p.contradictions = [c for c in p.contradictions if c.id not in closed]
        p.gaps = [g for g in p.gaps if g.id not in closed]

        p.summary = delta.summary or p.summary
        p.version += 1
        p.interpreter = self.interpreter.name
        linked = {c for i in p.incidents for c in i.comm_ids}
        linked |= {r.source_id for n in p.needs for r in n.evidence}
        p.unlinked_comm_ids = [c.id for c in all_comms if c.id not in linked]
        p.guard_notes = (notes + p.guard_notes)[:30]
        self.picture = p

        changes = [
            ch.model_copy(update={"t": t, "caused_by": [c for c in ch.caused_by if c in sources]})
            for ch in delta.changes
        ]
        if not changes:
            changes = [ChangeEntry(t=t, summary="Operational picture updated.")]
        return changes
