import { Router } from "express";
import { db, eventsTable, sessionsTable, anomaliesTable, zoneDwellsTable } from "@workspace/db";
import { eq, and, gte, lte, sql, count, avg, max, desc } from "drizzle-orm";
import { randomUUID } from "crypto";

const router = Router();

const EVENT_TYPES = ["entry", "exit", "zone_enter", "zone_exit", "purchase", "queue_join", "queue_abandon", "staff_detected"] as const;

function detectAnomalies(storeId: string, eventType: string): string[] {
  return [];
}

async function processEvent(event: {
  event_id: string;
  store_id: string;
  camera_id: string;
  event_type: string;
  timestamp: string;
  track_id: string;
  visitor_id?: string | null;
  zone_id?: string | null;
  is_staff?: boolean;
  confidence?: number;
  group_id?: string | null;
  bbox?: { x: number; y: number; w: number; h: number } | null;
  metadata?: Record<string, unknown> | null;
}) {
  const ts = new Date(event.timestamp);
  if (isNaN(ts.getTime())) throw new Error("Invalid timestamp");

  const isStaff = event.is_staff ?? false;
  const confidence = event.confidence ?? 1.0;
  const visitorId = event.visitor_id ?? event.track_id;

  await db.insert(eventsTable).values({
    id: event.event_id,
    storeId: event.store_id,
    cameraId: event.camera_id,
    eventType: event.event_type,
    timestamp: ts,
    trackId: event.track_id,
    visitorId,
    zoneId: event.zone_id ?? null,
    isStaff,
    confidence,
    groupId: event.group_id ?? null,
    bboxX: event.bbox?.x ?? null,
    bboxY: event.bbox?.y ?? null,
    bboxW: event.bbox?.w ?? null,
    bboxH: event.bbox?.h ?? null,
    metadata: (event.metadata ?? null) as Record<string, unknown> | null,
  });

  if (!isStaff) {
    if (event.event_type === "entry") {
      const sessionId = randomUUID();
      const existing = await db
        .select()
        .from(sessionsTable)
        .where(and(eq(sessionsTable.visitorId, visitorId), eq(sessionsTable.storeId, event.store_id)))
        .orderBy(desc(sessionsTable.entryTime))
        .limit(1);

      const reEntryNumber = existing.length > 0 ? (existing[0].reEntryNumber ?? 0) + 1 : 0;

      await db.insert(sessionsTable).values({
        sessionId,
        visitorId,
        storeId: event.store_id,
        entryTime: ts,
        exitTime: null,
        dwellSeconds: null,
        zonesVisited: [],
        isConverted: false,
        isStaff: false,
        reEntryNumber,
        trackIds: [event.track_id],
      });
    } else if (event.event_type === "exit") {
      const openSession = await db
        .select()
        .from(sessionsTable)
        .where(
          and(
            eq(sessionsTable.visitorId, visitorId),
            eq(sessionsTable.storeId, event.store_id),
            sql`${sessionsTable.exitTime} IS NULL`
          )
        )
        .orderBy(desc(sessionsTable.entryTime))
        .limit(1);

      if (openSession.length > 0) {
        const s = openSession[0];
        const dwell = (ts.getTime() - s.entryTime.getTime()) / 1000;
        await db
          .update(sessionsTable)
          .set({ exitTime: ts, dwellSeconds: dwell, updatedAt: new Date() })
          .where(eq(sessionsTable.sessionId, s.sessionId));
      }
    } else if (event.event_type === "purchase") {
      const openSession = await db
        .select()
        .from(sessionsTable)
        .where(
          and(
            eq(sessionsTable.visitorId, visitorId),
            eq(sessionsTable.storeId, event.store_id),
            sql`${sessionsTable.exitTime} IS NULL`
          )
        )
        .orderBy(desc(sessionsTable.entryTime))
        .limit(1);

      if (openSession.length > 0) {
        await db
          .update(sessionsTable)
          .set({ isConverted: true, updatedAt: new Date() })
          .where(eq(sessionsTable.sessionId, openSession[0].sessionId));
      }
    } else if (event.event_type === "zone_enter" && event.zone_id) {
      const openSession = await db
        .select()
        .from(sessionsTable)
        .where(
          and(
            eq(sessionsTable.visitorId, visitorId),
            eq(sessionsTable.storeId, event.store_id),
            sql`${sessionsTable.exitTime} IS NULL`
          )
        )
        .orderBy(desc(sessionsTable.entryTime))
        .limit(1);

      if (openSession.length > 0) {
        const s = openSession[0];
        const zonesVisited = [...(s.zonesVisited ?? [])];
        if (!zonesVisited.includes(event.zone_id)) zonesVisited.push(event.zone_id);
        await db
          .update(sessionsTable)
          .set({ zonesVisited, updatedAt: new Date() })
          .where(eq(sessionsTable.sessionId, s.sessionId));

        await db.insert(zoneDwellsTable).values({
          id: randomUUID(),
          sessionId: s.sessionId,
          visitorId,
          storeId: event.store_id,
          zoneId: event.zone_id,
          zoneName: event.zone_id,
          enterTime: ts,
          exitTime: null,
          dwellSeconds: null,
        });
      }
    } else if (event.event_type === "zone_exit" && event.zone_id) {
      const openDwell = await db
        .select()
        .from(zoneDwellsTable)
        .where(
          and(
            eq(zoneDwellsTable.visitorId, visitorId),
            eq(zoneDwellsTable.storeId, event.store_id),
            eq(zoneDwellsTable.zoneId, event.zone_id),
            sql`${zoneDwellsTable.exitTime} IS NULL`
          )
        )
        .orderBy(desc(zoneDwellsTable.enterTime))
        .limit(1);

      if (openDwell.length > 0) {
        const dwell = (ts.getTime() - openDwell[0].enterTime.getTime()) / 1000;
        await db
          .update(zoneDwellsTable)
          .set({ exitTime: ts, dwellSeconds: dwell })
          .where(eq(zoneDwellsTable.id, openDwell[0].id));
      }
    }
  }

  return visitorId;
}

