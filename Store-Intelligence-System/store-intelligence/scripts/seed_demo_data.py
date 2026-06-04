"""
Seed demo data into the Store Intelligence API.
Simulates a full day of store activity for store_001.

Usage:
  python scripts/seed_demo_data.py [--api-url http://localhost:8000]

Generates:
  - 120 unique visitors across 8 hours
  - ~40% conversion rate
  - 15% re-entry rate
  - 3 staff members
  - 5 queue spike events
  - Zone activity across all 6 zones
"""
from __future__ import annotations

import asyncio
import random
import uuid
import sys
import os
from datetime import datetime, timedelta

import httpx

API_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
STORE_ID = "store_001"

ZONES = ["zone_entrance", "zone_skincare", "zone_makeup", "zone_haircare", "zone_fragrances", "zone_billing"]

random.seed(42)  # Reproducible demo data


def make_event(event_type: str, track_id: str, visitor_id: str, ts: datetime,
               zone_id: str | None = None, is_staff: bool = False) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": "cam_entrance" if event_type in ("entry", "exit") else "cam_floor_north",
        "event_type": event_type,
        "timestamp": ts.isoformat(),
        "track_id": track_id,
        "visitor_id": visitor_id,
        "is_staff": is_staff,
        "confidence": round(random.uniform(0.75, 0.99), 2),
        "zone_id": zone_id,
    }


async def seed():
    day_start = datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    events: list[dict] = []

    # === Staff ===
    for s in range(3):
        ts = day_start + timedelta(minutes=random.randint(0, 30))
        events.append(make_event("staff_detected", f"staff_{s}", f"staff_{s}", ts, is_staff=True))

    # === Visitors ===
    num_visitors = 120
    visitor_ids = [str(uuid.uuid4()) for _ in range(num_visitors)]
    re_entry_visitors = random.sample(visitor_ids, int(num_visitors * 0.15))

    for i, vid in enumerate(visitor_ids):
        # Entry time spread over 8 hours
        entry_offset = timedelta(hours=random.uniform(0, 8), minutes=random.uniform(0, 60))
        entry_ts = day_start + entry_offset
        track_id = f"t_{i}"

        # Entry
        events.append(make_event("entry", track_id, vid, entry_ts))

        # Zone visits (1-4 zones)
        num_zones = random.randint(1, 4)
        visited_zones = random.sample(ZONES[:5], num_zones)  # exclude billing
        zone_ts = entry_ts + timedelta(minutes=2)
        for zone in visited_zones:
            events.append(make_event("zone_enter", track_id, vid, zone_ts, zone))
            dwell = random.uniform(30, 300)
            events.append(make_event("zone_exit", track_id, vid,
                                     zone_ts + timedelta(seconds=dwell), zone))
            zone_ts += timedelta(seconds=dwell + random.uniform(10, 60))

        # Conversion (~40%)
        converted = random.random() < 0.40
        if converted:
            events.append(make_event("queue_join", track_id, vid,
                                     zone_ts + timedelta(minutes=2), "zone_billing"))
            events.append(make_event("purchase", track_id, vid,
                                     zone_ts + timedelta(minutes=5)))

        # Exit
        dwell_total = random.uniform(600, 3600)  # 10 min to 1 hour
        exit_ts = entry_ts + timedelta(seconds=dwell_total)
        events.append(make_event("exit", track_id, vid, exit_ts))

        # Re-entry (some visitors return)
        if vid in re_entry_visitors:
            re_entry_ts = exit_ts + timedelta(hours=random.uniform(0.5, 2))
            re_track_id = f"t_{i}_re"
            events.append(make_event("entry", re_track_id, vid, re_entry_ts))
            re_exit_ts = re_entry_ts + timedelta(minutes=random.uniform(10, 40))
            events.append(make_event("exit", re_track_id, vid, re_exit_ts))

    # === Queue spike simulation ===
    spike_ts = day_start + timedelta(hours=5)  # 3pm rush
    for j in range(8):
        events.append(make_event("queue_join", f"spike_{j}", str(uuid.uuid4()),
                                 spike_ts + timedelta(seconds=j * 20)))

    # Sort events by timestamp for realistic ordering
    events.sort(key=lambda e: e["timestamp"])

    print(f"Generated {len(events)} events. Sending in batches...")

    async with httpx.AsyncClient(base_url=API_URL, timeout=30) as client:
        # Register store first
        # Note: stores are auto-created from events in the full system
        # Here we also ensure at least one record exists
        # (The API doesn't have a POST /stores endpoint — stores exist via config)

        batch_size = 50
        total_sent = 0
        for start in range(0, len(events), batch_size):
            batch = events[start:start + batch_size]
            resp = await client.post("/events/batch", json={"events": batch})
            result = resp.json()
            total_sent += result.get("created", 0)
            print(f"  Batch {start//batch_size + 1}: {result.get('created')} created, "
                  f"{result.get('duplicates')} dupes, {result.get('errors')} errors")

        print(f"\nSeed complete. {total_sent}/{len(events)} events ingested.")

        # Verify
        metrics = await client.get(f"/stores/{STORE_ID}/metrics")
        m = metrics.json()
        print(f"\nStore Metrics:")
        print(f"  Unique visitors: {m.get('unique_visitors')}")
        print(f"  Conversion rate: {m.get('conversion_rate', 0):.1%}")
        print(f"  Avg dwell: {m.get('avg_dwell_time_seconds', 0):.0f}s")
        print(f"  Re-entries: {m.get('re_entry_count')}")

        anomalies = await client.get(f"/stores/{STORE_ID}/anomalies")
        print(f"\nAnomalies detected: {len(anomalies.json())}")


if __name__ == "__main__":
    asyncio.run(seed())
