"""
collectors/models.py
Pydantic models for all normalized data structures.
Every collector normalizes its output to these before writing to storage.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class BGPChangeType(str, Enum):
    ANNOUNCE  = "announce"
    WITHDRAW  = "withdraw"
    UPDATE    = "update"      # path attribute change on existing prefix

class RPKIStatus(str, Enum):
    VALID     = "valid"
    INVALID   = "invalid"
    NOT_FOUND = "not-found"
    UNKNOWN   = "unknown"

class EventType(str, Enum):
    BGP_HIJACK        = "bgp_hijack"
    ROUTE_LEAK        = "route_leak"
    BGP_FLAP          = "bgp_flap"
    WITHDRAWAL_SURGE  = "withdrawal_surge"
    OUTAGE            = "outage"
    LATENCY_SPIKE     = "latency_spike"
    COMMUNITY_REPORT  = "community_report"

class Severity(int, Enum):
    INFO     = 1
    LOW      = 2
    MEDIUM   = 3
    HIGH     = 4
    CRITICAL = 5


# ─────────────────────────────────────────────
# BGP Update — raw normalized record
# ─────────────────────────────────────────────

class BGPUpdate(BaseModel):
    time:         datetime
    prefix:       str
    origin_asn:   Optional[int]   = None
    as_path:      list[int]       = Field(default_factory=list)
    communities:  list[str]       = Field(default_factory=list)
    change_type:  BGPChangeType
    collector:    str             # ripe-ris | routeviews
    peer_asn:     Optional[int]   = None
    next_hop:     Optional[str]   = None
    rpki_status:  RPKIStatus      = RPKIStatus.UNKNOWN

    class Config:
        use_enum_values = True

# ─────────────────────────────────────────────
# BGP Anomaly — detected event
# ─────────────────────────────────────────────

class BGPAnomaly(BaseModel):
    time:             datetime
    event_id:         str          = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type:       EventType
    affected_prefix:  Optional[str]  = None
    origin_asn:       Optional[int]  = None
    expected_asn:     Optional[int]  = None   # for hijack detection
    severity:         Severity
    confidence:       float          = 0.0    # 0.0 - 1.0
    source:           str            = "bgp-collector"
    raw_data:         dict           = Field(default_factory=dict)

    class Config:
        use_enum_values = True


# ─────────────────────────────────────────────
# AS Node — topology entity (written to Neo4j)
# ─────────────────────────────────────────────

class ASNode(BaseModel):
    asn:            int
    name:           Optional[str]   = None
    org:            Optional[str]   = None
    country:        Optional[str]   = None
    tier:           Optional[int]   = None    # 1, 2, or 3
    ixp_presence:   list[str]       = Field(default_factory=list)
    prefix_count_v4: int            = 0
    prefix_count_v6: int            = 0
    health_score:   Optional[float] = None
    last_updated:   Optional[datetime] = None


# ─────────────────────────────────────────────
# Network Event — correlated incident
# ─────────────────────────────────────────────

class TechnicalSignal(BaseModel):
    source:       str
    signal_type:  str
    confidence:   float
    data:         dict = Field(default_factory=dict)


class CommunitySignal(BaseModel):
    source:         str    # reddit | x | nanog | hn
    mention_count:  int    = 1
    sentiment:      str    = "informational"   # urgent | informational | resolved
    urgency_score:  float  = 0.0
    entities:       list[str] = Field(default_factory=list)
    timestamp:      datetime
    url:            Optional[str] = None


class NetworkEvent(BaseModel):
    event_id:              str          = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type:            EventType
    severity:              Severity
    confidence:            float        = 0.0
    affected_asns:         list[int]    = Field(default_factory=list)
    affected_prefixes:     list[str]    = Field(default_factory=list)
    affected_regions:      list[str]    = Field(default_factory=list)
    technical_signals:     list[TechnicalSignal]  = Field(default_factory=list)
    community_signals:     list[CommunitySignal]  = Field(default_factory=list)
    correlation_score:     float        = 0.0
    source_count:          int          = 1
    first_detected:        datetime
    last_updated:          Optional[datetime] = None
    resolved_at:           Optional[datetime] = None
    summary:               Optional[str] = None

    class Config:
        use_enum_values = True
