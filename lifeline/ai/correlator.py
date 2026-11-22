"""Deterministic correlation helpers.

These produce *hints* for the AI synthesis step (which reports may describe the same event)
and resolve the zone a session attaches from, using network metadata. The AI decides whether
hinted reports really are the same incident; the guards in `intelligence.py` enforce no double
counting and no dropped reports.
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


def _nearest(lat: float, lon: float, zones: list[Zone], within: float | None) -> Zone | None:
    best: tuple[float, Zone] | None = None
    for z in zones:
        d = distance_m(lat, lon, z.lat, z.lon)
        if (within is None or d <= z.radius_m * within) and (best is None or d < best[0]):
            best = (d, z)
    return best[1] if best else None


def resolve_zone(comm: Communication, scenario: Scenario) -> str | None:
    """Zone the session attaches from: explicit hint, coordinates, the serving cell's coverage,
    or a named site in the text, in that order."""
    zone_ids = {z.id for z in scenario.zones}
    if comm.zone_hint in zone_ids:
        return comm.zone_hint
    if comm.lat is not None and comm.lon is not None:
        z = _nearest(comm.lat, comm.lon, scenario.zones, 1.5)
        if z:
            return z.id
    cell = next((a for a in scenario.assets if a.id == comm.cell_id), None)
    if cell is not None:
        covered = [z for z in scenario.zones if z.id in cell.coverage_zones]
        if len(covered) == 1:
            return covered[0].id
        z = _nearest(cell.lat, cell.lon, covered or scenario.zones, None)
        if z:
            return z.id
    low = comm.text.lower()
    for site in scenario.sites:
        if mentions(low, _site_token(site.name)):
            return site.zone_id
    for z in scenario.zones:
        if z.name.lower() in low:
            return z.id
    return None


def _site_token(name: str) -> str:
    return name.lower().split()[0]


def skeleton(word: str) -> str:
    """Consonant skeleton: first letter, then the consonants, doubled letters collapsed
    ("lale", "lail" -> "ll"). Speech recognition often gets the vowels of unfamiliar names
    wrong but keeps their consonants."""
    w = re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", word.lower()))
    return (w[0] + "".join(ch for ch in w[1:] if ch not in "aeiouyhw")) if w else ""


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def mentions(text: str, token: str) -> bool:
    """Whether the text names the token, allowing speech-recognition misspellings."""
    low = text.lower()
    if token in low:
        return True
    key = skeleton(token)
    if len(token) < 4 or len(key) < 2:
        return False
    return any(
        w[0] == token[0] and skeleton(w) == key and edit_distance(w, token) <= 2
        for w in re.findall(r"[a-z]+", low)
    )


def location_tokens(comm: Communication, scenario: Scenario) -> set[str]:
    low = comm.text.lower()
    toks = {_site_token(s.name) for s in scenario.sites if mentions(low, _site_token(s.name))}
    toks |= {
        m.lower() for m in re.findall(r"\b([A-Z][a-z]+ (?:Lane|Street|Road|Square))\b", comm.text)
    }
    return toks


def correlation_hints(comms: list[Communication], scenario: Scenario) -> list[CorrelationHint]:
    """Union-find over communications that are close in space/time or name the same place."""
    comms = [c for c in comms if c.text]
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
            same_zone = zones[a.id] is None or zones[b.id] is None or zones[a.id] == zones[b.id]
            if reason and not same_zone:
                reason = None
            shared = toks[a.id] & toks[b.id]
            if shared:  # a caller can name a place far from where their phone is
                reason = f"same named place: {', '.join(sorted(shared))}"
            if reason:
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
