import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

// ── Queries ─────────────────────────────────────────────────────────────────

export const list = query({
  args: {
    status: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    if (args.status) {
      return await ctx.db
        .query("leads")
        .withIndex("by_status", (q) => q.eq("status", args.status!))
        .order("desc")
        .collect();
    }
    return await ctx.db.query("leads").order("desc").collect();
  },
});

export const get = query({
  args: { id: v.id("leads") },
  handler: async (ctx, args) => {
    return await ctx.db.get(args.id);
  },
});

export const stats = query({
  handler: async (ctx) => {
    const allLeads = await ctx.db.query("leads").collect();
    const now = new Date();

    const totalBooked = allLeads.filter((l) => l.status === "booked").length;
    const quotesPending = allLeads.filter((l) => l.status === "quoted").length;
    const newLeads = allLeads.filter((l) => l.status === "new").length;

    // Days since last booking
    const bookedLeads = allLeads
      .filter((l) => l.status === "booked" && l.eventDate)
      .sort((a, b) => b.eventDate.localeCompare(a.eventDate));

    let daysSinceLastBooking = 0;
    if (bookedLeads.length > 0) {
      const lastDate = new Date(bookedLeads[0].eventDate);
      daysSinceLastBooking = Math.floor(
        (now.getTime() - lastDate.getTime()) / (1000 * 60 * 60 * 24)
      );
    }

    return {
      totalLeads: allLeads.length,
      totalBooked,
      quotesPending,
      newLeads,
      daysSinceLastBooking,
    };
  },
});

// ── Mutations ───────────────────────────────────────────────────────────────

export const create = mutation({
  args: {
    firstName: v.string(),
    lastName: v.string(),
    email: v.string(),
    phone: v.string(),
    source: v.string(),
    eventType: v.string(),
    eventDate: v.string(),
    eventCity: v.string(),
    guestCount: v.number(),
    notes: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("leads", {
      ...args,
      carrier: "tmobile",
      status: "new",
      notes: args.notes ?? "",
      followUpCount: 0,
      initialSmsSent: false,
      initialEmailSent: false,
      discoveredAt: new Date().toISOString(),
    });
  },
});

export const updateStatus = mutation({
  args: {
    id: v.id("leads"),
    status: v.string(),
  },
  handler: async (ctx, args) => {
    await ctx.db.patch(args.id, { status: args.status });
  },
});
