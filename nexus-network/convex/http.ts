import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { api } from "./_generated/api";

const http = httpRouter();

// Webhook receiver — FastAPI backend posts new leads here
http.route({
  path: "/sync/lead",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const body = await request.json();

    const leadId = await ctx.runMutation(api.leads.create, {
      firstName: body.first_name || "",
      lastName: body.last_name || "",
      email: body.email || "",
      phone: body.phone || "",
      source: body.source || "webhook",
      eventType: body.event_type || "",
      eventDate: body.event_date || "",
      eventCity: body.event_city || "",
      guestCount: body.guest_count || 0,
      notes: body.notes || "",
    });

    return new Response(JSON.stringify({ ok: true, convex_id: leadId }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }),
});

// Agent activity logging
http.route({
  path: "/sync/agent-activity",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    const body = await request.json();

    await ctx.runMutation(api.agentActivity.log, {
      agentId: body.agent_id,
      action: body.action,
      details: body.details || "",
      result: body.result || "",
    });

    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }),
});

export default http;
