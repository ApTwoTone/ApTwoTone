"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface DashboardData {
  active_experiments: number;
  total_experiments: number;
  total_personas: number;
  total_revenue: number;
  by_category: Record<string, { total: number; running: number }>;
  recent_outcomes: Outcome[];
  trends_today: number;
}

interface Experiment {
  id: number;
  category: string;
  name: string;
  hypothesis: string;
  persona_id: number | null;
  config: Record<string, unknown>;
  status: string;
  started_at: string;
  completed_at: string;
  created_at: string;
  outcomes?: Outcome[];
}

interface Persona {
  id: number;
  name: string;
  platform: string;
  niche: string;
  personality: Record<string, unknown>;
  metrics: Record<string, unknown>;
  status: string;
  created_at: string;
}

interface TrendSignal {
  id: number;
  source: string;
  keyword: string;
  score: number;
  context: Record<string, unknown>;
  captured_at: string;
}

interface Outcome {
  id: number;
  experiment_id: number;
  metric_type: string;
  value: number;
  notes: string;
  recorded_at: string;
  experiment_name?: string;
  category?: string;
}

interface FailurePattern {
  category: string;
  name: string;
  hypothesis: string;
  notes: string;
  recorded_at: string;
}

type Tab = "command" | "experiments" | "personas" | "trends" | "portfolio" | "failures" | "vault" | "settings";

const CATEGORIES = ["viral_video", "seo_affiliate", "digital_product", "social_growth", "service_arbitrage"];
const CAT_LABELS: Record<string, string> = {
  viral_video: "Viral Video",
  seo_affiliate: "SEO Affiliate",
  digital_product: "Digital Product",
  social_growth: "Social Growth",
  service_arbitrage: "Service Arbitrage",
};

const PLATFORMS = ["tiktok", "youtube", "reddit", "twitter", "medium"];

function catLabel(cat: string) { return CAT_LABELS[cat] || cat; }

function statusColor(s: string) {
  switch (s) {
    case "running": return "text-green-400 bg-green-500/15";
    case "queued": return "text-yellow-400 bg-yellow-500/15";
    case "completed": return "text-blue-400 bg-blue-500/15";
    case "failed": return "text-red-400 bg-red-500/15";
    case "paused": return "text-muted bg-card";
    default: return "text-muted bg-card";
  }
}

// ── Main Component ───────────────────────────────────────────────────────────

