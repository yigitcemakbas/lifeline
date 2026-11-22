"""Domain models: the single source of truth for every layer.

Inputs (scenario + ingestion), AI outputs (interpretation + operational picture) and
deterministic decisions (network state, priorities, interventions) live here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- enums


class Channel(StrEnum):
    CELLULAR = "cellular"
    SATELLITE = "satellite"
    MESH = "mesh"
    TETRA = "tetra"
    CITIZEN_APP = "citizen_app"
    FACILITY = "facility"
    TELEMETRY = "telemetry"


class SourceRole(StrEnum):
    CITIZEN = "citizen"
    RESPONDER = "responder"
    FACILITY = "facility"
    OPERATOR = "operator"
    SYSTEM = "system"


class AssetKind(StrEnum):
    CORE = "core"
    CELL_TOWER = "cell_tower"
    BACKHAUL_LINK = "backhaul_link"
    SAT_TERMINAL = "sat_terminal"
    MESH_NODE = "mesh_node"
    MOBILE_BS = "mobile_base_station"
    TETRA_SITE = "tetra_site"


class AssetStatus(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    STANDBY = "standby"  # available but not active (sat terminals, mobile BS)


class RoadAccess(StrEnum):
    OPEN = "open"
    LIMITED = "limited"
    BLOCKED = "blocked"


class FactStatus(StrEnum):
    VERIFIED = "verified"
    REPORTED = "reported"
    INFERRED = "inferred"


class Tri(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class Service(StrEnum):
    RESPONDER_RADIO = "responder_radio"
    VOICE = "voice"
    DATA = "data"
    TEXT = "text"


class ConnState(StrEnum):
    CONNECTED = "connected"
    CONGESTED = "congested"
    DEGRADED = "degraded"
    ISOLATED = "isolated"


CONN_SEVERITY = {
    ConnState.CONNECTED: 0,
    ConnState.CONGESTED: 1,
    ConnState.DEGRADED: 2,
    ConnState.ISOLATED: 3,
}


class TriageFloor(StrEnum):
    NONE = "none"
    ELEVATED = "elevated"
    LIFE_SAFETY = "life_safety"


# --------------------------------------------------------------------------- scenario


class Site(BaseModel):
    id: str
    name: str
    kind: str  # free text: hospital, shelter, port, clinic, care_home, ...
    zone_id: str
    critical: bool = False
    lat: float | None = None  # defaults to the zone centre when omitted
    lon: float | None = None


class Zone(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    radius_m: float
    population: int
    road_access: RoadAccess = RoadAccess.OPEN


class Asset(BaseModel):
    id: str
    name: str
    kind: AssetKind
    lat: float
    lon: float
    status: AssetStatus = AssetStatus.UP
    capacity: float = 0.0  # abstract capacity units
    load: float = 0.0
    coverage_radius_m: float = 0.0
    power: Literal["grid", "battery", "none"] = "grid"
    battery_pct: float | None = None
    links: list[str] = Field(default_factory=list)  # ids of connected assets (graph edges)
    mobile: bool = False
    bandwidth: float = 0.0  # sat terminals: capacity available when activated
    serves: list[Service] = Field(default_factory=list)
    coverage_zones: list[str] = Field(default_factory=list)  # explicit coverage (else geometry)
    extra_zones: list[str] = Field(default_factory=list)  # coverage extended by rebalancing
    depot: str = ""  # free-text location label for standby mobile assets


class Dynamics(BaseModel):
    battery_drain_pct_per_min: float = 0.0
    demand_per_person: float = 0.01  # capacity units of emergency traffic per resident


class ScenarioMeta(BaseModel):
    id: str
    name: str
    description: str
    hazard_label: str  # display only; the engine never branches on it
    category: Literal["exercise", "validation"] = "exercise"
    order: int = 99
    center_lat: float
    center_lon: float
    zoom: float = 12.5


class ScenarioEvent(BaseModel):
    t: float  # minutes since scenario start
    kind: Literal["comm", "telemetry"]
    channel: Channel
    payload: dict[str, object]


class Scenario(BaseModel):
    meta: ScenarioMeta
    zones: list[Zone]
    sites: list[Site] = Field(default_factory=list)
    assets: list[Asset]
    dynamics: Dynamics = Field(default_factory=Dynamics)
    events: list[ScenarioEvent]


# --------------------------------------------------------------------------- ingestion


class Communication(BaseModel):
    id: str
    t: float
    channel: Channel
    adapter: str
    source_id: str
    source_role: SourceRole
    lat: float | None = None
    lon: float | None = None
    zone_hint: str | None = None
    text: str
    delivery_status: str = "delivered"
    triage_floor: TriageFloor = TriageFloor.NONE
    triage_terms: list[str] = Field(default_factory=list)


class TelemetryEvent(BaseModel):
    id: str
    t: float
    asset_id: str
    changes: dict[str, object]
    note: str = ""


# --------------------------------------------------------------------------- AI outputs


class EvidenceRef(BaseModel):
    source_id: str  # communication id or telemetry id
    quote: str


class Fact(BaseModel):
    claim: str
    status: FactStatus
    evidence: list[EvidenceRef] = Field(default_factory=list)
    flagged: str | None = None  # set by guards when evidence failed validation


class Interpretation(BaseModel):
    comm_id: str = ""
    summary: str
    facts: list[Fact]
    needs_mentioned: list[str] = Field(default_factory=list)
    location_text: str | None = None
    zone_id: str | None = None
    people_min: int | None = None
    people_max: int | None = None
    life_threat: Tri = Tri.UNKNOWN
    operations_affected: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    interpreter: str = "unknown"


class Relation(BaseModel):
    incident_id: str
    relation: Literal["same_area", "caused_by", "affects", "conflicts_with"]
    note: str = ""


class Incident(BaseModel):
    id: str
    title: str
    zone_id: str | None = None
    comm_ids: list[str]
    facts: list[Fact] = Field(default_factory=list)
    people_min: int | None = None
    people_max: int | None = None
    life_threat: Tri = Tri.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    related: list[Relation] = Field(default_factory=list)


class CommNeed(BaseModel):
    id: str
    who: str
    zone_id: str | None = None
    purpose: str
    service: Service
    category: Literal[
        "life_safety", "critical_facility", "emergency_operation", "population", "non_urgent"
    ]
    incident_ids: list[str] = Field(default_factory=list)
    people_min: int | None = None
    people_max: int | None = None
    life_threat: Tri = Tri.UNKNOWN
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    verification_needed: bool = False
    first_seen_t: float = 0.0
    status: Literal["open", "resolved"] = "open"
    resolution: str = ""


class DependencyStep(BaseModel):
    description: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class Dependency(BaseModel):
    id: str
    title: str
    steps: list[DependencyStep]
    status: FactStatus = FactStatus.INFERRED


class Contradiction(BaseModel):
    id: str
    description: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    suggested_verification: str = ""


class InfoGap(BaseModel):
    id: str
    question: str
    why_it_matters: str = ""


class ChangeEntry(BaseModel):
    t: float = 0.0
    summary: str
    caused_by: list[str] = Field(default_factory=list)
    needs_affected: list[str] = Field(default_factory=list)


class OperationalPicture(BaseModel):
    version: int = 0
    summary: str = "Awaiting first communications."
    incidents: list[Incident] = Field(default_factory=list)
    needs: list[CommNeed] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    gaps: list[InfoGap] = Field(default_factory=list)
    interpreter: str = "none"
    unlinked_comm_ids: list[str] = Field(default_factory=list)  # kept visible, never dropped
    guard_notes: list[str] = Field(default_factory=list)  # what the evidence guards changed


class SynthesisDelta(BaseModel):
    """What the synthesis step returns: upserts against the previous picture plus a change log.

    Returning a delta (not the whole picture) keeps LLM output small and forces the model to
    update the existing understanding rather than start from scratch.
    """

    summary: str
    incidents: list[Incident] = Field(default_factory=list)
    needs: list[CommNeed] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    gaps: list[InfoGap] = Field(default_factory=list)
    resolved_need_ids: list[str] = Field(default_factory=list)
    closed_item_ids: list[str] = Field(default_factory=list)  # contradictions/gaps settled
    changes: list[ChangeEntry] = Field(default_factory=list)


# --------------------------------------------------------------------------- deterministic


class ZoneState(BaseModel):
    zone_id: str
    state: ConnState
    capacity: float  # usable voice/data capacity (cellular + satellite access)
    demand: float
    channels: dict[str, float]  # channel -> usable capacity (cellular, satellite, mesh, tetra)
    causes: list[str]
    priority_access: bool = False

    @property
    def ratio(self) -> float:
        return self.capacity / self.demand if self.demand > 0 else float("inf")


class NeedAllocation(BaseModel):
    """Bandwidth the min-cost flow gives one need (abstract capacity units)."""

    demand: float
    allocated: float
    mode: Literal["priority", "best_effort"]
    pool_fraction: float | None = None  # share of the zone's best-effort pool that got through

    @property
    def fraction(self) -> float:
        return self.allocated / self.demand if self.demand > 0 else 1.0


class NetworkSnapshot(BaseModel):
    zones: dict[str, ZoneState]
    usable_assets: list[str]
    unusable: dict[str, str]  # asset id -> reason
    allocation: dict[str, NeedAllocation] = Field(default_factory=dict)  # need id -> flow result

    def facts(self) -> list[str]:
        out = [
            f"{z}: {s.state.value} ({'; '.join(s.causes) or 'ok'})" for z, s in self.zones.items()
        ]
        out += [f"{a} unusable: {r}" for a, r in self.unusable.items()]
        return out


class CriterionTrace(BaseModel):
    criterion: str
    value: str


class PrioritizedNeed(BaseModel):
    need: CommNeed
    tier: int
    tier_rule: str
    criteria: list[CriterionTrace]
    connectivity: ConnState
    served: Literal["unserved", "planned", "served"] = "unserved"
    served_by: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class Precondition(BaseModel):
    name: str
    ok: bool
    detail: str


class Intervention(BaseModel):
    id: str
    kind: str
    label: str
    target_zone: str
    asset_ids: list[str]
    scarce: list[str] = Field(default_factory=list)  # scarce resource ids consumed
    services: list[Service]
    capacity_added: float = 0.0
    eta_min: float = 0.0
    preconditions: list[Precondition] = Field(default_factory=list)
    effect: str = ""

    @property
    def feasible(self) -> bool:
        return all(p.ok for p in self.preconditions)


class PlanItem(BaseModel):
    intervention: Intervention
    needs_served: list[str]
    rationale: str


class Rejected(BaseModel):
    intervention: Intervention
    reason_kind: Literal["infeasible", "dominated", "no_need", "rejected_by_operator"]
    detail: str


class Recommendation(BaseModel):
    plan: list[PlanItem]
    rejected: list[Rejected]
    served_by_tier: dict[int, int]
    unserved_needs: list[str]


TimelineKind = Literal[
    "comm", "telemetry", "intelligence", "priority", "decision", "outcome", "system"
]
Actor = Literal["lifeline", "operator", ""]


class TimelineEntry(BaseModel):
    t: float
    kind: TimelineKind
    text: str  # headline: for decisions and outcomes, the intervention label
    detail: str = ""  # decisions: the reason; outcomes: the measured effect
    actor: Actor = ""  # who made a decision
    refs: list[str] = Field(default_factory=list)
