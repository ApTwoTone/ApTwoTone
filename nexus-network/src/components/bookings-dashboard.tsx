"use client";

import { useEffect, useState } from "react";
import { fetchApi, type Booking, type Lead } from "@/lib/api";

function StatCard({
  label,
  value,
  sub,
  color = "text-foreground",
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-muted mt-1">{sub}</p>}
    </div>
  );
}

function LeadRow({ lead }: { lead: Lead }) {
  const statusColor: Record<string, string> = {
    new: "bg-accent",
    new_lead: "bg-accent",
    contacted: "bg-warning",
    initial_contact: "bg-warning",
    awaiting_approval: "bg-warning",
    quoted: "bg-warning",
    booked: "bg-success",
    confirmed: "bg-success",
    lost: "bg-danger",
    blocklisted: "bg-danger",
    recovered: "bg-warning",
  };

  return (
    <tr className="border-b border-border hover:bg-card-hover transition-colors">
      <td className="px-4 py-3 text-sm">{lead.full_name || lead.name || "Unknown"}</td>
      <td className="px-4 py-3 text-sm text-muted">{lead.event_type || "—"}</td>
      <td className="px-4 py-3 text-sm text-muted">{lead.event_date || "—"}</td>
      <td className="px-4 py-3 text-sm text-muted">{lead.event_city || "—"}</td>
      <td className="px-4 py-3 text-sm">
        <span
          className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium text-white ${
            statusColor[lead.status] || "bg-muted"
          }`}
        >
          {lead.status}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-muted">{lead.source}</td>
    </tr>
  );
}

export function BookingsDashboard() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchApi<{ leads: Lead[] }>("/api/crm/leads")
      .then((data) => {
        setLeads(data.leads || []);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  const booked = leads.filter((l) => l.status === "booked").length;
  const quoted = leads.filter((l) => l.status === "quoted").length;
  const newLeads = leads.filter(
    (l) => ["new", "new_lead", "contacted", "initial_contact", "awaiting_approval", "recovered"].includes(l.status)
  ).length;

  // Safe date parser for timestamps (handles Unix seconds, ms, and ISO strings)
  const safeDate = (v: string | number | undefined): number => {
    if (!v) return NaN;
    if (typeof v === "number") return v < 1e12 ? v * 1000 : v;
    const ms = new Date(v).getTime();
    return Number.isFinite(ms) ? ms : NaN;
  };

  // Days since last booking
  const lastBooked = leads
    .filter((l) => l.status === "booked")
    .sort((a, b) => (safeDate(b.created_at) || 0) - (safeDate(a.created_at) || 0))[0];
  const daysSinceBooking = lastBooked && Number.isFinite(safeDate(lastBooked.created_at))
    ? Math.floor((Date.now() - safeDate(lastBooked.created_at)) / (1000 * 60 * 60 * 24))
    : "—";

  // Revenue estimate ($1,100 avg per booking)
  const revenueEstimate = booked * 1100;

  // Email system countdown (March 25, 2026)
  const emailLaunch = new Date("2026-03-25T00:00:00").getTime();
  const daysToEmail = Math.ceil((emailLaunch - Date.now()) / (1000 * 60 * 60 * 24));
  const emailColor = daysToEmail > 14 ? "text-success" : daysToEmail > 7 ? "text-warning" : "text-danger";

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="mb-6">
        <h2 className="text-xl font-bold">Bookings Dashboard</h2>
        <p className="text-sm text-muted mt-1">
          Goal: 1 booking every 5 days for the next 90 days
        </p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4 mb-4">
        <StatCard
          label="Days Since Last Booking"
          value={daysSinceBooking}
          color={
            typeof daysSinceBooking === "number" && daysSinceBooking > 5
              ? "text-danger"
              : "text-success"
          }
          sub="Target: < 5 days"
        />
        <StatCard label="Total Booked" value={booked} color="text-success" />
        <StatCard
          label="Revenue Estimate"
          value={`$${revenueEstimate.toLocaleString()}`}
          color="text-success"
          sub={`$1,100 avg × ${booked} bookings`}
        />
      </div>
      <div className="grid grid-cols-3 gap-4 mb-6">
        <StatCard
          label="Quotes Pending"
          value={quoted}
          color="text-warning"
          sub="Awaiting response"
        />
        <StatCard
          label="New Leads"
          value={newLeads}
          color="text-accent"
          sub="Need quotes"
        />
        <StatCard
          label="Email System Launch"
          value={daysToEmail > 0 ? `${daysToEmail}d` : "LIVE"}
          color={emailColor}
          sub="March 25, 2026"
        />
      </div>

      {/* Leads table */}
      <div className="bg-card rounded-xl border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <h3 className="text-sm font-medium">Recent Leads</h3>
          <span className="text-xs text-muted">{leads.length} total</span>
        </div>

        {loading ? (
          <div className="px-4 py-8 text-center text-muted text-sm">
            Loading leads...
          </div>
        ) : error ? (
          <div className="px-4 py-8 text-center text-danger text-sm">
            {error}
          </div>
        ) : leads.length === 0 ? (
          <div className="px-4 py-8 text-center text-muted text-sm">
            No leads yet — webhook is live and waiting
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-border text-left">
                <th className="px-4 py-2 text-xs font-medium text-muted">
                  Name
                </th>
                <th className="px-4 py-2 text-xs font-medium text-muted">
                  Event
                </th>
                <th className="px-4 py-2 text-xs font-medium text-muted">
                  Date
                </th>
                <th className="px-4 py-2 text-xs font-medium text-muted">
                  City
                </th>
                <th className="px-4 py-2 text-xs font-medium text-muted">
                  Status
                </th>
                <th className="px-4 py-2 text-xs font-medium text-muted">
                  Source
                </th>
              </tr>
            </thead>
            <tbody>
              {leads
                .sort(
                  (a, b) =>
                    new Date(b.created_at).getTime() -
                    new Date(a.created_at).getTime()
                )
                .slice(0, 20)
                .map((lead) => (
                  <LeadRow key={lead.id} lead={lead} />
                ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
