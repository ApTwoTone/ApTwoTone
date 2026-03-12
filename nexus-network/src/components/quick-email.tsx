"use client";

import { useState } from "react";
import { postApi } from "@/lib/api";

// ── Templates (same as zoar-email CLI) ──────────────────────────────────────

const TEMPLATES: Record<string, { label: string; subject: string; body: string; extraFields?: string[] }> = {
  custom: {
    label: "Custom",
    subject: "",
    body: "",
  },
  "follow-up": {
    label: "Follow-up",
    subject: "Zoar Bathroom Rentals — just tried calling!",
    body: `Hi {name},

Thanks for reaching out! I just tried calling but missed you.

To put together a custom quote for the luxury restroom trailer, I just need:
- What type of event?
- Approximate date?
- Location?

Reply here or text me anytime at (424) 235-8979.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com`,
  },
  quote: {
    label: "Quote",
    subject: "Your custom quote from Zoar Bathroom Rentals",
    extraFields: ["event_type", "event_date", "location"],
    body: `Hi {name},

Great chatting with you! Here's the quote for your {event_type}:

Event: {event_type}
Date: {event_date}
Location: {location}

Our luxury restroom trailer includes:
- Climate control (AC and heat)
- Real flushing porcelain toilets
- Running water sinks with mirrors
- Hardwood-style flooring
- Interior lighting
- Delivery, setup, restocking, and pickup

I'll send over some photos separately so you can see the trailer.

Ready to lock in your date? Just reply here or call me at (424) 235-8979.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com`,
  },
  photos: {
    label: "Photos",
    subject: "Photos of our luxury restroom trailer — Zoar Bathroom Rentals",
    body: `Hi {name},

As promised, here are some photos of our luxury restroom trailer!

The trailer features multiple private stalls, each with a real flushing toilet, running water sink, mirror, and climate control. We deliver, set up, and pick up — you don't have to worry about a thing.

Let me know if you have any questions or if you'd like to reserve your date.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com`,
  },
  reminder: {
    label: "Reminder",
    subject: "Quick follow up — Zoar Bathroom Rentals",
    body: `Hi {name},

Just following up on my earlier message about our luxury restroom trailers. Wanted to make sure it didn't get buried in your inbox!

If you're still looking for restroom facilities for your event, I'd love to help. Just need the event type, date, and location to put a quote together.

No pressure at all — just didn't want you to miss out if you're still interested.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com`,
  },
};

// ── Component ───────────────────────────────────────────────────────────────

