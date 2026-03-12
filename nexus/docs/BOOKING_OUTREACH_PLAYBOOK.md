# Booking Outreach Playbook

## Goal

Get bookings faster by focusing on direct-booking partners first instead of broad cold-email volume.

## Tomorrow Priority

Facebook lead ads and fast lead follow-up are the top operating priority.

Current live benchmark:

- Spend: $59.11
- Leads: 9
- Cost per lead: $6.57

Use these guardrails:

- Great: under $5 CPL
- Workable: $5 to $8 CPL
- Investigate immediately: above $8 CPL
- Pause and rebuild: above $12 CPL after at least 8 to 10 leads

Tomorrow's order of operations:

1. Review every Facebook lead from the last 7 days and mark each one qualified, weak, or spam.
2. Call or text every qualified lead within 5 minutes when a new lead comes in.
3. Audit the current lead form and ad creative for missing qualification questions.
4. Send follow-up emails only to qualified leads or referral partners tied to outdoor-event demand.
5. Cut any ad sets or audiences producing weak leads with no event date, no location, or no phone pickup.

## Good Lead Vs Bad Lead

Good lead signals:

- Outdoor or private-property event
- Wedding, quinceanera, party, festival, filming, or overflow restroom need
- Event date is real and within the next 1 to 6 months
- Service area is LA, SFV, Ventura County, Santa Clarita, or nearby profitable zones
- Guest count is large enough to justify a luxury restroom trailer
- Lead gives a real phone number and answers or replies
- Venue has limited bathrooms, no bathrooms, or bathroom distance/capacity issues

Bad lead signals:

- Indoor venue with permanent restrooms already sufficient
- Outside the target service area
- No date, no city, no event type, or fake contact info
- Tiny backyard event with no budget and low urgency
- Duplicate, spam, vendor solicitation, or "just browsing" with no decision-maker

## Data Every Lead Must Have

The system should capture these on every Facebook lead and every email/outreach lead:

- Event type
- Event date
- Event city
- Guest count
- Venue type: private property, venue, park, ranch, beach, lot, film site
- Indoor or outdoor
- Bathroom situation: none, limited, overflow needed, unknown
- Contact name
- Best phone and email
- Decision-maker status
- Budget signal: premium, normal, price-sensitive, unknown
- Urgency: same week, this month, this quarter, future

If those fields are missing, the lead is not ready for serious follow-up and should be re-qualified fast.

## What Was Wrong

- Too many emails were going to generic inboxes with weak buyer intent.
- The live batch sender was not reusing the stronger lead-quality checks already in Nexus.
- First-touch emails were too pitch-heavy and not direct enough for a quick reply.
- Planner-heavy outreach was soaking up volume that should have gone to venues, banquet halls, and rental partners.

## New Priority Order

Call or email in this order:

1. Wedding venues, event venues, banquet halls, and quinceanera venues
2. Party rentals and tent rentals
3. Catering companies with outdoor-event volume
4. Event planners and wedding planners only when they have a direct phone or named contact

Geographic focus:

- Moorpark
- Simi Valley
- Thousand Oaks
- Westlake Village
- Calabasas
- Woodland Hills
- Tarzana
- Encino
- Sherman Oaks
- Studio City
- North Hollywood
- Burbank
- Glendale
- Northridge
- Van Nuys
- Reseda
- San Fernando
- Greater LA

## Call Script

### Venues and banquet halls

Use this opener:

> Hi, I am calling on behalf of Zoar Bathroom Rentals. Quick question: do you already have a primary restroom trailer vendor for outdoor events or overflow bathroom capacity?

If they say yes:

> Perfect. We would love to be the backup or secondary option for dates when the primary is booked or the event footprint stretches restroom capacity.

If they say no:

> Great. We can be the preferred restroom trailer option for outdoor events, overflow guest counts, or layouts where the permanent bathrooms are not enough.

Close with:

> Who is the right person to send a one-page overview to?

### Party rentals, tent rentals, and catering

Use this opener:

> Hi, I am calling on behalf of Zoar Bathroom Rentals. Do your outdoor-event clients ever ask your team about restroom trailers?

If yes:

> We would like to be the restroom partner your team can call first when that comes up.

Close with:

> Should I send over the short partner overview so your team has it ready?

### Planners

Use this opener:

> Hi, I am calling on behalf of Zoar Bathroom Rentals. Quick question: do you already have a restroom trailer vendor you trust for outdoor or private-property events?

If yes:

> Great. We would like to be the backup option for peak dates or overflow situations.

If no:

> Great. We can be your preferred restroom vendor for those dates.

## Email Rule

First email should ask a yes-or-no partner question. Do not lead with features or referral money.

Good first-touch subjects:

- Backup restroom vendor for outdoor events
- Quick vendor list question
- Restroom trailer partner for outdoor clients
- Secondary restroom vendor option

## Follow-Up Cadence

1. Day 0: Call first when there is a phone number. If no answer, leave a short voicemail and send the short backup-vendor email.
2. Day 2 or 3: Follow-up email asking who handles vendor lists or partner relationships.
3. Day 5 to 7: Call again and reference the first email.
4. Day 11 to 15: Final close-the-loop email asking whether to send the one-pager or close the file.

Stop follow-ups when:

- They reply
- They ask to stop
- The email bounces
- The number is dead

## Daily Booking Sprint

Use this order every day:

1. Open `output/booking_hunt/sfv_la_call_list_latest.md`
2. Call the top 15 leads
3. Log any correct contact names
4. Send follow-up emails only to the people you actually reached or left voicemail for
5. Move any weak generic-inbox targets out of the active calling block

## Commands

Refresh the call-first lead pack:

```bash
python scripts/generate_booking_hunt.py --count 40
```

Preview who the live sender would email now:

```bash
python scripts/send_outreach_batch.py --dry-run --count 20
```
