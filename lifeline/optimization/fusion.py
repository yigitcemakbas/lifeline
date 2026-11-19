"""Deterministic evidence fusion over the reports behind a need.

Life threat: a Bayesian update in log-odds space. Start from a prior, then each independent
source adds signal x ln(likelihood ratio of its role): a rescuer on scene is more reliable than an
anonymous citizen. The signal comes from the AI's per-message reading (yes +1, no -1) or, if the
AI said nothing, from the rule-based pre-triage (+0.5). Repeated messages from one source count
once (its strongest message), so ten texts from one phone are not ten witnesses.

People affected: Marzullo's algorithm (used by NTP to reconcile clocks) finds the range that
the largest number of reports agree on. Reports outside it are surfaced as outliers. Ranking
still uses the conservative upper bound over all reports, so disagreement never lowers priority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from lifeline.models import (
    CommNeed,
    Communication,
    Interpretation,
    SourceRole,
    Tri,
    TriageFloor,
)

PRIOR = 0.2  # base rate: share of incoming situations that turn out to be life-threatening
ROLE_LR = {
    SourceRole.RESPONDER: 9.0,
    SourceRole.FACILITY: 6.0,
    SourceRole.OPERATOR: 4.0,
    SourceRole.CITIZEN: 3.0,
    SourceRole.SYSTEM: 1.5,
}
PRETRIAGE_SIGNAL = 0.5
THREAT_BANDS = [(0.95, "near-certain"), (0.8, "likely"), (0.4, "plausible"), (0.0, "unverified")]


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


@dataclass
class ThreatEstimate:
    probability: float
    sources: list[tuple[str, float]] = field(default_factory=list)  # (source, log-odds added)
    conflict: bool = False

    @property
    def band(self) -> int:
        """3 = near-certain ... 0 = unconfirmed."""
        return next(3 - i for i, (lo, _) in enumerate(THREAT_BANDS) if self.probability >= lo)

    def describe(self) -> str:
        label = THREAT_BANDS[3 - self.band][1]
        n = len(self.sources)
        txt = f"{self.probability:.0%} ({label}), {n} independent source{'s' if n != 1 else ''}"
        return txt + ("; sources disagree" if self.conflict else "")


def life_threat(
    comm_ids: list[str],
    comms: dict[str, Communication],
    interpretations: dict[str, Interpretation],
) -> ThreatEstimate:
    per_source: dict[str, float] = {}
    labels: dict[str, str] = {}
    signs: set[int] = set()
    for cid in comm_ids:
        c = comms.get(cid)
        if c is None:
            continue
        it = interpretations.get(c.id)
        rule = c.triage_floor == TriageFloor.LIFE_SAFETY
        if it is not None and it.life_threat == Tri.YES:
            signal = 1.0
        elif it is not None and it.life_threat == Tri.NO:
            signal = 0.0 if rule else -1.0  # rule and AI disagree: neutral, flagged below
            if rule:
                signs.update({1, -1})
        else:
            signal = PRETRIAGE_SIGNAL if rule else 0.0
        if signal:
            signs.add(1 if signal > 0 else -1)
        contribution = signal * math.log(ROLE_LR.get(c.source_role, 1.5))
        key = c.source_id
        if key not in per_source or abs(contribution) > abs(per_source[key]):
            per_source[key] = contribution
            labels[key] = f"{c.source_role.value} via {c.channel.value}"
    total = _logit(PRIOR) + sum(per_source.values())
    p = 1 / (1 + math.exp(-total))
    srcs = sorted(
        ((labels[k], v) for k, v in per_source.items() if v), key=lambda x: (-abs(x[1]), x[0])
    )
    return ThreatEstimate(probability=p, sources=srcs, conflict=len(signs) > 1)


@dataclass
class PeopleEstimate:
    lo: int | None
    hi: int | None
    agree: int
    total: int
    outliers: list[tuple[int, int]] = field(default_factory=list)
    upper_bound: int | None = None  # conservative: max over every report

    def describe(self) -> str:
        if self.lo is None:
            return "unknown"
        rng = f"{self.lo}" if self.lo == self.hi else f"{self.lo}-{self.hi}"
        txt = f"{rng} ({self.agree} of {self.total} reports agree)"
        if self.outliers:
            txt += "; outlier " + ", ".join(
                f"{a}" if a == b else f"{a}-{b}" for a, b in self.outliers
            )
        return txt


def marzullo(intervals: list[tuple[int, int]]) -> tuple[int, int, int]:
    """Largest set of mutually overlapping intervals. Returns (lo, hi, count)."""
    events = sorted(
        [(lo, -1) for lo, _ in intervals] + [(hi, +1) for _, hi in intervals]
    )  # starts (-1) sort before ends (+1) at equal offsets, so touching intervals overlap
    best, count, best_lo, best_hi = 0, 0, 0, 0
    for i, (x, kind) in enumerate(events):
        count -= kind
        if count > best:
            best, best_lo = count, x
            best_hi = events[i + 1][0]
    return best_lo, best_hi, best


def people(
    need: CommNeed,
    comm_ids: list[str],
    interpretations: dict[str, Interpretation],
) -> PeopleEstimate:
    ivs: list[tuple[int, int]] = []
    for cid in comm_ids:
        it = interpretations.get(cid)
        if it is None or (it.people_min is None and it.people_max is None):
            continue
        lo = it.people_min if it.people_min is not None else it.people_max
        hi = it.people_max if it.people_max is not None else it.people_min
        assert lo is not None and hi is not None
        ivs.append((min(lo, hi), max(lo, hi)))
    if not ivs:
        lo, hi = need.people_min, need.people_max
        if lo is None and hi is None:
            return PeopleEstimate(None, None, 0, 0)
        lo = lo if lo is not None else hi
        hi = hi if hi is not None else lo
        return PeopleEstimate(lo, hi, 1, 1, upper_bound=hi)
    lo, hi, k = marzullo(ivs)
    outliers = sorted({iv for iv in ivs if iv[1] < lo or iv[0] > hi})
    upper = max([hi for _, hi in ivs] + ([need.people_max] if need.people_max else []))
    return PeopleEstimate(lo, hi, k, len(ivs), outliers, upper)
