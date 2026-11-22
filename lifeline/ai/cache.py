"""On-disk record of real LLM outputs, so every scenario replays offline and reproducibly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parents[2] / "scenarios" / "cache"


def input_hash(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class AICache:
    def __init__(self, scenario_id: str, directory: Path = CACHE_DIR) -> None:
        self.path = directory / f"{scenario_id}.json"
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            self.entries = json.loads(self.path.read_text())

    def get(self, key: str) -> dict[str, Any] | None:
        return self.entries.get(key)

    def put(self, key: str, *, hash_: str, model: str, output: dict[str, Any]) -> None:
        self.entries[key] = {"hash": hash_, "model": model, "output": output}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=1, sort_keys=True) + "\n")

    @property
    def available(self) -> bool:
        return bool(self.entries)
