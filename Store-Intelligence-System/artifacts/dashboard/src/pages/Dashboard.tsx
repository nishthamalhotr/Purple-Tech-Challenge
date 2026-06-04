import { useSummary, useMetrics, useFunnel, useHeatmap, useAnomalies, useTimeline } from "@/hooks/useStoreData";
import {
  AreaChart, Area, BarChart, Bar, FunnelChart, Funnel, LabelList,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from "recharts";
import { Users, ShoppingBag, Clock, AlertTriangle, TrendingUp, TrendingDown, Activity, UserCheck } from "lucide-react";

function pct(v: number) { return `${(v * 100).toFixed(1)}%`; }
function secs(v: number) { return v >= 60 ? `${Math.round(v / 60)}m ${Math.round(v % 60)}s` : `${Math.round(v)}s`; }
function fmt(v: number) { return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v); }

function StatCard({
  title, value, sub, icon: Icon, trend, trendLabel, color = "purple",
}: {
  title: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  trend?: number;
  trendLabel?: string;
  color?: string;
}) {
  const colors: Record<string, string> = {
    purple: "bg-purple-50 text-purple-600",
    green: "bg-green-50 text-green-600",
    blue: "bg-blue-50 text-blue-600",
    amber: "bg-amber-50 text-amber-600",
    red: "bg-red-50 text-red-600",
  };
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{title}</p>
          <p className="mt-1 text-2xl font-bold text-gray-900">{value}</p>
          {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
        </div>
        <div className={`p-2 rounded-lg ${colors[color]}`}>
          <Icon size={20} />
        </div>
      </div>
      {trend !== undefined && (
        <div className="mt-3 flex items-center gap-1 text-xs">
          {trend >= 0 ? (
            <TrendingUp size={12} className="text-green-500" />
          ) : (
            <TrendingDown size={12} className="text-red-500" />
          )}
          <span className={trend >= 0 ? "text-green-600" : "text-red-600"}>
            {trend >= 0 ? "+" : ""}{pct(trend)}
          </span>
          {trendLabel && <span className="text-gray-400">{trendLabel}</span>}
        </div>
      )}
    </div>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const map: Record<string, string> = {
    high: "bg-red-100 text-red-700 border border-red-200",
    medium: "bg-amber-100 text-amber-700 border border-amber-200",
    low: "bg-blue-100 text-blue-700 border border-blue-200",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${map[severity] ?? map.low}`}>
      {severity.toUpperCase()}
    </span>
  );
}

const ANOMALY_ICONS: Record<string, string> = {
  queue_spike: "🔴",
  conversion_drop: "📉",
  dead_zone: "🟡",
  high_abandonment: "🚪",
  unusual_traffic: "📊",
};

const HEATMAP_COLORS = [
  "#f5f0ff", "#e9d5ff", "#d8b4fe", "#c084fc", "#a855f7", "#9333ea", "#7c3aed",
];

function intensityColor(intensity: number, isDead: boolean) {
  if (isDead) return "#fee2e2";
  const idx = Math.min(HEATMAP_COLORS.length - 1, Math.round(intensity * (HEATMAP_COLORS.length - 1)));
  return HEATMAP_COLORS[idx];
}

export default function Dashboard() {
  const summary = useSummary();
  const metrics = useMetrics();
  const funnel = useFunnel();
  const heatmap = useHeatmap();
  const anomalies = useAnomalies();
  const timeline = useTimeline();

  const s = summary.data;
  const m = metrics.data;

  const timelineData = (timeline.data ?? []).map((t) => ({
    ...t,
    hour: t.hour ? t.hour.slice(11, 16) : "",
  }));

  const funnelData = (funnel.data?.stages ?? []).map((st) => ({
    name: st.stage,
    value: st.count,
    drop: pct(st.drop_off),
  }));

  const FUNNEL_COLORS = ["#9333ea", "#a855f7", "#c084fc", "#e9d5ff"];

  const heatZones = heatmap.data?.zones ?? [];

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-600 to-purple-400 flex items-center justify-center">
              <Activity size={16} className="text-white" />
            </div>
            <div>
              <h1 className="text-base font-bold text-gray-900">Store Intelligence</h1>
              <p className="text-xs text-gray-500">{s?.store_name ?? "Loading…"} · Purplle Tech Challenge 2026</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 text-xs text-green-600 font-medium">
              <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
              Live · 10s refresh
            </span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {/* KPI Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            title="Today's Visitors"
            value={fmt(s?.today_visitors ?? 0)}
            sub={`${s?.current_occupancy ?? 0} in store now`}
            icon={Users}
            trend={s?.vs_yesterday_visitors}
            trendLabel="vs yesterday"
            color="purple"
          />
          <StatCard
            title="Conversion Rate"
            value={pct(s?.conversion_rate ?? 0)}
            sub={`${s?.today_conversions ?? 0} purchases`}
            icon={ShoppingBag}
            trend={s?.vs_7day_conversion}
            trendLabel="vs 7-day avg"
            color="green"
          />
          <StatCard
            title="Avg Dwell Time"
            value={secs(s?.avg_dwell_seconds ?? 0)}
            sub="per visitor session"
            icon={Clock}
            color="blue"
          />
          <StatCard
            title="Active Alerts"
            value={String(s?.active_anomalies ?? 0)}
            sub={`${s?.queue_depth ?? 0} in queue`}
            icon={AlertTriangle}
            color={s?.active_anomalies ? "red" : "amber"}
          />
        </div>

        {/* Secondary metrics */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Staff On Floor</p>
            <p className="text-xl font-bold text-gray-900 mt-1">{m?.staff_count ?? "–"}</p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Re-Entry Rate</p>
            <p className="text-xl font-bold text-gray-900 mt-1">
              {m ? pct(m.unique_visitors > 0 ? m.re_entry_count / m.unique_visitors : 0) : "–"}
            </p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Abandonment</p>
            <p className="text-xl font-bold text-gray-900 mt-1">{m ? pct(m.abandonment_rate) : "–"}</p>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">Total Entries</p>
            <p className="text-xl font-bold text-gray-900 mt-1">{fmt(m?.total_entries ?? 0)}</p>
          </div>
        </div>

        {/* Timeline */}
        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-800">Traffic Timeline — Today</h2>
            <div className="flex items-center gap-4 text-xs text-gray-500">
              <span className="flex items-center gap-1.5"><span className="w-3 h-2 rounded-sm bg-purple-500 inline-block" />Entries</span>
              <span className="flex items-center gap-1.5"><span className="w-3 h-2 rounded-sm bg-purple-200 inline-block" />Occupancy</span>
            </div>
          </div>
          {timelineData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-gray-400 text-sm">
              No traffic data yet — seed demo data to see charts
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={timelineData} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="entryGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#9333ea" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#9333ea" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="occGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#c084fc" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#c084fc" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="hour" tick={{ fontSize: 11, fill: "#9ca3af" }} />
                <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }} />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
                />
                <Area type="monotone" dataKey="occupancy" stroke="#c084fc" fill="url(#occGrad)" strokeWidth={1.5} name="Occupancy" />
                <Area type="monotone" dataKey="entries" stroke="#9333ea" fill="url(#entryGrad)" strokeWidth={2} name="Entries" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Funnel + Heatmap row */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Funnel */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-800 mb-4">Conversion Funnel</h2>
            {funnelData.length === 0 || funnelData.every(d => d.value === 0) ? (
              <div className="h-48 flex items-center justify-center text-gray-400 text-sm">
                No funnel data yet
              </div>
            ) : (
              <div className="space-y-3">
                {funnelData.map((stage, i) => {
                  const maxVal = funnelData[0]?.value || 1;
                  const width = maxVal > 0 ? (stage.value / maxVal) * 100 : 0;
                  return (
                    <div key={stage.name}>
                      <div className="flex justify-between text-xs mb-1">
                        <span className="text-gray-700 font-medium">{stage.name}</span>
                        <span className="text-gray-500">{fmt(stage.value)}</span>
                      </div>
                      <div className="h-8 bg-gray-100 rounded-lg overflow-hidden">
                        <div
                          className="h-full rounded-lg flex items-center justify-end pr-2 transition-all duration-500"
                          style={{
                            width: `${width}%`,
                            background: FUNNEL_COLORS[i] ?? FUNNEL_COLORS[3],
                          }}
                        >
                          {i > 0 && stage.drop !== "0.0%" && (
                            <span className="text-white text-xs font-medium">-{stage.drop}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Zone Heatmap */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-gray-800">Zone Dwell Heatmap</h2>
              <div className="flex items-center gap-1 text-xs text-gray-400">
                <span>Low</span>
                <div className="flex gap-0.5">
                  {HEATMAP_COLORS.map((c, i) => (
                    <div key={i} className="w-4 h-3 rounded-sm" style={{ background: c }} />
                  ))}
                </div>
                <span>High</span>
              </div>
            </div>
            {heatZones.length === 0 ? (
              <div className="h-48 flex items-center justify-center text-gray-400 text-sm">
                No zone data yet
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {heatZones.map((z) => (
                  <div
                    key={z.zone_id}
                    className="rounded-lg p-3 flex flex-col gap-1 border"
                    style={{
                      background: intensityColor(z.intensity, z.is_dead_zone),
                      borderColor: z.is_dead_zone ? "#fca5a5" : "transparent",
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-gray-800 truncate">{z.zone_name}</span>
                      {z.is_dead_zone && <span className="text-red-500 text-xs">⚠️</span>}
                    </div>
                    <span className="text-xs text-gray-500">{z.visitor_count} visitors</span>
                    <span className="text-xs text-gray-500">{secs(z.avg_dwell_seconds)} avg</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Anomaly Feed */}
        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-800">Anomaly Feed</h2>
            <span className="text-xs text-gray-400">{anomalies.data?.length ?? 0} active alerts</span>
          </div>
          {!anomalies.data || anomalies.data.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-gray-400">
              <span className="text-3xl mb-2">✅</span>
              <span className="text-sm">No active anomalies</span>
            </div>
          ) : (
            <div className="space-y-3">
              {anomalies.data.map((a) => (
                <div
                  key={a.id}
                  className="flex items-start gap-3 p-3 rounded-lg border border-gray-100 bg-gray-50 hover:bg-gray-100 transition-colors"
                >
                  <span className="text-xl mt-0.5">{ANOMALY_ICONS[a.anomaly_type] ?? "⚠️"}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <SeverityBadge severity={a.severity} />
                      <span className="text-xs font-medium text-gray-700 capitalize">
                        {a.anomaly_type.replace(/_/g, " ")}
                      </span>
                      <span className="text-xs text-gray-400 ml-auto">
                        {new Date(a.detected_at).toLocaleTimeString()}
                      </span>
                    </div>
                    <p className="text-xs text-gray-600">{a.description}</p>
                    <p className="text-xs text-purple-700 font-medium mt-1">→ {a.suggested_action}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
