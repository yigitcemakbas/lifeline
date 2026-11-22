"""Interpreters: the only place a language model is called.

- `GroqInterpreter`: live Groq models. Interpretation (one request per update, covering every
  session intercepted in it) and synthesis use separate models, so each has its own
  per-minute token budget.
- `RecordingInterpreter`: wraps the live interpreter and records every output
  (`make record`), for the offline evaluation and CI.
- `CachedInterpreter`: strict replay of those recordings. A missing or stale recording is an
  error, never a silent substitute.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal, Protocol

from lifeline.ai.cache import AICache, input_hash
from lifeline.ai.groq import (
    INTERPRET_MODEL,
    SYNTHESIS_MODEL,
    GroqBackend,
    LLMError,
    QuotaExhausted,
    estimate_tokens,
    groq_client,
)
from lifeline.ai.prompts import INTERPRET_SYSTEM, SYNTHESIS_SYSTEM
from lifeline.models import Communication, Interpretation, InterpretationBatch, SynthesisDelta

Stage = Literal["interpret", "synthesis"]
INTERPRET_BASE_TOKENS, INTERPRET_PER_ITEM_TOKENS = 600, 450
SYNTHESIS_OUTPUT_TOKENS = 3500


class MissingRecording(LLMError):
    pass


class Interpreter(Protocol):
    name: str

    def interpret(
        self, comms: list[Communication], context: dict[str, Any]
    ) -> list[Interpretation]:
        """Interpret a batch of intercepted sessions (one result per session, same ids)."""
        ...

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta: ...

    def ready(self, stage: Stage, payload_chars: int = 0) -> bool:
        """Whether a request for this stage can be sent now without waiting on a rate limit."""
        ...


def session_view(c: Communication, zone_id: str | None) -> dict[str, Any]:
    """What the model sees about one session: metadata and the text or transcript."""
    view: dict[str, Any] = {
        "id": c.id,
        "kind": c.kind.value,
        "channel": c.channel.value,
        "source_role": c.source_role.value,
        "time_min": c.t,
        "network_zone": zone_id,
        "coordinates": [c.lat, c.lon] if c.lat is not None else None,
        "text": c.text,
    }
    if c.transcript is not None:
        view["speech_recognition"] = "low confidence" if c.transcript.low_confidence else "ok"
    return view


def batch_inputs(comms: list[Communication], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "zones": context["zones"],
        "sessions": [session_view(c, context["zone_ids"].get(c.id)) for c in comms],
    }


# --------------------------------------------------------------------------- live


class GroqInterpreter:
    """Each stage has a preferred model and a fallback. Every model has its own per-minute and
    per-day quota on Groq; when the preferred model's daily quota runs out the stage moves to
    the next model instead of stalling, and returns to it once the quota resets."""

    def __init__(self, interpret_model: str | None = None, synthesis_model: str | None = None):
        client = groq_client()
        first_i = interpret_model or os.getenv("LIFELINE_INTERPRET_MODEL") or INTERPRET_MODEL
        first_s = synthesis_model or os.getenv("LIFELINE_SYNTHESIS_MODEL") or SYNTHESIS_MODEL
        models = dict.fromkeys([first_i, first_s, INTERPRET_MODEL, SYNTHESIS_MODEL])
        self.backends = {m: GroqBackend(m, client) for m in models}
        self.chains: dict[Stage, list[GroqBackend]] = {
            "interpret": [self.backends[m] for m in dict.fromkeys([first_i, first_s])],
            "synthesis": [self.backends[m] for m in dict.fromkeys([first_s, first_i])],
        }
        self.last_model: dict[Stage, str] = {"interpret": first_i, "synthesis": first_s}
        self.name = f"{first_i} + {first_s}"

    @property
    def waited_s(self) -> float:
        return sum(b.waited_s for b in self.backends.values())

    @property
    def tokens_used(self) -> int:
        return sum(b.tokens_used for b in self.backends.values())

    def _backend(self, stage: Stage) -> GroqBackend | None:
        return next((b for b in self.chains[stage] if b.available), None)

    def ready(self, stage: Stage, payload_chars: int = 0) -> bool:
        """`payload_chars`: size of the request's variable part (for interpretation, the
        sessions' text plus room for their output)."""
        b = self._backend(stage)
        if b is None:
            return False
        if stage == "interpret":
            return b.ready(INTERPRET_BASE_TOKENS * 2 + payload_chars // 3)
        return b.ready(int(payload_chars / 3) + SYNTHESIS_OUTPUT_TOKENS)

    def _complete(self, stage: Stage, system: str, user: str, schema: Any, output: int) -> Any:
        for b in self.chains[stage]:
            if not b.available:
                continue
            try:
                out = b.complete(system, user, schema, stage, output)
            except QuotaExhausted:
                continue
            self.last_model[stage] = b.model
            return out
        raise QuotaExhausted(f"{stage}: daily quota used up on every model")

    def interpret(
        self, comms: list[Communication], context: dict[str, Any]
    ) -> list[Interpretation]:
        user = json.dumps(batch_inputs(comms, context), ensure_ascii=False)
        out = self._complete(
            "interpret",
            INTERPRET_SYSTEM,
            user,
            InterpretationBatch,
            INTERPRET_BASE_TOKENS + INTERPRET_PER_ITEM_TOKENS * len(comms),
        )
        model = self.last_model["interpret"]
        return [it.model_copy(update={"interpreter": model}) for it in out.items]

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        out: SynthesisDelta = self._complete(
            "synthesis", SYNTHESIS_SYSTEM, user, SynthesisDelta, SYNTHESIS_OUTPUT_TOKENS
        )
        return out


def payload_chars(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


# --------------------------------------------------------------------------- recordings


def _interpret_key(comms: list[Communication]) -> str:
    return "interpret:" + "+".join(c.id for c in comms)


def _interpret_hash(comms: list[Communication], context: dict[str, Any]) -> str:
    return input_hash(batch_inputs(comms, context))


class CachedInterpreter:
    """Replays recorded outputs. Raises `MissingRecording` for anything not recorded from
    exactly these inputs (re-record with `make record`)."""

    def __init__(self, cache: AICache) -> None:
        self.cache = cache
        self.name = "recorded"
        if not cache.available:
            raise MissingRecording(f"no recording at {cache.path}; run `make record`")

    def ready(self, stage: Stage, payload_chars: int = 0) -> bool:
        return True

    def _lookup(self, key: str, h: str) -> dict[str, Any]:
        hit = self.cache.get(key)
        if hit is None:
            raise MissingRecording(f"{key}: not recorded")
        if hit["hash"] != h:
            raise MissingRecording(f"{key}: recorded from different inputs (stale)")
        return hit

    def interpret(
        self, comms: list[Communication], context: dict[str, Any]
    ) -> list[Interpretation]:
        hit = self._lookup(_interpret_key(comms), _interpret_hash(comms, context))
        batch = InterpretationBatch.model_validate(hit["output"])
        return [
            it.model_copy(update={"interpreter": f"recorded {hit['model']}"}) for it in batch.items
        ]

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        hit = self._lookup(f"synth:{payload['checkpoint']}", input_hash(payload))
        return SynthesisDelta.model_validate(hit["output"])


class RecordingInterpreter:
    """Calls the live interpreter and records each output into the cache."""

    def __init__(self, inner: GroqInterpreter, cache: AICache) -> None:
        self.inner = inner
        self.cache = cache
        self.name = inner.name

    def ready(self, stage: Stage, payload_chars: int = 0) -> bool:
        return True  # recording runs every stage on every update, waiting for budget

    def interpret(
        self, comms: list[Communication], context: dict[str, Any]
    ) -> list[Interpretation]:
        key, h = _interpret_key(comms), _interpret_hash(comms, context)
        hit = self.cache.get(key)
        if hit and hit["hash"] == h:
            return InterpretationBatch.model_validate(hit["output"]).items
        items = self.inner.interpret(comms, context)
        self.cache.put(
            key,
            hash_=h,
            model=self.inner.last_model["interpret"],
            output=InterpretationBatch(items=items).model_dump(mode="json"),
        )
        self.cache.save()
        return items

    def synthesize(self, payload: dict[str, Any]) -> SynthesisDelta:
        key, h = f"synth:{payload['checkpoint']}", input_hash(payload)
        hit = self.cache.get(key)
        if hit and hit["hash"] == h:
            return SynthesisDelta.model_validate(hit["output"])
        out = self.inner.synthesize(payload)
        self.cache.put(
            key,
            hash_=h,
            model=self.inner.last_model["synthesis"],
            output=out.model_dump(mode="json"),
        )
        self.cache.save()
        return out


__all__ = [
    "CachedInterpreter",
    "GroqInterpreter",
    "Interpreter",
    "LLMError",
    "MissingRecording",
    "RecordingInterpreter",
    "estimate_tokens",
    "payload_chars",
]
