"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

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

interface CommEntry {
  id: number;
  from_agent: string;
  from_dept: string;
  to_agent: string;
  to_dept: string;
  message_type: string;
  message: string;
  created_at: string;
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

// ── Helpers ──────────────────────────────────────────────────────────────────

function timeAgo(ts: string): string {
  if (!ts) return "—";
  const ago = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (ago < 0 || isNaN(ago)) return "—";
  if (ago < 60) return `${ago}s ago`;
  if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
  if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`;
  return `${Math.floor(ago / 86400)}d ago`;
}

function formatTokens(n: number): string {
  if (n === 0) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

// ── Detail Panel ─────────────────────────────────────────────────────────────

function AgentDetailPanel({
  agent,
  onClose,
}: {
  agent: AgentNode;
  onClose: () => void;
}) {
  const [comms, setComms] = useState<CommEntry[]>([]);

  useEffect(() => {
    fetchApi<{ comms: CommEntry[] }>(
      `/api/hierarchy/agent-comms?agent_id=${agent.id}&limit=10`
    ).then((r) => setComms(r.comms)).catch(() => {});
  }, [agent.id]);

  const dot = STATUS_DOT[agent.status] || "bg-gray-400";
  const modelStyle = MODEL_COLORS[agent.model] || "bg-gray-500/20 text-gray-300";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-card border border-border rounded-2xl w-[480px] max-h-[80vh] overflow-auto shadow-2xl" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="p-5 border-b border-border">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`w-3 h-3 rounded-full ${dot}`} />
              <h3 className="font-bold text-lg">{agent.name}</h3>
            </div>
            <button onClick={onClose} className="text-muted hover:text-foreground text-xl leading-none">&times;</button>
          </div>
          <p className="text-sm text-muted mt-1">{agent.description}</p>
          <div className="flex items-center gap-2 mt-2">
            <span className={`text-xs px-2 py-0.5 rounded-full ${modelStyle}`}>{agent.model}</span>
            <span className="text-xs text-muted font-mono">{agent.taskType}</span>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-4 p-5 border-b border-border">
          <div>
            <div className="text-xs text-muted">Tasks Today</div>
            <div className="text-lg font-bold">{agent.tasksToday}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Tokens Today</div>
            <div className="text-lg font-bold">{formatTokens(agent.tokensToday)}</div>
          </div>
          <div>
            <div className="text-xs text-muted">API Calls</div>
            <div className="text-lg font-bold">{agent.callsToday}</div>
          </div>
        </div>

        {/* Current State */}
        {(agent.currentTask || agent.errorMessage) && (
          <div className="p-5 border-b border-border space-y-2">
            {agent.currentTask && (
              <div>
                <div className="text-xs text-muted mb-1">Current Task</div>
                <div className="text-sm bg-accent/10 rounded-lg p-2">{agent.currentTask}</div>
              </div>
            )}
            {agent.errorMessage && (
              <div>
                <div className="text-xs text-red-400 mb-1">Last Error</div>
                <div className="text-sm bg-red-500/10 rounded-lg p-2 text-red-300">{agent.errorMessage}</div>
              </div>
            )}
          </div>
        )}

        {/* Recent Activity */}
        <div className="p-5">
          <div className="text-xs text-muted mb-3 uppercase tracking-wider">Recent Activity</div>
          {comms.length === 0 ? (
            <p className="text-sm text-muted">No recent activity</p>
          ) : (
            <div className="space-y-2">
              {comms.map((c) => (
                <div key={c.id} className="text-xs border-l-2 border-accent/30 pl-3 py-1">
                  <div className="text-foreground">{c.message}</div>
                  <div className="text-muted mt-0.5">{timeAgo(c.created_at)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Agent Card (compact, for the tree) ───────────────────────────────────────

function AgentCard({
  agent,
  onClick,
}: {
  agent: AgentNode;
  onClick: () => void;
}) {
  const dot = STATUS_DOT[agent.status] || "bg-gray-400";
  const modelStyle = MODEL_COLORS[agent.model] || "bg-gray-500/20 text-gray-300";

  return (
    <div
      onClick={onClick}
      className="bg-card border border-border rounded-xl p-3 w-[200px] cursor-pointer hover:border-accent/40 transition-all hover:shadow-lg hover:shadow-accent/5"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <div className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
        <span className="text-xs font-semibold truncate">{agent.name}</span>
      </div>
      {agent.currentTask && (
        <p className="text-[10px] text-muted truncate mb-1.5">{agent.currentTask}</p>
      )}
      <div className="flex items-center justify-between">
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${modelStyle}`}>{agent.model}</span>
        <span className="text-[10px] text-muted">{agent.tasksToday} tasks</span>
      </div>
      {/* Token mini-bar */}
      {agent.tokensToday > 0 && (
        <div className="mt-1.5 h-1 bg-gray-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent/60 rounded-full"
            style={{ width: `${Math.min(100, (agent.callsToday / 50) * 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ── Division Card (commander level) ──────────────────────────────────────────

function DivisionCard({
  division,
  onClick,
}: {
  division: DivisionNode;
  onClick: () => void;
}) {
  const dot = STATUS_DOT[division.status] || "bg-gray-400";
  const modelStyle = MODEL_COLORS[division.model] || "bg-gray-500/20 text-gray-300";
  const activeCount = division.agents.filter((a) => a.status === "active" || a.status === "running").length;

  return (
    <div
      onClick={onClick}
      className="bg-card border border-border rounded-xl p-3 w-[220px] cursor-pointer hover:border-accent/40 transition-all hover:shadow-lg hover:shadow-accent/5"
    >
      <div className="flex items-center gap-2 mb-1">
        <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${dot}`} />
        <span className="text-sm font-bold truncate">{division.name.replace(" Commander", "")}</span>
      </div>
      <p className="text-[10px] text-muted mb-2 truncate">{division.description}</p>
      <div className="flex items-center justify-between">
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${modelStyle}`}>{division.model}</span>
        <span className="text-[10px] text-muted">{activeCount}/{division.agents.length} active</span>
      </div>
    </div>
  );
}

// ── Master Control Bar ───────────────────────────────────────────────────────

function MasterControlBar({
  tree,
  onRefresh,
}: {
  tree: HierarchyTree;
  onRefresh: () => void;
}) {
  const [stopping, setStopping] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const totalAgents = tree.divisions.reduce((s, d) => s + d.agents.length, 0);
  const activeAgents = tree.divisions.reduce(
    (s, d) => s + d.agents.filter((a) => a.status === "active" || a.status === "running").length,
    0
  );
  const totalTokens = tree.divisions.reduce(
    (s, d) => s + d.agents.reduce((t, a) => t + a.tokensToday, 0),
    0
  );
  const totalTasks = tree.divisions.reduce(
    (s, d) => s + d.agents.reduce((t, a) => t + a.tasksToday, 0),
    0
  );

  const handlePause = async () => {
    await postApi("/api/coordinator/pause", {});
    onRefresh();
  };

  const handleResume = async () => {
    await postApi("/api/coordinator/resume", {});
    onRefresh();
  };

  const handleEmergencyStop = async () => {
    setStopping(true);
    try {
      await postApi("/api/coordinator/emergency-stop", {});
    } finally {
      setStopping(false);
      setShowConfirm(false);
      onRefresh();
    }
  };

  return (
    <>
      <div className="bg-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          {/* Stats */}
          <div className="flex items-center gap-6">
            <div>
              <div className="text-xs text-muted">Agents</div>
              <div className="text-lg font-bold">
                <span className="text-green-400">{activeAgents}</span>
                <span className="text-muted">/{totalAgents}</span>
              </div>
            </div>
            <div>
              <div className="text-xs text-muted">Tasks Today</div>
              <div className="text-lg font-bold">{totalTasks}</div>
            </div>
            <div>
              <div className="text-xs text-muted">Tokens Today</div>
              <div className="text-lg font-bold">{formatTokens(totalTokens)}</div>
            </div>
            <div className="flex items-center gap-2">
              <div className={`w-3 h-3 rounded-full ${tree.status === "running" ? "bg-green-500 animate-pulse" : "bg-yellow-400"}`} />
              <span className="text-sm font-semibold">{tree.name}</span>
            </div>
          </div>

          {/* Controls */}
          <div className="flex items-center gap-2">
            <button
              onClick={handlePause}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-yellow-500/15 text-yellow-300 hover:bg-yellow-500/25 transition-colors"
            >
              Pause All
            </button>
            <button
              onClick={handleResume}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-green-500/15 text-green-300 hover:bg-green-500/25 transition-colors"
            >
              Resume All
            </button>
            <button
              onClick={() => setShowConfirm(true)}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-red-500/15 text-red-300 hover:bg-red-500/25 transition-colors"
            >
              Emergency Stop
            </button>
          </div>
        </div>
      </div>

      {/* Emergency Stop Confirmation */}
      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
          <div className="bg-card border border-red-500/50 rounded-2xl p-6 w-[400px] shadow-2xl">
            <h3 className="text-lg font-bold text-red-400 mb-2">Emergency Stop</h3>
            <p className="text-sm text-muted mb-4">
              This will immediately pause the coordinator and terminate all fleet workers.
              Active tasks will be interrupted. Are you sure?
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 text-sm rounded-lg bg-gray-500/15 text-gray-300 hover:bg-gray-500/25"
              >
                Cancel
              </button>
              <button
                onClick={handleEmergencyStop}
                disabled={stopping}
                className="px-4 py-2 text-sm font-bold rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {stopping ? "Stopping..." : "STOP EVERYTHING"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Main Hierarchy View ──────────────────────────────────────────────────────

export function AgentControl() {
  const [tree, setTree] = useState<HierarchyTree | null>(null);
  const [error, setError] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<AgentNode | null>(null);
  const [expandedDiv, setExpandedDiv] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const resp = await fetchApi<HierarchyTree>("/api/hierarchy/tree");
      setTree(resp);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 10000);
    return () => clearInterval(iv);
  }, [fetchData]);

  if (error && !tree) {
    return (
      <div className="flex-1 overflow-auto p-6">
        <h2 className="text-2xl font-bold mb-4">Agent Control</h2>
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-red-400">{error}</div>
      </div>
    );
  }

  if (!tree) {
    return (
      <div className="flex-1 overflow-auto p-6">
        <h2 className="text-2xl font-bold mb-4">Agent Control</h2>
        <div className="text-center text-muted py-12">Loading hierarchy...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold">Agent Control</h2>
        <p className="text-sm text-muted mt-1">4-Division command structure — click any node for details</p>
      </div>

      {/* Master Control Bar */}
      <MasterControlBar tree={tree} onRefresh={fetchData} />

      {/* Hierarchy Tree — CSS grid layout */}
      <div className="space-y-4">
        {/* Root Node */}
        <div className="flex justify-center">
          <div className="bg-card border-2 border-accent/40 rounded-xl p-4 w-[280px] text-center">
            <div className="flex items-center justify-center gap-2 mb-1">
              <div className={`w-3 h-3 rounded-full ${tree.status === "running" ? "bg-green-500 animate-pulse" : "bg-yellow-400"}`} />
              <span className="font-bold">{tree.name}</span>
            </div>
            <span className={`text-xs px-2 py-0.5 rounded-full ${MODEL_COLORS.Orchestrator}`}>{tree.model}</span>
            <p className="text-[11px] text-muted mt-1">{tree.description}</p>
          </div>
        </div>

        {/* Connector lines from root to divisions */}
        <div className="flex justify-center">
          <div className="w-px h-6 bg-border" />
        </div>
        <div className="relative flex justify-center">
          <div className="absolute top-0 left-1/2 -translate-x-1/2 h-px bg-border" style={{ width: "75%" }} />
        </div>

        {/* Division row */}
        <div className="grid grid-cols-4 gap-4">
          {tree.divisions.map((div) => (
            <div key={div.id} className="flex flex-col items-center">
              {/* Vertical connector */}
              <div className="w-px h-4 bg-border" />
              {/* Division card */}
              <DivisionCard
                division={div}
                onClick={() => setExpandedDiv(expandedDiv === div.id ? null : div.id)}
              />
              {/* Expand indicator */}
              <div className="mt-1 text-xs text-muted">
                {expandedDiv === div.id ? "▲" : "▼"} {div.agents.length} agents
              </div>
            </div>
          ))}
        </div>

        {/* Expanded division agents */}
        {expandedDiv && (
          <div className="bg-card/50 border border-border rounded-xl p-4">
            {tree.divisions
              .filter((d) => d.id === expandedDiv)
              .map((div) => (
                <div key={div.id}>
                  <h3 className="text-sm font-bold mb-3 flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${STATUS_DOT[div.status] || "bg-gray-400"}`} />
                    {div.name} — Agents
                  </h3>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                    {div.agents.map((agent) => (
                      <AgentCard
                        key={agent.id}
                        agent={agent}
                        onClick={() => setSelectedAgent(agent)}
                      />
                    ))}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Agent Detail Panel (modal) */}
      {selectedAgent && (
        <AgentDetailPanel agent={selectedAgent} onClose={() => setSelectedAgent(null)} />
      )}
    </div>
  );
}
