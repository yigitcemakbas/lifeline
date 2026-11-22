"""Lifeline operator dashboard (Streamlit). Run with `make run`. Always uses the live models."""

from __future__ import annotations

import os
import time

import streamlit as st
from dotenv import load_dotenv

from lifeline.ai.interpreter import GroqInterpreter
from lifeline.ai.transcriber import WhisperTranscriber
from lifeline.dashboard import components as ui
from lifeline.dashboard.map_view import build_deck
from lifeline.dashboard.theme import CSS
from lifeline.models import Scenario
from lifeline.simulation.engine import Engine
from lifeline.simulation.scenario import list_scenarios

AUTO, OPERATOR = "Automatic", "Operator approval"


def get_engine(scenario: Scenario, auto_approve: bool) -> Engine:
    if st.session_state.get("engine_key") != scenario.meta.id:
        interp, stt = GroqInterpreter(), WhisperTranscriber()
        fresh = Engine(scenario, interp, stt, auto_approve=auto_approve, live=True, compare=True)
        # deep link: ?update=N opens the run at update N, e.g. to share a specific state
        target = st.query_params.get("update", "0")
        for _ in range(int(target) if target.isdigit() else 0):
            fresh.step()
        st.session_state.engine = fresh
        st.session_state.engine_key = scenario.meta.id
        st.session_state.ai_label = f"{stt.name} · {interp.name}"
        st.session_state.playing = False
    engine: Engine = st.session_state.engine
    engine.auto_approve = auto_approve
    return engine


def sidebar(scenarios: list[Scenario]) -> tuple[Scenario, str, float]:
    sb = st.sidebar
    sb.markdown("### Scenario")
    labels = {
        s.meta.id: (s.meta.name if s.meta.category == "exercise" else f"{s.meta.name} (validation)")
        for s in scenarios
    }
    sid = sb.radio("Scenario", list(labels), format_func=labels.__getitem__)
    scenario = next(s for s in scenarios if s.meta.id == sid)
    sb.caption(scenario.meta.description)
    sb.markdown("### Capacity interventions")
    decision_mode = sb.radio(
        "Approval",
        [AUTO, OPERATOR],
        help="Automatic applies the recommended capacity plan in order. Operator approval "
        "waits for you to approve or reject each intervention.",
    )
    sb.markdown("### Playback")
    speed = sb.slider("Seconds per minute of scenario time", 2.0, 30.0, 2.0, 1.0)
    return scenario, decision_mode, speed


def controls(engine: Engine) -> None:
    sb = st.sidebar
    c = sb.columns(2)
    if c[0].button("Play", use_container_width=True, type="primary"):
        st.session_state.playing = True
    if c[1].button("Pause", use_container_width=True):
        st.session_state.playing = False
    c = sb.columns(2)
    if c[0].button("Step", use_container_width=True):
        with st.spinner("Analysing intercepted sessions"):
            engine.step()
    if c[1].button("Reset", use_container_width=True):
        st.session_state.engine_key = None
        st.rerun()
    state = "Playing" if st.session_state.get("playing") else "Paused"
    feed = "all scenario events released" if engine.events_done else "events pending"
    sb.caption(f"{state} · {feed}")
    sb.divider()
    sb.caption(
        "Sessions, audio and network are simulated; Lifeline controls no real network. "
        "Transcription, interpretation and synthesis run live on Groq. The AI reads sessions; "
        "deterministic code ranks, admits and plans."
    )


def missing_key() -> None:
    st.title("Lifeline")
    st.error(
        "Lifeline runs its AI live and needs a Groq API key. Create a free key at "
        "console.groq.com, put `GROQ_API_KEY=...` in `.env` (see `.env.example`) and restart."
    )
    st.stop()


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Lifeline", page_icon=":material/cell_tower:", layout="wide")
    ui.html(CSS)
    if not os.getenv("GROQ_API_KEY"):
        missing_key()
    scenario, decision_mode, speed = sidebar(list_scenarios())
    engine = get_engine(scenario, auto_approve=decision_mode == AUTO)
    controls(engine)

    ui.header(engine, st.session_state.ai_label)
    ui.incident_timeline(engine)
    ui.latest_interception(engine)
    left, right = st.columns([3, 2])
    with left:
        st.pydeck_chart(build_deck(engine), height=420, use_container_width=True)
        ui.map_legend()
        a, b = st.columns(2)
        with a:
            ui.kpis(engine)
        with b:
            ui.comparison(engine)
        ui.gateways(engine)
        ui.operational_picture(engine)
    with right:
        if decision_mode == OPERATOR:
            ui.recommended_plan(engine)
        ui.queue(engine)
        with st.expander("Capacity interventions and decisions"):
            ui.decision_log(engine)
        with st.expander("AI pipeline status"):
            ui.ai_status(engine)

    if st.session_state.get("playing"):
        if engine.finished:
            st.session_state.playing = False
        else:
            time.sleep(speed)
            engine.step()
            st.rerun()
