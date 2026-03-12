"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, asArray } from "@/lib/api";

interface FBProspect {
  id: number;
  business_name: string;
  profile_url: string;
  phone: string;
  email: string;
  website: string;
  city: string;
  category: string;
  status: string;
  referral_score: number;
  activity_level: string;
  post_content: string;
  group_name: string;
  created_at: string;
}

interface FBStats {
  total: number;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  avg_score: number;
}

const CATEGORY_LABELS: Record<string, string> = {
  wedding_venue: "Wedding Venue",
  event_planner: "Event Planner",
  catering: "Catering",
  photographer: "Photographer",
  florist: "Florist",
  dj_entertainment: "DJ / Entertainment",
  rental_company: "Rental Company",
  decorator: "Decorator",
  bakery: "Bakery",
  officiant: "Officiant",
  hair_makeup: "Hair & Makeup",
  videographer: "Videographer",
  transportation: "Transportation",
  other: "Other",
};

const STATUS_COLORS: Record<string, string> = {
  new: "bg-accent/20 text-accent",
  drafted: "bg-purple-500/20 text-purple-300",
  ready: "bg-warning/20 text-warning",
  sent: "bg-success/20 text-success",
  replied: "bg-green-500/20 text-green-300",
  skipped: "bg-muted/20 text-muted",
};

