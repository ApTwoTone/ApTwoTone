import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

export const list = query({
  args: {
    status: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    if (args.status) {
      return await ctx.db
        .query("quotes")
        .withIndex("by_status", (q) => q.eq("status", args.status!))
        .order("desc")
        .collect();
    }
    return await ctx.db.query("quotes").order("desc").collect();
  },
});

export const byLead = query({
  args: { leadId: v.id("leads") },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("quotes")
      .withIndex("by_lead", (q) => q.eq("leadId", args.leadId))
      .order("desc")
      .collect();
  },
});

export const create = mutation({
  args: {
    leadId: v.id("leads"),
    quoteAmount: v.number(),
    tier: v.number(),
    distanceMiles: v.number(),
    eventType: v.string(),
    eventDate: v.string(),
    eventCity: v.string(),
    guestCount: v.number(),
    customerMessage: v.string(),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("quotes", {
      ...args,
      telegramMessageId: "",
      status: "pending_approval",
      urgentPingSent: false,
    });
  },
});

export const updateStatus = mutation({
  args: {
    id: v.id("quotes"),
    status: v.string(),
    sentAt: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const patch: Record<string, string> = { status: args.status };
    if (args.sentAt) patch.sentAt = args.sentAt;
    await ctx.db.patch(args.id, patch);
  },
});
