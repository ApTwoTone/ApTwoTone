"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { asArray, fetchApi, postApi } from "@/lib/api";

interface BetaRun {
  id: number;
  mode: string;
  status: string;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  summary_json: string;
  error: string;
}

interface BetaRunSummary {
  signals?: number;
  collected?: number;
  extracted?: number;
  scored?: number;
  validated?: number;
  decisioned?: number;
  stored?: number;
  dry_run?: boolean;
}

interface BetaStageMetric {
  run_id: number;
  stage_name: string;
  input_count: number;
  output_count: number;
  status: string;
  duration_ms: number;
  notes: string;
  created_at: string;
}

interface BetaEvent {
  id: number;
  run_id: number;
  stage_name: string;
  level: "info" | "warn" | "error" | string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface BetaLead {
  id: number;
  run_id: number;
  venue_name: string;
  city: string;
  region: string;
  website: string;
  category: string;
  tier: string;
  score: number;
  best_pitch_angle: string;
  signal_source: string;
  contact_email: string;
  contact_phone: string;
}

interface ProviderEntry {
  key_name: string;
  configured: boolean;
  preview: string;
  used_for_beta: boolean;
}

interface SafetyProfile {
  mode: string;
  outbound_disabled: boolean;
  platform_logins_disabled: boolean;
  blocked_platform_logins: string[];
  safe_sources: string[];
  writes_to_production_db: boolean;
  separate_beta_db: string;
  launch_behavior: string;
}

interface BetaDiagnostics {
  run_id: number;
  drop_stage: string;
  reason: string;
  google_places_key_configured: boolean;
  yelp_key_configured: boolean;
  summary: string;
}

interface BetaStatus {
  running: boolean;
  current_run_id: number | null;
  total_runs: number;
  total_beta_leads: number;
  queued_review: number;
  tier_counts: Record<string, number>;
  last_error: string;
  safety: SafetyProfile;
  policy: Record<string, unknown>;
  last_run: BetaRun | null;
  providers: ProviderEntry[];
  latest_diagnostics?: BetaDiagnostics;
}

interface RunpodCheckpoint {
  index: number;
  cap_usd: number;
  passed: boolean;
  active: boolean;
}

interface RunpodSpendMeter {
  current_usd: number;
  max_usd: number;
  percent: number;
}

interface RunpodPhaseMetric {
  phase_name: string;
  input_count: number;
  output_count: number;
  status: string;
  duration_ms: number;
  spend_after_usd: number;
  notes: string;
  created_at: string;
}

interface RunpodRunState {
  id: number;
  status: string;
  phase: string;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  spend_usd: number;
  max_credits_usd: number;
  promoted: number;
  report_path: string;
  error: string;
}

interface RunpodSprintStatus {
  running: boolean;
  current_run_id: number | null;
  stop_requested: boolean;
  last_error: string;
  run: RunpodRunState | null;
  runtime: Record<string, unknown>;
  preflight: {
    ok?: boolean;
    meta_credentials_ok?: boolean;
    beta_db_ok?: boolean;
    template_pack_ok?: boolean;
    ffmpeg_ok?: boolean;
    pillow_ok?: boolean;
    source_image_count?: number;
    missing_dependencies?: string[];
    warnings?: string[];
  };
  outputs: Record<string, number>;
  phase_metrics: RunpodPhaseMetric[];
  spend_meter: RunpodSpendMeter;
  phase_checkpoints: RunpodCheckpoint[];
}

interface RunpodSprintPlan {
  ok: boolean;
  estimate?: {
    lead_output_validated?: number;
    cold_email_priority_leads?: number;
    creative_statics_usable?: number;
    creative_videos_usable?: number;
    campaign_entities?: {
      campaigns?: number;
      adsets?: number;
      ads?: number;
    };
  };
  preflight?: {
    ok?: boolean;
    start_config_json?: string;
    missing_dependencies?: string[];
    warnings?: string[];
  };
}

const STAGES = [
  "public_signals",
  "collection",
  "extraction",
  "scoring",
  "validation",
  "decision",
  "launch",
  "feedback",
  "reinforcement",
] as const;

function stageLabel(id: string): string {
  return id
    .split("_")
    .map((w) => w.slice(0, 1).toUpperCase() + w.slice(1))
    .join(" ");
}

function timeAgo(ts: string): string {
  if (!ts) return "—";
  const raw = ts.includes("T") ? ts : ts.replace(" ", "T") + "Z";
  const ms = new Date(raw).getTime();
  if (!Number.isFinite(ms)) return "—";
  const diff = Math.max(0, Date.now() - ms);
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function parseSummary(run: BetaRun | undefined | null): BetaRunSummary | null {
  if (!run?.summary_json) return null;
  try {
    const parsed = JSON.parse(run.summary_json);
    if (parsed && typeof parsed === "object") return parsed as BetaRunSummary;
  } catch {
    // ignore invalid summary
  }
  return null;
}

function compactPayload(payload: Record<string, unknown>): string {
  const keys = Object.keys(payload || {});
  if (keys.length === 0) return "";
  const preview: Record<string, unknown> = {};
  for (const k of keys.slice(0, 4)) preview[k] = payload[k];
  return JSON.stringify(preview);
}

export function BetaResearch() {
  const [status, setStatus] = useState<BetaStatus | null>(null);
  const [runs, setRuns] = useState<BetaRun[]>([]);
  const [stages, setStages] = useState<BetaStageMetric[]>([]);
  const [events, setEvents] = useState<BetaEvent[]>([]);
  const [leads, setLeads] = useState<BetaLead[]>([]);
  const [tierFilter, setTierFilter] = useState<"" | "A" | "B" | "C">("");
  const [selectedRunId, setSelectedRunId] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [runningNow, setRunningNow] = useState(false);
  const [feedbackBusy, setFeedbackBusy] = useState<number | null>(null);
  const [sprintStatus, setSprintStatus] = useState<RunpodSprintStatus | null>(null);
  const [sprintPlan, setSprintPlan] = useState<RunpodSprintPlan | null>(null);
  const [sprintBusy, setSprintBusy] = useState<"" | "plan" | "start" | "stop" | "promote">("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [statusRes, runsRes, sprintRes] = await Promise.all([
        fetchApi<BetaStatus>("/api/beta-research/status"),
        fetchApi<{ runs: BetaRun[] }>("/api/beta-research/runs?limit=20"),
        fetchApi<RunpodSprintStatus>("/api/runpod-sprint/status"),
      ]);
      const allRuns = asArray<BetaRun>(runsRes?.runs);
      const runId = selectedRunId > 0 ? selectedRunId : Number(statusRes?.last_run?.id || 0);
      const runParam = runId > 0 ? `&run_id=${runId}` : "";

      const [stageRes, leadsRes, eventsRes] = await Promise.all([
        fetchApi<{ stages: BetaStageMetric[] }>(`/api/beta-research/stages?limit=20${runParam}`),
        fetchApi<{ leads: BetaLead[] }>(
          `/api/beta-research/leads?limit=120${tierFilter ? `&tier=${tierFilter}` : ""}${runParam}`,
          15000,
        ),
        fetchApi<{ events: BetaEvent[] }>(`/api/beta-research/events?limit=180${runParam}`),
      ]);

      setStatus(statusRes);
      setRuns(allRuns);
      setStages(asArray(stageRes?.stages));
      setLeads(asArray(leadsRes?.leads));
      setEvents(asArray(eventsRes?.events));
      setSprintStatus(sprintRes || null);
      setError("");
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [selectedRunId, tierFilter]);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 9000);
    return () => clearInterval(iv);
  }, [refresh]);

  const runSafeCycle = async () => {
    setRunningNow(true);
    try {
      await postApi(
        "/api/beta-research/run-cycle",
        {
          max_categories: 2,
          max_locations_per_category: 2,
          max_per_signal: 8,
          parallel_signals: 3,
          dry_run: false,
        },
        120000,
      );
      setSelectedRunId(0);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setRunningNow(false);
    }
  };

  const sendFeedback = async (leadId: number, verdict: "good" | "bad" | "needs_review") => {
    setFeedbackBusy(leadId);
    try {
      await postApi("/api/beta-research/feedback", { lead_id: leadId, verdict });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setFeedbackBusy(null);
    }
  };

  const planSprint = async () => {
    setSprintBusy("plan");
    try {
      const plan = await postApi<RunpodSprintPlan>(
        "/api/runpod-sprint/plan",
        {
          runpod: {
            enabled: true,
            max_credits_usd: 17,
            phase_caps: [5, 10, 15, 17],
            safety_mode: "no_platform_logins",
            launch_mode: "research_creative_only",
            include_media_images: false,
            strict_brand_relevance: true,
            cold_email_top_n: 250,
            cold_email_enrich_max: 120,
            cold_email_min_score: 58,
          },
        },
        30000,
      );
      setSprintPlan(plan);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSprintBusy("");
    }
  };

  const startSprint = async () => {
    setSprintBusy("start");
    try {
      await postApi(
        "/api/runpod-sprint/start",
        {
          runpod: {
            enabled: true,
            max_credits_usd: 17,
            phase_caps: [5, 10, 15, 17],
            safety_mode: "no_platform_logins",
            launch_mode: "research_creative_only",
            parallel_signals: 3,
            parallel_max: 6,
            include_media_images: false,
            strict_brand_relevance: true,
            cold_email_top_n: 250,
            cold_email_enrich_max: 120,
            cold_email_min_score: 58,
            target_leads_min: 300,
            target_leads_max: 600,
            target_statics_min: 120,
            target_statics_max: 250,
            target_videos_min: 20,
            target_videos_max: 40,
          },
        },
        120000,
      );
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSprintBusy("");
    }
  };

  const stopSprint = async () => {
    setSprintBusy("stop");
    try {
      await postApi("/api/runpod-sprint/stop", { run_id: sprintStatus?.run?.id || 0 }, 30000);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSprintBusy("");
    }
  };

  const promoteSprint = async () => {
    setSprintBusy("promote");
    try {
      await postApi("/api/runpod-sprint/promote", { run_id: sprintStatus?.run?.id || 0 }, 30000);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSprintBusy("");
    }
  };

  const selectedRun = useMemo(() => {
    const targetId = selectedRunId || Number(status?.last_run?.id || 0);
    return runs.find((r) => r.id === targetId) || status?.last_run || null;
  }, [runs, selectedRunId, status?.last_run]);

  const stageMap = useMemo(() => {
    const map = new Map<string, BetaStageMetric>();
    for (const s of stages) map.set(s.stage_name, s);
    return map;
  }, [stages]);

  const runSummary = useMemo(() => parseSummary(selectedRun), [selectedRun]);

  const diagnosticsText = useMemo(() => {
    if (status?.latest_diagnostics?.summary) return status.latest_diagnostics.summary;
    for (const stageName of STAGES) {
      if (stageName === "feedback" || stageName === "reinforcement") continue;
      const s = stageMap.get(stageName);
      if (s && s.input_count > 0 && s.output_count === 0) {
        return `Drop-off at ${stageLabel(stageName)} (${s.input_count} -> 0).`;
      }
    }
    if ((runSummary?.stored || 0) > 0) {
      return `Run stored ${runSummary?.stored || 0} leads in beta DB.`;
    }
    return "No major errors surfaced in stage metrics.";
  }, [runSummary?.stored, stageMap, status?.latest_diagnostics?.summary]);

  const keyWarnings = useMemo(() => {
    const configured = new Map<string, boolean>();
    for (const p of status?.providers || []) configured.set(p.key_name, p.configured);
    const warnings: string[] = [];
    if (!configured.get("google_places_api_key")) warnings.push("Google Places key missing for high-quality Maps collection.");
    if (!configured.get("yelp_api_key")) warnings.push("Yelp key missing; Yelp collection uses less reliable scrape fallback.");
    return warnings;
  }, [status?.providers]);

  const canRun = !runningNow && !status?.running;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-muted">Loading Beta Research...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold">Beta Research</h2>
          <p className="text-sm text-muted mt-1">
            Isolated agentic research loop: Public Signals → Collection → Extraction → Scoring → Validation → Decision → Launch → Feedback → Reinforcement
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="px-3 py-2 rounded-lg border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover transition-colors"
          >
            Refresh
          </button>
          <button
            onClick={runSafeCycle}
            disabled={!canRun}
            className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {runningNow || status?.running ? "Running..." : "Run Safe Cycle"}
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-accent/40 bg-accent/5 p-4">
        <p className="text-sm font-medium text-accent mb-1">Safety Lock Active (No Outbound)</p>
        <p className="text-xs text-muted">
          This beta mode does not send emails/SMS/calls, does not log in to social platforms, and writes only to a separate database.
        </p>
      </div>

      <div className="rounded-xl border border-border bg-card p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">Runpod Sprint Orchestrator</h3>
            <p className="text-xs text-muted mt-1">
              Hard-capped phased run: leads + creatives only (no platform logins, no outbound outreach, no Meta posting/writes).
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={planSprint}
              disabled={sprintBusy !== ""}
              className="px-3 py-2 rounded-lg border border-border text-xs text-muted hover:text-foreground disabled:opacity-50"
            >
              {sprintBusy === "plan" ? "Planning..." : "Plan"}
            </button>
            <button
              onClick={startSprint}
              disabled={sprintBusy !== "" || !!sprintStatus?.running}
              className="px-3 py-2 rounded-lg bg-accent text-white text-xs font-medium hover:bg-accent/90 disabled:opacity-50"
            >
              {sprintBusy === "start" ? "Starting..." : sprintStatus?.running ? "Running..." : "Start Sprint"}
            </button>
            <button
              onClick={stopSprint}
              disabled={sprintBusy !== "" || !sprintStatus?.running}
              className="px-3 py-2 rounded-lg border border-red-500/40 text-red-300 text-xs hover:bg-red-500/10 disabled:opacity-50"
            >
              {sprintBusy === "stop" ? "Stopping..." : "Stop"}
            </button>
            <button
              onClick={promoteSprint}
              disabled={sprintBusy !== "" || !sprintStatus?.run?.id}
              className="px-3 py-2 rounded-lg border border-emerald-500/40 text-emerald-300 text-xs hover:bg-emerald-500/10 disabled:opacity-50"
            >
              {sprintBusy === "promote" ? "Promoting..." : "Promote"}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
          <div className="rounded-lg border border-border/70 bg-background/40 px-3 py-3">
            <p className="text-xs text-muted">Spend Meter</p>
            <p className="text-sm font-semibold mt-1">
              ${Number(sprintStatus?.spend_meter?.current_usd || 0).toFixed(2)} / ${Number(sprintStatus?.spend_meter?.max_usd || 17).toFixed(2)}
            </p>
            <div className="w-full h-2 bg-background rounded mt-2 overflow-hidden">
              <div
                className="h-2 bg-accent"
                style={{ width: `${Math.max(0, Math.min(100, Number(sprintStatus?.spend_meter?.percent || 0)))}%` }}
              />
            </div>
            <p className="text-[11px] text-muted mt-1">
              Status: {sprintStatus?.running ? "running" : sprintStatus?.run?.status || "idle"}
              {sprintStatus?.stop_requested ? " · stop requested" : ""}
            </p>
          </div>

          <div className="rounded-lg border border-border/70 bg-background/40 px-3 py-3">
            <p className="text-xs text-muted">Worker Config</p>
            <p className="text-sm font-semibold mt-1">
              {Number(sprintStatus?.runtime?.parallel_signals || 0)} to {Number(sprintStatus?.runtime?.parallel_max || 0)} collectors
            </p>
            <p className="text-[11px] text-muted mt-1">
              Safety: {String(sprintStatus?.runtime?.safety_mode || "no_platform_logins")}
            </p>
            <p className="text-[11px] text-muted">
              Launch mode: {String(sprintStatus?.runtime?.launch_mode || "research_creative_only")}
            </p>
          </div>

          <div className="rounded-lg border border-border/70 bg-background/40 px-3 py-3">
            <p className="text-xs text-muted">Output Counters</p>
            <div className="text-[11px] text-muted mt-1 space-y-1">
              <p>Leads: {Number(sprintStatus?.outputs?.leads_validated || 0)}</p>
              <p>Cold Email Priority: {Number(sprintStatus?.outputs?.cold_email_priority_count || 0)} (enriched {Number(sprintStatus?.outputs?.cold_email_enriched_count || 0)})</p>
              <p>Needs Email Enrichment: {Number(sprintStatus?.outputs?.cold_email_needs_enrichment_count || 0)}</p>
              <p>Statics: {Number(sprintStatus?.outputs?.statics_usable || 0)}</p>
              <p>Videos: {Number(sprintStatus?.outputs?.videos_usable || 0)}</p>
              <p>Campaigns: {Number(sprintStatus?.outputs?.campaigns_created || 0)} · Ad sets: {Number(sprintStatus?.outputs?.adsets_created || 0)} · Ads: {Number(sprintStatus?.outputs?.ads_created || 0)}</p>
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-border/70 bg-background/40 px-3 py-3">
          <p className="text-xs font-medium mb-2">Phase Checkpoints</p>
          <div className="flex flex-wrap gap-2">
            {(sprintStatus?.phase_checkpoints || []).map((cp) => (
              <div
                key={cp.index}
                className={`px-2 py-1 rounded text-[11px] border ${
                  cp.passed
                    ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
                    : cp.active
                      ? "border-amber-500/40 text-amber-300 bg-amber-500/10"
                      : "border-border text-muted"
                }`}
              >
                ${cp.cap_usd.toFixed(0)} {cp.passed ? "passed" : cp.active ? "active" : "pending"}
              </div>
            ))}
          </div>
          {sprintPlan?.estimate && (
            <p className="text-[11px] text-muted mt-2">
              Planned output: leads {sprintPlan.estimate.lead_output_validated || 0}, cold-email priority {sprintPlan.estimate.cold_email_priority_leads || 0}, statics {sprintPlan.estimate.creative_statics_usable || 0}, videos{" "}
              {sprintPlan.estimate.creative_videos_usable || 0}.
            </p>
          )}
          {!!(sprintStatus?.preflight?.missing_dependencies || []).length && (
            <p className="text-[11px] text-amber-300 mt-2">
              Missing deps: {(sprintStatus?.preflight?.missing_dependencies || []).join(", ")}
            </p>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card px-4 py-3">
          <p className="text-xs text-muted">Agent Status</p>
          <p className={`text-lg font-semibold mt-1 ${status?.running ? "text-amber-400" : "text-emerald-400"}`}>
            {status?.running ? "Running" : "Idle"}
          </p>
        </div>
        <div className="rounded-lg border border-border bg-card px-4 py-3">
          <p className="text-xs text-muted">Total Runs</p>
          <p className="text-lg font-semibold mt-1">{status?.total_runs || 0}</p>
        </div>
        <div className="rounded-lg border border-border bg-card px-4 py-3">
          <p className="text-xs text-muted">Beta Leads</p>
          <p className="text-lg font-semibold mt-1">{status?.total_beta_leads || 0}</p>
        </div>
        <div className="rounded-lg border border-border bg-card px-4 py-3">
          <p className="text-xs text-muted">Queued Review</p>
          <p className="text-lg font-semibold mt-1 text-accent">{status?.queued_review || 0}</p>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h3 className="text-sm font-semibold">Run Diagnostics</h3>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSelectedRunId(0)}
              className={`px-2 py-1 rounded text-xs border ${
                selectedRunId === 0
                  ? "border-accent text-accent bg-accent/10"
                  : "border-border text-muted hover:text-foreground"
              }`}
            >
              Follow Latest
            </button>
            {selectedRun && (
              <span className="text-xs text-muted">
                Run #{selectedRun.id} · {selectedRun.duration_ms ? `${(selectedRun.duration_ms / 1000).toFixed(1)}s` : "—"}
              </span>
            )}
          </div>
        </div>
        <p className="text-sm">{diagnosticsText}</p>
        <div className="mt-2 text-xs text-muted space-y-1">
          <p>Results database: {status?.safety?.separate_beta_db || "—"}</p>
          {runSummary && (
            <p>
              Latest summary: signals {runSummary.signals || 0}, collected {runSummary.collected || 0}, validated {runSummary.validated || 0}, stored{" "}
              {runSummary.stored || 0}
            </p>
          )}
          {keyWarnings.map((w) => (
            <p key={w} className="text-amber-300">
              {w}
            </p>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold">Pipeline Funnel (Selected Run)</h3>
            <span className="text-xs text-muted">
              {selectedRun?.started_at ? `Started ${timeAgo(selectedRun.started_at)}` : "No runs yet"}
            </span>
          </div>
          <div className="space-y-2">
            {STAGES.map((stageId) => {
              const s = stageMap.get(stageId);
              return (
                <div key={stageId} className="rounded-lg border border-border/70 bg-background/40 px-3 py-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium">{stageLabel(stageId)}</span>
                    <span className={s?.status === "ok" ? "text-emerald-400" : "text-amber-400"}>
                      {s?.status || "pending"}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-muted mt-1">
                    <span>{s ? `${s.input_count} -> ${s.output_count}` : "—"}</span>
                    <span>{s ? `${s.duration_ms}ms` : ""}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-4">
          <h3 className="text-sm font-semibold mb-3">Provider / Key Visibility</h3>
          <div className="space-y-2">
            {(status?.providers || []).map((p) => (
              <div key={p.key_name} className="rounded-lg border border-border/70 px-3 py-2 flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium">{p.key_name}</p>
                  <p className="text-[11px] text-muted">{p.used_for_beta ? "Used by beta collection path" : "Optional for beta loop"}</p>
                </div>
                <span className={`text-xs px-2 py-1 rounded ${p.configured ? "bg-emerald-500/10 text-emerald-300" : "bg-zinc-500/10 text-zinc-300"}`}>
                  {p.configured ? "configured" : "not set"}
                </span>
              </div>
            ))}
          </div>
          <div className="mt-3 text-[11px] text-muted space-y-1">
            <p>Mode: {status?.safety?.mode || "research_only"}</p>
            <p>Launch behavior: {status?.safety?.launch_behavior || "queue_only_no_send"}</p>
            <p>Separate DB: {status?.safety?.separate_beta_db || "—"}</p>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">Latest Beta Leads</h3>
          <div className="flex items-center gap-2">
            {(["", "A", "B", "C"] as const).map((t) => (
              <button
                key={t || "all"}
                onClick={() => setTierFilter(t)}
                className={`px-2 py-1 rounded text-xs border transition-colors ${
                  tierFilter === t
                    ? "border-accent text-accent bg-accent/10"
                    : "border-border text-muted hover:text-foreground"
                }`}
              >
                {t || "All"}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted border-b border-border">
              <tr>
                <th className="text-left py-2 pr-2">Lead</th>
                <th className="text-left py-2 pr-2">Score</th>
                <th className="text-left py-2 pr-2">Tier</th>
                <th className="text-left py-2 pr-2">Angle</th>
                <th className="text-left py-2 pr-2">Contact</th>
                <th className="text-left py-2 pr-2">Source</th>
                <th className="text-left py-2">Feedback</th>
              </tr>
            </thead>
            <tbody>
              {leads.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-muted text-sm">
                    No beta leads for this run yet.
                  </td>
                </tr>
              )}
              {leads.map((lead) => (
                <tr key={lead.id} className="border-b border-border/40">
                  <td className="py-2 pr-2">
                    <div>
                      <p className="font-medium">{lead.venue_name || "Unknown"}</p>
                      <p className="text-xs text-muted">
                        {lead.city || "—"} · {lead.category || "—"}
                      </p>
                      {lead.website && (
                        <a
                          href={lead.website.startsWith("http") ? lead.website : `https://${lead.website}`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-accent hover:underline"
                        >
                          {lead.website}
                        </a>
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-2 font-semibold">{Math.round(lead.score || 0)}</td>
                  <td className="py-2 pr-2">
                    <span
                      className={`text-xs px-2 py-1 rounded ${
                        lead.tier === "A"
                          ? "bg-emerald-500/10 text-emerald-300"
                          : lead.tier === "B"
                            ? "bg-blue-500/10 text-blue-300"
                            : "bg-amber-500/10 text-amber-300"
                      }`}
                    >
                      {lead.tier}
                    </span>
                  </td>
                  <td className="py-2 pr-2 text-xs">{lead.best_pitch_angle || "referral_partner"}</td>
                  <td className="py-2 pr-2 text-xs">{lead.contact_phone || lead.contact_email || "No direct contact"}</td>
                  <td className="py-2 pr-2 text-xs text-muted">{lead.signal_source || "—"}</td>
                  <td className="py-2">
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => sendFeedback(lead.id, "good")}
                        disabled={feedbackBusy === lead.id}
                        className="px-2 py-1 rounded text-[11px] bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
                      >
                        Good
                      </button>
                      <button
                        onClick={() => sendFeedback(lead.id, "bad")}
                        disabled={feedbackBusy === lead.id}
                        className="px-2 py-1 rounded text-[11px] bg-red-500/10 text-red-300 hover:bg-red-500/20 disabled:opacity-50"
                      >
                        Bad
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">Live Run Trace</h3>
          <span className="text-xs text-muted">{events.length} events</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted border-b border-border">
              <tr>
                <th className="text-left py-2 pr-2">Time</th>
                <th className="text-left py-2 pr-2">Stage</th>
                <th className="text-left py-2 pr-2">Level</th>
                <th className="text-left py-2 pr-2">Message</th>
                <th className="text-left py-2">Details</th>
              </tr>
            </thead>
            <tbody>
              {events.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-muted">
                    No trace events yet for this run.
                  </td>
                </tr>
              )}
              {events.map((event) => (
                <tr key={event.id} className="border-b border-border/40">
                  <td className="py-2 pr-2 text-muted">{timeAgo(event.created_at)}</td>
                  <td className="py-2 pr-2">{stageLabel(event.stage_name || "pipeline")}</td>
                  <td className="py-2 pr-2">
                    <span
                      className={`px-1.5 py-0.5 rounded ${
                        event.level === "error"
                          ? "bg-red-500/10 text-red-300"
                          : event.level === "warn"
                            ? "bg-amber-500/10 text-amber-300"
                            : "bg-emerald-500/10 text-emerald-300"
                      }`}
                    >
                      {event.level}
                    </span>
                  </td>
                  <td className="py-2 pr-2">{event.message}</td>
                  <td className="py-2 text-muted font-mono break-all">{compactPayload(event.payload || {})}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="text-sm font-semibold mb-3">Recent Runs</h3>
        <div className="space-y-2">
          {runs.length === 0 && <p className="text-sm text-muted">No runs yet.</p>}
          {runs.map((run) => {
            const isSelected = (selectedRunId > 0 ? selectedRunId : status?.last_run?.id) === run.id;
            return (
              <button
                key={run.id}
                onClick={() => setSelectedRunId(run.id)}
                className={`w-full rounded-lg border px-3 py-2 flex items-center justify-between text-left ${
                  isSelected ? "border-accent/70 bg-accent/5" : "border-border/70"
                }`}
              >
                <div>
                  <p className="text-sm font-medium">Run #{run.id}</p>
                  <p className="text-xs text-muted">
                    {run.started_at} {run.duration_ms ? `· ${(run.duration_ms / 1000).toFixed(1)}s` : ""}
                  </p>
                </div>
                <span
                  className={`text-xs px-2 py-1 rounded ${
                    run.status === "completed"
                      ? "bg-emerald-500/10 text-emerald-300"
                      : run.status === "failed"
                        ? "bg-red-500/10 text-red-300"
                        : "bg-amber-500/10 text-amber-300"
                  }`}
                >
                  {run.status}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
