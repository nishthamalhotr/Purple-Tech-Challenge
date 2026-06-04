# Store Intelligence System
**Purplle Tech Challenge 2026 — Round 2**

Real-time retail analytics from CCTV footage. Detects visitors, tracks sessions, computes metrics, detects anomalies, and serves a live dashboard.

```
Raw CCTV → Detection Layer → Event Stream → Intelligence API → Live Dashboard
```

---

## Quickstart (≤ 5 commands)

```bash
git clone <repo-url> && cd store-intelligence

# Start the full demo stack with API, mock pipeline, and dashboard
docker compose up --build

# Seed demo data (in a new terminal)
docker compose exec api python scripts/seed_demo_data.py

# Install local dependencies (SQLite-only, no PostgreSQL build tools needed)
pip install -r requirements.txt

# If using PostgreSQL, install asyncpg support too
pip install -r requirements.txt -r requirements-postgres.txt

# Install optional pipeline dependencies locally
pip install -r requirements.txt -r requirements-pipeline.txt

# Run tests
docker compose exec api pytest

# View API docs
open http://localhost:8000/docs

# Run live CCTV stream ingestion
# Bash / WSL
python -m pipeline.run_pipeline \
  --config configs/store_layout.json \
  --api-base-url http://localhost:8000 \
  --stream cam_entrance=rtsp://<username>:<password>@<camera-host>/stream \
  --stream cam_billing=rtsp://<username>:<password>@<camera-host>/stream

# PowerShell
python -m pipeline.run_pipeline `
  --config configs/store_layout.json `
  --api-base-url http://localhost:8000 `
  --stream cam_entrance=rtsp://<username>:<password>@<camera-host>/stream `
  --stream cam_billing=rtsp://<username>:<password>@<camera-host>/stream

# Run the pipeline in mock mode (no live camera hardware)
# Bash / WSL
python -m pipeline.run_pipeline \
  --config configs/store_layout.json \
  --api-base-url http://localhost:8000 \
  --stream cam_entrance=sample.mp4 \
  --mock

# PowerShell
python -m pipeline.run_pipeline `
  --config configs/store_layout.json `
  --api-base-url http://localhost:8000 `
  --stream cam_entrance=sample.mp4 `
  --mock
```

**Dashboard:** The dashboard is now included in Docker Compose at `http://localhost:3000`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  CCTV Cameras                           │
│       cam_entrance | cam_floor_north | cam_billing      │
└──────────────────┬──────────────────────────────────────┘
                   │ frames @ 15-30 FPS
                   ▼
┌─────────────────────────────────────────────────────────┐
│              Detection Layer (pipeline/)                │
│  YOLOv8n → ByteTrack → EntryExitClassifier              │
│  StaffClassifier → ReIdentifier (OSNet)                 │
│  EventEmitter (buffered HTTP, retry)                    │
└──────────────────┬──────────────────────────────────────┘
                   │ POST /events/ingest (JSON)
                   ▼
┌─────────────────────────────────────────────────────────┐
│           Intelligence API (app/)   FastAPI             │
│  /events/ingest  → session state machine                │
│  /stores/{id}/metrics    → SQL aggregations             │
│  /stores/{id}/funnel     → conversion funnel            │
│  /stores/{id}/heatmap    → zone dwell intensity         │
│  /stores/{id}/anomalies  → threshold-based detection    │
│  /stores/{id}/sessions   → visitor session list         │
│  /stores/{id}/timeline   → hourly traffic chart         │
│  /stores/{id}/summary    → dashboard overview           │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│           Database (SQLite dev / PostgreSQL prod)       │
│  events | visitor_sessions | zone_dwells | anomalies    │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│                Live Dashboard                           │
│  React + React Query (10s refetch)                     │
│  Recharts charts | Zone heatmap | Anomaly feed          │
└─────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Detection | YOLOv8n + ByteTrack | 30 FPS on CPU, occlusion recovery |
| Re-ID | OSNet (torchreid) | Lightweight ReID, 70% cosine threshold |
| Staff detection | HSV histogram + dwell heuristic | No training data required |
| Event API | FastAPI + Pydantic | Async I/O, auto docs, type safety |
| Database | SQLite → PostgreSQL | Zero-config dev, production-ready |
| ORM | SQLAlchemy async | Non-blocking DB calls |
| Dashboard | React + Recharts | Real-time charts, React Query polling |
| Containerization | Docker Compose | Single command deploy |

