"""Groq API access: structured chat completions and speech-to-text, inside the account's limits.

Every request is planned against a client-side token bucket that mirrors Groq's per-minute
token limit (synchronised from the `x-ratelimit-*` response headers), so requests wait for
budget instead of failing with HTTP 429. Output budgets are sized per request: Groq rejects
(HTTP 413) any request whose input plus `max_completion_tokens` exceeds the per-minute limit.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from lifeline.models import Transcript

T = TypeVar("T", bound=BaseModel)

INTERPRET_MODEL = "openai/gpt-oss-20b"
SYNTHESIS_MODEL = "openai/gpt-oss-120b"
STT_MODEL = "whisper-large-v3-turbo"
DEFAULT_TPM = 8000
CHARS_PER_TOKEN = 3.5  # conservative: JSON-heavy prompts measure about 3.9 characters per token
MAX_ATTEMPTS = 5


class LLMError(RuntimeError):
    pass


class QuotaExhausted(LLMError):
    """The model's longer-window quota (e.g. tokens per day) is used up for a while."""


QUOTA_WAIT_S = 60.0  # a rate-limit wait longer than this means a daily quota, not per-minute


def parse_duration(s: str) -> float:
    """Groq durations look like '577ms', '7.66s' or '1m26.4s'; retry-after is plain seconds."""
    s = (s or "").strip()
    if re.fullmatch(r"[\d.]+", s):
        return float(s)
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(ms|h|m|s)", s or ""):
        total += float(value) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return total


class TokenBucket:
    """Client-side mirror of a tokens-per-minute limit with continuous refill."""

    def __init__(self, limit: int = DEFAULT_TPM, clock: Any = time.monotonic) -> None:
        self.limit = float(limit)
        self.clock = clock
        self.tokens = self.limit
        self.stamp = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.limit, self.tokens + (now - self.stamp) * self.limit / 60.0)
        self.stamp = now

    def wait_for(self, need: float) -> float:
        """Seconds until `need` tokens are available (0 if available now)."""
        self._refill()
        need = min(need, self.limit)
        return 0.0 if self.tokens >= need else (need - self.tokens) * 60.0 / self.limit

    def spend(self, n: float) -> None:
        self._refill()
        self.tokens -= n

    def sync(self, headers: Any) -> None:
        """Adopt the server's view of the remaining budget."""
        limit, remaining = (
            headers.get("x-ratelimit-limit-tokens"),
            headers.get("x-ratelimit-remaining-tokens"),
        )
        if limit and remaining:
            self.limit = float(limit)
            self.tokens = float(remaining)
            self.stamp = self.clock()


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline `$defs` and drop titles and defaults: compact schemas cost fewer tokens."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    result: dict[str, Any] = walk(schema)
    return result


def estimate_tokens(*parts: str) -> int:
    return int(sum(len(p) for p in parts) / CHARS_PER_TOKEN)


