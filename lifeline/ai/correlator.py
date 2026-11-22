"""Deterministic correlation helpers.

These produce *hints* for the AI synthesis step (which reports may describe the same event)
and resolve zones from metadata. The AI decides whether hinted reports really are the same
incident; the guards in `intelligence.py` enforce no double counting and no dropped reports.
"""

from __future__ import annotations

import re
from typing import TypedDict

from lifeline.models import Communication, Scenario, Zone
from lifeline.optimization.network import distance_m

SAME_PLACE_M = 300.0
TIME_WINDOW_MIN = 60.0


class CorrelationHint(TypedDict):
    comm_ids: list[str]
    reason: str


def resolve_zone(comm: Communication, scenario: Scenario) -> str | None:
    """Zone from explicit hint, coordinates, or a named site in the text, in that order."""
    zone_ids = {z.id for z in scenario.zones}
    if comm.zone_hint in zone_ids:
        return comm.zone_hint
    if comm.lat is not None and comm.lon is not None:
        best: tuple[float, Zone] | None = None
        for z in scenario.zones:
            d = distance_m(comm.lat, comm.lon, z.lat, z.lon)
            if d <= z.radius_m * 1.5 and (best is None or d < best[0]):
                best = (d, z)
        if best:
            return best[1].id
    low = comm.text.lower()
    for site in scenario.sites:
        if _site_token(site.name) in low:
            return site.zone_id
    for z in scenario.zones:
        if z.name.lower() in low:
            return z.id
    return None


def _site_token(name: str) -> str:
    return name.lower().split()[0]


def location_tokens(comm: Communication, scenario: Scenario) -> set[str]:
    low = comm.text.lower()
    toks = {_site_token(s.name) for s in scenario.sites if _site_token(s.name) in low}
    toks |= {
        m.lower() for m in re.findall(r"\b([A-Z][a-z]+ (?:Lane|Street|Road|Square))\b", comm.text)
    }
    return toks


def correlation_hints(comms: list[Communication], scenario: Scenario) -> list[CorrelationHint]:
    """Union-find over communications that are close in space/time or name the same place."""
    parent = {c.id: c.id for c in comms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    reasons: dict[frozenset[str], str] = {}
    zones = {c.id: resolve_zone(c, scenario) for c in comms}
    toks = {c.id: location_tokens(c, scenario) for c in comms}
    for i, a in enumerate(comms):
        for b in comms[i + 1 :]:
            if abs(a.t - b.t) > TIME_WINDOW_MIN:
                continue
            reason = None
            if (
                a.lat is not None
                and b.lat is not None
                and a.lon is not None
                and b.lon is not None
                and distance_m(a.lat, a.lon, b.lat, b.lon) <= SAME_PLACE_M
            ):
                reason = f"within {SAME_PLACE_M:.0f} m"
            shared = toks[a.id] & toks[b.id]
            if shared:
                reason = f"same named place: {', '.join(sorted(shared))}"
            if reason and (
                zones[a.id] is None or zones[b.id] is None or zones[a.id] == zones[b.id]
            ):
                parent[find(a.id)] = find(b.id)
                reasons[frozenset((a.id, b.id))] = reason
    groups: dict[str, list[str]] = {}
    for c in comms:
        groups.setdefault(find(c.id), []).append(c.id)
    out: list[CorrelationHint] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        why = sorted({r for k, r in reasons.items() if k <= set(members)})
        out.append({"comm_ids": members, "reason": "; ".join(why)})
    return out
