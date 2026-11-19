"""Rule-based pre-triage: an instant safety floor that never waits on the LLM.

It does not rank anything. It sets a minimum urgency (`TriageFloor`) used to order AI
processing and, via the priority policy, guarantees that a possible life-safety message is
never downgraded by AI uncertainty.
"""

from __future__ import annotations

import re

from lifeline.models import Channel, Communication, SourceRole, TriageFloor

LIFE_SAFETY_TERMS = [
    "trapped",
    "under rubble",
    "buried",
    "not breathing",
    "unconscious",
    "bleeding",
    "can't breathe",
    "cannot breathe",
    "drowning",
    "fire",
    "gas leak",
    "collapsed on",
    "ventilator",
    "oxygen",
    "dying",
    "sos",
    "mayday",
    "crushed",
    "pinned",
]
ELEVATED_TERMS = [
    "injured",
    "hurt",
    "collapse",
    "collapsed",
    "evacuat",
    "shelter",
    "hospital",
    "clinic",
    "patients",
    "no signal",
    "cut off",
    "isolated",
    "can't reach",
    "cannot reach",
    "dialysis",
    "insulin",
    "elderly",
    "children",
    "rescue",
]


def _matches(text: str, terms: list[str]) -> list[str]:
    low = text.lower()
    return [t for t in terms if re.search(r"\b" + re.escape(t), low)]


def pretriage(comm: Communication) -> Communication:
    life = _matches(comm.text, LIFE_SAFETY_TERMS)
    elevated = _matches(comm.text, ELEVATED_TERMS)
    floor = TriageFloor.NONE
    if life:
        floor = TriageFloor.LIFE_SAFETY
    elif elevated or comm.source_role in (SourceRole.RESPONDER, SourceRole.FACILITY):
        floor = TriageFloor.ELEVATED
    # A message arriving over a fragile last-resort channel suggests few alternatives remain.
    if floor == TriageFloor.NONE and comm.channel in (Channel.SATELLITE, Channel.MESH):
        floor = TriageFloor.ELEVATED
        elevated = [*elevated, f"fragile channel: {comm.channel.value}"]
    return comm.model_copy(update={"triage_floor": floor, "triage_terms": life + elevated})


FLOOR_ORDER = {TriageFloor.LIFE_SAFETY: 0, TriageFloor.ELEVATED: 1, TriageFloor.NONE: 2}


def processing_order(comms: list[Communication]) -> list[Communication]:
    """Life-safety first, then elevated, then the rest; stable by arrival time."""
    return sorted(comms, key=lambda c: (FLOOR_ORDER[c.triage_floor], c.t))
