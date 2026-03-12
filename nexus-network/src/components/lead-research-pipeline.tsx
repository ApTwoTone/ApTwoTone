"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchApi, postApi } from "@/lib/api";

/* ── Types ──────────────────────────────────────────────────────────────── */

interface DossierStats {
  total_dossiers: number;
  high_fit_75plus: number;
  pending_outreach: number;
  approved: number;
  sent: number;
  avg_confidence: number;
  research_queue: number;
}

interface LeadStats {
  total: number;
  upcoming?: number;
  by_type?: Record<string, number>;
  by_status?: Record<string, number>;
}

interface HunterStats {
  groups_configured: number;
  groups_scraped_today: number;
  is_running: boolean;
  session_type: string;
  event_leads_today: number;
  referral_leads_today: number;
  last_run: string;
}

interface Dossier {
  id: number;
  lead_id: number;
  lead_type: string;
  source_table: string;
  contact_name: string;
  business_name: string;
  phone: string;
  email: string;
  website: string;
  instagram: string;
  location: string;
  in_service_area: number;
  services: string[] | string;
  event_types: string[] | string;
  service_area: string;
  price_range: string;
  partnership_type: string;
  fit_score: number;
  fit_reasoning: string;
  approach: string;
  objection_prep: string;
  email_subject: string;
  email_body: string;
  call_script: string;
  full_dossier: string | Record<string, unknown>;
  research_confidence: number;
  model_used: string;
  outreach_status: string;
  created_at: string;
  updated_at: string;
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function fmtDate(v?: string): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fitColor(score: number): string {
  if (score >= 75) return "text-emerald-400";
  if (score >= 50) return "text-amber-400";
  return "text-zinc-400";
}

function fitBg(score: number): string {
  if (score >= 75) return "bg-emerald-500";
  if (score >= 50) return "bg-amber-500";
  return "bg-zinc-500";
}

function statusBadge(status: string): { label: string; cls: string } {
  switch (status) {
    case "approved": return { label: "Approved", cls: "text-cyan-400 bg-cyan-500/10" };
    case "sent": return { label: "Sent", cls: "text-emerald-400 bg-emerald-500/10" };
    case "sent_to_kai": return { label: "Sent to Kai", cls: "text-blue-400 bg-blue-500/10" };
    case "replied": return { label: "Replied", cls: "text-green-400 bg-green-500/10" };
    default: return { label: "Pending", cls: "text-zinc-400 bg-zinc-500/10" };
  }
}

function partnerBadge(type: string): { label: string; cls: string } {
  switch (type) {
    case "referral_partner": return { label: "Referral Partner", cls: "text-purple-400 bg-purple-500/10" };
    case "secondary_vendor": return { label: "Secondary Vendor", cls: "text-blue-400 bg-blue-500/10" };
    case "direct_pitch": return { label: "Direct Pitch", cls: "text-amber-400 bg-amber-500/10" };
    default: return { label: type || "Unknown", cls: "text-zinc-400 bg-zinc-500/10" };
  }
}

function parseJsonArray(v: string[] | string | undefined): string[] {
  if (!v) return [];
  if (Array.isArray(v)) return v;
  try { return JSON.parse(v); } catch { return []; }
}

/* ── Pipeline Stage Card ─────────────────────────────────────────────────── */

function StageCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color: string }) {
  return (
    <div className={`flex flex-col items-center px-5 py-3 rounded-xl bg-card border border-border min-w-[120px] border-t-2 ${color}`}>
      <span className="text-2xl font-bold">{value}</span>
      <span className="text-xs text-muted mt-1">{label}</span>
      {sub && <span className="text-[10px] text-muted mt-0.5">{sub}</span>}
    </div>
  );
}

function Arrow() {
  return <span className="text-muted text-lg mx-1 select-none shrink-0">&rarr;</span>;
}

/* ── Main Component ──────────────────────────────────────────────────────── */

