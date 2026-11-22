"""Interpreters: the only place an LLM is called.

- `GroqInterpreter`: free Groq API, JSON-schema output validated by pydantic.
- `CachedInterpreter`: replays real Groq outputs from `scenarios/cache/` (default; offline).
- `HeuristicInterpreter`: rule-based fallback, low confidence, clearly labelled.
- `RecordingInterpreter`: wraps a live interpreter and writes the cache (`make cache`).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from lifeline.ai.cache import AICache, input_hash
from lifeline.ai.prompts import INTERPRET_SYSTEM, SYNTHESIS_SYSTEM
from lifeline.models import (
    Channel,
    CommNeed,
    Communication,
    EvidenceRef,
    Fact,
    FactStatus,
    Incident,
    InfoGap,
    Interpretation,
    Service,
    SourceRole,
    SynthesisDelta,
    Tri,
    TriageFloor,
)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_OUTPUT_TOKENS = 6000
MIN_OUTPUT_TOKENS = 1500


class LLMError(RuntimeError):
    pass


class Interpreter(Protocol):
    name: str

    def interpret(self, comm: Communication, context: dict[str, Any]) -> Interpretation: ...

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta: ...


# --------------------------------------------------------------------------- Groq


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline `$defs` references and drop titles: flat, compact schemas cost fewer tokens."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    result: dict[str, Any] = walk(schema)
    return result


def _max_output_tokens(*parts: str) -> int:
    """Output budget so that input + output stays inside the per-minute token limit.

    Groq rejects (HTTP 413) any request whose input plus `max_completion_tokens` exceeds the
    account's tokens-per-minute limit, so the budget shrinks as the prompt grows.
    """
    tpm = int(os.getenv("LIFELINE_GROQ_TPM", "8000"))
    est_input = sum(len(p) for p in parts) // 3  # conservative: ~3 characters per token
    return max(MIN_OUTPUT_TOKENS, min(MAX_OUTPUT_TOKENS, tpm - est_input - 200))


class GroqBackend:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from groq import Groq  # imported lazily so the app runs without the key

        self.model = model or os.getenv("LIFELINE_GROQ_MODEL") or DEFAULT_MODEL
        key = api_key or os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is not set")
        self.client = Groq(api_key=key, max_retries=0)
        self.json_schema_ok = True

    def complete(self, system: str, user: str, schema: type[T], name: str, effort: str) -> T:
        import groq

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err = ""
        schema_json = json.dumps(_inline_refs(schema.model_json_schema()))
        for _attempt in range(5):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_completion_tokens": _max_output_tokens(
                    schema_json, *(m["content"] for m in messages)
                ),
            }
            if self.model.startswith("openai/gpt-oss"):
                kwargs["reasoning_effort"] = effort
            if self.json_schema_ok:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": name,
                        "schema": json.loads(schema_json),
                        "strict": False,
                    },
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}
                messages[0]["content"] = system + "\nJSON schema:\n" + schema_json
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except groq.RateLimitError as e:
                wait = float(e.response.headers.get("retry-after", "10") or 10)
                time.sleep(min(wait, 60) + 1)
                continue
            except groq.BadRequestError as e:
                if self.json_schema_ok and "response_format" in str(e):
                    self.json_schema_ok = False
                    continue
                last_err = str(e)
                if "json_validate_failed" in last_err:
                    continue
                raise LLMError(last_err) from e
            except groq.APIError as e:
                last_err = str(e)
                time.sleep(3)
                continue
            text = resp.choices[0].message.content or ""
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                last_err = str(e)[:1500]
                messages = [
                    *messages,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": "Your JSON did not validate. Fix these errors and return "
                        f"the full corrected JSON only:\n{last_err}",
                    },
                ]
        raise LLMError(f"no valid response after retries: {last_err[:300]}")


class GroqInterpreter:
    def __init__(self, backend: GroqBackend | None = None) -> None:
        self.backend = backend or GroqBackend()
        self.name = f"groq:{self.backend.model}"

    def interpret(self, comm: Communication, context: dict[str, Any]) -> Interpretation:
        user = json.dumps(
            {
                "zones": context["zones"],
                "communication": {
                    "id": comm.id,
                    "channel": comm.channel.value,
                    "source_role": comm.source_role.value,
                    "time_min": comm.t,
                    "coordinates": [comm.lat, comm.lon] if comm.lat is not None else None,
                    "zone_from_metadata": context.get("zone_id"),
                    "delivery_status": comm.delivery_status,
                    "text": comm.text,
                },
            },
            ensure_ascii=False,
        )
        out = self.backend.complete(INTERPRET_SYSTEM, user, Interpretation, "interpretation", "low")
        return out.model_copy(update={"comm_id": comm.id, "interpreter": self.name})

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        user = json.dumps(payload, ensure_ascii=False)
        return self.backend.complete(SYNTHESIS_SYSTEM, user, SynthesisDelta, "synthesis", "low")


# --------------------------------------------------------------------------- cache


class CachedInterpreter:
    """Replays recorded LLM outputs; falls back (labelled) on a cache miss."""

    def __init__(self, cache: AICache, fallback: Interpreter) -> None:
        self.cache = cache
        self.fallback = fallback
        self.name = "cached"
        self.misses: list[str] = []

    def _lookup(self, key: str, inputs: Any) -> dict[str, Any] | None:
        """A hit only counts if it was recorded from exactly these inputs."""
        hit = self.cache.get(key)
        if hit is None:
            self.misses.append(f"{key}: not recorded")
            return None
        if hit["hash"] != input_hash(inputs):
            self.misses.append(f"{key}: recorded from different inputs (stale)")
            return None
        return hit

    def interpret(self, comm: Communication, context: dict[str, Any]) -> Interpretation:
        hit = self._lookup(f"interpret:{comm.id}", [comm.model_dump(mode="json"), context])
        if hit is None:
            return self.fallback.interpret(comm, context)
        out = Interpretation.model_validate(hit["output"])
        return out.model_copy(update={"interpreter": f"cached {hit['model']}"})

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        hit = self._lookup(f"synth:{payload['checkpoint']}", payload)
        if hit is None:
            return self.fallback.synthesize(payload)
        return SynthesisDelta.model_validate(hit["output"])


class RecordingInterpreter:
    """Calls a live interpreter and records each output into the cache."""

    def __init__(self, inner: GroqInterpreter, cache: AICache) -> None:
        self.inner = inner
        self.cache = cache
        self.name = inner.name

    def interpret(self, comm: Communication, context: dict[str, Any]) -> Interpretation:
        key = f"interpret:{comm.id}"
        h = input_hash([comm.model_dump(mode="json"), context])
        hit = self.cache.get(key)
        if hit and hit["hash"] == h:
            return Interpretation.model_validate(hit["output"])
        out = self.inner.interpret(comm, context)
        self.cache.put(
            key, hash_=h, model=self.inner.backend.model, output=out.model_dump(mode="json")
        )
        self.cache.save()
        return out

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        key = f"synth:{payload['checkpoint']}"
        h = input_hash(payload)
        hit = self.cache.get(key)
        if hit and hit["hash"] == h:
            return SynthesisDelta.model_validate(hit["output"])
        out = self.inner.synthesize(payload)
        self.cache.put(
            key, hash_=h, model=self.inner.backend.model, output=out.model_dump(mode="json")
        )
        self.cache.save()
        return out


# --------------------------------------------------------------------------- heuristic

_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
}


def _numbers(text: str) -> list[int]:
    low = text.lower()
    nums = [int(n) for n in re.findall(r"\b(\d{1,4})\b", low)]
    nums += [v for w, v in _NUM_WORDS.items() if re.search(rf"\b{w}\b", low)]
    return [n for n in nums if 0 < n < 5000]


class HeuristicInterpreter:
    """Keyword rules. Used when no LLM is available; never pretends to be AI."""

    name = "heuristic"

    def interpret(self, comm: Communication, context: dict[str, Any]) -> Interpretation:
        quote = comm.text[:160]
        life = Tri.YES if comm.triage_floor == TriageFloor.LIFE_SAFETY else Tri.UNKNOWN
        if re.search(r"\b(nobody|no one) (hurt|injured)\b", comm.text.lower()):
            life = Tri.NO
        nums = _numbers(comm.text)
        return Interpretation(
            comm_id=comm.id,
            summary=f"{comm.source_role.value.title()} message via {comm.channel.value}.",
            facts=[
                Fact(
                    claim=comm.text,
                    status=FactStatus.REPORTED,
                    evidence=[EvidenceRef(source_id=comm.id, quote=quote)],
                )
            ],
            needs_mentioned=comm.triage_terms,
            zone_id=context.get("zone_id"),
            people_min=min(nums) if nums else None,
            people_max=max(nums) if nums else None,
            life_threat=life,
            missing_info=["heuristic extraction only; AI interpretation unavailable"],
            confidence=0.3,
            interpreter=self.name,
        )

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        prev = payload["previous_picture"]
        existing = {i["id"]: set(i["comm_ids"]) for i in prev["incidents"]}
        comms = {c["id"]: c for c in payload["all_communications"]}
        new_ids = [c["id"] for c in payload["new_communications"]]
        interp = {c["id"]: c["interpretation"] for c in payload["new_communications"]}
        groups = [set(h["comm_ids"]) for h in payload["correlation_hints"]]
        incidents: list[Incident] = []
        needs: list[CommNeed] = []
        handled: set[str] = set()
        counter = len(existing)
        for cid in new_ids:
            if cid in handled:
                continue
            group = next((g for g in groups if cid in g), {cid})
            handled |= group
            inc_id = next((i for i, ids in existing.items() if ids & group), None)
            if inc_id is None:
                counter += 1
                inc_id = f"INC-{counter}"
            members = sorted(group | existing.get(inc_id, set()))
            c0 = comms[cid]
            life = (
                Tri.YES
                if any(
                    comms[m]["triage_floor"] == TriageFloor.LIFE_SAFETY.value
                    for m in members
                    if m in comms  # members may include telemetry ids from earlier synthesis
                )
                else Tri.UNKNOWN
            )
            incidents.append(
                Incident(
                    id=inc_id,
                    title=c0["text"][:60],
                    zone_id=c0.get("zone_id"),
                    comm_ids=members,
                    life_threat=life,
                    confidence=0.3,
                )
            )
            for m in members:
                if m not in interp:
                    continue
                needs.append(self._need_for(comms[m], interp[m], inc_id))
        gaps = [
            InfoGap(id=f"GAP-{c}", question=f"Location of {c} unknown", why_it_matters="")
            for c in new_ids
            if comms[c].get("zone_id") is None
        ]
        return SynthesisDelta(
            summary=f"Heuristic picture: {len(prev['incidents']) + len(incidents)} incident "
            "groups from keyword rules (AI unavailable).",
            incidents=incidents,
            needs=needs,
            gaps=gaps,
        )

    @staticmethod
    def _need_for(c: dict[str, Any], it: dict[str, Any], inc_id: str) -> CommNeed:
        role, channel, text = c["source_role"], c["channel"], c["text"].lower()
        if role == SourceRole.FACILITY.value:
            cat, svc = "critical_facility", Service.DATA
        elif role == SourceRole.RESPONDER.value:
            cat = "emergency_operation"
            svc = Service.DATA if "data" in text else Service.RESPONDER_RADIO
        elif it.get("life_threat") == Tri.YES.value:
            cat, svc = "life_safety", Service.VOICE
        elif it.get("life_threat") == Tri.NO.value:
            cat, svc = "non_urgent", Service.TEXT
        elif channel == Channel.MESH.value:
            cat, svc = "population", Service.TEXT
        else:
            cat, svc = "population", Service.VOICE
        return CommNeed(
            id=f"NEED-{inc_id}-{cat}",
            who=f"{role} ({c['id']})",
            zone_id=c.get("zone_id"),
            purpose=c["text"][:80],
            service=svc,
            category=cat,  # type: ignore[arg-type]
            incident_ids=[inc_id],
            life_threat=Tri(it.get("life_threat", "unknown")),
            evidence=[EvidenceRef(source_id=c["id"], quote=c["text"][:160])],
            confidence=0.3,
            verification_needed=True,
        )


class FallbackInterpreter:
    """Use the primary interpreter; on any LLM failure fall back (and record why)."""

    def __init__(self, primary: Interpreter, fallback: Interpreter) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.errors: list[str] = []

    def interpret(self, comm: Communication, context: dict[str, Any]) -> Interpretation:
        try:
            return self.primary.interpret(comm, context)
        except Exception as e:  # noqa: BLE001 - any provider failure must not stop the picture
            self.errors.append(f"interpret {comm.id}: {e}")
            return self.fallback.interpret(comm, context)

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        try:
            return self.primary.synthesize(payload)
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"synthesis {payload.get('checkpoint')}: {e}")
            return self.fallback.synthesize(payload)
