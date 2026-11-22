"""Lifeline operator dashboard (Streamlit). Run with `make run`."""

from __future__ import annotations

import os
import time

import streamlit as st
from dotenv import load_dotenv

from lifeline.ai.cache import AICache
from lifeline.ai.interpreter import (
    CachedInterpreter,
    FallbackInterpreter,
    GroqInterpreter,
    HeuristicInterpreter,
    Interpreter,
)
from lifeline.dashboard import components as ui
from lifeline.dashboard.map_view import build_deck
from lifeline.dashboard.theme import CSS
from lifeline.models import Scenario
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios

AI_CACHED, AI_LIVE, AI_RULES = "Recorded AI outputs", "Live AI (Groq)", "Rules only (no AI)"
AUTO, OPERATOR = "Automatic", "Operator approval"


def make_interpreter(mode: str, scenario: Scenario) -> tuple[Interpreter, str]:
    if mode == AI_LIVE:
        groq = GroqInterpreter()
        return FallbackInterpreter(groq, HeuristicInterpreter()), f"live {groq.backend.model}"
    if mode == AI_RULES:
        return HeuristicInterpreter(), "rules only"
    cache = AICache(scenario.meta.id)
    label = "recorded Groq outputs" if cache.available else "no recording, rules fallback"
    return CachedInterpreter(cache, HeuristicInterpreter()), label


def get_engine(scenario: Scenario, ai_mode: str, auto_approve: bool) -> Engine:
    key = (scenario.meta.id, ai_mode)
    if st.session_state.get("engine_key") != key:
        interp, label = make_interpreter(ai_mode, scenario)
        fresh = Engine(scenario, interp, auto_approve=auto_approve)
        # deep link: ?update=N opens the run at update N, e.g. to share a specific state
        target = st.query_params.get("update", "0")
        for _ in range(int(target) if target.isdigit() else 0):
            fresh.step()
        st.session_state.engine = fresh
        st.session_state.engine_key = key
        st.session_state.mode_label = label
        st.session_state.playing = False
    engine: Engine = st.session_state.engine
    engine.auto_approve = auto_approve
    return engine


def sidebar(scenarios: list[Scenario]) -> tuple[Scenario, str, str, float]:
    sb = st.sidebar
    sb.markdown("### Scenario")
    labels = {
        s.meta.id: (s.meta.name if s.meta.category == "exercise" else f"{s.meta.name} (validation)")
        for s in scenarios
    }
    sid = sb.radio("Scenario", list(labels), format_func=labels.__getitem__)
    scenario = next(s for s in scenarios if s.meta.id == sid)
    sb.caption(scenario.meta.description)
    sb.markdown("### Interpretation")
    ai_mode = sb.radio("AI mode", [AI_CACHED, AI_LIVE, AI_RULES])
    if ai_mode == AI_LIVE and not os.getenv("GROQ_API_KEY"):
        sb.warning("Set GROQ_API_KEY in .env for live AI. Using recorded outputs.")
        ai_mode = AI_CACHED
    sb.markdown("### Decisions")
    decision_mode = sb.radio(
        "Approval",
        [AUTO, OPERATOR],
        help="Automatic applies the recommended plan in order. Operator approval waits for "
        "you to approve or reject each intervention.",
    )
    sb.markdown("### Playback")
    speed = sb.slider("Seconds per update", 2.0, 30.0, 2.0, 1.0)
    return scenario, ai_mode, decision_mode, speed


def controls(engine: Engine) -> None:
    sb = st.sidebar
    c = sb.columns(2)
    if c[0].button("Play", use_container_width=True, type="primary"):
        st.session_state.playing = True
    if c[1].button("Pause", use_container_width=True):
        st.session_state.playing = False
    c = sb.columns(2)
    if c[0].button("Step", use_container_width=True):
        with st.spinner("Processing communications"):
            engine.step()
    if c[1].button("Reset", use_container_width=True):
        st.session_state.engine_key = None
        st.rerun()
    state = "Playing" if st.session_state.get("playing") else "Paused"
    feed = "all scenario events released" if engine.events_done else "events pending"
    sb.caption(f"{state} · {feed}")
    sb.divider()
    sb.caption(
        "Channel adapters and network actions are simulated; Lifeline controls no real "
        "network. The AI interprets messages; deterministic code ranks, allocates and plans."
    )
    misses = getattr(engine.interpreter, "errors", None) or getattr(
        engine.interpreter, "misses", None
    )
    if misses:
        with sb.expander(f"Rules fallbacks ({len(misses)})"):
            for m in misses[-10:]:
                st.caption(str(m))


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Lifeline", page_icon=":material/cell_tower:", layout="wide")
    ui.html(CSS)
    scenario, ai_mode, decision_mode, speed = sidebar(list_scenarios())
    engine = get_engine(scenario, ai_mode, auto_approve=decision_mode == AUTO)
    controls(engine)

    ui.header(engine, st.session_state.mode_label)
    ui.incident_timeline(engine)
    ui.workflow(engine)
    left, right = st.columns([3, 2])
    with left:
        st.pydeck_chart(build_deck(engine), height=440, use_container_width=True)
        ui.map_legend()
        ui.overview(engine)
    with right:
        if decision_mode == OPERATOR:
            ui.recommended_plan(engine)
        ui.priority_queue(engine)
        with st.expander("Decision log"):
            ui.decision_log(engine)

    if st.session_state.get("playing"):
        if engine.events_done and not engine.pending:
            st.session_state.playing = False
        else:
            time.sleep(speed)
            engine.step()
            st.rerun()
