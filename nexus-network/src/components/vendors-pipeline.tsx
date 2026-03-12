"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface Vendor {
  id: number;
  name: string;
  phone: string;
  email: string;
  website: string;
  address: string;
  city: string;
  category: string;
  source: string;
  status: string;
  outreach_status: string;
  rating: number;
  review_count: number;
  contact_name?: string;
  contact_title?: string;
  referral_score?: number;
  distance_tier?: number;
  notes?: string;
  created_at: string;
  updated_at?: string;
}

interface VendorStats {
  total: number;
  by_category: Record<string, number>;
  by_status: Record<string, number>;
  outreach_total: number;
  outreach_pending: number;
  outreach_sent: number;
  outreach_replied: number;
}

interface OutreachRecord {
  id: number;
  vendor_id: number;
  channel: string;
  message_draft: string;
  status: string;
  sent_at: string | null;
  response: string;
  created_at: string;
}

interface EmailDraft {
  to: string;
  subject: string;
  plain_body: string;
  html_body: string;
  vendor_name: string;
  template_type: string;
}

// ── Constants ────────────────────────────────────────────────────────────────

const CATEGORY_LABELS: Record<string, string> = {
  wedding_venue: "Wedding Venues",
  wedding_planner: "Wedding Planners",
  event_planner: "Event Planners",
  quinceanera_venue: "Quinceañera Venues",
  quinceanera_planner: "Quinceañera Planners",
  catering: "Catering",
  party_rental: "Party Rental",
  dj_entertainment: "DJ & Entertainment",
  photography: "Photography",
  florist: "Florists",
  bartending_mobile_bar: "Bartending",
  construction: "Construction",
  film_production: "Film Production",
  festival_organizer: "Festivals",
  tent_rental: "Tent Rental",
  bounce_house: "Bounce House",
  photo_booth: "Photo Booth",
  church: "Church",
};

const OUTREACH_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  none:          { label: "New",     color: "text-blue-400 bg-blue-500/10" },
  "":            { label: "New",     color: "text-blue-400 bg-blue-500/10" },
  draft_ready:   { label: "Drafted", color: "text-purple-400 bg-purple-500/10" },
  approved:      { label: "Ready",   color: "text-cyan-400 bg-cyan-500/10" },
  sent:          { label: "Sent",    color: "text-yellow-400 bg-yellow-500/10" },
  replied:       { label: "Replied", color: "text-green-400 bg-green-500/10" },
};

// ── Approved cities (mirrors backend ALLOWED_CITIES in vendor_db.py) ────────

const ALLOWED_CITIES = new Set([
  "chatsworth", "woodland hills", "canoga park", "west hills", "winnetka",
  "reseda", "tarzana", "encino", "sherman oaks", "studio city",
  "north hollywood", "van nuys", "panorama city", "arleta", "pacoima",
  "sun valley", "sunland", "tujunga", "sylmar", "granada hills",
  "northridge", "porter ranch", "san fernando",
  "calabasas", "hidden hills", "agoura hills", "thousand oaks", "simi valley",
  "burbank", "glendale", "pasadena",
  "santa clarita", "valencia", "newhall", "canyon country",
]);

function cityInApprovedList(city: string | undefined): boolean {
  if (!city) return false;
  return ALLOWED_CITIES.has(city.split(",")[0].trim().toLowerCase());
}

