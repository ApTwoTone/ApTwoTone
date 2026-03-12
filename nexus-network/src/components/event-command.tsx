"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi } from "@/lib/api";

/* ── Event Command Center — Kanban board for event pipeline ── */

interface EventCard {
  id: string;
  event_type: string;
  estimated_date: string;
  estimated_location: string;
  guest_count: number;
  distance_miles: number;
  vendors_identified: number;
  vendors_contacted: number;
  vendors_confirmed: number;
  quote_sent: boolean;
  status: string;
}

interface EventData {
  columns: {
    signal_detected: EventCard[];
    vendors_identified: EventCard[];
    outreach_sent: EventCard[];
    partnership_active: EventCard[];
    quoted: EventCard[];
    booked: EventCard[];
  };
  stats: {
    total_events: number;
    active_partnerships: number;
    quotes_sent: number;
    booked: number;
  };
}

const COLUMNS = [
  { key: "signal_detected", label: "Signal Detected", color: "border-t-blue-500" },
  { key: "vendors_identified", label: "Vendors Found", color: "border-t-purple-500" },
  { key: "outreach_sent", label: "Outreach Sent", color: "border-t-yellow-500" },
  { key: "partnership_active", label: "Partnership Active", color: "border-t-green-500" },
  { key: "quoted", label: "Quoted", color: "border-t-orange-500" },
  { key: "booked", label: "Booked", color: "border-t-emerald-500" },
] as const;

export function EventCommandCenter() {
  const [data, setData] = useState<EventData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const result = await fetchApi<EventData>("/api/events/command");
      setData(result);
    } catch {
      setData({
        columns: {
          signal_detected: [],
          vendors_identified: [],
          outreach_sent: [],
          partnership_active: [],
          quoted: [],
          booked: [],
        },
        stats: { total_events: 0, active_partnerships: 0, quotes_sent: 0, booked: 0 },
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, [load]);

  if (loading && !data) {
    return (
      <div className="flex-1 p-6 overflow-auto">
        <div className="text-center text-muted py-12">Loading events...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-bold">Event Command Center</h2>
          <p className="text-sm text-muted mt-1">
            Every event signal in SFV/Greater LA tracked from detection to booking
          </p>
        </div>
        <button
          onClick={load}
          className="px-4 py-2 text-sm bg-card border border-border rounded-lg hover:bg-card-hover transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Stats Bar */}
      {data && (
        <div className="grid grid-cols-4 gap-3 mb-6">
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-accent">{data.stats.total_events}</p>
            <p className="text-xs text-muted">Events Tracked</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-purple-400">
              {data.stats.active_partnerships}
            </p>
            <p className="text-xs text-muted">Active Partners</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-yellow-400">{data.stats.quotes_sent}</p>
            <p className="text-xs text-muted">Quotes Sent</p>
          </div>
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <p className="text-2xl font-bold text-green-400">{data.stats.booked}</p>
            <p className="text-xs text-muted">Booked</p>
          </div>
        </div>
      )}

      {/* Kanban Board */}
      <div className="grid grid-cols-6 gap-3 min-h-[500px]">
        {COLUMNS.map((col) => {
          const cards = data?.columns[col.key as keyof typeof data.columns] || [];
          return (
            <div key={col.key} className="flex flex-col">
              <div
                className={`bg-card border border-border ${col.color} border-t-2 rounded-t-lg px-3 py-2`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold">{col.label}</span>
                  <span className="text-xs text-muted">{cards.length}</span>
                </div>
              </div>
              <div className="flex-1 bg-background/50 border border-t-0 border-border rounded-b-lg p-2 space-y-2 overflow-y-auto max-h-[600px]">
                {cards.length === 0 && (
                  <p className="text-[10px] text-muted text-center py-4">Empty</p>
                )}
                {cards.map((card) => (
                  <div
                    key={card.id}
                    className="bg-card border border-border rounded-lg p-2.5 hover:border-accent/30 transition-colors cursor-pointer"
                  >
                    <p className="text-xs font-medium mb-1">{card.event_type}</p>
                    <div className="space-y-0.5 text-[10px] text-muted">
                      <p>{card.estimated_location}</p>
                      <p>{card.estimated_date}</p>
                      {card.guest_count > 0 && <p>{card.guest_count} guests</p>}
                      {card.distance_miles > 0 && (
                        <p>{card.distance_miles} mi from base</p>
                      )}
                    </div>
                    {card.vendors_identified > 0 && (
                      <div className="mt-1.5 flex gap-1">
                        <span className="text-[9px] px-1 py-0.5 rounded bg-purple-500/10 text-purple-400">
                          {card.vendors_identified} vendors
                        </span>
                        {card.vendors_contacted > 0 && (
                          <span className="text-[9px] px-1 py-0.5 rounded bg-yellow-500/10 text-yellow-400">
                            {card.vendors_contacted} contacted
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
