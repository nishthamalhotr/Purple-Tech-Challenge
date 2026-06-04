import { Router } from "express";
import { db, eventsTable, sessionsTable, anomaliesTable, zoneDwellsTable, storesTable } from "@workspace/db";
import { eq, and, gte, lte, sql, count, avg, max, min, desc, isNull, isNotNull, ne } from "drizzle-orm";

const router = Router();

function dateRange(from?: string, to?: string) {
  const now = new Date();
  const start = from ? new Date(from) : new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = to ? new Date(to) : new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return { start, end };
}

router.get("/", async (req, res) => {
  const stores = await db.select().from(storesTable).orderBy(storesTable.name);
  return res.json(stores.map((s) => ({
    id: s.id,
    name: s.name,
    location: s.location,
    camera_count: s.cameraCount,
    is_active: s.isActive,
  })));
});

router.get("/:storeId/metrics", async (req, res) => {
  const { storeId } = req.params;
  const { start, end } = dateRange(req.query.from as string, req.query.to as string);

  const [entriesResult, exitsResult, staffResult, sessionsResult, queueResult, reEntryResult] = await Promise.all([
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "entry"),
        gte(eventsTable.timestamp, start), lte(eventsTable.timestamp, end), eq(eventsTable.isStaff, false))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "exit"),
        gte(eventsTable.timestamp, start), lte(eventsTable.timestamp, end), eq(eventsTable.isStaff, false))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "staff_detected"),
        gte(eventsTable.timestamp, start), lte(eventsTable.timestamp, end))
    ),
    db.select({
      total: count(),
      converted: sql<number>`SUM(CASE WHEN ${sessionsTable.isConverted} THEN 1 ELSE 0 END)::int`,
      avgDwell: avg(sessionsTable.dwellSeconds),
      abandonCount: sql<number>`SUM(CASE WHEN NOT ${sessionsTable.isConverted} AND ${sessionsTable.exitTime} IS NOT NULL THEN 1 ELSE 0 END)::int`,
      reEntries: sql<number>`SUM(CASE WHEN ${sessionsTable.reEntryNumber} > 0 THEN 1 ELSE 0 END)::int`,
    }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isStaff, false),
        gte(sessionsTable.entryTime, start), lte(sessionsTable.entryTime, end))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "queue_join"),
        gte(eventsTable.timestamp, start), lte(eventsTable.timestamp, end))
    ),
    db.select({ cnt: sql<number>`COUNT(DISTINCT ${sessionsTable.visitorId})` }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), ne(sessionsTable.reEntryNumber, 0),
        gte(sessionsTable.entryTime, start), lte(sessionsTable.entryTime, end))
    ),
  ]);

  const totalEntries = Number(entriesResult[0]?.cnt ?? 0);
  const totalExits = Number(exitsResult[0]?.cnt ?? 0);
  const staffCount = Number(staffResult[0]?.cnt ?? 0);
  const sessions = sessionsResult[0];
  const uniqueVisitors = Number(sessions?.total ?? 0);
  const converted = Number(sessions?.converted ?? 0);
  const avgDwell = Number(sessions?.avgDwell ?? 0);
  const abandoned = Number(sessions?.abandonCount ?? 0);
  const reEntries = Number(reEntryResult[0]?.cnt ?? 0);
  const queueDepth = Number(queueResult[0]?.cnt ?? 0);

  const currentOccupancy = Math.max(0, totalEntries - totalExits);
  const conversionRate = uniqueVisitors > 0 ? converted / uniqueVisitors : 0;
  const abandonmentRate = uniqueVisitors > 0 ? abandoned / uniqueVisitors : 0;

  return res.json({
    store_id: storeId,
    period_start: start.toISOString(),
    period_end: end.toISOString(),
    unique_visitors: uniqueVisitors,
    total_entries: totalEntries,
    total_exits: totalExits,
    staff_count: staffCount,
    conversion_rate: conversionRate,
    avg_dwell_time_seconds: avgDwell,
    abandonment_rate: abandonmentRate,
    current_occupancy: currentOccupancy,
    peak_occupancy: currentOccupancy,
    queue_depth: queueDepth,
    re_entry_count: reEntries,
  });
});

