import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // ── Core CRM ──────────────────────────────────────────────────────────────
  leads: defineTable({
    firstName: v.string(),
    lastName: v.string(),
    email: v.string(),
    phone: v.string(),
    carrier: v.string(),
    source: v.string(),
    status: v.string(), // new | contacted | quoted | booked | lost
    eventType: v.string(),
    eventDate: v.string(),
    eventCity: v.string(),
    guestCount: v.number(),
    notes: v.string(),
    followUpCount: v.number(),
    lastContactedAt: v.optional(v.string()),
    lastReplyAt: v.optional(v.string()),
    nextActionAt: v.optional(v.string()),
    initialSmsSent: v.boolean(),
    initialEmailSent: v.boolean(),
    discoveredAt: v.string(),
  })
    .index("by_status", ["status"])
    .index("by_source", ["source"])
    .index("by_event_date", ["eventDate"])
    .searchIndex("search_name", {
      searchField: "firstName",
      filterFields: ["status"],
    }),

  leadMessages: defineTable({
    leadId: v.id("leads"),
    direction: v.string(), // inbound | outbound
    channel: v.string(), // sms | email | telegram
    content: v.string(),
    subject: v.string(),
    status: v.string(), // sent | delivered | failed
    error: v.string(),
    modelUsed: v.string(),
  }).index("by_lead", ["leadId"]),

  // ── Quotes ────────────────────────────────────────────────────────────────
  quotes: defineTable({
    leadId: v.id("leads"),
    quoteAmount: v.number(),
    tier: v.number(),
    distanceMiles: v.number(),
    eventType: v.string(),
    eventDate: v.string(),
    eventCity: v.string(),
    guestCount: v.number(),
    customerMessage: v.string(),
    telegramMessageId: v.string(),
    status: v.string(), // pending_approval | approved | sent | rejected
    sentAt: v.optional(v.string()),
    urgentPingSent: v.boolean(),
  })
    .index("by_lead", ["leadId"])
    .index("by_status", ["status"]),

  // ── Bookings ──────────────────────────────────────────────────────────────
  bookings: defineTable({
    leadId: v.optional(v.id("leads")),
    eventName: v.string(),
    eventDate: v.string(),
    eventEndDate: v.string(),
    location: v.string(),
    status: v.string(), // confirmed | completed | cancelled
    calendarEventId: v.string(),
    price: v.number(),
    notes: v.string(),
  })
    .index("by_date", ["eventDate"])
    .index("by_status", ["status"]),

  // ── Agent Activity ────────────────────────────────────────────────────────
  agentActivity: defineTable({
    agentId: v.string(), // lead-accelerator | fb-lead-hunter | ad-optimizer | instant-quote
    action: v.string(),
    details: v.string(),
    result: v.string(),
  }).index("by_agent", ["agentId"]),

  // ── Ad Performance ────────────────────────────────────────────────────────
  adMetrics: defineTable({
    campaignId: v.string(),
    campaignName: v.string(),
    spend: v.number(),
    impressions: v.number(),
    reach: v.number(),
    clicks: v.number(),
    leads: v.number(),
    cpl: v.number(),
    ctr: v.number(),
    cpc: v.number(),
    period: v.string(), // today | yesterday | 7day_avg
    fetchedAt: v.string(),
  })
    .index("by_campaign", ["campaignId"])
    .index("by_period", ["period"]),

  // ── Facebook Groups ───────────────────────────────────────────────────────
  monitoredGroups: defineTable({
    name: v.string(),
    url: v.string(),
    category: v.string(),
    language: v.string(),
    lastScraped: v.optional(v.string()),
    active: v.boolean(),
  }).index("by_active", ["active"]),
});
