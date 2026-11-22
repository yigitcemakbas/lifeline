"""Domain models: the single source of truth for every layer.

Inputs (scenario + intercepted sessions), AI outputs (transcripts, interpretations, the
operational picture) and deterministic decisions (network state, queue order, admission,
interventions) live here.
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


class NetworkFamily(StrEnum):
    """The radio network a session uses; each has its own capacity."""

    CELLULAR = "cellular"  # towers, mobile base stations, satellite access terminals
    TETRA = "tetra"  # responder trunked radio
    MESH = "mesh"  # device-to-device mesh, bridged to the core
    SATELLITE = "satellite"  # handheld satellite messaging (external constellation)


class SessionKind(StrEnum):
    """What a channel event is, as the gateway sees it."""

    CALL = "call"  # voice call (audio, transcribed)
    SMS = "sms"
    DATA = "data"  # app or facility data session
    RADIO = "radio"  # TETRA group call (audio, transcribed)
    MESSAGE = "message"  # mesh or satellite short message


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


class Transcript(BaseModel):
    """Speech-to-text output for an intercepted call or radio transmission."""

    text: str
    model: str
    avg_logprob: float | None = None  # mean token log-probability over segments
    no_speech_prob: float | None = None
    duration_s: float | None = None

    @property
    def low_confidence(self) -> bool:
        return (self.avg_logprob is not None and self.avg_logprob < -0.7) or (
            self.no_speech_prob is not None and self.no_speech_prob > 0.5
        )


class Communication(BaseModel):
    """One intercepted session: a call, message or data session on its way to the network."""

    id: str
    t: float
    channel: Channel
    adapter: str
    kind: SessionKind
    family: NetworkFamily
    service: Service
    source_id: str
    source_role: SourceRole
    lat: float | None = None
    lon: float | None = None
    zone_hint: str | None = None
    cell_id: str | None = None  # access asset the session was headed to
    audio: str | None = None  # path of the intercepted audio, relative to scenarios/
    transcript: Transcript | None = None
    text: str = ""  # message text, or the transcript of an audio session ("" until transcribed)
    duration_min: float | None = None  # holding time once connected; None = until scenario end
    delivery_status: str = "delivered"
    triage_floor: TriageFloor = TriageFloor.NONE
    triage_terms: list[str] = Field(default_factory=list)

    @property
    def awaiting_transcript(self) -> bool:
        return self.audio is not None and self.transcript is None


class TelemetryEvent(BaseModel):
    id: str
    t: float
    asset_id: str
    changes: dict[str, object]
    note: str = ""


class SessionSpec(BaseModel):
    """What the admission gateway needs to know about an intercepted session."""

    id: str
    t_arrival: float
    family: NetworkFamily
    service: Service
    zone_id: str | None
    demand: float  # capacity units while connected
    duration_min: float | None  # None: stays connected until the scenario ends


# --------------------------------------------------------------------------- AI outputs


class EvidenceRef(BaseModel):
    source_id: str  # communication id or telemetry id
    quote: str


class Fact(BaseModel):
    claim: str
    status: FactStatus
    evidence: list[EvidenceRef] = Field(default_factory=list)
    flagged: str | None = None  # set by guards when evidence failed validation


NeedCategory = Literal[
    "life_safety", "critical_facility", "emergency_operation", "population", "non_urgent"
]


class Interpretation(BaseModel):
    comm_id: str = ""
    summary: str
    category: NeedCategory = "population"
    who: str = ""
    purpose: str = ""
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
    """What one intercepted session needs, built deterministically from its interpretation.

    `zone_id` is where the session attaches to the network (from network metadata);
    `location_zone_id` is where the emergency is, when the AI could place it (a relative calling
    about someone elsewhere)."""

    id: str
    who: str
    zone_id: str | None = None
    location_zone_id: str | None = None
    purpose: str
    service: Service
    family: NetworkFamily = NetworkFamily.CELLULAR
    category: NeedCategory
    analysed: bool = True  # False while transcription or interpretation is pending
    demand: float = 0.0  # capacity units while connected (0: derive from service)
    incident_ids: list[str] = Field(default_factory=list)
    people_min: int | None = None
    people_max: int | None = None
    life_threat: Tri = Tri.UNKNOWN
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    verification_needed: bool = False
    first_seen_t: float = 0.0


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


class OperationalPicture(BaseModel):
    version: int = 0
    summary: str = "Awaiting first communications."
    incidents: list[Incident] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    gaps: list[InfoGap] = Field(default_factory=list)
    interpreter: str = "none"
    unlinked_comm_ids: list[str] = Field(default_factory=list)  # kept visible, never dropped
    guard_notes: list[str] = Field(default_factory=list)  # what the evidence guards changed


class InterpretationBatch(BaseModel):
    """One interpretation request covers every session intercepted in the same update."""

    items: list[Interpretation]


class SynthesisDelta(BaseModel):
    """What the synthesis step returns: upserts against the previous picture plus a change log.

    Returning a delta (not the whole picture) keeps LLM output small and forces the model to
    update the existing understanding rather than start from scratch.
    """

    summary: str
    incidents: list[Incident] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    gaps: list[InfoGap] = Field(default_factory=list)
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

    @property
    def ratio(self) -> float:
        return self.capacity / self.demand if self.demand > 0 else float("inf")


class NeedAllocation(BaseModel):
    """Capacity the admission flow can route to one session (abstract capacity units)."""

    demand: float
    allocated: float

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
    status: Literal["held", "planned", "connected"] = "held"
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
    "comm", "telemetry", "intelligence", "priority", "admission", "decision", "outcome", "system"
]
Actor = Literal["lifeline", "operator", ""]


class TimelineEntry(BaseModel):
    t: float
    kind: TimelineKind
    text: str  # headline: for decisions and outcomes, the intervention label
    detail: str = ""  # decisions: the reason; outcomes: the measured effect
    actor: Actor = ""  # who made a decision
    refs: list[str] = Field(default_factory=list)
