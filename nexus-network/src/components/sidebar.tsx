"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { fetchApi } from "@/lib/api";

const NAV_ITEMS = [
  { id: "dashboard", label: "Dashboard", icon: "📈" },
  { id: "bookings", label: "Bookings", icon: "📅" },
  { id: "talk", label: "Talk to Nexus", icon: "💬" },
  { id: "leads", label: "Leads Pipeline", icon: "🎯" },
  { id: "conversations", label: "Conversations", icon: "💬" },
  { id: "vendors", label: "Vendors", icon: "🤝" },
  { id: "aggressive-outreach", label: "Aggressive Outreach", icon: "🎯" },
  { id: "email-marketing", label: "Email Marketing", icon: "📧" },
  { id: "quick-email", label: "Quick Email", icon: "✉️" },
  { id: "fleet", label: "Fleet Command", icon: "🚀" },
  { id: "event-leads", label: "Event Leads", icon: "🎉" },
  { id: "referrals", label: "Referral Leads", icon: "🔗" },
  { id: "dossiers", label: "Research Pipeline", icon: "🔬" },
  { id: "ads", label: "Ad Performance", icon: "📊" },
  { id: "health", label: "System Health", icon: "🛡️" },
  { id: "beta-research", label: "Beta Research", icon: "🧪" },
  { id: "entrepreneur", label: "Entrepreneur Lab", icon: "🧠" },
  { id: "snapshots", label: "Version History", icon: "🕐" },
  { id: "settings", label: "Settings", icon: "⚙️" },
] as const;

export type PageId = (typeof NAV_ITEMS)[number]["id"];

export function Sidebar({
  active,
  onNavigate,
}: {
  active: PageId;
  onNavigate: (id: PageId) => void;
}) {
  const { session, logout } = useAuth();
  const [uiVersion, setUiVersion] = useState<{ version: string; hash: string; built_at: string } | null>(null);
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    const check = () => {
      fetchApi<{ version: string; hash: string; built_at: string }>("/api/ui/version")
        .then(v => {
          setConnected(true);
          setUiVersion(prev => {
            if (prev && prev.hash !== v.hash) {
              const el = document.getElementById("ui-version-indicator");
              if (el) { el.classList.add("animate-pulse"); setTimeout(() => el.classList.remove("animate-pulse"), 3000); }
            }
            return v;
          });
        })
        .catch(() => setConnected(false));
    };
    check();
    const iv = setInterval(check, 10000);
    return () => clearInterval(iv);
  }, []);

  return (
    <aside className="w-56 shrink-0 bg-sidebar border-r border-border flex flex-col h-screen">
      <div className="px-4 py-5 border-b border-border">
        <h1 className="text-lg font-bold tracking-tight">Nexus Network</h1>
        <p className="text-xs text-muted mt-0.5">Zoar Command Center</p>
      </div>

      <nav className="flex-1 py-3 px-2 space-y-0.5">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            onClick={() => onNavigate(item.id)}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
              active === item.id
                ? "bg-accent/15 text-accent"
                : "text-muted hover:text-foreground hover:bg-card-hover"
            }`}
          >
            <span className="text-base">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </nav>

      <div className="px-4 py-3 border-t border-border space-y-2">
        <div id="ui-version-indicator" className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${connected ? "bg-success" : "bg-red-500 animate-pulse"}`} />
            <span className="text-xs text-muted">{connected ? "Server connected" : "Reconnecting..."}</span>
          </div>
          {uiVersion && (
            <span className="text-[9px] text-muted font-mono" title={`Built: ${uiVersion.built_at || "dev"}`}>
              {uiVersion.hash || "dev"}
            </span>
          )}
        </div>
        {session && (
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-medium">{session.name}</p>
              <p className="text-xs text-muted capitalize">{session.role}</p>
            </div>
            <button
              onClick={logout}
              className="text-xs text-muted hover:text-danger transition-colors"
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
