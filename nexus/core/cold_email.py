"""
B2B Cold Email Generator — Three category-based templates.

Templates:
  1. REFERRAL — planners, photographers, caterers, DJs, florists, rentals
  2. VENUE   — wedding/event/corporate venues, wineries, churches
  3. COMMERCIAL — construction, film production, festivals

CRITICAL RULES:
- NEVER include dollar amounts ($1,000, $1,200, etc.)
- NEVER say "all-inclusive"
- NEVER include specific trailer specs (stall count, AC, mirrors, etc.)
- Signature is brand-only: "Zoar Bathroom Rentals" — no personal names
- CAN-SPAM footer on every email
"""
import re
from core.email_templates import (
    _base_template, initial_outreach, follow_up,
    BRAND_TEXT, BRAND_GOLD, BRAND_MUTED,
)

# ── Forbidden patterns (pricing/all-inclusive/specs) ──────────────────────────
_FORBIDDEN = [
    re.compile(r"\$\s?\d"),                        # Any dollar amount
    re.compile(r"all[- ]inclusive", re.IGNORECASE), # "all-inclusive" or "all inclusive"
    re.compile(r"\$\d[\d,]*"),                      # Dollar with comma-formatted numbers
    re.compile(r"Escobar", re.IGNORECASE),          # Last name must never appear
    re.compile(r"4-stall", re.IGNORECASE),          # No trailer specs
]

CAN_SPAM_FOOTER = (
    "\n\n"
    "Zoar Bathroom Rentals | San Fernando Valley, CA\n"
    "If you do not wish to receive future emails, reply with "
    "\"unsubscribe\" and we will remove you immediately."
)

CAN_SPAM_HTML = """
<tr>
<td style="padding:16px 32px;border-top:1px solid #e0e0e0;font-size:11px;color:#999999;text-align:center;line-height:1.5;">
Zoar Bathroom Rentals | San Fernando Valley, CA<br>
If you do not wish to receive future emails, reply with "unsubscribe" and we will remove you immediately.
</td>
</tr>
"""

SIGNATURE = (
    "Zoar Bathroom Rentals\n"
    "(424) 235-8979\n"
    "zoarbathroomrental.com"
)

SIGNATURE_HTML = f"""<p style="margin:24px 0 4px;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Zoar Bathroom Rentals<br>
<a href="tel:+14242358979" style="color:{BRAND_GOLD};text-decoration:none;">(424) 235-8979</a><br>
<a href="https://zoarbathroomrental.com" style="color:{BRAND_GOLD};text-decoration:none;">zoarbathroomrental.com</a>
</p>"""

# ── Category-specific email templates ─────────────────────────────────────────
# Each category has TWO variants to avoid spam filter detection from identical
# bulk sends. generate_cold_email() picks variant based on vendor_id hash.

_WEDDING_PLANNER_BODY_A = (
    "Quick question: do you already have a restroom trailer vendor you trust "
    "for outdoor or private-property weddings?\n\n"
    "Zoar Bathroom Rentals covers LA, SFV, and nearby counties. We handle "
    "delivery, setup, pickup, and fast availability checks when venue "
    "restrooms are limited or the usual provider is booked.\n\n"
    "If you are open to a backup option, reply \"backup\" and I will send a "
    "one-page overview."
)

_WEDDING_PLANNER_BODY_B = (
    "For outdoor weddings, restroom coverage becomes urgent fast when the "
    "guest count grows or the venue bathrooms are too far from the event.\n\n"
    "We are Zoar Bathroom Rentals. We support planners in LA and SFV with a "
    "clean yes-or-no availability check, straightforward quoting, and full "
    "onsite setup.\n\n"
    "If you already have a primary restroom vendor, we would like to be your "
    "secondary option for overflow dates. Open to that?"
)

_VENUE_BODY_A = (
    "Quick question for your events team: do you already have a restroom "
    "trailer vendor for outdoor dates or overflow capacity?\n\n"
    "Zoar Bathroom Rentals works across LA and SFV. We are a strong backup "
    "option when on-site bathrooms are not enough, the event footprint is "
    "spread out, or the primary vendor is booked.\n\n"
    "If you are open to a secondary vendor option, reply \"vendor info\" and "
    "I will send a short one-page overview."
)