export function QuickEmail() {
  const [selectedTemplate, setSelectedTemplate] = useState("custom");
  const [toEmail, setToEmail] = useState("");
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" } | null>(null);

  // Quote-specific fields
  const [eventType, setEventType] = useState("");
  const [eventDate, setEventDate] = useState("");
  const [location, setLocation] = useState("");

  const showToast = (msg: string, type: "success" | "error") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 5000);
  };

  const handleTemplateSelect = (key: string) => {
    setSelectedTemplate(key);
    const t = TEMPLATES[key];
    if (key === "custom") {
      setSubject("");
      setBody("");
      return;
    }
    setSubject(t.subject);
    // Replace {name} with current name or placeholder
    const n = name || "there";
    let b = t.body.replace(/{name}/g, n);
    if (key === "quote") {
      b = b.replace(/{event_type}/g, eventType || "event")
           .replace(/{event_date}/g, eventDate || "TBD")
           .replace(/{location}/g, location || "TBD");
    }
    setBody(b);
  };

  // Re-apply name when name field changes and a template is selected
  const handleNameChange = (newName: string) => {
    setName(newName);
    if (selectedTemplate !== "custom") {
      const t = TEMPLATES[selectedTemplate];
      const n = newName || "there";
      let b = t.body.replace(/{name}/g, n);
      if (selectedTemplate === "quote") {
        b = b.replace(/{event_type}/g, eventType || "event")
             .replace(/{event_date}/g, eventDate || "TBD")
             .replace(/{location}/g, location || "TBD");
      }
      setBody(b);
    }
  };

  const handleSend = async () => {
    if (!toEmail || !subject || !body) {
      showToast("Fill in all fields", "error");
      return;
    }
    setSending(true);
    try {
      const res = await postApi<{ ok: boolean; error?: string; to?: string }>(
        "/api/quick-email/send",
        { to: toEmail, subject, body, name },
        30000,
      );
      if (res.ok) {
        showToast(`Sent to ${toEmail}`, "success");
        // Clear form
        setToEmail("");
        setName("");
        setSubject("");
        setBody("");
        setSelectedTemplate("custom");
        setEventType("");
        setEventDate("");
        setLocation("");
      } else {
        showToast(res.error || "Send failed", "error");
      }
    } catch (e) {
      showToast(String(e), "error");
    }
    setSending(false);
  };

  const handleClear = () => {
    setToEmail("");
    setName("");
    setSubject("");
    setBody("");
    setSelectedTemplate("custom");
    setEventType("");
    setEventDate("");
    setLocation("");
  };

  return (
    <div className="flex-1 overflow-auto p-6 space-y-6">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all ${
          toast.type === "success" ? "bg-success/20 text-success border border-success/30" : "bg-danger/20 text-danger border border-danger/30"
        }`}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Quick Email</h1>
        <p className="text-sm text-muted mt-1">Send branded Zoar emails in 30 seconds</p>
      </div>

      <div className="max-w-3xl mx-auto space-y-4">
        {/* Template Picker */}
        <div className="bg-card border border-border rounded-xl p-4">
          <label className="text-xs text-muted block mb-2">Template</label>
          <div className="flex flex-wrap gap-2">
            {Object.entries(TEMPLATES).map(([key, t]) => (
              <button
                key={key}
                onClick={() => handleTemplateSelect(key)}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  selectedTemplate === key
                    ? "bg-accent/15 text-accent border border-accent/30"
                    : "bg-background border border-border text-muted hover:text-foreground hover:bg-card-hover"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* Composer */}
        <div className="bg-card border border-border rounded-xl p-5 space-y-4">
          {/* From */}
          <div>
            <label className="text-xs text-muted block mb-1">From</label>
            <div className="text-sm text-muted bg-background border border-border rounded-md px-3 py-1.5">
              Zoar Bathroom Rentals &lt;zoarbathrooms@gmail.com&gt;
            </div>
          </div>

          {/* To */}
          <div>
            <label className="text-xs text-muted block mb-1">To</label>
            <input
              type="email"
              value={toEmail}
              onChange={(e) => setToEmail(e.target.value)}
              placeholder="lead@example.com"
              className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
            />
          </div>

          {/* Name */}
          <div>
            <label className="text-xs text-muted block mb-1">Recipient&apos;s first name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder="Ernie"
              className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
            />
          </div>

          {/* Quote-specific fields */}
          {selectedTemplate === "quote" && (
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-muted block mb-1">Event type</label>
                <input
                  type="text"
                  value={eventType}
                  onChange={(e) => setEventType(e.target.value)}
                  placeholder="Wedding"
                  className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="text-xs text-muted block mb-1">Event date</label>
                <input
                  type="text"
                  value={eventDate}
                  onChange={(e) => setEventDate(e.target.value)}
                  placeholder="June 15, 2026"
                  className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="text-xs text-muted block mb-1">Location</label>
                <input
                  type="text"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="Burbank, CA"
                  className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
                />
              </div>
            </div>
          )}

          {/* Subject */}
          <div>
            <label className="text-xs text-muted block mb-1">Subject</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Zoar Bathroom Rentals — ..."
              className="w-full bg-background border border-border rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-accent"
            />
          </div>

          {/* Body */}
          <div>
            <label className="text-xs text-muted block mb-1">Body</label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Hi there,&#10;&#10;Thanks for reaching out!..."
              className="w-full bg-background border border-border rounded-md px-3 py-2 text-sm resize-none h-72 focus:outline-none focus:border-accent font-mono text-xs leading-relaxed"
            />
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-between">
          <button
            onClick={handleClear}
            className="px-6 py-3 rounded-xl bg-card border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover transition-colors"
          >
            Clear
          </button>
          <button
            onClick={handleSend}
            disabled={sending || !toEmail || !subject || !body}
            className="px-8 py-3 rounded-xl bg-accent text-white text-sm font-semibold hover:bg-accent/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {sending ? "Sending..." : "Send Email"}
          </button>
        </div>
      </div>
    </div>
  );
}
