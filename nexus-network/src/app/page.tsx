"use client";

import { useState } from "react";
import { Sidebar, type PageId } from "@/components/sidebar";
import { BookingsDashboard } from "@/components/bookings-dashboard";
import { BookingsCalendar } from "@/components/bookings-calendar";
import { Dashboard } from "@/components/dashboard";
import { Conversations } from "@/components/conversations";
import { LeadsPipeline } from "@/components/leads-pipeline";
import { VendorsPipeline } from "@/components/vendors-pipeline";
import { AggressiveOutreach } from "@/components/aggressive-outreach";
import { EmailMarketingV2 } from "@/components/email-marketing-v2";
import { QuickEmail } from "@/components/quick-email";
import { FleetCommand } from "@/components/fleet-command";
import { AdPerformance } from "@/components/ad-performance";
import { FleetStatusBanner } from "@/components/fleet-status";
import { SettingsPage } from "@/components/settings-page";
import { TalkToNexus } from "@/components/talk-to-nexus";
import { SystemHealth } from "@/components/system-health";
import { EventLeads } from "@/components/event-leads";
import { ReferralLeads } from "@/components/referral-leads";
import { BetaResearch } from "@/components/beta-research";
import { SnapshotsPanel } from "@/components/snapshots-panel";
import { EntrepreneurLab } from "@/components/entrepreneur-lab";
import { LeadResearchPipeline } from "@/components/lead-research-pipeline";

const PAGES: Record<PageId, { title: string; description: string }> = {
  dashboard: { title: "Dashboard", description: "" },
  bookings: { title: "Bookings", description: "" },
  talk: { title: "Talk to Nexus", description: "" },
  leads: { title: "Leads Pipeline", description: "" },
  conversations: { title: "Conversations", description: "" },
  vendors: { title: "Vendor Partners", description: "" },
  "aggressive-outreach": { title: "Aggressive Outreach", description: "" },
  "email-marketing": { title: "Email Marketing", description: "" },
  "quick-email": { title: "Quick Email", description: "" },
  fleet: { title: "Fleet Command", description: "" },
  "event-leads": { title: "Event Leads", description: "" },
  referrals: { title: "Referral Leads", description: "" },
  dossiers: { title: "Lead Research Pipeline", description: "" },
  ads: { title: "Ad Performance", description: "" },
  health: { title: "System Health", description: "" },
  "beta-research": { title: "Beta Research", description: "" },
  entrepreneur: { title: "Entrepreneur Lab", description: "" },
  snapshots: { title: "Version History", description: "Snapshot before every big change. Restore with one click." },
  settings: {
    title: "Settings",
    description: "API keys, notification preferences, pricing config — coming soon",
  },
};

export default function Home() {
  const [page, setPage] = useState<PageId>("dashboard");

  const renderPage = () => {
    switch (page) {
      case "dashboard":
        return <Dashboard />;
      case "bookings":
        return <BookingsCalendar />;
      case "talk":
        return <TalkToNexus />;
      case "leads":
        return <LeadsPipeline />;
      case "conversations":
        return <Conversations />;
      case "vendors":
        return <VendorsPipeline />;
      case "aggressive-outreach":
        return <AggressiveOutreach />;
      case "email-marketing":
        return <EmailMarketingV2 />;
      case "quick-email":
        return <QuickEmail />;
      case "fleet":
        return <FleetCommand />;
      case "event-leads":
        return <EventLeads />;
      case "referrals":
        return <ReferralLeads />;
      case "dossiers":
        return <LeadResearchPipeline />;
      case "ads":
        return <AdPerformance />;
      case "health":
        return <SystemHealth />;
      case "beta-research":
        return <BetaResearch />;
      case "entrepreneur":
        return <EntrepreneurLab />;
      case "snapshots":
        return <SnapshotsPanel />;
      case "settings":
        return <SettingsPage />;
    }
  };

  return (
    <div className="flex h-screen bg-background">
      <Sidebar active={page} onNavigate={setPage} />
      <div className="flex-1 flex flex-col overflow-hidden">
        <FleetStatusBanner />
        {renderPage()}
      </div>
    </div>
  );
}
