#!/usr/bin/env python3
"""
Cold email outreach script for Zoar Bathroom Rentals.
Sends personalized partnership emails to venues, planners, caterers, and production companies.
Uses Gmail SMTP directly (bypasses CRM test_mode).
"""
import smtplib, time, json, sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────
GMAIL = "zoarbathrooms@gmail.com"
APP_PASSWORD = json.load(open(Path.home() / ".nexus" / "config.json")).get("gmail_app_password", "")
FROM_NAME = "Zoar Bathroom Rentals"
DELAY_BETWEEN_EMAILS = 25  # seconds between sends to avoid Gmail throttling

# ── Contacts ────────────────────────────────────────────────────────────
contacts = [
    # VENUES
    {"name": "Calamigos Ranch", "contact": "there", "email": "events@calamigos.com", "category": "venue", "note": "130-acre ranch/vineyard wedding venue in Malibu Wine Country"},
    {"name": "Hummingbird Nest Ranch", "contact": "Erica", "email": "erica@hbnest.com", "category": "venue", "note": "Multi-venue ranch estate in Simi Valley"},
    {"name": "Rancho Las Lomas", "contact": "there", "email": "info@rancholaslomas.com", "category": "venue", "note": "21-acre outdoor estate wedding venue in Silverado"},
    {"name": "Gerry Ranch", "contact": "there", "email": "info@gerryranch.com", "category": "venue", "note": "38-acre working avocado farm wedding venue in Camarillo"},
    {"name": "Quail Ranch", "contact": "Joanne", "email": "joanne@quailranchevents.com", "category": "venue", "note": "Private avocado/citrus ranch wedding venue in Simi Valley"},
    {"name": "Malibu Solstice Vineyards", "contact": "there", "email": "concierge@malibusolstice.net", "category": "venue", "note": "Mountaintop vineyard wedding venue in Malibu"},
    {"name": "The 1909", "contact": "there", "email": "info@the1909.com", "category": "venue", "note": "Outdoor canyon wedding venue in Topanga"},
    {"name": "Sunstone Winery & Villa", "contact": "there", "email": "events@sunstonewinery.com", "category": "venue", "note": "Vineyard wedding venue in Santa Ynez"},
    {"name": "Figueroa Mountain Farmhouse", "contact": "there", "email": "info@figueroamountainfarmhouse.com", "category": "venue", "note": "Rustic farmhouse venue in Los Olivos"},
    {"name": "Plated Events at the Ranch", "contact": "there", "email": "info@platedevents.com", "category": "venue", "note": "Outdoor ranch wedding venue in Ventura County"},

    # PLANNERS
    {"name": "Wild Heart Events", "contact": "there", "email": "hello@wildheartevents.com", "category": "planner", "note": "High-end wedding planner in Santa Barbara"},
    {"name": "LVL Weddings & Events", "contact": "there", "email": "info@lvlevents.com", "category": "planner", "note": "Luxury wedding planner serving LA, OC, Santa Barbara"},
    {"name": "Details Details", "contact": "there", "email": "info@aboutdetailsdetails.com", "category": "planner", "note": "Full-service event planner in Irvine"},
    {"name": "Cortney Helaine Events", "contact": "Cortney", "email": "cortney@cortneyhelaine.com", "category": "planner", "note": "Orange County planner specializing in rustic/barn weddings"},
    {"name": "Emily Coyne Events", "contact": "Emily", "email": "hello@emilycoyneevents.com", "category": "planner", "note": "Luxury destination wedding planner serving LA and SoCal"},

    # CATERERS
    {"name": "24 Carrots Catering & Events", "contact": "there", "email": "info@24carrots.com", "category": "caterer", "note": "Full-service outdoor caterer in Costa Mesa"},
    {"name": "Gourmet Celebrations", "contact": "there", "email": "info@gourmetcelebrations.com", "category": "caterer", "note": "Special event caterer serving LA and Orange County"},
    {"name": "TGIS Catering", "contact": "there", "email": "tgis.catering@tgiscatering.com", "category": "caterer", "note": "Outdoor wedding caterer in Long Beach"},
    {"name": "Made by Meg Catering", "contact": "Meg", "email": "info@mbmcatering.com", "category": "caterer", "note": "Chef catering weddings across LA and OC"},
    {"name": "Haute Chefs LA", "contact": "there", "email": "info@hautechefsla.com", "category": "caterer", "note": "Production catering and private events in LA"},
    {"name": "Colette's Catering & Events", "contact": "there", "email": "info@colettesevents.com", "category": "caterer", "note": "Full-service caterer in Fullerton"},

    # FILM/PRODUCTION
    {"name": "Image Locations", "contact": "Paul", "email": "paul@imagelocations.com", "category": "production", "note": "Largest filming location library in LA"},
    {"name": "All Pictures Media", "contact": "Hanif", "email": "support@allpicturesmedia.com", "category": "production", "note": "Film/photo location scouting agency in LA"},
    {"name": "LA Film Locations", "contact": "Monica", "email": "hello@lafilmlocations.com", "category": "production", "note": "Film location representation and management"},
    {"name": "Alpha Film Locations", "contact": "there", "email": "info@alphafilmlocations.com", "category": "production", "note": "LA film locations and production equipment services"},
    {"name": "Hummingbird Nest Ranch (Filming)", "contact": "Dustin", "email": "filming@hbnest.com", "category": "production", "note": "Major filming location ranch in Simi Valley"},
    {"name": "Line 204", "contact": "there", "email": "supplies@line204.com", "category": "production", "note": "Production rentals and location services in LA"},
]