class GroqBackend:
    """One chat model behind its own token bucket."""

    def __init__(self, model: str, client: Any, tpm: int | None = None) -> None:
        self.model = model
        self.client = client
        self.bucket = TokenBucket(tpm or int(os.getenv("LIFELINE_GROQ_TPM", DEFAULT_TPM)))
        self.json_schema_ok = True
        self.waited_s = 0.0  # total time spent waiting for rate-limit budget
        self.tokens_used = 0
        self.requests = 0
        self.exhausted_until = 0.0  # monotonic time until which the daily quota is used up

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.exhausted_until

    def ready(self, expected_tokens: int) -> bool:
        return self.bucket.wait_for(expected_tokens) == 0.0

    def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self.waited_s += seconds
            time.sleep(seconds)

    def complete(self, system: str, user: str, schema: type[T], name: str, output_tokens: int) -> T:
        import groq

        schema_json = json.dumps(inline_refs(schema.model_json_schema()), separators=(",", ":"))
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        budget = output_tokens
        last_err = ""
        for _ in range(MAX_ATTEMPTS):
            # A truncated answer is retried with a larger budget, up to what the per-minute
            # limit allows; constrained decoding occasionally loops, so a retry at the same
            # budget usually succeeds.
            est_input = estimate_tokens(schema_json, *(m["content"] for m in messages))
            ceiling = int(self.bucket.limit) - est_input - 200
            budget = max(256, min(budget, ceiling))
            self._wait(self.bucket.wait_for(est_input + budget))
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "temperature": 0,
                "max_completion_tokens": budget,
            }
            if self.model.startswith("openai/gpt-oss"):
                kwargs["reasoning_effort"] = "low"
            if self.json_schema_ok:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": json.loads(schema_json)},
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}
                messages[0]["content"] = system + "\nJSON schema:\n" + schema_json
            try:
                raw = self.client.chat.completions.with_raw_response.create(**kwargs)
            except groq.RateLimitError as e:
                self.bucket.sync(e.response.headers)
                wait = parse_duration(e.response.headers.get("retry-after", "")) or 5.0
                last_err = str(e)[:300]
                if wait > QUOTA_WAIT_S:
                    self.exhausted_until = time.monotonic() + wait
                    raise QuotaExhausted(
                        f"{self.model}: quota used up for {wait / 60:.0f} min"
                    ) from e
                self._wait(wait)
                continue
            except groq.BadRequestError as e:
                last_err = str(e)
                if self.json_schema_ok and "response_format" in last_err:
                    self.json_schema_ok = False
                    continue
                if "json_validate_failed" in last_err:
                    if "max completion tokens" in last_err:
                        last_err = "output truncated"
                    budget = int(budget * 1.5)
                    continue
                raise LLMError(last_err) from e
            except groq.APIError as e:
                last_err = str(e)
                self._wait(3)
                continue
            self.bucket.sync(raw.headers)
            resp = raw.parse()
            self.requests += 1
            self.tokens_used += resp.usage.total_tokens if resp.usage else 0
            choice = resp.choices[0]
            text = choice.message.content or ""
            if choice.finish_reason == "length":
                last_err = "output truncated"
                budget = int(budget * 1.6)
                continue
            try:
                return schema.model_validate_json(text)
            except ValidationError as e:
                last_err = str(e)[:1500]
                messages = [
                    *messages[:2],
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": "Your JSON did not validate. Fix these errors and return "
                        f"the full corrected JSON only:\n{last_err}",
                    },
                ]
        raise LLMError(
            f"{self.model}: no valid response after {MAX_ATTEMPTS} attempts: {last_err[:300]}"
        )


class GroqSpeech:
    """Speech-to-text with Whisper on Groq."""

    def __init__(self, client: Any, model: str | None = None) -> None:
        self.client = client
        self.model = model or os.getenv("LIFELINE_STT_MODEL") or STT_MODEL

    def transcribe(self, filename: str, audio: bytes) -> Transcript:
        import groq

        last_err = ""
        for _ in range(MAX_ATTEMPTS):
            try:
                raw = self.client.audio.transcriptions.with_raw_response.create(
                    file=(filename, audio),
                    model=self.model,
                    response_format="verbose_json",
                    language="en",
                    temperature=0,
                )
            except groq.RateLimitError as e:
                wait = parse_duration(e.response.headers.get("retry-after", "")) or 5.0
                if wait > QUOTA_WAIT_S:
                    raise QuotaExhausted(
                        f"{self.model}: quota used up for {wait / 60:.0f} min"
                    ) from e
                time.sleep(wait)
                continue
            except groq.APIError as e:
                last_err = str(e)
                time.sleep(2)
                continue
            data = json.loads(raw.text())
            segs = data.get("segments") or []
            n = max(1, len(segs))
            return Transcript(
                text=str(data.get("text", "")).strip(),
                model=self.model,
                avg_logprob=round(sum(s.get("avg_logprob", 0.0) for s in segs) / n, 3)
                if segs
                else None,
                no_speech_prob=round(
                    max((s.get("no_speech_prob", 0.0) for s in segs), default=0), 3
                )
                if segs
                else None,
                duration_s=data.get("duration"),
            )
        raise LLMError(f"{self.model}: transcription failed: {last_err[:300]}")


def groq_client(api_key: str | None = None) -> Any:
    from groq import Groq  # imported lazily so the package imports without the key

    key = api_key or os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set")
    return Groq(api_key=key, max_retries=0)
