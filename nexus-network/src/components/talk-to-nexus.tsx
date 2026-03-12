"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ───────────────────────────────────────────────────────────────────

interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  agent?: string;
  model?: string;
  category?: string;
  latency_ms?: number;
  build_id?: number;
  build_status?: string;
  created_at: string;
}

interface ActiveBuild {
  id: number;
  description: string;
  status: string;
  stage_model: string;
}

interface SystemSnapshot {
  vendor_count: number;
  active_workers: number;
  pending_approvals: number;
  active_builds: number;
}

const SUGGESTED_COMMANDS = [
  "Filter my vendors",
  "Show top 20 leads",
  "Send test batch",
  "Campaign status",
  "Check system health",
  "What is my CPL this week",
];

const COMMAND_GUIDE: { group: string; icon: string; commands: { phrase: string; desc: string }[] }[] = [
  {
    group: "Vendors", icon: "🤝",
    commands: [
      { phrase: "Filter my vendors", desc: "Score & filter all vendors by location, category, email validity" },
      { phrase: "Show top 20 leads", desc: "Display highest-scoring eligible vendors" },
      { phrase: "Score my vendors", desc: "Run enrichment pipeline on all vendors" },
      { phrase: "Show eligible vendors", desc: "List campaign-ready vendors" },
    ],
  },
  {
    group: "Email Campaigns", icon: "📧",
    commands: [
      { phrase: "Send test batch", desc: "Send 20 test emails to your inbox for review" },
      { phrase: "Start campaign", desc: "Create campaign & send Telegram approval" },
      { phrase: "Campaign status", desc: "Show active campaign stats (sent/bounced/replied)" },
      { phrase: "Pause campaign", desc: "Pause the active drip campaign" },
      { phrase: "Resume campaign", desc: "Resume a paused campaign" },
    ],
  },
  {
    group: "Leads & Data", icon: "📊",
    commands: [
      { phrase: "Show today's lead activity", desc: "Recent lead interactions" },
      { phrase: "What is my CPL this week", desc: "Cost per lead from ads" },
      { phrase: "Show pending approvals", desc: "Items waiting for your approval" },
      { phrase: "Check vendor count by zone", desc: "Vendor distribution by area" },
    ],
  },
  {
    group: "System", icon: "⚙️",
    commands: [
      { phrase: "Check system health", desc: "Run full system health scan" },
      { phrase: "Show worker status", desc: "Active fleet workers" },
      { phrase: "Build weekly ad brief", desc: "Generate creative brief" },
    ],
  },
];

const CATEGORY_ICONS: Record<string, { icon: string; color: string }> = {
  build: { icon: "🔨", color: "text-yellow-400" },
  vendor: { icon: "🤝", color: "text-green-400" },
  creative: { icon: "🎨", color: "text-purple-400" },
  revenue: { icon: "💰", color: "text-emerald-400" },
  data: { icon: "📊", color: "text-blue-400" },
  system: { icon: "⚙️", color: "text-orange-400" },
  general: { icon: "💬", color: "text-cyan-400" },
};

// ── Formatted Message (safe markdown: bold, code, newlines) ─────────────────

