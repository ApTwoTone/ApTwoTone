"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchApi, postApi } from "@/lib/api";

interface RuntimeStatus {
  running?: boolean;
  interval_sec?: number;
  parallelism?: number;
  last_cycle?: string;
  task_alive?: boolean;
  updated_at?: string;
  last_summary?: {
    processed?: number;
    succeeded?: number;
    failed?: number;
    revenue_delta?: number;
  };
}

interface RevenueStreamRow {
  stream?: string;
  total?: number;
}

interface DashboardData {
  total_revenue?: number;
  tracked_revenue_total?: number;
  modeled_revenue_total?: number;
  memory_revenue_total?: number;
  verified_revenue_total?: number;
  revenue_verification?: string;
  revenue_today?: number;
  actions_today?: number;
  runtime?: RuntimeStatus;
  all_experiments?: Array<{ id: number; name?: string; status?: string; created_at?: string }>;
  recent_actions?: Array<{ ts?: string; persona_id?: string; action_name?: string; revenue_delta?: number; obstacle?: string }>;
  recent_obstacles?: Array<{ ts?: string; persona_id?: string; obstacle?: string; result?: string }>;
  revenue_by_stream?: RevenueStreamRow[];
}

interface JourneyEntry {
  ts?: string;
  persona_id?: string;
  action_name?: string;
  action_summary?: string;
  result?: string;
  revenue_delta?: number;
  obstacle?: string;
}

function fmtMoney(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v || 0);
  if (!Number.isFinite(n)) return "$0.00";
  return `$${n.toFixed(2)}`;
}

function fmtDate(v: unknown): string {
  if (!v || typeof v !== "string") return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return d.toLocaleString();
}

