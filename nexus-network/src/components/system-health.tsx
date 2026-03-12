"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface HealthEvent {
  id: number;
  check_type: string;
  target: string;
  status: string;
  message: string;
  resolved_by: string;
  repair_task_id: string;
  detected_at: string;
  resolved_at: string;
}

interface RepairRecord {
  id: number;
  health_event_id: number;
  diagnosis: string;
  fix_approach: string;
  files_changed: string;
  status: string;
  model_used: string;
  tokens_used: number;
  duration_ms: number;
  created_at: string;
}

interface ModelPerf {
  model: string;
  provider: string;
  attempts: number;
  successes: number;
  success_rate: number;
  avg_latency: number;
  total_tokens: number;
}

interface BrainStats {
  brain: Record<string, number>;
  scanner: { recent_events: number; recent_repairs: number };
  model_performance: ModelPerf[];
}

interface ScanResult {
  issues_found: number;
  issues: HealthEvent[];
}

interface ProviderCap {
  provider: string;
  requests_today: number;
  tokens_today: number;
  daily_limit: number;
  threshold_pct: number;
  used_pct: number;
  hours_until_cap: number;
  status: string;
}

interface SystemInfo {
  cpu_pct: number;
  ram_used_mb: number;
  ram_total_mb: number;
  ram_pct: number;
  db_size_mb: number;
  api_calls_today: number;
  tokens_today: number;
  provider_caps: ProviderCap[];
  uptime_seconds: number;
  git_branch: string;
  git_hash: string;
  git_message: string;
  last_error: { message: string; timestamp: string };
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  ok: "text-emerald-400 bg-emerald-500/10",
  warning: "text-amber-400 bg-amber-500/10",
  error: "text-red-400 bg-red-500/10",
  critical: "text-red-500 bg-red-600/20",
  success: "text-emerald-400 bg-emerald-500/10",
  failed: "text-red-400 bg-red-500/10",
  escalated: "text-amber-400 bg-amber-500/10",
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || "text-zinc-400 bg-zinc-500/10";
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}

