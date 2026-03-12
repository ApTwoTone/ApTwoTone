# Nexus Network — Agent Context

## Project Overview
Nexus Network is the command center desktop app for Zoar Bathroom Rentals. Built with Next.js + Tauri, it displays bookings, leads, agents, and ad performance in a single Mac app.

## Tech Stack
- **Frontend**: Next.js 16 with Turbopack HMR
- **Desktop**: Tauri (Mac .dmg)
- **Database**: Convex (real-time subscriptions)
- **Styling**: Tailwind CSS v4
- **Language**: TypeScript 5, React 19

## Directory Structure
```
src/              — Next.js app directory (pages, components)
src-tauri/        — Tauri desktop shell config
convex/           — Convex schema, queries, mutations
public/           — Static assets
```

## Screens
1. **Bookings Dashboard** (main) — calendar, confirmed bookings, pipeline, revenue
2. **Leads Pipeline** — all leads with status, quotes, next action
3. **Agent Control Center** — agent status, tasks, quota usage, logs
4. **Ad Performance** — live Facebook ad metrics
5. **Quote Generator** — address + event type = instant price
6. **Settings** — API keys, notification prefs, agent toggles

## Key Conventions
- No authentication — single-user local app, AuthProvider returns permanent session
- All data via Convex real-time subscriptions
- Tailwind v4 for all styling
- Components use TypeScript + React 19 conventions
- The Tauri app loads from localhost:3000 (Next.js dev server)

## Running
```bash
cd ~/nexus-network && npm run dev        # Next.js dev server (port 3000)
cd ~/nexus-network && npm run tauri:dev  # Tauri desktop app
```

## Business Context
This app serves Kai (operator) as the single interface to manage Zoar Bathroom Rentals — a luxury restroom trailer rental business in San Fernando Valley, LA. The primary metric is bookings (1 every 5 days).
