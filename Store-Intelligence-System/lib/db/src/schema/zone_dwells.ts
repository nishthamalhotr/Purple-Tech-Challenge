import { pgTable, text, timestamp, real, integer, index } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const zoneDwellsTable = pgTable(
  "zone_dwells",
  {
    id: text("id").primaryKey(),
    sessionId: text("session_id").notNull(),
    visitorId: text("visitor_id").notNull(),
    storeId: text("store_id").notNull(),
    zoneId: text("zone_id").notNull(),
    zoneName: text("zone_name").notNull().default(""),
    enterTime: timestamp("enter_time", { withTimezone: true }).notNull(),
    exitTime: timestamp("exit_time", { withTimezone: true }),
    dwellSeconds: real("dwell_seconds"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index("zone_dwells_store_id_idx").on(t.storeId),
    index("zone_dwells_zone_id_idx").on(t.zoneId),
    index("zone_dwells_session_id_idx").on(t.sessionId),
  ]
);

export const insertZoneDwellSchema = createInsertSchema(zoneDwellsTable).omit({ createdAt: true });
export type InsertZoneDwell = z.infer<typeof insertZoneDwellSchema>;
export type ZoneDwell = typeof zoneDwellsTable.$inferSelect;