# ── Email Templates by Category ─────────────────────────────────────────

SUBJECT_LINES = {
    "venue":      "Luxury restroom solution for your outdoor events",
    "planner":    "A luxury restroom option for your clients' events",
    "caterer":    "Luxury restroom trailer — partner for your outdoor events",
    "production": "Luxury restroom trailer for your film & production locations",
}

def make_body(c):
    cat = c["category"]
    hi = f"Hi {c['contact']}," if c["contact"] != "there" else "Hi there,"

    if cat == "venue":
        return f"""{hi}

I'm reaching out from Zoar Bathroom Rentals here in Los Angeles. We provide a luxury restroom trailer for weddings and events across Southern California — and I think we could be a great fit for events at {c['name']}.

Our trailer features porcelain flushing toilets, running water, climate control (AC and heat), hardwood floors, LED lighting, and full-length mirrors in every stall. It's a far cry from a standard porta-potty, and our clients consistently tell us it was one of the best decisions they made for their event.

Delivery, setup, and pickup are all included in every rental.

I'd love to explore a referral partnership — we offer a referral fee for every booking that comes through your recommendation, and we can provide your clients with preferred pricing. Happy to send over photos and a video walkthrough so you can see exactly what we offer.

Would you be open to a quick call?

Best,
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""

    elif cat == "planner":
        return f"""{hi}

I'm reaching out from Zoar Bathroom Rentals in Los Angeles. We provide a luxury 4-stall restroom trailer for weddings, corporate events, and private parties across Southern California — and I think we could be a great resource for your clients.

Our trailer features porcelain toilets, running water, AC/heat, hardwood floors, LED lighting, and full-length mirrors. It's the kind of detail that makes outdoor events feel seamless — and our clients consistently say it was one of the best vendor decisions they made.

Delivery, professional setup, and pickup are all included.

I'd love to explore a referral partnership with you:
- We offer a referral fee for every booking through your recommendation
- Preferred pricing for your clients
- Happy to send you photos and a video walkthrough of the trailer

If you're open to it, I'd love to set up a quick call — whatever works best for your schedule.

