import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // Rental bookings
  bookings: defineTable({
    customerName: v.string(),
    customerEmail: v.string(),
    customerPhone: v.string(),
    eventDate: v.string(),
    eventAddress: v.string(),
    eventCity: v.string(),
    eventZip: v.string(),
    units: v.number(),
    status: v.union(
      v.literal("pending"),
      v.literal("confirmed"),
      v.literal("delivered"),
      v.literal("picked_up"),
      v.literal("cancelled")
    ),
    totalPrice: v.number(),
    notes: v.optional(v.string()),
    createdAt: v.number(),
  })
    .index("by_status", ["status"])
    .index("by_date", ["eventDate"]),

  // Contact form submissions
  contacts: defineTable({
    name: v.string(),
    email: v.string(),
    phone: v.optional(v.string()),
    message: v.string(),
    read: v.boolean(),
    createdAt: v.number(),
  }).index("by_read", ["read"]),
});
