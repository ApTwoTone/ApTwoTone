"use client";

import { useEffect, useState, useCallback } from "react";
import { fetchApi, postApi, asArray, type Booking, type Lead } from "@/lib/api";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function StatCard({ label, value, sub, color = "text-foreground" }: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  return (
    <div className="bg-card rounded-xl border border-border p-4">
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-muted mt-1">{sub}</p>}
    </div>
  );
}

function getDaysInMonth(year: number, month: number): Date[] {
  const days: Date[] = [];
  const first = new Date(year, month, 1);
  const startPad = first.getDay();
  for (let i = startPad - 1; i >= 0; i--) {
    days.push(new Date(year, month, -i));
  }
  const last = new Date(year, month + 1, 0).getDate();
  for (let d = 1; d <= last; d++) {
    days.push(new Date(year, month, d));
  }
  const remaining = 7 - (days.length % 7);
  if (remaining < 7) {
    for (let i = 1; i <= remaining; i++) {
      days.push(new Date(year, month + 1, i));
    }
  }
  return days;
}

function dateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function parseDate(s: string): Date {
  if (!s) return new Date(0);
  if (/^\d{10}$/.test(s)) return new Date(Number(s) * 1000);
  if (/^\d{13}$/.test(s)) return new Date(Number(s));
  return new Date(s);
}

interface BookingForm {
  customer_name: string;
  event_type: string;
  event_date: string;
  event_city: string;
  guest_count: string;
  phone: string;
  email: string;
}

const EMPTY_FORM: BookingForm = {
  customer_name: "", event_type: "Wedding", event_date: "", event_city: "",
  guest_count: "", phone: "", email: "",
};

