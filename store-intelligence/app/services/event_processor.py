"""
Event processor: core business logic for event ingestion.
Handles session management, re-entry detection, and zone tracking.

Design decision: async SQLAlchemy sessions for all DB operations.
This matches FastAPI's async-first model and avoids thread pool overhead.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, and_, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models import EventInput, EventType
from app.database import EventModel, SessionModel, ZoneDwellModel


async def process_event(event: EventInput, db: AsyncSession) -> str:
    """
    Persist an event and update session state machine.

    State machine:
        entry        → open new session (or re-entry)
        exit         → close open session, compute dwell
        purchase     → mark session as converted
        zone_enter   → open zone dwell record
        zone_exit    → close zone dwell, compute zone dwell
        queue_join   → no session state change (counted separately)
        queue_abandon→ no session state change (counted separately)
        staff_detected → no session created (staff excluded from metrics)

    Returns: visitor_id used for this event
    """
    ts = event.timestamp
    is_staff = event.is_staff
    visitor_id = event.visitor_id or event.track_id

    # Persist raw event — unique constraint on event_id provides idempotency
    db_event = EventModel(
        id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        event_type=event.event_type.value,
        timestamp=ts,
        track_id=event.track_id,
        visitor_id=visitor_id,
        zone_id=event.zone_id,
        is_staff=is_staff,
        confidence=event.confidence,
        group_id=event.group_id,
        bbox_x=event.bbox.x if event.bbox else None,
        bbox_y=event.bbox.y if event.bbox else None,
        bbox_w=event.bbox.w if event.bbox else None,
        bbox_h=event.bbox.h if event.bbox else None,
        metadata_json=event.metadata,
    )
    db.add(db_event)
    await db.flush()  # trigger unique constraint early

    if is_staff:
        await db.commit()
        return visitor_id

    if event.event_type == EventType.ENTRY:
        await _handle_entry(event, visitor_id, ts, db)
    elif event.event_type == EventType.EXIT:
        await _handle_exit(event, visitor_id, ts, db)
    elif event.event_type == EventType.PURCHASE:
        await _handle_purchase(event, visitor_id, db)
    elif event.event_type == EventType.ZONE_ENTER and event.zone_id:
        await _handle_zone_enter(event, visitor_id, ts, db)
    elif event.event_type == EventType.ZONE_EXIT and event.zone_id:
        await _handle_zone_exit(event, visitor_id, ts, db)

    await db.commit()
    return visitor_id


async def _handle_entry(event: EventInput, visitor_id: str, ts: datetime, db: AsyncSession) -> None:
    """Open a new visitor session. Count re-entries."""
    # Find previous sessions for this visitor in this store
    result = await db.execute(
        select(func.count()).where(
            and_(
                SessionModel.visitor_id == visitor_id,
                SessionModel.store_id == event.store_id,
            )
        )
    )
    prior_count = result.scalar() or 0
    re_entry_number = prior_count  # 0 = first visit, 1+ = re-entry

    session = SessionModel(
        session_id=str(uuid.uuid4()),
        visitor_id=visitor_id,
        store_id=event.store_id,
        entry_time=ts,
        exit_time=None,
        dwell_seconds=None,
        zones_visited=[],
        is_converted=False,
        is_staff=False,
        re_entry_number=re_entry_number,
        track_ids=[event.track_id],
    )
    db.add(session)


async def _handle_exit(event: EventInput, visitor_id: str, ts: datetime, db: AsyncSession) -> None:
    """Close open session and compute dwell time."""
    result = await db.execute(
        select(SessionModel).where(
            and_(
                SessionModel.visitor_id == visitor_id,
                SessionModel.store_id == event.store_id,
                SessionModel.exit_time == None,  # noqa: E711
            )
        ).order_by(SessionModel.entry_time.desc()).limit(1)
    )
    session = result.scalar_one_or_none()
    if session:
        dwell = (ts - session.entry_time).total_seconds()
        session.exit_time = ts
        session.dwell_seconds = max(0, dwell)
        session.updated_at = datetime.utcnow()


async def _handle_purchase(event: EventInput, visitor_id: str, db: AsyncSession) -> None:
    """Mark open session as converted."""
    result = await db.execute(
        select(SessionModel).where(
            and_(
                SessionModel.visitor_id == visitor_id,
                SessionModel.store_id == event.store_id,
                SessionModel.exit_time == None,  # noqa: E711
            )
        ).order_by(SessionModel.entry_time.desc()).limit(1)
    )
    session = result.scalar_one_or_none()
    if session:
        session.is_converted = True
        session.updated_at = datetime.utcnow()


async def _handle_zone_enter(event: EventInput, visitor_id: str, ts: datetime, db: AsyncSession) -> None:
    """Track zone entry; update session zones_visited list."""
    # Find open session
    result = await db.execute(
        select(SessionModel).where(
            and_(
                SessionModel.visitor_id == visitor_id,
                SessionModel.store_id == event.store_id,
                SessionModel.exit_time == None,  # noqa: E711
            )
        ).order_by(SessionModel.entry_time.desc()).limit(1)
    )
    session = result.scalar_one_or_none()
    session_id = session.session_id if session else str(uuid.uuid4())

    if session and event.zone_id not in (session.zones_visited or []):
        session.zones_visited = (session.zones_visited or []) + [event.zone_id]
        session.updated_at = datetime.utcnow()

    dwell = ZoneDwellModel(
        id=str(uuid.uuid4()),
        session_id=session_id,
        visitor_id=visitor_id,
        store_id=event.store_id,
        zone_id=event.zone_id,
        zone_name=event.zone_id,
        enter_time=ts,
        exit_time=None,
        dwell_seconds=None,
    )
    db.add(dwell)


async def _handle_zone_exit(event: EventInput, visitor_id: str, ts: datetime, db: AsyncSession) -> None:
    """Close open zone dwell record."""
    result = await db.execute(
        select(ZoneDwellModel).where(
            and_(
                ZoneDwellModel.visitor_id == visitor_id,
                ZoneDwellModel.store_id == event.store_id,
                ZoneDwellModel.zone_id == event.zone_id,
                ZoneDwellModel.exit_time == None,  # noqa: E711
            )
        ).order_by(ZoneDwellModel.enter_time.desc()).limit(1)
    )
    dwell = result.scalar_one_or_none()
    if dwell:
        dwell.exit_time = ts
        dwell.dwell_seconds = max(0, (ts - dwell.enter_time).total_seconds())