_VENUE_BODY_B = (
    "We are reaching out because venues sometimes need a backup restroom "
    "partner for private events, peak Saturdays, or layouts where guest "
    "bathrooms are not enough.\n\n"
    "Zoar Bathroom Rentals handles the full restroom trailer side from quote "
    "to pickup, and we move quickly when a client needs an answer.\n\n"
    "If you already have a primary vendor, we would like to be considered as "
    "a backup. If you do not, we would be happy to send a short intro."
)

_QUINCEANERA_BODY_A = (
    "Quincea\u00f1eras are such a special celebration \u2014 and so "
    "many happen in backyards, parks, or spaces where restroom access "
    "is limited.\n\n"
    "Zoar Bathroom Rentals provides luxury restroom trailers right "
    "here in the San Fernando Valley. Air conditioning, flushing "
    "toilets, mirrors, running water \u2014 a big upgrade over "
    "porta-potties.\n\n"
    "We deliver, set up, and pick everything up. Would you be open "
    "to referring us when your clients need facilities? We offer a "
    "referral fee for every booking."
)

_QUINCEANERA_BODY_B = (
    "When families plan a quincea\u00f1era at a backyard or park, the "
    "restroom situation is always a concern.\n\n"
    "We're Zoar Bathroom Rentals in the Valley. Our luxury trailers "
    "have climate control, real plumbing, and premium finishes \u2014 "
    "your clients' guests will be comfortable all night.\n\n"
    "We handle everything (delivery, setup, teardown) and offer "
    "referral fees for event professionals. Can I send a couple photos?"
)

_PARTY_RENTAL_BODY_A = (
    "A quick partnership idea: when your outdoor clients ask about restroom "
    "trailers, we can take that piece off your plate.\n\n"
    "Zoar Bathroom Rentals covers LA and SFV with fast quoting, delivery, "
    "setup, and pickup. That makes it easy for your team to say yes without "
    "adding another coordination headache.\n\n"
    "If you want a restroom partner for tent, table, or backyard event "
    "packages, reply \"partner\" and I will send a short overview."
)

_PARTY_RENTAL_BODY_B = (
    "Your clients booking outdoor rentals usually need a restroom answer at "
    "the same time they are choosing tents, tables, and layout.\n\n"
    "Zoar Bathroom Rentals can be your go-to restroom trailer partner for LA "
    "and SFV events. We quote quickly and handle all onsite logistics.\n\n"
    "Open to being our preferred rental partner on restroom-related jobs and "
    "letting us support yours when clients ask?"
)

_CONSTRUCTION_BODY_A = (
    "For job sites with client visits, model home showings, or "
    "office trailers where standard porta-potties don't cut it "
    "\u2014 our units are a professional upgrade.\n\n"
    "Zoar Bathroom Rentals provides luxury restroom trailers in the "
    "San Fernando Valley. Climate controlled, flushing toilets, "
    "running water. We handle delivery, setup, and pickup.\n\n"
    "Interested in hearing more? Happy to send specs and pricing."
)

_CONSTRUCTION_BODY_B = (
    "When you need a step up from standard porta-potties for client-"
    "facing job sites, our luxury trailers are the answer.\n\n"
    "We're Zoar Bathroom Rentals in the Valley. Climate controlled "
    "units with real plumbing \u2014 delivery and pickup handled.\n\n"
    "Can I send you some details?"
)

_REFERRAL_PARTNERSHIP_BODY_A = (
    "We're reaching out to a select group of event professionals "
    "in the San Fernando Valley about a partnership opportunity.\n\n"
    "Zoar Bathroom Rentals provides luxury restroom trailers for "
    "weddings, quincea\u00f1eras, and outdoor events \u2014 climate controlled, "
    "real plumbing, full delivery and setup handled by us.\n\n"
    "We'd love to have you as a referral partner. When you refer a "
    "client who books with us, you earn a referral commission for "
    "every confirmed event. No contracts, no obligations \u2014 just a "
    "simple way to offer your clients a premium solution and get "
    "rewarded for it.\n\n"
    "Interested? I can send over details and a few photos of our trailer."
)

_REFERRAL_PARTNERSHIP_BODY_B = (
    "Quick note \u2014 we're building a referral network of trusted "
    "{category_phrase} in the Valley and Greater LA area.\n\n"
    "Zoar Bathroom Rentals provides luxury restroom trailers with "
    "climate control, running water, and premium finishes. We handle "
    "everything \u2014 delivery, setup, and pickup.\n\n"
    "If you refer a client who books, you earn a referral commission "
    "on every confirmed rental. It's a straightforward partnership \u2014 "
    "you recommend a solution your clients need, and we take care "
    "of the rest.\n\n"
    "Would you be open to learning more? Happy to send photos and details."
)