function FormattedMessage({ text }: { text: string }) {
  // Strip raw tool markers and leaked tool calls
  let cleaned = text
    .replace(/===TOOL===\w+===ARGS===[\s\S]*?===END===/g, "")
    .replace(/===DB_QUERY===([\s\S]*?)===END===/g, "$1")
    .replace(/===FILE===[\s\S]*?===END===/g, "")
    .replace(/===\w+===/g, "")
    .replace(/\w+===ARGS=\{[^}]*\}/g, "[Querying database...]")
    .trim();

  // Split on code fences first
  const segments = cleaned.split(/(```[\s\S]*?```)/g);

  const rendered: React.ReactNode[] = [];
  let key = 0;

  for (const segment of segments) {
    if (segment.startsWith("```")) {
      // Code block
      const lines = segment.slice(3, -3);
      const firstNewline = lines.indexOf("\n");
      const code = firstNewline >= 0 ? lines.slice(firstNewline + 1) : lines;
      rendered.push(
        <pre key={key++} className="bg-black/30 border border-border/50 rounded-lg p-3 my-2 overflow-x-auto text-[11px] font-mono text-green-300/90 leading-relaxed">
          {code}
        </pre>
      );
    } else {
      // Inline formatting: bold + inline code
      const parts: React.ReactNode[] = [];
      const pattern = /(\*\*(.+?)\*\*|`([^`]+)`)/g;
      let lastIndex = 0;
      let match: RegExpExecArray | null;

      while ((match = pattern.exec(segment)) !== null) {
        if (match.index > lastIndex) {
          parts.push(<span key={key++}>{segment.slice(lastIndex, match.index)}</span>);
        }
        if (match[2]) {
          parts.push(<strong key={key++}>{match[2]}</strong>);
        } else if (match[3]) {
          parts.push(
            <code key={key++} className="bg-background/30 px-1 rounded text-xs font-mono">{match[3]}</code>
          );
        }
        lastIndex = match.index + match[0].length;
      }
      if (lastIndex < segment.length) {
        parts.push(<span key={key++}>{segment.slice(lastIndex)}</span>);
      }
      if (parts.length > 0) {
        rendered.push(<span key={key++}>{parts}</span>);
      }
    }
  }

  return <p className="whitespace-pre-wrap">{rendered}</p>;
}

// ── Build Progress Card (with SSE streaming) ────────────────────────────────

interface StageCost {
  input: number;
  output: number;
  cost: number;
}

function BuildProgressCard({ buildId, status: initialStatus }: { buildId: number; status: string }) {
  const STAGES = ["architect", "building", "reviewing", "patching", "claude_review"];
  const LABELS = ["Architect", "Build", "Review", "Patch", "Claude Gate"];
  const STAGE_KEYS = ["architect", "builder", "reviewer", "patcher", "claude_review"];

  const [liveStatus, setLiveStatus] = useState(initialStatus);
  const liveStatusRef = useRef(initialStatus);
  const currentIdx = STAGES.indexOf(liveStatus);
  const done = liveStatus === "merged" || liveStatus === "approved";
  const failed = liveStatus === "rejected" || liveStatus === "failed" || liveStatus === "escalated";
  const isRunning = !done && !failed;

  const [streamOutput, setStreamOutput] = useState<string[]>([]);
  const [stageCosts, setStageCosts] = useState<Record<string, StageCost>>({});
  const [totalCost, setTotalCost] = useState(0);
  const [expanded, setExpanded] = useState(true);
  const [streaming, setStreaming] = useState(false);
  const outputRef = useRef<HTMLDivElement>(null);

  // Fetch real status on mount (don't trust stale DB prop) + poll every 3s
  useEffect(() => {
    const API_BASE = "http://localhost:7860";
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/builds/${buildId}`);
        if (res.ok) {
          const data = await res.json();
          const s = data.status || data.build?.status;
          if (s && s !== liveStatusRef.current) {
            liveStatusRef.current = s;
            setLiveStatus(s);
          }
        }
      } catch { /* ignore */ }
    };
    fetchStatus(); // Immediate fetch on mount
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, [buildId]);

  // SSE streaming connection — connect for any non-terminal status
  useEffect(() => {
    if (!isRunning) return;

    const API_BASE = "http://localhost:7860";
    const source = new EventSource(`${API_BASE}/api/builds/${buildId}/stream`);
    setStreaming(true);

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === "token") {
          setStreamOutput((prev) => {
            const last = prev[prev.length - 1] || "";
            const updated = [...prev.slice(0, -1), last + data.text];
            const lines = updated.join("").split("\n");
            return lines.slice(-30);
          });
        } else if (data.type === "stage_start") {
          // Update live status from SSE events
          const stageStatusMap: Record<string, string> = {
            architect: "architect", builder: "building",
            reviewer: "reviewing", patcher: "patching",
          };
          if (stageStatusMap[data.stage]) setLiveStatus(stageStatusMap[data.stage]);
          setStreamOutput((prev) => [...prev, "", `── Stage: ${data.stage} (${data.model}) ──`]);
        } else if (data.type === "stage_complete") {
          setStageCosts((prev) => ({
            ...prev,
            [data.stage]: { input: 0, output: 0, cost: data.cost },
          }));
          setTotalCost((prev) => prev + (data.cost || 0));
          setStreamOutput((prev) => [
            ...prev, `── ${data.stage} complete ($${data.cost?.toFixed(2) || "free"}) ──`, "",
          ]);
        } else if (data.type === "build_complete") {
          setTotalCost(data.total_cost || 0);
          setLiveStatus("claude_review");
          setStreaming(false);
          source.close();
        } else if (data.type === "error") {
          setStreamOutput((prev) => [...prev, `ERROR: ${data.message}`]);
          setLiveStatus("failed");
          setStreaming(false);
          source.close();
        } else if (data.type === "timeout") {
          setStreaming(false);
          source.close();
        }
      } catch {
        // skip malformed events
      }
    };

    source.onerror = () => {
      setStreaming(false);
      source.close();
    };

    return () => {
      setStreaming(false);
      source.close();
    };
  }, [buildId, isRunning]);

  // Auto-scroll output
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [streamOutput]);

  return (
    <div className="bg-background/50 border border-border rounded-lg p-3 mt-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-mono text-muted">Build #{buildId}</span>
        <div className="flex items-center gap-2">
          {totalCost > 0 && (
            <span className="text-[10px] font-mono text-cyan-400">${totalCost.toFixed(2)}</span>
          )}
          <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
            done ? "bg-green-500/20 text-green-400" :
            failed ? "bg-red-500/20 text-red-400" :
            "bg-yellow-500/20 text-yellow-400"
          }`}>
            {liveStatus.replace("_", " ")}
          </span>
        </div>
      </div>

      {/* Stage progress bar with per-stage cost */}
      <div className="flex items-center gap-1">
        {LABELS.map((label, i) => {
          const stageDone = done || i < currentIdx;
          const stageActive = !done && !failed && i === currentIdx;
          const stageKey = STAGE_KEYS[i];
          const cost = stageCosts[stageKey];
          return (
            <div key={label} className="flex items-center gap-1">
              <div className={`w-2 h-2 rounded-full ${
                stageDone ? "bg-green-400" : stageActive ? "bg-yellow-400 animate-pulse" : "bg-border"
              }`} />
              <span className={`text-[9px] ${
                stageDone ? "text-green-400" : stageActive ? "text-yellow-400" : "text-muted/50"
              }`}>
                {label}{cost ? ` ($${cost.cost.toFixed(2)})` : ""}
              </span>
              {i < LABELS.length - 1 && <div className="w-2 h-px bg-border" />}
            </div>
          );
        })}
      </div>

      {/* Streaming output panel */}
      {(streaming || streamOutput.length > 0) && (
        <div className="mt-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[9px] text-muted hover:text-foreground transition-colors mb-1"
          >
            {expanded ? "▼ Hide output" : "▶ Show output"}
          </button>
          {expanded && (
            <div
              ref={outputRef}
              className="bg-black/30 rounded border border-border/50 p-2 max-h-40 overflow-y-auto font-mono text-[10px] text-green-300/80 leading-relaxed"
            >
              {streamOutput.map((line, i) => (
                <div key={i} className={line.startsWith("──") ? "text-cyan-400 mt-1" : line.startsWith("ERROR") ? "text-red-400" : ""}>
                  {line || "\u00A0"}
                </div>
              ))}
              {streaming && <span className="animate-pulse">▊</span>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Quick Actions Sidebar ───────────────────────────────────────────────────

function QuickActionsSidebar({ snapshot, collapsed, onToggle, onCommand }: {
  snapshot: SystemSnapshot | null;
  collapsed: boolean;
  onToggle: () => void;
  onCommand: (cmd: string) => void;
}) {
  const [guideOpen, setGuideOpen] = useState(false);
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

  return (
    <div className={`shrink-0 border-l border-border bg-sidebar transition-all duration-200 flex flex-col ${
      collapsed ? "w-10" : "w-64"
    }`}>
      <button
        onClick={onToggle}
        className="w-full px-3 py-3 text-xs text-muted hover:text-foreground transition-colors flex items-center justify-center"
      >
        {collapsed ? "◀" : "▶ Collapse"}
      </button>
      {!collapsed && (
        <div className="flex-1 overflow-y-auto px-3 pb-4 space-y-4">
          {/* System Status */}
          {snapshot && (
            <div>
              <h4 className="text-[10px] font-semibold text-muted uppercase mb-2">System Status</h4>
              <div className="space-y-1.5">
                {[
                  { label: "Vendors", value: snapshot.vendor_count.toLocaleString(), color: "text-green-400" },
                  { label: "Active Workers", value: String(snapshot.active_workers), color: "text-blue-400" },
                  { label: "Pending Approvals", value: String(snapshot.pending_approvals), color: snapshot.pending_approvals > 0 ? "text-yellow-400" : "text-muted" },
                  { label: "Active Builds", value: String(snapshot.active_builds), color: snapshot.active_builds > 0 ? "text-accent" : "text-muted" },
                ].map(s => (
                  <div key={s.label} className="flex items-center justify-between">
                    <span className="text-xs text-muted">{s.label}</span>
                    <span className={`text-xs font-mono font-medium ${s.color}`}>{s.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Command Guide Toggle */}
          <div>
            <button
              onClick={() => setGuideOpen(!guideOpen)}
              className="w-full flex items-center justify-between text-[10px] font-semibold text-muted uppercase hover:text-foreground transition-colors"
            >
              <span>Command Guide</span>
              <span className="text-xs">{guideOpen ? "▾" : "▸"}</span>
            </button>

            {guideOpen && (
              <div className="mt-2 space-y-1">
                {COMMAND_GUIDE.map(group => (
                  <div key={group.group}>
                    <button
                      onClick={() => setExpandedGroup(expandedGroup === group.group ? null : group.group)}
                      className="w-full flex items-center gap-1.5 px-1.5 py-1.5 rounded-md text-xs text-muted hover:text-foreground hover:bg-card-hover transition-colors"
                    >
                      <span className="text-sm">{group.icon}</span>
                      <span className="font-medium">{group.group}</span>
                      <span className="ml-auto text-[9px] text-muted/60">{group.commands.length}</span>
                    </button>
                    {expandedGroup === group.group && (
                      <div className="ml-2 border-l border-border/50 pl-2 space-y-0.5">
                        {group.commands.map(cmd => (
                          <button
                            key={cmd.phrase}
                            onClick={() => onCommand(cmd.phrase)}
                            className="w-full text-left px-2 py-1.5 rounded text-[11px] text-muted hover:text-foreground hover:bg-card-hover transition-colors group"
                            title={cmd.desc}
                          >
                            <div className="font-medium text-foreground/80 group-hover:text-foreground">{cmd.phrase}</div>
                            <div className="text-[9px] text-muted/70 leading-tight">{cmd.desc}</div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Active Build Banner ─────────────────────────────────────────────────────

function ActiveBuildBanner({ build }: { build: ActiveBuild | null }) {
  if (!build) return null;

  const STAGES = ["architect", "building", "reviewing", "patching", "claude_review"];
  const LABELS = ["Architect", "Build", "Review", "Patch", "Gate"];
  const idx = STAGES.indexOf(build.status);

  return (
    <div className="px-4 py-2 bg-accent/5 border-b border-border flex items-center gap-4">
      <span className="text-[10px] font-semibold text-accent uppercase">Active Build</span>
      <span className="text-xs text-foreground">#{build.id}: {build.description.slice(0, 60)}</span>
      <div className="flex items-center gap-1 ml-auto">
        {LABELS.map((label, i) => (
          <div key={label} className="flex items-center gap-0.5">
            <div className={`w-1.5 h-1.5 rounded-full ${
              i < idx ? "bg-green-400" : i === idx ? "bg-yellow-400 animate-pulse" : "bg-border"
            }`} />
            <span className={`text-[8px] ${
              i < idx ? "text-green-400" : i === idx ? "text-yellow-400" : "text-muted/50"
            }`}>{label}</span>
            {i < LABELS.length - 1 && <div className="w-2 h-px bg-border mx-0.5" />}
          </div>
        ))}
      </div>
      {build.stage_model && (
        <span className="text-[9px] text-muted ml-2">{build.stage_model}</span>
      )}
    </div>
  );
}

// ── Claude Spend Counter ────────────────────────────────────────────────────

function ClaudeSpendCounter() {
  const [spend, setSpend] = useState(0);

  useEffect(() => {
    const fetchSpend = async () => {
      try {
        const data = await fetchApi<{ spend_today: number }>("/api/talk/claude-spend");
        setSpend(data.spend_today || 0);
      } catch {
        // silent
      }
    };
    fetchSpend();
    const interval = setInterval(fetchSpend, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <span className={`text-[10px] ${spend > 0 ? "text-amber-400" : "text-muted"}`}>
      Claude today: ${spend.toFixed(3)}
    </span>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────

export function TalkToNexus() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [snapshot, setSnapshot] = useState<SystemSnapshot | null>(null);
  const [activeBuild, setActiveBuild] = useState<ActiveBuild | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const sendingRef = useRef(false);
  const userScrolledUp = useRef(false);
  const [showNewMsg, setShowNewMsg] = useState(false);
  const [showPalette, setShowPalette] = useState(false);
  const [paletteIndex, setPaletteIndex] = useState(0);

  // Flatten COMMAND_GUIDE for slash palette search
  const allCommands = useRef(
    COMMAND_GUIDE.flatMap(g => g.commands.map(c => ({ ...c, group: g.group, icon: g.icon })))
  );

  // Filtered commands for palette
  const paletteCommands = showPalette
    ? (() => {
        const filter = input.slice(1).toLowerCase();
        return allCommands.current
          .filter(c => !filter || c.phrase.toLowerCase().includes(filter) || c.desc.toLowerCase().includes(filter))
          .slice(0, 8);
      })()
    : [];

  // Track user scroll position
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const atBottom = scrollHeight - scrollTop - clientHeight < 100;
    userScrolledUp.current = !atBottom;
    if (atBottom) setShowNewMsg(false);
  }, []);

  // Auto-scroll only when user is at bottom
  useEffect(() => {
    if (scrollRef.current && !userScrolledUp.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    } else if (userScrolledUp.current && messages.length > 0) {
      const last = messages[messages.length - 1];
      if (last?.role === "assistant" && last?.model) {
        setShowNewMsg(true);
      }
    }
  }, [messages]);

  // Fetch message history + system snapshot
  const fetchHistory = useCallback(async () => {
    if (sendingRef.current) return; // Skip while sending to prevent duplicates
    try {
      const [historyData, snapshotData, buildData] = await Promise.all([
        fetchApi<{ messages: ChatMessage[] }>("/api/talk/history?limit=50").catch(() => ({ messages: [] })),
        fetchApi<SystemSnapshot>("/api/talk/snapshot").catch(() => null),
        fetchApi<{ builds: ActiveBuild[] }>("/api/builds?status=").catch(() => ({ builds: [] })),
      ]);
      setMessages(historyData.messages || []);
      if (snapshotData) setSnapshot(snapshotData);
      // Find active build
      const active = (buildData.builds || []).find((b: ActiveBuild) =>
        !["merged", "approved", "rejected", "failed"].includes(b.status)
      );
      setActiveBuild(active || null);
    } catch {
      // Silent fail
    }
  }, []);

  useEffect(() => {
    fetchHistory();
    const interval = setInterval(fetchHistory, 5000);
    return () => clearInterval(interval);
  }, [fetchHistory]);

  // Send message via SSE streaming
  const sendMessage = async () => {
    const text = input.trim();
    if (!text || sendingRef.current) return;  // Use ref (sync) for dedup
    sendingRef.current = true;
    setSending(true);
    userScrolledUp.current = false;  // Auto-scroll on send
    setInput("");

    const tempMsg: ChatMessage = {
      id: Date.now(),
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    const streamMsgId = Date.now() + 1;
    const streamMsg: ChatMessage = {
      id: streamMsgId,
      role: "assistant",
      content: "",
      agent: "Nexus AI",
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, tempMsg, streamMsg]);

    try {
      const API_BASE = "http://localhost:7860";
      const url = `${API_BASE}/api/talk/stream?message=${encodeURIComponent(text)}`;
      const source = new EventSource(url);
      let statusParts: string[] = [];

      source.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === "thinking") {
            statusParts.push(data.content);
            setMessages(prev => prev.map(m =>
              m.id === streamMsgId ? { ...m, content: statusParts.join(" → ") + "..." } : m
            ));
          } else if (data.type === "memory") {
            statusParts.push("Memory: " + data.content.slice(0, 40));
            setMessages(prev => prev.map(m =>
              m.id === streamMsgId ? { ...m, content: statusParts.join(" → ") + "..." } : m
            ));
          } else if (data.type === "provider") {
            // Provider info — will be shown on the final message
          } else if (data.type === "complete" && data.message) {
            // Replace stream msg with final response
            setMessages(prev => {
              const filtered = prev.filter(m => m.id !== streamMsgId);
              return [...filtered, data.message];
            });
            source.close();
            setSending(false);
            sendingRef.current = false;
            inputRef.current?.focus();
          } else if (data.type === "error") {
            setMessages(prev => prev.map(m =>
              m.id === streamMsgId ? { ...m, content: "Error: " + data.content, agent: "system" } : m
            ));
            source.close();
            setSending(false);
            sendingRef.current = false;
          }
        } catch {
          // Skip malformed events
        }
      };

      let retryCount = 0;
      const MAX_RETRIES = 3;

      source.onerror = () => {
        source.close();
        if (!sendingRef.current) return;

        if (retryCount < MAX_RETRIES) {
          retryCount++;
          const delay = Math.pow(2, retryCount - 1) * 1000; // 1s, 2s, 4s
          setMessages(prev => prev.map(m =>
            m.id === streamMsgId ? { ...m, content: `Reconnecting... (attempt ${retryCount}/${MAX_RETRIES})` } : m
          ));
          setTimeout(() => {
            if (!sendingRef.current) return;
            const retrySrc = new EventSource(url);
            retrySrc.onmessage = source.onmessage;
            retrySrc.onerror = source.onerror;
            // Replace source reference for timeout cleanup
            Object.assign(source, retrySrc);
          }, delay);
        } else {
          // Final fallback: POST
          postApi<{ message: ChatMessage }>("/api/talk/send", { message: text })
            .then(response => {
              setMessages(prev => {
                const filtered = prev.filter(m => m.id !== streamMsgId);
                return [...filtered, response.message].filter(Boolean) as ChatMessage[];
              });
            })
            .catch(() => {
              setMessages(prev => prev.map(m =>
                m.id === streamMsgId ? { ...m, content: "Connection failed. Try again.", agent: "system" } : m
              ));
            })
            .finally(() => {
              setSending(false);
              sendingRef.current = false;
              inputRef.current?.focus();
            });
        }
      };

      // Safety timeout: 40s max
      setTimeout(() => {
        if (sendingRef.current) {
          source.close();
          setSending(false);
          sendingRef.current = false;
          setMessages(prev => prev.map(m =>
            m.id === streamMsgId && !m.model
              ? { ...m, content: "Request timed out. Try again." }
              : m
          ));
        }
      }, 40000);
    } catch (e) {
      setMessages(prev => prev.map(m =>
        m.id === streamMsgId
          ? { ...m, content: "Failed: " + String(e), agent: "system" }
          : m
      ));
      setSending(false);
      sendingRef.current = false;
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (showPalette && paletteCommands.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPaletteIndex(i => Math.min(i + 1, paletteCommands.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPaletteIndex(i => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        setInput(paletteCommands[paletteIndex].phrase);
        setShowPalette(false);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setShowPalette(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Brain status bar */}
        <div className="px-6 py-1.5 flex items-center justify-between border-b border-border/50 shrink-0">
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            <span className="text-[10px] text-muted">Brain: Free models (ZAI + Cerebras + Groq)</span>
          </div>
          <ClaudeSpendCounter />
        </div>

        {/* Active build banner */}
        <ActiveBuildBanner build={activeBuild} />

        {/* Messages */}
        <div ref={scrollRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-6 py-4 space-y-4 relative">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="text-4xl mb-4">💬</div>
              <h2 className="text-xl font-bold text-foreground mb-2">Talk to Nexus</h2>
              <p className="text-sm text-muted max-w-md">
                Type anything in plain English. Nexus routes it to the right agent automatically.
                Build features, query data, control the system — all from here.
              </p>
            </div>
          )}

          {messages.filter((msg, idx, arr) => arr.findIndex(m => m.id === msg.id) === idx).map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div className={`max-w-[70%] ${msg.role === "user" ? "order-2" : ""}`}>
                {/* Agent/model badge */}
                {msg.role === "assistant" && (
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-base">
                      {CATEGORY_ICONS[msg.category || "general"]?.icon || "💬"}
                    </span>
                    <span className={`text-[10px] font-medium ${
                      CATEGORY_ICONS[msg.category || "general"]?.color || "text-muted"
                    }`}>
                      {msg.agent || "Nexus"}
                    </span>
                    {msg.model && (
                      <span className={`text-[9px] ${
                        msg.model?.includes("claude") ? "text-amber-400" : "text-emerald-400"
                      }`}>
                        {msg.model}
                      </span>
                    )}
                    <span className="text-[9px] text-muted">
                      {msg.model?.includes("claude") ? "$0.003" : "free"}
                    </span>
                    {msg.latency_ms != null && (
                      <span className="text-[9px] text-muted">
                        {msg.latency_ms < 1000
                          ? `${msg.latency_ms}ms`
                          : `${(msg.latency_ms / 1000).toFixed(1)}s`}
                      </span>
                    )}
                  </div>
                )}

                {/* Message bubble */}
                <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-accent text-white rounded-br-md"
                    : "bg-card border border-border text-foreground rounded-bl-md"
                } ${msg.content === "Thinking..." ? "animate-pulse" : ""}`}>
                  <FormattedMessage text={msg.content} />
                </div>

                {/* Build progress card */}
                {msg.build_id && msg.build_status && (
                  <BuildProgressCard buildId={msg.build_id} status={msg.build_status} />
                )}

                {/* Timestamp */}
                <p className={`text-[9px] text-muted mt-1 ${
                  msg.role === "user" ? "text-right" : ""
                }`}>
                  {new Date(msg.created_at).toLocaleTimeString()}
                </p>
              </div>
            </div>
          ))}

          {sending && (
            <div className="flex justify-start">
              <div className="bg-card border border-border rounded-2xl rounded-bl-md px-4 py-2.5">
                <div className="flex items-center gap-1">
                  <div className="w-2 h-2 rounded-full bg-accent animate-bounce" style={{ animationDelay: "0ms" }} />
                  <div className="w-2 h-2 rounded-full bg-accent animate-bounce" style={{ animationDelay: "150ms" }} />
                  <div className="w-2 h-2 rounded-full bg-accent animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            </div>
          )}

          {/* New message indicator when scrolled up */}
          {showNewMsg && (
            <div className="sticky bottom-2 flex justify-center">
              <button
                onClick={() => {
                  userScrolledUp.current = false;
                  setShowNewMsg(false);
                  scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
                }}
                className="px-3 py-1.5 text-[11px] bg-accent text-white rounded-full shadow-lg hover:bg-accent-hover transition-colors"
              >
                New message ↓
              </button>
            </div>
          )}
        </div>

        {/* Suggested commands */}
        <div className="px-6 py-2 flex items-center gap-2 overflow-x-auto shrink-0">
          {SUGGESTED_COMMANDS.map(cmd => (
            <button
              key={cmd}
              onClick={() => { setInput(cmd); inputRef.current?.focus(); }}
              className="px-3 py-1.5 text-[11px] text-muted hover:text-foreground bg-card border border-border rounded-full whitespace-nowrap transition-colors hover:bg-card-hover"
            >
              {cmd}
            </button>
          ))}
        </div>

        {/* Input area */}
        <div className="px-6 py-4 border-t border-border bg-sidebar">
          <div className="flex items-end gap-3 max-w-3xl mx-auto">
            <div className="flex-1 relative">
              {/* Slash command palette */}
              {showPalette && paletteCommands.length > 0 && (
                <div className="absolute bottom-full left-0 right-0 mb-2 bg-card border border-border rounded-xl shadow-lg overflow-hidden z-50 max-h-72 overflow-y-auto">
                  {paletteCommands.map((cmd, i) => (
                    <button
                      key={cmd.phrase}
                      onClick={() => {
                        setInput(cmd.phrase);
                        setShowPalette(false);
                        inputRef.current?.focus();
                      }}
                      className={`w-full px-4 py-2.5 flex items-center gap-3 text-left text-sm transition-colors ${
                        i === paletteIndex ? "bg-accent/20 text-foreground" : "text-muted hover:bg-card-hover hover:text-foreground"
                      }`}
                    >
                      <span className="text-base">{cmd.icon}</span>
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate">{cmd.phrase}</div>
                        <div className="text-[10px] text-muted truncate">{cmd.desc}</div>
                      </div>
                      <span className="ml-auto text-[10px] text-muted/60 shrink-0">{cmd.group}</span>
                    </button>
                  ))}
                </div>
              )}
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => {
                  const val = e.target.value;
                  setInput(val);
                  if (val.startsWith("/")) {
                    setShowPalette(true);
                    setPaletteIndex(0);
                  } else {
                    setShowPalette(false);
                  }
                }}
                onKeyDown={handleKeyDown}
                placeholder="Tell Nexus what to do..."
                rows={1}
                className="w-full px-4 py-3 bg-background border border-border rounded-xl text-sm text-foreground placeholder-muted resize-none focus:outline-none focus:ring-1 focus:ring-accent/50 focus:border-accent/50"
                style={{ minHeight: "44px", maxHeight: "120px" }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = "auto";
                  target.style.height = Math.min(target.scrollHeight, 120) + "px";
                }}
              />
            </div>
            {/* Microphone placeholder */}
            <button
              className="w-11 h-11 flex items-center justify-center rounded-xl border border-border text-muted hover:text-foreground transition-colors"
              title="Voice input (coming soon)"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            </button>
            {/* Send button */}
            <button
              onClick={sendMessage}
              disabled={!input.trim() || sending}
              className="w-11 h-11 flex items-center justify-center rounded-xl bg-accent text-white hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Quick actions sidebar */}
      <QuickActionsSidebar
        snapshot={snapshot}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        onCommand={(cmd) => { setInput(cmd); inputRef.current?.focus(); }}
      />
    </div>
  );
}
