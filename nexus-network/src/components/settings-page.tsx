"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { fetchApi, postApi } from "@/lib/api";

interface ChangelogEntry {
  id: number;
  version: string;
  build_hash: string;
  changed_files: string;
  summary: string;
  created_at: string;
}

interface FleetProvider {
  id: string;
  name: string;
  model: string;
  keys_total: number;
  keys_active: number;
  rpm_total: number;
}

interface MilestoneData {
  fired: Record<string, string>;
  total_milestones_hit: number;
  counts: { vendors?: number; outreach?: number; bookings?: number };
  next_milestones: Record<string, { target: number; current: number; remaining: number }>;
}

interface ResourceData {
  cpu_percent: number;
  ram_percent: number;
  scale_factor: number;
}

interface KeyRegResult {
  status: string;
  provider: string;
  provider_name: string;
  validated: boolean;
  key_preview: string;
  provider_key_count: number;
  total_keys: number;
  message?: string;
  error?: string;
}

const PROVIDER_COLORS: Record<string, string> = {
  groq: "text-orange-400",
  cerebras: "text-blue-400",
  gemini: "text-cyan-400",
  openrouter: "text-purple-400",
  mistral: "text-yellow-400",
  huggingface: "text-amber-400",
  moonshot: "text-indigo-400",
  zai: "text-green-400",
  apifreellm: "text-pink-400",
};

function detectProviderClient(key: string): string {
  if (key.startsWith("sk-or-v1-")) return "openrouter";
  if (key.startsWith("csk-")) return "cerebras";
  if (key.startsWith("gsk_")) return "groq";
  if (key.startsWith("AIzaSy")) return "gemini";
  if (key.startsWith("hf_")) return "huggingface";
  if (key.startsWith("apf_")) return "apifreellm";
  if (key.includes(".") && key.length > 40) {
    const parts = key.split(".");
    if (parts.length === 2 && /^[0-9a-f]+$/.test(parts[0])) return "zai";
  }
  if (key.startsWith("sk-") && !key.startsWith("sk-or") && !key.startsWith("sk-ant")) return "moonshot";
  if (key.length === 32 && /^[a-zA-Z0-9]+$/.test(key)) return "mistral";
  return "";
}