Best,
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""

    elif cat == "caterer":
        return f"""{hi}

I'm reaching out from Zoar Bathroom Rentals in Los Angeles. We provide a luxury restroom trailer for outdoor weddings and events across Southern California — and since you're regularly working at outdoor venues, I thought a partnership could be a great fit.

Our 4-stall trailer features porcelain flushing toilets, running water, climate control, hardwood floors, LED lighting, and full-length mirrors. Delivery, setup, and pickup are all included.

When you're coordinating outdoor events, restrooms are one of those details clients often overlook until the last minute. We'd love to be the vendor you can recommend — we offer a referral fee for every booking and preferred pricing for your clients.

Happy to send you photos and a quick video walkthrough.

Best,
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""

    elif cat == "production":
        return f"""{hi}

I'm reaching out from Zoar Bathroom Rentals in Los Angeles. We provide a luxury restroom trailer that's a major step up from standard porta-potties on set — and I thought it could be a great option for productions at your locations.

Our 4-stall trailer features porcelain flushing toilets, running water, AC/heat, hardwood floors, LED lighting, and full-length mirrors. It's climate-controlled and fully self-contained — delivery, setup, and pickup all included.

For productions shooting at outdoor or remote locations, having a quality restroom on site makes a real difference for cast and crew comfort. We're competitively priced and can accommodate same-week bookings when available.

Would love to connect and see if there's a fit — happy to send over some photos or jump on a quick call.

Best,
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""

    return ""


# ── Send Logic ──────────────────────────────────────────────────────────

def send_email(to_email, subject, body):
    # OUTBOUND GATE: Kill switch + approval check (cold outreach has NO approval_id → always blocked)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from integrations.messaging import outbound_gate
    gate = outbound_gate("email", to_email, "", body,
                         approval_id=None, code_path="send_cold_outreach.send_email")
    if not gate["ok"]:
        print(f"[BLOCKED] Cold outreach to {to_email} — {gate['reason']}")
        raise RuntimeError(f"BLOCKED: {gate['reason']}")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{FROM_NAME} <{GMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    try:
        server.login(GMAIL, APP_PASSWORD)
        server.sendmail(GMAIL, to_email, msg.as_string())
    finally:
        server.quit()


def main():
    dry_run = "--dry-run" in sys.argv
    log_file = Path(__file__).parent / "outreach_log.json"
    results = []

    total = len(contacts)
    print(f"\n{'='*60}")
    print(f"Zoar Bathroom Rentals — Cold Email Outreach")
    print(f"{'='*60}")
    print(f"Total contacts: {total}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE SEND'}")
    print(f"Delay: {DELAY_BETWEEN_EMAILS}s between emails")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    for i, c in enumerate(contacts, 1):
        subject = SUBJECT_LINES[c["category"]]
        body = make_body(c)

        print(f"[{i}/{total}] {c['name']} ({c['category']})")
        print(f"         → {c['email']}")

        if dry_run:
            print(f"         ✓ DRY RUN — skipped")
            results.append({"name": c["name"], "email": c["email"], "category": c["category"], "status": "dry_run"})
        else:
            try:
                send_email(c["email"], subject, body)
                print(f"         ✓ SENT")
                results.append({"name": c["name"], "email": c["email"], "category": c["category"], "status": "sent", "ts": datetime.now().isoformat()})
            except Exception as e:
                err = str(e)
                print(f"         ✗ FAILED: {err}")
                results.append({"name": c["name"], "email": c["email"], "category": c["category"], "status": "failed", "error": err, "ts": datetime.now().isoformat()})

        # Delay between sends (skip after last one)
        if not dry_run and i < total:
            print(f"         ⏱ waiting {DELAY_BETWEEN_EMAILS}s...")
            time.sleep(DELAY_BETWEEN_EMAILS)

    # Save log
    log_file.write_text(json.dumps(results, indent=2))

    sent = sum(1 for r in results if r["status"] == "sent")
    failed = sum(1 for r in results if r["status"] == "failed")

    print(f"\n{'='*60}")
    print(f"COMPLETE — Sent: {sent} | Failed: {failed} | Total: {total}")
    print(f"Log saved: {log_file}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
