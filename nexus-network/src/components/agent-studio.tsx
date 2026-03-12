"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface AgentInfo {
  agent_id: string;
  agent_type: string;
  model: string;
  status: string;
  current_task: Record<string, unknown> | null;
  tasks_completed: number;
  last_active: number;
}

interface FileLock {
  file_path: string;
  agent_id: string;
  task_id: string;
  locked_at: string;
  ttl_seconds: number;
}

interface BugReport {
  id: number;
  file_path: string;
  line_number: number;
  severity: string;
  category: string;
  description: string;
  suggested_fix: string;
  status: string;
  found_at: string;
}

interface StudioTask {
  task_id: string;
  task_type: string;
  status: string;
  priority: number;
  created_at: number;
  completed_at: number | null;
  provider_used: string;
  model_used: string;
  processing_ms: number;
}

interface MetricRow {
  agent_type: string;
  total_tasks: number;
  successes: number;
  avg_duration: number;
  total_tokens: number;
}

interface StudioStatus {
  agents: Record<string, AgentInfo>;
  active_builds: number;
  total_sub_agents: number;
  max_parallel_builds: number;
  max_total_concurrent: number;
  file_locks: FileLock[];
  running: boolean;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  idle: "bg-success",
  working: "bg-warning animate-pulse",
  error: "bg-red-500",
};

const SEVERITY_COLORS: Record<string, string> = {
  error: "text-red-400",
  warning: "text-yellow-400",
  info: "text-blue-400",
};

