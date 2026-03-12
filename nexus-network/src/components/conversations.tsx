"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { fetchApi, postApi, putApi, asArray, useSSE } from "@/lib/api";

interface ConversationSummary {
  lead_id: number;
  first_name: string;
  last_name: string;
  full_name: string;
  display_name?: string;
  phone: string;
  email: string;
  booking_status: string;
  business_name?: string;
  source?: string;
  source_detail?: string;
  source_group_name?: string;
  last_message: string;
  last_message_ts: string;
  last_channel: string;
  last_direction: string;
  message_count: number;
  sms_count?: number;
  email_count?: number;
  unread_count?: number;
  cluster_lead_ids?: number[];
}

interface Message {
  id: number;
  lead_id: number;
  channel: string;
  direction: string;
  content: string;
  subject?: string;
  ts: string;
  status?: string;
}

interface LeadDetail {
  id: number;
  name: string;
  full_name?: string;
  phone: string;
  email: string;
  event_type: string;
  event_date: string;
  event_city: string;
  guest_count: number;
  status: string;
  source: string;
  notes: string;
  created_at: string;
  job_title?: string;
  venue_location?: string;
  website?: string;
  source_detail?: string;
  source_group_name?: string;
  is_vendor?: number | boolean;
  vendor_category?: string;
  is_venue?: number | boolean;
  venue_name?: string;
  joined_referral_program?: number | boolean;
  referral_partner_type?: string;
  referrals_given_count?: number;
  referrals_booked_count?: number;
  referral_payout_total?: number;
  lead_temperature_override?: string;
}

interface LeadProfile {
  lead_added_at: string;
  where_found: string;
  source_classification: string;
  facebook_group_name: string;
  job_title: string;
  venue_location: string;
  website: string;
  did_reach_out: {
    call: boolean;
    sms: boolean;
    email: boolean;
    facebook_dm: boolean;
    instagram_dm: boolean;
  };
  reached_out_methods: string[];
  did_reply: boolean;
  reply_platforms: string[];
  latest_reply_text: string;
  latest_reply_at: string;
  replied_to_primary_message: boolean;
  warm_or_cold: string;
  follow_up: {
    auto_follow_up_blocked_due_to_reply: boolean;
    sent_48h: boolean;
    due_48h: boolean;
    reply_after_follow_up: boolean;
    reply_after_follow_up_text: string;
  };
  classification: {
    is_vendor: boolean;
    vendor_category: string;
    is_venue: boolean;
    venue_name: string;
  };
  referral: {
    joined_referral_program: boolean;
    partner_type: string;
    referrals_given: number;
    referrals_booked: number;
    payout_total: number;
  };
  metrics: {
    outbound_messages: number;
    inbound_messages: number;
  };
}

interface ProfileDraft {
  job_title: string;
  venue_location: string;
  website: string;
  source_detail: string;
  source_group_name: string;
  is_vendor: boolean;
  vendor_category: string;
  is_venue: boolean;
  venue_name: string;
  joined_referral_program: boolean;
  referral_partner_type: string;
  referrals_given_count: string;
  referrals_booked_count: string;
  referral_payout_total: string;
  lead_temperature_override: string;
}

const CHANNEL_ICONS: Record<string, string> = {
  sms: "\ud83d\udcf1",
  email: "\ud83d\udce7",
  messenger: "\ud83d\udcac",
  call: "\ud83d\udcde",
  telegram: "\u2709\ufe0f",
  note: "\ud83d\udcdd",
};

const STATUS_COLORS: Record<string, string> = {
  new: "bg-accent",
  new_lead: "bg-accent",
  contacted: "bg-blue-400",
  quoted: "bg-warning",
  booked: "bg-success",
  lost: "bg-danger",
  replied: "bg-success",
};

function timeAgo(ts: string): string {
  if (!ts) return "";
  const now = Date.now();
  let ms: number;
  if (/^\d{10}$/.test(ts)) ms = Number(ts) * 1000;
  else if (/^\d{13}$/.test(ts)) ms = Number(ts);
  else ms = new Date(ts).getTime();
  const diff = now - ms;
  if (diff < 60000) return "now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`;
  return `${Math.floor(diff / 86400000)}d`;
}