_FOLLOWUP_1_BODY = (
    "Just following up on my note from last week. We provide luxury "
    "restroom trailers for outdoor events in the Valley \u2014 would love "
    "to be a resource for your clients when they need facilities.\n\n"
    "No pressure at all \u2014 just wanted to make sure it didn't get "
    "buried. Happy to answer any questions."
)

_FOLLOWUP_2_BODY = (
    "Last note from me \u2014 I know you're busy. If your clients ever "
    "need luxury restroom trailers for outdoor events, we're here in "
    "the Valley and ready to help.\n\n"
    "Feel free to save our info for when the need comes up. Wishing "
    "you a great season."
)

# Each category maps to [variant_a, variant_b]
_TEMPLATE_BODIES_AB = {
    "wedding_planner": [_WEDDING_PLANNER_BODY_A, _WEDDING_PLANNER_BODY_B],
    "venue": [_VENUE_BODY_A, _VENUE_BODY_B],
    "quinceanera": [_QUINCEANERA_BODY_A, _QUINCEANERA_BODY_B],
    "party_rental": [_PARTY_RENTAL_BODY_A, _PARTY_RENTAL_BODY_B],
    "construction": [_CONSTRUCTION_BODY_A, _CONSTRUCTION_BODY_B],
    "referral_partnership": [_REFERRAL_PARTNERSHIP_BODY_A, _REFERRAL_PARTNERSHIP_BODY_B],
    "followup_1": [_FOLLOWUP_1_BODY, _FOLLOWUP_1_BODY],
    "followup_2": [_FOLLOWUP_2_BODY, _FOLLOWUP_2_BODY],
}

# Legacy single-body dict for backward compat
_TEMPLATE_BODIES = {k: v[0] for k, v in _TEMPLATE_BODIES_AB.items()}

# Subject lines — now personalized with {business_name}
_TEMPLATE_SUBJECTS_AB = {
    "wedding_planner": [
        "Backup restroom vendor for {business_name}",
        "Outdoor event restroom partner for {business_name}",
    ],
    "venue": [
        "Backup restroom vendor for {business_name}",
        "Quick vendor list question for {business_name}",
    ],
    "quinceanera": [
        "Ba\u00f1os de lujo \u2014 pregunta r\u00e1pida, {business_name}",
        "Restroom referral for {business_name}?",
    ],
    "party_rental": [
        "Restroom trailer partner for {business_name}",
        "Outdoor event add-on for {business_name}",
    ],
    "construction": [
        "Upgraded restroom trailers for {business_name}",
        "Quick question, {business_name}",
    ],
    "referral_partnership": [
        "Referral partnership opportunity, {business_name}",
        "Partner with Zoar Bathroom Rentals, {business_name}?",
    ],
    "followup_1": [
        "Re: {original_subject}",
        "Re: {original_subject}",
    ],
    "followup_2": [
        "One last note \u2014 Zoar Bathroom Rentals",
        "One last note \u2014 Zoar Bathroom Rentals",
    ],
}

# Legacy single-subject dict
_TEMPLATE_SUBJECTS = {k: v[0] for k, v in _TEMPLATE_SUBJECTS_AB.items()}

# ── Category → template mapping ──────────────────────────────────────────────

