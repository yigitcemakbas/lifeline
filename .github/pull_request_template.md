## What

## Why

## How tested
- [ ] `make check` passes
- [ ] Ran the dashboard (`make run`) for affected scenarios

## Checklist
- [ ] PR title follows Conventional Commits (`feat(ai): ...`)
- [ ] Docs updated (README / AGENTS.md / docs/architecture.md / scenarios/README.md) where behaviour changed
- [ ] AI layer still produces no ranking, capacity or feasibility; optimizer still has no LLM calls
- [ ] No hazard-specific logic added under `lifeline/`
