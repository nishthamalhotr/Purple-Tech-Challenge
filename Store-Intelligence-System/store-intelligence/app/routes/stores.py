"""
Store analytics endpoints.

All read endpoints accept optional ?from=&to= ISO datetime range.
Default window: today (00:00 to now in UTC).

Performance notes:
- All queries use indexed columns (store_id + timestamp compound index)
- Heatmap query: GROUP BY over zone_dwells, O(zone_count × visitor_count)
- Timeline: CTE with generate_series — only PostgreSQL; SQLite uses Python fallback
- For high-traffic stores: add materialized view / Redis cache with 30s TTL
"""
from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, and_, func, case, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    get_db, EventModel, SessionModel, ZoneDwellModel, AnomalyModel, StoreModel
)
from app.models import (
    StoreMetrics, FunnelData, FunnelStage, HeatmapData, ZoneHeat,
    AnomalyRecord, VisitorSession, TimelinePoint, StoreSummary, Store
)

router = APIRouter(prefix="/stores", tags=["stores"])


def _parse_range(from_: Optional[str], to_: Optional[str]):
    now = datetime.utcnow()
    start = datetime.fromisoformat(from_) if from_ else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.fromisoformat(to_) if to_ else now + timedelta(days=1)
    return start, end


@router.get("/", response_model=list[Store])
async def list_stores(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoreModel).order_by(StoreModel.name))
    stores = result.scalars().all()
    return [Store(
        id=s.id, name=s.name, location=s.location or "",
        camera_count=s.camera_count, is_active=s.is_active
    ) for s in stores]


@router.get("/{store_id}/metrics", response_model=StoreMetrics)
async def get_metrics(
    store_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to_: Optional[str] = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(from_, to_)

    base = and_(EventModel.store_id == store_id,
                EventModel.timestamp >= start, EventModel.timestamp <= end)

    entries = await db.execute(select(func.count()).where(and_(base, EventModel.event_type == "entry", EventModel.is_staff == False)))  # noqa
    exits = await db.execute(select(func.count()).where(and_(base, EventModel.event_type == "exit", EventModel.is_staff == False)))  # noqa
    staff = await db.execute(select(func.count()).where(and_(base, EventModel.event_type == "staff_detected")))
    queue_j = await db.execute(select(func.count()).where(and_(base, EventModel.event_type == "queue_join")))

    sbase = and_(SessionModel.store_id == store_id, SessionModel.is_staff == False,  # noqa
                 SessionModel.entry_time >= start, SessionModel.entry_time <= end)
    sess = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((SessionModel.is_converted == True, 1), else_=0)).label("converted"),  # noqa
            func.avg(SessionModel.dwell_seconds).label("avg_dwell"),
            func.sum(case((and_(SessionModel.is_converted == False, SessionModel.exit_time != None), 1), else_=0)).label("abandoned"),  # noqa
            func.sum(case((SessionModel.re_entry_number > 0, 1), else_=0)).label("re_entries"),
        ).where(sbase)
    )
    row = sess.one()

    total_entries = entries.scalar() or 0
    total_exits = exits.scalar() or 0
    unique_visitors = row.total or 0
    converted = row.converted or 0
    avg_dwell = float(row.avg_dwell or 0)
    abandoned = row.abandoned or 0
    re_entries = row.re_entries or 0
    occupancy = max(0, total_entries - total_exits)

    return StoreMetrics(
        store_id=store_id,
        period_start=start,
        period_end=end,
        unique_visitors=unique_visitors,
        total_entries=total_entries,
        total_exits=total_exits,
        staff_count=staff.scalar() or 0,
        conversion_rate=converted / unique_visitors if unique_visitors else 0.0,
        avg_dwell_time_seconds=avg_dwell,
        abandonment_rate=abandoned / unique_visitors if unique_visitors else 0.0,
        current_occupancy=occupancy,
        peak_occupancy=occupancy,
        queue_depth=queue_j.scalar() or 0,
        re_entry_count=re_entries,
    )


