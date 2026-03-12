"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, asArray, type Lead, type Booking } from "@/lib/api";

interface CRMDashboard {
  total_leads: number;
  total_value: number;
  won_revenue: number;
  won_count: number;
  conversion_pct: number;
  stage_counts: Record<string, number>;
  funnel?: { stage: string; count: number }[];
}

interface AgentInfo {
  name: string;
  status: string;
  last_run: string;
  tasks_completed: number;
  errors: number;
}

interface MarketingStats {
  total_vendors: number;
  with_email: number;
  contacted: number;
  eligible_uncontacted: number;
  sent_total: number;
  sent_today: number;
  replied: number;
  bounced: number;
  reply_rate_pct: number;
  bounce_rate_pct: number;
}

function StatCard({
  label,
  value,
  sub,
  color = "text-foreground",
  size = "normal",
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
  size?: "normal" | "large";
}) {
  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className={`${size === "large" ? "text-3xl" : "text-2xl"} font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-muted mt-1">{sub}</p>}
    </div>
  );
}

function FunnelChart({ stages }: { stages: { stage: string; count: number }[] }) {
  const max = Math.max(...stages.map((s) => s.count), 1);
  const colors: Record<string, string> = {
    new: "bg-accent",
    contacted: "bg-blue-400",
    quoted: "bg-warning",
    booked: "bg-success",
    lost: "bg-danger",
  };
  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <h3 className="text-sm font-medium mb-4">Conversion Funnel</h3>
      <div className="space-y-3">
        {stages.map((s) => (
          <div key={s.stage} className="flex items-center gap-3">
            <span className="text-xs text-muted w-20 capitalize shrink-0">{s.stage}</span>
            <div className="flex-1 bg-border rounded-full h-6 overflow-hidden">
              <div
                className={`h-full rounded-full ${colors[s.stage] || "bg-accent"} transition-all`}
                style={{ width: `${Math.max((s.count / max) * 100, 4)}%` }}
              />
            </div>
            <span className="text-sm font-medium w-8 text-right">{s.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function UpcomingBookings({ bookings }: { bookings: Booking[] }) {
  const upcoming = bookings
    .filter((b) => b.status !== "cancelled")
    .sort((a, b) => new Date(a.event_date).getTime() - new Date(b.event_date).getTime())
    .slice(0, 5);

  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <h3 className="text-sm font-medium mb-3">Upcoming Bookings</h3>
      {upcoming.length === 0 ? (
        <p className="text-xs text-muted">No upcoming bookings</p>
      ) : (
        <div className="space-y-2">
          {upcoming.map((b) => (
            <div key={b.id} className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
              <div>
                <p className="text-sm font-medium">{b.customer_name}</p>
                <p className="text-xs text-muted">{b.event_type}</p>
              </div>
              <div className="text-right">
                <p className="text-xs text-muted">
                  {new Date(b.event_date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                </p>
                <p className="text-xs font-medium text-success">${b.total_price}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentStatusGrid({ agents }: { agents: AgentInfo[] }) {
  const statusColor: Record<string, string> = {
    running: "bg-success",
    idle: "bg-warning",
    stopped: "bg-muted",
    error: "bg-danger",
  };

  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <h3 className="text-sm font-medium mb-3">Active Agents</h3>
      {agents.length === 0 ? (
        <p className="text-xs text-muted">No agents running</p>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {agents.slice(0, 6).map((a) => (
            <div key={a.name} className="flex items-center gap-2 py-1">
              <div className={`w-2 h-2 rounded-full shrink-0 ${statusColor[a.status] || "bg-muted"}`} />
              <span className="text-xs truncate">{a.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EmailMini({ stats }: { stats: MarketingStats | null }) {
  if (!stats) return null;
  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <h3 className="text-sm font-medium mb-3">Email Outreach</h3>
      <div className="space-y-2">
        <div className="flex justify-between">
          <span className="text-xs text-muted">Sent today</span>
          <span className="text-sm font-medium">{stats.sent_today}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-xs text-muted">Total sent</span>
          <span className="text-sm font-medium">{stats.sent_total}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-xs text-muted">Reply rate</span>
          <span className="text-sm font-medium text-success">{stats.reply_rate_pct}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-xs text-muted">Eligible remaining</span>
          <span className="text-sm font-medium text-accent">{stats.eligible_uncontacted}</span>
        </div>
      </div>
    </div>
  );
}

export function Dashboard() {
  const [crm, setCrm] = useState<CRMDashboard | null>(null);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [emailStats, setEmailStats] = useState<MarketingStats | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(() => {
    Promise.all([
      fetchApi<CRMDashboard>("/api/crm/dashboard").catch(() => null),
      fetchApi<{ agents: AgentInfo[] }>("/api/agents/status").catch(() => ({ agents: [] })),
      fetchApi<{ bookings: Booking[] }>("/api/bookings").catch(() => ({ bookings: [] })),
      fetchApi<MarketingStats>("/api/email-marketing/stats").catch(() => null),
    ]).then(([crmData, agentData, bookingData, emailData]) => {
      if (crmData) setCrm(crmData);
      setAgents(asArray(agentData?.agents));
      setBookings(asArray(bookingData?.bookings));
      if (emailData) setEmailStats(emailData);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 30000);
    return () => clearInterval(iv);
  }, [loadData]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
      </div>
    );
  }

  const daysSinceBooking = bookings.length > 0
    ? Math.floor(
        (Date.now() - Math.max(...bookings.map((b) => new Date(b.created_at).getTime()))) /
          (1000 * 60 * 60 * 24),
      )
    : 99;

  const funnel = crm?.funnel || crm?.stage_counts
    ? Object.entries(crm?.stage_counts || {}).map(([stage, count]) => ({ stage, count }))
    : [
        { stage: "new", count: crm?.total_leads || 0 },
        { stage: "quoted", count: 0 },
        { stage: "booked", count: crm?.won_count || 0 },
      ];

  const activeAgents = agents.filter((a) => a.status === "running").length;

  return (
    <div className="flex-1 p-6 overflow-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold mb-1">Dashboard</h2>
        <p className="text-sm text-muted">Zoar Bathroom Rentals — Command Center</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard
          label="Days Since Booking"
          value={daysSinceBooking}
          color={daysSinceBooking > 5 ? "text-danger" : "text-success"}
          sub={daysSinceBooking > 5 ? "Goal: every 5 days" : "On track"}
          size="large"
        />
        <StatCard
          label="Total Bookings"
          value={crm?.won_count || 0}
          color="text-success"
        />
        <StatCard
          label="Pipeline Value"
          value={`$${(crm?.total_value || 0).toLocaleString()}`}
          color="text-accent"
        />
        <StatCard
          label="Conversion Rate"
          value={`${crm?.conversion_pct || 0}%`}
          color="text-foreground"
        />
        <StatCard
          label="Active Agents"
          value={activeAgents}
          sub={`${agents.length} total`}
          color="text-accent"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="md:col-span-2">
          <FunnelChart stages={funnel} />
        </div>
        <StatCard
          label="Won Revenue"
          value={`$${(crm?.won_revenue || 0).toLocaleString()}`}
          color="text-success"
          sub={`${crm?.total_leads || 0} total leads`}
          size="large"
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <UpcomingBookings bookings={bookings} />
        <AgentStatusGrid agents={agents} />
        <EmailMini stats={emailStats} />
      </div>
    </div>
  );
}
