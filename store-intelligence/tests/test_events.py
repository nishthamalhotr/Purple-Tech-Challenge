"""
Unit + integration tests for the event ingestion API.

Coverage targets:
- Empty store (no events yet)
- All-staff clip (no visitor metrics)
- Re-entry detection
- Duplicate ingest (idempotency)
- Malformed events
- Zero purchases (conversion rate = 0)
- Queue abandonment

# AI-ASSISTED: Test edge cases were derived from the challenge spec using AI
# to enumerate scenarios that stress the state machine. The test assertions
# were written by hand to match the expected business logic.
"""
from __future__ import annotations

import pytest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.database import get_db, init_db, engine, Base


@pytest.fixture(autouse=True)
async def reset_db():
    """Drop and recreate all tables before each test for isolation."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


def make_event(
    event_type: str = "entry",
    store_id: str = "store_001",
    track_id: str = "track_1",
    visitor_id: str | None = None,
    is_staff: bool = False,
    zone_id: str | None = None,
    timestamp: datetime | None = None,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": "cam_entrance",
        "event_type": event_type,
        "timestamp": (timestamp or datetime.utcnow()).isoformat(),
        "track_id": track_id,
        "visitor_id": visitor_id,
        "is_staff": is_staff,
        "confidence": 0.92,
        "zone_id": zone_id,
    }


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_empty_store_metrics():
    """Empty store: all metrics should be zero, no errors."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/stores/store_001/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["current_occupancy"] == 0


@pytest.mark.asyncio
async def test_db_unavailable_returns_503():
    async def fake_get_db():
        raise SQLAlchemyError("db unavailable")

    app.dependency_overrides[get_db] = fake_get_db
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/stores/store_001/metrics")
    app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["status"] == "error"
    assert resp.json()["detail"] == "Database unavailable"


