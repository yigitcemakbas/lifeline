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
from lifeline.dashboard.theme import CSS, EVENT_CSS
from lifeline.models import Scenario
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios

MODES = ["Cached AI (replay)", "Live AI (Groq)", "Rules only (no AI)"]


def make_interpreter(mode: str, scenario: Scenario) -> tuple[Interpreter, str]:
    if mode == MODES[1]:
        groq = GroqInterpreter()
        return FallbackInterpreter(groq, HeuristicInterpreter()), f"live {groq.backend.model}"
    if mode == MODES[2]:
        return HeuristicInterpreter(), "rules only"
    cache = AICache(scenario.meta.id)
    label = "cached Groq outputs" if cache.available else "no cache, heuristic fallback"
    return CachedInterpreter(cache, HeuristicInterpreter()), label


def get_engine(scenario: Scenario, mode: str) -> Engine:
    key = (scenario.meta.id, mode)
    if st.session_state.get("engine_key") != key:
        interp, label = make_interpreter(mode, scenario)
        st.session_state.engine = Engine(scenario, interp, autopilot=True)
        st.session_state.engine_key = key
        st.session_state.mode_label = label
        st.session_state.playing = False
    engine: Engine = st.session_state.engine
    return engine


def sidebar(scenarios: list[Scenario]) -> tuple[Scenario, str, float]:
    sb = st.sidebar
    sb.markdown("### Scenario")
    labels = {
        s.meta.id: (
            f"{s.meta.name} · {s.meta.hazard_label}"
            if s.meta.category == "demo"
            else f"Validation · {s.meta.name}"
        )
        for s in scenarios
    }
    sid = sb.radio(
        "Preset", list(labels), format_func=labels.__getitem__, label_visibility="collapsed"
    )
    scenario = next(s for s in scenarios if s.meta.id == sid)
    sb.caption(scenario.meta.description)
    sb.markdown("### Intelligence")
    has_key = bool(os.getenv("GROQ_API_KEY"))
    mode = sb.radio("AI mode", MODES, label_visibility="collapsed")
    if mode == MODES[1] and not has_key:
        sb.error("Set GROQ_API_KEY in .env for live AI. Using cached replay.")
        mode = MODES[0]
    sb.markdown("### Simulation")
    speed = sb.slider("Seconds per update", 2.0, 15.0, 6.0, 1.0)
    return scenario, mode, speed


def controls(engine: Engine) -> None:
    c = st.sidebar.columns(4)
    if c[0].button("▶", help="Play", use_container_width=True):
        st.session_state.playing = True
    if c[1].button("⏸", help="Pause", use_container_width=True):
        st.session_state.playing = False
    if c[2].button("⏭", help="Step", use_container_width=True):
        with st.spinner("Processing communications…"):
            engine.step()
    if c[3].button("⟲", help="Reset", use_container_width=True):
        st.session_state.engine_key = None
        st.rerun()
    st.sidebar.caption(
        f"{'▶ playing' if st.session_state.get('playing') else '⏸ paused'} · "
        f"{'scenario feed complete, clock still advances' if engine.events_done else 'feed running'}"
    )
    st.sidebar.markdown("---")
    st.sidebar.caption(
        "All channel adapters and network actions are **simulated**. Lifeline recommends; "
        "operators decide. The AI interprets; deterministic code ranks and allocates."
    )
    err = getattr(engine.interpreter, "errors", None) or getattr(engine.interpreter, "misses", None)
    if err:
        with st.sidebar.expander(f"AI fallbacks ({len(err)})"):
            for e in err[-10:]:
                st.caption(str(e))


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Lifeline", page_icon="📡", layout="wide")
    ui.html(CSS)
    ui.html(EVENT_CSS)
    scenarios = list_scenarios()
    scenario, mode, speed = sidebar(scenarios)
    engine = get_engine(scenario, mode)
    controls(engine)

    ui.header(engine, st.session_state.mode_label)
    ui.event_banner(engine)
    left, right = st.columns([3, 2])
    with left:
        st.pydeck_chart(build_deck(engine), height=440, use_container_width=True)
        ui.html(
            '<div class="ll-legend"><span><i style="background:#2ea078"></i>connected</span>'
            '<span><i style="background:#e3c84b"></i>congested</span>'
            '<span><i style="background:#ff8c28"></i>degraded</span>'
            '<span><i style="background:#e63c3c"></i>cut off</span><span><i style="background:#c8d0da"></i>working site</span>'
            '<span><i style="background:#4c8dff"></i>available resource</span>'
            '<span><i style="border:3px solid #fff;background:transparent"></i>latest event</span></div>'
        )
        ui.overview(engine)
    with right:
        ui.ai_decisions(engine)

    if st.session_state.get("playing"):
        if engine.events_done and not engine.pending:
            st.session_state.playing = False
        else:
            time.sleep(speed)
            engine.step()
            st.rerun()
