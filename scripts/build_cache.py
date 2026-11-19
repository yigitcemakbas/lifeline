"""Record real Groq outputs for every scenario into scenarios/cache/ (resumable).

Usage: make cache            (all scenarios)
       python scripts/build_cache.py quake_dense_city
Needs GROQ_API_KEY in .env. Cached outputs must come from this script, never by hand.
"""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

from lifeline.ai.cache import AICache
from lifeline.ai.interpreter import GroqInterpreter, RecordingInterpreter
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios


def main(selected: list[str]) -> int:
    load_dotenv()
    groq = GroqInterpreter()
    for s in list_scenarios():
        if selected and s.meta.id not in selected:
            continue
        cache = AICache(s.meta.id)
        rec = RecordingInterpreter(groq, cache)
        print(f"== {s.meta.id} ({groq.backend.model})", flush=True)
        t0 = time.time()
        engine = Engine(s, rec)
        while not engine.events_done:
            engine.step()
            print(
                f"  checkpoint {engine.cp_index}/{len(engine.checkpoint_times)} "
                f"T+{engine.t:.0f}  needs={len(engine.priorities)}",
                flush=True,
            )
        print(f"  done in {time.time() - t0:.0f}s, {len(cache.entries)} cached outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