@router.get("/{store_id}/funnel", response_model=FunnelData)
async def get_funnel(
    store_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to_: Optional[str] = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(from_, to_)
    sbase = and_(SessionModel.store_id == store_id, SessionModel.is_staff == False,  # noqa
                 SessionModel.entry_time >= start, SessionModel.entry_time <= end)
    ebase = and_(EventModel.store_id == store_id,
                 EventModel.timestamp >= start, EventModel.timestamp <= end)

    entered_r = await db.execute(select(func.count()).where(sbase))
    browsed_r = await db.execute(select(func.count()).where(and_(sbase, func.json_array_length(SessionModel.zones_visited) > 0) if hasattr(func, 'json_array_length') else sbase))
    queue_r = await db.execute(select(func.count()).where(and_(ebase, EventModel.event_type == "queue_join")))
    purchased_r = await db.execute(select(func.count()).where(and_(sbase, SessionModel.is_converted == True)))  # noqa

    entered = entered_r.scalar() or 0
    browsed = min(entered, browsed_r.scalar() or entered)  # cap at entered
    queue = queue_r.scalar() or 0
    purchased = purchased_r.scalar() or 0

    stages = [
        FunnelStage(stage="Entered Store", count=entered, drop_off=0.0, avg_time_in_stage_seconds=None),
        FunnelStage(stage="Browsed Zones", count=browsed,
                    drop_off=(entered - browsed) / entered if entered else 0.0, avg_time_in_stage_seconds=None),
        FunnelStage(stage="Reached Checkout", count=queue,
                    drop_off=(browsed - queue) / browsed if browsed else 0.0, avg_time_in_stage_seconds=None),
        FunnelStage(stage="Completed Purchase", count=purchased,
                    drop_off=(queue - purchased) / queue if queue else 0.0, avg_time_in_stage_seconds=None),
    ]
    return FunnelData(store_id=store_id, stages=stages)


@router.get("/{store_id}/heatmap", response_model=HeatmapData)
async def get_heatmap(
    store_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to_: Optional[str] = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(from_, to_)
    result = await db.execute(
        select(
            ZoneDwellModel.zone_id,
            ZoneDwellModel.zone_name,
            func.coalesce(func.sum(ZoneDwellModel.dwell_seconds), 0).label("total_dwell"),
            func.count(func.distinct(ZoneDwellModel.visitor_id)).label("visitor_count"),
            func.coalesce(func.avg(ZoneDwellModel.dwell_seconds), 0).label("avg_dwell"),
        ).where(
            and_(ZoneDwellModel.store_id == store_id,
                 ZoneDwellModel.enter_time >= start, ZoneDwellModel.enter_time <= end)
        ).group_by(ZoneDwellModel.zone_id, ZoneDwellModel.zone_name)
    )
    rows = result.all()

    max_dwell = max((float(r.total_dwell) for r in rows), default=1.0)
    max_dwell = max(max_dwell, 1.0)

    zones = [
        ZoneHeat(
            zone_id=r.zone_id,
            zone_name=r.zone_name or r.zone_id,
            total_dwell_seconds=float(r.total_dwell),
            visitor_count=int(r.visitor_count),
            avg_dwell_seconds=float(r.avg_dwell),
            intensity=float(r.total_dwell) / max_dwell,
            is_dead_zone=float(r.total_dwell) / max_dwell < 0.1 and int(r.visitor_count) < 3,
        )
        for r in rows
    ]
    return HeatmapData(store_id=store_id, zones=zones)


@router.get("/{store_id}/anomalies", response_model=list[AnomalyRecord])
async def get_anomalies(
    store_id: str,
    severity: Optional[str] = None,
    resolved: Optional[bool] = None,
    limit: int = Query(50, le=500),
    db: AsyncSession = Depends(get_db),
):
    conditions = [AnomalyModel.store_id == store_id]
    if severity:
        conditions.append(AnomalyModel.severity == severity)
    if resolved is not None:
        conditions.append(AnomalyModel.resolved == resolved)

    result = await db.execute(
        select(AnomalyModel).where(and_(*conditions))
        .order_by(AnomalyModel.detected_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [AnomalyRecord(
        id=a.id, store_id=a.store_id, anomaly_type=a.anomaly_type,
        severity=a.severity, detected_at=a.detected_at, description=a.description,
        suggested_action=a.suggested_action, metric_value=a.metric_value,
        threshold_value=a.threshold_value, resolved=a.resolved, zone_id=a.zone_id,
    ) for a in rows]


@router.get("/{store_id}/sessions", response_model=list[VisitorSession])
async def get_sessions(
    store_id: str,
    from_: Optional[str] = Query(None, alias="from"),
    to_: Optional[str] = Query(None, alias="to"),
    limit: int = Query(100, le=1000),
    db: AsyncSession = Depends(get_db),
):
    start, end = _parse_range(from_, to_)
    result = await db.execute(
        select(SessionModel).where(
            and_(SessionModel.store_id == store_id,
                 SessionModel.entry_time >= start, SessionModel.entry_time <= end)
        ).order_by(SessionModel.entry_time.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [VisitorSession(
        session_id=s.session_id, visitor_id=s.visitor_id, store_id=s.store_id,
        entry_time=s.entry_time, exit_time=s.exit_time, dwell_seconds=s.dwell_seconds,
        zones_visited=s.zones_visited or [], is_converted=s.is_converted,
        is_staff=s.is_staff, re_entry_number=s.re_entry_number, track_ids=s.track_ids or [],
    ) for s in rows]


@router.get("/{store_id}/timeline", response_model=list[TimelinePoint])
async def get_timeline(
    store_id: str,
    date_: Optional[str] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
):
    if date_:
        target = datetime.fromisoformat(date_)
    else:
        target = datetime.utcnow()

    day_start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    result = await db.execute(
        select(
            func.strftime("%Y-%m-%dT%H:00:00", EventModel.timestamp).label("hour"),
            func.sum(case((EventModel.event_type == "entry", 1), else_=0)).label("entries"),
            func.sum(case((EventModel.event_type == "exit", 1), else_=0)).label("exits"),
            func.sum(case((EventModel.event_type == "purchase", 1), else_=0)).label("conversions"),
        ).where(
            and_(EventModel.store_id == store_id,
                 EventModel.is_staff == False,  # noqa
                 EventModel.timestamp >= day_start, EventModel.timestamp < day_end)
        ).group_by("hour").order_by("hour")
    )
    rows = result.all()

    points = []
    running_occupancy = 0
    for r in rows:
        running_occupancy += (r.entries or 0) - (r.exits or 0)
        points.append(TimelinePoint(
            hour=r.hour or "",
            entries=r.entries or 0,
            exits=r.exits or 0,
            occupancy=max(0, running_occupancy),
            conversions=r.conversions or 0,
        ))
    return points


@router.get("/{store_id}/summary", response_model=StoreSummary)
async def get_summary(store_id: str, db: AsyncSession = Depends(get_db)):
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    seven_days_ago = today_start - timedelta(days=7)

    store_r = await db.execute(select(StoreModel).where(StoreModel.id == store_id).limit(1))
    store = store_r.scalar_one_or_none()

    sbase_today = and_(SessionModel.store_id == store_id, SessionModel.is_staff == False, SessionModel.entry_time >= today_start)  # noqa
    sbase_yest = and_(SessionModel.store_id == store_id, SessionModel.is_staff == False,  # noqa
                      SessionModel.entry_time >= yesterday_start, SessionModel.entry_time < today_start)
    sbase_7d = and_(SessionModel.store_id == store_id, SessionModel.is_staff == False,  # noqa
                    SessionModel.entry_time >= seven_days_ago, SessionModel.entry_time < today_start)

    today_r = await db.execute(select(
        func.count().label("total"),
        func.sum(case((SessionModel.is_converted == True, 1), else_=0)).label("converted"),  # noqa
        func.avg(SessionModel.dwell_seconds).label("avg_dwell"),
    ).where(sbase_today))

    yest_r = await db.execute(select(func.count().label("total")).where(sbase_yest))
    hist_r = await db.execute(select(
        func.count().label("total"),
        func.sum(case((SessionModel.is_converted == True, 1), else_=0)).label("converted"),  # noqa
    ).where(sbase_7d))

    anomaly_r = await db.execute(select(func.count()).where(
        and_(AnomalyModel.store_id == store_id, AnomalyModel.resolved == False)))  # noqa

    queue_r = await db.execute(select(func.count()).where(
        and_(EventModel.store_id == store_id, EventModel.event_type == "queue_join",
             EventModel.timestamp >= today_start)))

    entries_r = await db.execute(select(func.count()).where(
        and_(EventModel.store_id == store_id, EventModel.event_type == "entry",
             EventModel.is_staff == False, EventModel.timestamp >= today_start)))  # noqa
    exits_r = await db.execute(select(func.count()).where(
        and_(EventModel.store_id == store_id, EventModel.event_type == "exit",
             EventModel.is_staff == False, EventModel.timestamp >= today_start)))  # noqa

    t = today_r.one()
    y = yest_r.one()
    h = hist_r.one()

    today_total = t.total or 0
    today_conv = t.converted or 0
    conv_rate = today_conv / today_total if today_total else 0.0
    avg_dwell = float(t.avg_dwell or 0)

    yest_total = y.total or 0
    hist_total = h.total or 0
    hist_conv = h.converted or 0
    hist_rate = hist_conv / hist_total if hist_total else 0.0
    vs_yest = (today_total - yest_total) / yest_total if yest_total else 0.0
    vs_7d = conv_rate - hist_rate

    occupancy = max(0, (entries_r.scalar() or 0) - (exits_r.scalar() or 0))

    return StoreSummary(
        store_id=store_id,
        store_name=store.name if store else store_id,
        today_visitors=today_total,
        today_conversions=today_conv,
        conversion_rate=conv_rate,
        avg_dwell_seconds=avg_dwell,
        current_occupancy=occupancy,
        active_anomalies=anomaly_r.scalar() or 0,
        queue_depth=queue_r.scalar() or 0,
        vs_yesterday_visitors=vs_yest,
        vs_7day_conversion=vs_7d,
    )
