"use client";

import { useEffect, useState } from "react";
import { fetchApi, type Lead } from "@/lib/api";

const STATUSES = ["new", "contacted", "quoted", "booked", "lost"] as const;
type Status = (typeof STATUSES)[number];

const STATUS_MAP: Record<string, Status> = {
  new: "new",
  new_lead: "new",
  contacted: "contacted",
  initial_contact: "contacted",
  awaiting_approval: "contacted",
  quoted: "quoted",
  booked: "booked",
  confirmed: "booked",
  lost: "lost",
  blocklisted: "lost",
  recovered: "contacted",
};

function normalizeStatus(raw: string): Status {
  return STATUS_MAP[raw] || "new";
}

const STATUS_CONFIG: Record<
  Status,
  { label: string; color: string; bg: string }
> = {
  new: { label: "New", color: "text-blue-400", bg: "bg-blue-500/10 border-blue-500/20" },
  contacted: { label: "Contacted", color: "text-yellow-400", bg: "bg-yellow-500/10 border-yellow-500/20" },
  quoted: { label: "Quoted", color: "text-orange-400", bg: "bg-orange-500/10 border-orange-500/20" },
  booked: { label: "Booked", color: "text-green-400", bg: "bg-green-500/10 border-green-500/20" },
  lost: { label: "Lost", color: "text-red-400", bg: "bg-red-500/10 border-red-500/20" },
};

function leadTimestamp(lead: Lead): number {
  const value = lead.updated_at || lead.discovered_at || lead.date_added || lead.created_at;
  if (!value) return 0;
  if (typeof value === "number") {
    return value < 1e12 ? value * 1000 : value;
  }
  const ts = new Date(value).getTime();
  return Number.isFinite(ts) ? ts : 0;
}

function LeadCard({ lead }: { lead: Lead }) {
  const createdMs = leadTimestamp(lead);
  const daysAgo = Number.isFinite(createdMs)
    ? Math.max(0, Math.floor((Date.now() - createdMs) / (1000 * 60 * 60 * 24)))
    : 0;

  return (
    <div className="bg-card border border-border rounded-lg p-3 mb-2 hover:border-accent/30 transition-colors cursor-pointer">
      <div className="flex items-start justify-between">
        <p className="text-sm font-medium truncate">{lead.full_name || lead.name || lead.business_name || "Unknown"}</p>
        <span className="text-xs text-muted shrink-0 ml-2">
          {daysAgo === 0 ? "today" : `${daysAgo}d ago`}
        </span>
      </div>
      {lead.event_type && (
        <p className="text-xs text-muted mt-1">{lead.event_type}</p>
      )}
      {lead.event_city && (
        <p className="text-xs text-muted">{lead.event_city}</p>
      )}
      <div className="flex items-center gap-2 mt-2">
        <span className="text-xs px-1.5 py-0.5 rounded bg-card-hover text-muted">
          {lead.source}
        </span>
        {lead.phone && (
          <span className="text-xs text-muted">{lead.phone.slice(-4).padStart(lead.phone.length, "*")}</span>
        )}
      </div>
    </div>
  );
}

function PipelineColumn({
  status,
  leads,
}: {
  status: Status;
  leads: Lead[];
}) {
  const config = STATUS_CONFIG[status];
  return (
    <div className={`flex-1 min-w-[200px] rounded-xl border ${config.bg} p-3`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className={`text-sm font-semibold ${config.color}`}>
          {config.label}
        </h3>
        <span className="text-xs text-muted bg-card px-2 py-0.5 rounded-full">
          {leads.length}
        </span>
      </div>
      <div className="space-y-0">
        {leads.length === 0 ? (
          <p className="text-xs text-muted text-center py-4">No leads</p>
        ) : (
          leads
            .sort(
              (a, b) =>
                leadTimestamp(b) - leadTimestamp(a)
            )
            .map((lead) => <LeadCard key={lead.id} lead={lead} />)
        )}
      </div>
    </div>
  );
}

export function LeadsPipeline() {
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

  const grouped = STATUSES.reduce(
    (acc, s) => {
      acc[s] = leads.filter((l) => normalizeStatus(l.status || l.booking_status || "") === s);
      return acc;
    },
    {} as Record<Status, Lead[]>
  );

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="mb-6">
        <h2 className="text-xl font-bold">Leads Pipeline</h2>
        <p className="text-sm text-muted mt-1">
          {leads.length} total leads across all stages
        </p>
      </div>

      {loading ? (
        <div className="text-center text-muted py-12">Loading pipeline...</div>
      ) : error ? (
        <div className="text-center text-danger py-12">{error}</div>
      ) : (
        <div className="flex gap-4 overflow-x-auto pb-4">
          {STATUSES.map((status) => (
            <PipelineColumn
              key={status}
              status={status}
              leads={grouped[status]}
            />
          ))}
        </div>
      )}
    </div>
  );
}
