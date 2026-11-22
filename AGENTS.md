# AGENTS.md: working on Lifeline

Conventions for contributors and coding agents. Keep this file short.

## Setup and commands
```bash
python3 -m venv .venv && make install   # editable install with dev tools
cp .env.example .env                    # GROQ_API_KEY (free): the dashboard runs the AI live
make run                                # Streamlit dashboard
make check                              # ruff lint + format check, mypy, pytest
make evaluate                           # replay recordings; compare with FCFS and no gateway
make record                             # re-record Whisper + Groq outputs (needs GROQ_API_KEY)
make audio                              # regenerate scenario audio from scripts (macOS + ffmpeg)
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
3. **AI vs deterministic separation.** The AI layer (`lifeline/ai/`) transcribes, interprets and synthesises: what a session is about, facts, incidents, dependencies, contradictions, gaps. It never ranks, admits, computes capacity, or decides feasibility. `lifeline/optimization/` contains no model calls.
4. **Evidence or it didn't happen.** Every AI fact carries verbatim evidence references, which are validated against the source text. Facts are labelled `verified`, `reported` or `inferred`. Unknowns stay unknown.
5. **Fail-safe priority.** Rule-based pre-triage sets a floor that AI uncertainty or AI failure cannot lower; a session awaiting analysis is held above routine traffic. Connected sessions are never pre-empted, routine traffic is never reordered among itself, and nothing is dropped.
6. **Explicit decision authority.** Interventions are approved either by an operator or, in automatic mode, by the engine in plan order; every decision is logged with its actor and reason. All interventions are simulated: there are no real network integrations, and the UI says so.
7. **Live AI, reproducible evaluation.** The dashboard always runs Whisper and the Groq models live; there is no offline or rules-only mode. The evaluation and CI replay `scenarios/cache/`, which must be real output from `make record`, never hand-written; replay is strict (a missing or stale entry is an error).
8. **Every channel end to end.** Each `Channel` has exactly one adapter, appears in the scenarios, and is tested through interception, analysis, ranking and admission (`tests/test_ingestion.py`).

## Code style
- Python 3.12+, typed, pydantic models in `lifeline/models.py` as the single source of truth.
- Keep modules small and testable. The engine (`lifeline/simulation/engine.py`) has no Streamlit imports.
- Tests live in `tests/`; the AI services are replaced by deterministic test doubles (`tests/fakes.py`), so tests make no network calls.
