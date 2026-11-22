"""Analysis of intercepted sessions and the evolving operational picture.

`IntelligenceEngine` runs the AI stages for every intercepted session:

1. transcription of voice sessions (calls, TETRA transmissions);
2. interpretation, one request per update covering every session ready for it (up to
   `MAX_BATCH`), in pre-triage order;
3. synthesis: a delta against the previous operational picture (incidents, dependency chains,
   contradictions, gaps), merged under deterministic guards.

Guards: evidence must be verbatim, "verified" needs an authoritative or corroborated source,
unknown zones are rejected, and no session is dropped. A failed AI request never blocks the
gateway: the session stays queued with its pre-triage floor and is retried on the next update.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lifeline.ai.correlator import correlation_hints, resolve_zone
from lifeline.ai.interpreter import Interpreter, payload_chars
from lifeline.ai.transcriber import Transcriber
from lifeline.ingestion.pretriage import pretriage, processing_order
from lifeline.models import (
    ChangeEntry,
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
MAX_BATCH = 6  # sessions per interpretation request
MAX_SYNTHESIS_NEW = 4  # new sessions per synthesis request; the rest go into the next one
SYNTHESIS_MAX_GAP_MIN = 10.0  # routine-only material waits at most this long for synthesis
ROUTINE = frozenset({"population", "non_urgent"})
INTERPRET_OUTPUT_CHARS = 1350  # room for one interpretation in the output (~450 tokens)
MAX_ERRORS = 20


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower().strip(".,;:!?\"'")


@dataclass
class AnalysisStatus:
    """What the AI stages still owe, and why (shown to the operator)."""

    pending_transcripts: list[str] = field(default_factory=list)
    pending_interpretation: list[str] = field(default_factory=list)
    pending_synthesis: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # most recent first

    def error(self, t: float, msg: str) -> None:
        self.errors = [f"T+{t:.0f}: {msg}", *self.errors][:MAX_ERRORS]

    @property
    def backlog(self) -> int:
        return len(self.pending_transcripts) + len(self.pending_interpretation)


class IntelligenceEngine:
    def __init__(
        self,
        scenario: Scenario,
        interpreter: Interpreter,
        transcriber: Transcriber,
        defer: bool = False,
    ) -> None:
        self.scenario = scenario
        self.interpreter = interpreter
        self.transcriber = transcriber
        self.defer = defer  # live: wait for rate-limit budget instead of blocking the gateway
        self.picture = OperationalPicture()
        self.interpretations: dict[str, Interpretation] = {}
        self.comm_zone: dict[str, str | None] = {}
        self.checkpoint = 0
        self.status = AnalysisStatus()
        self._unsynth_telemetry: list[str] = []
        self._last_synthesis_t = 0.0

    # ------------------------------------------------------------------ context

    def zones_context(self) -> list[dict[str, Any]]:
        return [
            {
                "id": z.id,
                "name": z.name,
                "sites": [s.name for s in self.scenario.sites if s.zone_id == z.id],
            }
            for z in self.scenario.zones
        ]

    def intercept(self, comm: Communication) -> Communication:
        """Register a new session: resolve its network zone and queue it for analysis."""
        self.comm_zone[comm.id] = resolve_zone(comm, self.scenario)
        if comm.awaiting_transcript:
            self.status.pending_transcripts.append(comm.id)
            return comm
        self.status.pending_interpretation.append(comm.id)
        return pretriage(comm)

    # ------------------------------------------------------------------ stages

    def transcribe(self, t: float, comms: dict[str, Communication]) -> list[str]:
        """Transcribe pending voice sessions in place; returns the ids transcribed now."""
        done = []
        for cid in list(self.status.pending_transcripts):
            c = comms[cid]
            try:
                tr = self.transcriber.transcribe(c)
            except Exception as e:  # noqa: BLE001 - provider failure: retry next update
                self.status.error(t, f"transcription of {cid} failed: {e}")
                continue
            comms[cid] = pretriage(c.model_copy(update={"transcript": tr, "text": tr.text}))
            self.status.pending_transcripts.remove(cid)
            self.status.pending_interpretation.append(cid)
            done.append(cid)
        return done

    def interpret(self, t: float, comms: dict[str, Communication]) -> list[str]:
        """Interpret pending sessions in batches; returns the ids interpreted now."""
        pending = processing_order([comms[i] for i in self.status.pending_interpretation])
        done: list[str] = []
        for start in range(0, len(pending), MAX_BATCH):
            batch = pending[start : start + MAX_BATCH]
            size = sum(len(c.text) + INTERPRET_OUTPUT_CHARS for c in batch)
            if self.defer and not self.interpreter.ready("interpret", size):
                break  # sessions stay held with their pre-triage floor; batched next update
            context = {
                "zones": self.zones_context(),
                "zone_ids": {c.id: self.comm_zone.get(c.id) for c in batch},
            }
            try:
                items = self.interpreter.interpret(batch, context)
            except Exception as e:  # noqa: BLE001 - provider failure: retry next update
                self.status.error(t, f"interpretation of {', '.join(c.id for c in batch)}: {e}")
                break
            by_id = {it.comm_id: it for it in items}
            for c in batch:
                it = by_id.get(c.id)
                if it is None:
                    self.status.error(t, f"interpretation returned no result for {c.id}")
                    continue
                self.interpretations[c.id] = self._guard_interpretation(it, c)
                self.status.pending_interpretation.remove(c.id)
                self.status.pending_synthesis.append(c.id)
                done.append(c.id)
        return done

    def synthesize(
        self,
        t: float,
        comms: dict[str, Communication],
        telemetry: list[TelemetryEvent],
        new_telemetry: list[str],
    ) -> list[ChangeEntry]:
        """Update the operational picture when it matters: a new urgent session, a full batch,
        or `SYNTHESIS_MAX_GAP_MIN` since the last update for routine sessions and telemetry.
        In live mode, also only when the synthesis model has budget; otherwise the update is
        deferred, never lost."""
        self._unsynth_telemetry += new_telemetry
        pending = self.status.pending_synthesis
        urgent = any(self.interpretations[i].category not in ROUTINE for i in pending)
        overdue = t - self._last_synthesis_t >= SYNTHESIS_MAX_GAP_MIN
        if not (urgent or len(pending) >= MAX_SYNTHESIS_NEW or (overdue and pending)) and not (
            overdue and self._unsynth_telemetry and self.picture.version
        ):
            return []
        all_comms = [c for c in comms.values() if c.text]
        new = [comms[i] for i in self.status.pending_synthesis[:MAX_SYNTHESIS_NEW]]
        recent = [e for e in telemetry if e.id in set(self._unsynth_telemetry)]
        payload = self.build_payload(t, new, all_comms, recent, self.checkpoint + 1)
        if self.defer and not self.interpreter.ready("synthesis", payload_chars(payload)):
            return []
        try:
            delta = self.interpreter.synthesize(payload)
        except Exception as e:  # noqa: BLE001 - provider failure: retry next update
            self.status.error(t, f"synthesis: {e}")
            return []
        self.checkpoint += 1
        self._last_synthesis_t = t
        self.status.pending_synthesis = self.status.pending_synthesis[len(new) :]
        self._unsynth_telemetry = []
        return self.merge(delta, t, all_comms, telemetry)

    def build_payload(
        self,
        t: float,
        new_comms: list[Communication],
        all_comms: list[Communication],
        telemetry: list[TelemetryEvent],
        checkpoint: int,
    ) -> dict[str, Any]:
        """`telemetry`: the notes not yet seen by synthesis. Only new sessions, the earlier ones
        hinted to match them and new telemetry are sent; everything older is already summarised
        in the previous picture. This keeps each request a constant size as the scenario grows,
        inside the provider's per-minute token limit."""
        p = self.picture
        relevant = {c.id for c in new_comms}
        hints = [
            h for h in correlation_hints(all_comms, self.scenario) if set(h["comm_ids"]) & relevant
        ]
        for h in hints:
            relevant |= set(h["comm_ids"])
        return {
            "checkpoint": checkpoint,
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
                "dependencies": [{"id": d.id, "title": d.title} for d in p.dependencies],
                "contradictions": [
                    {"id": c.id, "description": c.description} for c in p.contradictions
                ],
                "gaps": [{"id": g.id, "question": g.question} for g in p.gaps],
            },
            "new_sessions": [
                {
                    "id": c.id,
                    "interpretation": self.interpretations[c.id].model_dump(
                        mode="json",
                        include={  # facts are left out: the text itself is in sessions
                            "summary",
                            "category",
                            "people_min",
                            "people_max",
                            "life_threat",
                            "operations_affected",
                            "zone_id",
                        },
                    ),
                }
                for c in new_comms
            ],
            "sessions": [self._comm_entry(c) for c in all_comms if c.id in relevant],
            "telemetry": [{"id": e.id, "asset_id": e.asset_id, "note": e.note} for e in telemetry],
            "correlation_hints": hints,
        }

    def _comm_entry(self, c: Communication) -> dict[str, Any]:
        it = self.interpretations.get(c.id)
        return {
            "id": c.id,
            "t": c.t,
            "channel": c.channel.value,
            "source_role": c.source_role.value,
            "network_zone": self.comm_zone.get(c.id),
            "category": it.category if it else None,
            "text": c.text,
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
        zone = it.zone_id if it.zone_id in zone_ids else None
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
