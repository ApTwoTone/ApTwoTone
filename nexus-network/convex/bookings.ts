import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

export const list = query({
  handler: async (ctx) => {
    return await ctx.db
      .query("bookings")
      .withIndex("by_date")
      .order("desc")
      .collect();
  },
});

export const upcoming = query({
  handler: async (ctx) => {
    const today = new Date().toISOString().split("T")[0];
    const all = await ctx.db
      .query("bookings")
      .withIndex("by_date")
      .order("asc")
      .collect();
    return all.filter((b) => b.eventDate >= today && b.status === "confirmed");
  },
});

export const create = mutation({
  args: {
    leadId: v.optional(v.id("leads")),
    eventName: v.string(),
    eventDate: v.string(),
    eventEndDate: v.optional(v.string()),
    location: v.string(),
    price: v.number(),
    notes: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("bookings", {
      leadId: args.leadId,
      eventName: args.eventName,
      eventDate: args.eventDate,
      eventEndDate: args.eventEndDate ?? "",
      location: args.location,
      status: "confirmed",
      calendarEventId: "",
      price: args.price,
      notes: args.notes ?? "",
    });
  },
});