function timeAgo(ts: number | string): string {
  if (!ts) return "never";
  const s = typeof ts === "number" ? ts : new Date(ts).getTime() / 1000;
  const ago = Math.floor(Date.now() / 1000 - s);
  if (ago < 60) return `${ago}s ago`;
  if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
  if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`;
  return `${Math.floor(ago / 86400)}d ago`;
}

// ── Component ────────────────────────────────────────────────────────────────

export function AgentStudio() {
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [bugs, setBugs] = useState<BugReport[]>([]);
  const [tasks, setTasks] = useState<StudioTask[]>([]);
  const [metrics, setMetrics] = useState<MetricRow[]>([]);
  const [tab, setTab] = useState<"agents" | "tasks" | "locks" | "bugs" | "metrics">("agents");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [s, b, t, m] = await Promise.all([
        fetchApi<StudioStatus>("/api/studio/status"),
        fetchApi<{ bugs: BugReport[] }>("/api/studio/bugs"),
        fetchApi<{ tasks: StudioTask[] }>("/api/studio/tasks"),
        fetchApi<{ by_agent: MetricRow[] }>("/api/studio/metrics"),
      ]);
      setStatus(s);
      setBugs(b.bugs || []);
      setTasks(t.tasks || []);
      setMetrics(m.by_agent || []);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  const resolveBug = async (id: number) => {
    await postApi(`/api/studio/bugs/${id}/resolve`, {});
    refresh();
  };

  if (error && !status) {
    return (
      <div className="flex-1 overflow-auto p-6">
        <h2 className="text-2xl font-bold mb-4">Agent Studio</h2>
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-red-400">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Agent Studio</h2>
          <p className="text-sm text-muted mt-1">
            {status?.active_builds ?? 0}/{status?.max_parallel_builds ?? 5} parallel builds
            {status?.running && <span className="ml-2 text-success">Daemon running</span>}
          </p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 bg-card rounded-lg p-1">
        {(["agents", "tasks", "locks", "bugs", "metrics"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              tab === t ? "bg-accent/15 text-accent" : "text-muted hover:text-foreground"
            }`}
          >
            {t === "agents" && "Agents"}
            {t === "tasks" && `Tasks (${tasks.length})`}
            {t === "locks" && `Locks (${status?.file_locks?.length ?? 0})`}
            {t === "bugs" && `Bugs (${bugs.length})`}
            {t === "metrics" && "Metrics"}
          </button>
        ))}
      </div>

      {/* Agent Cards */}
      {tab === "agents" && status && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {Object.entries(status.agents).map(([key, agent]) => (
            <div key={key} className="bg-card border border-border rounded-xl p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold capitalize">{agent.agent_type}</h3>
                <div className="flex items-center gap-2">
                  <div className={`w-2.5 h-2.5 rounded-full ${STATUS_COLORS[agent.status] || "bg-gray-500"}`} />
                  <span className="text-xs text-muted capitalize">{agent.status}</span>
                </div>
              </div>
              <div className="text-xs text-muted space-y-1">
                <p>Model: <span className="text-foreground">{agent.model}</span></p>
                <p>Tasks done: <span className="text-foreground">{agent.tasks_completed}</span></p>
                <p>Last active: <span className="text-foreground">{timeAgo(agent.last_active)}</span></p>
              </div>
              {agent.current_task && (
                <div className="bg-warning/10 border border-warning/30 rounded-lg p-2 text-xs">
                  Working: {String((agent.current_task as Record<string, unknown>).description ?? agent.current_task.task_id ?? "...").slice(0, 60)}
                </div>
              )}
            </div>
          ))}

          {/* Bug Hunter card (separate since it's independent) */}
          <div className="bg-card border border-border rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Bug Hunter</h3>
              <div className="flex items-center gap-2">
                <div className="w-2.5 h-2.5 rounded-full bg-success" />
                <span className="text-xs text-muted">Daemon</span>
              </div>
            </div>
            <div className="text-xs text-muted space-y-1">
              <p>Model: <span className="text-foreground">llama-4-scout</span></p>
              <p>Open bugs: <span className="text-foreground">{bugs.length}</span></p>
              <p>Interval: <span className="text-foreground">Every 10 min</span></p>
            </div>
          </div>
        </div>
      )}

      {/* Task Queue */}
      {tab === "tasks" && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted">
                <th className="px-4 py-3">Task ID</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Provider</th>
                <th className="px-4 py-3">Duration</th>
                <th className="px-4 py-3">Created</th>
              </tr>
            </thead>
            <tbody>
              {tasks.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">No studio tasks yet</td></tr>
              )}
              {tasks.map((t) => (
                <tr key={t.task_id} className="border-b border-border/50 hover:bg-card-hover">
                  <td className="px-4 py-2 font-mono text-xs">{t.task_id}</td>
                  <td className="px-4 py-2">{t.task_type.replace("studio_", "")}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs ${
                      t.status === "completed" ? "bg-success/20 text-success" :
                      t.status === "failed" ? "bg-red-500/20 text-red-400" :
                      t.status === "in_progress" ? "bg-warning/20 text-warning" :
                      "bg-gray-500/20 text-gray-400"
                    }`}>{t.status}</span>
                  </td>
                  <td className="px-4 py-2 text-muted">{t.provider_used || "-"}</td>
                  <td className="px-4 py-2 text-muted">{t.processing_ms ? `${t.processing_ms}ms` : "-"}</td>
                  <td className="px-4 py-2 text-muted">{timeAgo(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* File Lock Map */}
      {tab === "locks" && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted">
                <th className="px-4 py-3">File</th>
                <th className="px-4 py-3">Agent</th>
                <th className="px-4 py-3">Task</th>
                <th className="px-4 py-3">Locked At</th>
                <th className="px-4 py-3">TTL</th>
              </tr>
            </thead>
            <tbody>
              {(!status?.file_locks || status.file_locks.length === 0) && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-muted">No active file locks</td></tr>
              )}
              {status?.file_locks?.map((lock, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-card-hover">
                  <td className="px-4 py-2 font-mono text-xs">{lock.file_path}</td>
                  <td className="px-4 py-2">{lock.agent_id}</td>
                  <td className="px-4 py-2 font-mono text-xs text-muted">{lock.task_id || "-"}</td>
                  <td className="px-4 py-2 text-muted">{timeAgo(lock.locked_at)}</td>
                  <td className="px-4 py-2 text-muted">{lock.ttl_seconds}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Bug Reports */}
      {tab === "bugs" && (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted">
                <th className="px-4 py-3">ID</th>
                <th className="px-4 py-3">File</th>
                <th className="px-4 py-3">Line</th>
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3">Action</th>
              </tr>
            </thead>
            <tbody>
              {bugs.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">No open bugs</td></tr>
              )}
              {bugs.map((bug) => (
                <tr key={bug.id} className="border-b border-border/50 hover:bg-card-hover">
                  <td className="px-4 py-2 text-muted">#{bug.id}</td>
                  <td className="px-4 py-2 font-mono text-xs">{bug.file_path}</td>
                  <td className="px-4 py-2 text-muted">{bug.line_number || "-"}</td>
                  <td className="px-4 py-2">
                    <span className={SEVERITY_COLORS[bug.severity] || "text-muted"}>{bug.severity}</span>
                  </td>
                  <td className="px-4 py-2 max-w-md truncate" title={bug.description}>{bug.description}</td>
                  <td className="px-4 py-2">
                    <button
                      onClick={() => resolveBug(bug.id)}
                      className="text-xs text-accent hover:text-accent/80 transition-colors"
                    >
                      Resolve
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Metrics */}
      {tab === "metrics" && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {metrics.map((m) => (
            <div key={m.agent_type} className="bg-card border border-border rounded-xl p-4 space-y-2">
              <h3 className="font-semibold capitalize">{m.agent_type}</h3>
              <div className="text-xs text-muted space-y-1">
                <p>Tasks: <span className="text-foreground">{m.total_tasks}</span></p>
                <p>Success rate: <span className="text-foreground">
                  {m.total_tasks > 0 ? Math.round((m.successes / m.total_tasks) * 100) : 0}%
                </span></p>
                <p>Avg duration: <span className="text-foreground">{m.avg_duration}s</span></p>
                <p>Total tokens: <span className="text-foreground">{m.total_tokens.toLocaleString()}</span></p>
              </div>
              {/* Success bar */}
              <div className="w-full bg-border rounded-full h-1.5">
                <div
                  className="bg-success rounded-full h-1.5 transition-all"
                  style={{ width: `${m.total_tasks > 0 ? (m.successes / m.total_tasks) * 100 : 0}%` }}
                />
              </div>
            </div>
          ))}
          {metrics.length === 0 && (
            <div className="col-span-full text-center text-muted py-8">
              No metrics yet. Submit tasks to see performance data.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
