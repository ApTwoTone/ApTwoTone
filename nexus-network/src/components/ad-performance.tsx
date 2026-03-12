"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface AdMetrics {
  total_spend: number;
  total_impressions: number;
  total_reach: number;
  total_clicks: number;
  total_leads: number;
  avg_cpl: number;
  avg_ctr: number;
  avg_cpc: number;
  campaigns: CampaignMetric[];
}

interface CampaignMetric {
  campaign_name: string;
  campaign_id: string;
  spend: number;
  impressions: number;
  clicks: number;
  leads: number;
  cpl: number;
  ctr: number;
}

interface ResearchReport {
  id: number;
  topic: string;
  report_type: string;
  key_findings: string;
  recommended_action: string;
  content: string;
  model_used: string;
  tokens_used: number;
  created_at: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function MetricCard({
  label,
  value,
  sub,
  color = "text-foreground",
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-muted mt-1">{sub}</p>}
    </div>
  );
}

function timeAgo(ts: string): string {
  if (!ts) return "—";
  const ago = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (ago < 0 || isNaN(ago)) return "—";
  if (ago < 60) return `${ago}s ago`;
  if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
  if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`;
  return `${Math.floor(ago / 86400)}d ago`;
}

const REPORT_BADGES: Record<string, string> = {
  audience: "bg-blue-500/20 text-blue-300",
  competitor: "bg-orange-500/20 text-orange-300",
  creative: "bg-purple-500/20 text-purple-300",
};

// ── Main Component ───────────────────────────────────────────────────────────

export function AdPerformance() {
  const [tab, setTab] = useState<"metrics" | "research">("metrics");
  const [metrics, setMetrics] = useState<AdMetrics | null>(null);
  const [weeklyAvg, setWeeklyAvg] = useState<Record<string, number> | null>(null);
  const [report, setReport] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState<"today" | "yesterday" | "7day_avg">("today");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Research state
  const [reports, setReports] = useState<ResearchReport[]>([]);
  const [expandedReport, setExpandedReport] = useState<number | null>(null);
  const [runningResearch, setRunningResearch] = useState(false);

  const fetchMetrics = useCallback(async () => {
    try {
      const [raw, avg, rep] = await Promise.all([
        fetchApi<Record<string, unknown>>(`/api/ads/metrics?period=${period}`).catch(() => null),
        fetchApi<Record<string, number>>("/api/ads/metrics?period=7day_avg").catch(() => null),
        fetchApi<{ report: string }>("/api/ads/report/daily").catch(() => null),
      ]);
      if (raw) {
        const campaigns = Array.isArray(raw.campaigns) ? raw.campaigns as CampaignMetric[] : [];
        setMetrics({
          total_spend: Number(raw.total_spend ?? raw.spend ?? 0),
          total_impressions: Number(raw.total_impressions ?? raw.impressions ?? 0),
          total_reach: Number(raw.total_reach ?? raw.reach ?? 0),
          total_clicks: Number(raw.total_clicks ?? raw.clicks ?? 0),
          total_leads: Number(raw.total_leads ?? raw.leads ?? 0),
          avg_cpl: Number(raw.avg_cpl ?? raw.cpl ?? 0),
          avg_ctr: Number(raw.avg_ctr ?? raw.ctr ?? 0),
          avg_cpc: Number(raw.avg_cpc ?? raw.cpc ?? 0),
          campaigns,
        });
      }
      setWeeklyAvg(avg);
      setReport(rep?.report || "");
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [period]);

  const fetchResearch = useCallback(async () => {
    try {
      const resp = await fetchApi<{ reports: ResearchReport[] }>("/api/research/reports?limit=20");
      setReports(resp.reports || []);
    } catch {
      // silent — research tab is supplementary
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchMetrics();
    fetchResearch();
    // Auto-refresh every 60s
    const iv = setInterval(() => {
      fetchMetrics();
      fetchResearch();
    }, 60000);
    return () => clearInterval(iv);
  }, [fetchMetrics, fetchResearch]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchMetrics();
    await fetchResearch();
    setRefreshing(false);
  };

  const handleRunResearch = async () => {
    setRunningResearch(true);
    try {
      await postApi("/api/research/run", {});
      // Wait a moment, then refresh
      setTimeout(fetchResearch, 5000);
    } finally {
      setRunningResearch(false);
    }
  };

  const cplColor = (cpl: number) => {
    if (cpl === 0) return "text-muted";
    if (cpl <= 5) return "text-success";
    if (cpl <= 8) return "text-warning";
    return "text-danger";
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-muted">Loading ad metrics...</p>
      </div>
    );
  }

  const m = metrics || {
    total_spend: 0, total_impressions: 0, total_reach: 0,
    total_clicks: 0, total_leads: 0, avg_cpl: 0, avg_ctr: 0, avg_cpc: 0,
    campaigns: [],
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      {/* Header with tabs */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold">Ad Performance</h2>
          <p className="text-sm text-muted mt-1">
            {lastUpdated
              ? `Last updated: ${lastUpdated.toLocaleTimeString()}`
              : "Facebook Ads — target CPL: $2.34–$4.93"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Tab toggle */}
          <div className="flex gap-1 bg-card rounded-lg border border-border p-1">
            <button
              onClick={() => setTab("metrics")}
              className={`px-3 py-1 text-xs rounded-md transition-colors ${
                tab === "metrics" ? "bg-accent text-white" : "text-muted hover:text-foreground"
              }`}
            >
              Metrics
            </button>
            <button
              onClick={() => setTab("research")}
              className={`px-3 py-1 text-xs rounded-md transition-colors ${
                tab === "research" ? "bg-accent text-white" : "text-muted hover:text-foreground"
              }`}
            >
              Research
            </button>
          </div>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="px-3 py-1.5 text-xs rounded-lg bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50"
          >
            {refreshing ? "Refreshing..." : "Refresh Now"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-3 mb-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* ── Metrics Tab ─────────────────────────────────────────────────── */}
      {tab === "metrics" && (
        <>
          {/* Period toggle */}
          <div className="flex gap-1 bg-card rounded-lg border border-border p-1 w-fit mb-6">
            {(["today", "yesterday", "7day_avg"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`px-3 py-1 text-xs rounded-md transition-colors ${
                  period === p ? "bg-accent text-white" : "text-muted hover:text-foreground"
                }`}
              >
                {p === "7day_avg" ? "7-Day Avg" : p.charAt(0).toUpperCase() + p.slice(1)}
              </button>
            ))}
          </div>

          {/* Top metrics */}
          <div className="grid grid-cols-5 gap-4 mb-6">
            <MetricCard
              label="Cost Per Lead"
              value={m.avg_cpl > 0 ? `$${m.avg_cpl.toFixed(2)}` : "—"}
              color={cplColor(m.avg_cpl)}
              sub={weeklyAvg ? `7d avg: $${(weeklyAvg.avg_cpl || 0).toFixed(2)}` : undefined}
            />
            <MetricCard label="Total Spend" value={`$${m.total_spend.toFixed(2)}`} sub="Budget: $100/week" />
            <MetricCard label="Leads" value={String(m.total_leads)} color={m.total_leads > 0 ? "text-success" : "text-muted"} />
            <MetricCard label="Reach" value={m.total_reach > 1000 ? `${(m.total_reach / 1000).toFixed(1)}K` : String(m.total_reach)} />
            <MetricCard label="CTR" value={m.avg_ctr > 0 ? `${m.avg_ctr.toFixed(2)}%` : "—"} color={m.avg_ctr >= 2 ? "text-success" : m.avg_ctr >= 1 ? "text-warning" : "text-muted"} />
          </div>

          <div className="grid grid-cols-2 gap-6">
            {/* Campaign breakdown */}
            <div className="bg-card rounded-xl border border-border overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <h3 className="text-sm font-semibold">Campaign Breakdown</h3>
              </div>
              {m.campaigns.length > 0 ? (
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-border text-left">
                      <th className="px-4 py-2 text-xs font-medium text-muted">Campaign</th>
                      <th className="px-4 py-2 text-xs font-medium text-muted">Spend</th>
                      <th className="px-4 py-2 text-xs font-medium text-muted">Leads</th>
                      <th className="px-4 py-2 text-xs font-medium text-muted">CPL</th>
                      <th className="px-4 py-2 text-xs font-medium text-muted">CTR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.campaigns.map((c, i) => (
                      <tr key={i} className="border-b border-border hover:bg-card-hover">
                        <td className="px-4 py-2 text-sm truncate max-w-[200px]">{c.campaign_name}</td>
                        <td className="px-4 py-2 text-sm">${c.spend.toFixed(2)}</td>
                        <td className="px-4 py-2 text-sm">{c.leads}</td>
                        <td className={`px-4 py-2 text-sm font-medium ${cplColor(c.cpl)}`}>
                          {c.cpl > 0 ? `$${c.cpl.toFixed(2)}` : "—"}
                        </td>
                        <td className="px-4 py-2 text-sm">{c.ctr.toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="px-4 py-8 text-center text-muted text-sm">
                  No campaign data yet — Ad Monitor runs every 6 hours
                </div>
              )}
            </div>

            {/* Daily report */}
            <div className="bg-card rounded-xl border border-border overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <h3 className="text-sm font-semibold">Latest Report & Recommendations</h3>
              </div>
              <div className="p-4">
                {report ? (
                  <pre className="text-xs text-muted whitespace-pre-wrap font-sans leading-relaxed">{report}</pre>
                ) : (
                  <p className="text-sm text-muted text-center py-4">
                    No report generated yet — will appear after first Ad Monitor cycle
                  </p>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ── Research Tab ────────────────────────────────────────────────── */}
      {tab === "research" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted">
              AI Market Analysis — generated daily at 6am PT by Audience Researcher agent
            </p>
            <button
              onClick={handleRunResearch}
              disabled={runningResearch}
              className="px-3 py-1.5 text-xs rounded-lg bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50"
            >
              {runningResearch ? "Running..." : "Run Research Now"}
            </button>
          </div>

          {reports.length === 0 ? (
            <div className="bg-card rounded-xl border border-border p-8 text-center text-muted">
              No research reports yet — first batch generates at 6am PT daily, or click "Run Research Now"
            </div>
          ) : (
            <div className="space-y-3">
              {reports.map((r) => {
                const badgeStyle = REPORT_BADGES[r.report_type] || "bg-gray-500/20 text-gray-300";
                const isExpanded = expandedReport === r.id;
                return (
                  <div key={r.id} className="bg-card rounded-xl border border-border overflow-hidden">
                    <div
                      className="p-4 cursor-pointer hover:bg-card-hover transition-colors"
                      onClick={() => setExpandedReport(isExpanded ? null : r.id)}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className={`text-[10px] px-2 py-0.5 rounded-full ${badgeStyle}`}>
                            {r.report_type}
                          </span>
                          <h4 className="text-sm font-semibold">{r.topic}</h4>
                        </div>
                        <div className="flex items-center gap-3 text-xs text-muted">
                          {r.model_used && <span>{r.model_used}</span>}
                          <span>{timeAgo(r.created_at)}</span>
                          <span>{isExpanded ? "▲" : "▼"}</span>
                        </div>
                      </div>
                      <p className="text-xs text-muted line-clamp-2">{r.key_findings}</p>
                    </div>
                    {isExpanded && (
                      <div className="px-4 pb-4 border-t border-border pt-3">
                        <pre className="text-xs text-muted whitespace-pre-wrap font-sans leading-relaxed">
                          {r.content}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