export function LeadResearchPipeline() {
  const [dossierStats, setDossierStats] = useState<DossierStats | null>(null);
  const [eventStats, setEventStats] = useState<LeadStats | null>(null);
  const [referralStats, setReferralStats] = useState<LeadStats | null>(null);
  const [hunterStats, setHunterStats] = useState<HunterStats | null>(null);
  const [dossiers, setDossiers] = useState<Dossier[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [toast, setToast] = useState("");

  // Filters
  const [search, setSearch] = useState("");
  const [minFit, setMinFit] = useState(0);
  const [partnerFilter, setPartnerFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortField, setSortField] = useState<"fit_score" | "research_confidence" | "created_at">("fit_score");
  const [sortAsc, setSortAsc] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [ds, es, rs, hs, dl] = await Promise.all([
        fetchApi<DossierStats>("/api/dossiers/stats").catch(() => null),
        fetchApi<LeadStats>("/api/event-leads/stats").catch(() => null),
        fetchApi<LeadStats>("/api/referral-leads/stats").catch(() => null),
        fetchApi<HunterStats>("/api/hunter/stats").catch(() => null),
        fetchApi<Dossier[]>("/api/dossiers").catch(() => []),
      ]);
      setDossierStats(ds);
      setEventStats(es);
      setReferralStats(rs);
      setHunterStats(hs);
      setDossiers(Array.isArray(dl) ? dl : []);
    } catch { /* handled per-call */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 30000);
    return () => clearInterval(iv);
  }, [loadData]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(t);
  }, [toast]);

  /* ── Filter & Sort ──────────────────────────────────────────────────── */

  const filtered = dossiers
    .filter(d => {
      if (search) {
        const q = search.toLowerCase();
        if (!d.contact_name.toLowerCase().includes(q) &&
            !d.business_name.toLowerCase().includes(q) &&
            !(d.email || "").toLowerCase().includes(q)) return false;
      }
      if (minFit > 0 && d.fit_score < minFit) return false;
      if (partnerFilter !== "all" && d.partnership_type !== partnerFilter) return false;
      if (statusFilter !== "all" && d.outreach_status !== statusFilter) return false;
      return true;
    })
    .sort((a, b) => {
      let cmp = 0;
      if (sortField === "fit_score") cmp = a.fit_score - b.fit_score;
      else if (sortField === "research_confidence") cmp = a.research_confidence - b.research_confidence;
      else cmp = (a.created_at || "").localeCompare(b.created_at || "");
      return sortAsc ? cmp : -cmp;
    });

  const handleSort = (field: typeof sortField) => {
    if (sortField === field) setSortAsc(!sortAsc);
    else { setSortField(field); setSortAsc(false); }
  };

  const handleApprove = async (id: number) => {
    try {
      await postApi(`/api/dossiers/${id}/approve`, {});
      setToast("Dossier approved for outreach");
      loadData();
    } catch {
      setToast("Failed to approve dossier");
    }
  };

  const copyText = (text: string, label: string) => {
    navigator.clipboard.writeText(text).then(() => setToast(`${label} copied`)).catch(() => {});
  };

  /* ── Pipeline counts ────────────────────────────────────────────────── */

  const groupsCount = hunterStats?.groups_configured ?? 55;
  const scrapedToday = hunterStats?.groups_scraped_today ?? 0;
  const eventTotal = eventStats?.total ?? 0;
  const referralTotal = referralStats?.total ?? 0;
  const leadsFound = eventTotal + referralTotal;
  const researchQueue = dossierStats?.research_queue ?? 0;
  const dossiersReady = dossierStats?.total_dossiers ?? 0;
  const approved = dossierStats?.approved ?? 0;

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-muted animate-pulse">Loading pipeline...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* ── Section A: Pipeline Flow ──────────────────────────────────── */}
      <div>
        <h2 className="text-sm font-medium text-muted mb-3 uppercase tracking-wider">Pipeline Flow</h2>
        <div className="flex items-center gap-1 overflow-x-auto pb-2">
          <StageCard
            label="FB Groups"
            value={groupsCount}
            sub={`${scrapedToday} scraped today`}
            color="border-t-blue-500"
          />
          <Arrow />
          <StageCard
            label="Hunter"
            value={hunterStats?.is_running ? "Active" : "Idle"}
            sub={hunterStats?.session_type === "marathon" ? "Marathon mode" : "Standard"}
            color="border-t-blue-400"
          />
          <Arrow />
          <StageCard
            label="Leads Found"
            value={leadsFound}
            sub={`${eventTotal} event / ${referralTotal} referral`}
            color="border-t-indigo-500"
          />
          <Arrow />
          <StageCard
            label="Research Queue"
            value={researchQueue}
            sub="Pending deep research"
            color="border-t-amber-500"
          />
          <Arrow />
          <StageCard
            label="Dossiers Ready"
            value={dossiersReady}
            sub={`${dossierStats?.high_fit_75plus ?? 0} high fit`}
            color="border-t-emerald-500"
          />
          <Arrow />
          <StageCard
            label="Approved"
            value={approved}
            sub={`${dossierStats?.sent ?? 0} sent`}
            color="border-t-cyan-500"
          />
        </div>
      </div>

      {/* ── Section B: Stats Bar ──────────────────────────────────────── */}
      <div className="flex flex-wrap gap-4 text-sm">
        {[
          { label: "Total Dossiers", value: dossiersReady },
          { label: "High Fit (75+)", value: dossierStats?.high_fit_75plus ?? 0 },
          { label: "Avg Confidence", value: `${dossierStats?.avg_confidence ?? 0}%` },
          { label: "Research Queue", value: researchQueue },
          { label: "Approved", value: approved },
          { label: "Sent", value: dossierStats?.sent ?? 0 },
        ].map(s => (
          <div key={s.label} className="bg-card border border-border rounded-lg px-4 py-2 flex items-baseline gap-2">
            <span className="text-lg font-semibold">{s.value}</span>
            <span className="text-xs text-muted">{s.label}</span>
          </div>
        ))}
      </div>

      {/* ── Section C: Filters ────────────────────────────────────────── */}
      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder="Search name, business, email..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm w-64 focus:border-accent focus:outline-none"
        />
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted">Min Fit:</label>
          <input
            type="range"
            min={0} max={100} step={5}
            value={minFit}
            onChange={e => setMinFit(Number(e.target.value))}
            className="w-24 accent-accent"
          />
          <span className="text-xs text-muted w-8">{minFit}</span>
        </div>
        <select
          value={partnerFilter}
          onChange={e => setPartnerFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm focus:border-accent focus:outline-none"
        >
          <option value="all">All Types</option>
          <option value="referral_partner">Referral Partner</option>
          <option value="secondary_vendor">Secondary Vendor</option>
          <option value="direct_pitch">Direct Pitch</option>
        </select>
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm focus:border-accent focus:outline-none"
        >
          <option value="all">All Statuses</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="sent">Sent</option>
          <option value="replied">Replied</option>
        </select>
        <span className="text-xs text-muted ml-auto">{filtered.length} dossiers</span>
      </div>

      {/* ── Toast ─────────────────────────────────────────────────────── */}
      {toast && (
        <div className="text-xs text-emerald-300 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2">
          {toast}
        </div>
      )}

      {/* ── Section D: Dossier Table ──────────────────────────────────── */}
      {filtered.length === 0 ? (
        <div className="bg-card border border-border rounded-xl p-12 text-center">
          <p className="text-muted text-lg mb-2">No dossiers yet</p>
          <p className="text-xs text-muted">
            The Lead Hunter finds leads and queues them for deep research. Dossiers will appear here once the research agent processes them.
          </p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted uppercase tracking-wider">
                <th className="text-left px-4 py-3">Contact</th>
                <th className="text-left px-4 py-3">Business</th>
                <th className="text-left px-4 py-3 cursor-pointer hover:text-foreground" onClick={() => handleSort("fit_score")}>
                  Fit {sortField === "fit_score" ? (sortAsc ? "↑" : "↓") : ""}
                </th>
                <th className="text-left px-4 py-3">Type</th>
                <th className="text-left px-4 py-3 cursor-pointer hover:text-foreground" onClick={() => handleSort("research_confidence")}>
                  Confidence {sortField === "research_confidence" ? (sortAsc ? "↑" : "↓") : ""}
                </th>
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3 cursor-pointer hover:text-foreground" onClick={() => handleSort("created_at")}>
                  Created {sortField === "created_at" ? (sortAsc ? "↑" : "↓") : ""}
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(d => (
                <>
                  <tr
                    key={d.id}
                    onClick={() => setExpandedId(expandedId === d.id ? null : d.id)}
                    className={`border-b border-border cursor-pointer transition-colors ${
                      expandedId === d.id ? "bg-card-hover" : "hover:bg-card-hover"
                    }`}
                  >
                    <td className="px-4 py-3 font-medium">{d.contact_name || "—"}</td>
                    <td className="px-4 py-3 text-muted">{d.business_name || "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-12 h-1.5 bg-zinc-700 rounded-full overflow-hidden">
                          <div className={`h-full rounded-full ${fitBg(d.fit_score)}`} style={{ width: `${d.fit_score}%` }} />
                        </div>
                        <span className={`font-mono text-xs ${fitColor(d.fit_score)}`}>{d.fit_score}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${partnerBadge(d.partnership_type).cls}`}>
                        {partnerBadge(d.partnership_type).label}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs text-muted">{d.research_confidence}%</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded text-xs ${statusBadge(d.outreach_status).cls}`}>
                        {statusBadge(d.outreach_status).label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-muted">{fmtDate(d.created_at)}</td>
                  </tr>

                  {/* ── Expanded Detail Panel ──────────────────────────── */}
                  {expandedId === d.id && (
                    <tr key={`detail-${d.id}`}>
                      <td colSpan={7} className="px-0 py-0">
                        <DossierDetail dossier={d} onApprove={handleApprove} onCopy={copyText} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ── Dossier Detail Component ────────────────────────────────────────────── */

function DossierDetail({
  dossier: d,
  onApprove,
  onCopy,
}: {
  dossier: Dossier;
  onApprove: (id: number) => void;
  onCopy: (text: string, label: string) => void;
}) {
  const services = parseJsonArray(d.services);
  const eventTypes = parseJsonArray(d.event_types);

  return (
    <div className="bg-[#151515] border-t border-border px-6 py-5 space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* ── Contact Card ──────────────────────────────────────────── */}
        <div className="space-y-3">
          <h4 className="text-xs font-medium text-muted uppercase tracking-wider">Contact</h4>
          <div className="space-y-1.5 text-sm">
            <p className="font-medium text-base">{d.contact_name || "Unknown"}</p>
            {d.business_name && <p className="text-muted">{d.business_name}</p>}
            {d.phone && (
              <p className="flex items-center gap-2">
                <span className="text-muted text-xs">Phone:</span> {d.phone}
              </p>
            )}
            {d.email && (
              <p className="flex items-center gap-2">
                <span className="text-muted text-xs">Email:</span>
                <a href={`mailto:${d.email}`} className="text-accent hover:underline">{d.email}</a>
              </p>
            )}
            {d.website && (
              <p className="flex items-center gap-2">
                <span className="text-muted text-xs">Web:</span>
                <a href={d.website} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline truncate max-w-[200px]">{d.website}</a>
              </p>
            )}
            {d.instagram && (
              <p className="flex items-center gap-2">
                <span className="text-muted text-xs">IG:</span> {d.instagram}
              </p>
            )}
            {d.location && (
              <p className="flex items-center gap-2">
                <span className="text-muted text-xs">Location:</span> {d.location}
                {d.in_service_area ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400">In Area</span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-500/10 text-zinc-400">Outside</span>
                )}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-muted pt-1">
            <span>Lead #{d.lead_id}</span>
            <span>&middot;</span>
            <span className={d.lead_type === "EVENT_OPPORTUNITY" ? "text-amber-400" : "text-purple-400"}>
              {d.lead_type === "EVENT_OPPORTUNITY" ? "Event" : "Referral"}
            </span>
          </div>
        </div>

        {/* ── Business Intel ─────────────────────────────────────────── */}
        <div className="space-y-3">
          <h4 className="text-xs font-medium text-muted uppercase tracking-wider">Business Intel</h4>
          {services.length > 0 && (
            <div>
              <p className="text-xs text-muted mb-1">Services</p>
              <div className="flex flex-wrap gap-1">
                {services.map((s, i) => (
                  <span key={i} className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 text-xs">{s}</span>
                ))}
              </div>
            </div>
          )}
          {eventTypes.length > 0 && (
            <div>
              <p className="text-xs text-muted mb-1">Event Types</p>
              <div className="flex flex-wrap gap-1">
                {eventTypes.map((e, i) => (
                  <span key={i} className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 text-xs">{e}</span>
                ))}
              </div>
            </div>
          )}
          {d.service_area && (
            <p className="text-sm"><span className="text-muted text-xs">Service Area:</span> {d.service_area}</p>
          )}
          {d.price_range && (
            <p className="text-sm"><span className="text-muted text-xs">Price Range:</span> {d.price_range}</p>
          )}
        </div>

        {/* ── Fit Analysis ───────────────────────────────────────────── */}
        <div className="space-y-3">
          <h4 className="text-xs font-medium text-muted uppercase tracking-wider">Fit Analysis</h4>
          <div className="flex items-center gap-3">
            <span className={`text-4xl font-bold ${fitColor(d.fit_score)}`}>{d.fit_score}</span>
            <div>
              <span className={`px-2 py-0.5 rounded text-xs ${partnerBadge(d.partnership_type).cls}`}>
                {partnerBadge(d.partnership_type).label}
              </span>
              <div className="w-24 h-1.5 bg-zinc-700 rounded-full overflow-hidden mt-2">
                <div className={`h-full rounded-full ${fitBg(d.fit_score)}`} style={{ width: `${d.fit_score}%` }} />
              </div>
            </div>
          </div>
          {d.fit_reasoning && (
            <div>
              <p className="text-xs text-muted mb-0.5">Reasoning</p>
              <p className="text-sm text-zinc-300 leading-relaxed">{d.fit_reasoning}</p>
            </div>
          )}
          {d.approach && (
            <div>
              <p className="text-xs text-muted mb-0.5">Approach</p>
              <p className="text-sm text-zinc-300 leading-relaxed">{d.approach}</p>
            </div>
          )}
          {d.objection_prep && (
            <div>
              <p className="text-xs text-muted mb-0.5">Objection Prep</p>
              <p className="text-sm text-zinc-300 leading-relaxed">{d.objection_prep}</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Outreach Ready ────────────────────────────────────────────── */}
      {(d.email_subject || d.email_body || d.call_script) && (
        <div className="border-t border-border pt-4 space-y-3">
          <h4 className="text-xs font-medium text-muted uppercase tracking-wider">Outreach Materials</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {d.email_body && (
              <div className="bg-background border border-border rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted">Email Draft</p>
                  <button
                    onClick={() => onCopy(d.email_body, "Email")}
                    className="text-[10px] text-accent hover:underline"
                  >
                    Copy
                  </button>
                </div>
                {d.email_subject && <p className="text-xs font-medium">Subject: {d.email_subject}</p>}
                <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">{d.email_body}</p>
              </div>
            )}
            {d.call_script && (
              <div className="bg-background border border-border rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted">Call Script</p>
                  <button
                    onClick={() => onCopy(d.call_script, "Call script")}
                    className="text-[10px] text-accent hover:underline"
                  >
                    Copy
                  </button>
                </div>
                <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">{d.call_script}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Meta + Actions ────────────────────────────────────────────── */}
      <div className="border-t border-border pt-4 flex items-center justify-between">
        <div className="flex items-center gap-4 text-xs text-muted">
          <span>Confidence: <strong className="text-foreground">{d.research_confidence}%</strong></span>
          <span>Model: {d.model_used || "—"}</span>
          <span>Researched: {fmtDate(d.created_at)}</span>
        </div>
        <div className="flex items-center gap-2">
          {d.outreach_status === "pending" && (
            <button
              onClick={() => onApprove(d.id)}
              className="bg-accent text-white text-xs px-4 py-1.5 rounded-lg hover:bg-blue-600 transition-colors"
            >
              Approve for Outreach
            </button>
          )}
          {d.outreach_status === "approved" && (
            <span className="text-xs text-cyan-400">Approved — ready for outreach</span>
          )}
        </div>
      </div>
    </div>
  );
}