router.post("/ingest", async (req, res) => {
  const body = req.body as Record<string, unknown>;

  if (!body.event_id || !body.store_id || !body.camera_id || !body.event_type || !body.timestamp || !body.track_id) {
    return res.status(400).json({ error: "Missing required fields: event_id, store_id, camera_id, event_type, timestamp, track_id" });
  }

  if (!EVENT_TYPES.includes(body.event_type as (typeof EVENT_TYPES)[number])) {
    return res.status(400).json({ error: `Invalid event_type. Must be one of: ${EVENT_TYPES.join(", ")}` });
  }

  try {
    await processEvent(body as Parameters<typeof processEvent>[0]);
    return res.status(201).json({
      event_id: body.event_id,
      status: "created",
      anomalies_triggered: [],
    });
  } catch (err: unknown) {
    const e = err as { code?: string; message?: string };
    if (e?.code === "23505") {
      return res.status(409).json({
        event_id: body.event_id,
        status: "duplicate",
        anomalies_triggered: [],
      });
    }
    req.log.error({ err }, "Failed to ingest event");
    return res.status(500).json({ error: "Internal server error" });
  }
});

router.post("/batch", async (req, res) => {
  const { events } = req.body as { events: unknown[] };
  if (!Array.isArray(events)) {
    return res.status(400).json({ error: "events must be an array" });
  }

  let created = 0;
  let duplicates = 0;
  let errors = 0;
  const anomaliesTriggered: string[] = [];

  for (const evt of events) {
    const e = evt as Parameters<typeof processEvent>[0];
    try {
      await processEvent(e);
      created++;
    } catch (err: unknown) {
      const er = err as { code?: string };
      if (er?.code === "23505") {
        duplicates++;
      } else {
        errors++;
      }
    }
  }

  return res.status(201).json({
    total: events.length,
    created,
    duplicates,
    errors,
    anomalies_triggered: anomaliesTriggered,
  });
});

export default router;
