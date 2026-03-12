"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { fetchApi, useSSE } from "@/lib/api";

/* ── Live Intel — Real-time intelligence feed for SFV/Greater LA events ── */

interface EventSignal {
  id: string;
  event_type: string;
  estimated_date: string;
  estimated_location: string;
  guest_count: number;
  source_platform: string;
  bathroom_needed: "high" | "medium" | "low";
  detected_at: string;
  raw_text: string;
}

interface VendorActivity {
  id: string;
  vendor_name: string;
  signal_type: string;
  signal_detail: string;
  detected_at: string;
  has_partnership: boolean;
}

interface OutreachItem {
  id: string;
  vendor_name: string;
  urgency: "high" | "medium" | "low";
  type: string;
  message_preview: string;
  created_at: string;
}

interface WorkerActivity {
  worker_id: string;
  description: string;
  timestamp: string;
}

interface IntelData {
  event_signals: EventSignal[];
  vendor_activity: VendorActivity[];
  outreach_queue: OutreachItem[];
  worker_activity: WorkerActivity[];
}

const BATHROOM_COLORS = {
  high: "bg-green-500",
  medium: "bg-yellow-500",
  low: "bg-gray-500",
};

const URGENCY_COLORS = {
  high: "text-red-400 bg-red-500/10",
  medium: "text-yellow-400 bg-yellow-500/10",
  low: "text-gray-400 bg-gray-500/10",
};

interface ActivityEntry {
  id: number;
  ts: number;
  agent_id: string;
  action: string;
  detail: string;
}

function SystemActivityPanel() {
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = useSSE<ActivityEntry>(
      "/api/agents/activity-stream",
      (entry) => {
        setActivities((prev) => [entry, ...prev].slice(0, 100));
      },
    );
    return close;
  }, []);

  // Fallback: fetch recent if SSE not connected
  useEffect(() => {
    fetchApi<{ activity: ActivityEntry[] }>("/api/agents/activity")
      .then((res) => {
        if (res.activity?.length) setActivities(res.activity.reverse());
      })
      .catch(() => {});
  }, []);

  return (
    <div className="bg-card border border-border rounded-xl p-4">
      <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
        <span className="text-base">⚡</span>
        System Activity
        <span className="ml-auto text-xs text-success animate-pulse">● Live</span>
      </h3>
      <div ref={scrollRef} className="space-y-1 max-h-[400px] overflow-y-auto font-mono">
        {activities.length === 0 && (
          <p className="text-xs text-muted py-4 text-center font-sans">
            Waiting for agent activity...
          </p>
        )}
        {activities.map((item) => (
          <div key={item.id || item.ts} className="text-[11px] text-muted flex gap-2">
            <span className="text-accent shrink-0">{item.agent_id}</span>
            <span className="text-white/60 shrink-0">{item.action}</span>
            <span className="truncate">{item.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function LiveIntel() {
  const [data, setData] = useState<IntelData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const result = await fetchApi<IntelData>("/api/intel/live");
      setData(result);
    } catch {
      // Intel may not have data yet — show empty state
      setData({
        event_signals: [],
        vendor_activity: [],
        outreach_queue: [],
        worker_activity: [],
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 10000); // 10s refresh
    return () => clearInterval(interval);
  }, [load]);

  if (loading && !data) {
    return (
      <div className="flex-1 p-6 overflow-auto">
        <div className="text-center text-muted py-12">Loading intel feed...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold">Live Intel</h2>
          <p className="text-sm text-muted mt-1">
            Real-time event signals, vendor activity, and outreach queue
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          <span className="text-xs text-muted">Live — refreshing every 10s</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Panel 1: Event Signals */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <span className="text-base">📡</span>
            Live Event Signals
            <span className="ml-auto text-xs text-muted">
              {data?.event_signals.length || 0} detected
            </span>
          </h3>
          <div className="space-y-2 max-h-[400px] overflow-y-auto">
            {data?.event_signals.length === 0 && (
              <p className="text-xs text-muted py-4 text-center">
                No event signals detected yet. Agents are scanning...
              </p>
            )}
            {data?.event_signals.map((signal) => (
              <div
                key={signal.id}
                className="p-3 bg-background rounded-lg border border-border"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">{signal.event_type}</span>
                  <div className="flex items-center gap-1.5">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        BATHROOM_COLORS[signal.bathroom_needed]
                      }`}
                    />
                    <span className="text-[10px] text-muted uppercase">
                      {signal.bathroom_needed === "high"
                        ? "Needs bathroom"
                        : signal.bathroom_needed === "medium"
                          ? "Possible"
                          : "Unclear"}
                    </span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2 text-xs text-muted">
                  <span>{signal.estimated_location}</span>
                  <span>•</span>
                  <span>{signal.estimated_date}</span>
                  {signal.guest_count > 0 && (
                    <>
                      <span>•</span>
                      <span>{signal.guest_count} guests</span>
                    </>
                  )}
                  <span>•</span>
                  <span>{signal.source_platform}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Panel 2: Vendor Activity */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <span className="text-base">🏢</span>
            Vendor Activity
            <span className="ml-auto text-xs text-muted">
              {data?.vendor_activity.length || 0} signals
            </span>
          </h3>
          <div className="space-y-2 max-h-[400px] overflow-y-auto">
            {data?.vendor_activity.length === 0 && (
              <p className="text-xs text-muted py-4 text-center">
                No vendor activity detected yet.
              </p>
            )}
            {data?.vendor_activity.map((item) => (
              <div
                key={item.id}
                className="p-3 bg-background rounded-lg border border-border"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">{item.vendor_name}</span>
                  {item.has_partnership && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-400 border border-green-500/20">
                      Partner
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted">{item.signal_detail}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Panel 3: Outreach Queue */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
            <span className="text-base">📤</span>
            Outreach Queue
            <span className="ml-auto text-xs text-muted">
              {data?.outreach_queue.length || 0} pending
            </span>
          </h3>
          <div className="space-y-2 max-h-[400px] overflow-y-auto">
            {data?.outreach_queue.length === 0 && (
              <p className="text-xs text-muted py-4 text-center">
                No outreach items pending approval.
              </p>
            )}
            {data?.outreach_queue.map((item) => (
              <div
                key={item.id}
                className={`p-3 bg-background rounded-lg border ${
                  item.urgency === "high"
                    ? "border-red-500/30"
                    : "border-border"
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium">{item.vendor_name}</span>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded ${
                      URGENCY_COLORS[item.urgency]
                    }`}
                  >
                    {item.urgency.toUpperCase()}
                  </span>
                </div>
                <p className="text-xs text-muted truncate">{item.message_preview}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Panel 4: System Activity (SSE real-time) */}
        <SystemActivityPanel />
      </div>
    </div>
  );
}