function conversationName(c?: Partial<ConversationSummary> | null): string {
  if (!c) return "Unknown";
  const direct = (c.display_name || c.full_name || "").trim();
  if (direct) return direct;
  const combo = `${c.first_name || ""} ${c.last_name || ""}`.trim();
  if (combo) return combo;
  if ((c.email || "").includes("@")) return (c.email || "").split("@")[0] || "Unknown";
  if ((c.phone || "").trim()) return c.phone || "Unknown";
  return "Unknown";
}

function sourceTag(source?: string, detail?: string): { label: string; cls: string } | null {
  const src = (source || "").toLowerCase();
  const det = (detail || "").toLowerCase();
  if (src.includes("facebook") || det.includes("facebook") || det.includes("lead form")) {
    return { label: "FB AD", cls: "bg-blue-500/20 text-blue-300 border-blue-400/40" };
  }
  if (src.includes("google_voice") || src.includes("sms")) {
    return { label: "SMS", cls: "bg-cyan-500/20 text-cyan-300 border-cyan-400/40" };
  }
  if (src.includes("email")) {
    return { label: "EMAIL", cls: "bg-violet-500/20 text-violet-300 border-violet-400/40" };
  }
  if (src.includes("website")) {
    return { label: "WEB", cls: "bg-emerald-500/20 text-emerald-300 border-emerald-400/40" };
  }
  if (src.includes("referral")) {
    return { label: "REF", cls: "bg-amber-500/20 text-amber-300 border-amber-400/40" };
  }
  return null;
}

function ContactAvatar({ name }: { name: string }) {
  const initial = (name || "?")[0].toUpperCase();
  return (
    <div className="w-9 h-9 rounded-full bg-accent/20 text-accent flex items-center justify-center text-sm font-bold shrink-0">
      {initial}
    </div>
  );
}

