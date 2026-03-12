"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi, asArray } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface EmailDraft {
  queue_id?: number;
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
  status?: string;
  scheduled_date?: string;
  quality_score?: number;
  quality_reasons?: string[];
  quality_flags?: string[];
  contactability_signals?: number;
  generic_inbox?: boolean;
  name_source?: string;
  name_confidence?: number;
  name_personalized?: boolean;
  proof_snippet?: string;
}

interface PreviewBatchResponse {
  batch: EmailDraft[];
  count: number;
  total_eligible: number;
  segment: string | null;
  scheduled_date?: string;
  by_status?: Record<string, number>;
  queue_status?: string;
}

interface MarketingStats {
  total_vendors: number;
  with_email: number;
  contacted: number;
  eligible_uncontacted: number;
  by_segment: Record<string, number>;
  sent_total: number;
  sent_today: number;
  queued_tomorrow?: number;
  approved_tomorrow?: number;
  tomorrow_date?: string;
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
  tomorrow_date?: string;
  pending_review_tomorrow?: number;
  approved_tomorrow?: number;
  health: string;
}

interface SentEmail {
  id: number;
  vendor_id: number;
  vendor_name?: string;
  vendor_email?: string;
  channel: string;
  message_draft?: string;
  subject?: string;
  status: string;
  sent_at?: string;
  created_at: string;
}

// ── Quality helpers ──────────────────────────────────────────────────────────

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

function qualityMissing(v: EmailDraft): number {
  if (typeof v.contactability_signals === "number") {
    return Math.max(0, 2 - v.contactability_signals);
  }
  return [!!v.email, !!v.phone, !!v.website, cityInApprovedList(v.city)].filter((x) => !x).length;
}

function qualityColor(v: EmailDraft): string {
  const missing = qualityMissing(v);
  if (missing === 0) return "bg-green-400";
  if (missing === 1) return "bg-yellow-400";
  return "bg-red-400";
}

function qualityLabel(v: EmailDraft): string {
  if (v.generic_inbox) return "Generic inbox";
  const missing = qualityMissing(v);
  if (missing === 0) return "Send-ready";
  if (missing === 1) return "Verify";
  return "Low quality";
}

function isRedQuality(v: EmailDraft): boolean {
  return qualityMissing(v) >= 2 || !!v.generic_inbox;
}

const MORNING_BATCH_TARGET = 30;

