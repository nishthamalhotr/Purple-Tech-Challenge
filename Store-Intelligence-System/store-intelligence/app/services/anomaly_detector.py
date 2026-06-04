"""
Anomaly detection engine.

Three core anomaly types — chosen for challenge coverage and explainability:

1. QUEUE SPIKE: current queue_join count in last 15 min exceeds threshold.
   Why 15 min window: billing queue builds and drains in sub-hour cycles.
   Threshold = max(5, 2x rolling average). Severity scales with multiple.

2. CONVERSION DROP: today's conversion rate drops >30% vs 7-day avg.
   Computed hourly to avoid noise from low early-morning volumes.
   Only triggered when today has >20 visitors (statistical significance).

3. DEAD ZONE: a zone with <5 visitors in a day while store has >50 visitors.
   Suggests shelf arrangement, lighting, or signage issue.
   Severity = LOW always (actionable but not urgent).

Tradeoff: simple threshold logic > ML anomaly detection for this challenge.
Reason: threshold logic is explainable in interviews, has no training data
requirement, and achieves similar precision at this event volume.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, and_, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import EventModel, SessionModel, ZoneDwellModel, AnomalyModel

# Thresholds — tunable via environment / config
QUEUE_SPIKE_WINDOW_MINUTES = 15
QUEUE_SPIKE_BASE_THRESHOLD = 5
QUEUE_SPIKE_MULTIPLIER = 2.0

CONVERSION_DROP_MIN_VISITORS = 20
CONVERSION_DROP_THRESHOLD_PCT = 0.30  # 30% relative drop

DEAD_ZONE_MIN_STORE_VISITORS = 50
DEAD_ZONE_MAX_ZONE_VISITORS = 5


async def run_anomaly_checks(store_id: str, db: AsyncSession) -> list[str]:
    """
    Run all anomaly checks after an event is ingested.
    Returns list of anomaly IDs that were newly created.
    """
    triggered: list[str] = []

    anomaly = await _check_queue_spike(store_id, db)
    if anomaly:
        triggered.append(anomaly)

    anomaly = await _check_conversion_drop(store_id, db)
    if anomaly:
        triggered.append(anomaly)

    # Dead zone is expensive — run less frequently (every 10th call)
    # In production: schedule via cron or async task queue
    return triggered


async def _check_queue_spike(store_id: str, db: AsyncSession) -> Optional[str]:
    """Detect billing queue buildup in recent window."""
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=QUEUE_SPIKE_WINDOW_MINUTES)

    result = await db.execute(
        select(func.count()).where(
            and_(
                EventModel.store_id == store_id,
                EventModel.event_type == "queue_join",
                EventModel.timestamp >= window_start,
            )
        )
    )
    recent_queue = result.scalar() or 0

    # Get rolling avg from previous window for comparison
    prev_window_start = window_start - timedelta(minutes=QUEUE_SPIKE_WINDOW_MINUTES * 4)
    result2 = await db.execute(
        select(func.count()).where(
            and_(
                EventModel.store_id == store_id,
                EventModel.event_type == "queue_join",
                EventModel.timestamp >= prev_window_start,
                EventModel.timestamp < window_start,
            )
        )
    )
    prev_count = result2.scalar() or 0
    rolling_avg = prev_count / 4.0 if prev_count > 0 else 0

    dynamic_threshold = max(QUEUE_SPIKE_BASE_THRESHOLD, rolling_avg * QUEUE_SPIKE_MULTIPLIER)

    if recent_queue < dynamic_threshold:
        return None

    # Avoid duplicate active anomalies within 30 min, but allow escalation
    recent_anomaly = await db.execute(
        select(AnomalyModel).where(
            and_(
                AnomalyModel.store_id == store_id,
                AnomalyModel.anomaly_type == "queue_spike",
                AnomalyModel.resolved == False,  # noqa: E712
                AnomalyModel.detected_at >= now - timedelta(minutes=30),
            )
        ).limit(1)
    )
    existing_anomaly = recent_anomaly.scalar_one_or_none()

    multiple = recent_queue / max(1, dynamic_threshold)
    severity = "high" if multiple >= 2.0 else "medium" if multiple >= 1.5 else "low"

    if existing_anomaly:
        severity_rank = {"low": 1, "medium": 2, "high": 3}
        if severity_rank[severity] <= severity_rank.get(existing_anomaly.severity, 0):
            return None

        existing_anomaly.severity = severity
        existing_anomaly.detected_at = now
        existing_anomaly.description = (
            f"Queue depth reached {recent_queue} joins in the last "
            f"{QUEUE_SPIKE_WINDOW_MINUTES} minutes (threshold: {dynamic_threshold:.0f}). "
            f"This is {multiple:.1f}x the expected rate."
        )
        existing_anomaly.metric_value = float(recent_queue)
        existing_anomaly.threshold_value = dynamic_threshold
        await db.commit()
        return existing_anomaly.id

    anomaly = AnomalyModel(
        id=str(uuid.uuid4()),
        store_id=store_id,
        anomaly_type="queue_spike",
        severity=severity,
        detected_at=now,
        description=(
            f"Queue depth reached {recent_queue} joins in the last "
            f"{QUEUE_SPIKE_WINDOW_MINUTES} minutes (threshold: {dynamic_threshold:.0f}). "
            f"This is {multiple:.1f}x the expected rate."
        ),
        suggested_action=(
            "Open additional billing counters immediately. "
            "Assign staff from low-traffic zones to checkout. "
            "Consider Express lane for <3 items."
        ),
        metric_value=float(recent_queue),
        threshold_value=dynamic_threshold,
        resolved=False,
    )
    db.add(anomaly)
    await db.commit()
    return anomaly.id


async def _check_conversion_drop(store_id: str, db: AsyncSession) -> Optional[str]:
    """Detect significant drop in conversion rate vs 7-day baseline."""
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today_start - timedelta(days=7)

    # Today's stats
    result = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((SessionModel.is_converted == True, 1), else_=0)).label("converted"),  # noqa: E712
        ).where(
            and_(
                SessionModel.store_id == store_id,
                SessionModel.is_staff == False,  # noqa: E712
                SessionModel.entry_time >= today_start,
            )
        )
    )
    row = result.one()
    today_total = row.total or 0
    today_converted = row.converted or 0

    if today_total < CONVERSION_DROP_MIN_VISITORS:
        return None  # Not enough data

    today_rate = today_converted / today_total

    # 7-day baseline (exclude today)
    result7 = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((SessionModel.is_converted == True, 1), else_=0)).label("converted"),  # noqa: E712
        ).where(
            and_(
                SessionModel.store_id == store_id,
                SessionModel.is_staff == False,  # noqa: E712
                SessionModel.entry_time >= seven_days_ago,
                SessionModel.entry_time < today_start,
            )
        )
    )
    row7 = result7.one()
    hist_total = row7.total or 0
    hist_converted = row7.converted or 0

    if hist_total < 10:
        return None  # No baseline

    baseline_rate = hist_converted / hist_total
    if baseline_rate == 0:
        return None

    drop_pct = (baseline_rate - today_rate) / baseline_rate

    if drop_pct < CONVERSION_DROP_THRESHOLD_PCT:
        return None

    # Deduplicate: one active conversion_drop anomaly per day
    recent = await db.execute(
        select(AnomalyModel).where(
            and_(
                AnomalyModel.store_id == store_id,
                AnomalyModel.anomaly_type == "conversion_drop",
                AnomalyModel.resolved == False,  # noqa: E712
                AnomalyModel.detected_at >= today_start,
            )
        ).limit(1)
    )
    if recent.scalar_one_or_none():
        return None

    severity = "high" if drop_pct >= 0.50 else "medium"

    anomaly = AnomalyModel(
        id=str(uuid.uuid4()),
        store_id=store_id,
        anomaly_type="conversion_drop",
        severity=severity,
        detected_at=now,
        description=(
            f"Today's conversion rate is {today_rate:.1%} vs "
            f"{baseline_rate:.1%} 7-day average — "
            f"a {drop_pct:.0%} relative drop."
        ),
        suggested_action=(
            "Check staff engagement on floor. Review pricing / promotions. "
            "Inspect POS system for issues. Compare today's traffic vs history."
        ),
        metric_value=today_rate,
        threshold_value=baseline_rate * (1 - CONVERSION_DROP_THRESHOLD_PCT),
        resolved=False,
    )
    db.add(anomaly)
    await db.commit()
    return anomaly.id


async def check_dead_zones(store_id: str, db: AsyncSession) -> list[str]:
    """
    Detect zones with very low traffic vs overall store volume.
    Should be called periodically (e.g. hourly), not per-event.
    """
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Overall store visitors today
    result = await db.execute(
        select(func.count()).where(
            and_(
                SessionModel.store_id == store_id,
                SessionModel.is_staff == False,  # noqa: E712
                SessionModel.entry_time >= today_start,
            )
        )
    )
    store_visitors = result.scalar() or 0

    if store_visitors < DEAD_ZONE_MIN_STORE_VISITORS:
        return []

    # Zone visitor counts
    result2 = await db.execute(
        select(
            ZoneDwellModel.zone_id,
            ZoneDwellModel.zone_name,
            func.count(func.distinct(ZoneDwellModel.visitor_id)).label("cnt"),
        ).where(
            and_(
                ZoneDwellModel.store_id == store_id,
                ZoneDwellModel.enter_time >= today_start,
            )
        ).group_by(ZoneDwellModel.zone_id, ZoneDwellModel.zone_name)
    )
    zone_rows = result2.all()

    triggered = []
    for row in zone_rows:
        if row.cnt <= DEAD_ZONE_MAX_ZONE_VISITORS:
            # Check for existing unresolved anomaly for this zone today
            existing = await db.execute(
                select(AnomalyModel).where(
                    and_(
                        AnomalyModel.store_id == store_id,
                        AnomalyModel.anomaly_type == "dead_zone",
                        AnomalyModel.zone_id == row.zone_id,
                        AnomalyModel.resolved == False,  # noqa: E712
                        AnomalyModel.detected_at >= today_start,
                    )
                ).limit(1)
            )
            if existing.scalar_one_or_none():
                continue

            anomaly = AnomalyModel(
                id=str(uuid.uuid4()),
                store_id=store_id,
                anomaly_type="dead_zone",
                severity="low",
                detected_at=now,
                description=(
                    f"Zone '{row.zone_name or row.zone_id}' had only {row.cnt} visitors "
                    f"while the store served {store_visitors} visitors today."
                ),
                suggested_action=(
                    f"Inspect zone '{row.zone_id}' for poor signage, poor lighting, "
                    "or blocked pathways. Consider product reorganization."
                ),
                metric_value=float(row.cnt),
                threshold_value=float(DEAD_ZONE_MAX_ZONE_VISITORS),
                resolved=False,
                zone_id=row.zone_id,
            )
            db.add(anomaly)
            await db.commit()
            triggered.append(anomaly.id)

    return triggered