export function Conversations() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [leadDetail, setLeadDetail] = useState<LeadDetail | null>(null);
  const [leadProfile, setLeadProfile] = useState<LeadProfile | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [msgsLoading, setMsgsLoading] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [addingNote, setAddingNote] = useState(false);
  const [composeChannel, setComposeChannel] = useState<"sms" | "email">("sms");
  const [threadChannel, setThreadChannel] = useState<"all" | "sms" | "email">("all");
  const [smsDraft, setSmsDraft] = useState("");
  const [emailSubject, setEmailSubject] = useState("Re: Zoar Bathroom Rentals");
  const [emailDraft, setEmailDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loggingCall, setLoggingCall] = useState(false);
  const [sendError, setSendError] = useState("");
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>({
    job_title: "",
    venue_location: "",
    website: "",
    source_detail: "",
    source_group_name: "",
    is_vendor: false,
    vendor_category: "",
    is_venue: false,
    venue_name: "",
    joined_referral_program: false,
    referral_partner_type: "",
    referrals_given_count: "0",
    referrals_booked_count: "0",
    referral_payout_total: "0",
    lead_temperature_override: "",
  });
  const [showDetail, setShowDetail] = useState(true);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const selectedIdRef = useRef<number | null>(null);

  const loadConversations = useCallback(async () => {
    try {
      const res = await fetchApi<{ conversations: ConversationSummary[] }>("/api/crm/conversations");
      setConversations(asArray(res?.conversations));
    } catch {
      // try fallback
      try {
        const res = await fetchApi<{ leads: LeadDetail[] }>("/api/crm/leads");
        const convos: ConversationSummary[] = asArray<LeadDetail>(res?.leads).map((l) => ({
          lead_id: l.id,
          first_name: (l.full_name || l.name || "").split(" ")[0],
          last_name: (l.full_name || l.name || "").split(" ").slice(1).join(" "),
          full_name: l.full_name || l.name || "",
          phone: l.phone || "",
          email: l.email || "",
          booking_status: l.status || "new",
          last_message: "",
          last_message_ts: l.created_at || "",
          last_channel: "",
          last_direction: "",
          message_count: 0,
        }));
        setConversations(convos);
      } catch { /* */ }
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadConversations();
    const iv = setInterval(loadConversations, 30000);
    return () => clearInterval(iv);
  }, [loadConversations]);

  const markConversationRead = useCallback(async (leadId: number) => {
    try {
      await postApi(`/api/crm/conversations/${leadId}/read`, {});
    } catch {
      // non-critical
    }
  }, []);

  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    const close = useSSE<any>("/api/events", (ev) => {
      const type = String(ev?.type || "");
      if (type === "ping") return;

      // Refresh conversation UI immediately on inbound/outbound messaging events.
      if (type !== "lead_event" && type !== "msg_sent" && type !== "msg_received" && type !== "ghl_event") {
        return;
      }

      loadConversations();
      const activeLeadId = selectedIdRef.current;
      if (activeLeadId) {
        Promise.all([
          fetchApi<{ messages: Message[] }>(`/api/crm/conversations/${activeLeadId}/messages`).catch(() => ({ messages: [] })),
          fetchApi<{ lead: LeadDetail; profile?: LeadProfile }>(`/api/crm/leads/${activeLeadId}`).catch(() => ({ lead: null as any, profile: undefined as LeadProfile | undefined })),
        ]).then(([msgsRes, leadRes]) => {
          setMessages(asArray(msgsRes?.messages));
          setLeadDetail(leadRes?.lead || null);
          setLeadProfile(leadRes?.profile || null);
          markConversationRead(activeLeadId);
        }).catch(() => { /* non-critical */ });
      }
    });
    return close;
  }, [loadConversations, markConversationRead]);

  const selectConversation = useCallback(async (leadId: number) => {
    setSelectedId(leadId);
    setThreadChannel("all");
    setMsgsLoading(true);
    try {
      const [msgsRes, leadRes] = await Promise.all([
        fetchApi<{ messages: Message[] }>(`/api/crm/conversations/${leadId}/messages`).catch(() => ({ messages: [] })),
        fetchApi<{ lead: LeadDetail; profile?: LeadProfile }>(`/api/crm/leads/${leadId}`).catch(() => ({ lead: null as any, profile: undefined as LeadProfile | undefined })),
      ]);
      setMessages(asArray(msgsRes?.messages));
      setLeadDetail(leadRes.lead || null);
      setLeadProfile(leadRes.profile || null);
      markConversationRead(leadId);
    } catch { /* */ }
    setMsgsLoading(false);
  }, [markConversationRead]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleAddNote = async () => {
    if (!selectedId || !noteText.trim()) return;
    setAddingNote(true);
    try {
      await postApi(`/api/crm/leads/${selectedId}/note`, { note: noteText.trim(), author: "Kai" });
      setNoteText("");
      selectConversation(selectedId);
    } catch { /* */ }
    setAddingNote(false);
  };

  const selectedConvo = conversations.find((c) => c.lead_id === selectedId);

  useEffect(() => {
    if (!leadDetail) return;
    setProfileDraft({
      job_title: leadDetail.job_title || "",
      venue_location: leadDetail.venue_location || "",
      website: leadDetail.website || "",
      source_detail: leadDetail.source_detail || "",
      source_group_name: leadDetail.source_group_name || "",
      is_vendor: Boolean(leadDetail.is_vendor),
      vendor_category: leadDetail.vendor_category || "",
      is_venue: Boolean(leadDetail.is_venue),
      venue_name: leadDetail.venue_name || "",
      joined_referral_program: Boolean(leadDetail.joined_referral_program),
      referral_partner_type: leadDetail.referral_partner_type || "",
      referrals_given_count: String(leadDetail.referrals_given_count ?? 0),
      referrals_booked_count: String(leadDetail.referrals_booked_count ?? 0),
      referral_payout_total: String(leadDetail.referral_payout_total ?? 0),
      lead_temperature_override: leadDetail.lead_temperature_override || "",
    });
  }, [leadDetail?.id]);

  useEffect(() => {
    if (!selectedConvo) return;
    if (selectedConvo.phone) {
      setComposeChannel(selectedConvo.last_channel === "email" && selectedConvo.email ? "email" : "sms");
      return;
    }
    if (selectedConvo.email) setComposeChannel("email");
  }, [selectedConvo?.lead_id]);

  const handleSendMessage = async () => {
    if (!selectedId) return;
    setSendError("");
    setSending(true);
    try {
      if (composeChannel === "sms") {
        const msg = smsDraft.trim();
        if (!msg) throw new Error("Enter an SMS message.");
        await postApi(`/api/crm/leads/${selectedId}/sms`, { message: msg });
        setSmsDraft("");
      } else {
        const msg = emailDraft.trim();
        const subj = emailSubject.trim() || "Re: Zoar Bathroom Rentals";
        if (!msg) throw new Error("Enter an email message.");
        await postApi(`/api/crm/leads/${selectedId}/email`, { subject: subj, message: msg });
        setEmailDraft("");
      }
      await selectConversation(selectedId);
      await loadConversations();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Send failed");
    } finally {
      setSending(false);
    }
  };

  const handleSaveProfile = async () => {
    if (!selectedId) return;
    setProfileError("");
    setProfileSaving(true);
    try {
      const payload = {
        job_title: profileDraft.job_title.trim(),
        venue_location: profileDraft.venue_location.trim(),
        website: profileDraft.website.trim(),
        source_detail: profileDraft.source_detail.trim(),
        source_group_name: profileDraft.source_group_name.trim(),
        is_vendor: profileDraft.is_vendor ? 1 : 0,
        vendor_category: profileDraft.vendor_category.trim(),
        is_venue: profileDraft.is_venue ? 1 : 0,
        venue_name: profileDraft.venue_name.trim(),
        joined_referral_program: profileDraft.joined_referral_program ? 1 : 0,
        referral_partner_type: profileDraft.referral_partner_type.trim(),
        referrals_given_count: Number(profileDraft.referrals_given_count || "0") || 0,
        referrals_booked_count: Number(profileDraft.referrals_booked_count || "0") || 0,
        referral_payout_total: Number(profileDraft.referral_payout_total || "0") || 0,
        lead_temperature_override: profileDraft.lead_temperature_override.trim(),
      };
      const res = await putApi<{ lead: LeadDetail; profile?: LeadProfile }>(`/api/crm/leads/${selectedId}`, payload);
      setLeadDetail(res.lead || null);
      setLeadProfile(res.profile || null);
      await loadConversations();
    } catch (e) {
      setProfileError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setProfileSaving(false);
    }
  };

  const handleLogCallAttempt = async () => {
    if (!selectedId) return;
    if (!selectedConvo?.phone) {
      setSendError("No phone number on this lead.");
      return;
    }
    setSendError("");
    setLoggingCall(true);
    try {
      await postApi(`/api/crm/leads/${selectedId}/call-attempt`, {
        outcome: "no_answer",
        note: "Manual call attempt logged from Conversations",
        actor: "Kai",
      });
      await selectConversation(selectedId);
      await loadConversations();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Could not log call attempt");
    } finally {
      setLoggingCall(false);
    }
  };

  const filtered = conversations.filter((c) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      conversationName(c).toLowerCase().includes(q) ||
      (c.phone || "").includes(q) ||
      (c.email || "").toLowerCase().includes(q)
    );
  });

  const threadMessages = messages.filter((m) => {
    if (threadChannel === "all") return true;
    return (m.channel || "").toLowerCase() === threadChannel;
  });
  const smsMsgCount = messages.filter((m) => (m.channel || "").toLowerCase() === "sms").length;
  const emailMsgCount = messages.filter((m) => (m.channel || "").toLowerCase() === "email").length;
  const selectedName = conversationName(selectedConvo);
  const selectedTag = sourceTag(selectedConvo?.source, selectedConvo?.source_detail);

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Left: Contact List */}
      <div className="w-[280px] shrink-0 border-r border-border flex flex-col bg-sidebar">
        <div className="p-3 border-b border-border">
          <input
            type="text"
            placeholder="Search contacts..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center py-10">
              <div className="animate-spin w-5 h-5 border-2 border-accent border-t-transparent rounded-full" />
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-10 text-sm text-muted">
              {searchQuery ? "No matches" : "No conversations yet"}
            </div>
          ) : (
            filtered.map((c) => {
              const cName = conversationName(c);
              const cTag = sourceTag(c.source, c.source_detail);
              const unread = Number(c.unread_count || 0);
              return (
                <button
                  key={c.lead_id}
                  onClick={() => selectConversation(c.lead_id)}
                  className={`w-full text-left px-3 py-3 border-b border-border transition-colors flex items-start gap-3 ${
                    selectedId === c.lead_id ? "bg-accent/10" : "hover:bg-card-hover"
                  }`}
                >
                  <ContactAvatar name={cName} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium truncate">{cName}</span>
                      <div className="flex items-center gap-1 shrink-0 ml-2">
                        {unread > 0 && (
                          <span className="inline-flex min-w-5 h-5 px-1.5 items-center justify-center rounded-full bg-accent text-white text-[10px] font-semibold">
                            {Math.min(unread, 99)}
                          </span>
                        )}
                        <span className="text-[10px] text-muted">{timeAgo(c.last_message_ts)}</span>
                      </div>
                    </div>
                    <p className="text-xs text-muted truncate mt-0.5">
                      {c.last_message || c.email || c.phone || "No messages"}
                    </p>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <span className={`inline-block w-1.5 h-1.5 rounded-full ${STATUS_COLORS[c.booking_status] || "bg-muted"}`} />
                      <span className="text-[10px] text-muted capitalize">{c.booking_status?.replace(/_/g, " ")}</span>
                      {c.message_count > 0 && (
                        <span className="text-[10px] text-muted">&middot; {c.message_count} msgs</span>
                      )}
                      {(Number(c.sms_count || 0) > 0 || Number(c.email_count || 0) > 0) && (
                        <span className="text-[10px] text-muted">
                          ({Number(c.sms_count || 0)} SMS / {Number(c.email_count || 0)} Email)
                        </span>
                      )}
                      {cTag && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${cTag.cls}`}>
                          {cTag.label}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* Center: Messages */}
      <div className="flex-1 flex flex-col min-w-0">
        {!selectedId ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <p className="text-lg text-muted">Select a conversation</p>
              <p className="text-sm text-muted mt-1">{conversations.length} contacts</p>
            </div>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="px-4 py-3 border-b border-border flex items-center justify-between bg-card">
              <div className="flex items-center gap-3">
                <ContactAvatar name={selectedName} />
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold">{selectedName}</p>
                    {selectedTag && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border ${selectedTag.cls}`}>
                        {selectedTag.label}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted">
                    {selectedConvo?.phone || ""} {selectedConvo?.phone && selectedConvo?.email ? " \u2022 " : ""} {selectedConvo?.email || ""}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowDetail(!showDetail)}
                className="text-xs text-muted hover:text-foreground px-2 py-1 rounded hover:bg-card-hover transition-colors"
              >
                {showDetail ? "Hide Details" : "Show Details"}
              </button>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              <div className="flex items-center gap-2">
                {[
                  { key: "all", label: `All (${messages.length})` },
                  { key: "sms", label: `SMS (${smsMsgCount})` },
                  { key: "email", label: `Email (${emailMsgCount})` },
                ].map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setThreadChannel(tab.key as "all" | "sms" | "email")}
                    className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
                      threadChannel === tab.key
                        ? "bg-accent text-white border-accent"
                        : "bg-background border-border text-muted hover:text-foreground"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              {msgsLoading ? (
                <div className="flex items-center justify-center py-10">
                  <div className="animate-spin w-5 h-5 border-2 border-accent border-t-transparent rounded-full" />
                </div>
              ) : threadMessages.length === 0 ? (
                <div className="text-center py-10 text-sm text-muted">
                  {threadChannel === "all" ? "No messages yet" : `No ${threadChannel.toUpperCase()} messages in this conversation`}
                </div>
              ) : (
                threadMessages.map((m) => {
                  const isOutbound = m.direction === "outbound";
                  return (
                    <div key={m.id} className={`flex ${isOutbound ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[70%] rounded-xl px-4 py-2.5 ${
                        isOutbound ? "bg-accent/15 text-foreground" : "bg-card border border-border"
                      }`}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs">{CHANNEL_ICONS[m.channel] || "\ud83d\udce8"}</span>
                          <span className="text-[10px] text-muted capitalize">{m.channel}</span>
                          <span className="text-[10px] text-muted">{isOutbound ? "\u2192 sent" : "\u2190 received"}</span>
                        </div>
                        {m.subject && (
                          <p className="text-xs text-muted mb-1">Subject: {m.subject}</p>
                        )}
                        <p className="text-sm whitespace-pre-wrap">{m.content}</p>
                        <p className="text-[10px] text-muted mt-1.5 text-right">
                          {m.ts ? new Date(m.ts).toLocaleString("en-US", {
                            month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
                          }) : ""}
                        </p>
                      </div>
                    </div>
                  );
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Composer + Add Note */}
            <div className="px-4 py-3 border-t border-border space-y-3">
              <div className="border border-border rounded-lg p-3 space-y-2">
                <p className="text-[10px] text-muted uppercase tracking-wider">Compose (sends immediately)</p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setComposeChannel("sms")}
                    disabled={!selectedConvo?.phone}
                    className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
                      composeChannel === "sms"
                        ? "bg-accent text-white border-accent"
                        : "bg-background border-border text-muted hover:text-foreground"
                    } disabled:opacity-40`}
                  >
                    SMS
                  </button>
                  <button
                    onClick={() => setComposeChannel("email")}
                    disabled={!selectedConvo?.email}
                    className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
                      composeChannel === "email"
                        ? "bg-accent text-white border-accent"
                        : "bg-background border-border text-muted hover:text-foreground"
                    } disabled:opacity-40`}
                  >
                    Email
                  </button>
                  <button
                    onClick={handleLogCallAttempt}
                    disabled={loggingCall || !selectedConvo?.phone}
                    className="px-3 py-1.5 text-xs rounded-md border border-border bg-background text-muted hover:text-foreground disabled:opacity-40"
                  >
                    {loggingCall ? "Logging Call..." : "Log Call Attempt"}
                  </button>
                </div>

                {composeChannel === "sms" ? (
                  <textarea
                    placeholder={selectedConvo?.phone ? "Reply via SMS..." : "No phone number on this lead"}
                    value={smsDraft}
                    onChange={(e) => setSmsDraft(e.target.value)}
                    rows={3}
                    className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent resize-none"
                  />
                ) : (
                  <div className="space-y-2">
                    <input
                      type="text"
                      placeholder="Email subject"
                      value={emailSubject}
                      onChange={(e) => setEmailSubject(e.target.value)}
                      className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                    />
                    <textarea
                      placeholder={selectedConvo?.email ? "Reply via email..." : "No email on this lead"}
                      value={emailDraft}
                      onChange={(e) => setEmailDraft(e.target.value)}
                      rows={4}
                      className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent resize-none"
                    />
                  </div>
                )}

                {sendError && <p className="text-xs text-danger">{sendError}</p>}

                <div className="flex justify-end">
                  <button
                    onClick={handleSendMessage}
                    disabled={sending || (composeChannel === "sms" ? !smsDraft.trim() : !emailDraft.trim())}
                    className="px-4 py-2 bg-accent text-white text-sm rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50"
                  >
                    {sending ? "Sending..." : composeChannel === "sms" ? "Send SMS Immediately" : "Send Email Immediately"}
                  </button>
                </div>
              </div>

              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Add a note (phone call, meeting, etc.)"
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddNote()}
                  className="flex-1 bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                />
                <button
                  onClick={handleAddNote}
                  disabled={addingNote || !noteText.trim()}
                  className="px-4 py-2 bg-accent text-white text-sm rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50"
                >
                  {addingNote ? "..." : "Note"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Right: Contact Details */}
      {selectedId && showDetail && leadDetail && (
        <div className="w-[360px] shrink-0 border-l border-border overflow-y-auto bg-sidebar p-4 space-y-4">
          <h3 className="text-sm font-semibold">Contact Details</h3>

          <div className="space-y-3">
            {[
              { label: "Name", value: leadDetail.full_name || leadDetail.name },
              { label: "Phone", value: leadDetail.phone },
              { label: "Email", value: leadDetail.email },
              { label: "Job Title", value: leadDetail.job_title },
              { label: "Event Type", value: leadDetail.event_type },
              { label: "Event Date", value: leadDetail.event_date ? new Date(leadDetail.event_date).toLocaleDateString() : "—" },
              { label: "Event City", value: leadDetail.event_city },
              { label: "Venue Location", value: leadDetail.venue_location },
              { label: "Website", value: leadDetail.website },
              { label: "Guest Count", value: leadDetail.guest_count || "—" },
              { label: "Source", value: leadDetail.source },
              { label: "Source Detail", value: leadDetail.source_detail },
              { label: "FB Group", value: leadDetail.source_group_name },
            ].map((field) => (
              <div key={field.label}>
                <p className="text-[10px] text-muted uppercase tracking-wider">{field.label}</p>
                <p className="text-sm mt-0.5">{field.value || "—"}</p>
              </div>
            ))}
          </div>

          <div>
            <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Status</p>
            <span className={`inline-block px-2.5 py-1 rounded-full text-xs font-medium text-white ${
              STATUS_COLORS[leadDetail.status] || "bg-muted"
            }`}>
              {leadDetail.status?.replace(/_/g, " ")}
            </span>
          </div>

          {leadDetail.notes && (
            <div>
              <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Notes</p>
              <p className="text-xs text-muted whitespace-pre-wrap">{leadDetail.notes}</p>
            </div>
          )}

          <div>
            <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Created</p>
            <p className="text-xs text-muted">
              {leadDetail.created_at ? new Date(leadDetail.created_at).toLocaleDateString() : "—"}
            </p>
          </div>

          {leadProfile && (
            <div className="border border-border rounded-lg p-3 space-y-2">
              <p className="text-[10px] text-muted uppercase tracking-wider">Lead Intelligence</p>
              <p className="text-xs">
                <span className="text-muted">Found As:</span> {leadProfile.source_classification || "unknown"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Reached Out:</span>{" "}
                {leadProfile.reached_out_methods.length ? leadProfile.reached_out_methods.join(", ") : "Not yet"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Did Reply:</span> {leadProfile.did_reply ? "Yes" : "No"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Reply Platform:</span>{" "}
                {leadProfile.reply_platforms.length ? leadProfile.reply_platforms.join(", ") : "—"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Replied To Primary:</span>{" "}
                {leadProfile.replied_to_primary_message ? "Yes" : "No"}
              </p>
              <p className="text-xs">
                <span className="text-muted">48h Follow-up Sent:</span> {leadProfile.follow_up.sent_48h ? "Yes" : "No"}
              </p>
              <p className="text-xs">
                <span className="text-muted">48h Follow-up Due:</span> {leadProfile.follow_up.due_48h ? "Yes" : "No"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Auto Follow-up Blocked:</span>{" "}
                {leadProfile.follow_up.auto_follow_up_blocked_due_to_reply ? "Yes" : "No"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Warm/Cold:</span>{" "}
                <span className={leadProfile.warm_or_cold === "warm" ? "text-success" : leadProfile.warm_or_cold === "cold" ? "text-danger" : ""}>
                  {leadProfile.warm_or_cold}
                </span>
              </p>
              <p className="text-xs">
                <span className="text-muted">Referral Program:</span>{" "}
                {leadProfile.referral.joined_referral_program ? "Joined" : "Not joined"}
              </p>
              <p className="text-xs">
                <span className="text-muted">Referrals:</span>{" "}
                {leadProfile.referral.referrals_given} given / {leadProfile.referral.referrals_booked} booked
              </p>
              <p className="text-xs">
                <span className="text-muted">Payout:</span> ${leadProfile.referral.payout_total.toFixed(2)}
              </p>
              {leadProfile.latest_reply_text && (
                <div>
                  <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Latest Reply</p>
                  <p className="text-xs text-muted whitespace-pre-wrap">{leadProfile.latest_reply_text}</p>
                </div>
              )}
            </div>
          )}

          <div className="border border-border rounded-lg p-3 space-y-2">
            <p className="text-[10px] text-muted uppercase tracking-wider">Profile Editor</p>

            <input
              type="text"
              placeholder="Job title"
              value={profileDraft.job_title}
              onChange={(e) => setProfileDraft((d) => ({ ...d, job_title: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />
            <input
              type="text"
              placeholder="Venue location"
              value={profileDraft.venue_location}
              onChange={(e) => setProfileDraft((d) => ({ ...d, venue_location: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />
            <input
              type="text"
              placeholder="Website"
              value={profileDraft.website}
              onChange={(e) => setProfileDraft((d) => ({ ...d, website: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />
            <input
              type="text"
              placeholder="Where found (exact)"
              value={profileDraft.source_detail}
              onChange={(e) => setProfileDraft((d) => ({ ...d, source_detail: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />
            <input
              type="text"
              placeholder="Facebook group name"
              value={profileDraft.source_group_name}
              onChange={(e) => setProfileDraft((d) => ({ ...d, source_group_name: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />

            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-muted flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={profileDraft.is_vendor}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, is_vendor: e.target.checked }))}
                />
                Is Vendor
              </label>
              <label className="text-xs text-muted flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={profileDraft.is_venue}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, is_venue: e.target.checked }))}
                />
                Is Venue
              </label>
            </div>

            <input
              type="text"
              placeholder="Vendor category (catering, planner, etc.)"
              value={profileDraft.vendor_category}
              onChange={(e) => setProfileDraft((d) => ({ ...d, vendor_category: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />
            <input
              type="text"
              placeholder="Venue name"
              value={profileDraft.venue_name}
              onChange={(e) => setProfileDraft((d) => ({ ...d, venue_name: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            />

            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-muted flex items-center gap-2 col-span-2">
                <input
                  type="checkbox"
                  checked={profileDraft.joined_referral_program}
                  onChange={(e) => setProfileDraft((d) => ({ ...d, joined_referral_program: e.target.checked }))}
                />
                Joined Referral Program
              </label>
              <input
                type="text"
                placeholder="Partner type (vendor/venue)"
                value={profileDraft.referral_partner_type}
                onChange={(e) => setProfileDraft((d) => ({ ...d, referral_partner_type: e.target.value }))}
                className="col-span-2 w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
              />
              <input
                type="number"
                placeholder="Referrals given"
                value={profileDraft.referrals_given_count}
                onChange={(e) => setProfileDraft((d) => ({ ...d, referrals_given_count: e.target.value }))}
                className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
              />
              <input
                type="number"
                placeholder="Referrals booked"
                value={profileDraft.referrals_booked_count}
                onChange={(e) => setProfileDraft((d) => ({ ...d, referrals_booked_count: e.target.value }))}
                className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
              />
              <input
                type="number"
                step="0.01"
                placeholder="Referral payout total"
                value={profileDraft.referral_payout_total}
                onChange={(e) => setProfileDraft((d) => ({ ...d, referral_payout_total: e.target.value }))}
                className="col-span-2 w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
              />
            </div>

            <select
              value={profileDraft.lead_temperature_override}
              onChange={(e) => setProfileDraft((d) => ({ ...d, lead_temperature_override: e.target.value }))}
              className="w-full bg-background border border-border rounded px-2.5 py-1.5 text-xs focus:outline-none focus:border-accent"
            >
              <option value="">Auto warm/cold</option>
              <option value="warm">Warm</option>
              <option value="cold">Cold</option>
            </select>

            {profileError && <p className="text-xs text-danger">{profileError}</p>}

            <button
              onClick={handleSaveProfile}
              disabled={profileSaving}
              className="w-full px-3 py-2 bg-accent text-white text-xs rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50"
            >
              {profileSaving ? "Saving..." : "Save Lead Profile"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
