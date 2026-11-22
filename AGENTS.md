# AGENTS.md: working on Lifeline

Conventions for contributors and coding agents. Keep this file short.

## Setup and commands
```bash
python3 -m venv .venv && make install   # editable install with dev tools
cp .env.example .env                    # optional: GROQ_API_KEY for live AI
make run                                # Streamlit dashboard
make check                              # ruff lint + format check, mypy, pytest
make cache                              # re-record AI outputs with Groq (needs GROQ_API_KEY)
make evaluate                           # policy evaluation against FCFS and best-effort baselines
make fmt                                # auto-format + autofix
```
Run `make check` before every commit. CI (`.github/workflows/ci.yml`) runs the same targets plus a wheel build.

## Commits, branches, PRs
- **Conventional Commits**: `<type>(<scope>): <summary>`. Types: `feat fix docs refactor test chore ci perf`. Scope: the module (`ingestion`, `ai`, `optimization`, `simulation`, `dashboard`, `scenarios`) or `docs`/`ci`. The template is in `.gitmessage` (`git config commit.template .gitmessage`).
- Small, self-contained commits may go straight to `main`. For larger work, open a branch and a PR using the template; the PR title must be a Conventional Commit (enforced in CI) and the PR is squash-merged.
- Update docs in the same change: README (usage), `docs/architecture.md` (design), `scenarios/README.md` (scenario schema).

## Architecture invariants (do not break)
1. **Event-agnostic core.** Nothing under `lifeline/` may contain hazard-specific logic or words (`earthquake`, `quake`, `seismic`). This is enforced by `tests/test_scenarios.py`. Hazards and outages are described only in `scenarios/*.json`.
2. **Scenarios are data.** You add a scenario by adding a JSON file, not code. Scenario files never contain expected recommendations.
3. **AI vs deterministic separation.** The AI layer (`lifeline/ai/`) interprets and synthesises: facts, needs, incidents, dependencies, contradictions, gaps. It never ranks, computes capacity, or decides feasibility. `lifeline/optimization/` contains no LLM calls.
4. **Evidence or it didn't happen.** Every AI fact carries verbatim evidence references, which are validated against the source text. Facts are labelled `verified`, `reported` or `inferred`. Unknowns stay unknown.
5. **Fail-safe priority.** Rule-based pre-triage sets a floor that AI uncertainty cannot lower. Lower-priority needs and communications are never dropped.
6. **Explicit decision authority.** Interventions are approved either by an operator or, in automatic mode, by the engine in plan order; every decision is logged with its actor and reason. All interventions are simulated: there are no real network integrations, and the UI says so.
7. **Offline and reproducible.** The default AI mode replays `scenarios/cache/`. Recorded outputs must be real model output from `make cache`, never hand-written; an entry is replayed only if its input hash matches, so a changed prompt or scenario falls back visibly instead of replaying stale output.

## Code style
- Python 3.12+, typed, pydantic models in `lifeline/models.py` as the single source of truth.
- Keep modules small and testable. The engine (`lifeline/simulation/engine.py`) has no Streamlit imports.
- Tests live in `tests/`, and the engine is tested with the heuristic interpreter (no network calls in tests).
