"""
Pydantic models for the Store Intelligence API.
All events are validated here before entering the pipeline.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import uuid


class EventType(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"
    ZONE_ENTER = "zone_enter"
    ZONE_EXIT = "zone_exit"
    PURCHASE = "purchase"
    QUEUE_JOIN = "queue_join"
    QUEUE_ABANDON = "queue_abandon"
    STAFF_DETECTED = "staff_detected"


class AnomalyType(str, Enum):
    QUEUE_SPIKE = "queue_spike"
    CONVERSION_DROP = "conversion_drop"
    DEAD_ZONE = "dead_zone"
    HIGH_ABANDONMENT = "high_abandonment"
    UNUSUAL_TRAFFIC = "unusual_traffic"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BoundingBox(BaseModel):
    x: float = Field(..., ge=0)
    y: float = Field(..., ge=0)
    w: float = Field(..., gt=0)
    h: float = Field(..., gt=0)


class EventInput(BaseModel):
    """
    Single behavioral event from the detection pipeline.
    event_id is used as the idempotency key — re-submitting the same
    event_id is a no-op (409 Conflict with status='duplicate').
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str = Field(..., min_length=1)
    camera_id: str = Field(..., min_length=1)
    event_type: EventType
    timestamp: datetime
    track_id: str = Field(..., min_length=1)
    visitor_id: Optional[str] = None
    zone_id: Optional[str] = None
    is_staff: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    group_id: Optional[str] = None
    bbox: Optional[BoundingBox] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_valid(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def visitor_id_defaults_to_track_id(self) -> "EventInput":
        if not self.visitor_id:
            self.visitor_id = self.track_id
        return self


class EventBatchInput(BaseModel):
    events: list[EventInput] = Field(..., min_length=1, max_length=1000)


class IngestResponse(BaseModel):
    event_id: str
    status: str  # "created" | "duplicate"
    anomalies_triggered: list[str] = []


class BatchIngestResponse(BaseModel):
    total: int
    created: int
    duplicates: int
    errors: int
    anomalies_triggered: list[str] = []


class StoreMetrics(BaseModel):
    store_id: str
    period_start: datetime
    period_end: datetime
    unique_visitors: int
    total_entries: int
    total_exits: int
    staff_count: int
    conversion_rate: float
    avg_dwell_time_seconds: float
    abandonment_rate: float
    current_occupancy: int
    peak_occupancy: int
    queue_depth: int
    re_entry_count: int


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off: float
    avg_time_in_stage_seconds: Optional[float] = None


class FunnelData(BaseModel):
    store_id: str
    stages: list[FunnelStage]


class ZoneHeat(BaseModel):
    zone_id: str
    zone_name: str
    total_dwell_seconds: float
    visitor_count: int
    avg_dwell_seconds: float
    intensity: float  # 0.0–1.0 normalized
    is_dead_zone: bool


class HeatmapData(BaseModel):
    store_id: str
    zones: list[ZoneHeat]


class AnomalyRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    anomaly_type: AnomalyType
    severity: Severity
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    description: str
    suggested_action: str
    metric_value: Optional[float] = None
    threshold_value: Optional[float] = None
    resolved: bool = False
    zone_id: Optional[str] = None


class VisitorSession(BaseModel):
    session_id: str
    visitor_id: str
    store_id: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    dwell_seconds: Optional[float] = None
    zones_visited: list[str] = []
    is_converted: bool = False
    is_staff: bool = False
    re_entry_number: int = 0
    track_ids: list[str] = []


class TimelinePoint(BaseModel):
    hour: str
    entries: int
    exits: int
    occupancy: int
    conversions: int


class StoreSummary(BaseModel):
    store_id: str
    store_name: str
    today_visitors: int
    today_conversions: int
    conversion_rate: float
    avg_dwell_seconds: float
    current_occupancy: int
    active_anomalies: int
    queue_depth: int
    vs_yesterday_visitors: float
    vs_7day_conversion: float


class Store(BaseModel):
    id: str
    name: str
    location: str = ""
    camera_count: int = 1
    is_active: bool = True