export function RevenueLab() {
  const [tab, setTab] = useState<Tab>("command");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [trends, setTrends] = useState<TrendSignal[]>([]);
  const [failures, setFailures] = useState<FailurePattern[]>([]);
  const [portfolio, setPortfolio] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  // Filters
  const [catFilter, setCatFilter] = useState("");
  const [platFilter, setPlatFilter] = useState("");

  // New experiment form
  const [showNewExp, setShowNewExp] = useState(false);
  const [newExp, setNewExp] = useState({ category: "viral_video", name: "", hypothesis: "" });

  const fetchDashboard = useCallback(() => {
    fetchApi<DashboardData>("/api/d3/dashboard").then(setDashboard).catch(() => {});
  }, []);

  const fetchExperiments = useCallback(() => {
    const params = new URLSearchParams();
    if (catFilter) params.set("category", catFilter);
    fetchApi<{ experiments: Experiment[] }>(`/api/d3/experiments?${params}`).then(d => setExperiments(d.experiments)).catch(() => {});
  }, [catFilter]);

  const fetchPersonas = useCallback(() => {
    const params = new URLSearchParams();
    if (platFilter) params.set("platform", platFilter);
    fetchApi<{ personas: Persona[] }>(`/api/d3/personas?${params}`).then(d => setPersonas(d.personas)).catch(() => {});
  }, [platFilter]);

  const fetchTrends = useCallback(() => {
    fetchApi<{ trends: TrendSignal[] }>("/api/d3/trends?limit=100").then(d => setTrends(d.trends)).catch(() => {});
  }, []);

  const fetchFailures = useCallback(() => {
    fetchApi<{ failures: FailurePattern[] }>("/api/d3/failures").then(d => setFailures(d.failures)).catch(() => {});
  }, []);

  useEffect(() => {
    fetchDashboard();
    const iv = setInterval(fetchDashboard, 5000);
    return () => clearInterval(iv);
  }, [fetchDashboard]);

  useEffect(() => {
    if (tab === "experiments") fetchExperiments();
    if (tab === "personas") fetchPersonas();
    if (tab === "trends") fetchTrends();
    if (tab === "failures") fetchFailures();
    if (tab === "portfolio") {
      fetchApi<Record<string, unknown>>("/api/d3/portfolio").then(setPortfolio).catch(() => {});
    }
  }, [tab, fetchExperiments, fetchPersonas, fetchTrends, fetchFailures]);

  const createExperiment = async () => {
    if (!newExp.name) return;
    setLoading(true);
    try {
      await postApi("/api/d3/experiments", newExp);
      setShowNewExp(false);
      setNewExp({ category: "viral_video", name: "", hypothesis: "" });
      fetchExperiments();
    } catch { /* ignore */ }
    setLoading(false);
  };

  const runExperiment = async (id: number) => {
    setLoading(true);
    try {
      await postApi(`/api/d3/experiments/${id}/run`, {});
      fetchExperiments();
      fetchDashboard();
    } catch { /* ignore */ }
    setLoading(false);
  };

  const generatePersona = async () => {
    setLoading(true);
    try {
      await postApi("/api/d3/personas", { platform: platFilter || "tiktok", niche: "general" });
      fetchPersonas();
      fetchDashboard();
    } catch { /* ignore */ }
    setLoading(false);
  };

  const scanTrends = async () => {
    setLoading(true);
    try {
      await postApi("/api/d3/trends/scan", {});
      fetchTrends();
      fetchDashboard();
    } catch { /* ignore */ }
    setLoading(false);
  };

  const triggerSelfImprove = async () => {
    setLoading(true);
    try {
      await postApi("/api/d3/self-improve", {});
      fetchFailures();
    } catch { /* ignore */ }
    setLoading(false);
  };

  const TABS: { id: Tab; label: string }[] = [
    { id: "command", label: "Command Center" },
    { id: "experiments", label: `Experiments (${dashboard?.total_experiments ?? 0})` },
    { id: "personas", label: `Personas (${dashboard?.total_personas ?? 0})` },
    { id: "trends", label: `Trends (${dashboard?.trends_today ?? 0})` },
    { id: "portfolio", label: "Portfolio" },
    { id: "failures", label: "Failure Lab" },
    { id: "vault", label: "Content Vault" },
    { id: "settings", label: "Settings" },
  ];

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-6 pt-5 pb-3 flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-amber-400">Revenue Lab</h2>
          <p className="text-xs text-muted mt-0.5">Division Three — Autonomous Revenue Experimentation</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="px-2 py-1 rounded text-[10px] font-mono bg-amber-500/10 text-amber-400 border border-amber-500/20">
            TIER 6
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="px-6 pt-2 flex items-center gap-1 shrink-0 overflow-x-auto">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-xs font-medium rounded-t-lg transition-colors whitespace-nowrap ${
              tab === t.id
                ? "bg-card border border-b-0 border-amber-500/30 text-amber-400"
                : "text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {tab === "command" && <CommandCenter dashboard={dashboard} />}
        {tab === "experiments" && (
          <ExperimentsTab
            experiments={experiments}
            catFilter={catFilter}
            setCatFilter={setCatFilter}
            showNew={showNewExp}
            setShowNew={setShowNewExp}
            newExp={newExp}
            setNewExp={setNewExp}
            onCreate={createExperiment}
            onRun={runExperiment}
            loading={loading}
          />
        )}
        {tab === "personas" && (
          <PersonasTab
            personas={personas}
            platFilter={platFilter}
            setPlatFilter={setPlatFilter}
            onGenerate={generatePersona}
            loading={loading}
          />
        )}
        {tab === "trends" && (
          <TrendsTab trends={trends} onScan={scanTrends} loading={loading} />
        )}
        {tab === "portfolio" && <PortfolioTab portfolio={portfolio} />}
        {tab === "failures" && (
          <FailureLabTab failures={failures} onSelfImprove={triggerSelfImprove} loading={loading} />
        )}
        {tab === "vault" && <ContentVaultTab experiments={experiments} />}
        {tab === "settings" && <SettingsTab dashboard={dashboard} />}
      </div>
    </div>
  );
}

// ── Tab: Command Center ──────────────────────────────────────────────────────