router.get("/:storeId/funnel", async (req, res) => {
  const { storeId } = req.params;
  const { start, end } = dateRange(req.query.from as string, req.query.to as string);

  const [entered, browsed, queueJoined, purchased] = await Promise.all([
    db.select({ cnt: count() }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isStaff, false), gte(sessionsTable.entryTime, start), lte(sessionsTable.entryTime, end))
    ),
    db.select({ cnt: count() }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isStaff, false),
        sql`array_length(${sessionsTable.zonesVisited}, 1) > 0`,
        gte(sessionsTable.entryTime, start), lte(sessionsTable.entryTime, end))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "queue_join"),
        gte(eventsTable.timestamp, start), lte(eventsTable.timestamp, end))
    ),
    db.select({ cnt: count() }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isConverted, true),
        gte(sessionsTable.entryTime, start), lte(sessionsTable.entryTime, end))
    ),
  ]);

  const enteredCount = Number(entered[0]?.cnt ?? 0);
  const browsedCount = Number(browsed[0]?.cnt ?? enteredCount);
  const queueCount = Number(queueJoined[0]?.cnt ?? 0);
  const purchasedCount = Number(purchased[0]?.cnt ?? 0);

  const stages = [
    { stage: "Entered Store", count: enteredCount, drop_off: 0, avg_time_in_stage_seconds: null },
    { stage: "Browsed Zones", count: browsedCount, drop_off: enteredCount > 0 ? (enteredCount - browsedCount) / enteredCount : 0, avg_time_in_stage_seconds: null },
    { stage: "Reached Checkout", count: queueCount, drop_off: browsedCount > 0 ? (browsedCount - queueCount) / browsedCount : 0, avg_time_in_stage_seconds: null },
    { stage: "Completed Purchase", count: purchasedCount, drop_off: queueCount > 0 ? (queueCount - purchasedCount) / queueCount : 0, avg_time_in_stage_seconds: null },
  ];

  return res.json({ store_id: storeId, stages });
});

router.get("/:storeId/heatmap", async (req, res) => {
  const { storeId } = req.params;
  const { start, end } = dateRange(req.query.from as string, req.query.to as string);

  const zoneStats = await db
    .select({
      zoneId: zoneDwellsTable.zoneId,
      zoneName: zoneDwellsTable.zoneName,
      totalDwell: sql<number>`COALESCE(SUM(${zoneDwellsTable.dwellSeconds}), 0)`,
      visitorCount: sql<number>`COUNT(DISTINCT ${zoneDwellsTable.visitorId})`,
      avgDwell: sql<number>`COALESCE(AVG(${zoneDwellsTable.dwellSeconds}), 0)`,
    })
    .from(zoneDwellsTable)
    .where(
      and(eq(zoneDwellsTable.storeId, storeId), gte(zoneDwellsTable.enterTime, start), lte(zoneDwellsTable.enterTime, end))
    )
    .groupBy(zoneDwellsTable.zoneId, zoneDwellsTable.zoneName);

  const maxDwell = zoneStats.reduce((m, z) => Math.max(m, Number(z.totalDwell)), 1);

  const zones = zoneStats.map((z) => {
    const totalDwell = Number(z.totalDwell);
    const intensity = totalDwell / maxDwell;
    return {
      zone_id: z.zoneId,
      zone_name: z.zoneName || z.zoneId,
      total_dwell_seconds: totalDwell,
      visitor_count: Number(z.visitorCount),
      avg_dwell_seconds: Number(z.avgDwell),
      intensity,
      is_dead_zone: intensity < 0.1 && Number(z.visitorCount) < 3,
    };
  });

  return res.json({ store_id: storeId, zones });
});