export function BookingsCalendar() {
  const [currentMonth, setCurrentMonth] = useState(() => new Date());
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<BookingForm>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const loadData = useCallback(() => {
    Promise.all([
      fetchApi<{ bookings: Booking[] }>("/api/bookings").catch(() => ({ bookings: [] })),
      fetchApi<{ leads: Lead[] }>("/api/crm/leads").catch(() => ({ leads: [] })),
    ]).then(([bookingData, leadData]) => {
      setBookings(asArray(bookingData?.bookings));
      setLeads(asArray(leadData?.leads));
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    loadData();
    const iv = setInterval(loadData, 30000);
    return () => clearInterval(iv);
  }, [loadData]);

  const year = currentMonth.getFullYear();
  const month = currentMonth.getMonth();
  const days = getDaysInMonth(year, month);
  const todayKey = dateKey(new Date());

  const bookingsByDate = new Map<string, Booking[]>();
  for (const b of bookings) {
    const d = dateKey(parseDate(b.event_date));
    if (!bookingsByDate.has(d)) bookingsByDate.set(d, []);
    bookingsByDate.get(d)!.push(b);
  }

  const leadsByDate = new Map<string, Lead[]>();
  for (const l of leads) {
    if (l.event_date && l.status !== "lost" && l.status !== "booked") {
      const d = dateKey(parseDate(l.event_date));
      if (!leadsByDate.has(d)) leadsByDate.set(d, []);
      leadsByDate.get(d)!.push(l);
    }
  }

  const prevMonth = () => setCurrentMonth(new Date(year, month - 1, 1));
  const nextMonth = () => setCurrentMonth(new Date(year, month + 1, 1));
  const goToday = () => setCurrentMonth(new Date());

  const selectedBookings = selectedDate ? bookingsByDate.get(selectedDate) || [] : [];
  const selectedLeads = selectedDate ? leadsByDate.get(selectedDate) || [] : [];

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await postApi("/api/bookings/create", {
        customer_name: form.customer_name,
        event_type: form.event_type,
        event_date: form.event_date,
        event_city: form.event_city,
        guest_count: parseInt(form.guest_count) || 0,
        phone: form.phone,
        email: form.email,
      });
      setShowForm(false);
      setForm(EMPTY_FORM);
      loadData();
    } catch {
      // errors handled by postApi
    }
    setSubmitting(false);
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="animate-spin w-6 h-6 border-2 border-accent border-t-transparent rounded-full" />
      </div>
    );
  }

  const confirmedCount = bookings.filter((b) => b.status === "confirmed" || b.status === "booked").length;
  const pendingQuotes = leads.filter((l) => l.status === "quoted" || l.status === "new" || l.status === "new_lead").length;
  const revenue = bookings.reduce((sum, b) => sum + (b.total_price || 0), 0);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="p-6 pb-0 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold">Bookings Calendar</h2>
            <p className="text-sm text-muted">Trailer availability & scheduling</p>
          </div>
          <button
            onClick={() => { setShowForm(true); setSelectedDate(null); }}
            className="px-4 py-2 bg-accent hover:bg-accent-hover text-white text-sm rounded-lg transition-colors"
          >
            + New Booking
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Confirmed Bookings" value={confirmedCount} color="text-success" />
          <StatCard label="Pending Quotes" value={pendingQuotes} color="text-warning" />
          <StatCard label="Total Revenue" value={`$${revenue.toLocaleString()}`} color="text-accent" />
          <StatCard label="Total Leads" value={leads.length} color="text-foreground" />
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={prevMonth} className="p-2 hover:bg-card-hover rounded-lg transition-colors text-muted hover:text-foreground">
              &larr;
            </button>
            <h3 className="text-lg font-semibold w-48 text-center">
              {MONTHS[month]} {year}
            </h3>
            <button onClick={nextMonth} className="p-2 hover:bg-card-hover rounded-lg transition-colors text-muted hover:text-foreground">
              &rarr;
            </button>
          </div>
          <button onClick={goToday} className="px-3 py-1 text-sm text-muted hover:text-foreground hover:bg-card-hover rounded-lg transition-colors">
            Today
          </button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden p-6 pt-4 gap-4">
        <div className="flex-1 flex flex-col min-w-0">
          <div className="grid grid-cols-7 mb-1">
            {DAYS.map((d) => (
              <div key={d} className="text-center text-xs text-muted py-2 font-medium">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 flex-1 border border-border rounded-lg overflow-hidden">
            {days.map((day, i) => {
              const dk = dateKey(day);
              const isCurrentMonth = day.getMonth() === month;
              const isToday = dk === todayKey;
              const dayBookings = bookingsByDate.get(dk) || [];
              const dayLeads = leadsByDate.get(dk) || [];
              const isSelected = dk === selectedDate;

              return (
                <button
                  key={i}
                  onClick={() => setSelectedDate(dk === selectedDate ? null : dk)}
                  className={`border-b border-r border-border p-1.5 text-left transition-colors min-h-[80px] flex flex-col ${
                    !isCurrentMonth ? "opacity-30" : ""
                  } ${isSelected ? "bg-accent/10" : "hover:bg-card-hover"}`}
                >
                  <span className={`text-xs font-medium inline-flex items-center justify-center w-6 h-6 rounded-full ${
                    isToday ? "bg-accent text-white" : ""
                  }`}>
                    {day.getDate()}
                  </span>
                  <div className="flex flex-col gap-0.5 mt-1 overflow-hidden">
                    {dayBookings.slice(0, 2).map((b) => (
                      <div key={b.id} className="text-[10px] px-1 py-0.5 rounded bg-success/20 text-success truncate">
                        {b.customer_name || b.event_type}
                      </div>
                    ))}
                    {dayLeads.slice(0, 2).map((l) => (
                      <div key={l.id} className="text-[10px] px-1 py-0.5 rounded bg-warning/20 text-warning truncate">
                        {l.name || l.event_type}
                      </div>
                    ))}
                    {(dayBookings.length + dayLeads.length) > 2 && (
                      <span className="text-[9px] text-muted">+{dayBookings.length + dayLeads.length - 2} more</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          <div className="flex items-center gap-6 mt-3">
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-success/20 border border-success/40" />
              <span className="text-xs text-muted">Booked</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded bg-warning/20 border border-warning/40" />
              <span className="text-xs text-muted">Pending Quote</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3 h-3 rounded-full bg-accent" />
              <span className="text-xs text-muted">Today</span>
            </div>
          </div>
        </div>

        {(selectedDate || showForm) && (
          <div className="w-80 shrink-0 bg-card border border-border rounded-xl p-4 overflow-y-auto">
            {showForm ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">New Booking</h3>
                  <button onClick={() => setShowForm(false)} className="text-muted hover:text-foreground text-lg">&times;</button>
                </div>
                <div className="space-y-3">
                  {([
                    ["customer_name", "Customer Name", "text"],
                    ["phone", "Phone", "tel"],
                    ["email", "Email", "email"],
                    ["event_type", "Event Type", "select"],
                    ["event_date", "Event Date", "date"],
                    ["event_city", "Event City", "text"],
                    ["guest_count", "Guest Count", "number"],
                  ] as const).map(([key, label, type]) => (
                    <div key={key}>
                      <label className="text-xs text-muted block mb-1">{label}</label>
                      {type === "select" ? (
                        <select
                          value={form[key]}
                          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                          className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm"
                        >
                          {["Wedding", "Quinceañera", "Corporate", "Backyard Party", "Festival", "Film Production"].map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type={type}
                          value={form[key]}
                          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                          className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm"
                        />
                      )}
                    </div>
                  ))}
                  <button
                    onClick={handleSubmit}
                    disabled={submitting || !form.customer_name || !form.event_date}
                    className="w-full py-2 bg-accent hover:bg-accent-hover text-white text-sm rounded-lg transition-colors disabled:opacity-50"
                  >
                    {submitting ? "Creating..." : "Create Booking"}
                  </button>
                </div>
              </div>
            ) : selectedDate && (
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">
                    {new Date(selectedDate + "T12:00:00").toLocaleDateString("en-US", {
                      weekday: "long", month: "long", day: "numeric",
                    })}
                  </h3>
                  <button onClick={() => setSelectedDate(null)} className="text-muted hover:text-foreground text-lg">&times;</button>
                </div>

                {selectedBookings.length > 0 && (
                  <div>
                    <p className="text-xs text-muted mb-2">Bookings</p>
                    {selectedBookings.map((b) => (
                      <div key={b.id} className="bg-success/10 border border-success/20 rounded-lg p-3 mb-2">
                        <p className="text-sm font-medium">{b.customer_name}</p>
                        <p className="text-xs text-muted">{b.event_type} &middot; {b.event_city}</p>
                        <p className="text-xs text-success mt-1">${b.total_price}</p>
                      </div>
                    ))}
                  </div>
                )}

                {selectedLeads.length > 0 && (
                  <div>
                    <p className="text-xs text-muted mb-2">Pending Leads</p>
                    {selectedLeads.map((l) => (
                      <div key={l.id} className="bg-warning/10 border border-warning/20 rounded-lg p-3 mb-2">
                        <p className="text-sm font-medium">{l.name}</p>
                        <p className="text-xs text-muted">{l.event_type} &middot; {l.event_city}</p>
                        <p className="text-xs text-muted mt-1">Status: {l.status}</p>
                      </div>
                    ))}
                  </div>
                )}

                {selectedBookings.length === 0 && selectedLeads.length === 0 && (
                  <div className="text-center py-8">
                    <p className="text-sm text-muted">No events on this date</p>
                    <button
                      onClick={() => {
                        setForm({ ...EMPTY_FORM, event_date: selectedDate });
                        setShowForm(true);
                      }}
                      className="mt-3 px-3 py-1.5 text-xs bg-accent/15 text-accent rounded-lg hover:bg-accent/25 transition-colors"
                    >
                      + Add Booking
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
