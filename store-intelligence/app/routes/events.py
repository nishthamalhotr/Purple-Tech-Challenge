"""
Event ingestion endpoints.

POST /events/ingest  — single event (idempotent via event_id)
POST /events/batch   — up to 1000 events per call

Idempotency: unique constraint on events.id. Duplicate = 409 + status='duplicate'.
Atomicity: each event is its own transaction. Batch is per-event, not batch-level.
Reasoning: partial success in batch is more useful than all-or-nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EventInput, EventBatchInput, IngestResponse, BatchIngestResponse
from app.services.event_processor import process_event
from app.services.anomaly_detector import run_anomaly_checks

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/ingest", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_event(request: Request, event: EventInput, db: AsyncSession = Depends(get_db)):
    """
    Ingest a single behavioral event.
    Returns 409 if event_id already exists (idempotent replay).
    """
    request.state.event_count = 1
    try:
        await process_event(event, db)
    except IntegrityError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=IngestResponse(
                event_id=event.event_id,
                status="duplicate",
                anomalies_triggered=[],
            ).model_dump(),
        )

    anomalies = await run_anomaly_checks(event.store_id, db)

    return IngestResponse(
        event_id=event.event_id,
        status="created",
        anomalies_triggered=anomalies,
    )


@router.post("/batch", response_model=BatchIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_event_batch(request: Request, payload: EventBatchInput, db: AsyncSession = Depends(get_db)):
    """
    Ingest up to 1000 events in one call.
    Returns per-item counts: created, duplicates, errors.
    """
    request.state.event_count = len(payload.events)
    created = 0
    duplicates = 0
    errors = 0
    anomalies: set[str] = set()

    for event in payload.events:
        try:
            await process_event(event, db)
            new_anomalies = await run_anomaly_checks(event.store_id, db)
            anomalies.update(new_anomalies)
            created += 1
        except IntegrityError:
            await db.rollback()
            duplicates += 1
        except Exception:
            await db.rollback()
            errors += 1

    return BatchIngestResponse(
        total=len(payload.events),
        created=created,
        duplicates=duplicates,
        errors=errors,
        anomalies_triggered=list(anomalies),
    )