function timeAgo(ts: string): string {
  if (!ts) return "—";
  const raw = Number(ts);
  const ms = !isNaN(raw)
    ? (raw < 1e12 ? raw * 1000 : raw)
    : new Date(ts).getTime();
  if (!Number.isFinite(ms)) return "—";
  const diff = Math.max(0, Date.now() - ms);
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function formatUptime(secs: number): string {
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
  return `${Math.floor(secs / 86400)}d ${Math.floor((secs % 86400) / 3600)}h`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

// ── Component ────────────────────────────────────────────────────────────────

export function SystemHealth() {
  const [events, setEvents] = useState<HealthEvent[]>([]);
  const [repairs, setRepairs] = useState<RepairRecord[]>([]);
  const [brainStats, setBrainStats] = useState<BrainStats | null>(null);
  const [sysInfo, setSysInfo] = useState<SystemInfo | null>(null);
  const [scanning, setScanning] = useState(false);
  const [lastScan, setLastScan] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [evts, reps, stats, sys] = await Promise.all([
        fetchApi<{ events: HealthEvent[] }>("/api/health/events?limit=50"),
        fetchApi<{ repairs: RepairRecord[] }>("/api/health/repairs?limit=20"),
        fetchApi<BrainStats>("/api/health/brain-stats"),
        fetchApi<SystemInfo>("/api/health/system-info"),
      ]);
      setEvents(evts.events || []);
      setRepairs(reps.repairs || []);
      setBrainStats(stats);
      setSysInfo(sys);
    } catch {
      // API may not be ready yet
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 15_000);
    return () => clearInterval(iv);
  }, [refresh]);

  const runScan = async () => {
    setScanning(true);
    try {
      const result = await fetchApi<ScanResult>("/api/health/scan");
      setLastScan(result);
      await refresh();
    } catch {
      // scan failed
    } finally {
      setScanning(false);
    }
  };

  // Scan type summary
  const scanTypes = ["endpoint", "log_error", "db_schema", "import", "frontend", "process"];
  const typeCounts: Record<string, { ok: number; issues: number }> = {};
  for (const t of scanTypes) {
    typeCounts[t] = { ok: 0, issues: 0 };
  }
  for (const e of events) {
    const t = e.check_type;
    if (typeCounts[t]) {
      if (e.status === "ok") typeCounts[t].ok++;
      else typeCounts[t].issues++;
    }
  }

  const successfulRepairs = repairs.filter((r) => r.status === "success").length;
  const models = brainStats?.model_performance || [];

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-muted text-sm">Loading system health...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">System Health</h2>
          <p className="text-sm text-muted mt-1">
            Division 4 — Self-Healing Flywheel
          </p>
        </div>
        <button
          onClick={runScan}
          disabled={scanning}
          className="px-4 py-2 bg-accent/20 text-accent rounded-lg text-sm font-medium hover:bg-accent/30 transition-colors disabled:opacity-50"
        >
          {scanning ? "Scanning..." : "Run Scan Now"}
        </button>
      </div>

      {/* System Overview Cards */}
      {sysInfo && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <div className="rounded-lg border border-border bg-card px-4 py-3">
              <p className="text-xs text-muted">CPU</p>
              <p className={`text-2xl font-bold mt-1 ${sysInfo.cpu_pct > 80 ? "text-red-400" : sysInfo.cpu_pct > 50 ? "text-amber-400" : "text-emerald-400"}`}>
                {sysInfo.cpu_pct}%
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card px-4 py-3">
              <p className="text-xs text-muted">RAM</p>
              <p className={`text-2xl font-bold mt-1 ${sysInfo.ram_pct > 85 ? "text-red-400" : sysInfo.ram_pct > 70 ? "text-amber-400" : ""}`}>
                {sysInfo.ram_pct}%
              </p>
              <p className="text-xs text-muted">{Math.round(sysInfo.ram_used_mb / 1024)}G / {Math.round(sysInfo.ram_total_mb / 1024)}G</p>
            </div>
            <div className="rounded-lg border border-border bg-card px-4 py-3">
              <p className="text-xs text-muted">Database</p>
              <p className="text-2xl font-bold mt-1">{sysInfo.db_size_mb} MB</p>
            </div>
            <div className="rounded-lg border border-border bg-card px-4 py-3">
              <p className="text-xs text-muted">API Calls Today</p>
              <p className="text-2xl font-bold mt-1 text-accent">{sysInfo.api_calls_today.toLocaleString()}</p>
            </div>
            <div className="rounded-lg border border-border bg-card px-4 py-3">
              <p className="text-xs text-muted">Tokens Today</p>
              <p className="text-2xl font-bold mt-1">{formatTokens(sysInfo.tokens_today)}</p>
            </div>
            <div className="rounded-lg border border-border bg-card px-4 py-3">
              <p className="text-xs text-muted">Uptime</p>
              <p className="text-2xl font-bold mt-1">{formatUptime(sysInfo.uptime_seconds)}</p>
            </div>
          </div>

          {/* Provider Budget Bars */}
          {sysInfo.provider_caps.length > 0 && (
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-sm font-semibold mb-3">Provider Budgets</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {sysInfo.provider_caps
                  .filter((p) => p.provider !== "ollama" && p.daily_limit < 999999)
                  .map((p) => (
                    <div key={p.provider} className="flex items-center gap-3">
                      <span className="text-xs font-medium w-20 text-right">{p.provider}</span>
                      <div className="flex-1 h-3 bg-zinc-800 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${
                            p.used_pct >= p.threshold_pct ? "bg-red-400" :
                            p.used_pct >= p.threshold_pct * 0.7 ? "bg-amber-400" :
                            "bg-emerald-400"
                          }`}
                          style={{ width: `${Math.min(100, p.used_pct)}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted w-28">
                        {p.used_pct}% | {p.hours_until_cap}h left
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* System Info Line */}
          <div className="flex flex-wrap items-center gap-4 text-xs text-muted">
            {sysInfo.git_branch && (
              <span>Branch: <span className="text-foreground font-mono">{sysInfo.git_branch}</span></span>
            )}
            {sysInfo.git_hash && (
              <span>Commit: <span className="text-foreground font-mono">{sysInfo.git_hash}</span> — {sysInfo.git_message}</span>
            )}
            {sysInfo.last_error.message && (
              <span className="text-red-400">
                Last error: {sysInfo.last_error.message.slice(0, 80)} ({timeAgo(sysInfo.last_error.timestamp)})
              </span>
            )}
          </div>
        </div>
      )}

      {/* Last Scan Result */}
      {lastScan && (
        <div className={`rounded-lg border p-4 ${
          lastScan.issues_found === 0
            ? "border-emerald-500/30 bg-emerald-500/5"
            : "border-amber-500/30 bg-amber-500/5"
        }`}>
          <div className="text-sm font-medium">
            {lastScan.issues_found === 0
              ? "All systems healthy"
              : `${lastScan.issues_found} issue${lastScan.issues_found > 1 ? "s" : ""} detected`}
          </div>
        </div>
      )}

      {/* Scan Type Grid */}
      <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
        {scanTypes.map((t) => {
          const label = t.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
          const hasIssues = typeCounts[t].issues > 0;
          return (
            <div
              key={t}
              className={`rounded-lg border p-3 text-center ${
                hasIssues
                  ? "border-amber-500/30 bg-amber-500/5"
                  : "border-border bg-card"
              }`}
            >
              <div className={`text-xs font-medium mb-1 ${hasIssues ? "text-amber-400" : "text-muted"}`}>
                {label}
              </div>
              <div className={`text-lg font-bold ${hasIssues ? "text-amber-400" : "text-emerald-400"}`}>
                {hasIssues ? typeCounts[t].issues : "OK"}
              </div>
            </div>
          );
        })}
      </div>

      {/* Two-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Events */}
        <div className="rounded-lg border border-border bg-card">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h3 className="text-sm font-semibold">Recent Health Events</h3>
            <span className="text-xs text-muted">{events.length} events</span>
          </div>
          <div className="divide-y divide-border max-h-80 overflow-y-auto">
            {events.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted">
                No health events yet. Run a scan to start monitoring.
              </div>
            ) : (
              events.slice(0, 20).map((e) => (
                <div key={e.id} className="px-4 py-2.5 flex items-center justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted">{e.check_type}</span>
                      <StatusBadge status={e.status} />
                    </div>
                    <p className="text-sm truncate mt-0.5">{e.target}</p>
                    {e.message && (
                      <p className="text-xs text-muted truncate">{e.message}</p>
                    )}
                  </div>
                  <div className="text-xs text-muted whitespace-nowrap">
                    {timeAgo(e.detected_at)}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Recent Repairs */}
        <div className="rounded-lg border border-border bg-card">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <h3 className="text-sm font-semibold">Recent Repairs</h3>
            <span className="text-xs text-emerald-400">
              {successfulRepairs} auto-fixed
            </span>
          </div>
          <div className="divide-y divide-border max-h-80 overflow-y-auto">
            {repairs.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted">
                No repairs yet. The flywheel will log repairs here.
              </div>
            ) : (
              repairs.map((r) => (
                <div key={r.id} className="px-4 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={r.status} />
                      <span className="text-xs text-muted">{r.model_used || "—"}</span>
                    </div>
                    <span className="text-xs text-muted">{timeAgo(r.created_at)}</span>
                  </div>
                  <p className="text-sm mt-1 truncate">
                    {r.diagnosis || r.fix_approach || "Repair attempt"}
                  </p>
                  {r.duration_ms > 0 && (
                    <p className="text-xs text-muted mt-0.5">
                      {r.duration_ms}ms | {r.tokens_used} tokens
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Model Performance */}
      {models.length > 0 && (
        <div className="rounded-lg border border-border bg-card">
          <div className="px-4 py-3 border-b border-border">
            <h3 className="text-sm font-semibold">Model Performance</h3>
            <p className="text-xs text-muted mt-0.5">Which models are best at self-healing tasks</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted border-b border-border">
                  <th className="px-4 py-2 text-left">Model</th>
                  <th className="px-4 py-2 text-left">Provider</th>
                  <th className="px-4 py-2 text-right">Attempts</th>
                  <th className="px-4 py-2 text-right">Success Rate</th>
                  <th className="px-4 py-2 text-right">Avg Latency</th>
                  <th className="px-4 py-2 text-right">Tokens</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {models.map((m, i) => (
                  <tr key={i} className="hover:bg-card-hover transition-colors">
                    <td className="px-4 py-2 font-medium">{m.model}</td>
                    <td className="px-4 py-2 text-muted">{m.provider}</td>
                    <td className="px-4 py-2 text-right">{m.attempts}</td>
                    <td className="px-4 py-2 text-right">
                      <span className={m.success_rate >= 80 ? "text-emerald-400" : m.success_rate >= 50 ? "text-amber-400" : "text-red-400"}>
                        {m.success_rate}%
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-muted">{m.avg_latency}ms</td>
                    <td className="px-4 py-2 text-right text-muted">
                      {formatTokens(m.total_tokens)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Brain Knowledge Stats */}
      {brainStats && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-semibold mb-3">Brain Knowledge</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            {Object.entries(brainStats.brain).map(([table, count]) => {
              const label = table
                .replace("brain_", "")
                .replace(/_/g, " ")
                .replace(/\b\w/g, (c) => c.toUpperCase());
              return (
                <div key={table} className="text-center">
                  <div className="text-lg font-bold">{count}</div>
                  <div className="text-xs text-muted">{label}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