export function SettingsPage() {
  const [providers, setProviders] = useState<FleetProvider[]>([]);
  const [milestones, setMilestones] = useState<MilestoneData | null>(null);
  const [resources, setResources] = useState<ResourceData | null>(null);
  const [serverStatus, setServerStatus] = useState<"online" | "offline" | "checking">("checking");
  const [changelog, setChangelog] = useState<ChangelogEntry[]>([]);
  const [keyInput, setKeyInput] = useState("");
  const [detectedProvider, setDetectedProvider] = useState("");
  const [registering, setRegistering] = useState(false);
  const [regResult, setRegResult] = useState<KeyRegResult | null>(null);
  const [regHistory, setRegHistory] = useState<KeyRegResult[]>([]);
  const keyInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [fleetData, msData, resData] = await Promise.all([
        fetchApi<{ providers: FleetProvider[] }>("/api/fleet/status").catch(() => ({ providers: [] })),
        fetchApi<MilestoneData>("/api/milestones").catch(() => null),
        fetchApi<ResourceData>("/api/fleet/resources").catch(() => null),
      ]);
      const clData = await fetchApi<{ entries: ChangelogEntry[] }>("/api/ui/changelog").catch(() => ({ entries: [] }));
      setProviders(fleetData.providers || []);
      if (msData) setMilestones(msData);
      if (resData) setResources(resData);
      setChangelog(clData.entries || []);
      setServerStatus("online");
    } catch {
      setServerStatus("offline");
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const handleKeyInput = (val: string) => {
    setKeyInput(val);
    setDetectedProvider(detectProviderClient(val.trim()));
    setRegResult(null);
  };

  const registerKey = async () => {
    if (!keyInput.trim()) return;
    setRegistering(true);
    setRegResult(null);
    try {
      const res = await postApi<KeyRegResult>("/api/keys/register", { key: keyInput.trim() });
      setRegResult(res);
      if (res.status === "ok" || res.status === "duplicate") {
        setRegHistory(prev => [res, ...prev]);
        setKeyInput("");
        setDetectedProvider("");
        // Refresh providers
        load();
        // Auto-focus back to input for next key
        setTimeout(() => keyInputRef.current?.focus(), 100);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Registration failed";
      setRegResult({ status: "error", provider: "", provider_name: "", validated: false, key_preview: "", provider_key_count: 0, total_keys: 0, error: msg });
    } finally {
      setRegistering(false);
    }
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-xl font-bold mb-1">Settings</h2>
        <p className="text-sm text-muted mb-6">System configuration, API status, and milestones</p>

        {/* Server Status */}
        <div className="bg-card border border-border rounded-xl p-5 mb-6">
          <h3 className="text-sm font-semibold mb-4">System Status</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${
                serverStatus === "online" ? "bg-green-400 animate-pulse" :
                serverStatus === "offline" ? "bg-red-400" : "bg-yellow-400 animate-pulse"
              }`} />
              <div>
                <p className="text-xs font-medium">Backend Server</p>
                <p className="text-[10px] text-muted capitalize">{serverStatus}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${providers.length > 0 ? "bg-green-400" : "bg-red-400"}`} />
              <div>
                <p className="text-xs font-medium">AI Fleet</p>
                <p className="text-[10px] text-muted">{providers.length} providers</p>
              </div>
            </div>
            {resources && (
              <>
                <div>
                  <p className="text-xs font-medium">CPU</p>
                  <p className={`text-sm font-bold ${resources.cpu_percent > 80 ? "text-red-400" : "text-green-400"}`}>
                    {resources.cpu_percent.toFixed(1)}%
                  </p>
                </div>
                <div>
                  <p className="text-xs font-medium">RAM</p>
                  <p className={`text-sm font-bold ${resources.ram_percent > 85 ? "text-red-400" : "text-green-400"}`}>
                    {resources.ram_percent.toFixed(1)}%
                  </p>
                </div>
              </>
            )}
          </div>
        </div>

        {/* API Keys / Providers */}
        <div className="bg-card border border-border rounded-xl p-5 mb-6">
          <h3 className="text-sm font-semibold mb-4">API Providers</h3>
          {providers.length === 0 ? (
            <p className="text-sm text-muted">No providers configured. Start the backend server.</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {providers.map(p => (
                <div key={p.id} className="flex items-center justify-between p-3 bg-background rounded-lg border border-border">
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${p.keys_active === p.keys_total ? "bg-green-400" : p.keys_active > 0 ? "bg-yellow-400" : "bg-red-400"}`} />
                    <div>
                      <p className="text-xs font-medium">{p.name}</p>
                      <p className="text-[10px] text-muted">{p.model}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-xs font-medium">{p.keys_active}/{p.keys_total} keys</p>
                    <p className="text-[10px] text-muted">{p.rpm_total} RPM</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Key Registration */}
        <div className="bg-card border border-accent/30 rounded-xl p-5 mb-6">
          <h3 className="text-sm font-semibold mb-3">Register API Key</h3>
          <div className="flex gap-2 items-center">
            <div className="flex-1 relative">
              <input
                ref={keyInputRef}
                type="text"
                value={keyInput}
                onChange={e => handleKeyInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !registering) registerKey(); }}
                placeholder="Paste API key here..."
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:border-accent/50 placeholder:text-muted"
                autoComplete="off"
                spellCheck={false}
              />
              {detectedProvider && (
                <span className={`absolute right-3 top-1/2 -translate-y-1/2 text-xs font-medium ${PROVIDER_COLORS[detectedProvider] || "text-muted"}`}>
                  {detectedProvider.toUpperCase()}
                </span>
              )}
            </div>
            <button
              onClick={registerKey}
              disabled={registering || !keyInput.trim() || !detectedProvider}
              className="px-4 py-2 bg-accent text-white text-sm font-medium rounded-lg disabled:opacity-40 hover:bg-accent/80 transition-colors shrink-0"
            >
              {registering ? "..." : "Register"}
            </button>
          </div>

          {/* Result feedback */}
          {regResult && (
            <div className={`mt-3 p-2.5 rounded-lg text-xs ${
              regResult.status === "ok" ? "bg-green-500/10 border border-green-500/20 text-green-400" :
              regResult.status === "duplicate" ? "bg-yellow-500/10 border border-yellow-500/20 text-yellow-400" :
              "bg-red-500/10 border border-red-500/20 text-red-400"
            }`}>
              {regResult.status === "ok" && (
                <span>Registered <span className="font-medium">{regResult.key_preview}</span> → {regResult.provider_name} ({regResult.provider_key_count} keys) | Total: {regResult.total_keys}</span>
              )}
              {regResult.status === "duplicate" && (
                <span>Key already registered for {regResult.provider}</span>
              )}
              {regResult.status === "error" && (
                <span>{regResult.error || "Registration failed"}</span>
              )}
            </div>
          )}

          {/* Recent registrations */}
          {regHistory.length > 0 && (
            <div className="mt-3 space-y-1 max-h-32 overflow-y-auto">
              {regHistory.map((r, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] px-2 py-1 rounded bg-background">
                  <span className={`font-medium ${PROVIDER_COLORS[r.provider] || "text-muted"}`}>{r.provider_name || r.provider}</span>
                  <span className="font-mono text-muted">{r.key_preview}</span>
                  <span className={r.status === "ok" ? "text-green-400" : "text-yellow-400"}>
                    {r.status === "ok" ? "saved" : r.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Milestones */}
        {milestones && (
          <div className="bg-card border border-border rounded-xl p-5 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold">Milestones</h3>
              <span className="text-xs text-muted">{milestones.total_milestones_hit} achieved</span>
            </div>

            {/* Next targets */}
            {Object.keys(milestones.next_milestones).length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
                {Object.entries(milestones.next_milestones).map(([category, info]) => (
                  <div key={category} className="p-3 bg-background rounded-lg border border-border">
                    <p className="text-[10px] text-muted uppercase mb-1">{category}</p>
                    <p className="text-sm font-bold">
                      {info.current.toLocaleString()} / {info.target.toLocaleString()}
                    </p>
                    <div className="w-full h-1.5 bg-card rounded-full overflow-hidden mt-1.5">
                      <div
                        className="h-full bg-accent rounded-full"
                        style={{ width: `${Math.min(100, (info.current / info.target) * 100)}%` }}
                      />
                    </div>
                    <p className="text-[10px] text-muted mt-1">{info.remaining} remaining</p>
                  </div>
                ))}
              </div>
            )}

            {/* Fired milestones */}
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {Object.entries(milestones.fired).reverse().map(([key, date]) => (
                <div key={key} className="flex items-center justify-between text-xs px-2 py-1.5 rounded hover:bg-background transition-colors">
                  <span className="font-medium">{key.replace(/_/g, " ")}</span>
                  <span className="text-muted">{new Date(date).toLocaleDateString()}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Pricing Configuration */}
        <div className="bg-card border border-border rounded-xl p-5 mb-6">
          <h3 className="text-sm font-semibold mb-4">Pricing Tiers</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="p-4 bg-background rounded-lg border border-green-500/20">
              <p className="text-xs text-green-400 font-medium mb-1">Tier 1: 0-10 mi</p>
              <p className="text-lg font-bold">$1,000</p>
              <p className="text-[10px] text-muted mt-1">Venue rate: $1,200 ($200 kickback)</p>
            </div>
            <div className="p-4 bg-background rounded-lg border border-yellow-500/20">
              <p className="text-xs text-yellow-400 font-medium mb-1">Tier 2: 10-20 mi</p>
              <p className="text-lg font-bold">$1,200 + mileage</p>
              <p className="text-[10px] text-muted mt-1">Venue rate: $1,500 ($300 kickback)</p>
            </div>
            <div className="p-4 bg-background rounded-lg border border-red-500/20">
              <p className="text-xs text-red-400 font-medium mb-1">Tier 3: 20+ mi</p>
              <p className="text-lg font-bold">$1,500 + mileage</p>
              <p className="text-[10px] text-muted mt-1">Individual bookings only</p>
            </div>
          </div>
          <div className="mt-3 p-3 bg-background rounded-lg border border-border">
            <p className="text-xs text-muted">
              Deposit: $100 booking + $60 damage = <span className="text-foreground font-medium">$160 total</span> |
              Balance due 14 days before event | Card on file required
            </p>
          </div>
        </div>

        {/* Notification Preferences */}
        <div className="bg-card border border-border rounded-xl p-5 mb-6">
          <h3 className="text-sm font-semibold mb-4">Notification Rules</h3>
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between p-2 bg-background rounded-lg">
              <span>Max Telegram pings per hour</span>
              <span className="font-medium text-accent">3-5</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-background rounded-lg">
              <span>New lead notification</span>
              <span className="font-medium text-green-400">Immediate</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-background rounded-lg">
              <span>Ad performance reports</span>
              <span className="font-medium">Every 6 hours</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-background rounded-lg">
              <span>Daily digest (AM)</span>
              <span className="font-medium">8:00 AM</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-background rounded-lg">
              <span>Evening report</span>
              <span className="font-medium">6:00 PM</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-background rounded-lg">
              <span>Milestone notifications</span>
              <span className="font-medium text-green-400">Enabled</span>
            </div>
          </div>
        </div>

        {/* UI Changelog */}
        <div className="bg-card border border-border rounded-xl p-5 mb-6">
          <h3 className="text-sm font-semibold mb-4">UI Changelog</h3>
          {changelog.length === 0 ? (
            <p className="text-sm text-muted">No builds recorded yet. The frontend watcher will log changes here.</p>
          ) : (
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {changelog.map(entry => (
                <div key={entry.id} className="flex items-center justify-between text-xs px-2 py-1.5 rounded hover:bg-background transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-accent">{entry.build_hash}</span>
                    <span className="text-muted">{entry.summary}</span>
                  </div>
                  <span className="text-muted shrink-0 ml-2">
                    {entry.created_at ? new Date(entry.created_at + "Z").toLocaleString() : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* App Info */}
        <div className="text-center text-xs text-muted py-4">
          <p>Nexus Network v1.0 | Zoar Command Center</p>
          <p className="mt-1">{providers.length} AI providers | {providers.reduce((s, p) => s + p.keys_total, 0)} API keys | $0/month additional cost</p>
        </div>
      </div>
    </div>
  );
}
