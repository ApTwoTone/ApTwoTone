"use client";

import { useState } from "react";

const EVENT_TYPES = [
  "Wedding",
  "Quinceañera",
  "Corporate Event",
  "Backyard Party",
  "Festival / Fair",
  "Film / Production",
  "Other",
];

interface QuoteResult {
  tier: number;
  distance: number;
  base_price: number;
  mileage_surcharge: number;
  total: number;
  deposit: number;
  balance_due: number;
}

export function QuoteGenerator() {
  const [city, setCity] = useState("");
  const [eventType, setEventType] = useState(EVENT_TYPES[0]);
  const [eventDate, setEventDate] = useState("");
  const [guestCount, setGuestCount] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [isVenue, setIsVenue] = useState(false);
  const [quote, setQuote] = useState<QuoteResult | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function generateQuote() {
    if (!city) {
      setError("City is required");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await fetch("http://localhost:7860/api/quote/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          city,
          event_type: eventType.toLowerCase(),
          event_date: eventDate,
          guest_count: parseInt(guestCount) || 0,
          customer_name: customerName,
          is_venue: isVenue,
        }),
      });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const data = await res.json();
      setQuote(data.quote);
      setMessage(data.message || "");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="mb-6">
        <h2 className="text-xl font-bold">Quote Generator</h2>
        <p className="text-sm text-muted mt-1">
          Tier-based pricing from San Fernando, CA 91340
        </p>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Form */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h3 className="text-sm font-semibold mb-4">Quote Details</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted block mb-1">Customer Name</label>
              <input
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                placeholder="e.g. Maria Garcia"
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Event City *</label>
              <input
                value={city}
                onChange={(e) => setCity(e.target.value)}
                placeholder="e.g. Northridge, Pasadena, Long Beach"
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="text-xs text-muted block mb-1">Event Type</label>
              <select
                value={eventType}
                onChange={(e) => setEventType(e.target.value)}
                className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
              >
                {EVENT_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted block mb-1">Event Date</label>
                <input
                  type="date"
                  value={eventDate}
                  onChange={(e) => setEventDate(e.target.value)}
                  className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="text-xs text-muted block mb-1">Guest Count</label>
                <input
                  type="number"
                  value={guestCount}
                  onChange={(e) => setGuestCount(e.target.value)}
                  placeholder="150"
                  className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="venue"
                checked={isVenue}
                onChange={(e) => setIsVenue(e.target.checked)}
                className="rounded"
              />
              <label htmlFor="venue" className="text-sm text-muted">
                Venue event (10% kickback applies)
              </label>
            </div>

            {error && <p className="text-xs text-danger">{error}</p>}

            <button
              onClick={generateQuote}
              disabled={loading}
              className="w-full py-2.5 bg-accent text-white text-sm font-medium rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {loading ? "Calculating..." : "Generate Quote"}
            </button>
          </div>
        </div>

        {/* Result */}
        <div>
          {quote && (
            <div className="bg-card border border-border rounded-xl p-5 mb-4">
              <h3 className="text-sm font-semibold mb-3">Price Breakdown</h3>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted">Tier</span>
                  <span className="font-medium">Tier {quote.tier}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted">Distance</span>
                  <span>{quote.distance.toFixed(1)} miles</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted">Base Price</span>
                  <span>${quote.base_price.toLocaleString()}</span>
                </div>
                {quote.mileage_surcharge > 0 && (
                  <div className="flex justify-between text-sm">
                    <span className="text-muted">Mileage Surcharge</span>
                    <span>${quote.mileage_surcharge.toLocaleString()}</span>
                  </div>
                )}
                <hr className="border-border" />
                <div className="flex justify-between text-base font-bold">
                  <span>Total</span>
                  <span className="text-success">${quote.total.toLocaleString()}</span>
                </div>
                <div className="flex justify-between text-sm text-muted">
                  <span>Deposit (50%)</span>
                  <span>${quote.deposit.toLocaleString()}</span>
                </div>
                <div className="flex justify-between text-sm text-muted">
                  <span>Balance Due</span>
                  <span>${quote.balance_due.toLocaleString()}</span>
                </div>
              </div>
            </div>
          )}

          {message && (
            <div className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold">Customer Message</h3>
                <button
                  onClick={() => navigator.clipboard.writeText(message)}
                  className="text-xs text-accent hover:text-accent-hover"
                >
                  Copy
                </button>
              </div>
              <pre className="text-xs text-muted whitespace-pre-wrap font-sans leading-relaxed">
                {message}
              </pre>
            </div>
          )}
        </div>
      </div>

      {/* Pricing tiers reference */}
      <div className="mt-6 bg-card border border-border rounded-xl p-5">
        <h3 className="text-sm font-semibold mb-3">Pricing Tiers (from San Fernando, CA 91340)</h3>
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-background rounded-lg p-3 border border-border">
            <p className="text-sm font-semibold text-success">Tier 1: 0-10 mi</p>
            <p className="text-lg font-bold">$1,000</p>
            <p className="text-xs text-muted">SFV, Burbank, Glendale, etc.</p>
          </div>
          <div className="bg-background rounded-lg p-3 border border-border">
            <p className="text-sm font-semibold text-warning">Tier 2: 10-20 mi</p>
            <p className="text-lg font-bold">$1,200 + mileage</p>
            <p className="text-xs text-muted">Pasadena, Hollywood, DTLA, etc.</p>
          </div>
          <div className="bg-background rounded-lg p-3 border border-border">
            <p className="text-sm font-semibold text-danger">Tier 3: 20+ mi</p>
            <p className="text-lg font-bold">$1,500 + mileage</p>
            <p className="text-xs text-muted">Long Beach, Malibu, Anaheim, etc.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