@pytest.mark.asyncio
async def test_ingest_single_entry():
    """Single entry event should create a visitor session."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/events/ingest", json=make_event("entry"))
    assert resp.status_code == 201
    assert resp.json()["status"] == "created"


@pytest.mark.asyncio
async def test_duplicate_event_returns_409():
    """Same event_id sent twice returns 409 duplicate, not error."""
    event = make_event("entry")
    async with AsyncClient(app=app, base_url="http://test") as client:
        r1 = await client.post("/events/ingest", json=event)
        r2 = await client.post("/events/ingest", json=event)
    # First call: 201 created
    assert r1.status_code == 201
    assert r1.json()["status"] == "created"
    # Second call: 409 duplicate (not a 500)
    assert r2.status_code == 409
    assert r2.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_malformed_event_rejected():
    """Missing required fields should return 422."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/events/ingest", json={"event_type": "entry"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_event_type_rejected():
    """Unknown event type should return 422."""
    event = make_event()
    event["event_type"] = "teleportation"
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/events/ingest", json=event)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_all_staff_clip_no_visitor_metrics():
    """When all events are staff, unique_visitors should be 0."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        for i in range(5):
            await client.post("/events/ingest", json=make_event(
                "staff_detected", track_id=f"staff_{i}", is_staff=True
            ))
        resp = await client.get("/stores/store_001/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
    assert data["current_occupancy"] == 0


@pytest.mark.asyncio
async def test_re_entry_detection():
    """Visitor enters, exits, and enters again — re_entry_count should be 1."""
    visitor_id = "visitor_returning"
    ts = datetime.utcnow()

    async with AsyncClient(app=app, base_url="http://test") as client:
        # First visit
        await client.post("/events/ingest", json=make_event(
            "entry", track_id="t1", visitor_id=visitor_id, timestamp=ts
        ))
        await client.post("/events/ingest", json=make_event(
            "exit", track_id="t1", visitor_id=visitor_id,
            timestamp=ts + timedelta(minutes=10)
        ))
        # Re-entry
        await client.post("/events/ingest", json=make_event(
            "entry", track_id="t2", visitor_id=visitor_id,
            timestamp=ts + timedelta(hours=1)
        ))

        resp = await client.get("/stores/store_001/metrics")

    data = resp.json()
    assert data["re_entry_count"] >= 1


@pytest.mark.asyncio
async def test_zero_purchases_conversion_rate():
    """Visitors enter/exit but make no purchases — conversion_rate = 0."""
    ts = datetime.utcnow()
    async with AsyncClient(app=app, base_url="http://test") as client:
        for i in range(5):
            await client.post("/events/ingest", json=make_event(
                "entry", track_id=f"t{i}", visitor_id=f"v{i}", timestamp=ts + timedelta(minutes=i)
            ))
            await client.post("/events/ingest", json=make_event(
                "exit", track_id=f"t{i}", visitor_id=f"v{i}",
                timestamp=ts + timedelta(minutes=i + 30)
            ))
        resp = await client.get("/stores/store_001/metrics")
    data = resp.json()
    assert data["unique_visitors"] == 5
    assert data["conversion_rate"] == 0.0
    assert data["avg_dwell_time_seconds"] > 0


@pytest.mark.asyncio
async def test_purchase_updates_conversion_rate():
    """Purchase event should convert a session."""
    ts = datetime.utcnow()
    visitor_id = "v_buyer"
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/events/ingest", json=make_event(
            "entry", track_id="t1", visitor_id=visitor_id, timestamp=ts
        ))
        await client.post("/events/ingest", json=make_event(
            "purchase", track_id="t1", visitor_id=visitor_id,
            timestamp=ts + timedelta(minutes=15)
        ))
        resp = await client.get("/stores/store_001/metrics")
    data = resp.json()
    assert data["conversion_rate"] == 1.0


@pytest.mark.asyncio
async def test_queue_abandonment_counted():
    """Queue join without exit should be counted in queue_depth."""
    ts = datetime.utcnow()
    async with AsyncClient(app=app, base_url="http://test") as client:
        for i in range(3):
            await client.post("/events/ingest", json=make_event(
                "entry", track_id=f"t{i}", visitor_id=f"v{i}",
                timestamp=ts + timedelta(seconds=i)
            ))
        for i in range(3):
            await client.post("/events/ingest", json=make_event(
                "queue_join", track_id=f"t{i}", visitor_id=f"v{i}",
                timestamp=ts + timedelta(minutes=10 + i)
            ))
        resp = await client.get("/stores/store_001/metrics")
    data = resp.json()
    assert data["queue_depth"] == 3


@pytest.mark.asyncio
async def test_batch_ingest():
    """Batch ingest should process all events."""
    events = [make_event("entry", track_id=f"t{i}", visitor_id=f"v{i}") for i in range(5)]
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/events/batch", json={"events": events})
    data = resp.json()
    assert resp.status_code == 201
    assert data["total"] == 5
    assert data["created"] == 5
    assert data["duplicates"] == 0


@pytest.mark.asyncio
async def test_batch_deduplication():
    """Batch with duplicate event_ids should report them as duplicates."""
    event = make_event("entry", track_id="t1")
    async with AsyncClient(app=app, base_url="http://test") as client:
        # First send alone
        await client.post("/events/ingest", json=event)
        # Now send in batch with same event_id
        resp = await client.post("/events/batch", json={"events": [event]})
    data = resp.json()
    assert data["duplicates"] == 1
    assert data["created"] == 0


@pytest.mark.asyncio
async def test_dwell_time_computed_on_exit():
    """Dwell time should be entry → exit delta in seconds."""
    ts = datetime.utcnow()
    visitor_id = "v_dwell"
    async with AsyncClient(app=app, base_url="http://test") as client:
        await client.post("/events/ingest", json=make_event(
            "entry", track_id="t1", visitor_id=visitor_id, timestamp=ts
        ))
        await client.post("/events/ingest", json=make_event(
            "exit", track_id="t1", visitor_id=visitor_id,
            timestamp=ts + timedelta(minutes=20)
        ))
        resp = await client.get("/stores/store_001/sessions")
    sessions = resp.json()
    assert len(sessions) == 1
    assert abs(sessions[0]["dwell_seconds"] - 1200) < 5  # ~20 minutes


@pytest.mark.asyncio
async def test_funnel_stages():
    """Funnel should have 4 stages with correct ordering."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/stores/store_001/funnel")
    data = resp.json()
    assert len(data["stages"]) == 4
    stages = [s["stage"] for s in data["stages"]]
    assert "Entered Store" in stages
    assert "Completed Purchase" in stages


@pytest.mark.asyncio
async def test_heatmap_empty():
    """Empty heatmap returns zones list (empty if no zone events)."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/stores/store_001/heatmap")
    assert resp.status_code == 200
    assert resp.json()["zones"] == []


@pytest.mark.asyncio
async def test_anomalies_empty():
    """No anomalies in empty store."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/stores/store_001/anomalies")
    assert resp.status_code == 200
    assert resp.json() == []