export function EntrepreneurLab() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [journey, setJourney] = useState<JourneyEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [intervalSec, setIntervalSec] = useState(1800);
  const [parallelism, setParallelism] = useState(4);
  const [cycleLimit, setCycleLimit] = useState(20);

  const runtimeFromAny = useMemo(() => runtime || dashboard?.runtime || {}, [runtime, dashboard]);

  const refresh = useCallback(async () => {
    try {
      const [dashRes, runtimeRes, journeyRes] = await Promise.all([
        fetchApi<{ status: string; dashboard: DashboardData }>("/api/d3/entrepreneur/dashboard"),
        fetchApi<{ status: string; runtime: RuntimeStatus }>("/api/d3/entrepreneur/runtime"),
        fetchApi<{ status: string; entries: JourneyEntry[] }>("/api/d3/entrepreneur/journey?days=7&limit=80"),
      ]);
      setDashboard(dashRes.dashboard || {});
      setRuntime(runtimeRes.runtime || {});
      setJourney(Array.isArray(journeyRes.entries) ? journeyRes.entries : []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load entrepreneur data");
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 10000);
    return () => clearInterval(iv);
  }, [refresh]);

  const runCycle = async () => {
    setLoading(true);
    try {
      await postApi("/api/d3/entrepreneur/cycle", {
        parallelism: Math.max(1, parallelism),
        limit: Math.max(1, cycleLimit),
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cycle failed");
    }
    setLoading(false);
  };

  const startLoop = async () => {
    setLoading(true);
    try {
      await postApi("/api/d3/entrepreneur/loop/start", {
        interval_sec: Math.max(30, intervalSec),
        parallelism: Math.max(1, parallelism),
      });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start loop");
    }
    setLoading(false);
  };

  const stopLoop = async () => {
    setLoading(true);
    try {
      await postApi("/api/d3/entrepreneur/loop/stop", {});
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to stop loop");
    }
    setLoading(false);
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 pt-5 pb-3 flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-emerald-400">Entrepreneur Lab</h2>
          <p className="text-xs text-muted mt-0.5">Division Three autonomous planner/operator/critic runtime</p>
        </div>
        <button
          onClick={refresh}
          className="px-3 py-1.5 text-xs rounded border border-border text-muted hover:text-foreground hover:bg-card-hover transition-colors"
        >
          Refresh
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 pb-6 space-y-4">
        {error && (
          <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/25 rounded-md px-3 py-2">
            {error}
          </div>
        )}

        <div className="grid grid-cols-4 gap-3">
          <MetricCard label="Runtime" value={runtimeFromAny.running ? "Running" : "Idle"} accent />
          <MetricCard label="Modeled Revenue" value={fmtMoney(dashboard?.modeled_revenue_total ?? dashboard?.tracked_revenue_total)} />
          <MetricCard label="Revenue Today" value={fmtMoney(dashboard?.revenue_today)} />
          <MetricCard label="Actions Today" value={String(dashboard?.actions_today || 0)} />
        </div>

        <div className="text-xs text-yellow-300 bg-yellow-500/10 border border-yellow-500/20 rounded-md px-3 py-2">
          Revenue here is modeled from approved internal experiment deltas. It is not a verified YouTube payout feed.
        </div>

        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex flex-wrap items-end gap-3">
            <ControlNumber
              label="Parallelism"
              value={parallelism}
              min={1}
              max={64}
              onChange={setParallelism}
            />
            <ControlNumber
              label="Cycle Limit"
              value={cycleLimit}
              min={1}
              max={200}
              onChange={setCycleLimit}
            />
            <ControlNumber
              label="Loop Interval (sec)"
              value={intervalSec}
              min={30}
              max={7200}
              onChange={setIntervalSec}
            />
            <div className="flex gap-2">
              <button
                onClick={runCycle}
                disabled={loading}
                className="px-3 py-1.5 text-xs rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/30 disabled:opacity-50"
              >
                Run Cycle Now
              </button>
              <button
                onClick={startLoop}
                disabled={loading}
                className="px-3 py-1.5 text-xs rounded bg-blue-500/20 text-blue-400 border border-blue-500/30 hover:bg-blue-500/30 disabled:opacity-50"
              >
                Start Loop
              </button>
              <button
                onClick={stopLoop}
                disabled={loading}
                className="px-3 py-1.5 text-xs rounded bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 hover:bg-yellow-500/30 disabled:opacity-50"
              >
                Stop Loop
              </button>
            </div>
          </div>

          <div className="mt-3 grid grid-cols-4 gap-3 text-xs text-muted">
            <div>Task Alive: {runtimeFromAny.task_alive ? "Yes" : "No"}</div>
            <div>Interval: {runtimeFromAny.interval_sec || 0}s</div>
            <div>Parallelism: {runtimeFromAny.parallelism || 0}</div>
            <div>Last Cycle: {fmtDate(runtimeFromAny.last_cycle)}</div>
          </div>
          <div className="mt-2 text-xs text-muted">
            Last Summary: processed {runtimeFromAny.last_summary?.processed || 0}, succeeded{" "}
            {runtimeFromAny.last_summary?.succeeded || 0}, failed {runtimeFromAny.last_summary?.failed || 0}, revenue
            delta {fmtMoney(runtimeFromAny.last_summary?.revenue_delta || 0)}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="bg-card border border-border rounded-lg p-4">
            <h3 className="text-sm font-medium mb-2">Revenue By Stream</h3>
            <div className="space-y-1 text-xs">
              {(dashboard?.revenue_by_stream || []).slice(0, 10).map((row, idx) => (
                <div key={`${row.stream || "unknown"}-${idx}`} className="flex items-center justify-between">
                  <span className="text-muted">{row.stream || "unknown"}</span>
                  <span className="text-emerald-400 font-medium">{fmtMoney(row.total)}</span>
                </div>
              ))}
              {!dashboard?.revenue_by_stream?.length && <p className="text-muted">No revenue streams yet.</p>}
            </div>
          </div>
          <div className="bg-card border border-border rounded-lg p-4">
            <h3 className="text-sm font-medium mb-2">Recent Obstacles</h3>
            <div className="space-y-2 text-xs">
              {(dashboard?.recent_obstacles || []).slice(0, 8).map((row, idx) => (
                <div key={`obs-${idx}`} className="border border-border/50 rounded px-2 py-1">
                  <div className="text-muted">{fmtDate(row.ts)}</div>
                  <div className="text-foreground mt-0.5">{row.obstacle || "Obstacle recorded"}</div>
                  <div className="text-muted mt-0.5">{row.result || ""}</div>
                </div>
              ))}
              {!dashboard?.recent_obstacles?.length && <p className="text-muted">No obstacles logged.</p>}
            </div>
          </div>
        </div>

        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-border text-sm font-medium">Journey (latest 7 days)</div>
          <table className="w-full text-xs">
            <thead className="border-b border-border text-muted">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Timestamp</th>
                <th className="px-3 py-2 text-left font-medium">Persona</th>
                <th className="px-3 py-2 text-left font-medium">Action</th>
                <th className="px-3 py-2 text-left font-medium">Result</th>
                <th className="px-3 py-2 text-right font-medium">Revenue Δ</th>
              </tr>
            </thead>
            <tbody>
              {journey.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-muted">
                    No journey entries yet.
                  </td>
                </tr>
              ) : (
                journey.slice(0, 60).map((entry, idx) => (
                  <tr key={`journey-${idx}`} className="border-b border-border/40 hover:bg-card-hover">
                    <td className="px-3 py-2 text-muted">{fmtDate(entry.ts)}</td>
                    <td className="px-3 py-2">{entry.persona_id || "—"}</td>
                    <td className="px-3 py-2">{entry.action_name || entry.action_summary || "—"}</td>
                    <td className="px-3 py-2 text-muted">{entry.result || entry.obstacle || "—"}</td>
                    <td className="px-3 py-2 text-right text-emerald-400">{fmtMoney(entry.revenue_delta || 0)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`bg-card border rounded-lg p-4 ${accent ? "border-emerald-500/30" : "border-border"}`}>
      <p className="text-xs text-muted">{label}</p>
      <p className={`text-xl font-bold mt-1 ${accent ? "text-emerald-400" : "text-foreground"}`}>{value}</p>
    </div>
  );
}

function ControlNumber({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="text-xs text-muted flex flex-col gap-1">
      {label}
      <input
        type="number"
        className="bg-background border border-border rounded px-2 py-1.5 text-xs w-32"
        value={value}
        min={min}
        max={max}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value || min))))}
      />
    </label>
  );
}