function normalizeWebsiteUrl(website: string | undefined): string | null {
  const raw = (website || "").trim();
  if (!raw) return null;
  const withProtocol = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
  try {
    const parsed = new URL(withProtocol);
    if (!parsed.hostname || !parsed.hostname.includes(".")) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function qualityScore(v: Vendor): number {
  let score = 0;
  if (v.email) score++;
  if (v.phone) score++;
  if (v.website) score++;
  if (cityInApprovedList(v.city)) score++;
  return score;
}

function qualityBadge(score: number): { color: string; label: string } {
  if (score >= 4) return { color: "bg-green-400", label: "Ready" };
  if (score >= 3) return { color: "bg-yellow-400", label: "Verify" };
  return { color: "bg-red-400", label: "Low" };
}

function timeAgo(ts: string | null): string {
  if (!ts) return "—";
  const ago = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (isNaN(ago) || ago < 0) return "—";
  if (ago < 60) return "just now";
  if (ago < 3600) return `${Math.floor(ago / 60)}m ago`;
  if (ago < 86400) return `${Math.floor(ago / 3600)}h ago`;
  return `${Math.floor(ago / 86400)}d ago`;
}

// NOTE: Email HTML preview uses dangerouslySetInnerHTML with server-generated
// content from cold_email.py templates only — never user-supplied HTML.

// ── Component ────────────────────────────────────────────────────────────────

export function VendorsPipeline() {
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [stats, setStats] = useState<VendorStats | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [catFilter, setCatFilter] = useState("");
  const [outreachFilter, setOutreachFilter] = useState("");
  const [sendReadyOnly, setSendReadyOnly] = useState(true);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 50;

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [outreachHistory, setOutreachHistory] = useState<OutreachRecord[]>([]);
  const [composerVendorId, setComposerVendorId] = useState<number | null>(null);
  const [draft, setDraft] = useState<EmailDraft | null>(null);
  const [draftEdits, setDraftEdits] = useState<{ to: string; subject: string; body: string }>({ to: "", subject: "", body: "" });
  const [previewMode, setPreviewMode] = useState(false);
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkSending, setBulkSending] = useState(false);
  const [editingNotes, setEditingNotes] = useState("");

  const fetchVendors = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (catFilter) params.set("category", catFilter);
      if (outreachFilter) params.set("outreach_status", outreachFilter);
      if (sendReadyOnly) params.set("send_ready", "true");
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(page * PAGE_SIZE));
      const resp = await fetchApi<{ vendors: Vendor[]; total: number }>(`/api/vendors?${params.toString()}`);
      setVendors(resp.vendors || []);
      setTotal(resp.total || 0);
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, [search, catFilter, outreachFilter, sendReadyOnly, page]);

  const fetchStats = useCallback(async () => {
    try { setStats(await fetchApi<VendorStats>("/api/vendors/stats")); } catch { /* non-critical */ }
  }, []);

  useEffect(() => { fetchVendors(); }, [fetchVendors]);
  useEffect(() => { fetchStats(); const iv = setInterval(fetchStats, 30000); return () => clearInterval(iv); }, [fetchStats]);

  const toggleExpand = async (vendor: Vendor) => {
    if (expandedId === vendor.id) { setExpandedId(null); setComposerVendorId(null); setDraft(null); return; }
    setExpandedId(vendor.id);
    setComposerVendorId(null);
    setDraft(null);
    setEditingNotes(vendor.notes || "");
    try {
      const resp = await fetchApi<{ vendor: Vendor; outreach_history: OutreachRecord[] }>(`/api/vendors/${vendor.id}`);
      setOutreachHistory(resp.outreach_history || []);
    } catch { setOutreachHistory([]); }
  };

  const openComposer = async (vendorId: number) => {
    setComposerVendorId(vendorId);
    setPreviewMode(false);
    try {
      const d = await postApi<EmailDraft>(`/api/vendors/${vendorId}/draft-email`, {});
      setDraft(d);
      setDraftEdits({ to: d.to, subject: d.subject, body: d.plain_body });
    } catch (e) { showToast("Failed to load template: " + String(e), "error"); }
  };

  const handleSend = async () => {
    if (!composerVendorId || !draft) return;
    setSending(true);
    try {
      const result = await postApi<{ ok?: boolean; error?: string }>(`/api/vendors/${composerVendorId}/send-email`, {
        to_email: draftEdits.to, subject: draftEdits.subject, plain_body: draftEdits.body, html_body: draft.html_body,
      });
      if (result.ok) { showToast(`Email sent to ${draft.vendor_name}`, "success"); setComposerVendorId(null); setDraft(null); fetchVendors(); fetchStats(); }
      else showToast(result.error || "Send failed", "error");
    } catch (e) { showToast(String(e), "error"); }
    finally { setSending(false); }
  };

  const handleSaveDraft = async () => {
    if (!composerVendorId) return;
    try { await postApi(`/api/vendors/${composerVendorId}/outreach`, { channel: "email", message_draft: draftEdits.body }); showToast("Draft saved", "success"); }
    catch (e) { showToast("Save failed: " + String(e), "error"); }
  };

  const handleMarkSkip = async (vendorId: number) => {
    try { await postApi(`/api/vendors/${vendorId}/status`, { outreach_status: "skip", status: "not_interested" }); showToast("Marked as skip", "success"); setExpandedId(null); fetchVendors(); }
    catch (e) { showToast(String(e), "error"); }
  };

  const handleSaveNotes = async (vendorId: number) => {
    try { await postApi(`/api/vendors/${vendorId}/status`, { notes: editingNotes }); showToast("Notes saved", "success"); }
    catch (e) { showToast(String(e), "error"); }
  };

  const handleBulkSend = async () => {
    if (selectedIds.size === 0) return;
    setBulkSending(true);
    try {
      const result = await postApi<{ sent: number; blocked: number; errors: Array<{ vendor_id: number; error: string }> }>("/api/vendors/bulk-send", { vendor_ids: Array.from(selectedIds) });
      showToast(`Bulk: ${result.sent} sent, ${result.blocked} blocked, ${result.errors.length} errors`, result.errors.length > 0 ? "error" : "success");
      setSelectedIds(new Set()); fetchVendors(); fetchStats();
    } catch (e) { showToast(String(e), "error"); }
    finally { setBulkSending(false); }
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };

  const showToast = (msg: string, type: "success" | "error") => { setToast({ msg, type }); setTimeout(() => setToast(null), 4000); };

  const totalPages = Math.ceil(total / PAGE_SIZE);

  // Default sort: green (4) first, yellow (3), red (0-2) last
  const sortedVendors = [...vendors].sort((a, b) => qualityScore(b) - qualityScore(a));

  return (
    <div className="flex-1 overflow-auto p-6 space-y-5">
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg ${toast.type === "success" ? "bg-success/20 text-success border border-success/30" : "bg-red-500/20 text-red-400 border border-red-500/30"}`}>
          {toast.msg}
        </div>
      )}

      <div>
        <h2 className="text-2xl font-bold">Vendors</h2>
        <p className="text-sm text-muted mt-1">Referral partner outreach engine</p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: "Total Vendors", value: stats.total },
            { label: "Outreach Drafts", value: stats.outreach_pending },
            { label: "Emails Sent", value: stats.outreach_sent, accent: true },
            { label: "Replies", value: stats.outreach_replied, accent: true },
            { label: "Partners", value: stats.by_status?.partner || 0, accent: true },
          ].map((s) => (
            <div key={s.label} className="bg-card border border-border rounded-xl px-4 py-3">
              <p className="text-xs text-muted">{s.label}</p>
              <p className={`text-2xl font-bold mt-1 ${s.accent ? "text-accent" : ""}`}>{s.value.toLocaleString()}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-3 bg-card border border-border rounded-lg p-3">
        <input type="text" placeholder="Search vendors..." value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          className="flex-1 min-w-[200px] bg-transparent border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent" />
        <select value={catFilter} onChange={(e) => { setCatFilter(e.target.value); setPage(0); }}
          className="bg-card border border-border rounded-md px-2 py-1.5 text-sm">
          <option value="">All Categories</option>
          {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={outreachFilter} onChange={(e) => { setOutreachFilter(e.target.value); setPage(0); }}
          className="bg-card border border-border rounded-md px-2 py-1.5 text-sm">
          <option value="">All Status</option>
          <option value="uncontacted">Uncontacted</option>
          <option value="draft_ready">Drafted</option>
          <option value="sent">Sent</option>
          <option value="replied">Replied</option>
          <option value="skip">Skipped</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm text-muted cursor-pointer">
          <input type="checkbox" checked={sendReadyOnly} onChange={(e) => { setSendReadyOnly(e.target.checked); setPage(0); }} className="rounded" />
          Send-ready only
        </label>
      </div>

      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 bg-accent/10 border border-accent/30 rounded-lg px-4 py-2">
          <span className="text-sm font-medium text-accent">{selectedIds.size} selected</span>
          <button onClick={handleBulkSend} disabled={bulkSending}
            className="px-3 py-1 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent/80 disabled:opacity-50">
            {bulkSending ? "Sending..." : "Send Batch"}
          </button>
          <button onClick={() => setSelectedIds(new Set())} className="px-3 py-1 rounded-md text-sm text-muted hover:text-foreground">Clear</button>
        </div>
      )}

      {error && <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-red-400 text-sm">{error}</div>}

      {/* Vendor Table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted">
              <th className="px-3 py-3 w-8">
                <input type="checkbox"
                  checked={selectedIds.size === sortedVendors.length && sortedVendors.length > 0}
                  onChange={(e) => { if (e.target.checked) setSelectedIds(new Set(sortedVendors.map(v => v.id))); else setSelectedIds(new Set()); }}
                  className="rounded" />
              </th>
              <th className="px-3 py-3">Vendor</th>
              <th className="px-3 py-3">Category</th>
              <th className="px-3 py-3">Phone</th>
              <th className="px-3 py-3">Website</th>
              <th className="px-3 py-3">Contact</th>
              <th className="px-3 py-3">Outreach</th>
              <th className="px-3 py-3 w-20">Action</th>
            </tr>
          </thead>
          <tbody>
            {sortedVendors.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-12 text-center text-muted">No vendors match your filters.</td></tr>
            )}
            {sortedVendors.map((v) => {
              const qs = qualityScore(v);
              const badge = qualityBadge(qs);
              const outStatus = OUTREACH_STATUS_LABELS[v.outreach_status || "none"] || OUTREACH_STATUS_LABELS["none"];
              const isExpanded = expandedId === v.id;
              const showComposer = composerVendorId === v.id && draft;

              return (
                <VendorRowBlock key={v.id}
                  v={v} qs={qs} badge={badge} outStatus={outStatus} isExpanded={isExpanded}
                  isSelected={selectedIds.has(v.id)} showComposer={!!showComposer}
                  onToggleSelect={() => toggleSelect(v.id)} onToggleExpand={() => toggleExpand(v)}
                  outreachHistory={isExpanded ? outreachHistory : []}
                  draft={draft} draftEdits={draftEdits} previewMode={previewMode} sending={sending}
                  editingNotes={editingNotes}
                  onOpenComposer={() => openComposer(v.id)} onSend={handleSend} onSaveDraft={handleSaveDraft}
                  onMarkSkip={() => handleMarkSkip(v.id)} onSaveNotes={() => handleSaveNotes(v.id)}
                  setDraftEdits={setDraftEdits} setPreviewMode={setPreviewMode} setEditingNotes={setEditingNotes}
                />
              );
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted">{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}</span>
          <div className="flex gap-2">
            <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
              className="px-3 py-1 rounded-md text-sm bg-card border border-border disabled:opacity-30 hover:bg-card-hover">Prev</button>
            <button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}
              className="px-3 py-1 rounded-md text-sm bg-card border border-border disabled:opacity-30 hover:bg-card-hover">Next</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Vendor Row with Expand ───────────────────────────────────────────────────

function VendorRowBlock({ v, qs, badge, outStatus, isExpanded, isSelected, showComposer,
  onToggleSelect, onToggleExpand, outreachHistory, draft, draftEdits, previewMode, sending,
  editingNotes, onOpenComposer, onSend, onSaveDraft, onMarkSkip, onSaveNotes,
  setDraftEdits, setPreviewMode, setEditingNotes,
}: {
  v: Vendor; qs: number; badge: { color: string; label: string };
  outStatus: { label: string; color: string }; isExpanded: boolean; isSelected: boolean; showComposer: boolean;
  onToggleSelect: () => void; onToggleExpand: () => void; outreachHistory: OutreachRecord[];
  draft: EmailDraft | null; draftEdits: { to: string; subject: string; body: string };
  previewMode: boolean; sending: boolean; editingNotes: string;
  onOpenComposer: () => void; onSend: () => void; onSaveDraft: () => void;
  onMarkSkip: () => void; onSaveNotes: () => void;
  setDraftEdits: (e: { to: string; subject: string; body: string }) => void;
  setPreviewMode: (v: boolean) => void; setEditingNotes: (v: string) => void;
}) {
  const websiteHref = normalizeWebsiteUrl(v.website);

  return (
    <>
      <tr className={`border-b border-border/50 hover:bg-card-hover cursor-pointer ${isExpanded ? "bg-card-hover" : ""}`} onClick={onToggleExpand}>
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={isSelected} onChange={onToggleSelect} className="rounded" />
        </td>
        <td className="px-3 py-2">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${badge.color}`} title={`Quality: ${qs}/4 — ${badge.label}`} />
            <div>
              <p className="font-medium">{v.name}</p>
              <p className="text-xs text-muted">{v.city || "—"}</p>
            </div>
          </div>
        </td>
        <td className="px-3 py-2 text-muted">{CATEGORY_LABELS[v.category] || v.category}</td>
        <td className="px-3 py-2 text-muted font-mono text-xs">{v.phone || "—"}</td>
        <td className="px-3 py-2">
          {websiteHref ? (
            <a href={websiteHref}
              target="_blank" rel="noopener noreferrer"
              className="text-accent text-xs hover:underline truncate block max-w-[140px]"
              onClick={(e) => e.stopPropagation()}>
              {v.website.replace(/^https?:\/\/(www\.)?/, "").slice(0, 25)}
            </a>
          ) : v.website ? (
            <span className="text-warning text-xs">Invalid URL</span>
          ) : <span className="text-muted">—</span>}
        </td>
        <td className="px-3 py-2 text-muted text-xs">{v.contact_name || "—"}</td>
        <td className="px-3 py-2">
          <span className={`px-2 py-0.5 rounded-full text-xs ${outStatus.color}`}>{outStatus.label}</span>
        </td>
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          {v.email && <button onClick={onOpenComposer} className="text-xs text-accent hover:text-accent/80 font-medium">Email</button>}
        </td>
      </tr>

      {isExpanded && (
        <tr>
          <td colSpan={8} className="p-0">
            <div className="bg-card/50 border-b border-border p-4">
              <div className={`grid gap-4 ${showComposer ? "grid-cols-2" : "grid-cols-1"}`}>
                {/* Left: Details */}
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                    <div><span className="text-muted">Address:</span> {v.address || "—"}</div>
                    <div><span className="text-muted">Email:</span> <span className="font-mono text-xs">{v.email || "—"}</span></div>
                    <div><span className="text-muted">Phone:</span> <span className="font-mono text-xs">{v.phone || "—"}</span></div>
                    <div><span className="text-muted">Website:</span>{" "}
                      {websiteHref ? <a href={websiteHref} target="_blank" rel="noopener noreferrer" className="text-accent text-xs hover:underline">{v.website}</a> : (v.website ? <span className="text-warning text-xs">Invalid URL</span> : "—")}
                    </div>
                    <div><span className="text-muted">Source:</span> {v.source || "—"}</div>
                    <div><span className="text-muted">Added:</span> {v.created_at ? new Date(v.created_at).toLocaleDateString() : "—"}</div>
                  </div>

                  {outreachHistory.length > 0 && (
                    <div>
                      <h4 className="text-xs font-semibold text-muted uppercase mb-2">Outreach History</h4>
                      <div className="space-y-2">
                        {outreachHistory.map((o) => (
                          <div key={o.id} className="bg-card border border-border rounded-lg p-2 text-xs">
                            <div className="flex justify-between">
                              <span className="font-medium">{o.channel} — {o.status}</span>
                              <span className="text-muted">{timeAgo(o.sent_at || o.created_at)}</span>
                            </div>
                            <p className="text-muted mt-1 truncate">{o.message_draft.slice(0, 100)}</p>
                            {o.response && <p className="text-success mt-1">Reply: {o.response.slice(0, 100)}</p>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div>
                    <h4 className="text-xs font-semibold text-muted uppercase mb-2">Notes</h4>
                    <div className="flex gap-2">
                      <textarea value={editingNotes} onChange={(e) => setEditingNotes(e.target.value)}
                        className="flex-1 bg-card border border-border rounded-md px-3 py-2 text-sm resize-none h-16 focus:outline-none focus:border-accent"
                        placeholder="Add notes about this vendor..." />
                      <button onClick={onSaveNotes} className="px-3 py-1 rounded-md text-xs bg-accent/15 text-accent hover:bg-accent/25 self-end">Save</button>
                    </div>
                  </div>

                  <div className="flex gap-2">
                    {v.email && <button onClick={onOpenComposer} className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium hover:bg-accent/80">Draft Email</button>}
                    <button onClick={onMarkSkip} className="px-4 py-2 rounded-lg bg-card border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover">Mark as Skip</button>
                  </div>
                </div>

                {/* Right: Email Composer */}
                {showComposer && draft && (
                  <div className="border-l border-border pl-4 space-y-3">
                    <h4 className="text-sm font-semibold">Email Composer</h4>
                    <div>
                      <label className="text-xs text-muted block mb-1">From</label>
                      <div className="text-sm text-muted bg-card border border-border rounded-md px-3 py-1.5">Zoar Bathroom Rentals &lt;zoarbathrooms@gmail.com&gt;</div>
                    </div>
                    <div>
                      <label className="text-xs text-muted block mb-1">To</label>
                      <input type="email" value={draftEdits.to} onChange={(e) => setDraftEdits({ ...draftEdits, to: e.target.value })}
                        className="w-full bg-card border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent" />
                    </div>
                    <div>
                      <label className="text-xs text-muted block mb-1">Subject</label>
                      <input type="text" value={draftEdits.subject} onChange={(e) => setDraftEdits({ ...draftEdits, subject: e.target.value })}
                        className="w-full bg-card border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent" />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs text-muted">Body</label>
                        <button onClick={() => setPreviewMode(!previewMode)} className="text-xs text-accent hover:text-accent/80">
                          {previewMode ? "Edit" : "Preview"}
                        </button>
                      </div>
                      {previewMode ? (
                        // Server-generated HTML from cold_email.py templates only
                        <div className="bg-white text-black rounded-md p-4 text-sm min-h-[200px] max-h-[300px] overflow-auto"
                          dangerouslySetInnerHTML={{ __html: draft.html_body }} />
                      ) : (
                        <textarea value={draftEdits.body} onChange={(e) => setDraftEdits({ ...draftEdits, body: e.target.value })}
                          className="w-full bg-card border border-border rounded-md px-3 py-2 text-sm resize-none h-48 focus:outline-none focus:border-accent font-mono text-xs" />
                      )}
                    </div>
                    <div className="text-xs text-muted">Template: <span className="text-accent capitalize">{draft.template_type}</span></div>
                    <div className="flex gap-2">
                      <button onClick={onSend} disabled={sending || !draftEdits.to}
                        className="px-4 py-2 rounded-lg bg-success text-white text-sm font-medium hover:bg-success/80 disabled:opacity-50">
                        {sending ? "Sending..." : "Send"}
                      </button>
                      <button onClick={onSaveDraft} className="px-4 py-2 rounded-lg bg-card border border-border text-sm hover:bg-card-hover">Save Draft</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
