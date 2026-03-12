"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

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
  groq: "G",
  cerebras: "C",
  gemini: "G",
  openrouter: "OR",
  mistral: "M",
  huggingface: "HF",
  moonshot: "K",
  zai: "Z",
  apifreellm: "AF",
  ollama: "OL",
};

interface WorkerStatus {
  total_active: number;
  shift: string;
}

interface TaskStats {
  today?: { completed: number; tokens: number };
}

export function FleetStatusBanner() {
  const [fleet, setFleet] = useState<FleetData | null>(null);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  const [taskStats, setTaskStats] = useState<TaskStats | null>(null);
  const [vendorCount, setVendorCount] = useState<number>(0);

  const load = useCallback(async () => {
    try {
      const [fleetData, workerData, taskData, vendorData] = await Promise.all([
        fetchApi<FleetData>("/api/fleet/status").catch(() => null),
        fetchApi<WorkerStatus>("/api/fleet/workers").catch(() => null),
        fetchApi<TaskStats>("/api/fleet/tasks").catch(() => null),
        fetchApi<{ total: number }>("/api/vendors/stats").catch(() => null),
      ]);
      if (fleetData) setFleet(fleetData);
      if (workerData) setWorkers(workerData);
      if (taskData) setTaskStats(taskData);
      if (vendorData) setVendorCount(vendorData.total || 0);
    } catch {
      // Fleet status may not be available yet
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, [load]);

  if (!fleet) return null;

  const activeWorkers = workers?.total_active || 0;
  const tasksToday = taskStats?.today?.completed || fleet.total_calls_today;

  return (
    <div className="px-4 py-2 bg-card border-b border-border flex items-center gap-4 text-xs">
      <div className="flex items-center gap-1.5">
        <div className="w-2 h-2 rounded-full bg-success animate-pulse" />
        <span className="font-medium">Fleet Online</span>
      </div>
      <span className="text-muted">|</span>
      <span>
        <span className="text-accent font-bold">{activeWorkers}</span>{" "}
        <span className="text-muted">workers</span>
      </span>
      <span>
        <span className="text-accent font-bold">
          {tasksToday.toLocaleString()}
        </span>{" "}
        <span className="text-muted">tasks</span>
      </span>
      <span>
        <span className="text-accent font-bold">
          {vendorCount.toLocaleString()}
        </span>{" "}
        <span className="text-muted">vendors</span>
      </span>
      <span>
        <span className="text-accent font-bold">
          {fleet.total_rpm.toLocaleString()}
        </span>{" "}
        <span className="text-muted">RPM</span>
      </span>
      <div className="flex-1" />
      <span className="text-muted">
        {fleet.total_keys} keys | {fleet.total_providers} providers |{" "}
        {workers?.shift || "day"} shift
      </span>
    </div>
  );
}

export function FleetDashboard() {
  const [fleet, setFleet] = useState<FleetData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, { ok: boolean; latency_ms: number; content: string }>
  >({});

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchApi<FleetData>("/api/fleet/status");
      setFleet(data);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, [load]);

  const testProvider = async (providerId: string) => {
    setTesting(providerId);
    try {
      const result = await postApi<{
        ok: boolean;
        latency_ms: number;
        content: string;
      }>(`/api/fleet/test/${providerId}`, {});
      setTestResults((prev) => ({ ...prev, [providerId]: result }));
    } catch {
      setTestResults((prev) => ({
        ...prev,
        [providerId]: { ok: false, latency_ms: 0, content: "Request failed" },
      }));
    } finally {
      setTesting(null);
    }
  };

  if (loading && !fleet) {
    return (
      <div className="flex-1 p-6 overflow-auto">
        <div className="text-center text-muted py-12">
          Loading fleet status...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 p-6 overflow-auto">
        <div className="text-center text-danger py-12">{error}</div>
      </div>
    );
  }

  if (!fleet) return null;

  return (
    <div className="flex-1 p-6 overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold">AI Fleet Command</h2>
          <p className="text-sm text-muted mt-1">
            {fleet.total_providers} providers, {fleet.total_keys} keys,{" "}
            {fleet.total_rpm.toLocaleString()} RPM capacity
          </p>
        </div>
        <button
          onClick={load}
          className="px-4 py-2 text-sm bg-card border border-border rounded-lg hover:bg-card-hover transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Top Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted uppercase tracking-wider">
            Providers
          </p>
          <p className="text-2xl font-bold mt-1 text-accent">
            {fleet.total_providers}
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted uppercase tracking-wider">
            API Keys
          </p>
          <p className="text-2xl font-bold mt-1 text-accent">
            {fleet.total_keys}
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted uppercase tracking-wider">
            Total RPM
          </p>
          <p className="text-2xl font-bold mt-1 text-green-400">
            {fleet.total_rpm.toLocaleString()}
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted uppercase tracking-wider">
            Parallel Workers
          </p>
          <p className="text-2xl font-bold mt-1 text-purple-400">
            {fleet.parallel_workers}
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted uppercase tracking-wider">
            Daily Capacity
          </p>
          <p className="text-2xl font-bold mt-1 text-yellow-400">
            {fleet.total_daily_capacity > 100000
              ? (fleet.total_daily_capacity / 1000000).toFixed(1) + "M"
              : fleet.total_daily_capacity.toLocaleString()}
          </p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted uppercase tracking-wider">
            Calls Today
          </p>
          <p className="text-2xl font-bold mt-1">
            {fleet.total_calls_today.toLocaleString()}
          </p>
        </div>
      </div>

      {/* Provider Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {fleet.providers.map((prov) => {
          const color =
            PROVIDER_COLORS[prov.id] ||
            "text-gray-400 bg-gray-500/10 border-gray-500/20";
          const icon = PROVIDER_ICONS[prov.id] || "?";
          const tr = testResults[prov.id];

          return (
            <div
              key={prov.id}
              className="bg-card border border-border rounded-xl p-4"
            >
              {/* Provider Header */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div
                    className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold border ${color}`}
                  >
                    {icon}
                  </div>
                  <div>
                    <p className="text-sm font-semibold">{prov.name}</p>
                    <p className="text-xs text-muted truncate max-w-[180px]">
                      {prov.model}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  {prov.keys_active === prov.keys_total ? (
                    <div className="w-2 h-2 rounded-full bg-success" />
                  ) : prov.keys_active > 0 ? (
                    <div className="w-2 h-2 rounded-full bg-yellow-400" />
                  ) : (
                    <div className="w-2 h-2 rounded-full bg-red-400" />
                  )}
                  <span className="text-xs text-muted">
                    {prov.keys_active}/{prov.keys_total}
                  </span>
                </div>
              </div>

              {/* Metrics */}
              <div className="grid grid-cols-3 gap-2 mb-3">
                <div className="text-center">
                  <p className="text-xs text-muted">RPM</p>
                  <p className="text-sm font-bold">{prov.rpm_total}</p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted">RPD</p>
                  <p className="text-sm font-bold">
                    {prov.rpd_total > 100000
                      ? Math.round(prov.rpd_total / 1000) + "K"
                      : prov.rpd_total.toLocaleString()}
                  </p>
                </div>
                <div className="text-center">
                  <p className="text-xs text-muted">Today</p>
                  <p className="text-sm font-bold">{prov.calls_today}</p>
                </div>
              </div>

              {/* Strength */}
              <p className="text-xs text-muted mb-2">{prov.strength}</p>

              {/* Task Types */}
              <div className="flex flex-wrap gap-1 mb-3">
                {prov.task_types.map((t) => (
                  <span
                    key={t}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-background border border-border text-muted"
                  >
                    {t}
                  </span>
                ))}
              </div>

              {/* Test Button + Result */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => testProvider(prov.id)}
                  disabled={testing === prov.id}
                  className="px-3 py-1 text-xs bg-background border border-border rounded hover:bg-card-hover transition-colors disabled:opacity-50"
                >
                  {testing === prov.id ? "Testing..." : "Ping"}
                </button>
                {tr && (
                  <span
                    className={`text-xs ${tr.ok ? "text-green-400" : "text-red-400"}`}
                  >
                    {tr.ok
                      ? `OK (${tr.latency_ms}ms)`
                      : `FAIL: ${tr.content.slice(0, 40)}`}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
