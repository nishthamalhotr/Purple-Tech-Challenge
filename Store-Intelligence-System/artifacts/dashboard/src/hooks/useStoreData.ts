import { useQuery } from "@tanstack/react-query";

const VITE_API_URL = import.meta.env.VITE_API_URL as string | undefined;
const API_BASE = VITE_API_URL ? VITE_API_URL.replace(/\/+$/, "") : "/api";
const STORE_ID = "store_001";
const REFETCH_MS = 10_000;

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export interface StoreSummary {
  store_id: string;
  store_name: string;
  today_visitors: number;
  today_conversions: number;
  conversion_rate: number;
  avg_dwell_seconds: number;
  current_occupancy: number;
  active_anomalies: number;
  queue_depth: number;
  vs_yesterday_visitors: number;
  vs_7day_conversion: number;
}

export interface StoreMetrics {
  store_id: string;
  unique_visitors: number;
  total_entries: number;
  total_exits: number;
  staff_count: number;
  conversion_rate: number;
  avg_dwell_time_seconds: number;
  abandonment_rate: number;
  current_occupancy: number;
  queue_depth: number;
  re_entry_count: number;
}

export interface FunnelStage {
  stage: string;
  count: number;
  drop_off: number;
}

export interface FunnelData {
  store_id: string;
  stages: FunnelStage[];
}

export interface ZoneHeat {
  zone_id: string;
  zone_name: string;
  total_dwell_seconds: number;
  visitor_count: number;
  avg_dwell_seconds: number;
  intensity: number;
  is_dead_zone: boolean;
}

export interface HeatmapData {
  store_id: string;
  zones: ZoneHeat[];
}

export interface AnomalyRecord {
  id: string;
  store_id: string;
  anomaly_type: string;
  severity: string;
  detected_at: string;
  description: string;
  suggested_action: string;
  metric_value: number | null;
  threshold_value: number | null;
  resolved: boolean;
  zone_id: string | null;
}

export interface TimelinePoint {
  hour: string;
  entries: number;
  exits: number;
  occupancy: number;
  conversions: number;
}

export function useSummary() {
  return useQuery<StoreSummary>({
    queryKey: ["summary", STORE_ID],
    queryFn: () => apiFetch(`/stores/${STORE_ID}/summary`),
    refetchInterval: REFETCH_MS,
  });
}

export function useMetrics() {
  return useQuery<StoreMetrics>({
    queryKey: ["metrics", STORE_ID],
    queryFn: () => apiFetch(`/stores/${STORE_ID}/metrics`),
    refetchInterval: REFETCH_MS,
  });
}

export function useFunnel() {
  return useQuery<FunnelData>({
    queryKey: ["funnel", STORE_ID],
    queryFn: () => apiFetch(`/stores/${STORE_ID}/funnel`),
    refetchInterval: REFETCH_MS,
  });
}

export function useHeatmap() {
  return useQuery<HeatmapData>({
    queryKey: ["heatmap", STORE_ID],
    queryFn: () => apiFetch(`/stores/${STORE_ID}/heatmap`),
    refetchInterval: REFETCH_MS,
  });
}

export function useAnomalies() {
  return useQuery<AnomalyRecord[]>({
    queryKey: ["anomalies", STORE_ID],
    queryFn: () => apiFetch(`/stores/${STORE_ID}/anomalies?resolved=false&limit=20`),
    refetchInterval: REFETCH_MS,
  });
}

export function useTimeline() {
  return useQuery<TimelinePoint[]>({
    queryKey: ["timeline", STORE_ID],
    queryFn: () => apiFetch(`/stores/${STORE_ID}/timeline`),
    refetchInterval: REFETCH_MS,
  });
}