function CommandCenter({ dashboard }: { dashboard: DashboardData | null }) {
  if (!dashboard) return <div className="py-8 text-center text-muted text-sm">Loading...</div>;

  return (
    <div className="pt-4 space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Active Experiments" value={dashboard.active_experiments} accent />
        <StatCard label="Total Personas" value={dashboard.total_personas} />
        <StatCard label="Total Revenue" value={`$${dashboard.total_revenue.toFixed(2)}`} accent />
        <StatCard label="Trends Today" value={dashboard.trends_today} />
      </div>

      {/* Category breakdown */}
      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-medium mb-3">Experiment Categories</h3>
        <div className="space-y-3">
          {CATEGORIES.map(cat => {
            const data = dashboard.by_category[cat] || { total: 0, running: 0 };
            const pct = dashboard.total_experiments > 0
              ? Math.round((data.total / dashboard.total_experiments) * 100)
              : 0;
            return (
              <div key={cat} className="flex items-center gap-3">
                <span className="text-xs text-muted w-28 shrink-0">{catLabel(cat)}</span>
                <div className="flex-1 h-2 bg-background rounded-full overflow-hidden">
                  <div
                    className="h-full bg-amber-500/60 rounded-full transition-all"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-xs text-muted w-16 text-right">
                  {data.total} ({data.running} active)
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Recent outcomes */}
      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-medium mb-3">Recent Activity</h3>
        {dashboard.recent_outcomes.length === 0 ? (
          <p className="text-xs text-muted">No outcomes recorded yet.</p>
        ) : (
          <div className="space-y-2">
            {dashboard.recent_outcomes.map(o => (
              <div key={o.id} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-amber-400 font-medium">{o.experiment_name || `#${o.experiment_id}`}</span>
                  <span className="text-muted">{o.metric_type}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={o.value > 0 ? "text-green-400" : "text-red-400"}>{o.value}</span>
                  <span className="text-muted">{o.recorded_at?.slice(0, 16)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className={`bg-card border rounded-lg p-4 ${accent ? "border-amber-500/30" : "border-border"}`}>
      <p className="text-xs text-muted">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${accent ? "text-amber-400" : "text-foreground"}`}>{value}</p>
    </div>
  );
}

// ── Tab: Experiments ─────────────────────────────────────────────────────────

function ExperimentsTab({
  experiments, catFilter, setCatFilter, showNew, setShowNew, newExp, setNewExp, onCreate, onRun, loading,
}: {
  experiments: Experiment[];
  catFilter: string;
  setCatFilter: (v: string) => void;
  showNew: boolean;
  setShowNew: (v: boolean) => void;
  newExp: { category: string; name: string; hypothesis: string };
  setNewExp: (v: { category: string; name: string; hypothesis: string }) => void;
  onCreate: () => void;
  onRun: (id: number) => void;
  loading: boolean;
}) {
  return (
    <div className="pt-4 space-y-4">
      {/* Filter pills + new button */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => setCatFilter("")}
          className={`px-3 py-1 text-xs rounded-full border transition-colors ${
            !catFilter ? "bg-amber-500/15 text-amber-400 border-amber-500/30" : "text-muted border-border hover:text-foreground"
          }`}
        >All</button>
        {CATEGORIES.map(c => (
          <button
            key={c}
            onClick={() => setCatFilter(catFilter === c ? "" : c)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              catFilter === c ? "bg-amber-500/15 text-amber-400 border-amber-500/30" : "text-muted border-border hover:text-foreground"
            }`}
          >{catLabel(c)}</button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => setShowNew(!showNew)}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors"
        >
          + New Experiment
        </button>
      </div>

      {/* New experiment form */}
      {showNew && (
        <div className="bg-card border border-amber-500/20 rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-muted block mb-1">Category</label>
              <select
                value={newExp.category}
                onChange={e => setNewExp({ ...newExp, category: e.target.value })}
                className="w-full bg-background border border-border rounded px-2 py-1.5 text-xs"
              >
                {CATEGORIES.map(c => <option key={c} value={c}>{catLabel(c)}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Name</label>
              <input
                value={newExp.name}
                onChange={e => setNewExp({ ...newExp, name: e.target.value })}
                placeholder="Experiment name..."
                className="w-full bg-background border border-border rounded px-2 py-1.5 text-xs"
              />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Hypothesis</label>
              <input
                value={newExp.hypothesis}
                onChange={e => setNewExp({ ...newExp, hypothesis: e.target.value })}
                placeholder="What you expect to happen..."
                className="w-full bg-background border border-border rounded px-2 py-1.5 text-xs"
              />
            </div>
          </div>
          <button
            onClick={onCreate}
            disabled={loading || !newExp.name}
            className="px-4 py-1.5 text-xs font-medium rounded-lg bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-50 transition-colors"
          >
            {loading ? "Creating..." : "Create Experiment"}
          </button>
        </div>
      )}

      {/* Experiments table */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted">
              <th className="px-3 py-2 text-left font-medium">ID</th>
              <th className="px-3 py-2 text-left font-medium">Name</th>
              <th className="px-3 py-2 text-left font-medium">Category</th>
              <th className="px-3 py-2 text-left font-medium">Status</th>
              <th className="px-3 py-2 text-left font-medium">Created</th>
              <th className="px-3 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {experiments.length === 0 ? (
              <tr><td colSpan={6} className="px-3 py-8 text-center text-muted">No experiments yet. Create one above.</td></tr>
            ) : experiments.map(e => (
              <tr key={e.id} className="border-b border-border/50 hover:bg-card-hover transition-colors">
                <td className="px-3 py-2 text-muted">#{e.id}</td>
                <td className="px-3 py-2 font-medium">{e.name}</td>
                <td className="px-3 py-2">
                  <span className="px-2 py-0.5 rounded-full text-[10px] bg-amber-500/10 text-amber-400">
                    {catLabel(e.category)}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded-full text-[10px] ${statusColor(e.status)}`}>
                    {e.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-muted">{e.created_at?.slice(0, 10)}</td>
                <td className="px-3 py-2 text-right">
                  {(e.status === "queued" || e.status === "paused") && (
                    <button
                      onClick={() => onRun(e.id)}
                      disabled={loading}
                      className="px-2 py-0.5 text-[10px] rounded bg-green-500/15 text-green-400 hover:bg-green-500/25 transition-colors"
                    >
                      Run
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Tab: Personas ────────────────────────────────────────────────────────────

function PersonasTab({
  personas, platFilter, setPlatFilter, onGenerate, loading,
}: {
  personas: Persona[];
  platFilter: string;
  setPlatFilter: (v: string) => void;
  onGenerate: () => void;
  loading: boolean;
}) {
  return (
    <div className="pt-4 space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={() => setPlatFilter("")}
          className={`px-3 py-1 text-xs rounded-full border transition-colors ${
            !platFilter ? "bg-amber-500/15 text-amber-400 border-amber-500/30" : "text-muted border-border hover:text-foreground"
          }`}
        >All</button>
        {PLATFORMS.map(p => (
          <button
            key={p}
            onClick={() => setPlatFilter(platFilter === p ? "" : p)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors capitalize ${
              platFilter === p ? "bg-amber-500/15 text-amber-400 border-amber-500/30" : "text-muted border-border hover:text-foreground"
            }`}
          >{p}</button>
        ))}
        <div className="flex-1" />
        <button
          onClick={onGenerate}
          disabled={loading}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors"
        >
          {loading ? "Generating..." : "+ Generate Persona"}
        </button>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {personas.length === 0 ? (
          <div className="col-span-3 py-8 text-center text-muted text-xs">No personas yet. Generate one above.</div>
        ) : personas.map(p => (
          <div key={p.id} className="bg-card border border-border rounded-lg p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-sm">{p.name}</span>
              <span className={`px-2 py-0.5 rounded-full text-[10px] ${p.status === "active" ? "text-green-400 bg-green-500/15" : "text-muted bg-card"}`}>
                {p.status}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-[10px] bg-amber-500/10 text-amber-400 capitalize">{p.platform}</span>
              <span className="text-xs text-muted">{p.niche}</span>
            </div>
            {(p.personality as Record<string, string>)?.tone && (
              <p className="text-[10px] text-muted">
                Tone: {String(p.personality.tone)} | Style: {String(p.personality.style || "—")}
              </p>
            )}
            <p className="text-[10px] text-muted">Created: {p.created_at?.slice(0, 10)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Tab: Trends ──────────────────────────────────────────────────────────────

function TrendsTab({
  trends, onScan, loading,
}: { trends: TrendSignal[]; onScan: () => void; loading: boolean }) {
  const grouped = trends.reduce<Record<string, TrendSignal[]>>((acc, t) => {
    (acc[t.source] = acc[t.source] || []).push(t);
    return acc;
  }, {});

  return (
    <div className="pt-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Trend Intelligence</h3>
        <button
          onClick={onScan}
          disabled={loading}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors"
        >
          {loading ? "Scanning..." : "Scan Now"}
        </button>
      </div>

      {Object.keys(grouped).length === 0 ? (
        <p className="text-xs text-muted py-8 text-center">No trends yet. Click Scan Now to discover trending topics.</p>
      ) : Object.entries(grouped).map(([source, items]) => (
        <div key={source} className="bg-card border border-border rounded-lg p-4">
          <h4 className="text-xs font-medium text-amber-400 uppercase mb-3">{source.replace("_", " ")}</h4>
          <div className="space-y-2">
            {items.slice(0, 10).map(t => (
              <div key={t.id} className="flex items-center gap-3">
                <span className="text-xs flex-1">{t.keyword}</span>
                <div className="w-24 h-1.5 bg-background rounded-full overflow-hidden">
                  <div className="h-full bg-amber-500/60 rounded-full" style={{ width: `${t.score}%` }} />
                </div>
                <span className="text-[10px] text-muted w-8 text-right">{Math.round(t.score)}</span>
                <span className="text-[10px] text-muted">{t.captured_at?.slice(0, 10)}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Tab: Portfolio ───────────────────────────────────────────────────────────

function PortfolioTab({ portfolio }: { portfolio: Record<string, unknown> | null }) {
  if (!portfolio) return <div className="py-8 text-center text-muted text-sm">Loading portfolio analysis...</div>;

  const byCat = (portfolio.by_category || {}) as Record<string, { total: number; completed: number; failed: number; revenue: number }>;
  const analysis = (portfolio.analysis || {}) as Record<string, unknown>;
  const totalRev = (portfolio.total_revenue as number) || 0;

  return (
    <div className="pt-4 space-y-6">
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Total Revenue" value={`$${totalRev.toFixed(2)}`} accent />
        <StatCard label="Total Experiments" value={(portfolio.total_experiments as number) || 0} />
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-medium mb-3">Revenue by Category</h3>
        <div className="space-y-3">
          {CATEGORIES.map(cat => {
            const data = byCat[cat] || { total: 0, completed: 0, failed: 0, revenue: 0 };
            const maxRev = Math.max(...Object.values(byCat).map(c => c.revenue), 1);
            const pct = Math.round((data.revenue / maxRev) * 100);
            return (
              <div key={cat} className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span>{catLabel(cat)}</span>
                  <span className="text-amber-400">${data.revenue.toFixed(2)}</span>
                </div>
                <div className="h-2 bg-background rounded-full overflow-hidden">
                  <div className="h-full bg-amber-500/60 rounded-full transition-all" style={{ width: `${pct}%` }} />
                </div>
                <div className="flex gap-4 text-[10px] text-muted">
                  <span>{data.total} total</span>
                  <span>{data.completed} completed</span>
                  <span>{data.failed} failed</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {(analysis.summary as string) && (
        <div className="bg-card border border-amber-500/20 rounded-lg p-4">
          <h3 className="text-sm font-medium text-amber-400 mb-2">AI Analysis</h3>
          <p className="text-xs text-muted whitespace-pre-wrap">{String(analysis.summary)}</p>
          {Array.isArray(analysis.recommendations) && (
            <ul className="mt-2 space-y-1">
              {(analysis.recommendations as string[]).map((r, i) => (
                <li key={i} className="text-xs text-muted flex gap-2">
                  <span className="text-amber-400">-</span> {r}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ── Tab: Failure Lab ─────────────────────────────────────────────────────────

function FailureLabTab({
  failures, onSelfImprove, loading,
}: { failures: FailurePattern[]; onSelfImprove: () => void; loading: boolean }) {
  return (
    <div className="pt-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Failure Patterns</h3>
        <button
          onClick={onSelfImprove}
          disabled={loading}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors"
        >
          {loading ? "Analyzing..." : "Trigger Self-Improvement"}
        </button>
      </div>

      {failures.length === 0 ? (
        <p className="text-xs text-muted py-8 text-center">No failure patterns recorded yet. Run experiments first.</p>
      ) : (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="px-3 py-2 text-left font-medium">Category</th>
                <th className="px-3 py-2 text-left font-medium">Experiment</th>
                <th className="px-3 py-2 text-left font-medium">Notes</th>
                <th className="px-3 py-2 text-left font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((f, i) => (
                <tr key={i} className="border-b border-border/50">
                  <td className="px-3 py-2">
                    <span className="px-2 py-0.5 rounded-full text-[10px] bg-amber-500/10 text-amber-400">
                      {catLabel(f.category)}
                    </span>
                  </td>
                  <td className="px-3 py-2">{f.name}</td>
                  <td className="px-3 py-2 text-muted max-w-xs truncate">{f.notes?.slice(0, 100)}</td>
                  <td className="px-3 py-2 text-muted">{f.recorded_at?.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Tab: Content Vault ───────────────────────────────────────────────────────

function ContentVaultTab({ experiments }: { experiments: Experiment[] }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<Experiment | null>(null);

  useEffect(() => {
    if (selected) {
      fetchApi<Experiment>(`/api/d3/experiments/${selected}`).then(setDetail).catch(() => {});
    }
  }, [selected]);

  const withContent = experiments.filter(e => e.status === "completed" || e.status === "running");

  return (
    <div className="pt-4 space-y-4">
      <h3 className="text-sm font-medium">Generated Content</h3>

      {withContent.length === 0 ? (
        <p className="text-xs text-muted py-8 text-center">No content generated yet. Complete experiments first.</p>
      ) : (
        <div className="grid grid-cols-4 gap-3">
          {withContent.map(e => (
            <button
              key={e.id}
              onClick={() => setSelected(e.id)}
              className={`text-left bg-card border rounded-lg p-3 transition-colors hover:bg-card-hover ${
                selected === e.id ? "border-amber-500/30" : "border-border"
              }`}
            >
              <p className="text-xs font-medium truncate">{e.name}</p>
              <p className="text-[10px] text-amber-400 mt-1">{catLabel(e.category)}</p>
              <p className="text-[10px] text-muted mt-0.5">{e.status}</p>
            </button>
          ))}
        </div>
      )}

      {detail && detail.outcomes && detail.outcomes.length > 0 && (
        <div className="bg-card border border-border rounded-lg p-4">
          <h4 className="text-xs font-medium mb-2">{detail.name} — Outcomes</h4>
          <div className="space-y-2">
            {detail.outcomes.map(o => (
              <div key={o.id} className="text-xs">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-amber-400 font-medium">{o.metric_type}</span>
                  <span className="text-muted">= {o.value}</span>
                </div>
                {o.notes && (
                  <pre className="text-[10px] text-muted bg-background rounded p-2 overflow-x-auto max-h-40 whitespace-pre-wrap">
                    {o.notes.slice(0, 2000)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Tab: Settings ────────────────────────────────────────────────────────────

function SettingsTab({ dashboard }: { dashboard: DashboardData | null }) {
  return (
    <div className="pt-4 space-y-4">
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-medium">Division Three Configuration</h3>
        <div className="grid grid-cols-2 gap-4 text-xs">
          <div>
            <p className="text-muted">Worker Tier</p>
            <p className="font-medium text-amber-400">Tier 6</p>
          </div>
          <div>
            <p className="text-muted">Active Experiments</p>
            <p className="font-medium">{dashboard?.active_experiments ?? 0}</p>
          </div>
          <div>
            <p className="text-muted">Self-Improvement Rate Limit</p>
            <p className="font-medium">1 per hour</p>
          </div>
          <div>
            <p className="text-muted">Primary AI Provider</p>
            <p className="font-medium">ZAI GLM (unlimited)</p>
          </div>
          <div>
            <p className="text-muted">Fallback Provider</p>
            <p className="font-medium">Ollama Local (qwen3:8b)</p>
          </div>
          <div>
            <p className="text-muted">Categories</p>
            <p className="font-medium">{CATEGORIES.length}</p>
          </div>
        </div>
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-medium mb-2">Experiment Categories</h3>
        <div className="space-y-1">
          {CATEGORIES.map(c => (
            <div key={c} className="flex items-center justify-between text-xs py-1">
              <span>{catLabel(c)}</span>
              <span className="text-green-400">Enabled</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
