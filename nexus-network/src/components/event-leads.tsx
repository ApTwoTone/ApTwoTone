"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, asArray, type Lead } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  new: "bg-accent",
  new_lead: "bg-accent",
  contacted: "bg-blue-400",
  quoted: "bg-warning",
  booked: "bg-success",
  lost: "bg-danger",
  follow_up: "bg-purple-400",
};

const EVENT_TYPES = ["All", "Wedding", "Quinceañera", "Corporate", "Backyard Party", "Festival", "Film Production"];
const STATUS_OPTIONS = ["All", "new", "contacted", "quoted", "booked", "lost"];

function timeAgo(ts: string): string {
  if (!ts) return "\u2014";
  const now = Date.now();
  let ms: number;
  if (/^\d{10}$/.test(ts)) ms = Number(ts) * 1000;
  else if (/^\d{13}$/.test(ts)) ms = Number(ts);
  else ms = new Date(ts).getTime();
  const diff = now - ms;
  if (diff < 86400000) return "today";
  const days = Math.floor(diff / 86400000);
  return `${days}d ago`;
}

type SortField = "event_date" | "created_at" | "status" | "name";

export function EventLeads() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [eventTypeFilter, setEventTypeFilter] = useState("All");
  const [sortField, setSortField] = useState<SortField>("event_date");
  const [sortAsc, setSortAsc] = useState(true);
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null);

  const loadData = useCallback(async () => {
    try {
      const res = await fetchApi<{ leads: Lead[] }>("/api/crm/leads");
      setLeads(asArray(res?.leads));
    } catch { /* */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 30000);
    return () => clearInterval(iv);
  }, [loadData]);

  const handleSort = (field: SortField) => {
    if (sortField === field) setSortAsc(!sortAsc);
    else { setSortField(field); setSortAsc(true); }
  };

  const filtered = leads
    .filter((l) => {
      if (search) {
        const q = search.toLowerCase();
        if (!(l.name || "").toLowerCase().includes(q) &&
            !(l.event_city || "").toLowerCase().includes(q) &&
            !(l.email || "").toLowerCase().includes(q)) return false;
      }
      if (statusFilter !== "All" && l.status !== statusFilter && l.status !== `${statusFilter}_lead`) return false;
      if (eventTypeFilter !== "All" && (l.event_type || "").toLowerCase() !== eventTypeFilter.toLowerCase()) return false;
      return true;
    })
    .sort((a, b) => {
      const dir = sortAsc ? 1 : -1;
      if (sortField === "name") return dir * (a.name || "").localeCompare(b.name || "");
      if (sortField === "status") return dir * (a.status || "").localeCompare(b.status || "");
      if (sortField === "event_date") {
        const da = a.event_date ? new Date(a.event_date).getTime() : 0;
        const db = b.event_date ? new Date(b.event_date).getTime() : 0;
        return dir * (da - db);
      }
      const ca = a.created_at ? new Date(a.created_at).getTime() : 0;
      const cb = b.created_at ? new Date(b.created_at).getTime() : 0;
      return dir * (ca - cb);
    });

  const byStatus = leads.reduce<Record<string, number>>((acc, l) => {
    const s = (l.status || "new").replace("_lead", "");
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden p-6 space-y-4">
      <div>
        <h2 className="text-xl font-bold">Event Leads</h2>
        <p className="text-sm text-muted">All leads with event details</p>
      </div>

      {/* Stats */}
      <div className="flex gap-3 flex-wrap">
        <div className="bg-card border border-border rounded-lg px-3 py-2">
          <span className="text-xs text-muted">Total</span>
          <span className="text-sm font-bold ml-2">{leads.length}</span>
        </div>
        {Object.entries(byStatus).map(([status, count]) => (
          <div key={status} className="bg-card border border-border rounded-lg px-3 py-2 flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[status] || "bg-muted"}`} />
            <span className="text-xs text-muted capitalize">{status}</span>
            <span className="text-sm font-bold">{count}</span>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Search name, city, email..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm w-64 focus:outline-none focus:border-accent"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm"
        >
          {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s === "All" ? "All Status" : s}</option>)}
        </select>
        <select
          value={eventTypeFilter}
          onChange={(e) => setEventTypeFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm"
        >
          {EVENT_TYPES.map((t) => <option key={t} value={t}>{t === "All" ? "All Events" : t}</option>)}
        </select>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto bg-card border border-border rounded-xl">
        <table className="w-full">
          <thead className="sticky top-0 bg-card">
            <tr className="border-b border-border">
              {([
                ["name", "Name"],
                ["event_date", "Event Date"],
                ["", "Type"],
                ["", "City"],
                ["", "Guests"],
                ["status", "Status"],
                ["created_at", "Source"],
                ["created_at", "Created"],
              ] as [SortField | "", string][]).map(([field, label], i) => (
                <th
                  key={i}
                  className={`text-left px-4 py-3 text-xs text-muted font-medium ${field ? "cursor-pointer hover:text-foreground" : ""}`}
                  onClick={() => field && handleSort(field)}
                >
                  {label}
                  {field && sortField === field && <span className="ml-1">{sortAsc ? "\u2191" : "\u2193"}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="text-center py-10 text-sm text-muted">
                  {search || statusFilter !== "All" || eventTypeFilter !== "All" ? "No matching leads" : "No leads yet"}
                </td>
              </tr>
            ) : (
              filtered.map((l) => (
                <tr
                  key={l.id}
                  onClick={() => setSelectedLead(l)}
                  className="border-b border-border last:border-0 hover:bg-card-hover transition-colors cursor-pointer"
                >
                  <td className="px-4 py-3 text-sm font-medium">{l.name || "\u2014"}</td>
                  <td className="px-4 py-3 text-sm">
                    {l.event_date ? new Date(l.event_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "\u2014"}
                  </td>
                  <td className="px-4 py-3 text-sm text-muted">{l.event_type || "\u2014"}</td>
                  <td className="px-4 py-3 text-sm text-muted">{l.event_city || "\u2014"}</td>
                  <td className="px-4 py-3 text-sm text-muted">{l.guest_count || "\u2014"}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium text-white ${
                      STATUS_COLORS[(l.status || "new").replace("_lead", "")] || "bg-muted"
                    }`}>
                      {(l.status || "new").replace("_lead", "")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">{l.source || "\u2014"}</td>
                  <td className="px-4 py-3 text-xs text-muted">{timeAgo(l.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Detail Modal */}
      {selectedLead && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setSelectedLead(null)}>
          <div className="bg-card border border-border rounded-xl p-6 max-w-lg w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold">{selectedLead.name}</h3>
              <button onClick={() => setSelectedLead(null)} className="text-muted hover:text-foreground text-lg">&times;</button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {[
                ["Phone", selectedLead.phone],
                ["Email", selectedLead.email],
                ["Event Type", selectedLead.event_type],
                ["Event Date", selectedLead.event_date ? new Date(selectedLead.event_date).toLocaleDateString() : "\u2014"],
                ["Event City", selectedLead.event_city],
                ["Guest Count", selectedLead.guest_count || "\u2014"],
                ["Source", selectedLead.source],
                ["Status", selectedLead.status],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <p className="text-[10px] text-muted uppercase tracking-wider">{label}</p>
                  <p className="text-sm mt-0.5">{value || "\u2014"}</p>
                </div>
              ))}
            </div>
            {selectedLead.notes && (
              <div className="mt-4">
                <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Notes</p>
                <p className="text-sm text-muted whitespace-pre-wrap">{selectedLead.notes}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
