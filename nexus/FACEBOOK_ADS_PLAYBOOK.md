# Facebook Ads Playbook — Zoar Bathroom Rentals

**Goal:** Generate bookings via Facebook Lead Gen ads at $10/day.
**Campaign Config:** `~/.nexus/fb_ads/campaign_config.json`
**Script:** `scripts/create_lead_gen_campaign.py`

---

## Campaign Architecture

```
Campaign: "Zoar Lead Gen — SFV Weddings Mar 2026"
  Objective: OUTCOME_LEADS (Lead Generation)
  Budget: $10/day (CBO)
  Bid: Lowest Cost
  |
  Ad Set: "SFV — Events + Engaged Women 24-45"
    Location: 25mi radius from Van Nuys (34.1867, -118.4490)
    Age: 24-45, Women
    Languages: English + Spanish
    Interests: Wedding planning, Outdoor wedding, Event planning,
               Party planning, Bridal shower, Wedding venues
    Placements: FB Feed, IG Feed, FB Stories, IG Stories
    Optimization: LEAD_GENERATION
    |
    Ad A: "Wedding Contrast" — emotional hook, porta-potty comparison
    Ad B: "Social Proof" — feature-focused, guests love it
    Ad C: "Urgency/Seasonal" — spring dates filling up
    |
    Lead Form: "Zoar — Free Quote (Instant Form)"
      Fields: Name (auto-fill), Phone (auto-fill), Email (auto-fill), Event Type (dropdown)
      Thank-you: "We'll reach out shortly" + Call (424) 235-8979
```

## Lead Flow

```
Lead submits Instant Form on FB/IG
  -> Facebook webhook fires to /api/crm/webhook/facebook (HMAC verified)
  -> Lead inserted into CRM (leads table, source='facebook_ad')
  -> Lead Accelerator (Agent 1) generates personalized quote within 60 seconds
  -> Telegram notification to Kai with SEND / EDIT / SKIP buttons
  -> If approved: SMS + email sent to lead via outbound_gate() (Rule 0 enforced)
  -> 15-min polling fallback catches any missed webhook leads
```

## Ad Copy Rules

**APPROVED:**
- "Starting at $999" or "Starting at $1,000"
- "Delivery and setup included"
- "Pricing varies by location"
- "Get a free quote"

**NEVER USE:**
- "All-inclusive"
- "No hidden fees"
- Specific prices above $1,000 in ad copy
- Film, production, grip, construction references
- Individual names (Carlos, Kai) — always "Zoar Bathroom Rentals"

## KPI Targets

| Metric | Target | Acceptable | Alert |
|--------|--------|-----------|-------|
| CPL | < $8 | $8-$20 | > $20 |
| CTR | > 1.5% | 0.8-1.5% | < 0.5% |
| CPM | < $25 | $25-$40 | > $50 |
| Form conversion | > 15% | 8-15% | < 5% |
| Frequency (7-day) | < 2.0 | 2.0-3.0 | > 3.5 |

## 7-Day No-Touch Rule

After launch, DO NOT edit the campaign for 7 days. Meta's GEM prediction model needs calibration time. Edits reset the learning phase. Exceptions: fixing actual errors (ad rejected, form broken, wrong budget).

## Optimization Schedule

**Day 1-6:** Monitor only. No changes.

**Day 7 — First optimization:**
- If CPL < $20 and leads > 0: Keep running. Consider +20% budget.
- If impressions > 3K, clicks > 30, leads = 0: Lead form problem. Check form works.
- If impressions > 3K, CTR < 0.5%: Creative problem. Ads not stopping scroll.
- If impressions < 500 after 3 days: Delivery problem. Check approval/targeting.
- If one ad gets 80%+ spend: Normal — Andromeda picked winner. Pause losers, create 2 new concepts.

**Day 14 — Creative refresh:**
- Add 2-3 new ad concepts (different angles, not text swaps)
- Pause worst performer
- Test video if budget allows

**Day 21 — Scaling decision:**
- If CPL stable and < $20: Increase to $15/day
- If CPL rising: Hold at $10 and refresh creative

**Day 30 — Full review:**
- Total spend, total leads, bookings, ROI
- Which ad won? What angle resonated?
- Audience insights: what demographics converted?
- Next month strategy

## Creative Refresh Cycle

Andromeda needs genuinely different creative every 2-3 weeks. "Different" means:
- Different image/setting (not same photo with different crop)
- Different hook (first 3 words of primary text)
- Different emotional angle (fear, aspiration, urgency, social proof)
- Different format if possible (static -> carousel -> video)

**Fatigue signals:** CTR declining over 3+ days, frequency > 2.5, CPL rising 20%+

## Scaling Strategy

```
$10/day (Week 1-2): Prove concept. Get first leads.
$15/day (Week 3):   If CPL < $20, increase 50%.
$20/day (Week 4):   If still performing, consider second ad set.
$30/day (Month 2):  Split into 2 ad sets if budget allows.
$50/day (Month 2-3): After first booking proves ROI.
```

**ROI math:** One rental = $1,000-$2,500. At $10/day = $300/month. One booking pays for the entire month of ads 3-8x over.

## Budget Rules

- **Hard cap: $100/week** ($14.28/day max). Current spend: $10/day = $70/week.
- Alert if daily spend exceeds $12 (120% of budget)
- Never run two ad sets simultaneously at $10/day — fragments the budget
- CBO (Campaign Budget Optimization) handles allocation across ads

## Monitoring (core/ad_monitor.py)

- Lead polling: every 15 minutes (webhook fallback)
- Alerts: CPL > $8, no leads in 24h, spend > $12/day, reach drop 40%+
- Daily report: 8 AM PT via Telegram
- Weekly deep-dive: Sunday 10 AM PT via Telegram

## Red Alerts (Telegram immediately)

- Daily spend exceeds $12
- Ad rejected by Meta
- Zero impressions for 6+ hours
- Lead form broken (no submissions coming through)
- CPL exceeds $40 on any single day

## Prohibited Content

- Film, TV, production, grip, lighting — NEVER mention
- Construction — not targeting
- Zoar Grip and Lighting — NEVER reference
- Individual names in ads
- Prices above $1,000 in ad copy
