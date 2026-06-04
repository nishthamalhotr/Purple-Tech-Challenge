import { pgTable, text, timestamp, boolean, real, index } from "drizzle-orm/pg-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod/v4";

export const anomaliesTable = pgTable(
  "anomalies",
  {
    id: text("id").primaryKey(),
    storeId: text("store_id").notNull(),
    anomalyType: text("anomaly_type").notNull(),
    severity: text("severity").notNull(),
    detectedAt: timestamp("detected_at", { withTimezone: true }).notNull().defaultNow(),
    description: text("description").notNull(),
    suggestedAction: text("suggested_action").notNull(),
    metricValue: real("metric_value"),
    thresholdValue: real("threshold_value"),
    resolved: boolean("resolved").notNull().default(false),
    zoneId: text("zone_id"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index("anomalies_store_id_idx").on(t.storeId),
    index("anomalies_detected_at_idx").on(t.detectedAt),
    index("anomalies_resolved_idx").on(t.resolved),
    index("anomalies_store_severity_idx").on(t.storeId, t.severity),
  ]
);

export const insertAnomalySchema = createInsertSchema(anomaliesTable).omit({ createdAt: true });
export type InsertAnomaly = z.infer<typeof insertAnomalySchema>;
export type Anomaly = typeof anomaliesTable.$inferSelect;
