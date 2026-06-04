# CHOICES.md — Architecture Decision Record

This document records the three most significant technical decisions, the options considered, what AI suggested, and the final reasoning.

---

## Decision 1: Detection Model — YOLOv8n + ByteTrack

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| YOLOv8n + ByteTrack | Fast, mature API, ByteTrack built-in | Nano model loses small detections at >15m |
| YOLOv9 + ByteTrack | Slightly better mAP (+1%) | Slower, less mature ecosystem |
| RT-DETR | Transformer-based, better occlusion | 2-3x slower inference, overkill |
| MediaPipe Pose | Lightweight | Pose, not tracking; no track IDs |

### What AI Suggested

AI initially suggested RT-DETR because "transformer-based detection handles occlusion better." After pushback on inference budget, it converged to YOLOv8 + ByteTrack.

### What Was Rejected

RT-DETR: 3x inference cost with marginal real-world accuracy gain in a constrained store environment (fixed cameras, controlled lighting).

DeepSORT: Requires per-frame ReID embedding extraction in the tracking loop. Adds ~20ms/frame on CPU. ByteTrack achieves comparable track continuity using IoU-matching of low-confidence detections instead.

### Final Reasoning

YOLOv8n achieves 30+ FPS on CPU with >85% mAP@0.5 on the COCO persons class. ByteTrack handles occlusion by maintaining both high-confidence and low-confidence tracklet lists — a person obscured for 2-3 frames is recovered without losing their track_id. This is critical for accurate dwell time computation.

The nano variant is the deliberate choice: a 7MB model weight fits in CI, cold-starts in <1 second, and runs without GPU in testing environments.

**Interview defense:** "We chose YOLOv8n because accuracy at store scale is bottlenecked by camera placement, not model size. A better model with a blurry 15m camera gives the same result. ByteTrack's strength is recovery — we never lose a track_id during a brief occlusion."

---

## Decision 2: Event Schema Design

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| JSON over HTTP | Human-readable, Pydantic docs, easy testing | ~3x wire overhead vs binary |
| Apache Avro | Compact, schema evolution | Requires schema registry |
| Protobuf | Fast, typed | Binary debugging, extra toolchain |
| Kafka + Avro | Durable, high-throughput | Operational overhead, overkill |

### What AI Suggested

AI suggested Kafka + Avro for the event stream, citing "production-grade durability." This was rejected.

### What Was Rejected

Kafka: Adds ZooKeeper/KRaft dependency, broker management, consumer group coordination. At single-store scale (<1000 events/hour per camera), this is pure overhead. The event buffer in `EventEmitter` with exponential backoff provides equivalent durability for transient API outages.

Avro: Requires a schema registry service. Binary format makes debugging harder during the challenge review period.

### Final Reasoning

The event schema is designed around three principles:

1. **Idempotency:** `event_id` is a client-generated UUID used as the primary key. Re-submitting the same event_id is a no-op (UNIQUE constraint → 409 response). This makes the pipeline safe to retry without data corruption.

2. **Explicit staff exclusion:** `is_staff` flag is a first-class schema field, not inferred server-side. The pipeline has the best context for staff classification; the API trusts it.

3. **visitor_id decoupling from track_id:** `track_id` is the per-camera tracker assignment (changes on re-entry). `visitor_id` is the re-identified person ID (stable across sessions). Keeping them separate allows the API to compute re-entry rates correctly without requiring the tracker to perform ReID.

**Interview defense:** "JSON is a deliberate choice for explainability. In production, we'd add a Kafka topic between the pipeline and API for durability, keeping the API interface unchanged. The event_id idempotency key makes that migration transparent."

---

## Decision 3: API Architecture — FastAPI over Flask/Django

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| FastAPI + asyncpg | Async-native, auto docs, Pydantic | Smaller ecosystem than Django |
| Flask + SQLAlchemy | Familiar, simple | Sync-only, manual docs |
| Django REST Framework | Batteries included | Heavy ORM, sync by default |
| Express (Node.js) | Fast, event-loop native | Weaker typing for ML-adjacent work |

### What AI Suggested

AI suggested FastAPI from the start. This was accepted.

### Why FastAPI is Correct Here

1. **Async I/O:** All database queries are async. On a 4-core server, sync Flask would queue requests during slow DB operations. FastAPI + asyncpg handles 10x more concurrent requests on identical hardware.

2. **Pydantic validation:** Every event payload is validated against a typed schema with field-level error messages. Zero boilerplate for a challenge submission.

3. **Auto-generated docs:** `/docs` (Swagger UI) at startup provides a working interactive API explorer for reviewers — no Postman collection needed.

4. **Type safety:** Python type hints + Pydantic models make the codebase self-documenting. A reviewer can understand the data flow without reading docstrings.

### What Was Rejected

Django: ORM is synchronous by default. Async support exists but is second-class. Django's added complexity (settings module, migrations framework, admin) is irrelevant for an API-only service.

Flask: No native async support. Adding async requires Quart or Flask's experimental async extension. No built-in validation — would require Marshmallow or similar.

**Interview defense:** "FastAPI's performance characteristics match our workload — I/O bound, high concurrency, relatively simple request handling. Django would be the right choice if we needed the admin panel or had complex ORM requirements. We don't."

---

## Summary Table

| Decision | Rejected | Chosen | Key Reason |
|----------|----------|--------|------------|
| Detection | RT-DETR, DeepSORT | YOLOv8n + ByteTrack | Speed + occlusion recovery + simplicity |
| Event schema | Kafka/Avro | JSON/HTTP + UUID idempotency | Explainability + zero ops overhead |
| API framework | Flask, Django | FastAPI | Async I/O + Pydantic + auto docs |
