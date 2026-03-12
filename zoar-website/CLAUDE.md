# Zoar Bathroom Rentals — Website

## Stack
- **Frontend**: Next.js 16 (App Router, TypeScript, Tailwind CSS v4)
- **Auth**: WorkOS AuthKit (`@workos-inc/authkit-nextjs`)
- **Database**: Convex (reactive, real-time)
- **Deploy**: Cloudflare Pages via OpenNext adapter (`@opennextjs/cloudflare`)
- **Domain**: zoarbathroomrentals.com (Cloudflare DNS)

## Architecture
```
src/
  app/           — Next.js App Router pages
  components/    — React components
  middleware.ts  — WorkOS auth middleware
convex/
  schema.ts      — Database schema
  *.ts           — Queries and mutations
```

## Commands
```bash
npm run dev        # Local dev server
npm run build      # Next.js production build
npm run build:cf   # Build for Cloudflare
npm run deploy     # Build + deploy to Cloudflare
npm run typecheck  # TypeScript check
npm run lint       # ESLint
```

## Rules
- All rules from ~/nexus/CLAUDE.md apply here
- Use Convex for all data operations (no raw SQL)
- Use WorkOS AuthKit for all auth (no custom auth)
- Test locally before pushing: `npm run typecheck && npm run lint`
- PR to main triggers Claude review — fix all issues before merge

## Corrections Log
<!-- Append corrections here -->
