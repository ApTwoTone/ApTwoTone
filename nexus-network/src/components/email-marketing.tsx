"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface EmailDraft {
  vendor_id: number;
  vendor_name: string;
  email: string;
  phone?: string;
  website?: string;
  category: string;
  city: string;
  campaign_quality: number;
  segment: string;
  subject: string;
  plain_body: string;
  html_body: string;
  template_type?: string;
  step_number: number;
}

interface PreviewBatchResponse {
  batch: EmailDraft[];
  count: number;
  total_eligible: number;
  segment: string | null;
}

interface MarketingStats {
  total_vendors: number;
  with_email: number;
  contacted: number;
  eligible_uncontacted: number;
  by_segment: Record<string, number>;
  sent_total: number;
  sent_today: number;
  replied: number;
  bounced: number;
  reply_rate_pct: number;
  bounce_rate_pct: number;
}

interface CampaignHealth {
  sent_today: number;
  daily_limit: number;
  daily_remaining: number;
  sent_this_hour: number;
  hourly_limit: number;
  hourly_remaining: number;
  health: string;
}

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

// ── Quality helpers ──────────────────────────────────────────────────────────

function qualityMissing(v: EmailDraft): number {
  return [!!v.email, !!v.phone, !!v.website, cityInApprovedList(v.city)].filter((x) => !x).length;
}

function qualityColor(v: EmailDraft): string {
  const missing = qualityMissing(v);
  if (missing === 0) return "bg-green-400";
  if (missing === 1) return "bg-yellow-400";
  return "bg-red-400";
}

function qualityLabel(v: EmailDraft): string {
  const missing = qualityMissing(v);
  if (missing === 0) return "Send-ready";
  if (missing === 1) return "Verify";
  return "Low quality";
}

function isRedQuality(v: EmailDraft): boolean {
  return qualityMissing(v) >= 2;
}

// NOTE: Email HTML preview uses dangerouslySetInnerHTML with server-generated
// content from cold_email.py templates only — never user-supplied HTML.
// This is trusted content from our own backend template engine.

// ── Component ────────────────────────────────────────────────────────────────

