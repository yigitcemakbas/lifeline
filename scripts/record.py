"""Record real Whisper and Groq outputs for every scenario into scenarios/cache/ (resumable).

The evaluation (`make evaluate`) and CI replay these recordings so results are reproducible
without network access; the dashboard always runs the models live.

Usage: make record                         (all scenarios)
       python scripts/record.py quake_dense_city
Needs GROQ_API_KEY in .env. Recordings must come from this script, never written by hand.
"""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

from lifeline.ai.cache import AICache
from lifeline.ai.interpreter import GroqInterpreter, RecordingInterpreter
from lifeline.ai.transcriber import RecordingTranscriber, WhisperTranscriber
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios


def main(selected: list[str]) -> int:
    load_dotenv()
    groq, whisper = GroqInterpreter(), WhisperTranscriber()
    failed = 0
    for s in list_scenarios():
        if selected and s.meta.id not in selected:
            continue
        cache = AICache(s.meta.id)
        print(f"== {s.meta.id} ({whisper.name}; {groq.name})", flush=True)
        t0 = time.time()
        engine = Engine(
            s,
            RecordingInterpreter(groq, cache),
            RecordingTranscriber(whisper, cache),
            auto_approve=True,
        )
        seen = 0
        while engine.t < engine.end_t:
            engine.step()
            errors = engine.intel.status.errors
            for err in reversed(errors[: len(errors) - seen] if len(errors) > seen else []):
                print(f"  error: {err[:300]}", flush=True)
            seen = len(errors)
            if engine.last_event_ids:
                print(f"  T+{engine.t:.0f}  {len(cache.entries)} recorded", flush=True)
        status = engine.intel.status
        if not (status.backlog or status.pending_synthesis):
            stale = cache.prune()
            cache.save()
            if stale:
                print(f"  pruned {len(stale)} entries from earlier inputs", flush=True)
        if status.backlog or status.pending_synthesis:
            failed += 1
            print(f"  INCOMPLETE: {status.errors[:3]}", flush=True)
        print(
            f"  done in {time.time() - t0:.0f}s, {len(cache.entries)} recorded outputs, "
            f"{groq.tokens_used} tokens so far",
            flush=True,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