CATEGORY_TO_TEMPLATE = {
    # WEDDING PLANNER — planners, photographers, caterers, DJs, florists
    "event_planner": "wedding_planner",
    "wedding_planner": "wedding_planner",
    "corporate_event_planner": "wedding_planner",
    "catering": "wedding_planner",
    "caterer": "wedding_planner",
    "photography": "wedding_planner",
    "photographer": "wedding_planner",
    "videography": "wedding_planner",
    "florist": "wedding_planner",
    "dj_entertainment": "wedding_planner",
    "dj": "wedding_planner",
    "live_band": "wedding_planner",
    "bartending_mobile_bar": "wedding_planner",
    "bartending": "wedding_planner",
    "officiant": "wedding_planner",
    "hair_makeup": "wedding_planner",
    "wedding_cake": "wedding_planner",
    "event_decorator": "wedding_planner",
    "decorator": "wedding_planner",
    # PARTY RENTAL — rental companies, booths, entertainment
    "party_rental": "party_rental",
    "bounce_house": "party_rental",
    "photo_booth": "party_rental",
    "tent_rental": "party_rental",
    "lighting_design": "party_rental",
    "lighting": "party_rental",
    "dance_floor": "party_rental",
    "furniture_rental": "party_rental",
    "food_truck": "party_rental",
    "coffee_cart": "party_rental",
    "balloon_artist": "party_rental",
    # QUINCEANERA — quinceañera-specific vendors
    "quinceanera_planner": "quinceanera",
    "quinceanera": "quinceanera",
    # VENUE — venues, wineries, churches
    "wedding_venue": "venue",
    "quinceanera_venue": "venue",
    "event_venue": "venue",
    "hotel_venue": "venue",
    "corporate_event_venue": "venue",
    "venue": "venue",
    "winery_venue": "venue",
    "winery": "venue",
    "farm_ranch_venue": "venue",
    "church": "venue",
    "church_hall": "venue",
    "community_center": "venue",
    "banquet_hall": "venue",
    "country_club": "venue",
    "restaurant": "venue",
    # CONSTRUCTION
    "construction": "construction",
    "generator_rental": "construction",
    "setup_crew": "construction",
    # GENERAL COMMERCIAL
    "festival_organizer": "wedding_planner",
    "festival": "wedding_planner",
    "corporate": "wedding_planner",
    "stage_rental": "party_rental",
}

# Category phrase for the referral template's {category_phrase} placeholder
_CATEGORY_PHRASES = {
    "event_planner": "event planners",
    "wedding_planner": "wedding planners",
    "quinceanera_planner": "quincea\u00f1era planners",
    "corporate_event_planner": "corporate event planners",
    "catering": "caterers",
    "caterer": "caterers",
    "photography": "photographers",
    "photographer": "photographers",
    "videography": "videographers",
    "florist": "florists",
    "dj_entertainment": "DJs and entertainers",
    "dj": "DJs",
    "live_band": "musicians",
    "bartending_mobile_bar": "bartenders",
    "bartending": "bartenders",
    "party_rental": "party rental companies",
    "bounce_house": "entertainment rental companies",
    "photo_booth": "photo booth operators",
    "tent_rental": "tent rental companies",
    "lighting_design": "lighting designers",
    "lighting": "lighting professionals",
    "event_decorator": "event decorators",
    "decorator": "decorators",
    "food_truck": "food truck operators",
    "balloon_artist": "balloon artists",
    "officiant": "officiants",
    "hair_makeup": "hair and makeup artists",
    "wedding_cake": "cake designers",
}


def validate_email_content(text: str) -> list:
    """Check email text for forbidden content. Returns list of violations."""
    violations = []
    for pattern in _FORBIDDEN:
        if pattern.search(text):
            violations.append("Forbidden pattern found: %s" % pattern.pattern)
    return violations


def _get_template_key(category: str) -> str:
    """Map database category to template key. Falls back to referral."""
    cat = (category or "").strip()
    # Direct match
    if cat in _TEMPLATE_BODIES:
        return cat
    # Explicit mapping
    cat_lower = cat.lower().replace(" ", "_")
    if cat_lower in CATEGORY_TO_TEMPLATE:
        return CATEGORY_TO_TEMPLATE[cat_lower]
    # Fuzzy match
    for db_cat, tmpl_key in CATEGORY_TO_TEMPLATE.items():
        if db_cat in cat_lower or cat_lower in db_cat:
            return tmpl_key
    return "wedding_planner"