export function EmailMarketing() {
  const [stats, setStats] = useState<MarketingStats | null>(null);
  const [health, setHealth] = useState<CampaignHealth | null>(null);

  // Single-vendor queue
  const [queue, setQueue] = useState<EmailDraft[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [totalEligible, setTotalEligible] = useState(0);
  const [totalReviewed, setTotalReviewed] = useState(0);

  // Editable fields for current vendor
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [previewHtml, setPreviewHtml] = useState(false);

  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  const showToast = (msg: string, type: "success" | "error") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Load stats + health on mount
  const refreshStats = useCallback(async () => {
    try { setStats(await fetchApi<MarketingStats>("/api/email-marketing/stats")); } catch { /* non-critical */ }
    try { setHealth(await fetchApi<CampaignHealth>("/api/email-marketing/campaign-health")); } catch { /* non-critical */ }
  }, []);

  useEffect(() => { refreshStats(); }, [refreshStats]);

  // Load batch of vendors (fetches 20 at a time for buffer)
  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchApi<PreviewBatchResponse>(
        `/api/email-marketing/preview-batch?count=20&min_quality=40`
      );
      setQueue(res.batch);
      setTotalEligible(res.total_eligible);
      setTotalReviewed(0);
      // Skip to first non-red vendor
      const firstGood = res.batch.findIndex((v) => !isRedQuality(v));
      const startIdx = firstGood >= 0 ? firstGood : 0;
      setCurrentIndex(startIdx);
      if (res.batch.length > 0) {
        setEditSubject(res.batch[startIdx].subject);
        setEditBody(res.batch[startIdx].plain_body);
      }
    } catch {
      showToast("Failed to load vendors", "error");
    }
    setLoading(false);
  }, []);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  // Current vendor
  const current = queue[currentIndex] || null;
  const qDot = current ? qualityColor(current) : "";
  const qLabel = current ? qualityLabel(current) : "";

  // Find next non-red vendor starting from idx
  const findNextGood = (startIdx: number): number => {
    for (let i = startIdx; i < queue.length; i++) {
      if (!isRedQuality(queue[i])) return i;
    }
    return -1; // all remaining are red
  };

  // Advance to next vendor (auto-skips red)
  const goNext = () => {
    const goodIdx = findNextGood(currentIndex + 1);
    if (goodIdx >= 0) {
      setCurrentIndex(goodIdx);
      setEditSubject(queue[goodIdx].subject);
      setEditBody(queue[goodIdx].plain_body);
      setPreviewHtml(false);
      setTotalReviewed((r) => r + 1);
    } else {
      // Queue exhausted — reload
      loadQueue();
      refreshStats();
    }
  };

  // Skip current vendor
  const handleSkip = async () => {
    if (!current) return;
    try {
      await postApi("/api/email-marketing/skip", { vendor_ids: [current.vendor_id] });
      showToast(`Skipped ${current.vendor_name}`, "success");
      goNext();
    } catch {
      showToast("Skip failed", "error");
    }
  };

  // Send email to current vendor
  const handleSend = async () => {
    if (!current) return;
    setSending(true);
    try {
      const res = await postApi<{ sent: number; errors: { vendor_id: number; error: string }[] }>(
        "/api/email-marketing/approve-batch",
        {
          emails: [{
            vendor_id: current.vendor_id,
            subject: editSubject,
            plain_body: editBody,
            html_body: current.html_body,
          }],
        },
        60000
      );
      if (res.sent > 0) {
        showToast(`Email sent to ${current.vendor_name}`, "success");
        goNext();
        refreshStats();
      } else {
        const err = res.errors?.[0]?.error || "Send failed";
        showToast(err, "error");
      }
    } catch (e) {
      showToast(String(e), "error");
    }
    setSending(false);
  };

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all ${
          toast.type === "success" ? "bg-success/20 text-success border border-success/30" : "bg-danger/20 text-danger border border-danger/30"
        }`}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Email Marketing</h1>
          <p className="text-sm text-muted mt-1">Review one vendor at a time — Send or Skip</p>
        </div>
        {health && (
          <div className="flex items-center gap-3">
            <div className={`px-3 py-1.5 rounded-lg text-xs font-medium ${
              health.health === "good" ? "bg-success/10 text-success" : "bg-warning/10 text-warning"
            }`}>
              {health.daily_remaining}/{health.daily_limit} daily remaining
            </div>
          </div>
        )}
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {[
            { label: "With Email", value: stats.with_email.toLocaleString() },
            { label: "Eligible", value: stats.eligible_uncontacted.toLocaleString(), accent: true },
            { label: "Sent Total", value: stats.sent_total.toLocaleString() },
            { label: "Sent Today", value: stats.sent_today.toString() },
            { label: "Replied", value: `${stats.replied} (${stats.reply_rate_pct}%)`, success: stats.replied > 0 },
            { label: "Reviewed Now", value: String(totalReviewed) },
          ].map((s) => (
            <div key={s.label} className="bg-card border border-border rounded-lg px-3 py-2.5">
              <p className="text-xs text-muted">{s.label}</p>
              <p className={`text-lg font-semibold mt-0.5 ${
                s.accent ? "text-accent" : s.success ? "text-success" : ""
              }`}>
                {s.value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
          <span className="ml-3 text-sm text-muted">Loading vendors...</span>
        </div>
      )}

      {/* Empty State */}
      {!loading && !current && (
        <div className="text-center py-20">
          <p className="text-lg text-muted">No eligible vendors to review</p>
          <p className="text-sm text-muted mt-2">All vendors have been contacted or skipped</p>
          <button onClick={loadQueue} className="mt-4 px-4 py-2 bg-accent/15 text-accent rounded-lg text-sm hover:bg-accent/25">
            Reload Queue
          </button>
        </div>
      )}

      {/* Single Vendor View */}
      {!loading && current && (
        <div className="max-w-3xl mx-auto space-y-4">
          {/* Counter */}
          <div className="text-center text-sm text-muted">
            Vendor {currentIndex + 1} of {queue.length} loaded ({totalEligible.toLocaleString()} total eligible)
          </div>

          {/* Vendor Info Card */}
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className={`w-3 h-3 rounded-full ${qDot}`} title={qLabel} />
                <div>
                  <h3 className="text-lg font-bold">{current.vendor_name}</h3>
                  <p className="text-sm text-muted mt-0.5">
                    {current.category.replace(/_/g, " ")} {current.city ? `\u2022 ${current.city}` : ""}
                  </p>
                </div>
              </div>
              <span className="text-xs text-muted capitalize px-2 py-1 bg-card-hover rounded">
                {current.template_type || current.segment} template
              </span>
            </div>
            <div className="grid grid-cols-3 gap-4 mt-4 text-sm">
              <div>
                <span className="text-xs text-muted block">Email</span>
                <span className="font-mono text-xs">{current.email}</span>
              </div>
              <div>
                <span className="text-xs text-muted block">Phone</span>
                <span className="font-mono text-xs">{current.phone || "\u2014"}</span>
              </div>
              <div>
                <span className="text-xs text-muted block">Website</span>
                {current.website ? (
                  <a href={current.website.startsWith("http") ? current.website : `https://${current.website}`}
                    target="_blank" rel="noopener noreferrer"
                    className="text-accent text-xs hover:underline truncate block">
                    {current.website.replace(/^https?:\/\/(www\.)?/, "").slice(0, 30)}
                  </a>
                ) : <span className="text-xs text-muted">{"\u2014"}</span>}
              </div>
            </div>
          </div>

          {/* Email Editor */}
          <div className="bg-card border border-border rounded-xl p-5 space-y-4">
            <div>
              <label className="text-xs text-muted block mb-1">From</label>
              <div className="text-sm text-muted bg-background border border-border rounded-md px-3 py-1.5">
                Zoar Bathroom Rentals &lt;zoarbathrooms@gmail.com&gt;
              </div>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">To</label>
              <div className="text-sm bg-background border border-border rounded-md px-3 py-1.5 font-mono text-xs">
                {current.email}
              </div>
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Subject</label>
              <input
                type="text"
                value={editSubject}
                onChange={(e) => setEditSubject(e.target.value)}
                className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs text-muted">Body</label>
                <button
                  onClick={() => setPreviewHtml(!previewHtml)}
                  className="text-xs text-accent hover:text-accent/80"
                >
                  {previewHtml ? "Edit" : "HTML Preview"}
                </button>
              </div>
              {previewHtml ? (
                // Server-generated HTML from cold_email.py templates only — trusted content
                <div
                  className="bg-white text-black rounded-md p-4 text-sm min-h-[250px] max-h-[400px] overflow-auto border border-border"
                  dangerouslySetInnerHTML={{ __html: current.html_body }}
                />
              ) : (
                <textarea
                  value={editBody}
                  onChange={(e) => setEditBody(e.target.value)}
                  className="w-full bg-background border border-border rounded-md px-3 py-2 text-sm resize-none h-64 focus:outline-none focus:border-accent font-mono text-xs leading-relaxed"
                />
              )}
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center justify-between">
            <button
              onClick={handleSkip}
              className="px-6 py-3 rounded-xl bg-card border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover transition-colors"
            >
              Skip
            </button>
            <button
              onClick={handleSend}
              disabled={sending || !current.email}
              className="px-8 py-3 rounded-xl bg-accent text-white text-sm font-semibold hover:bg-accent/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {sending ? "Sending..." : "Send Email"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