router.get("/:storeId/anomalies", async (req, res) => {
  const { storeId } = req.params;
  const { severity, resolved, limit } = req.query;

  let query = db.select().from(anomaliesTable).where(eq(anomaliesTable.storeId, storeId));

  const conditions: ReturnType<typeof eq>[] = [eq(anomaliesTable.storeId, storeId)];
  if (severity) conditions.push(eq(anomaliesTable.severity, severity as string));
  if (resolved !== undefined) conditions.push(eq(anomaliesTable.resolved, resolved === "true"));

  const rows = await db
    .select()
    .from(anomaliesTable)
    .where(and(...conditions))
    .orderBy(desc(anomaliesTable.detectedAt))
    .limit(Number(limit ?? 50));

  return res.json(rows.map((a) => ({
    id: a.id,
    store_id: a.storeId,
    anomaly_type: a.anomalyType,
    severity: a.severity,
    detected_at: a.detectedAt.toISOString(),
    description: a.description,
    suggested_action: a.suggestedAction,
    metric_value: a.metricValue,
    threshold_value: a.thresholdValue,
    resolved: a.resolved,
    zone_id: a.zoneId,
  })));
});

router.get("/:storeId/sessions", async (req, res) => {
  const { storeId } = req.params;
  const { start, end } = dateRange(req.query.from as string, req.query.to as string);
  const limit = Number(req.query.limit ?? 100);

  const rows = await db
    .select()
    .from(sessionsTable)
    .where(and(eq(sessionsTable.storeId, storeId), gte(sessionsTable.entryTime, start), lte(sessionsTable.entryTime, end)))
    .orderBy(desc(sessionsTable.entryTime))
    .limit(limit);

  return res.json(rows.map((s) => ({
    session_id: s.sessionId,
    visitor_id: s.visitorId,
    store_id: s.storeId,
    entry_time: s.entryTime.toISOString(),
    exit_time: s.exitTime?.toISOString() ?? null,
    dwell_seconds: s.dwellSeconds,
    zones_visited: s.zonesVisited ?? [],
    is_converted: s.isConverted,
    is_staff: s.isStaff,
    re_entry_number: s.reEntryNumber,
    track_ids: s.trackIds ?? [],
  })));
});

router.get("/:storeId/timeline", async (req, res) => {
  const { storeId } = req.params;
  const dateStr = req.query.date as string;
  const targetDate = dateStr ? new Date(dateStr) : new Date();
  const start = new Date(targetDate.getFullYear(), targetDate.getMonth(), targetDate.getDate());
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);

  const rows = await db.execute(sql`
    WITH hours AS (
      SELECT generate_series(${start}::timestamptz, ${end}::timestamptz - interval '1 hour', interval '1 hour') AS hour
    ),
    entries AS (
      SELECT date_trunc('hour', timestamp) AS h, COUNT(*) AS cnt
      FROM ${eventsTable}
      WHERE store_id = ${storeId} AND event_type = 'entry' AND is_staff = false
        AND timestamp >= ${start} AND timestamp < ${end}
      GROUP BY h
    ),
    exits AS (
      SELECT date_trunc('hour', timestamp) AS h, COUNT(*) AS cnt
      FROM ${eventsTable}
      WHERE store_id = ${storeId} AND event_type = 'exit' AND is_staff = false
        AND timestamp >= ${start} AND timestamp < ${end}
      GROUP BY h
    ),
    purchases AS (
      SELECT date_trunc('hour', timestamp) AS h, COUNT(*) AS cnt
      FROM ${eventsTable}
      WHERE store_id = ${storeId} AND event_type = 'purchase'
        AND timestamp >= ${start} AND timestamp < ${end}
      GROUP BY h
    )
    SELECT
      hours.hour::text AS hour,
      COALESCE(entries.cnt, 0)::int AS entries,
      COALESCE(exits.cnt, 0)::int AS exits,
      COALESCE(purchases.cnt, 0)::int AS conversions,
      (SUM(COALESCE(entries.cnt, 0) - COALESCE(exits.cnt, 0)) OVER (ORDER BY hours.hour))::int AS occupancy
    FROM hours
    LEFT JOIN entries ON entries.h = hours.hour
    LEFT JOIN exits ON exits.h = hours.hour
    LEFT JOIN purchases ON purchases.h = hours.hour
    ORDER BY hours.hour
  `);

  return res.json(rows.rows.map((r: Record<string, unknown>) => ({
    hour: r.hour,
    entries: Number(r.entries),
    exits: Number(r.exits),
    occupancy: Math.max(0, Number(r.occupancy)),
    conversions: Number(r.conversions),
  })));
});