def generate_cold_email(business_name: str, category: str = "",
                        contact_name: str = "", city: str = "",
                        vendor_id: int = 0) -> dict:
    """
    Generate a category-specific cold email for a B2B lead.
    Returns {subject, plain_body, html_body, template_type, variant} or {error}.

    Uses A/B variant selection based on vendor_id hash so the same vendor
    always gets the same variant, but different vendors get different copy
    to avoid spam filter detection from identical bulk sends.
    """
    tmpl_key = _get_template_key(category)

    # Pick A/B variant — deterministic per vendor
    variant_idx = (vendor_id or hash(business_name)) % 2
    subjects = _TEMPLATE_SUBJECTS_AB.get(tmpl_key, _TEMPLATE_SUBJECTS_AB["wedding_planner"])
    bodies = _TEMPLATE_BODIES_AB.get(tmpl_key, _TEMPLATE_BODIES_AB["wedding_planner"])

    # Subject line — now includes business name
    bname = (business_name or "").strip()
    try:
        subject = subjects[variant_idx].format(
            business_name=bname or "your team",
            original_subject="Quick question about restroom solutions",
        )
    except (KeyError, IndexError):
        subject = subjects[0].format(
            business_name=bname or "your team",
            original_subject="Quick question about restroom solutions",
        )

    # Greeting — use first name if available, otherwise business name
    name = (contact_name or "").strip()
    if name:
        greeting = "Hi %s," % name
    elif bname:
        greeting = "Hi %s team," % bname
    else:
        greeting = "Hi there,"

    # Body — format placeholders
    cat_lower = (category or "").strip().lower().replace(" ", "_")
    category_phrase = _CATEGORY_PHRASES.get(cat_lower, "event professionals")
    body = bodies[variant_idx].format(
        business_name=bname,
        category_phrase=category_phrase,
    )

    # Assemble plain text
    plain_body = (
        "%s\n\n"
        "%s\n\n"
        "Best,\n"
        "%s"
        "%s"
    ) % (greeting, body, SIGNATURE, CAN_SPAM_FOOTER)

    # Validate
    violations = validate_email_content(plain_body)
    if violations:
        return {"error": "Email content validation failed: %s" % violations}

    # Build full body text with greeting + body + signature for HTML rendering
    full_body_text = "%s\n\n%s\n\nBest,\n%s" % (greeting, body, SIGNATURE)

    # HTML version — use branded template functions with CTA buttons
    if tmpl_key in ("followup_1", "followup_2"):
        html_body, _ = follow_up("", full_body_text, step_type="gentle")
    else:
        html_body, _ = initial_outreach("", full_body_text, subject)

    # Inject CAN-SPAM footer into HTML
    html_body = html_body.replace(
        "</td></tr>\n</table>\n</body>",
        "%s</td></tr>\n</table>\n</body>" % CAN_SPAM_HTML,
    )

    return {
        "subject": subject,
        "plain_body": plain_body,
        "html_body": html_body,
        "template_type": tmpl_key,
        "variant": "A" if variant_idx == 0 else "B",
    }


def generate_referral_email(business_name: str, category: str = "",
                            contact_name: str = "", city: str = "",
                            vendor_id: int = 0) -> dict:
    """
    Generate a referral partnership email for a vendor.

    Uses the 'referral_partnership' template regardless of vendor category.
    Same A/B variant logic as generate_cold_email() but with partnership-
    focused copy and soft commission language (no dollar amounts).

    Returns {subject, plain_body, html_body, template_type, variant} or {error}.
    """
    tmpl_key = "referral_partnership"

    # Pick A/B variant — deterministic per vendor
    variant_idx = (vendor_id or hash(business_name)) % 2
    subjects = _TEMPLATE_SUBJECTS_AB[tmpl_key]
    bodies = _TEMPLATE_BODIES_AB[tmpl_key]

    # Subject line
    bname = (business_name or "").strip()
    subject = subjects[variant_idx].format(
        business_name=bname or "your team",
        original_subject="",
    )

    # Greeting
    name = (contact_name or "").strip()
    if name:
        greeting = "Hi %s," % name
    elif bname:
        greeting = "Hi %s team," % bname
    else:
        greeting = "Hi there,"

    # Body — format placeholders
    cat_lower = (category or "").strip().lower().replace(" ", "_")
    category_phrase = _CATEGORY_PHRASES.get(cat_lower, "event professionals")
    body = bodies[variant_idx].format(
        business_name=bname,
        category_phrase=category_phrase,
    )

    # Assemble plain text
    plain_body = (
        "%s\n\n"
        "%s\n\n"
        "Best,\n"
        "%s"
        "%s"
    ) % (greeting, body, SIGNATURE, CAN_SPAM_FOOTER)

    # Validate
    violations = validate_email_content(plain_body)
    if violations:
        return {"error": "Email content validation failed: %s" % violations}

    full_body_text = "%s\n\n%s\n\nBest,\n%s" % (greeting, body, SIGNATURE)
    html_body, _ = initial_outreach("", full_body_text, subject)

    # Inject CAN-SPAM footer into HTML
    html_body = html_body.replace(
        "</td></tr>\n</table>\n</body>",
        "%s</td></tr>\n</table>\n</body>" % CAN_SPAM_HTML,
    )

    return {
        "subject": subject,
        "plain_body": plain_body,
        "html_body": html_body,
        "template_type": tmpl_key,
        "variant": "A" if variant_idx == 0 else "B",
    }