---

## Folder Structure

```
store-intelligence/
├── app/                      # FastAPI application
│   ├── main.py               # App factory, middleware, routers
│   ├── models.py             # Pydantic request/response models
│   ├── database.py           # SQLAlchemy models, engine setup
│   ├── routes/
│   │   ├── events.py         # POST /events/ingest, /batch
│   │   └── stores.py         # GET /stores/{id}/metrics, funnel, etc.
│   └── services/
│       ├── event_processor.py # Session state machine
│       └── anomaly_detector.py # Queue spike, conversion drop, dead zone
├── pipeline/                 # CCTV detection pipeline
│   ├── detector.py           # YOLOv8 + ByteTrack wrapper
│   ├── entry_exit_classifier.py # Tripwire crossing, staff, re-ID
│   ├── event_emitter.py      # HTTP event emission with retry
│   └── video_processor.py   # End-to-end video processing
├── tests/
│   ├── test_events.py        # Event ingestion + session tests
│   └── test_anomaly_detector.py # Anomaly detection tests
├── configs/
│   └── store_layout.json     # Camera config, zones, tripwire
├── scripts/
│   └── seed_demo_data.py     # Demo data generator
├── docker/
│   ├── Dockerfile.api        # API container
│   └── Dockerfile.pipeline   # Pipeline container
├── docs/
│   ├── DESIGN.md             # AI-assisted decisions + tradeoffs
│   └── CHOICES.md            # Architecture decision records
├── docker-compose.yml
└── requirements.txt
```

---

## Key Design Decisions

1. **Idempotent events:** Each event has a UUID `event_id`. Re-submitting returns 409 without side effects. Safe to retry on network failure.

2. **Staff exclusion:** `is_staff=true` events are stored but excluded from all visitor metrics. Staff count is reported separately.

3. **Re-entry detection:** `visitor_id` (from ReID) is stable across sessions. Re-entry number increments on each new entry for the same visitor.

4. **Anomaly deduplication:** Queue spike anomalies have a 30-minute deduplication window. Only one active anomaly per type at a time.

5. **Zero-config dev → production path:** `DATABASE_URL=sqlite+aiosqlite:///./db` for dev, `DATABASE_URL=postgresql+asyncpg://...` for prod. Same schema, same code.

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/events/ingest` | POST | Ingest single event |
| `/events/batch` | POST | Ingest up to 1000 events |
| `/stores/{id}/metrics` | GET | Real-time store metrics |
| `/stores/{id}/funnel` | GET | Conversion funnel |
| `/stores/{id}/heatmap` | GET | Zone dwell heatmap |
| `/stores/{id}/anomalies` | GET | Detected anomalies |
| `/stores/{id}/sessions` | GET | Visitor sessions |
| `/stores/{id}/timeline` | GET | Hourly traffic timeline |
| `/stores/{id}/summary` | GET | Dashboard summary stats |

Full docs: `http://localhost:8000/docs`

---

## Running Tests

```bash
# Inside Docker
docker compose exec api pytest -v

# Locally
pip install -r requirements.txt
pytest -v
```

**Coverage targets:**
- Empty store ✓
- All-staff clip ✓
- Re-entry detection ✓
- Duplicate ingest (idempotency) ✓
- Malformed events ✓
- Zero purchases ✓
- Queue abandonment ✓

---

## Production Checklist

- [ ] Set `DATABASE_URL` to PostgreSQL connection string
- [ ] Set `SECRET_KEY` for session signing
- [ ] Configure camera `store_layout.json` for your store
- [ ] Set staff uniform HSV range in config
- [ ] Point `video_processor.py` at live RTSP stream URLs
- [ ] Configure alerting on anomaly webhook
- [ ] Add Redis for metrics caching (TTL=30s) at high traffic
