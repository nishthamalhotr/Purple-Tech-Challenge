---
name: Store Intelligence Architecture
description: Key decisions for the Purplle Tech Challenge 2026 Round 2 store intelligence system
---

# Store Intelligence — Durable Architecture Decisions

## Event Idempotency
event_id (UUID) is the primary key on the events table. Re-submitting the same event_id returns status="duplicate" without creating a duplicate. This makes the detection pipeline safe to retry on network failures.

**Why:** CCTV pipelines retry on transient failures. Without idempotency, a 2-second network hiccup doubles every metric.

**How to apply:** Always generate event_id client-side (in the pipeline), not server-side.

## Staff Exclusion
`is_staff=true` on events excludes the track from all visitor metrics (sessions, conversion rate, dwell). Staff count is reported separately. Classification is three-tier: explicit ID list > uniform color (HSV) > 2-hour dwell heuristic.

**Why:** Staff inflate occupancy and dwell metrics. A store with 3 staff and 10 customers should show 10 visitors, not 13.

## Anomaly Detection Strategy
Three threshold-based rules (not ML):
1. Queue spike: >5 queue_join events in 15-min window (2x rolling avg)
2. Conversion drop: >30% relative drop vs 7-day baseline (min 20 visitors)
3. Dead zone: zone with <5 visitors when store has >50 (scheduled, not per-event)

30-minute deduplication window prevents duplicate active anomalies.

**Why:** Threshold logic is explainable in interviews, has no training data requirement, fails loudly and predictably.

## Database Path
SQLite dev (sqlite+aiosqlite:///./db) → PostgreSQL prod (postgresql+asyncpg://...). Same SQLAlchemy models, single DATABASE_URL env var.

## Re-Entry Detection
visitor_id (from OSNet ReID) is stable across sessions. re_entry_number counts how many prior sessions exist for the same visitor_id + store_id pair before this entry.
