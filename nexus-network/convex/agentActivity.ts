import { query, mutation } from "./_generated/server";
import { v } from "convex/values";

export const listByAgent = query({
  args: { agentId: v.string() },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("agentActivity")
      .withIndex("by_agent", (q) => q.eq("agentId", args.agentId))
      .order("desc")
      .take(50);
  },
});

export const recent = query({
  handler: async (ctx) => {
    return await ctx.db.query("agentActivity").order("desc").take(20);
  },
});

export const log = mutation({
  args: {
    agentId: v.string(),
    action: v.string(),
    details: v.string(),
    result: v.string(),
  },
  handler: async (ctx, args) => {
    return await ctx.db.insert("agentActivity", args);
  },
});
