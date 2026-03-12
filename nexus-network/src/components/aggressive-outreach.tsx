"use client";

import { useEffect, useState } from "react";
import { fetchApi, postApi } from "@/lib/api";

interface OutreachSummary {
  total_targets: number;
  email_ready: number;
  call_ready: number;
  sms_allowed: number;
  queued_email: number;
  last_run?: string;
  by_status?: Record<string, number>;
}

interface OutreachSource {
  source_url?: string;
  page_title?: string;
  evidence_snippet?: string;
}

interface OutreachActivity {
  agent_name?: string;
  event_type?: string;
  detail?: string;
  created_at?: string;
}

interface OutreachTarget {
  id: number;
  business_name: string;
  category: string;
  city: string;
  website?: string;
  official_email?: string;
  official_phone?: string;
  contact_name?: string;
  contact_role?: string;
  contact_confidence?: number;
  evidence_snippet?: string;
  message_subject?: string;
  message_body?: string;
  call_script?: string;
  sms_reason?: string;
  sms_allowed?: number;
  email_ready?: number;
  call_ready?: number;
  status: string;
  updated_at?: string;
  sales_brief?: string;
  offer_strategy?: string;
  qualifying_questions?: string[];
  fit_signals?: string[];
  research_stack?: string[];
  site_quality_flags?: string[];
  discovered_emails?: string[];
  discovered_phones?: string[];
  sources?: OutreachSource[];
  activity?: OutreachActivity[];
}

const STATUS_OPTIONS = ["all", "researched", "queued_email", "needs_review"] as const;
const TARGET_LIMITS = [25, 50, 100] as const;

