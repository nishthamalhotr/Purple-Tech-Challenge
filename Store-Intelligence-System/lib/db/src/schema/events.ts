import { pgTable, text, timestamp, boolean, real, integer, jsonb, index, unique } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const eventsTable = pgTable(
  "events",
  {
    id: text("id").primaryKey(),
    storeId: text("store_id").notNull(),
    cameraId: text("camera_id").notNull(),
    eventType: text("event_type").notNull(),
    timestamp: timestamp("timestamp", { withTimezone: true }).notNull(),
    trackId: text("track_id").notNull(),
    visitorId: text("visitor_id"),
    zoneId: text("zone_id"),
    isStaff: boolean("is_staff").notNull().default(false),
    confidence: real("confidence").notNull().default(1.0),
    groupId: text("group_id"),
    bboxX: real("bbox_x"),
    bboxY: real("bbox_y"),
    bboxW: real("bbox_w"),
    bboxH: real("bbox_h"),
    metadata: jsonb("metadata"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index("events_store_id_idx").on(t.storeId),
    index("events_store_timestamp_idx").on(t.storeId, t.timestamp),
    index("events_event_type_idx").on(t.eventType),
    index("events_visitor_id_idx").on(t.visitorId),
    unique("events_id_unique").on(t.id),
  ]
);

export const insertEventSchema = createInsertSchema(eventsTable).omit({ createdAt: true });
export type InsertEvent = z.infer<typeof insertEventSchema>;
export type Event = typeof eventsTable.$inferSelect;
