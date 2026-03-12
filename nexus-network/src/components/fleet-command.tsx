"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi, asArray } from "@/lib/api";

// ── Types (from agent-control + fleet-status) ────────────────────────────────

interface AgentNode {
  id: string;
  name: string;
  model: string;
  taskType: string;
  description: string;
  status: string;
  currentTask: string;
  tasksToday: number;
  tokensToday: number;
  callsToday: number;
  lastActive: string;
  errorMessage: string;
}

interface DivisionNode {
  id: string;
  name: string;
  model: string;
  description: string;
  status: string;
  agents: AgentNode[];
}

interface HierarchyTree {
  id: string;
  name: string;
  model: string;
  description: string;
  status: string;
  divisions: DivisionNode[];
}

interface ProviderStatus {
  id: string;
  name: string;
  model: string;
  keys_total: number;
  keys_active: number;
  rpm_per_key: number;
  rpm_total: number;
  rpd_per_key: number;
  rpd_total: number;
  calls_today: number;
  strength: string;
  task_types: string[];
  openai_compat: boolean;
}

interface FleetData {
  providers: ProviderStatus[];
  total_providers: number;
  total_keys: number;
  total_rpm: number;
  total_daily_capacity: number;
  parallel_workers: number;
  total_calls_today: number;
}

interface WorkerStatus {
  total_active: number;
  shift: string;
}

interface TaskStats {
  pending?: number;
  claimed?: number;
  in_progress?: number;
  completed?: number;
  failed?: number;
  dead_letter?: number;
  today?: { completed: number; tokens: number; processing_ms: number };
  by_tier?: Record<string, number>;
}

interface ActivityEntry {
  task_id: string;
  task_type: string;
  tier: number;
  status: string;
  provider_used: string;
  model_used: string;
  tokens_consumed: number;
  processing_ms: number;
  created_at: string;
  completed_at: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  running: "bg-green-500 animate-pulse",
  active: "bg-green-500 animate-pulse",
  idle: "bg-yellow-400",
  stopped: "bg-gray-500",
  error: "bg-red-500",
  completed: "bg-blue-400",
};

const MODEL_COLORS: Record<string, string> = {
  Orchestrator: "bg-purple-500/20 text-purple-300",
  Groq: "bg-blue-500/20 text-blue-300",
  Cerebras: "bg-cyan-500/20 text-cyan-300",
  Gemini: "bg-yellow-500/20 text-yellow-300",
  ZAI: "bg-orange-500/20 text-orange-300",
  System: "bg-gray-500/20 text-gray-300",
  Ollama: "bg-green-500/20 text-green-300",
};

const PROVIDER_COLORS: Record<string, string> = {
  groq: "text-orange-400 bg-orange-500/10 border-orange-500/20",
  cerebras: "text-cyan-400 bg-cyan-500/10 border-cyan-500/20",
  gemini: "text-blue-400 bg-blue-500/10 border-blue-500/20",
  openrouter: "text-purple-400 bg-purple-500/10 border-purple-500/20",
  mistral: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  huggingface: "text-yellow-400 bg-yellow-500/10 border-yellow-500/20",
  moonshot: "text-indigo-400 bg-indigo-500/10 border-indigo-500/20",
  zai: "text-green-400 bg-green-500/10 border-green-500/20",
  apifreellm: "text-pink-400 bg-pink-500/10 border-pink-500/20",
  ollama: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
};

const PROVIDER_ICONS: Record<string, string> = {
  groq: "G", cerebras: "C", gemini: "G", openrouter: "OR", mistral: "M",
  huggingface: "HF", moonshot: "K", zai: "Z", apifreellm: "AF", ollama: "OL",
};

