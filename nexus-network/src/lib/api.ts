const API_BASE = "http://localhost:7860";

export async function fetchApi<T>(path: string, timeoutMs = 10000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function postApi<T>(path: string, body: unknown, timeoutMs = 45000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`API POST ${path}: ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function putApi<T>(path: string, body: unknown, timeoutMs = 45000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`API PUT ${path}: ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

export function useSSE<T>(
  path: string,
  onMessage: (data: T) => void,
): () => void {
  const url = `${API_BASE}${path}`;
  const source = new EventSource(url);
  source.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as T;
      onMessage(data);
    } catch {
      // skip malformed events
    }
  };
  return () => source.close();
}

export function asArray<T>(v: unknown): T[] {
  return Array.isArray(v) ? v : [];
}

export interface Lead {
  id: number;
  name: string;
  full_name?: string;
  business_name?: string;
  phone: string;
  email: string;
  source: string;
  status: string;
  booking_status?: string;
  event_type: string;
  event_date: string;
  event_city: string;
  guest_count: number;
  created_at: string;
  discovered_at?: string;
  updated_at?: string;
  date_added?: string;
  notes: string;
}

export interface Booking {
  id: number;
  lead_id: number;
  event_date: string;
  event_city: string;
  event_type: string;
  customer_name: string;
  total_price: number;
  deposit_paid: boolean;
  status: string;
  created_at: string;
}

export interface AgentStatus {
  name: string;
  status: "running" | "stopped" | "error";
  last_run: string;
  next_run: string;
  tasks_completed: number;
  errors: number;
}
