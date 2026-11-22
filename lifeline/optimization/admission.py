"""The admission gateway: Lifeline in the path between callers and the radio network.

Every new session (call, SMS, data session, radio transmission, message) is intercepted and
held. Once a minute (one tick) the gateway decides which held sessions the network carries now,
using the capacity network in `flow.py`:

1. Sessions whose holding time has elapsed complete and free their capacity.
2. Connected sessions are re-routed first and are never pre-empted. If the network can no
   longer carry one (its tower lost power, say) the session drops and is held again.
3. Background traffic that is already connected keeps its capacity. Background traffic is the
   zone's ordinary demand (population x demand per person), modelled as a fluid: connected
   background calls end with a mean holding time of `HOLDING_MIN`, new background attempts
   arrive at the matching rate, and attempts that wait longer than `PATIENCE_MIN` give up.
4. Held sessions and waiting background attempts are admitted in the order the policy sets.

Policies (the same network, the same traffic; only step 4 differs):

* `lifeline`: urgent sessions (tiers 1-3) first, in urgency order. When a life-safety or
  facility session does not fit yet, the capacity it could already use is held back for it
  rather than given to smaller, lower-ranked traffic, so a large session (a hospital's data
  link) is not starved. Routine and non-urgent sessions keep their place in arrival order with
  the background traffic: Lifeline never reorders ordinary traffic among itself.
* `arrival`: a gateway without content analysis, first come first served.
* `uncontrolled`: no gateway, how a congested network behaves today. Blocked callers retry;
  capacity that frees up goes to whichever attempt gets in first, so every waiting session and
  background attempt in a zone has the same chance. Seeded, so results are reproducible.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

from lifeline.models import Asset, NetworkFamily, NetworkSnapshot, SessionSpec, Zone
from lifeline.optimization.flow import CapacityNetwork, Demand

Policy = Literal["lifeline", "arrival", "uncontrolled"]
POLICIES: tuple[Policy, ...] = ("lifeline", "arrival", "uncontrolled")
HOLDING_MIN = 3.0  # mean holding time of background calls
PATIENCE_MIN = 5.0  # background attempts give up after waiting this long
ROUTINE_TIER = 4


@dataclass
class SessionState:
    state: Literal["held", "connected", "completed"] = "held"
    connected_t: float | None = None  # latest connection
    first_connected_t: float | None = None
    completed_t: float | None = None
    via: str = ""
    drops: int = 0


@dataclass
class Chunk:
    """Background attempts that arrived in one tick and are still waiting."""

    zone_id: str
    t: float
    units: float


@dataclass
class AdmissionEvent:
    session_id: str
    kind: Literal["connected", "dropped", "completed"]
    t: float
    via: str = ""
    wait: float = 0.0


Item = tuple[Literal["session"], str] | tuple[Literal["chunk"], Chunk]


class AdmissionController:
    def __init__(
        self,
        policy: Policy = "lifeline",
        seed: int = 0,
        holding_min: float = HOLDING_MIN,
        patience_min: float = PATIENCE_MIN,
    ) -> None:
        self.policy = policy
        self.rng = random.Random(seed)
        self.holding_min = holding_min
        self.patience_min = patience_min
        self.specs: dict[str, SessionSpec] = {}
        self.states: dict[str, SessionState] = {}
        self.bg_connected: dict[str, float] = {}
        self.chunks: list[Chunk] = []
        self.loads: dict[str, tuple[float, float]] = {}  # asset id -> (used, capacity)
        self.t = 0.0
        self.started = False

    # ------------------------------------------------------------------ queries

    def held(self) -> list[str]:
        return [s for s, st in self.states.items() if st.state == "held"]

    def wait(self, sid: str, now: float) -> float:
        """Minutes from arrival to first connection (or until `now` if still waiting)."""
        st, sp = self.states[sid], self.specs[sid]
        end = st.first_connected_t if st.first_connected_t is not None else now
        return max(0.0, end - sp.t_arrival)

    def queue(self, ranked: list[str], tiers: dict[str, int]) -> list[str]:
        """Held sessions in the order this gateway admits them (background traffic omitted)."""
        return [it[1] for it in self._order(ranked, tiers) if it[0] == "session"]

    def background_waiting(self, zone_id: str) -> float:
        return sum(c.units for c in self.chunks if c.zone_id == zone_id)

    # ------------------------------------------------------------------ tick

    def tick(
        self,
        t: float,
        specs: list[SessionSpec],
        ranked: list[str],
        tiers: dict[str, int],
        assets: list[Asset],
        zones: list[Zone],
        snap: NetworkSnapshot,
        demand_per_person: float,
    ) -> list[AdmissionEvent]:
        dt = t - self.t if self.started else 0.0
        self.t = t
        for sp in specs:
            if sp.id not in self.states:
                self.specs[sp.id] = sp
                self.states[sp.id] = SessionState()
        events = self._complete(t)
        self._background(t, dt, zones, demand_per_person)
        net = CapacityNetwork(assets, zones, snap)
        events += self._keep_connected(t, net)
        if self.policy == "uncontrolled":
            events += self._admit_contended(t, net)
        else:
            for item in self._order(ranked, tiers):
                if item[0] == "session":
                    ev = self._try_session(item[1], t, net)
                    if ev:
                        events.append(ev)
                    elif self.policy == "lifeline" and tiers.get(item[1], ROUTINE_TIER) < 3:
                        self._reserve(item[1], net, snap)
                else:
                    self._serve_chunk(item[1], item[1].units, net)
        self.chunks = [c for c in self.chunks if c.units > 1e-6]
        self.loads = net.loads()
        return events

    def _complete(self, t: float) -> list[AdmissionEvent]:
        out = []
        for sid, st in self.states.items():
            dur = self.specs[sid].duration_min
            if st.state != "connected" or dur is None or st.connected_t is None:
                continue
            if t > st.connected_t and t >= st.connected_t + dur:
                st.state, st.completed_t = "completed", t
                out.append(AdmissionEvent(sid, "completed", t, st.via))
        return out

    def _background(self, t: float, dt: float, zones: list[Zone], dpp: float) -> None:
        demand = {z.id: z.population * dpp for z in zones if z.population * dpp > 0}
        if not self.started:
            self.started = True
            self.bg_connected = dict.fromkeys(demand, 0.0)
            self.chunks = [Chunk(z, t, d) for z, d in sorted(demand.items())]
            return
        if dt <= 0:
            return
        churn = 1 - math.exp(-dt / self.holding_min)
        for z, d in sorted(demand.items()):
            self.bg_connected[z] *= 1 - churn
            self.chunks.append(Chunk(z, t, d * churn))  # steady state: arrivals = departures
        self.chunks = [c for c in self.chunks if t - c.t <= self.patience_min]

    def _keep_connected(self, t: float, net: CapacityNetwork) -> list[AdmissionEvent]:
        out = []
        connected = sorted(
            (s for s, st in self.states.items() if st.state == "connected"),
            key=lambda s: (self.states[s].connected_t or 0.0, s),
        )
        for sid in connected:
            sp, st = self.specs[sid], self.states[sid]
            if sp.zone_id and net.route(Demand(sid, sp.zone_id, sp.family, sp.demand)) > 0:
                st.via = net.via.get(sid, st.via)
                continue
            st.state, st.drops = "held", st.drops + 1
            out.append(AdmissionEvent(sid, "dropped", t, st.via))
        for z in sorted(self.bg_connected):
            if self.bg_connected[z] > 0:
                self.bg_connected[z] = net.route(
                    Demand(f"bg:{z}", z, NetworkFamily.CELLULAR, self.bg_connected[z], whole=False)
                )
        return out

    def _order(self, ranked: list[str], tiers: dict[str, int]) -> list[Item]:
        held = set(self.held())
        chunks = sorted(self.chunks, key=lambda c: (c.t, c.zone_id))

        def by_arrival(sessions: list[str]) -> list[Item]:
            items: list[tuple[float, int, str, Item]] = [
                (self.specs[s].t_arrival, 1, s, ("session", s)) for s in sessions
            ]
            items += [(c.t, 0, c.zone_id, ("chunk", c)) for c in chunks]
            return [it for *_, it in sorted(items, key=lambda x: x[:3])]

        if self.policy == "arrival":
            return by_arrival(sorted(held))
        # urgent sessions in urgency order, then routine traffic first come first served
        urgent = [s for s in ranked if s in held and tiers.get(s, ROUTINE_TIER) < ROUTINE_TIER]
        out: list[Item] = [("session", s) for s in urgent]
        return out + by_arrival(sorted(held - set(urgent)))

    def _reserve(self, sid: str, net: CapacityNetwork, snap: NetworkSnapshot) -> None:
        """Hold back what an urgent session at the head of the queue could already get, so
        smaller, lower-ranked traffic cannot take it; it fits once enough has freed up. Only
        when the zone could ever carry it, so an impossible session never blocks a zone."""
        sp = self.specs[sid]
        zs = snap.zones.get(sp.zone_id or "")
        if zs is None:
            return
        ceiling = {
            NetworkFamily.CELLULAR: zs.capacity,
            NetworkFamily.TETRA: zs.channels.get("tetra", 0.0),
            NetworkFamily.MESH: zs.channels.get("mesh", 0.0),
        }.get(sp.family, float("inf"))
        if sp.demand <= ceiling:
            net.route(Demand(f"hold:{sid}", zs.zone_id, sp.family, sp.demand, whole=False))

    def _try_session(self, sid: str, t: float, net: CapacityNetwork) -> AdmissionEvent | None:
        sp, st = self.specs[sid], self.states[sid]
        if sp.zone_id is None or net.route(Demand(sid, sp.zone_id, sp.family, sp.demand)) <= 0:
            return None
        st.state, st.connected_t, st.via = "connected", t, net.via.get(sid, "")
        if st.first_connected_t is None:
            st.first_connected_t = t
        return AdmissionEvent(sid, "connected", t, st.via, self.wait(sid, t))

    def _serve_chunk(self, c: Chunk, units: float, net: CapacityNetwork) -> None:
        got = net.route(
            Demand(f"bgw:{c.zone_id}:{c.t:g}", c.zone_id, NetworkFamily.CELLULAR, units, False)
        )
        c.units -= got
        self.bg_connected[c.zone_id] = self.bg_connected.get(c.zone_id, 0.0) + got

    def _admit_contended(self, t: float, net: CapacityNetwork) -> list[AdmissionEvent]:
        """No gateway: in each zone and network, every waiting attempt has the same chance."""
        groups: dict[tuple[str, NetworkFamily], tuple[list[str], list[Chunk]]] = {}
        for s in sorted(self.held()):
            sp = self.specs[s]
            if sp.zone_id:
                groups.setdefault((sp.zone_id, sp.family), ([], []))[0].append(s)
        for c in self.chunks:
            groups.setdefault((c.zone_id, NetworkFamily.CELLULAR), ([], []))[1].append(c)
        out = []
        for (zid, fam), (sessions, chunks) in sorted(groups.items()):
            total = sum(self.specs[s].demand for s in sessions) + sum(c.units for c in chunks)
            free = net.available(zid, fam)
            share = min(1.0, free / total) if total > 0 else 1.0
            self.rng.shuffle(sessions)
            for s in sessions:
                if self.rng.random() < share:
                    ev = self._try_session(s, t, net)
                    if ev:
                        out.append(ev)
            for c in chunks:
                self._serve_chunk(c, c.units * share, net)
        return out