function fmtDate(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function statusTone(status: string): string {
  switch (status) {
    case "queued_email":
      return "text-cyan-400 bg-cyan-500/10";
    case "needs_review":
      return "text-yellow-400 bg-yellow-500/10";
    default:
      return "text-blue-400 bg-blue-500/10";
  }
}

export function AggressiveOutreach() {
  const [summary, setSummary] = useState<OutreachSummary | null>(null);
  const [targets, setTargets] = useState<OutreachTarget[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_OPTIONS)[number]>("all");
  const [limit, setLimit] = useState<(typeof TARGET_LIMITS)[number]>(50);
  const [workers, setWorkers] = useState(5);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const selected = targets.find((target) => target.id === selectedId) || targets[0] || null;

  async function loadData() {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      qs.set("limit", String(limit));
      if (statusFilter !== "all") {
        qs.set("status", statusFilter);
      }
      const [summaryResp, targetsResp] = await Promise.all([
        fetchApi<OutreachSummary>("/api/aggressive-outreach/summary"),
        fetchApi<{ targets?: OutreachTarget[] }>(`/api/aggressive-outreach/targets?${qs.toString()}`),
      ]);
      const nextTargets = targetsResp.targets || [];
      setSummary(summaryResp);
      setTargets(nextTargets);
      setSelectedId((current) => {
        if (current && nextTargets.some((target) => target.id === current)) return current;
        return nextTargets[0]?.id ?? null;
      });
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load outreach workspace");
    }
    setLoading(false);
  }

  useEffect(() => {
    loadData();
  }, [limit, statusFilter]);

  useEffect(() => {
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, [limit, statusFilter]);

  useEffect(() => {
    if (!toast) return;
    const timeout = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(timeout);
  }, [toast]);

  async function runResearch() {
    setRunning(true);
    try {
      await postApi("/api/aggressive-outreach/run", {
        limit,
        concurrency: Math.max(1, workers),
        force_refresh: false,
      });
      setToast("Research run completed");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Research run failed");
    }
    setRunning(false);
  }

  async function queueEmail(targetId: number) {
    setSaving(true);
    try {
      await postApi(`/api/aggressive-outreach/targets/${targetId}/queue-email`, {});
      setToast("Queued email draft");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue email");
    }
    setSaving(false);
  }

  async function updateStatus(targetId: number, nextStatus: string) {
    setSaving(true);
    try {
      await postApi(`/api/aggressive-outreach/targets/${targetId}/status`, { status: nextStatus });
      setToast(`Marked ${nextStatus.replace("_", " ")}`);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update target status");
    }
    setSaving(false);
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 pt-5 pb-3 shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold tracking-tight">Aggressive Outreach</h2>
            <p className="text-sm text-muted mt-1">
              Native sales-research workspace with curated drafts, call-first targeting, and cold SMS blocked until consent.
            </p>
          </div>
          <div className="text-xs text-muted pt-1">Last run: {fmtDate(summary?.last_run)}</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 pb-6 space-y-4">
        {error && (
          <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/25 rounded-md px-3 py-2">{error}</div>
        )}
        {toast && (
          <div className="text-xs text-emerald-300 bg-emerald-500/10 border border-emerald-500/25 rounded-md px-3 py-2">{toast}</div>
        )}

        <div className="grid grid-cols-5 gap-3">
          <MetricCard label="Targets Researched" value={String(summary?.total_targets || 0)} />
          <MetricCard label="Email Ready" value={String(summary?.email_ready || 0)} />
          <MetricCard label="Call Ready" value={String(summary?.call_ready || 0)} />
          <MetricCard label="SMS Allowed" value={String(summary?.sms_allowed || 0)} />
          <MetricCard label="Queued Emails" value={String(summary?.queued_email || 0)} />
        </div>

        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex flex-wrap items-end gap-3">
            <LabeledSelect
              label="Targets"
              value={String(limit)}
              onChange={(value) => setLimit(Number(value) as (typeof TARGET_LIMITS)[number])}
              options={TARGET_LIMITS.map((item) => ({ value: String(item), label: String(item) }))}
            />
            <LabeledInput
              label="Workers"
              value={String(workers)}
              min={1}
              max={12}
              onChange={(value) => setWorkers(Math.max(1, Math.min(12, Number(value) || 1)))}
            />
            <LabeledSelect
              label="Status"
              value={statusFilter}
              onChange={(value) => setStatusFilter(value as (typeof STATUS_OPTIONS)[number])}
              options={STATUS_OPTIONS.map((item) => ({ value: item, label: item === "all" ? "All" : item.replace("_", " ") }))}
            />
            <button
              onClick={runResearch}
              disabled={running}
              className="px-4 py-2 text-sm rounded-md bg-accent text-background font-medium hover:opacity-90 disabled:opacity-50"
            >
              {running ? "Running..." : "Run 5-Agent Research"}
            </button>
            <button
              onClick={loadData}
              disabled={loading}
              className="px-4 py-2 text-sm rounded-md border border-border text-muted hover:text-foreground hover:bg-card-hover disabled:opacity-50"
            >
              Refresh
            </button>
            <div className="text-xs text-muted ml-auto">
              {summary?.by_status ? Object.entries(summary.by_status).map(([key, value]) => `${key}: ${value}`).join(" · ") : "—"}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="text-center text-muted py-16">Loading outreach workspace...</div>
        ) : (
          <div className="grid grid-cols-[1.4fr_1fr] gap-4 min-h-[680px]">
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                <h3 className="text-sm font-medium">Targets</h3>
                <span className="text-xs text-muted">{targets.length} loaded</span>
              </div>
              <div className="max-h-[780px] overflow-y-auto">
                {targets.length === 0 ? (
                  <div className="text-sm text-muted px-4 py-12">No targets match the current filter.</div>
                ) : (
                  targets.map((target) => {
                    const selectedRow = selected?.id === target.id;
                    return (
                      <button
                        key={target.id}
                        onClick={() => setSelectedId(target.id)}
                        className={`w-full text-left px-4 py-4 border-b border-border/60 transition-colors ${
                          selectedRow ? "bg-card-hover" : "hover:bg-card-hover/60"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium">{target.business_name}</div>
                            <div className="text-xs text-muted mt-1">
                              {target.category.replaceAll("_", " ")} · {target.city || "Unknown city"}
                            </div>
                            <div className="text-xs text-muted mt-2">
                              {target.contact_name ? `Contact: ${target.contact_name}` : "Contact: safe fallback"}
                              {typeof target.contact_confidence === "number" ? ` · ${target.contact_confidence}% confidence` : ""}
                            </div>
                          </div>
                          <div className="text-right shrink-0">
                            <div className={`inline-flex px-2 py-1 rounded-full text-[11px] ${statusTone(target.status)}`}>
                              {target.status.replace("_", " ")}
                            </div>
                            <div className="text-[11px] text-muted mt-2">{fmtDate(target.updated_at)}</div>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2 mt-3">
                          {target.official_email && (
                            <span className="text-[11px] px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-300">
                              Email
                            </span>
                          )}
                          {target.official_phone && (
                            <span className="text-[11px] px-2 py-1 rounded-full bg-blue-500/10 text-blue-300">
                              Call
                            </span>
                          )}
                          {!target.sms_allowed && (
                            <span className="text-[11px] px-2 py-1 rounded-full bg-red-500/10 text-red-300">
                              SMS blocked
                            </span>
                          )}
                        </div>
                      </button>
                    );
                  })
                )}
              </div>
            </div>

            <div className="bg-card border border-border rounded-lg overflow-hidden">
              <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                <div>
                  <div className="text-[11px] uppercase tracking-[0.2em] text-muted">Selected Target</div>
                  <h3 className="text-lg font-semibold mt-1">{selected?.business_name || "No target selected"}</h3>
                </div>
                {selected && (
                  <div className="flex items-center gap-2">
                    <select
                      value={selected.status}
                      onChange={(event) => updateStatus(selected.id, event.target.value)}
                      disabled={saving}
                      className="bg-background border border-border rounded-md px-3 py-2 text-sm"
                    >
                      <option value="researched">Researched</option>
                      <option value="queued_email">Queued Email</option>
                      <option value="needs_review">Needs Review</option>
                    </select>
                    <button
                      onClick={() => queueEmail(selected.id)}
                      disabled={saving || !selected.email_ready}
                      className="px-4 py-2 text-sm rounded-md bg-accent text-background font-medium hover:opacity-90 disabled:opacity-50"
                    >
                      Queue Email
                    </button>
                  </div>
                )}
              </div>

              {selected ? (
                <div className="p-4 space-y-4 max-h-[780px] overflow-y-auto">
                  <div className="grid grid-cols-2 gap-3">
                    <InfoCard label="Official Email" value={selected.official_email || "Not ready"} />
                    <InfoCard label="Official Phone" value={selected.official_phone || "Not ready"} />
                    <InfoCard label="Website" value={selected.website || "—"} />
                    <InfoCard label="SMS Policy" value={selected.sms_reason || "Cold SMS blocked until consent."} />
                  </div>

                  <TagRow title="Fit Signals" items={selected.fit_signals} />
                  <TagRow title="Quality Flags" items={selected.site_quality_flags} emptyLabel="No quality flags" />
                  <TagRow title="Research Stack" items={selected.research_stack} />

                  <TextCard title="Evidence" body={selected.evidence_snippet || "No evidence captured yet."} />
                  <TextCard title="Sales Brief" body={selected.sales_brief || "No sales brief generated."} />
                  <TextCard title="Offer Strategy" body={selected.offer_strategy || "No offer strategy generated."} />
                  <ListCard title="Qualifying Questions" items={selected.qualifying_questions} emptyLabel="No qualifying questions generated." />
                  <TextCard title="Curated Email Subject" body={selected.message_subject || "No subject generated."} mono />
                  <TextCard title="Curated Email Body" body={selected.message_body || "No email body generated."} mono />
                  <TextCard title="Call Script" body={selected.call_script || "No call script generated."} mono />
                  <TagRow title="Discovered Emails" items={selected.discovered_emails} emptyLabel="None" />
                  <TagRow title="Discovered Phones" items={selected.discovered_phones} emptyLabel="None" />

                  <div className="grid grid-cols-2 gap-3">
                    <ListCard
                      title="Source Pages"
                      items={(selected.sources || []).map((source) => source.source_url || source.page_title || "Untitled source")}
                      emptyLabel="No source pages logged."
                    />
                    <ListCard
                      title="Recent Activity"
                      items={(selected.activity || []).map((item) => {
                        const when = item.created_at ? `${fmtDate(item.created_at)} · ` : "";
                        return `${when}${item.agent_name || "agent"}: ${item.detail || item.event_type || "updated"}`;
                      })}
                      emptyLabel="No activity logged."
                    />
                  </div>
                </div>
              ) : (
                <div className="text-sm text-muted px-4 py-12">Select a target to inspect the research and queue the curated draft.</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-3xl font-semibold mt-2">{value}</div>
    </div>
  );
}

function LabeledInput({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: string;
  min: number;
  max: number;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-muted">
      <div className="mb-1">{label}</div>
      <input
        value={value}
        min={min}
        max={max}
        type="number"
        onChange={(event) => onChange(event.target.value)}
        className="w-24 bg-background border border-border rounded-md px-3 py-2 text-sm text-foreground"
      />
    </label>
  );
}

function LabeledSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  return (
    <label className="text-xs text-muted">
      <div className="mb-1">{label}</div>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="bg-background border border-border rounded-md px-3 py-2 text-sm text-foreground"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted">{label}</div>
      <div className="text-sm mt-2 break-all">{value}</div>
    </div>
  );
}

function TextCard({ title, body, mono = false }: { title: string; body: string; mono?: boolean }) {
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted mb-2">{title}</div>
      <pre className={`whitespace-pre-wrap text-sm ${mono ? "font-mono" : "font-sans"}`}>{body}</pre>
    </div>
  );
}

function ListCard({ title, items, emptyLabel }: { title: string; items?: string[]; emptyLabel: string }) {
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted mb-2">{title}</div>
      {items && items.length > 0 ? (
        <div className="space-y-2">
          {items.map((item, index) => (
            <div key={`${title}-${index}`} className="text-sm text-foreground">
              {item}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-sm text-muted">{emptyLabel}</div>
      )}
    </div>
  );
}

function TagRow({ title, items, emptyLabel = "None" }: { title: string; items?: string[]; emptyLabel?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.15em] text-muted mb-2">{title}</div>
      <div className="flex flex-wrap gap-2">
        {items && items.length > 0 ? (
          items.map((item) => (
            <span key={`${title}-${item}`} className="px-2 py-1 rounded-full bg-card-hover text-xs text-foreground">
              {item}
            </span>
          ))
        ) : (
          <span className="text-sm text-muted">{emptyLabel}</span>
        )}
      </div>
    </div>
  );
}