router.get("/:storeId/summary", async (req, res) => {
  const { storeId } = req.params;
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const todayEnd = new Date(todayStart.getTime() + 24 * 60 * 60 * 1000);
  const yesterdayStart = new Date(todayStart.getTime() - 24 * 60 * 60 * 1000);
  const sevenDaysAgo = new Date(todayStart.getTime() - 7 * 24 * 60 * 60 * 1000);

  const [store, todaySessions, yesterdaySessions, sevenDaySessions, activeAnomalies, queueDepth, entries, exits] = await Promise.all([
    db.select().from(storesTable).where(eq(storesTable.id, storeId)).limit(1),
    db.select({
      total: count(),
      converted: sql<number>`SUM(CASE WHEN ${sessionsTable.isConverted} THEN 1 ELSE 0 END)::int`,
      avgDwell: avg(sessionsTable.dwellSeconds),
    }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isStaff, false),
        gte(sessionsTable.entryTime, todayStart), lte(sessionsTable.entryTime, todayEnd))
    ),
    db.select({ total: count() }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isStaff, false),
        gte(sessionsTable.entryTime, yesterdayStart), lte(sessionsTable.entryTime, todayStart))
    ),
    db.select({
      convRate: sql<number>`AVG(CASE WHEN ${sessionsTable.isConverted} THEN 1.0 ELSE 0.0 END)`,
    }).from(sessionsTable).where(
      and(eq(sessionsTable.storeId, storeId), eq(sessionsTable.isStaff, false),
        gte(sessionsTable.entryTime, sevenDaysAgo), lte(sessionsTable.entryTime, todayStart))
    ),
    db.select({ cnt: count() }).from(anomaliesTable).where(
      and(eq(anomaliesTable.storeId, storeId), eq(anomaliesTable.resolved, false))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "queue_join"),
        gte(eventsTable.timestamp, todayStart))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "entry"),
        eq(eventsTable.isStaff, false), gte(eventsTable.timestamp, todayStart))
    ),
    db.select({ cnt: count() }).from(eventsTable).where(
      and(eq(eventsTable.storeId, storeId), eq(eventsTable.eventType, "exit"),
        eq(eventsTable.isStaff, false), gte(eventsTable.timestamp, todayStart))
    ),
  ]);

  const todayVisitors = Number(todaySessions[0]?.total ?? 0);
  const todayConversions = Number(todaySessions[0]?.converted ?? 0);
  const conversionRate = todayVisitors > 0 ? todayConversions / todayVisitors : 0;
  const avgDwell = Number(todaySessions[0]?.avgDwell ?? 0);
  const yesterdayVisitors = Number(yesterdaySessions[0]?.total ?? 1);
  const sevenDayConv = Number(sevenDaySessions[0]?.convRate ?? 0);
  const currentOccupancy = Math.max(0, Number(entries[0]?.cnt ?? 0) - Number(exits[0]?.cnt ?? 0));

  return res.json({
    store_id: storeId,
    store_name: store[0]?.name ?? storeId,
    today_visitors: todayVisitors,
    today_conversions: todayConversions,
    conversion_rate: conversionRate,
    avg_dwell_seconds: avgDwell,
    current_occupancy: currentOccupancy,
    active_anomalies: Number(activeAnomalies[0]?.cnt ?? 0),
    queue_depth: Number(queueDepth[0]?.cnt ?? 0),
    vs_yesterday_visitors: yesterdayVisitors > 0 ? (todayVisitors - yesterdayVisitors) / yesterdayVisitors : 0,
    vs_7day_conversion: sevenDayConv > 0 ? conversionRate - sevenDayConv : 0,
  });
});

export default router;
