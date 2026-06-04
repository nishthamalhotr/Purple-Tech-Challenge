import { pgTable, text, timestamp, boolean, real, integer, index } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const sessionsTable = pgTable(
  "visitor_sessions",
  {
    sessionId: text("session_id").primaryKey(),
    visitorId: text("visitor_id").notNull(),
    storeId: text("store_id").notNull(),
    entryTime: timestamp("entry_time", { withTimezone: true }).notNull(),
    exitTime: timestamp("exit_time", { withTimezone: true }),
    dwellSeconds: real("dwell_seconds"),
    zonesVisited: text("zones_visited").array().notNull().default([]),
    isConverted: boolean("is_converted").notNull().default(false),
    isStaff: boolean("is_staff").notNull().default(false),
    reEntryNumber: integer("re_entry_number").notNull().default(0),
    trackIds: text("track_ids").array().notNull().default([]),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index("sessions_store_id_idx").on(t.storeId),
    index("sessions_visitor_id_idx").on(t.visitorId),
    index("sessions_entry_time_idx").on(t.entryTime),
    index("sessions_store_entry_idx").on(t.storeId, t.entryTime),
  ]
);

export const insertSessionSchema = createInsertSchema(sessionsTable).omit({ createdAt: true, updatedAt: true });
export type InsertSession = z.infer<typeof insertSessionSchema>;
export type Session = typeof sessionsTable.$inferSelect;
