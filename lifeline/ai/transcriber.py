"""Speech-to-text for intercepted voice sessions (calls and TETRA transmissions).

- `WhisperTranscriber`: live Whisper on Groq.
- `RecordingTranscriber` / `CachedTranscriber`: record and strictly replay transcripts for
  the offline evaluation, keyed by a hash of the audio bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from lifeline.ai.cache import AICache
from lifeline.ai.groq import GroqSpeech, groq_client
from lifeline.ai.interpreter import MissingRecording
from lifeline.models import Communication, Transcript

AUDIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"


def audio_bytes(comm: Communication, root: Path = AUDIO_ROOT) -> bytes:
    if comm.audio is None:
        raise ValueError(f"{comm.id} has no audio")
    return (root / comm.audio).read_bytes()


def audio_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class Transcriber(Protocol):
    name: str

    def transcribe(self, comm: Communication) -> Transcript: ...


class WhisperTranscriber:
    def __init__(self, model: str | None = None) -> None:
        self.speech = GroqSpeech(groq_client(), model)
        self.name = self.speech.model

    def transcribe(self, comm: Communication) -> Transcript:
        assert comm.audio is not None
        return self.speech.transcribe(Path(comm.audio).name, audio_bytes(comm))


class CachedTranscriber:
    def __init__(self, cache: AICache) -> None:
        self.cache = cache
        self.name = "recorded"

    def transcribe(self, comm: Communication) -> Transcript:
        hit = self.cache.get(f"transcribe:{comm.id}")
        if hit is None:
            raise MissingRecording(f"transcribe:{comm.id}: not recorded")
        if hit["hash"] != audio_hash(audio_bytes(comm)):
            raise MissingRecording(f"transcribe:{comm.id}: recorded from different audio (stale)")
        return Transcript.model_validate(hit["output"])


class RecordingTranscriber:
    def __init__(self, inner: WhisperTranscriber, cache: AICache) -> None:
        self.inner = inner
        self.cache = cache
        self.name = inner.name

    def transcribe(self, comm: Communication) -> Transcript:
        key, h = f"transcribe:{comm.id}", audio_hash(audio_bytes(comm))
        hit = self.cache.get(key)
        if hit and hit["hash"] == h:
            return Transcript.model_validate(hit["output"])
        out = self.inner.transcribe(comm)
        self.cache.put(key, hash_=h, model=out.model, output=out.model_dump(mode="json"))
        self.cache.save()
        return out