function timeAgo(ts: string): string {
  if (!ts) return "\u2014";
  const ago = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (ago < 0 || isNaN(ago)) return "\u2014";
  if (ago < 60) return `${ago}s ago`;
  if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
  if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`;
  return `${Math.floor(ago / 86400)}d ago`;
}

function formatTokens(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

type FleetTab = "overview" | "agents" | "providers";

export function FleetCommand() {
  const [activeTab, setActiveTab] = useState<FleetTab>("overview");
  const [tree, setTree] = useState<HierarchyTree | null>(null);
  const [fleet, setFleet] = useState<FleetData | null>(null);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  const [taskStats, setTaskStats] = useState<TaskStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedDivs, setExpandedDivs] = useState<Set<string>>(new Set());
  const [selectedAgent, setSelectedAgent] = useState<AgentNode | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; ms: number; error?: string }>>({});
  const [activity, setActivity] = useState<ActivityEntry[]>([]);

  const loadData = useCallback(async () => {
    const [treeData, fleetData, workerData, taskData] = await Promise.all([
      fetchApi<HierarchyTree>("/api/hierarchy/tree").catch(() => null),
      fetchApi<FleetData>("/api/fleet/status").catch(() => null),
      fetchApi<WorkerStatus>("/api/fleet/workers").catch(() => null),
      fetchApi<TaskStats>("/api/fleet/tasks").catch(() => null),
    ]);
    setTree(treeData);
    setFleet(fleetData);
    setWorkers(workerData);
    setTaskStats(taskData);
    setLoading(false);
  }, []);

  const loadActivity = useCallback(async () => {
    const res = await fetchApi<{ tasks: ActivityEntry[] }>("/api/fleet/tasks/activity").catch(() => null);
    setActivity(asArray(res?.tasks));
  }, []);

  useEffect(() => {
    loadData();
    loadActivity();
    const iv = setInterval(loadData, 15000);
    const ivFast = setInterval(loadActivity, 5000);
    return () => { clearInterval(iv); clearInterval(ivFast); };
  }, [loadData, loadActivity]);

  const toggleDiv = (id: string) => {
    setExpandedDivs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const testProvider = async (providerId: string) => {
    setTesting(providerId);
    try {
      const start = Date.now();
      const res = await postApi<{ status: string; error?: string }>(`/api/fleet/test/${providerId}`, {});
      const ms = Date.now() - start;
      setTestResults((prev) => ({ ...prev, [providerId]: { ok: res.status === "ok", ms, error: res.error } }));
    } catch (e) {
      setTestResults((prev) => ({ ...prev, [providerId]: { ok: false, ms: 0, error: String(e) } }));
    }
    setTesting(null);
  };

  const allAgents = tree?.divisions?.flatMap((d) => d.agents || []) || [];
  const totalAgents = allAgents.length;
  const activeAgents = allAgents.filter((a) => a.status === "running" || a.status === "active").length;
  const idleAgents = allAgents.filter((a) => a.status === "idle").length;
  const failedAgents = allAgents.filter((a) => a.status === "error").length;
  const stoppedAgents = totalAgents - activeAgents - idleAgents - failedAgents;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
      </div>
    );
  }

  const tabs: { id: FleetTab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "agents", label: "Agents" },
    { id: "providers", label: "Providers" },
  ];

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold mb-1">NEXUS COMMAND CENTER</h2>
        <div className="flex items-center gap-4 text-sm">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-green-500" />{activeAgents} Running
          </span>
          {idleAgents > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-yellow-400" />{idleAgents} Idle
            </span>
          )}
          {failedAgents > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-red-500" />{failedAgents} Failed
            </span>
          )}
          {stoppedAgents > 0 && (
            <span className="flex items-center gap-1.5 text-muted">
              <span className="w-2 h-2 rounded-full bg-gray-500" />{stoppedAgents} Stopped
            </span>
          )}
          <span className="text-muted ml-2">
            Providers: {fleet?.total_providers || 0} &middot; Keys: {fleet?.total_keys || 0} &middot; RPM: {fleet?.total_rpm || 0}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="bg-card border border-border rounded-lg px-3 py-2.5">
          <p className="text-xs text-muted">Agents</p>
          <p className="text-lg font-semibold">{activeAgents}<span className="text-muted text-sm">/{totalAgents}</span></p>
        </div>
        <div className="bg-card border border-border rounded-lg px-3 py-2.5">
          <p className="text-xs text-muted">Providers</p>
          <p className="text-lg font-semibold">{fleet?.total_providers || 0}</p>
        </div>
        <div className="bg-card border border-border rounded-lg px-3 py-2.5">
          <p className="text-xs text-muted">Total RPM</p>
          <p className="text-lg font-semibold text-accent">{fleet?.total_rpm || 0}</p>
        </div>
        <div className="bg-card border border-border rounded-lg px-3 py-2.5">
          <p className="text-xs text-muted">Workers</p>
          <p className="text-lg font-semibold">{workers?.total_active || 0}</p>
        </div>
        <div className="bg-card border border-border rounded-lg px-3 py-2.5">
          <p className="text-xs text-muted">Calls Today</p>
          <p className="text-lg font-semibold">{fleet?.total_calls_today || 0}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === t.id ? "border-accent text-accent" : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview Tab */}
      {activeTab === "overview" && (
        <div className="space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Agent Tree (compact) */}
          <div className="bg-card border border-border rounded-xl p-4">
            <h3 className="text-sm font-medium mb-3">Agent Hierarchy</h3>
            {tree?.divisions?.map((div) => (
              <div key={div.id} className="mb-2">
                <button
                  onClick={() => toggleDiv(div.id)}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-card-hover transition-colors"
                >
                  <span className="text-xs">{expandedDivs.has(div.id) ? "\u25bc" : "\u25b6"}</span>
                  <div className={`w-2 h-2 rounded-full ${STATUS_DOT[div.status] || "bg-gray-500"}`} />
                  <span className="text-sm font-medium">{div.name}</span>
                  <span className="text-xs text-muted ml-auto">{div.agents?.length || 0} agents</span>
                </button>
                {expandedDivs.has(div.id) && div.agents?.map((agent) => (
                  <button
                    key={agent.id}
                    onClick={() => setSelectedAgent(agent)}
                    className="w-full flex items-center gap-2 pl-8 pr-2 py-1 hover:bg-card-hover rounded transition-colors"
                  >
                    <div className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[agent.status] || "bg-gray-500"}`} />
                    <span className="text-xs truncate">{agent.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ml-auto ${MODEL_COLORS[agent.model] || "bg-gray-500/20 text-gray-300"}`}>
                      {agent.model}
                    </span>
                  </button>
                ))}
              </div>
            )) || <p className="text-xs text-muted">No agent data available</p>}
          </div>

          {/* Provider Grid (compact) */}
          <div className="bg-card border border-border rounded-xl p-4">
            <h3 className="text-sm font-medium mb-3">AI Providers</h3>
            <div className="grid grid-cols-2 gap-2">
              {fleet?.providers?.map((p) => {
                const colors = PROVIDER_COLORS[p.id] || "text-gray-400 bg-gray-500/10 border-gray-500/20";
                const icon = PROVIDER_ICONS[p.id] || "?";
                return (
                  <div key={p.id} className={`rounded-lg border px-3 py-2 ${colors}`}>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold">{icon}</span>
                      <span className="text-xs font-medium capitalize">{p.name}</span>
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-[10px] opacity-70">{p.rpm_total} RPM</span>
                      <span className="text-[10px] opacity-70">{p.calls_today} calls</span>
                    </div>
                  </div>
                );
              }) || <p className="text-xs text-muted col-span-2">No providers</p>}
            </div>
          </div>
        </div>

        {/* Row 2: Activity Log + Queue Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Activity Log */}
          <div className="bg-card border border-border rounded-xl p-4">
            <h3 className="text-sm font-medium mb-3">Live Activity</h3>
            {activity.length === 0 ? (
              <p className="text-xs text-muted py-4 text-center">No recent activity</p>
            ) : (
              <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
                {activity.slice(0, 30).map((a, i) => {
                  const statusColor = a.status === "completed" ? "text-green-400"
                    : a.status === "failed" ? "text-red-400"
                    : a.status === "in_progress" ? "text-blue-400"
                    : "text-muted";
                  const ts = a.completed_at || a.created_at;
                  const time = ts ? new Date(ts).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "";
                  return (
                    <div key={a.task_id || i} className="flex items-start gap-2 py-1 border-b border-border/50 last:border-0">
                      <span className="text-[10px] text-muted font-mono shrink-0 w-16 pt-0.5">{time}</span>
                      <span className={`text-[10px] shrink-0 w-12 pt-0.5 font-medium ${statusColor}`}>{a.status}</span>
                      <div className="flex-1 min-w-0">
                        <span className="text-xs">{a.task_type}</span>
                        {a.provider_used && (
                          <span className="text-[10px] text-muted ml-1.5">via {a.provider_used}</span>
                        )}
                      </div>
                      <div className="text-right shrink-0">
                        {a.processing_ms > 0 && <span className="text-[10px] text-muted">{a.processing_ms}ms</span>}
                        {a.tokens_consumed > 0 && <span className="text-[10px] text-muted ml-1.5">{formatTokens(a.tokens_consumed)}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Queue Stats */}
          <div className="bg-card border border-border rounded-xl p-4">
            <h3 className="text-sm font-medium mb-3">Task Queue</h3>
            {!taskStats ? (
              <p className="text-xs text-muted py-4 text-center">No queue data</p>
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: "Pending", value: taskStats.pending || 0, color: "text-yellow-400" },
                    { label: "Active", value: (taskStats.claimed || 0) + (taskStats.in_progress || 0), color: "text-blue-400" },
                    { label: "Completed", value: taskStats.completed || 0, color: "text-green-400" },
                    { label: "Failed", value: taskStats.failed || 0, color: taskStats.failed ? "text-red-400" : "text-muted" },
                    { label: "Dead Letter", value: taskStats.dead_letter || 0, color: taskStats.dead_letter ? "text-red-400" : "text-muted" },
                  ].map((s) => (
                    <div key={s.label}>
                      <p className="text-[10px] text-muted">{s.label}</p>
                      <p className={`text-lg font-semibold ${s.color}`}>{s.value}</p>
                    </div>
                  ))}
                </div>
                {taskStats.today && (
                  <div className="border-t border-border pt-3">
                    <p className="text-[10px] text-muted uppercase tracking-wider mb-2">Today</p>
                    <div className="flex gap-4">
                      <div>
                        <p className="text-xs text-muted">Completed</p>
                        <p className="text-sm font-medium text-green-400">{taskStats.today.completed}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted">Tokens</p>
                        <p className="text-sm font-medium">{formatTokens(taskStats.today.tokens)}</p>
                      </div>
                      {taskStats.today.processing_ms > 0 && (
                        <div>
                          <p className="text-xs text-muted">Avg Time</p>
                          <p className="text-sm font-medium">
                            {taskStats.today.completed > 0
                              ? `${Math.round(taskStats.today.processing_ms / taskStats.today.completed)}ms`
                              : "—"}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        </div>
      )}

      {/* Agents Tab */}
      {activeTab === "agents" && (
        <div className="space-y-4">
          {tree ? (
            <>
              {/* Root */}
              <div className="bg-card border border-border rounded-xl p-4">
                <div className="flex items-center gap-3">
                  <div className={`w-3 h-3 rounded-full ${STATUS_DOT[tree.status] || "bg-gray-500"}`} />
                  <div>
                    <h3 className="text-sm font-bold">{tree.name}</h3>
                    <p className="text-xs text-muted">{tree.description}</p>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded ml-auto ${MODEL_COLORS[tree.model] || ""}`}>{tree.model}</span>
                </div>
              </div>

              {/* Divisions */}
              {tree.divisions?.map((div) => (
                <div key={div.id} className="bg-card border border-border rounded-xl overflow-hidden">
                  <button
                    onClick={() => toggleDiv(div.id)}
                    className="w-full flex items-center gap-3 px-4 py-3 hover:bg-card-hover transition-colors"
                  >
                    <span className="text-sm">{expandedDivs.has(div.id) ? "\u25bc" : "\u25b6"}</span>
                    <div className={`w-2.5 h-2.5 rounded-full ${STATUS_DOT[div.status] || "bg-gray-500"}`} />
                    <div className="text-left">
                      <p className="text-sm font-semibold">{div.name}</p>
                      <p className="text-xs text-muted">{div.description}</p>
                    </div>
                    <span className="text-xs text-muted ml-auto">{div.agents?.length || 0} agents</span>
                  </button>
                  {expandedDivs.has(div.id) && (
                    <div className="border-t border-border">
                      {div.agents?.map((agent) => (
                        <button
                          key={agent.id}
                          onClick={() => setSelectedAgent(agent)}
                          className="w-full flex items-center gap-3 px-6 py-2.5 hover:bg-card-hover transition-colors border-b border-border last:border-0"
                        >
                          <div className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[agent.status] || "bg-gray-500"}`} />
                          <div className="text-left flex-1 min-w-0">
                            <p className="text-sm font-medium truncate">{agent.name}</p>
                            <p className="text-xs text-muted truncate">{agent.currentTask || agent.description}</p>
                          </div>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${MODEL_COLORS[agent.model] || ""}`}>
                            {agent.model}
                          </span>
                          <div className="text-right shrink-0 ml-2">
                            <p className="text-[10px] text-muted">{agent.tasksToday} tasks</p>
                            <p className="text-[10px] text-muted">{timeAgo(agent.lastActive)}</p>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </>
          ) : (
            <div className="text-center py-10 text-sm text-muted">No agent hierarchy available</div>
          )}
        </div>
      )}

      {/* Providers Tab */}
      {activeTab === "providers" && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {fleet?.providers?.map((p) => {
            const colors = PROVIDER_COLORS[p.id] || "text-gray-400 bg-gray-500/10 border-gray-500/20";
            const icon = PROVIDER_ICONS[p.id] || "?";
            const result = testResults[p.id];
            return (
              <div key={p.id} className={`rounded-xl border p-4 ${colors}`}>
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-bold">{icon}</span>
                    <div>
                      <p className="text-sm font-semibold capitalize">{p.name}</p>
                      <p className="text-[10px] opacity-70 font-mono">{p.model}</p>
                    </div>
                  </div>
                  <button
                    onClick={() => testProvider(p.id)}
                    disabled={testing === p.id}
                    className="text-xs px-2 py-1 rounded bg-white/10 hover:bg-white/20 transition-colors disabled:opacity-50"
                  >
                    {testing === p.id ? "..." : "Test"}
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <span className="opacity-60">Keys</span>
                    <p className="font-medium">{p.keys_active}/{p.keys_total}</p>
                  </div>
                  <div>
                    <span className="opacity-60">RPM</span>
                    <p className="font-medium">{p.rpm_total}</p>
                  </div>
                  <div>
                    <span className="opacity-60">Daily Cap</span>
                    <p className="font-medium">{p.rpd_total.toLocaleString()}</p>
                  </div>
                  <div>
                    <span className="opacity-60">Calls Today</span>
                    <p className="font-medium">{p.calls_today}</p>
                  </div>
                </div>

                {result && (
                  <div className={`mt-2 text-xs px-2 py-1 rounded ${result.ok ? "bg-green-500/20 text-green-300" : "bg-red-500/20 text-red-300"}`}>
                    {result.ok ? `OK (${result.ms}ms)` : result.error || "Failed"}
                  </div>
                )}
              </div>
            );
          }) || <p className="text-sm text-muted col-span-3">No providers configured</p>}
        </div>
      )}

      {/* Agent Detail Modal */}
      {selectedAgent && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setSelectedAgent(null)}>
          <div className="bg-card border border-border rounded-xl p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className={`w-3 h-3 rounded-full ${STATUS_DOT[selectedAgent.status] || "bg-gray-500"}`} />
                <h3 className="text-lg font-bold">{selectedAgent.name}</h3>
              </div>
              <button onClick={() => setSelectedAgent(null)} className="text-muted hover:text-foreground text-lg">&times;</button>
            </div>

            <div className="space-y-3">
              {[
                { label: "Model", value: selectedAgent.model },
                { label: "Status", value: selectedAgent.status },
                { label: "Task Type", value: selectedAgent.taskType },
                { label: "Description", value: selectedAgent.description },
                { label: "Current Task", value: selectedAgent.currentTask || "\u2014" },
                { label: "Tasks Today", value: String(selectedAgent.tasksToday) },
                { label: "Tokens Today", value: formatTokens(selectedAgent.tokensToday) },
                { label: "Calls Today", value: String(selectedAgent.callsToday) },
                { label: "Last Active", value: timeAgo(selectedAgent.lastActive) },
              ].map((f) => (
                <div key={f.label} className="flex justify-between">
                  <span className="text-xs text-muted">{f.label}</span>
                  <span className="text-sm font-medium">{f.value}</span>
                </div>
              ))}
              {selectedAgent.errorMessage && (
                <div className="bg-danger/10 border border-danger/20 rounded-lg p-3">
                  <p className="text-xs text-danger">{selectedAgent.errorMessage}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