function ScoreBar({ score }: { score: number }) {
  const color = score >= 60 ? "bg-success" : score >= 30 ? "bg-warning" : "bg-danger";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 bg-border rounded-full h-2 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.min(score, 100)}%` }} />
      </div>
      <span className="text-xs font-medium w-6">{score}</span>
    </div>
  );
}

export function ReferralLeads() {
  const [prospects, setProspects] = useState<FBProspect[]>([]);
  const [stats, setStats] = useState<FBStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("All");
  const [statusFilter, setStatusFilter] = useState("All");
  const [minScore, setMinScore] = useState(0);
  const [sortField, setSortField] = useState<"referral_score" | "created_at" | "business_name">("referral_score");
  const [sortAsc, setSortAsc] = useState(false);
  const [selectedProspect, setSelectedProspect] = useState<FBProspect | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [prospectRes, statsRes] = await Promise.all([
        fetchApi<{ prospects: FBProspect[] }>("/api/fb/prospects/scored?sort=score&order=desc&limit=200")
          .catch(() => fetchApi<{ prospects: FBProspect[] }>("/api/fb/prospects?limit=200"))
          .catch(() => ({ prospects: [] })),
        fetchApi<FBStats>("/api/fb/stats/scored")
          .catch(() => fetchApi<FBStats>("/api/fb/stats"))
          .catch(() => null),
      ]);
      setProspects(asArray(prospectRes?.prospects));
      if (statsRes) setStats(statsRes);
    } catch { /* */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSort = (field: typeof sortField) => {
    if (sortField === field) setSortAsc(!sortAsc);
    else { setSortField(field); setSortAsc(field === "business_name"); }
  };

  const categories = Array.from(new Set(prospects.map((p) => p.category).filter(Boolean)));

  const filtered = prospects
    .filter((p) => {
      if (search) {
        const q = search.toLowerCase();
        if (!(p.business_name || "").toLowerCase().includes(q) &&
            !(p.city || "").toLowerCase().includes(q) &&
            !(p.email || "").toLowerCase().includes(q)) return false;
      }
      if (categoryFilter !== "All" && p.category !== categoryFilter) return false;
      if (statusFilter !== "All" && p.status !== statusFilter) return false;
      if (minScore > 0 && (p.referral_score || 0) < minScore) return false;
      return true;
    })
    .sort((a, b) => {
      const dir = sortAsc ? 1 : -1;
      if (sortField === "business_name") return dir * (a.business_name || "").localeCompare(b.business_name || "");
      if (sortField === "referral_score") return dir * ((a.referral_score || 0) - (b.referral_score || 0));
      const ca = a.created_at ? new Date(a.created_at).getTime() : 0;
      const cb = b.created_at ? new Date(b.created_at).getTime() : 0;
      return dir * (ca - cb);
    });

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
        <h2 className="text-xl font-bold">Referral Leads</h2>
        <p className="text-sm text-muted">Vendor prospects from Facebook groups</p>
      </div>

      {/* Stats */}
      <div className="flex gap-3 flex-wrap">
        <div className="bg-card border border-border rounded-lg px-3 py-2">
          <span className="text-xs text-muted">Total</span>
          <span className="text-sm font-bold ml-2">{stats?.total || prospects.length}</span>
        </div>
        {stats?.avg_score !== undefined && (
          <div className="bg-card border border-border rounded-lg px-3 py-2">
            <span className="text-xs text-muted">Avg Score</span>
            <span className="text-sm font-bold ml-2">{Math.round(stats.avg_score)}</span>
          </div>
        )}
        {stats?.by_category && Object.entries(stats.by_category)
          .sort(([, a], [, b]) => b - a)
          .slice(0, 4)
          .map(([cat, count]) => (
            <div key={cat} className="bg-card border border-border rounded-lg px-3 py-2">
              <span className="text-xs text-muted capitalize">{CATEGORY_LABELS[cat] || cat.replace(/_/g, " ")}</span>
              <span className="text-sm font-bold ml-2">{count}</span>
            </div>
          ))}
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <input
          type="text"
          placeholder="Search business, city, email..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm w-64 focus:outline-none focus:border-accent"
        />
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm"
        >
          <option value="All">All Categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{CATEGORY_LABELS[c] || c.replace(/_/g, " ")}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-background border border-border rounded-lg px-3 py-2 text-sm"
        >
          <option value="All">All Status</option>
          {["new", "drafted", "ready", "sent", "replied", "skipped"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">Min score:</span>
          <input
            type="range"
            min={0} max={100} step={10}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-20"
          />
          <span className="text-xs font-medium w-6">{minScore}</span>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto bg-card border border-border rounded-xl">
        <table className="w-full">
          <thead className="sticky top-0 bg-card">
            <tr className="border-b border-border">
              <th className="text-left px-4 py-3 text-xs text-muted font-medium cursor-pointer hover:text-foreground"
                onClick={() => handleSort("business_name")}>
                Business {sortField === "business_name" && (sortAsc ? "\u2191" : "\u2193")}
              </th>
              <th className="text-left px-4 py-3 text-xs text-muted font-medium">Category</th>
              <th className="text-left px-4 py-3 text-xs text-muted font-medium">City</th>
              <th className="text-left px-4 py-3 text-xs text-muted font-medium cursor-pointer hover:text-foreground"
                onClick={() => handleSort("referral_score")}>
                Score {sortField === "referral_score" && (sortAsc ? "\u2191" : "\u2193")}
              </th>
              <th className="text-left px-4 py-3 text-xs text-muted font-medium">Status</th>
              <th className="text-left px-4 py-3 text-xs text-muted font-medium">Group</th>
              <th className="text-left px-4 py-3 text-xs text-muted font-medium">Contact</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center py-10 text-sm text-muted">
                  {prospects.length === 0 ? "No referral leads yet. The scraper is running..." : "No matching leads"}
                </td>
              </tr>
            ) : (
              filtered.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => setSelectedProspect(p)}
                  className="border-b border-border last:border-0 hover:bg-card-hover transition-colors cursor-pointer"
                >
                  <td className="px-4 py-3 text-sm font-medium">{p.business_name || "\u2014"}</td>
                  <td className="px-4 py-3 text-xs text-muted capitalize">{CATEGORY_LABELS[p.category] || p.category?.replace(/_/g, " ") || "\u2014"}</td>
                  <td className="px-4 py-3 text-xs text-muted">{p.city || "\u2014"}</td>
                  <td className="px-4 py-3"><ScoreBar score={p.referral_score || 0} /></td>
                  <td className="px-4 py-3">
                    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[p.status] || "bg-muted/20 text-muted"}`}>
                      {p.status || "new"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted truncate max-w-[120px]">{p.group_name || "\u2014"}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1">
                      {p.email && <span className="text-[10px] text-accent" title={p.email}>\ud83d\udce7</span>}
                      {p.phone && <span className="text-[10px] text-success" title={p.phone}>\ud83d\udcf1</span>}
                      {p.website && <span className="text-[10px] text-warning" title={p.website}>\ud83c\udf10</span>}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Detail Slideout */}
      {selectedProspect && (
        <div className="fixed inset-0 bg-black/50 flex justify-end z-50" onClick={() => setSelectedProspect(null)}>
          <div
            className="w-[400px] bg-card border-l border-border h-full overflow-y-auto p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold">{selectedProspect.business_name}</h3>
              <button onClick={() => setSelectedProspect(null)} className="text-muted hover:text-foreground text-lg">&times;</button>
            </div>

            <div className="flex items-center gap-2">
              <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[selectedProspect.status] || "bg-muted/20 text-muted"}`}>
                {selectedProspect.status}
              </span>
              <span className="text-xs text-muted capitalize">{CATEGORY_LABELS[selectedProspect.category] || selectedProspect.category?.replace(/_/g, " ")}</span>
            </div>

            <ScoreBar score={selectedProspect.referral_score || 0} />

            <div className="space-y-3">
              {[
                ["City", selectedProspect.city],
                ["Email", selectedProspect.email],
                ["Phone", selectedProspect.phone],
                ["Website", selectedProspect.website],
                ["Activity Level", selectedProspect.activity_level],
                ["Facebook Group", selectedProspect.group_name],
              ].map(([label, value]) => (
                <div key={String(label)}>
                  <p className="text-[10px] text-muted uppercase tracking-wider">{label}</p>
                  {label === "Website" && value ? (
                    <a
                      href={String(value).startsWith("http") ? String(value) : `https://${value}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-accent hover:underline"
                    >
                      {String(value).replace(/^https?:\/\/(www\.)?/, "").slice(0, 40)}
                    </a>
                  ) : (
                    <p className="text-sm mt-0.5">{value || "\u2014"}</p>
                  )}
                </div>
              ))}
            </div>

            {selectedProspect.profile_url && (
              <a
                href={selectedProspect.profile_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block px-3 py-1.5 bg-accent/15 text-accent rounded-lg text-sm hover:bg-accent/25 transition-colors"
              >
                View Facebook Profile
              </a>
            )}

            {selectedProspect.post_content && (
              <div>
                <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Original Post</p>
                <div className="bg-background border border-border rounded-lg p-3 text-xs text-muted whitespace-pre-wrap max-h-48 overflow-y-auto">
                  {selectedProspect.post_content}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