function formatBatchDate(dateStr?: string): string {
  if (!dateStr) return "tomorrow";
  const d = new Date(`${dateStr}T00:00:00`);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function normalizeWebsiteUrl(website?: string): string | null {
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

type Tab = "review" | "sent" | "analytics";

// Security note: The HTML preview in the Review tab uses dangerouslySetInnerHTML
// with server-generated HTML from cold_email.py templates ONLY — this is trusted
// content from our own backend template engine, never user-supplied HTML.
// This is the same pattern used in the original email-marketing.tsx component.

function HtmlPreview({ html }: { html: string }) {
  // Renders trusted server-generated HTML from cold_email.py templates
  return (
    <div
      className="bg-white text-black rounded-md p-4 text-sm min-h-[250px] max-h-[400px] overflow-auto border border-border"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export function EmailMarketingV2() {
  const [activeTab, setActiveTab] = useState<Tab>("review");
  const [stats, setStats] = useState<MarketingStats | null>(null);
  const [health, setHealth] = useState<CampaignHealth | null>(null);

  // Review tab state
  const [queue, setQueue] = useState<EmailDraft[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [totalEligible, setTotalEligible] = useState(0);
  const [totalReviewed, setTotalReviewed] = useState(0);
  const [batchScheduledDate, setBatchScheduledDate] = useState("");
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [previewHtml, setPreviewHtml] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Sent tab state
  const [sentEmails, setSentEmails] = useState<SentEmail[]>([]);
  const [sentLoading, setSentLoading] = useState(false);

  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  const showToast = (msg: string, type: "success" | "error") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const refreshStats = useCallback(async () => {
    try { setStats(await fetchApi<MarketingStats>("/api/email-marketing/stats")); } catch { /* non-critical */ }
    try { setHealth(await fetchApi<CampaignHealth>("/api/email-marketing/campaign-health")); } catch { /* non-critical */ }
  }, []);

  useEffect(() => { refreshStats(); }, [refreshStats]);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchApi<PreviewBatchResponse>(
        `/api/email-marketing/preview-batch?count=20&min_quality=40`
      );
      const batch = asArray<EmailDraft>(res?.batch);
      setQueue(batch);
      setTotalEligible(res?.total_eligible || 0);
      setTotalReviewed(0);
      setBatchScheduledDate(res?.scheduled_date || "");
      const firstGood = batch.findIndex((v) => !isRedQuality(v));
      const startIdx = firstGood >= 0 ? firstGood : 0;
      setCurrentIndex(startIdx);
      if (batch.length > 0) {
        setEditSubject(batch[startIdx].subject);
        setEditBody(batch[startIdx].plain_body);
      }
    } catch {
      showToast("Failed to load vendors", "error");
    }
    setLoading(false);
  }, []);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const loadSent = useCallback(async () => {
    setSentLoading(true);
    try {
      const res = await fetchApi<{ queue?: SentEmail[]; outreach?: SentEmail[] }>(
        "/api/email/queue?status=sent&limit=200"
      );
      const queueRows = asArray<SentEmail>(res?.queue);
      setSentEmails(queueRows.length ? queueRows : asArray<SentEmail>(res?.outreach));
    } catch {
      try {
        const res = await fetchApi<{ queue: SentEmail[] }>("/api/email-marketing/sent-history");
        setSentEmails(asArray(res?.queue));
      } catch { /* non-critical */ }
    }
    setSentLoading(false);
  }, []);

  useEffect(() => {
    if (activeTab === "sent") loadSent();
  }, [activeTab, loadSent]);

  const current = queue[currentIndex] || null;
  const currentWebsiteHref = normalizeWebsiteUrl(current?.website);
  const effectiveBatchDate = batchScheduledDate || health?.tomorrow_date || stats?.tomorrow_date || "";
  const approvedTomorrow = health?.approved_tomorrow ?? stats?.approved_tomorrow ?? 0;
  const pendingTomorrow = health?.pending_review_tomorrow ?? stats?.queued_tomorrow ?? 0;
  const remainingToTarget = Math.max(0, MORNING_BATCH_TARGET - approvedTomorrow);

  const findNextGood = (startIdx: number): number => {
    for (let i = startIdx; i < queue.length; i++) {
      if (!isRedQuality(queue[i])) return i;
    }
    return -1;
  };

  const goNext = () => {
    const goodIdx = findNextGood(currentIndex + 1);
    if (goodIdx >= 0) {
      setCurrentIndex(goodIdx);
      setEditSubject(queue[goodIdx].subject);
      setEditBody(queue[goodIdx].plain_body);
      setPreviewHtml(false);
      setTotalReviewed((r) => r + 1);
    } else {
      loadQueue();
      refreshStats();
    }
  };

  const handleSkip = async () => {
    if (!current) return;
    try {
      await postApi("/api/email-marketing/skip", {
        vendor_ids: [current.vendor_id],
        scheduled_date: current.scheduled_date || effectiveBatchDate || undefined,
        permanent: false,
      });
      showToast(`Removed ${current.vendor_name} from morning batch`, "success");
      goNext();
      refreshStats();
    } catch {
      showToast("Failed to remove from morning batch", "error");
    }
  };

  const handleAddToMorningBatch = async () => {
    if (!current) return;
    setSending(true);
    try {
      const scheduledDate = current.scheduled_date || effectiveBatchDate || undefined;
      const res = await postApi<{
        status: string;
        approved: number;
        queued_for_tomorrow: number;
        scheduled_date?: string;
        errors?: { vendor_id: number; error: string }[];
      }>(
        "/api/email-marketing/approve-batch",
        {
          emails: [{
            vendor_id: current.vendor_id,
            queue_id: current.queue_id,
            subject: editSubject,
            plain_body: editBody,
            html_body: current.html_body,
          }],
          scheduled_date: scheduledDate,
        },
        60000
      );
      if ((res.approved || 0) > 0 || (res.queued_for_tomorrow || 0) > 0) {
        const approvedFor = res.scheduled_date || scheduledDate;
        if (approvedFor) setBatchScheduledDate(approvedFor);
        showToast(
          `Added ${current.vendor_name} to ${formatBatchDate(approvedFor)} morning batch`,
          "success"
        );
        goNext();
        refreshStats();
      } else {
        showToast(res.errors?.[0]?.error || "Could not add to morning batch", "error");
      }
    } catch (e) {
      showToast(String(e), "error");
    }
    setSending(false);
  };

  const handleRegenerate = async () => {
    if (!current) return;
    setRegenerating(true);
    try {
      const res = await postApi<{
        ok: boolean;
        subject: string;
        plain_body: string;
        html_body: string;
      }>(
        "/api/email-marketing/regenerate",
        {
          vendor_id: current.vendor_id,
          queue_id: current.queue_id,
          step_number: current.step_number || 1,
          current_subject: editSubject,
          current_plain_body: editBody,
        },
      );
      if (!res?.ok) {
        showToast("Could not regenerate copy", "error");
        return;
      }
      setEditSubject(res.subject || editSubject);
      setEditBody(res.plain_body || editBody);
      setQueue((prev) =>
        prev.map((item, idx) =>
          idx === currentIndex
            ? {
                ...item,
                subject: res.subject || item.subject,
                plain_body: res.plain_body || item.plain_body,
                html_body: res.html_body || item.html_body,
              }
            : item
        )
      );
      showToast("Generated another draft variation", "success");
    } catch {
      showToast("Failed to regenerate draft", "error");
    }
    setRegenerating(false);
  };

  const handleDeleteLead = async () => {
    if (!current) return;
    const reason = window.prompt("Reason for deleting this lead from batch + database?", "Low-fit lead");
    if (reason === null) return;

    setDeleting(true);
    try {
      const res = await postApi<{
        ok: boolean;
        vendor_name?: string;
      }>("/api/email-marketing/reject-delete", {
        vendor_id: current.vendor_id,
        queue_id: current.queue_id,
        reason: (reason || "").trim(),
      });
      if (!res?.ok) {
        showToast("Could not delete lead", "error");
        return;
      }
      showToast(`Deleted ${res.vendor_name || current.vendor_name}`, "success");
      goNext();
      refreshStats();
    } catch {
      showToast("Failed to delete lead", "error");
    }
    setDeleting(false);
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: "review", label: "Morning Batch Review" },
    { id: "sent", label: "Sent" },
    { id: "analytics", label: "Analytics" },
  ];

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg ${
          toast.type === "success" ? "bg-success/20 text-success border border-success/30" : "bg-danger/20 text-danger border border-danger/30"
        }`}>
          {toast.msg}
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Email Marketing</h1>
          <p className="text-sm text-muted mt-1">
            Review and approve outreach for the next morning batch (no immediate send from this tab)
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-accent/15 text-accent">
            {approvedTomorrow}/{MORNING_BATCH_TARGET} approved for {formatBatchDate(effectiveBatchDate)}
          </div>
          {health && (
            <div className={`px-3 py-1.5 rounded-lg text-xs font-medium ${
              health.health === "good" ? "bg-success/10 text-success" : "bg-warning/10 text-warning"
            }`}>
              {health.daily_remaining}/{health.daily_limit} send-now capacity today
            </div>
          )}
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {[
            { label: "With Email", value: stats.with_email.toLocaleString(), cls: "" },
            { label: "Eligible", value: stats.eligible_uncontacted.toLocaleString(), cls: "text-accent" },
            { label: "Sent Total", value: stats.sent_total.toLocaleString(), cls: "" },
            { label: "Sent Today", value: stats.sent_today.toString(), cls: "" },
            { label: "Replied", value: `${stats.replied} (${stats.reply_rate_pct}%)`, cls: stats.replied > 0 ? "text-success" : "" },
            { label: "Bounced", value: `${stats.bounced} (${stats.bounce_rate_pct}%)`, cls: stats.bounced > 0 ? "text-danger" : "" },
          ].map((s) => (
            <div key={s.label} className="bg-card border border-border rounded-lg px-3 py-2.5">
              <p className="text-xs text-muted">{s.label}</p>
              <p className={`text-lg font-semibold mt-0.5 ${s.cls}`}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === t.id ? "border-accent text-accent" : "border-transparent text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === "review" && (
        <>
          {loading && (
            <div className="flex items-center justify-center py-20">
              <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
              <span className="ml-3 text-sm text-muted">Loading vendors...</span>
            </div>
          )}

          {!loading && !current && (
            <div className="text-center py-20">
              <p className="text-lg text-muted">
                No pending vendors for {formatBatchDate(effectiveBatchDate)} morning batch
              </p>
              <p className="text-sm text-muted mt-2">Everything in this batch has been reviewed or removed</p>
              <button onClick={loadQueue} className="mt-4 px-4 py-2 bg-accent/15 text-accent rounded-lg text-sm hover:bg-accent/25">
                Reload Queue
              </button>
            </div>
          )}

          {!loading && current && (
            <div className="max-w-3xl mx-auto space-y-4">
              <div className="text-center space-y-1">
                <p className="text-sm text-accent">
                  Morning batch date: {formatBatchDate(effectiveBatchDate)}
                </p>
                <p className="text-sm text-muted">
                  Vendor {currentIndex + 1} of {queue.length} loaded ({totalEligible.toLocaleString()} total eligible)
                  &middot; Reviewed: {totalReviewed}
                </p>
                <p className="text-xs text-muted">
                  Approved: {approvedTomorrow}/{MORNING_BATCH_TARGET} &middot; Remaining to target: {remainingToTarget}
                  &middot; Pending review: {pendingTomorrow}
                </p>
              </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                  <div className="rounded-lg border border-border bg-card px-3 py-2 text-muted">
                    <span className="font-semibold text-foreground">Immediate Send</span>: use Quick Email.
                  </div>
                  <div className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-accent">
                    <span className="font-semibold">Morning Batch Review</span>: Add To Morning Batch queues for {formatBatchDate(effectiveBatchDate)} morning.
                  </div>
                </div>

              <div className="bg-card border border-border rounded-xl p-5">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className={`w-3 h-3 rounded-full ${qualityColor(current)}`} title={qualityLabel(current)} />
                    <div>
                      <h3 className="text-lg font-bold">{current.vendor_name}</h3>
                      <p className="text-sm text-muted mt-0.5">
                        {current.category.replace(/_/g, " ")} {current.city ? `\u2022 ${current.city}` : ""}
                      </p>
                      <p className="text-xs mt-1 text-muted">
                        Greeting mode: {current.name_personalized ? "Verified first-name" : "Safe fallback (Hi there)"}{" "}
                        {typeof current.name_confidence === "number" ? `· confidence ${current.name_confidence}` : ""}
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
                    {currentWebsiteHref ? (
                      <a href={currentWebsiteHref}
                        target="_blank" rel="noopener noreferrer"
                        className="text-accent text-xs hover:underline truncate block">
                        {(current.website || "").replace(/^https?:\/\/(www\.)?/, "").slice(0, 30)}
                      </a>
                    ) : current.website ? (
                      <span className="text-xs text-warning">Invalid URL</span>
                    ) : <span className="text-xs text-muted">{"\u2014"}</span>}
                  </div>
                </div>
                {!!current.proof_snippet && (
                  <div className="mt-3 rounded-md border border-accent/30 bg-accent/10 px-3 py-2">
                    <p className="text-[11px] text-accent">Proof line: {current.proof_snippet}</p>
                  </div>
                )}
                {!!current.quality_reasons?.length && (
                  <div className="mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2">
                    <p className="text-[11px] text-warning">
                      Quality notes: {current.quality_reasons.join(" · ")}
                    </p>
                  </div>
                )}
              </div>

              <div className="bg-card border border-border rounded-xl p-5 space-y-4">
                <div className="rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-xs text-accent">
                  This page queues emails for {formatBatchDate(effectiveBatchDate)} morning. It does not send immediately.
                </div>
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
                    <div className="flex items-center gap-3">
                      <button
                        onClick={handleRegenerate}
                        disabled={regenerating}
                        className="text-xs text-accent hover:text-accent/80 disabled:opacity-50"
                      >
                        {regenerating ? "Regenerating..." : "Regenerate Body"}
                      </button>
                      <button onClick={() => setPreviewHtml(!previewHtml)} className="text-xs text-accent hover:text-accent/80">
                        {previewHtml ? "Edit" : "HTML Preview"}
                      </button>
                    </div>
                  </div>
                  {previewHtml ? (
                    <HtmlPreview html={current.html_body} />
                  ) : (
                    <textarea
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      className="w-full bg-background border border-border rounded-md px-3 py-2 text-sm resize-none h-64 focus:outline-none focus:border-accent font-mono text-xs leading-relaxed"
                    />
                  )}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <button onClick={handleSkip}
                    className="px-6 py-3 rounded-xl bg-card border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover transition-colors">
                    Skip For Morning Batch
                  </button>
                  <button
                    onClick={handleDeleteLead}
                    disabled={deleting}
                    className="px-5 py-3 rounded-xl border border-danger/40 text-danger text-sm hover:bg-danger/10 transition-colors disabled:opacity-50"
                  >
                    {deleting ? "Deleting..." : "Delete Lead"}
                  </button>
                </div>
                <div className="text-right">
                  <button onClick={handleAddToMorningBatch} disabled={sending || !current.email}
                    className="px-8 py-3 rounded-xl bg-accent text-white text-sm font-semibold hover:bg-accent/80 transition-colors disabled:opacity-50">
                    {sending ? "Adding..." : "Add To Morning Batch"}
                  </button>
                  <p className="text-xs text-muted mt-2">
                    {approvedTomorrow}/{MORNING_BATCH_TARGET} approved for {formatBatchDate(effectiveBatchDate)} morning
                  </p>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {activeTab === "sent" && (
        <div>
          {sentLoading ? (
            <div className="flex items-center justify-center py-20">
              <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
            </div>
          ) : sentEmails.length === 0 ? (
            <div className="text-center py-20">
              <p className="text-lg text-muted">No sent emails yet</p>
              <p className="text-sm text-muted mt-2">Start approving from the Morning Batch Review tab</p>
            </div>
          ) : (
            <div className="bg-card border border-border rounded-xl overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left px-4 py-3 text-xs text-muted font-medium">Vendor</th>
                    <th className="text-left px-4 py-3 text-xs text-muted font-medium">Email</th>
                    <th className="text-left px-4 py-3 text-xs text-muted font-medium">Status</th>
                    <th className="text-left px-4 py-3 text-xs text-muted font-medium">Sent At</th>
                  </tr>
                </thead>
                <tbody>
                  {sentEmails.map((e) => (
                    <tr key={e.id} className="border-b border-border last:border-0 hover:bg-card-hover transition-colors">
                      <td className="px-4 py-3 text-sm">{e.vendor_name || `Vendor #${e.vendor_id}`}</td>
                      <td className="px-4 py-3 text-xs font-mono text-muted">{e.vendor_email || "\u2014"}</td>
                      <td className="px-4 py-3">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                          e.status === "replied" ? "bg-success/20 text-success" :
                          e.status === "bounced" ? "bg-danger/20 text-danger" :
                          e.status === "sent" ? "bg-accent/20 text-accent" :
                          "bg-muted/20 text-muted"
                        }`}>
                          {e.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted">
                        {e.sent_at ? new Date(e.sent_at).toLocaleDateString("en-US", {
                          month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                        }) : "\u2014"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === "analytics" && stats && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-card rounded-xl border border-border p-5">
              <p className="text-xs text-muted mb-1">Total Sent</p>
              <p className="text-3xl font-bold">{stats.sent_total}</p>
              <p className="text-xs text-muted mt-2">{stats.sent_today} sent today</p>
            </div>
            <div className="bg-card rounded-xl border border-border p-5">
              <p className="text-xs text-muted mb-1">Reply Rate</p>
              <p className="text-3xl font-bold text-success">{stats.reply_rate_pct}%</p>
              <p className="text-xs text-muted mt-2">{stats.replied} replies received</p>
            </div>
            <div className="bg-card rounded-xl border border-border p-5">
              <p className="text-xs text-muted mb-1">Bounce Rate</p>
              <p className="text-3xl font-bold text-danger">{stats.bounce_rate_pct}%</p>
              <p className="text-xs text-muted mt-2">{stats.bounced} bounced</p>
            </div>
          </div>

          {stats.by_segment && Object.keys(stats.by_segment).length > 0 && (
            <div className="bg-card rounded-xl border border-border p-5">
              <h3 className="text-sm font-medium mb-4">Vendors by Category</h3>
              <div className="space-y-2">
                {Object.entries(stats.by_segment)
                  .sort(([, a], [, b]) => b - a)
                  .map(([segment, count]) => {
                    const pct = stats.with_email > 0 ? (count / stats.with_email) * 100 : 0;
                    return (
                      <div key={segment} className="flex items-center gap-3">
                        <span className="text-xs text-muted w-32 capitalize truncate shrink-0">
                          {segment.replace(/_/g, " ")}
                        </span>
                        <div className="flex-1 bg-border rounded-full h-4 overflow-hidden">
                          <div className="h-full bg-accent/60 rounded-full" style={{ width: `${Math.max(pct, 2)}%` }} />
                        </div>
                        <span className="text-xs font-medium w-10 text-right">{count}</span>
                      </div>
                    );
                  })}
              </div>
            </div>
          )}

          <div className="bg-card rounded-xl border border-border p-5">
            <h3 className="text-sm font-medium mb-4">Outreach Funnel</h3>
            <div className="flex items-end gap-2 h-40">
              {[
                { label: "With Email", value: stats.with_email, color: "bg-muted" },
                { label: "Contacted", value: stats.contacted, color: "bg-accent" },
                { label: "Replied", value: stats.replied, color: "bg-success" },
              ].map((step) => {
                const maxVal = Math.max(stats.with_email, 1);
                const height = Math.max((step.value / maxVal) * 100, 5);
                return (
                  <div key={step.label} className="flex-1 flex flex-col items-center gap-2">
                    <span className="text-sm font-bold">{step.value}</span>
                    <div className="w-full rounded-t-lg transition-all" style={{ height: `${height}%` }}>
                      <div className={`w-full h-full rounded-t-lg ${step.color}`} />
                    </div>
                    <span className="text-xs text-muted text-center">{step.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
